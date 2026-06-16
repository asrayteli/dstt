from flask import Blueprint, render_template, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime, date
import pandas as pd
import numpy as np
import xlrd
from sqlalchemy import or_, and_
from io import BytesIO
import csv

from app.utils.atomic_io import write_json_atomic

from app.models import db, Employee, Office, UploadHistory, EditHistory, SalaryMapping, EmployeeSalary, SalaryUploadHistory, ContactUploadHistory
from app.access_control import (
    is_admin_user as _is_dstt_admin,
    user_office_codes as _dstt_user_office_codes,
)
from app.security.file_crypto import (
    decrypt_json_from_file as _decrypt_session_json,
    encrypt_json_to_file as _encrypt_session_json,
    try_safe_unlink as _safe_unlink,
)


_SESSION_FILE_SUFFIX = ".enc"


def _session_path(uploads_path: str, session_key: str) -> str:
    return os.path.join(uploads_path, f"{session_key}{_SESSION_FILE_SUFFIX}")


def _load_session_payload(uploads_path: str, session_key: str):
    """暗号化 session ファイルを優先し、旧来の ``.json`` も読めるようにする。"""
    enc_path = _session_path(uploads_path, session_key)
    if os.path.exists(enc_path):
        return enc_path, _decrypt_session_json(enc_path)
    legacy_path = os.path.join(uploads_path, f"{session_key}.json")
    if os.path.exists(legacy_path):
        # 旧形式（平文）は後方互換として読んだら即削除し、暗号化形式へ移行させる
        with open(legacy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _safe_unlink(legacy_path)
        return enc_path, data
    return enc_path, None

pluslist_bp = Blueprint("pluslist", __name__, url_prefix="/tools/pluslist")

# 初期管理者ID
INITIAL_ADMIN_ID = None

# アップロード設定
UPLOAD_FOLDER = 'uploads/pluslist'
ALLOWED_EXTENSIONS = {'xls', 'xlsx', 'csv'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 編集履歴の最大保持件数
MAX_EDIT_HISTORY = 50


def get_data_path():
    """データディレクトリのパスを取得"""
    return os.path.join(current_app.root_path, 'static', 'pluslist')


def ensure_data_directories():
    """必要なディレクトリとファイルを作成"""
    data_path = get_data_path()
    uploads_path = os.path.join(data_path, 'uploads')

    # ディレクトリ作成
    os.makedirs(uploads_path, exist_ok=True)

    # permissions.jsonの初期化
    permissions_file = os.path.join(data_path, 'pluslist_permissions.json')
    if not os.path.exists(permissions_file):
        initial_permissions = {
            "admins": [],
            "user_offices": {}
        }
        write_json_atomic(permissions_file, initial_permissions)


def load_permissions():
    """権限情報を読み込み"""
    permissions_file = os.path.join(get_data_path(), 'pluslist_permissions.json')
    try:
        with open(permissions_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"admins": [], "user_offices": {}}


def save_permissions(permissions):
    """権限情報を保存"""
    permissions_file = os.path.join(get_data_path(), 'pluslist_permissions.json')
    write_json_atomic(permissions_file, permissions)


def is_admin(user_id):
    """管理者権限チェック。DSTT管理者(is_admin=True)か、pluslist独自の管理者。"""
    if _is_dstt_admin():
        return True
    permissions = load_permissions()
    return user_id in permissions.get('admins', [])


def get_user_offices(user_id):
    """ユーザーがアクセス可能な営業所コードリストを取得。
    - DSTT管理者は全営業所。
    - それ以外は「ユーザーに紐付けた支店/営業所のコード」＋「pluslist独自の個別付与」の和集合。
    """
    if is_admin(user_id):
        return [office.office_code for office in Office.query.all()]

    permissions = load_permissions()
    legacy = set(permissions.get('user_offices', {}).get(user_id, []))

    try:
        from flask_login import current_user as _cu
        # current_user が一致する場合のみ新方式を併用（他ユーザーIDで呼ばれる場合は従来のみ）
        if getattr(_cu, 'is_authenticated', False) and getattr(_cu, 'username', None) == user_id:
            legacy |= set(_dstt_user_office_codes(_cu))
    except Exception:
        pass

    return sorted(legacy)


def has_office_access(user_id, office_code):
    """特定の営業所へのアクセス権限をチェック"""
    if is_admin(user_id):
        return True
    return office_code in get_user_offices(user_id)


def allowed_file(filename):
    """許可されたファイル拡張子かチェック"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_date(date_str):
    """日付文字列をdate型に変換"""
    # None, NaN, NaT, 空文字列などをチェック
    if date_str is None or date_str == '':
        return None

    # pandasのNaやNaTをチェック
    try:
        if pd.isna(date_str):
            return None
    except (TypeError, ValueError):
        pass

    # datetime.date型の場合はそのまま返す
    if isinstance(date_str, date) and not isinstance(date_str, datetime):
        return date_str

    # datetime型またはTimestamp型の場合
    if isinstance(date_str, (datetime, pd.Timestamp)):
        return date_str.date()

    # numpy.datetime64型の場合（pandasが使用することがある）
    if isinstance(date_str, np.datetime64):
        # NaT（Not a Time）をチェック
        if pd.isna(date_str):
            return None
        ts = pd.Timestamp(date_str)
        return ts.date()

    # 数値の場合（Excelシリアル値）
    if isinstance(date_str, (int, float)):
        # NaN, Infをチェック
        if not np.isfinite(date_str):
            return None
        # 妥当な範囲をチェック（1900年～2100年 = シリアル値 0～73050程度）
        if date_str < 0 or date_str > 100000:
            return None
        try:
            # pandasでExcelシリアル値を日付に変換
            dt = pd.to_datetime(date_str, unit='D', origin='1899-12-30')
            if pd.isna(dt):
                return None
            return dt.date()
        except Exception:
            return None

    # 文字列の場合、様々な形式に対応
    date_str = str(date_str).strip()
    if not date_str or date_str == 'nan' or date_str == 'NaT':
        return None

    # 一般的な日付形式を試行
    for fmt in ['%Y/%m/%d', '%Y-%m-%d', '%Y%m%d', '%Y年%m月%d日']:
        try:
            return datetime.strptime(date_str, fmt).date()
        except Exception:
            continue

    # 数値文字列の場合（シリアル値が文字列化されている）
    try:
        serial_value = float(date_str)
        # 妥当な範囲をチェック
        if not np.isfinite(serial_value) or serial_value < 0 or serial_value > 100000:
            return None
        dt = pd.to_datetime(serial_value, unit='D', origin='1899-12-30')
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        pass

    return None


def read_excel_file(file_path, original_filename=None):
    """
    Excelファイル(.xls, .xlsx)またはCSVファイルを読み込む
    暫定会社略称が「DST」の行のみをフィルタリング
    """
    # 元のファイル名がある場合はそこから拡張子を取得（より確実）
    if original_filename:
        file_ext = os.path.splitext(original_filename)[1].lower()
    else:
        file_ext = os.path.splitext(file_path)[1].lower()

    try:
        # ファイル形式に応じて適切なエンジンを使用
        # 数値列を文字列として読み込むための設定（前ゼロを保持）
        # dtypeを使用して、指定した列を文字列型として読み込む
        # 日付列は除外して自動パースさせる
        str_columns = {
            '社員番号': str,
            '所属コード［＊］': str,
            '原価コード［＊］': str,
            '管理担当社員番号': str,
            '郵便番号': str,
            '契約コード［＊］': str,
            '電話番号': str,
            '携帯電話(個人)': str
        }

        if file_ext == '.xlsx':
            try:
                df = pd.read_excel(file_path, engine='openpyxl', dtype=str_columns)
            except Exception as e:
                raise ValueError(f".xlsxファイルの読み込みに失敗しました（openpyxl使用）: {str(e)}")

        elif file_ext == '.xls':
            try:
                df = pd.read_excel(file_path, engine='xlrd', dtype=str_columns)
            except Exception as e:
                raise ValueError(f".xlsファイルの読み込みに失敗しました（xlrd使用）: {str(e)}")

        elif file_ext == '.csv':
            try:
                # まずUTF-8 with BOMで試行
                df = pd.read_csv(file_path, encoding='utf-8-sig', dtype=str_columns)
            except Exception:
                try:
                    # UTF-8で試行
                    df = pd.read_csv(file_path, encoding='utf-8', dtype=str_columns)
                except Exception:
                    try:
                        # Shift-JISで試行（日本語ファイルの場合）
                        df = pd.read_csv(file_path, encoding='shift-jis', dtype=str_columns)
                    except Exception as e:
                        raise ValueError(f"CSVファイルの読み込みに失敗しました: {str(e)}")
        else:
            # エラーメッセージに詳細情報を追加
            raise ValueError(f"サポートされていないファイル形式: '{file_ext}' (元のファイル名: {original_filename}, 保存先: {file_path})")

        # 列名の前後の空白を削除（Excelでスペースが入る場合がある）
        df.columns = df.columns.str.strip()

        # カラム名を確認（CSVサンプルと同じ構造を想定）
        # 暫定会社略称でフィルタリング
        if '暫定会社略称' in df.columns:
            df = df[df['暫定会社略称'] == 'DST']
        else:
            # カラム名一覧をログに出力（デバッグ用）
            available_columns = ', '.join(df.columns.tolist()[:10])  # 最初の10列
            raise ValueError(f"'暫定会社略称'列が見つかりません。利用可能な列: {available_columns}...")

        # 日付列はpandasが自動パースした状態のまま返す
        # parse_date()関数がすべての形式（datetime、数値、文字列）を処理する
        return df

    except ValueError:
        # ValueErrorはそのまま再送出
        raise
    except Exception as e:
        raise ValueError(f"予期しないエラー: {str(e)}")


def serialize_employee_data(data):
    """
    社員データをJSON化可能な形式に変換（日付型を文字列に）
    """
    serialized = data.copy()
    # datetime.date型を文字列に変換
    # datetimeはdateのサブクラスなので、先にdatetimeをチェック
    if serialized.get('birth_date'):
        if isinstance(serialized['birth_date'], datetime):
            serialized['birth_date'] = serialized['birth_date'].date().isoformat()
        elif isinstance(serialized['birth_date'], date):
            serialized['birth_date'] = serialized['birth_date'].isoformat()

    if serialized.get('hire_date'):
        if isinstance(serialized['hire_date'], datetime):
            serialized['hire_date'] = serialized['hire_date'].date().isoformat()
        elif isinstance(serialized['hire_date'], date):
            serialized['hire_date'] = serialized['hire_date'].isoformat()

    return serialized


def deserialize_employee_data(data):
    """
    JSON化された社員データを元に戻す（文字列を日付型に）
    """
    deserialized = data.copy()
    # 文字列をdatetime.date型に変換
    # 既にdate型の場合もあるので、そのケースも処理
    if 'birth_date' in deserialized and deserialized['birth_date'] is not None:
        if isinstance(deserialized['birth_date'], str):
            deserialized['birth_date'] = parse_date(deserialized['birth_date'])
        elif isinstance(deserialized['birth_date'], datetime):
            deserialized['birth_date'] = deserialized['birth_date'].date()
        elif isinstance(deserialized['birth_date'], date):
            # 既にdate型の場合はそのまま
            pass

    if 'hire_date' in deserialized and deserialized['hire_date'] is not None:
        if isinstance(deserialized['hire_date'], str):
            deserialized['hire_date'] = parse_date(deserialized['hire_date'])
        elif isinstance(deserialized['hire_date'], datetime):
            deserialized['hire_date'] = deserialized['hire_date'].date()
        elif isinstance(deserialized['hire_date'], date):
            # 既にdate型の場合はそのまま
            pass

    return deserialized


def parse_employee_data(row):
    """
    DataFrameの1行を社員データに変換
    除外列：暫定会社コード、暫定会社略称、社員区分［＊］、社員属性区分［＊］、社員属性区分
    """
    return {
        'employee_number': str(row.get('社員番号', '')).strip(),
        'office_code': str(row.get('所属コード［＊］', '')).strip(),
        'office_name': str(row.get('所属名称', '')).strip(),
        'employee_name': str(row.get('社員名称', '')).strip(),
        'employee_kana': str(row.get('社員ｶﾅ名称', '')).strip() if pd.notna(row.get('社員ｶﾅ名称')) else None,
        'employee_type': str(row.get('社員区分', '')).strip() if pd.notna(row.get('社員区分')) else None,
        'gender': str(row.get('性別', '')).strip() if pd.notna(row.get('性別')) else None,
        'cost_code': str(row.get('原価コード［＊］', '')).strip() if pd.notna(row.get('原価コード［＊］')) else None,
        'cost_name': str(row.get('原価名称', '')).strip() if pd.notna(row.get('原価名称')) else None,
        'manager_number': str(row.get('管理担当社員番号', '')).strip() if pd.notna(row.get('管理担当社員番号')) else None,
        'manager_name': str(row.get('管理担当社員名', '')).strip() if pd.notna(row.get('管理担当社員名')) else None,
        'postal_code': str(row.get('郵便番号', '')).strip() if pd.notna(row.get('郵便番号')) else None,
        'address1': str(row.get('住所１', '')).strip() if pd.notna(row.get('住所１')) else None,
        'address2': str(row.get('住所２', '')).strip() if pd.notna(row.get('住所２')) else None,
        'mansion_name': str(row.get('マンション名', '')).strip() if pd.notna(row.get('マンション名')) else None,
        'contract_code': str(row.get('契約コード［＊］', '')).strip() if pd.notna(row.get('契約コード［＊］')) else None,
        'company_name': str(row.get('法人名', '')).strip() if pd.notna(row.get('法人名')) else None,
        'site_name': str(row.get('現場名', '')).strip() if pd.notna(row.get('現場名')) else None,
        'job_title': str(row.get('職種名称', '')).strip() if pd.notna(row.get('職種名称')) else None,
        'birth_date': parse_date(row.get('生年月日')),
        'hire_date': parse_date(row.get('入社日付')),
        'retirement_date': str(row.get('退職日付', '')).strip() if pd.notna(row.get('退職日付')) else None,
        'phone_number': str(row.get('電話番号', '')).strip() if pd.notna(row.get('電話番号')) else None,
        'mobile_phone': str(row.get('携帯電話(個人)', '')).strip() if pd.notna(row.get('携帯電話(個人)')) else None,
        'health_insurance': str(row.get('健康保険加入区分', '')).strip() if pd.notna(row.get('健康保険加入区分')) else None,
    }


def detect_diff(uploaded_data, office_codes):
    """
    アップロードされたデータと既存データの差分を検出

    Args:
        uploaded_data: アップロードされたデータのリスト
        office_codes: 対象営業所コードのリスト

    Returns:
        {
            'to_add': 追加するデータ,
            'to_update': 更新するデータ,
            'to_delete': 削除するデータ（ファイルに含まれていない既存社員）,
            'stats': 統計情報
        }
    """
    # 既存データを取得（対象営業所のみ）
    existing_employees = Employee.query.filter(
        Employee.office_code.in_(office_codes),
        Employee.is_deleted == False
    ).all()

    existing_dict = {emp.employee_number: emp for emp in existing_employees}
    uploaded_dict = {data['employee_number']: data for data in uploaded_data}

    to_add = []
    to_update = []
    to_delete = []

    # 追加・更新の検出
    for emp_num, data in uploaded_dict.items():
        if emp_num not in existing_dict:
            # 新規追加
            to_add.append(data)
        else:
            # 更新チェック
            existing = existing_dict[emp_num]
            # 退職者として残っていた社員がファイルに復活した場合は、退職フラグを
            # 解除するため必ず更新対象に含める
            has_changes = bool(existing.is_retired)

            # 各フィールドをチェック
            if not has_changes:
                for key, value in data.items():
                    if key in ['birth_date', 'hire_date']:
                        existing_value = getattr(existing, key)
                        if existing_value != value:
                            has_changes = True
                            break
                    else:
                        existing_value = getattr(existing, key, None)
                        # None と空文字列を同一視
                        if (existing_value or '') != (value or ''):
                            has_changes = True
                            break

            if has_changes:
                to_update.append({
                    'employee': existing,
                    'new_data': data
                })

    # 退職の検出（ファイルに含まれていない社員）
    # 既に退職フラグが立っている社員は再検出しない（毎回カウントされるのを防ぐ）
    for emp_num, existing in existing_dict.items():
        if emp_num not in uploaded_dict and not existing.is_retired:
            to_delete.append(existing)

    stats = {
        'add_count': len(to_add),
        'update_count': len(to_update),
        'delete_count': len(to_delete),
        'total_count': len(uploaded_data)
    }

    return {
        'to_add': to_add,
        'to_update': to_update,
        'to_delete': to_delete,
        'stats': stats
    }


def trim_edit_history():
    """編集履歴を最新50件に制限"""
    count = EditHistory.query.count()
    if count > MAX_EDIT_HISTORY:
        # 古い履歴を削除
        to_delete = count - MAX_EDIT_HISTORY
        old_histories = EditHistory.query.order_by(EditHistory.edited_at.asc()).limit(to_delete).all()
        for history in old_histories:
            db.session.delete(history)


def record_edit_history(employee_number, user_id, action, field_name=None, old_value=None, new_value=None):
    """Web編集履歴を記録"""
    history = EditHistory(
        employee_number=employee_number,
        edited_by=user_id,
        action=action,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value
    )
    db.session.add(history)

    # 履歴件数を制限
    trim_edit_history()


# ===== ルート定義 =====

@pluslist_bp.route("/")
@login_required
def index():
    """メインページ"""
    ensure_data_directories()

    user_id = str(current_user.username)
    is_admin_user = is_admin(user_id)
    user_offices = get_user_offices(user_id)

    # 営業所一覧を取得
    offices = Office.query.all()
    office_list = [{'code': o.office_code, 'name': o.office_name} for o in offices]

    return render_template(
        "pluslist.html",
        user_id=user_id,
        is_admin=is_admin_user,
        user_offices=user_offices,
        offices=office_list
    )


@pluslist_bp.route("/api/employees")
@login_required
def get_employees():
    """社員一覧を取得（ページネーション、検索、ソート対応）"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    if not user_offices:
        return jsonify({"error": "アクセス権限がありません"}), 403

    # クエリパラメータ
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    search = request.args.get('search', '')
    sort_by = request.args.get('sort_by', 'employee_number')
    sort_order = request.args.get('sort_order', 'asc')
    office_filter = request.args.get('office', '')
    show_deleted = request.args.get('show_deleted', 'false') == 'true'

    # 新しい検索パラメータ
    search_scope = request.args.get('search_scope', 'all')
    search_mode = request.args.get('search_mode', 'partial')
    search_operator = request.args.get('search_operator', 'and')
    search_columns_str = request.args.get('search_columns', '')
    hire_date_from = request.args.get('hire_date_from', '')
    hire_date_to = request.args.get('hire_date_to', '')
    birth_date_from = request.args.get('birth_date_from', '')
    birth_date_to = request.args.get('birth_date_to', '')

    # ベースクエリ
    query = Employee.query.filter(Employee.office_code.in_(user_offices))

    # 退職者／削除済みフィルタ（既定では在籍中のみ、チェック時は退職者・削除済みも表示）
    if not show_deleted:
        query = query.filter(Employee.is_deleted == False, Employee.is_retired == False)

    # 営業所フィルタ
    if office_filter:
        query = query.filter(Employee.office_code == office_filter)

    # 検索（高度な検索機能）
    if search:
        # 複数キーワード対応（スペース区切り）
        keywords = search.split()

        # 検索対象列の決定
        if search_scope == 'specific' and search_columns_str:
            search_columns = search_columns_str.split(',')
        else:
            # デフォルトは全主要列
            search_columns = [
                'employee_number', 'employee_name', 'employee_kana',
                'office_name', 'job_title', 'mobile_phone', 'phone_number',
                'address1', 'address2', 'postal_code', 'company_name', 'site_name'
            ]

        # 検索条件を構築
        def build_keyword_conditions(keyword):
            conditions = []
            for col_name in search_columns:
                if hasattr(Employee, col_name):
                    col = getattr(Employee, col_name)
                    if search_mode == 'exact':
                        conditions.append(col == keyword)
                    elif search_mode == 'prefix':
                        conditions.append(col.like(f"{keyword}%"))
                    else:  # partial
                        conditions.append(col.like(f"%{keyword}%"))
            return or_(*conditions) if conditions else None

        # AND/OR検索
        if search_operator == 'or':
            # OR検索：いずれかのキーワードに一致
            all_conditions = []
            for keyword in keywords:
                cond = build_keyword_conditions(keyword)
                if cond is not None:
                    all_conditions.append(cond)
            if all_conditions:
                query = query.filter(or_(*all_conditions))
        else:
            # AND検索：全てのキーワードに一致
            for keyword in keywords:
                cond = build_keyword_conditions(keyword)
                if cond is not None:
                    query = query.filter(cond)

    # 日付範囲検索
    if hire_date_from:
        query = query.filter(Employee.hire_date >= hire_date_from)
    if hire_date_to:
        query = query.filter(Employee.hire_date <= hire_date_to)
    if birth_date_from:
        query = query.filter(Employee.birth_date >= birth_date_from)
    if birth_date_to:
        query = query.filter(Employee.birth_date <= birth_date_to)

    # 複数フィールド条件（フィールドごとの個別検索）
    field_employee_number = request.args.get('field_employee_number', '')
    field_employee_name = request.args.get('field_employee_name', '')
    field_employee_kana = request.args.get('field_employee_kana', '')
    field_gender = request.args.get('field_gender', '')
    field_job_title = request.args.get('field_job_title', '')
    field_mobile_phone = request.args.get('field_mobile_phone', '')
    field_address = request.args.get('field_address', '')
    field_company_name = request.args.get('field_company_name', '')
    field_manager_name = request.args.get('field_manager_name', '')
    field_employee_type = request.args.get('field_employee_type', '')

    if field_employee_number:
        query = query.filter(Employee.employee_number.like(f"%{field_employee_number}%"))
    if field_employee_name:
        query = query.filter(Employee.employee_name.like(f"%{field_employee_name}%"))
    if field_employee_kana:
        query = query.filter(Employee.employee_kana.like(f"%{field_employee_kana}%"))
    if field_gender:
        query = query.filter(Employee.gender == field_gender)
    if field_job_title:
        query = query.filter(Employee.job_title.like(f"%{field_job_title}%"))
    if field_mobile_phone:
        query = query.filter(Employee.mobile_phone.like(f"%{field_mobile_phone}%"))
    if field_address:
        query = query.filter(or_(
            Employee.address1.like(f"%{field_address}%"),
            Employee.address2.like(f"%{field_address}%")
        ))
    if field_company_name:
        query = query.filter(Employee.company_name.like(f"%{field_company_name}%"))
    if field_manager_name:
        query = query.filter(Employee.manager_name.like(f"%{field_manager_name}%"))
    if field_employee_type:
        query = query.filter(Employee.employee_type.like(f"%{field_employee_type}%"))

    # ソート
    if hasattr(Employee, sort_by):
        sort_column = getattr(Employee, sort_by)
        if sort_order == 'desc':
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

    # ページネーション
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # 賃金データを含めるかどうか
    include_salary = request.args.get('include_salary', 'false') == 'true'

    employees = []
    for emp in pagination.items:
        emp_dict = emp.to_dict()

        # 賃金データを追加（オプション）
        if include_salary:
            salaries = EmployeeSalary.query.filter_by(employee_number=emp.employee_number).all()
            mappings = {m.item_id: m for m in SalaryMapping.query.all()}
            salary_data = {}
            for s in salaries:
                if s.item_id in mappings:
                    mapping = mappings[s.item_id]
                    salary_data[f'salary_{mapping.column_key}'] = s.amount
            emp_dict['salary_data'] = salary_data

        employees.append(emp_dict)

    return jsonify({
        'employees': employees,
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
        'per_page': per_page
    })


@pluslist_bp.route("/api/employee/<employee_number>")
@login_required
def get_employee(employee_number):
    """社員詳細を取得"""
    user_id = str(current_user.username)

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if not employee:
        return jsonify({"error": "社員が見つかりません"}), 404

    # アクセス権限チェック
    if not has_office_access(user_id, employee.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    emp_dict = employee.to_dict()

    # 賃金データを追加
    salaries = EmployeeSalary.query.filter_by(employee_number=employee_number).all()
    mappings = {m.item_id: m for m in SalaryMapping.query.all()}
    salary_data = {}
    for s in salaries:
        if s.item_id in mappings:
            mapping = mappings[s.item_id]
            salary_data[f'salary_{mapping.column_key}'] = {
                'item_id': s.item_id,
                'display_name': mapping.display_name,
                'amount': s.amount
            }
    emp_dict['salary_data'] = salary_data

    return jsonify(emp_dict)


@pluslist_bp.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    """ファイルアップロード＆差分プレビュー"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    if not user_offices:
        return jsonify({"error": "アクセス権限がありません"}), 403

    # ファイルチェック
    if 'file' not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "ファイルが選択されていません"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "許可されていないファイル形式です（.xls, .xlsx, .csv のみ）"}), 400

    try:
        # 元のファイル名を保存（拡張子判定用）
        original_filename = file.filename

        # 一時ファイルとして保存
        filename = secure_filename(file.filename)
        uploads_path = os.path.join(get_data_path(), 'uploads')
        os.makedirs(uploads_path, exist_ok=True)
        temp_path = os.path.join(uploads_path, f"temp_{user_id}_{filename}")
        file.save(temp_path)

        # Excelファイルを読み込み（元のファイル名も渡して拡張子判定を確実にする）
        df = read_excel_file(temp_path, original_filename)

        # データをパース
        uploaded_data = []
        errors = []
        office_codes_in_file = set()

        for idx, row in df.iterrows():
            try:
                data = parse_employee_data(row)

                # 必須項目チェック
                if not data['employee_number']:
                    errors.append(f"行{idx+2}: 社員番号が必須です")
                    continue
                if not data['employee_name']:
                    errors.append(f"行{idx+2}: 社員名称が必須です")
                    continue
                if not data['address1']:
                    errors.append(f"行{idx+2}: 住所１が必須です")
                    continue

                office_codes_in_file.add(data['office_code'])
                uploaded_data.append(data)
            except Exception as e:
                errors.append(f"行{idx+2}: {str(e)}")

        # 権限のある営業所のみをフィルタリング
        registered_offices = [o.office_code for o in Office.query.all()]
        valid_office_codes = []
        skipped_offices = []

        for office_code in office_codes_in_file:
            if office_code not in registered_offices:
                skipped_offices.append(office_code)
            elif office_code in user_offices or is_admin(user_id):
                valid_office_codes.append(office_code)

        # 権限のある営業所のデータのみ抽出
        filtered_data = [d for d in uploaded_data if d['office_code'] in valid_office_codes]

        if not filtered_data:
            return jsonify({
                "error": "アップロード可能なデータがありません",
                "details": f"登録されていない営業所: {', '.join(skipped_offices) if skipped_offices else 'なし'}"
            }), 400

        # 差分検出
        diff_result = detect_diff(filtered_data, valid_office_codes)

        # 元ファイルはここ以降参照しないため、平文で残さずに削除する
        _safe_unlink(temp_path)

        # 差分結果を暗号化してディスクに保存（インポート時に復号して使う）
        session_key = f"upload_session_{user_id}_{datetime.now().timestamp()}"
        temp_data = {
            'diff_result': {
                'to_add': [serialize_employee_data(d) for d in diff_result['to_add']],
                'to_update': [{'employee_number': u['employee'].employee_number,
                               'new_data': serialize_employee_data(u['new_data'])}
                             for u in diff_result['to_update']],
                'to_delete': [e.employee_number for e in diff_result['to_delete']],
                'stats': diff_result['stats']
            },
            'filename': filename,
            'office_codes': valid_office_codes,
            'skipped_offices': skipped_offices
        }

        # セッション情報は AES-256-GCM で暗号化してディスク保存
        session_file = _session_path(uploads_path, session_key)
        try:
            _encrypt_session_json(session_file, temp_data)
        except Exception:
            _safe_unlink(session_file)
            raise

        return jsonify({
            "success": True,
            "session_key": session_key,
            "stats": diff_result['stats'],
            "errors": errors,
            "skipped_offices": skipped_offices
        })

    except Exception as e:
        # 途中で失敗した場合、平文の痕跡を残さない
        try:
            _safe_unlink(temp_path)
        except Exception:
            pass
        return jsonify({"error": f"ファイル処理エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/import", methods=["POST"])
@login_required
def import_data():
    """差分プレビュー後のデータインポート実行"""
    user_id = str(current_user.username)

    data = request.json
    session_key = data.get('session_key')

    if not session_key:
        return jsonify({"error": "セッションキーが必要です"}), 400

    try:
        # セッション情報を読み込み（暗号化ファイルを優先、旧形式もフォールバック）
        uploads_path = os.path.join(get_data_path(), 'uploads')
        session_file, temp_data = _load_session_payload(uploads_path, session_key)

        if temp_data is None:
            return jsonify({"error": "セッションが期限切れです"}), 400

        diff_result = temp_data['diff_result']
        # 営業所コードは session を信用せず、現ユーザーの権限で必ず再検証する
        user_offices = set(get_user_offices(user_id))
        raw_office_codes = temp_data.get('office_codes') or []
        if is_admin(user_id):
            office_codes = list(raw_office_codes)
        else:
            office_codes = [c for c in raw_office_codes if c in user_offices]
            if not office_codes:
                _safe_unlink(session_file)
                return jsonify({
                    "error": "インポート対象営業所への権限がありません"
                }), 403
            if len(office_codes) != len(raw_office_codes):
                # 権限から外れた営業所がセッションに含まれていた → 該当レコードを除外
                allowed = set(office_codes)
                def _keep(d):
                    return d.get('office_code') in allowed
                diff_result['to_add'] = [d for d in diff_result['to_add'] if _keep(d)]
                diff_result['to_update'] = [
                    u for u in diff_result['to_update']
                    if _keep(u.get('new_data') or {})
                ]

        # データベースに反映
        added_count = 0
        updated_count = 0
        deleted_count = 0

        # 追加
        for emp_data in diff_result['to_add']:
            # JSON化されたデータをデシリアライズ（日付を復元）
            deserialized = deserialize_employee_data(emp_data)

            # 論理削除されたデータが存在する場合は復活させる
            existing = Employee.query.filter_by(employee_number=deserialized['employee_number']).first()
            if existing and existing.is_deleted:
                # 既存の論理削除データを復活＋更新
                for key, value in deserialized.items():
                    setattr(existing, key, value)
                existing.is_deleted = False
                existing.is_retired = False  # 名簿に載ったので退職フラグを解除
                existing.updated_at = datetime.utcnow()
            elif not existing:
                # 完全に新規の場合のみ追加
                employee = Employee(**deserialized)
                db.session.add(employee)
            added_count += 1

        # 更新
        for update_info in diff_result['to_update']:
            employee = Employee.query.filter_by(
                employee_number=update_info['employee_number'],
                is_deleted=False
            ).first()
            if employee:
                # JSON化されたデータをデシリアライズ（日付を復元）
                deserialized = deserialize_employee_data(update_info['new_data'])
                for key, value in deserialized.items():
                    setattr(employee, key, value)
                # ファイルに載っている＝在籍中なので退職フラグを解除
                employee.is_retired = False
                employee.updated_at = datetime.utcnow()
                updated_count += 1

        # 退職（論理削除はせず、退職者フラグを立ててサーバーに残す）
        for emp_number in diff_result['to_delete']:
            employee = Employee.query.filter_by(
                employee_number=emp_number,
                is_deleted=False
            ).first()
            if employee:
                employee.is_retired = True
                if not employee.retirement_date:
                    employee.retirement_date = "？退職？"
                employee.updated_at = datetime.utcnow()
                deleted_count += 1

        # アップロード履歴を記録
        for office_code in office_codes:
            upload_history = UploadHistory(
                uploaded_by=user_id,
                office_code=office_code,
                filename=temp_data['filename'],
                added_count=added_count,
                updated_count=updated_count,
                deleted_count=deleted_count,
                total_count=diff_result['stats']['total_count']
            )
            db.session.add(upload_history)

        db.session.commit()

        # 暗号化されたセッション／残存していた一時ファイルを削除
        _safe_unlink(temp_data.get('file_path') or "")
        _safe_unlink(session_file)

        return jsonify({
            "success": True,
            "added": added_count,
            "updated": updated_count,
            "deleted": deleted_count
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"インポートエラー: {str(e)}"}), 500


@pluslist_bp.route("/api/employee", methods=["POST"])
@login_required
def create_employee():
    """社員を新規作成（Web UI）"""
    user_id = str(current_user.username)
    data = request.json

    office_code = data.get('office_code')
    if not has_office_access(user_id, office_code):
        return jsonify({"error": "この営業所への権限がありません"}), 403

    try:
        # 社員番号の重複チェック
        existing = Employee.query.filter_by(employee_number=data['employee_number']).first()
        if existing:
            return jsonify({"error": "この社員番号は既に登録されています"}), 400

        # 日付フィールドを変換
        if data.get('birth_date'):
            data['birth_date'] = parse_date(data['birth_date'])
        if data.get('hire_date'):
            data['hire_date'] = parse_date(data['hire_date'])

        employee = Employee(**data)
        db.session.add(employee)

        # 編集履歴を記録
        record_edit_history(
            employee_number=data['employee_number'],
            user_id=user_id,
            action='create',
            field_name='all',
            new_value='新規作成'
        )

        db.session.commit()

        return jsonify({"success": True, "employee": employee.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"作成エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/employee/<employee_number>", methods=["PUT"])
@login_required
def update_employee(employee_number):
    """社員情報を更新（Web UI）"""
    user_id = str(current_user.username)
    data = request.json

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if not employee:
        return jsonify({"error": "社員が見つかりません"}), 404

    if not has_office_access(user_id, employee.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    try:
        # 変更履歴を記録
        for key, new_value in data.items():
            if hasattr(employee, key):
                old_value = getattr(employee, key)

                # 日付フィールドの変換
                if key in ['birth_date', 'hire_date']:
                    new_value = parse_date(new_value) if new_value else None

                # 変更があった場合のみ履歴記録
                if old_value != new_value:
                    record_edit_history(
                        employee_number=employee_number,
                        user_id=user_id,
                        action='update',
                        field_name=key,
                        old_value=str(old_value) if old_value else '',
                        new_value=str(new_value) if new_value else ''
                    )
                    setattr(employee, key, new_value)

        employee.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({"success": True, "employee": employee.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/employee/<employee_number>", methods=["DELETE"])
@login_required
def delete_employee(employee_number):
    """社員を削除（論理削除）"""
    user_id = str(current_user.username)

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if not employee:
        return jsonify({"error": "社員が見つかりません"}), 404

    if not has_office_access(user_id, employee.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    try:
        employee.is_deleted = True
        employee.updated_at = datetime.utcnow()

        # 編集履歴を記録
        record_edit_history(
            employee_number=employee_number,
            user_id=user_id,
            action='delete',
            field_name='is_deleted',
            old_value='False',
            new_value='True'
        )

        db.session.commit()

        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"削除エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/export/<format>")
@login_required
def export_data(format):
    """データエクスポート（Excel/CSV）- 現在の表示内容を反映"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    if not user_offices:
        return jsonify({"error": "アクセス権限がありません"}), 403

    # 全パラメータを取得（get_employeesと同じ）
    search = request.args.get('search', '')
    sort_by = request.args.get('sort_by', 'employee_number')
    sort_order = request.args.get('sort_order', 'asc')
    office_filter = request.args.get('office', '')
    show_deleted = request.args.get('show_deleted', 'false') == 'true'

    # 詳細検索パラメータ
    search_scope = request.args.get('search_scope', 'all')
    search_mode = request.args.get('search_mode', 'partial')
    search_operator = request.args.get('search_operator', 'and')
    search_columns_str = request.args.get('search_columns', '')
    hire_date_from = request.args.get('hire_date_from', '')
    hire_date_to = request.args.get('hire_date_to', '')
    birth_date_from = request.args.get('birth_date_from', '')
    birth_date_to = request.args.get('birth_date_to', '')

    # フィールド別検索
    field_employee_number = request.args.get('field_employee_number', '')
    field_employee_name = request.args.get('field_employee_name', '')
    field_employee_kana = request.args.get('field_employee_kana', '')
    field_gender = request.args.get('field_gender', '')
    field_job_title = request.args.get('field_job_title', '')
    field_mobile_phone = request.args.get('field_mobile_phone', '')
    field_address = request.args.get('field_address', '')
    field_company_name = request.args.get('field_company_name', '')
    field_manager_name = request.args.get('field_manager_name', '')
    field_employee_type = request.args.get('field_employee_type', '')

    # 表示列の順序（空文字列を除外）
    visible_columns_str = request.args.get('visible_columns', '')
    visible_columns = [col.strip() for col in visible_columns_str.split(',') if col.strip()] if visible_columns_str else []

    # ベースクエリ（get_employeesと同じロジック）
    query = Employee.query.filter(Employee.office_code.in_(user_offices))

    if not show_deleted:
        query = query.filter(Employee.is_deleted == False, Employee.is_retired == False)

    if office_filter:
        query = query.filter(Employee.office_code == office_filter)

    # 検索（get_employeesと同じロジック）
    if search:
        keywords = search.split()

        if search_scope == 'specific' and search_columns_str:
            search_columns = [col.strip() for col in search_columns_str.split(',') if col.strip()]
        else:
            search_columns = [
                'employee_number', 'employee_name', 'employee_kana',
                'office_name', 'job_title', 'mobile_phone', 'phone_number',
                'address1', 'address2', 'postal_code', 'company_name', 'site_name'
            ]

        def build_keyword_conditions(keyword):
            conditions = []
            for col_name in search_columns:
                if hasattr(Employee, col_name):
                    col = getattr(Employee, col_name)
                    if search_mode == 'exact':
                        conditions.append(col == keyword)
                    elif search_mode == 'prefix':
                        conditions.append(col.like(f"{keyword}%"))
                    else:
                        conditions.append(col.like(f"%{keyword}%"))
            return or_(*conditions) if conditions else None

        if search_operator == 'or':
            all_conditions = []
            for keyword in keywords:
                cond = build_keyword_conditions(keyword)
                if cond is not None:
                    all_conditions.append(cond)
            if all_conditions:
                query = query.filter(or_(*all_conditions))
        else:
            for keyword in keywords:
                cond = build_keyword_conditions(keyword)
                if cond is not None:
                    query = query.filter(cond)

    # 日付範囲検索
    if hire_date_from:
        query = query.filter(Employee.hire_date >= hire_date_from)
    if hire_date_to:
        query = query.filter(Employee.hire_date <= hire_date_to)
    if birth_date_from:
        query = query.filter(Employee.birth_date >= birth_date_from)
    if birth_date_to:
        query = query.filter(Employee.birth_date <= birth_date_to)

    # フィールド別検索
    if field_employee_number:
        query = query.filter(Employee.employee_number.like(f"%{field_employee_number}%"))
    if field_employee_name:
        query = query.filter(Employee.employee_name.like(f"%{field_employee_name}%"))
    if field_employee_kana:
        query = query.filter(Employee.employee_kana.like(f"%{field_employee_kana}%"))
    if field_gender:
        query = query.filter(Employee.gender == field_gender)
    if field_job_title:
        query = query.filter(Employee.job_title.like(f"%{field_job_title}%"))
    if field_mobile_phone:
        query = query.filter(Employee.mobile_phone.like(f"%{field_mobile_phone}%"))
    if field_address:
        query = query.filter(or_(
            Employee.address1.like(f"%{field_address}%"),
            Employee.address2.like(f"%{field_address}%")
        ))
    if field_company_name:
        query = query.filter(Employee.company_name.like(f"%{field_company_name}%"))
    if field_manager_name:
        query = query.filter(Employee.manager_name.like(f"%{field_manager_name}%"))
    if field_employee_type:
        query = query.filter(Employee.employee_type.like(f"%{field_employee_type}%"))

    # ソート
    if hasattr(Employee, sort_by):
        sort_column = getattr(Employee, sort_by)
        if sort_order == 'desc':
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

    employees = query.all()

    # 合計表示オプション
    show_salary_total = request.args.get('show_salary_total', 'false') == 'true'
    total_visible_only = request.args.get('total_visible_only', 'true') == 'true'

    # 列名マッピング（日本語）
    column_mapping = {
        'employee_number': '社員番号',
        'office_code': '所属コード',
        'office_name': '所属名称',
        'employee_name': '社員名称',
        'employee_kana': '社員カナ名称',
        'employee_type': '社員区分',
        'gender': '性別',
        'cost_code': '原価コード',
        'cost_name': '原価名称',
        'manager_number': '管理担当社員番号',
        'manager_name': '管理担当社員名',
        'postal_code': '郵便番号',
        'address1': '住所1',
        'address2': '住所2',
        'mansion_name': 'マンション名',
        'contract_code': '契約コード',
        'company_name': '法人名',
        'site_name': '現場名',
        'job_title': '職種名称',
        'birth_date': '生年月日',
        'age': '年齢',
        'hire_date': '入社日付',
        'tenure': '入社年数',
        'retirement_date': '退職日付',
        'phone_number': '電話番号',
        'mobile_phone': '携帯電話',
        'company_mobile': '会社携帯',
        'email': 'メールアドレス',
        'health_insurance': '健康保険加入区分'
    }

    # 賃金マッピングを取得して列名マッピングに追加
    salary_mappings = SalaryMapping.query.order_by(SalaryMapping.sort_order).all()
    salary_mapping_dict = {}  # column_key -> mapping object
    for mapping in salary_mappings:
        col_key = f'salary_{mapping.column_key}'
        column_mapping[col_key] = mapping.display_name
        salary_mapping_dict[mapping.column_key] = mapping

    # 表示列が指定されていない場合はデフォルト
    if not visible_columns:
        visible_columns = [
            'employee_number', 'employee_name', 'office_name', 'job_title',
            'birth_date', 'hire_date', 'mobile_phone'
        ]

    # 賃金列の判定
    visible_salary_columns = [col for col in visible_columns if col.startswith('salary_')]
    has_salary_columns = len(visible_salary_columns) > 0

    # 賃金データを事前に取得（社員番号をキーにした辞書）
    salary_data_by_employee = {}
    if has_salary_columns or (show_salary_total and not total_visible_only):
        employee_numbers = [emp.employee_number for emp in employees]
        if employee_numbers:
            salary_records = EmployeeSalary.query.filter(
                EmployeeSalary.employee_number.in_(employee_numbers)
            ).all()
            for record in salary_records:
                if record.employee_number not in salary_data_by_employee:
                    salary_data_by_employee[record.employee_number] = {}
                # item_id -> column_keyのマッピング
                mapping = SalaryMapping.query.filter_by(item_id=record.item_id).first()
                if mapping:
                    col_key = f'salary_{mapping.column_key}'
                    salary_data_by_employee[record.employee_number][col_key] = record.amount

    # データを表示列の順序で構築
    # 列順序を保持するためにヘッダーリストを作成
    column_headers = [column_mapping.get(col, col) for col in visible_columns]

    # 合計列を追加
    if show_salary_total:
        column_headers.append('合計' if total_visible_only else '合計(全項目)')

    data = []
    for emp in employees:
        emp_dict = emp.to_dict()
        row = {}
        emp_salary_data = salary_data_by_employee.get(emp.employee_number, {})

        for col in visible_columns:
            jp_col = column_mapping.get(col, col)

            # 値の取得と整形
            if col == 'birth_date':
                row[jp_col] = emp.birth_date.strftime('%Y/%m/%d') if emp.birth_date else ''
            elif col == 'age':
                row[jp_col] = emp.calculate_age() or ''
            elif col == 'hire_date':
                row[jp_col] = emp.hire_date.strftime('%Y/%m/%d') if emp.hire_date else ''
            elif col == 'tenure':
                row[jp_col] = emp.calculate_tenure() or ''
            elif col.startswith('salary_'):
                # 賃金列
                amount = emp_salary_data.get(col)
                row[jp_col] = amount if amount is not None else ''
            else:
                row[jp_col] = emp_dict.get(col) or ''

        # 合計計算
        if show_salary_total:
            total_header = '合計' if total_visible_only else '合計(全項目)'
            total = 0
            has_any_data = False

            # 計算対象の列を決定
            if total_visible_only:
                columns_to_sum = visible_salary_columns
            else:
                columns_to_sum = [f'salary_{m.column_key}' for m in salary_mappings]

            for col in columns_to_sum:
                amount = emp_salary_data.get(col)
                if amount is not None:
                    has_any_data = True
                    # membership_fee（社員会費）はマイナスとして扱う
                    if col == 'salary_membership_fee':
                        total -= amount
                    else:
                        total += amount

            row[total_header] = total if has_any_data else ''

        data.append(row)

    # 列順序を明示的に指定してDataFrame作成
    df = pd.DataFrame(data, columns=column_headers)

    # ファイル生成
    if format == 'excel':
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='社員名簿')
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'社員名簿_{datetime.now().strftime("%Y%m%d")}.xlsx'
        )
    elif format == 'csv':
        output = BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'社員名簿_{datetime.now().strftime("%Y%m%d")}.csv'
        )
    else:
        return jsonify({"error": "サポートされていない形式です"}), 400


@pluslist_bp.route("/api/histories/upload")
@login_required
def get_upload_histories():
    """ファイルアップロード履歴を取得"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    histories = UploadHistory.query.filter(
        UploadHistory.office_code.in_(user_offices)
    ).order_by(UploadHistory.uploaded_at.desc()).limit(100).all()

    return jsonify([{
        'id': h.id,
        'uploaded_by': h.uploaded_by,
        'uploaded_at': h.uploaded_at.isoformat(),
        'office_code': h.office_code,
        'filename': h.filename,
        'added_count': h.added_count,
        'updated_count': h.updated_count,
        'deleted_count': h.deleted_count,
        'total_count': h.total_count
    } for h in histories])


@pluslist_bp.route("/api/histories/edit/<employee_number>")
@login_required
def get_edit_histories(employee_number):
    """特定社員のWeb編集履歴を取得"""
    user_id = str(current_user.username)

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if not employee:
        return jsonify({"error": "社員が見つかりません"}), 404

    if not has_office_access(user_id, employee.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    histories = EditHistory.query.filter_by(
        employee_number=employee_number
    ).order_by(EditHistory.edited_at.desc()).limit(50).all()

    return jsonify([{
        'id': h.id,
        'edited_by': h.edited_by,
        'edited_at': h.edited_at.isoformat(),
        'action': h.action,
        'field_name': h.field_name,
        'old_value': h.old_value,
        'new_value': h.new_value
    } for h in histories])


# ===== 管理者機能 =====

@pluslist_bp.route("/api/admin/offices", methods=["GET"])
@login_required
def get_all_offices():
    """全営業所一覧を取得（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    offices = Office.query.all()
    return jsonify([{
        'office_code': o.office_code,
        'office_name': o.office_name,
        'created_by': o.created_by,
        'created_at': o.created_at.isoformat()
    } for o in offices])


@pluslist_bp.route("/api/admin/office", methods=["POST"])
@login_required
def create_office():
    """営業所を新規作成（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json
    office_code = data.get('office_code')
    office_name = data.get('office_name')

    if not office_code or not office_name:
        return jsonify({"error": "営業所コードと名称が必要です"}), 400

    # 既存チェック
    existing = Office.query.filter_by(office_code=office_code).first()
    if existing:
        return jsonify({"error": "この営業所コードは既に登録されています"}), 400

    try:
        office = Office(
            office_code=office_code,
            office_name=office_name,
            created_by=user_id
        )
        db.session.add(office)
        db.session.commit()

        return jsonify({"success": True, "office": {
            'office_code': office.office_code,
            'office_name': office.office_name
        }})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"作成エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/admin/office/<office_code>", methods=["DELETE"])
@login_required
def delete_office(office_code):
    """営業所を削除（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    office = Office.query.filter_by(office_code=office_code).first()
    if not office:
        return jsonify({"error": "営業所が見つかりません"}), 404

    # 所属社員数をチェック
    employee_count = Employee.query.filter_by(office_code=office_code).count()

    try:
        db.session.delete(office)
        db.session.commit()

        return jsonify({"success": True, "deleted_employees": employee_count})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"削除エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/admin/permissions", methods=["GET"])
@login_required
def get_all_permissions():
    """全ユーザーの権限一覧を取得（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    permissions = load_permissions()
    offices = {o.office_code: o.office_name for o in Office.query.all()}

    users_info = []
    all_users = set(permissions.get('user_offices', {}).keys()) | set(permissions.get('admins', []))

    for uid in all_users:
        user_offices = permissions.get('user_offices', {}).get(uid, [])
        user_info = {
            "user_id": uid,
            "is_admin": uid in permissions.get('admins', []),
            "offices": [{'code': code, 'name': offices.get(code, code)} for code in user_offices]
        }
        users_info.append(user_info)

    return jsonify(users_info)


@pluslist_bp.route("/api/admin/grant", methods=["POST"])
@login_required
def grant_permission():
    """ユーザーに権限を付与（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json
    target_user_id = data.get('user_id')
    office_code = data.get('office_code')

    permissions = load_permissions()

    if target_user_id not in permissions['user_offices']:
        permissions['user_offices'][target_user_id] = []

    if office_code not in permissions['user_offices'][target_user_id]:
        permissions['user_offices'][target_user_id].append(office_code)

    save_permissions(permissions)

    return jsonify({"success": True})


@pluslist_bp.route("/api/admin/revoke", methods=["POST"])
@login_required
def revoke_permission():
    """ユーザーから権限を剥奪（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json
    target_user_id = data.get('user_id')
    office_code = data.get('office_code')

    permissions = load_permissions()

    if target_user_id in permissions.get('user_offices', {}):
        if office_code in permissions['user_offices'][target_user_id]:
            permissions['user_offices'][target_user_id].remove(office_code)

            # 空のリストになった場合はキーごと削除
            if not permissions['user_offices'][target_user_id]:
                del permissions['user_offices'][target_user_id]

    save_permissions(permissions)

    return jsonify({"success": True})


@pluslist_bp.route("/api/admin/clear-office-data", methods=["POST"])
@login_required
def clear_office_data():
    """指定した営業所の全社員データをクリア（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json
    office_code = data.get('office_code')

    if not office_code:
        return jsonify({"error": "営業所コードが必要です"}), 400

    try:
        # 営業所の存在確認
        office = Office.query.filter_by(office_code=office_code).first()
        if not office:
            return jsonify({"error": "営業所が見つかりません"}), 404

        # 該当営業所の社員を論理削除
        employees = Employee.query.filter_by(
            office_code=office_code,
            is_deleted=False
        ).all()

        deleted_count = len(employees)

        for emp in employees:
            emp.is_deleted = True
            emp.updated_at = datetime.now()

        db.session.commit()

        return jsonify({
            "success": True,
            "deleted_count": deleted_count,
            "office_name": office.office_name
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"データ削除エラー: {str(e)}"}), 500


# ===== 他DSTTツール連携API =====

@pluslist_bp.route("/api/search_employee")
@login_required
def search_employee_api():
    """他のツールから社員情報を検索するAPI"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    query_str = request.args.get('q', '')
    if not query_str:
        return jsonify([])

    # 社員番号または名前で検索（退職者・削除済みは在籍者として返さない）
    employees = Employee.query.filter(
        Employee.office_code.in_(user_offices),
        Employee.is_deleted == False,
        Employee.is_retired == False,
        or_(
            Employee.employee_number.like(f"%{query_str}%"),
            Employee.employee_name.like(f"%{query_str}%"),
            Employee.employee_kana.like(f"%{query_str}%")
        )
    ).limit(10).all()

    return jsonify([{
        'employee_number': e.employee_number,
        'employee_name': e.employee_name,
        'office_name': e.office_name,
        'job_title': e.job_title,
        'postal_code': e.postal_code,
        'address1': e.address1,
        'address2': e.address2,
        'mansion_name': e.mansion_name
    } for e in employees])


# ===== 賃金情報機能 =====

def get_salary_mapping_file_path():
    """マッピング設定ファイルのパスを取得"""
    return os.path.join(get_data_path(), 'salary_mapping.json')


def load_salary_mappings_from_db():
    """DBからマッピング情報を取得"""
    mappings = SalaryMapping.query.order_by(SalaryMapping.sort_order).all()
    return {m.item_id: m for m in mappings}


def read_salary_file(file_path, original_filename=None):
    """
    賃金情報ファイル（ファイルB）を読み込む
    .xls形式（Excel 97-2003）のみ対応
    """
    if original_filename:
        file_ext = os.path.splitext(original_filename)[1].lower()
    else:
        file_ext = os.path.splitext(file_path)[1].lower()

    if file_ext != '.xls':
        raise ValueError(f"賃金情報ファイルは.xls形式のみ対応しています（現在: {file_ext}）")

    try:
        # xlrdで.xlsファイルを読み込み
        df = pd.read_excel(file_path, engine='xlrd', dtype=str)
    except Exception as e:
        raise ValueError(f".xlsファイルの読み込みに失敗しました: {str(e)}")

    # 列名の前後の空白を削除
    df.columns = df.columns.str.strip()

    return df


def parse_salary_row(row, mappings):
    """
    賃金データ行をパースする
    row: DataFrameの1行
    mappings: {item_id: SalaryMapping} の辞書

    Returns:
        {
            'employee_number': 社員番号,
            'employee_name': 社員名,
            'salary_items': [{item_id, amount}, ...],
            'skipped_items': [item_id, ...],  # マッピングにない項目ID
            'errors': [エラーメッセージ, ...]
        }
    """
    result = {
        'employee_number': None,
        'employee_name': None,
        'salary_items': [],
        'skipped_items': [],
        'errors': []
    }

    # A列: 社員番号、B列: 社員名名称
    columns = row.index.tolist()

    if len(columns) < 2:
        result['errors'].append('列数が不足しています')
        return result

    # 社員番号を取得（A列）
    emp_number = row.iloc[0]
    if pd.isna(emp_number) or str(emp_number).strip() == '':
        result['errors'].append('社員番号が空です')
        return result

    result['employee_number'] = str(emp_number).strip()

    # 社員名を取得（B列）
    emp_name = row.iloc[1]
    result['employee_name'] = str(emp_name).strip() if pd.notna(emp_name) else ''

    # C列以降: 項目ID, 項目名称, 金額 の3列セットで繰り返し
    col_idx = 2  # C列から開始
    while col_idx + 2 < len(columns):
        item_id_val = row.iloc[col_idx]
        # item_name_val = row.iloc[col_idx + 1]  # 項目名称（使用しない）
        amount_val = row.iloc[col_idx + 2]

        # 空欄チェック（項目IDが空なら終了）
        if pd.isna(item_id_val) or str(item_id_val).strip() == '':
            col_idx += 3
            continue

        item_id = str(int(float(item_id_val))) if isinstance(item_id_val, (int, float)) else str(item_id_val).strip()

        # マッピング確認
        if item_id not in mappings:
            result['skipped_items'].append(item_id)
            col_idx += 3
            continue

        # 金額を取得
        try:
            if pd.isna(amount_val) or str(amount_val).strip() == '':
                amount = 0
            else:
                amount = int(float(amount_val))
        except (ValueError, TypeError):
            result['errors'].append(f'項目ID {item_id} の金額が不正です: {amount_val}')
            col_idx += 3
            continue

        result['salary_items'].append({
            'item_id': item_id,
            'amount': amount
        })

        col_idx += 3

    return result


@pluslist_bp.route("/api/admin/salary-mappings", methods=["GET"])
@login_required
def get_salary_mappings():
    """賃金項目マッピング一覧を取得（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    mappings = SalaryMapping.query.order_by(SalaryMapping.sort_order).all()
    return jsonify([m.to_dict() for m in mappings])


@pluslist_bp.route("/api/admin/salary-mapping", methods=["POST"])
@login_required
def create_salary_mapping():
    """賃金項目マッピングを新規作成（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json
    item_id = data.get('item_id', '').strip()
    display_name = data.get('display_name', '').strip()
    column_key = data.get('column_key', '').strip()
    sort_order = data.get('sort_order', 0)

    if not item_id or not display_name or not column_key:
        return jsonify({"error": "項目ID、表示名、DBキーは必須です"}), 400

    # 重複チェック
    existing_id = SalaryMapping.query.filter_by(item_id=item_id).first()
    if existing_id:
        return jsonify({"error": f"項目ID '{item_id}' は既に登録されています"}), 400

    existing_key = SalaryMapping.query.filter_by(column_key=column_key).first()
    if existing_key:
        return jsonify({"error": f"DBキー '{column_key}' は既に使用されています"}), 400

    try:
        mapping = SalaryMapping(
            item_id=item_id,
            display_name=display_name,
            column_key=column_key,
            sort_order=sort_order
        )
        db.session.add(mapping)
        db.session.commit()

        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"作成エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/admin/salary-mapping/<item_id>", methods=["PUT"])
@login_required
def update_salary_mapping(item_id):
    """賃金項目マッピングを更新（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    mapping = SalaryMapping.query.filter_by(item_id=item_id).first()
    if not mapping:
        return jsonify({"error": "マッピングが見つかりません"}), 404

    data = request.json
    display_name = data.get('display_name', '').strip()
    column_key = data.get('column_key', '').strip()
    sort_order = data.get('sort_order')

    if display_name:
        mapping.display_name = display_name

    if column_key:
        # 重複チェック（自分以外）
        existing = SalaryMapping.query.filter(
            SalaryMapping.column_key == column_key,
            SalaryMapping.item_id != item_id
        ).first()
        if existing:
            return jsonify({"error": f"DBキー '{column_key}' は既に使用されています"}), 400
        mapping.column_key = column_key

    if sort_order is not None:
        mapping.sort_order = sort_order

    try:
        db.session.commit()
        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/admin/salary-mapping/<item_id>", methods=["DELETE"])
@login_required
def delete_salary_mapping(item_id):
    """賃金項目マッピングを削除（管理者のみ）"""
    user_id = str(current_user.username)

    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    mapping = SalaryMapping.query.filter_by(item_id=item_id).first()
    if not mapping:
        return jsonify({"error": "マッピングが見つかりません"}), 404

    try:
        # 関連する賃金データも削除
        EmployeeSalary.query.filter_by(item_id=item_id).delete()
        db.session.delete(mapping)
        db.session.commit()

        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"削除エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/upload-salary", methods=["POST"])
@login_required
def upload_salary_file():
    """賃金情報ファイル（ファイルB）のアップロード＆プレビュー"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    if not user_offices:
        return jsonify({"error": "アクセス権限がありません"}), 403

    # ファイルチェック
    if 'file' not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "ファイルが選択されていません"}), 400

    original_filename = file.filename
    file_ext = os.path.splitext(original_filename)[1].lower()

    if file_ext != '.xls':
        return jsonify({"error": "賃金情報ファイルは.xls形式のみ対応しています"}), 400

    try:
        # マッピング情報を取得
        mappings = load_salary_mappings_from_db()

        if not mappings:
            return jsonify({
                "error": "賃金項目マッピングが設定されていません。管理者設定から設定してください。"
            }), 400

        # 一時ファイルとして保存
        filename = secure_filename(file.filename)
        uploads_path = os.path.join(get_data_path(), 'uploads')
        os.makedirs(uploads_path, exist_ok=True)
        temp_path = os.path.join(uploads_path, f"salary_temp_{user_id}_{filename}")
        file.save(temp_path)

        # ファイル読み込み
        df = read_salary_file(temp_path, original_filename)

        # ヘッダー行をスキップ（1行目がヘッダー）
        if len(df) == 0:
            return jsonify({"error": "データがありません"}), 400

        # 結果集計
        preview_data = []
        all_skipped_items = set()
        errors = []
        success_count = 0
        skip_count = 0
        error_count = 0

        for idx, row in df.iterrows():
            result = parse_salary_row(row, mappings)

            if result['errors']:
                errors.extend([f"行{idx+2}: {e}" for e in result['errors']])
                error_count += 1
                continue

            if not result['employee_number']:
                continue

            # スキップされた項目を集計
            all_skipped_items.update(result['skipped_items'])

            if result['salary_items']:
                # 社員の存在確認
                employee = Employee.query.filter_by(
                    employee_number=result['employee_number'],
                    is_deleted=False
                ).first()

                employee_exists = employee is not None
                has_access = employee_exists and employee.office_code in user_offices

                preview_data.append({
                    'row_number': idx + 2,
                    'employee_number': result['employee_number'],
                    'employee_name': result['employee_name'],
                    'salary_items': result['salary_items'],
                    'skipped_items': result['skipped_items'],
                    'employee_exists': employee_exists,
                    'has_access': has_access
                })

                if employee_exists and has_access:
                    success_count += 1
                else:
                    skip_count += 1

        # スキップされた項目IDに警告
        skipped_warnings = []
        if all_skipped_items:
            skipped_warnings.append(
                f"マッピングにない項目ID（スキップされました）: {', '.join(sorted(all_skipped_items))}"
            )

        # 元ファイルはここ以降参照しないため削除し、平文の賃金データを残さない
        _safe_unlink(temp_path)

        # セッション情報は暗号化して保存
        session_key = f"salary_upload_{user_id}_{datetime.now().timestamp()}"
        session_data = {
            'filename': original_filename,
            'preview_data': preview_data
        }

        session_file = _session_path(uploads_path, session_key)
        try:
            _encrypt_session_json(session_file, session_data)
        except Exception:
            _safe_unlink(session_file)
            raise

        return jsonify({
            "success": True,
            "session_key": session_key,
            "preview": preview_data[:50],  # プレビューは最大50件
            "stats": {
                "total_rows": len(df),
                "success_count": success_count,
                "skip_count": skip_count,
                "error_count": error_count
            },
            "errors": errors[:20],  # エラーは最大20件
            "warnings": skipped_warnings
        })

    except Exception as e:
        return jsonify({"error": f"ファイル処理エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/import-salary", methods=["POST"])
@login_required
def import_salary_data():
    """賃金データのインポート実行"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    data = request.json
    session_key = data.get('session_key')

    if not session_key:
        return jsonify({"error": "セッションキーが必要です"}), 400

    try:
        # セッション情報を読み込み（暗号化優先、旧形式フォールバック）
        uploads_path = os.path.join(get_data_path(), 'uploads')
        session_file, session_data = _load_session_payload(uploads_path, session_key)

        if session_data is None:
            return jsonify({"error": "セッションが期限切れです"}), 400

        preview_data = session_data['preview_data']

        # データベースに反映
        success_count = 0
        skip_count = 0
        errors = []

        for item in preview_data:
            if not item['employee_exists'] or not item['has_access']:
                skip_count += 1
                continue

            employee_number = item['employee_number']

            try:
                # 既存の賃金データを削除（置き換え）
                EmployeeSalary.query.filter_by(employee_number=employee_number).delete()

                # 新しい賃金データを登録
                for salary_item in item['salary_items']:
                    salary = EmployeeSalary(
                        employee_number=employee_number,
                        item_id=salary_item['item_id'],
                        amount=salary_item['amount'],
                        uploaded_by=user_id
                    )
                    db.session.add(salary)

                success_count += 1

            except Exception as e:
                errors.append(f"社員番号 {employee_number}: {str(e)}")
                skip_count += 1

        # アップロード履歴を記録
        history = SalaryUploadHistory(
            uploaded_by=user_id,
            filename=session_data['filename'],
            success_count=success_count,
            skip_count=skip_count,
            error_count=len(errors),
            total_rows=len(preview_data)
        )
        db.session.add(history)

        db.session.commit()

        # 暗号化セッション／残存していた一時ファイルを削除
        _safe_unlink(session_data.get('file_path') or "")
        _safe_unlink(session_file)

        return jsonify({
            "success": True,
            "stats": {
                "success_count": success_count,
                "skip_count": skip_count,
                "error_count": len(errors)
            },
            "errors": errors[:10]
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"インポートエラー: {str(e)}"}), 500


@pluslist_bp.route("/api/salary-mappings-public", methods=["GET"])
@login_required
def get_salary_mappings_public():
    """賃金項目マッピング一覧を取得（列選択用、全ユーザー可）"""
    mappings = SalaryMapping.query.order_by(SalaryMapping.sort_order).all()
    return jsonify([{
        'item_id': m.item_id,
        'display_name': m.display_name,
        'column_key': m.column_key
    } for m in mappings])


@pluslist_bp.route("/api/employee-salaries/<employee_number>", methods=["GET"])
@login_required
def get_employee_salaries(employee_number):
    """特定社員の賃金データを取得"""
    user_id = str(current_user.username)

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if not employee:
        return jsonify({"error": "社員が見つかりません"}), 404

    if not has_office_access(user_id, employee.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    salaries = EmployeeSalary.query.filter_by(employee_number=employee_number).all()

    # マッピング情報と結合
    mappings = {m.item_id: m for m in SalaryMapping.query.all()}

    result = {}
    for s in salaries:
        if s.item_id in mappings:
            mapping = mappings[s.item_id]
            result[mapping.column_key] = {
                'item_id': s.item_id,
                'display_name': mapping.display_name,
                'amount': s.amount
            }

    return jsonify(result)


@pluslist_bp.route("/api/histories/salary-upload")
@login_required
def get_salary_upload_histories():
    """賃金ファイルアップロード履歴を取得"""
    user_id = str(current_user.username)
    query = SalaryUploadHistory.query
    if not is_admin(user_id):
        query = query.filter_by(uploaded_by=user_id)
    histories = query.order_by(
        SalaryUploadHistory.uploaded_at.desc()
    ).limit(100).all()

    return jsonify([{
        'id': h.id,
        'uploaded_by': h.uploaded_by,
        'uploaded_at': h.uploaded_at.isoformat(),
        'filename': h.filename,
        'success_count': h.success_count,
        'skip_count': h.skip_count,
        'error_count': h.error_count,
        'total_rows': h.total_rows
    } for h in histories])


# ===== 連絡先情報機能（ファイルC: メールアドレス / 会社携帯） =====

def _pad_leading_zeros(value, width):
    """Excelで先頭の0が落ちた数値を文字列化し、指定桁数まで0埋めして返す。

    社員番号(7桁)・電話番号(11桁)はExcel上で数値として扱われ頭の0が
    消えてしまうため、サーバー側で数字のみを抽出して左ゼロ埋めする。
    値が無い場合は None を返す。
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    s = str(value).strip()
    if s == '' or s.lower() in ('nan', 'nat', 'none'):
        return None

    # Excel由来で "12345.0" のように小数点が付くケースを救済
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]

    # 数字以外（ハイフン等）は除去してから0埋め
    digits = ''.join(ch for ch in s if ch.isdigit())
    if digits == '':
        return None

    # 想定桁数を超える場合はゼロ埋めせずそのまま返す（切り捨てない）
    if len(digits) >= width:
        return digits
    return digits.zfill(width)


def _clean_email(value):
    """メールアドレスセルを文字列に整形する。空欄は None。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if s == '' or s.lower() in ('nan', 'nat', 'none'):
        return None
    return s


def read_contact_file(file_path, original_filename=None):
    """
    連絡先情報ファイル（ファイルC）を読み込む。
    .xlsx形式のみ対応。1行目はヘッダーのため列位置で読み取る（header=None）。
        B列(index1): 社員番号
        D列(index3): メールアドレス
        H列(index7): 電話番号（会社携帯）
    """
    if original_filename:
        file_ext = os.path.splitext(original_filename)[1].lower()
    else:
        file_ext = os.path.splitext(file_path)[1].lower()

    if file_ext != '.xlsx':
        raise ValueError(f"連絡先情報ファイルは.xlsx形式のみ対応しています（現在: {file_ext}）")

    try:
        # 列位置で扱うため header=None。前ゼロ保持のため全列を文字列で読み込む
        df = pd.read_excel(file_path, engine='openpyxl', header=None, dtype=str)
    except Exception as e:
        raise ValueError(f".xlsxファイルの読み込みに失敗しました: {str(e)}")

    return df


def parse_contact_row(row):
    """
    連絡先データ行（ファイルC）をパースする。
    Returns: {'employee_number', 'email', 'company_mobile'}
    """
    def _cell(idx):
        return row.iloc[idx] if len(row) > idx else None

    return {
        'employee_number': _pad_leading_zeros(_cell(1), 7),   # B列・7桁
        'email': _clean_email(_cell(3)),                      # D列
        'company_mobile': _pad_leading_zeros(_cell(7), 11),   # H列・11桁
    }


@pluslist_bp.route("/api/upload-contact", methods=["POST"])
@login_required
def upload_contact_file():
    """連絡先情報ファイル（ファイルC）のアップロード＆プレビュー"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)

    if not user_offices:
        return jsonify({"error": "アクセス権限がありません"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "ファイルが選択されていません"}), 400

    original_filename = file.filename
    file_ext = os.path.splitext(original_filename)[1].lower()

    if file_ext != '.xlsx':
        return jsonify({"error": "連絡先情報ファイルは.xlsx形式のみ対応しています"}), 400

    temp_path = None
    try:
        filename = secure_filename(file.filename)
        uploads_path = os.path.join(get_data_path(), 'uploads')
        os.makedirs(uploads_path, exist_ok=True)
        temp_path = os.path.join(uploads_path, f"contact_temp_{user_id}_{filename}")
        file.save(temp_path)

        df = read_contact_file(temp_path, original_filename)

        # 1行目はヘッダーのためスキップ（2行目以降がデータ）
        data_rows = df.iloc[1:] if len(df) > 0 else df

        preview_data = []
        errors = []
        success_count = 0
        skip_count = 0
        error_count = 0

        for idx, row in data_rows.iterrows():
            parsed = parse_contact_row(row)

            # 社員番号が無い行はスキップ（空行など）
            if not parsed['employee_number']:
                continue

            # メール・会社携帯がいずれも無い行は対象外
            if not parsed['email'] and not parsed['company_mobile']:
                continue

            employee = Employee.query.filter_by(
                employee_number=parsed['employee_number'],
                is_deleted=False
            ).first()

            employee_exists = employee is not None
            has_access = employee_exists and employee.office_code in user_offices

            preview_data.append({
                'row_number': int(idx) + 1,
                'employee_number': parsed['employee_number'],
                'email': parsed['email'],
                'company_mobile': parsed['company_mobile'],
                'employee_name': employee.employee_name if employee_exists else '',
                'employee_exists': employee_exists,
                'has_access': has_access
            })

            if employee_exists and has_access:
                success_count += 1
            else:
                skip_count += 1

        # 元ファイルは平文で残さず削除
        _safe_unlink(temp_path)
        temp_path = None

        if not preview_data:
            return jsonify({
                "error": "アップロード可能なデータがありません",
                "details": "社員番号・メールアドレス・会社携帯のいずれも読み取れませんでした"
            }), 400

        session_key = f"contact_upload_{user_id}_{datetime.now().timestamp()}"
        session_data = {
            'filename': original_filename,
            'preview_data': preview_data
        }

        session_file = _session_path(uploads_path, session_key)
        try:
            _encrypt_session_json(session_file, session_data)
        except Exception:
            _safe_unlink(session_file)
            raise

        return jsonify({
            "success": True,
            "session_key": session_key,
            "preview": preview_data[:50],
            "stats": {
                "total_rows": len(data_rows),
                "success_count": success_count,
                "skip_count": skip_count,
                "error_count": error_count
            },
            "errors": errors[:20]
        })

    except Exception as e:
        try:
            if temp_path:
                _safe_unlink(temp_path)
        except Exception:
            pass
        return jsonify({"error": f"ファイル処理エラー: {str(e)}"}), 500


@pluslist_bp.route("/api/import-contact", methods=["POST"])
@login_required
def import_contact_data():
    """連絡先データ（ファイルC）のインポート実行"""
    user_id = str(current_user.username)
    user_offices = set(get_user_offices(user_id))

    data = request.json
    session_key = data.get('session_key')

    if not session_key:
        return jsonify({"error": "セッションキーが必要です"}), 400

    try:
        uploads_path = os.path.join(get_data_path(), 'uploads')
        session_file, session_data = _load_session_payload(uploads_path, session_key)

        if session_data is None:
            return jsonify({"error": "セッションが期限切れです"}), 400

        preview_data = session_data['preview_data']

        success_count = 0
        skip_count = 0
        errors = []

        for item in preview_data:
            # セッションを信用せず、権限と存在を再検証する
            employee = Employee.query.filter_by(
                employee_number=item['employee_number'],
                is_deleted=False
            ).first()

            if employee is None or employee.office_code not in user_offices:
                skip_count += 1
                continue

            try:
                changed = False
                # 値がある項目のみ更新し、空欄での意図しない消去を防ぐ。
                # 一括取り込みのため個別の編集履歴(EditHistory)は残さない
                # （ファイルA/Bと同様。全体の記録は ContactUploadHistory に残す）
                if item.get('email') and employee.email != item['email']:
                    employee.email = item['email']
                    changed = True
                if item.get('company_mobile') and employee.company_mobile != item['company_mobile']:
                    employee.company_mobile = item['company_mobile']
                    changed = True

                if changed:
                    employee.updated_at = datetime.utcnow()
                success_count += 1

            except Exception as e:
                errors.append(f"社員番号 {item['employee_number']}: {str(e)}")
                skip_count += 1

        history = ContactUploadHistory(
            uploaded_by=user_id,
            filename=session_data['filename'],
            success_count=success_count,
            skip_count=skip_count,
            error_count=len(errors),
            total_rows=len(preview_data)
        )
        db.session.add(history)

        db.session.commit()

        _safe_unlink(session_data.get('file_path') or "")
        _safe_unlink(session_file)

        return jsonify({
            "success": True,
            "stats": {
                "success_count": success_count,
                "skip_count": skip_count,
                "error_count": len(errors)
            },
            "errors": errors[:10]
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"インポートエラー: {str(e)}"}), 500


@pluslist_bp.route("/api/histories/contact-upload")
@login_required
def get_contact_upload_histories():
    """連絡先ファイル（ファイルC）アップロード履歴を取得"""
    user_id = str(current_user.username)
    query = ContactUploadHistory.query
    if not is_admin(user_id):
        query = query.filter_by(uploaded_by=user_id)
    histories = query.order_by(
        ContactUploadHistory.uploaded_at.desc()
    ).limit(100).all()

    return jsonify([{
        'id': h.id,
        'uploaded_by': h.uploaded_by,
        'uploaded_at': h.uploaded_at.isoformat(),
        'filename': h.filename,
        'success_count': h.success_count,
        'skip_count': h.skip_count,
        'error_count': h.error_count,
        'total_rows': h.total_rows
    } for h in histories])
