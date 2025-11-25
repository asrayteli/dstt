from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
import os
import csv
import traceback
from datetime import datetime

subject_analysis_tool_bp = Blueprint("subject_analysis_tool", __name__, url_prefix="/tools/subject_analysis_tool")


def get_upload_folder():
    """一時アップロードフォルダのパスを取得"""
    folder = os.path.join(current_app.root_path, 'static', 'subject_analysis_tool', 'uploads')
    os.makedirs(folder, exist_ok=True)
    return folder


@subject_analysis_tool_bp.route("/")
@login_required
def index():
    """メインページ"""
    return render_template("subject_analysis_tool.html")


@subject_analysis_tool_bp.route("/api/upload", methods=["POST"])
@login_required
def upload_files():
    """ファイルアップロードとデータパース"""
    try:
        # ファイル取得
        subject_file = request.files.get('subject_file')  # 科目別推移表（必須）
        prev_year_subject_file = request.files.get('prev_year_subject_file')  # 前年科目別推移表（オプション）
        site_file = request.files.get('site_file')  # 現場表（オプション）

        if not subject_file:
            return jsonify({"error": "科目別推移表が必要です"}), 400

        # ファイル保存
        upload_folder = get_upload_folder()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        user_id = current_user.username

        subject_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_subject.csv")
        subject_file.save(subject_path)

        # メインデータ読み込み
        subject_data = read_csv_with_encoding(subject_path)
        if not subject_data:
            return jsonify({"error": "科目別推移表の読み込みに失敗しました"}), 400

        # データパース
        parsed_data = parse_subject_data(subject_data)

        # 前年データ読み込み（オプション）
        prev_year_data = None
        if prev_year_subject_file and prev_year_subject_file.filename:
            prev_year_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_prev_subject.csv")
            prev_year_subject_file.save(prev_year_path)
            prev_year_csv = read_csv_with_encoding(prev_year_path)
            if prev_year_csv:
                prev_year_data = parse_subject_data(prev_year_csv)
            # ファイル削除
            try:
                os.remove(prev_year_path)
            except:
                pass

        # 現場表読み込み（オプション）
        site_data = None
        if site_file and site_file.filename:
            site_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_site.csv")
            site_file.save(site_path)
            site_csv = read_csv_with_encoding(site_path)
            if site_csv:
                site_data = parse_site_data(site_csv)
            # ファイル削除
            try:
                os.remove(site_path)
            except:
                pass

        # ファイル削除
        try:
            os.remove(subject_path)
        except:
            pass

        # レスポンス構築
        response = {
            "success": True,
            "data": {
                "current_year": parsed_data,
                "prev_year": prev_year_data,
                "site_mapping": site_data
            },
            "metadata": {
                "current_year_count": len(parsed_data),
                "prev_year_count": len(prev_year_data) if prev_year_data else 0,
                "site_count": len(site_data) if site_data else 0
            }
        }

        return jsonify(response)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"処理中にエラーが発生しました: {str(e)}"}), 500


def read_csv_with_encoding(file_path):
    """複数のエンコーディングでCSVを読み込む"""
    encodings = ['utf-8', 'shift_jis', 'cp932', 'utf-8-sig']

    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                reader = csv.reader(f)
                data = list(reader)
                return data
        except:
            continue

    return None


def parse_subject_data(csv_data):
    """科目別推移表をパースして構造化

    Returns:
        list: [
            {
                "contract_code": "12345678",
                "corp_name": "○○法人",
                "site_name": "××現場",
                "subject_code": "科目コード",
                "subject_name": "科目名称",
                "contract_type": "請負形態名称",
                "amounts": [4月, 5月, ..., 3月] (12ヶ月分)
            },
            ...
        ]
    """
    parsed = []

    for i, row in enumerate(csv_data):
        if i == 0:  # ヘッダー行をスキップ
            continue
        if len(row) < 13:
            continue

        contract_code = row[8].strip() if len(row) > 8 else ""
        corp_name = row[9].strip() if len(row) > 9 else ""
        site_name = row[10].strip() if len(row) > 10 else ""
        subject_code = row[11].strip() if len(row) > 11 else ""
        subject_name = row[12].strip() if len(row) > 12 else ""
        contract_type = row[5].strip() if len(row) > 5 else ""

        # 科目コードが「販売費」の場合はスキップ
        if subject_code == "販売費":
            continue

        # 科目名称が空の場合は、間接原価以外はスキップ
        if not subject_name and subject_code != "間接原価":
            continue

        # 契約コードが空の場合はスキップ
        if not contract_code:
            continue

        # 自動車売上の場合は、契約形態名称を科目名称として使用
        # （基本請負料とその他請負料を別々に扱うため）
        display_subject_name = subject_name
        if subject_name == "自動車売上":
            if contract_type in ["基本請負料", "その他請負料"]:
                display_subject_name = contract_type
            else:
                # 想定外の契約形態の場合は元の名前を使用
                display_subject_name = f"自動車売上({contract_type})"

        # 金額データ抽出（列13以降が各月のデータ、4月から開始）
        amounts = []
        for j in range(13, len(row)):
            try:
                amount = float(row[j]) if row[j] else 0
                amounts.append(amount)
            except:
                amounts.append(0)

        # 12ヶ月分のデータがない場合は0で埋める
        while len(amounts) < 12:
            amounts.append(0)

        # 原価の符号を反転（売上以外はマイナス→プラス、プラス→マイナス）
        # 売上は「基本請負料」「その他請負料」のみ
        is_revenue = display_subject_name in ["基本請負料", "その他請負料"]

        if not is_revenue:
            # 原価の場合は符号を反転
            amounts = [-amount for amount in amounts]

        parsed.append({
            "contract_code": contract_code,
            "corp_name": corp_name,
            "site_name": site_name,
            "subject_code": subject_code,
            "subject_name": display_subject_name,  # 表示用の科目名
            "original_subject_name": subject_name,  # 元の科目名（参照用）
            "contract_type": contract_type,
            "is_revenue": is_revenue,  # 売上かどうかのフラグ
            "amounts": amounts[:12]  # 12ヶ月分のみ
        })

    return parsed


def parse_site_data(csv_data):
    """現場表をパースして辞書化

    Returns:
        dict: {
            "契約コード": "セグメント",
            ...
        }
    """
    site_dict = {}

    for i, row in enumerate(csv_data):
        if i == 0:  # ヘッダー行をスキップ
            continue
        if len(row) >= 2:
            contract_code = row[0].strip()
            segment = row[1].strip()

            # 契約コードが空の場合はスキップ
            if not contract_code:
                continue

            site_dict[contract_code] = segment

    return site_dict
