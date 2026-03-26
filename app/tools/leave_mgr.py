from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
import os
import json
from datetime import datetime
import re
from pathlib import Path
from typing import Any

leave_mgr_bp = Blueprint("leave_mgr", __name__, url_prefix="/tools/leave_mgr")

# 初期管理者ID（ハードコーディング）
INITIAL_ADMIN_ID = "3243012"

# 休暇種類と色の定義
LEAVE_TYPES = {
    "有休": "#DC2626",        # 赤系
    "代休": "#10B981",        # 緑系
    "慶弔休暇": "#8B5CF6",    # 紫系
    "介護休暇": "#F97316",    # オレンジ系
    "リフレッシュ休暇": "#EC4899",  # ピンク系
    "その他": "#0EA5E9"       # 青系
}

SYNC_SOURCE_CLOUDSHIFT = "cloudshift"
SYNC_REMARK_PREFIX = "[from CloudShift]"

def get_data_path():
    """データディレクトリのパスを取得"""
    return os.path.join(current_app.root_path, 'static', 'leave_mgr')

def ensure_data_directories():
    """必要なディレクトリとファイルを作成"""
    data_path = get_data_path()
    calendars_path = os.path.join(data_path, 'calendars')
    
    # ディレクトリ作成
    os.makedirs(calendars_path, exist_ok=True)
    
    # permissions.jsonの初期化
    permissions_file = os.path.join(data_path, 'permissions.json')
    if not os.path.exists(permissions_file):
        initial_permissions = {
            "admins": [INITIAL_ADMIN_ID],
            "user_calendars": {
                INITIAL_ADMIN_ID: []
            }
        }
        with open(permissions_file, 'w', encoding='utf-8') as f:
            json.dump(initial_permissions, f, ensure_ascii=False, indent=2)
    
    # calendar_meta.jsonの初期化
    meta_file = os.path.join(data_path, 'calendar_meta.json')
    if not os.path.exists(meta_file):
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def load_permissions():
    """権限情報を読み込み"""
    permissions_file = os.path.join(get_data_path(), 'permissions.json')
    try:
        with open(permissions_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"admins": [INITIAL_ADMIN_ID], "user_calendars": {}}

def save_permissions(permissions):
    """権限情報を保存"""
    permissions_file = os.path.join(get_data_path(), 'permissions.json')
    with open(permissions_file, 'w', encoding='utf-8') as f:
        json.dump(permissions, f, ensure_ascii=False, indent=2)

def load_calendar_meta():
    """カレンダーメタ情報を読み込み"""
    meta_file = os.path.join(get_data_path(), 'calendar_meta.json')
    try:
        with open(meta_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_calendar_meta(meta):
    """カレンダーメタ情報を保存"""
    meta_file = os.path.join(get_data_path(), 'calendar_meta.json')
    with open(meta_file, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    """管理者権限チェック"""
    permissions = load_permissions()
    return user_id in permissions.get('admins', [])

def get_user_calendars(user_id):
    """ユーザーがアクセス可能なカレンダーIDリストを取得"""
    permissions = load_permissions()
    return permissions.get('user_calendars', {}).get(user_id, [])

def validate_calendar_id(calendar_id):
    """カレンダーIDのバリデーション"""
    # 英数字とアンダースコアのみ、最大20文字
    if not re.match(r'^[a-zA-Z0-9_]+$', calendar_id):
        return False, "カレンダーIDは英数字とアンダースコアのみ使用可能です"
    if len(calendar_id) > 20:
        return False, "カレンダーIDは20文字以内にしてください"
    
    # 数値のみの場合はアンダースコアを追加
    if calendar_id.isdigit():
        calendar_id = calendar_id + "_"
    
    return True, calendar_id

def load_calendar_data(calendar_id, year_month):
    """カレンダーデータを読み込み"""
    file_path = os.path.join(get_data_path(), 'calendars', f"{calendar_id}_{year_month}.json")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"leaves": []}

def save_calendar_data(calendar_id, year_month, data):
    """カレンダーデータを保存"""
    calendars_path = os.path.join(get_data_path(), 'calendars')
    os.makedirs(calendars_path, exist_ok=True)
    
    file_path = os.path.join(calendars_path, f"{calendar_id}_{year_month}.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def has_assigned_deputy(leave):
    """代務者が実質的に設定されているかを判定"""
    deputies = leave.get("deputies", [])
    if isinstance(deputies, str):
        deputies = [d.strip() for d in deputies.split(",")]
    if not isinstance(deputies, list):
        return False

    normalized = [str(d).strip() for d in deputies if str(d).strip()]
    if not normalized:
        return False

    unset_tokens = {"なし", "無し", "-", "－", "ー", "未設定", "未定"}
    return any(d not in unset_tokens for d in normalized)

def _clean_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _normalize_year_month_key(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError("year_month is required")
    normalized = text.replace("-", "")
    if not re.fullmatch(r"\d{6}", normalized):
        raise ValueError("year_month must be YYYYMM or YYYY-MM")
    return normalized


def _normalize_month_key(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError("month_key is required")
    if re.fullmatch(r"\d{6}", text):
        return f"{text[:4]}-{text[4:]}"
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    raise ValueError("month_key must be YYYYMM or YYYY-MM")


def _normalize_employee_number(value: Any) -> str:
    return _clean_text(value)


def _cloudshift_remarks(comment: Any) -> str:
    text = _clean_text(comment)
    if text:
        return f"{SYNC_REMARK_PREFIX} / {text}"
    return SYNC_REMARK_PREFIX


def _safe_source_value(value: Any) -> str:
    text = _clean_text(value)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)[:120]


def _cloudshift_leave_id(
    source_project_id: str,
    source_month_key: str,
    source_entry_id: str,
    day: str,
    index: int,
) -> str:
    raw_entry_id = source_entry_id or f"{day}-{index}"
    parts = [
        "cloudshift",
        source_project_id or "project",
        source_month_key or "month",
        raw_entry_id,
    ]
    return _safe_source_value("-".join(parts))


def _cloudshift_accessible_calendar_ids(user_id: str) -> list[str]:
    permissions = load_permissions()
    calendar_meta = load_calendar_meta()

    if is_admin(user_id):
        calendar_ids = set(calendar_meta.keys())
        calendar_ids.update(permissions.get("user_calendars", {}).get(user_id, []))
    else:
        calendar_ids = set(permissions.get("user_calendars", {}).get(user_id, []))

    return sorted(
        calendar_ids,
        key=lambda calendar_id: (
            str(calendar_meta.get(calendar_id, {}).get("name", calendar_id)).lower(),
            calendar_id,
        ),
    )


def get_cloudshift_calendar_options(user_id: str) -> list[dict[str, str]]:
    calendar_meta = load_calendar_meta()
    return [
        {
            "calendar_id": calendar_id,
            "name": str(calendar_meta.get(calendar_id, {}).get("name", calendar_id)),
        }
        for calendar_id in _cloudshift_accessible_calendar_ids(user_id)
    ]


def _cloudshift_source_matches(
    leave: dict[str, Any],
    *,
    user_id: str,
    source_project_id: str,
    source_month_key: str,
) -> bool:
    if leave.get("sync_source") != SYNC_SOURCE_CLOUDSHIFT:
        return False
    if source_project_id and str(leave.get("source_project_id", "")) != source_project_id:
        return False
    if source_month_key and str(leave.get("source_month_key", "")) != source_month_key:
        return False

    leave_user_id = str(leave.get("sync_user_id") or leave.get("created_by") or "")
    return leave_user_id == user_id


def _remove_cloudshift_source_entries(
    user_id: str,
    source_project_id: str,
    source_month_key: str,
) -> dict[str, Any]:
    removed_by_calendar: dict[str, int] = {}
    removed_total = 0
    target_year_month = source_month_key.replace("-", "")

    for calendar_id in _cloudshift_accessible_calendar_ids(user_id):
        calendar_data = load_calendar_data(calendar_id, target_year_month)
        leaves = list(calendar_data.get("leaves", []))
        kept_leaves = []
        removed_count = 0
        for leave in leaves:
            if _cloudshift_source_matches(
                leave,
                user_id=user_id,
                source_project_id=source_project_id,
                source_month_key=source_month_key,
            ):
                removed_count += 1
                continue
            kept_leaves.append(leave)

        if removed_count:
            calendar_data["leaves"] = kept_leaves
            save_calendar_data(calendar_id, target_year_month, calendar_data)
            removed_by_calendar[calendar_id] = removed_count
            removed_total += removed_count

    return {"removed_total": removed_total, "removed_by_calendar": removed_by_calendar}


def _normalize_cloudshift_leave(
    payload: dict[str, Any],
    *,
    user_id: str,
    calendar_id: str,
    target_year_month: str,
    source_project_id: str,
    source_month_key: str,
    index: int,
) -> tuple[dict[str, Any] | None, str | None]:
    date = _clean_text(payload.get("date"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None, "invalid date"
    if date.replace("-", "")[:6] != target_year_month:
        return None, "date does not match target month"

    leave_type = _clean_text(payload.get("leave_type"))
    if leave_type not in LEAVE_TYPES:
        return None, "invalid leave_type"

    name = _clean_text(payload.get("name"))
    if not name:
        return None, "name is required"

    employee_number = _normalize_employee_number(payload.get("employee_number"))
    if not employee_number:
        return None, "employee_number is required"

    source_entry_id = _clean_text(
        payload.get("source_entry_id")
        or payload.get("id")
        or payload.get("source_id")
    )

    leave = {
        "id": _cloudshift_leave_id(
            source_project_id,
            source_month_key,
            source_entry_id,
            date.replace("-", ""),
            index,
        ),
        "date": date,
        "name": name,
        "employee_number": employee_number,
        "leave_type": leave_type,
        "deputies": [],
        "remarks": _cloudshift_remarks(payload.get("comment") or payload.get("remarks")),
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "confirmed_by": None,
        "confirmed_at": None,
        "sync_source": SYNC_SOURCE_CLOUDSHIFT,
        "sync_user_id": user_id,
        "source_project_id": source_project_id,
        "source_month_key": source_month_key,
        "source_entry_id": source_entry_id,
        "source_entry_index": index,
        "source_calendar_id": calendar_id,
        "target_year_month": target_year_month,
    }
    return leave, None


def replace_cloudshift_leaves(
    *,
    user_id: str,
    calendar_id: str,
    target_year_month: str,
    source_project_id: str,
    source_month_key: str,
    leaves: list[dict[str, Any]],
) -> dict[str, Any]:
    if calendar_id not in _cloudshift_accessible_calendar_ids(user_id):
        raise PermissionError("calendar is not accessible")

    cleanup = _remove_cloudshift_source_entries(user_id, source_project_id, source_month_key)

    calendar_data = load_calendar_data(calendar_id, target_year_month)
    existing_leaves = list(calendar_data.get("leaves", []))
    normalized_leaves: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for index, payload in enumerate(leaves):
        if not isinstance(payload, dict):
            skipped.append(
                {
                    "index": str(index),
                    "reason": "invalid entry type",
                }
            )
            continue
        normalized, reason = _normalize_cloudshift_leave(
            payload,
            user_id=user_id,
            calendar_id=calendar_id,
            target_year_month=target_year_month,
            source_project_id=source_project_id,
            source_month_key=source_month_key,
            index=index,
        )
        if normalized is None:
            skipped.append(
                {
                    "index": str(index),
                    "reason": reason or "invalid entry",
                }
            )
            continue
        normalized_leaves.append(normalized)

    calendar_data["leaves"] = existing_leaves + normalized_leaves
    save_calendar_data(calendar_id, target_year_month, calendar_data)

    return {
        "calendar_id": calendar_id,
        "year_month": target_year_month,
        "removed_total": cleanup["removed_total"],
        "removed_by_calendar": cleanup["removed_by_calendar"],
        "created_total": len(normalized_leaves),
        "skipped_total": len(skipped),
        "skipped": skipped,
    }


@leave_mgr_bp.route("/")
@login_required
def index():
    """メインページ"""
    ensure_data_directories()
    
    # 現在のユーザーID（usernameを使用）
    user_id = str(current_user.username)
    
    # ユーザーの権限情報
    is_admin_user = is_admin(user_id)
    user_calendars = get_user_calendars(user_id)
    
    # カレンダーメタ情報
    calendar_meta = load_calendar_meta()
    
    return render_template(
        "leave_mgr.html",
        user_id=user_id,
        is_admin=is_admin_user,
        user_calendars=user_calendars,
        calendar_meta=calendar_meta,
        leave_types=LEAVE_TYPES
    )

@leave_mgr_bp.route("/api/calendar/<calendar_id>/<year_month>")
@login_required
def get_calendar(calendar_id, year_month):
    """カレンダーデータを取得"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    data = load_calendar_data(calendar_id, year_month)
    return jsonify(data)

@leave_mgr_bp.route("/api/search")
@login_required
def search_leaves():
    """休暇を検索"""
    user_id = str(current_user.username)

    calendar_id = request.args.get("calendar_id", "").strip()
    if not calendar_id:
        return jsonify({"error": "calendar_idは必須です"}), 400

    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    specific_month = request.args.get("specific_month", "").strip()  # YYYY-MM
    name = request.args.get("name", "").strip()
    employee_number = request.args.get("employee_number", "").strip()
    leave_type = request.args.get("leave_type", "").strip()
    deputy = request.args.get("deputy", "").strip()
    remarks = request.args.get("remarks", "").strip().lower()
    confirmed = request.args.get("confirmed", "all").strip()  # all / confirmed / unconfirmed
    deputy_status = request.args.get("deputy_status", "all").strip()  # all / set / unset

    calendars_path = os.path.join(get_data_path(), "calendars")
    if not os.path.exists(calendars_path):
        return jsonify({"results": [], "count": 0})

    results = []
    prefix = f"{calendar_id}_"

    for filename in os.listdir(calendars_path):
        if not filename.startswith(prefix) or not filename.endswith(".json"):
            continue

        year_month = filename[len(prefix):-5]
        if specific_month:
            target_ym = specific_month.replace("-", "")
            if year_month != target_ym:
                continue

        file_path = os.path.join(calendars_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        for leave in data.get("leaves", []):
            leave_date = str(leave.get("date", ""))
            leave_name = str(leave.get("name", ""))
            leave_emp_no = str(leave.get("employee_number", ""))
            leave_type_value = str(leave.get("leave_type", ""))
            leave_deputies = leave.get("deputies", [])
            leave_remarks = str(leave.get("remarks", ""))
            is_confirmed = bool(leave.get("confirmed_by"))
            has_deputy = has_assigned_deputy(leave)

            if date_from and leave_date and leave_date < date_from:
                continue
            if date_to and leave_date and leave_date > date_to:
                continue
            if name and leave_name.strip() != name:
                continue
            if employee_number and employee_number not in leave_emp_no:
                continue
            if leave_type and leave_type != leave_type_value:
                continue
            if deputy:
                if isinstance(leave_deputies, list):
                    deputies_list = [str(d).strip() for d in leave_deputies]
                elif isinstance(leave_deputies, str):
                    deputies_list = [d.strip() for d in re.split(r"[,、]", leave_deputies)]
                else:
                    deputies_list = []
                if deputy not in deputies_list:
                    continue
            if remarks and remarks not in leave_remarks.lower():
                continue
            if confirmed == "confirmed" and not is_confirmed:
                continue
            if confirmed == "unconfirmed" and is_confirmed:
                continue
            if deputy_status == "set" and not has_deputy:
                continue
            if deputy_status == "unset" and has_deputy:
                continue

            item = dict(leave)
            item["year_month"] = year_month
            item["has_assigned_deputy"] = has_deputy
            results.append(item)

    results.sort(key=lambda x: (x.get("date", ""), x.get("name", "")))
    return jsonify({"results": results, "count": len(results)})

@leave_mgr_bp.route("/api/leave", methods=["POST"])
@login_required
def add_leave():
    """休暇を追加"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    # 重複チェック（force=Trueの場合はスキップ）
    if not data.get('force', False):
        date = data.get('date')
        name = data.get('name')
        existing = [l for l in calendar_data['leaves'] if l['date'] == date and l['name'] == name]
        
        if existing:
            return jsonify({"warning": "同じ名前で同日に登録されています", "existing": existing}), 409
    
    # 新しい休暇を追加
    new_leave = {
        "id": datetime.now().isoformat(),
        "date": data.get('date'),
        "name": data.get('name'),
        "employee_number": data.get('employee_number', ''),  # 社員番号（任意）
        "leave_type": data.get('leave_type'),
        "deputies": data.get('deputies', []),
        "remarks": data.get('remarks', ''),
        "created_by": user_id,  # 記入者
        "created_at": datetime.now().isoformat(),
        "confirmed_by": None,  # 確認者（初期値はNone）
        "confirmed_at": None   # 確認日時（初期値はNone）
    }
    
    calendar_data['leaves'].append(new_leave)
    
    # 保存
    save_calendar_data(calendar_id, year_month, calendar_data)
    
    return jsonify({"success": True, "leave": new_leave})

@leave_mgr_bp.route("/api/leave/<leave_id>", methods=["PUT", "DELETE"])
@login_required
def modify_leave(leave_id):
    """休暇を編集または削除"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    if request.method == "DELETE":
        # 削除権限チェック：自分が作成した休暇のみ削除可能
        leave_to_delete = None
        for leave in calendar_data['leaves']:
            if leave['id'] == leave_id:
                leave_to_delete = leave
                break
        
        if not leave_to_delete:
            return jsonify({"error": "休暇が見つかりません"}), 404
        
        # 管理者または作成者のみ削除可能
        if not is_admin(user_id) and leave_to_delete.get('created_by') != user_id:
            return jsonify({"error": "自分が登録した休暇のみ削除できます"}), 403
        
        # 削除実行
        calendar_data['leaves'] = [l for l in calendar_data['leaves'] if l['id'] != leave_id]

        # 保存
        save_calendar_data(calendar_id, year_month, calendar_data)
    else:
        # 編集
        leave_found = False
        for leave in calendar_data['leaves']:
            if leave['id'] == leave_id:
                leave_found = True
                # 編集権限チェック：管理者または作成者のみ編集可能
                if not is_admin(user_id) and leave.get('created_by') != user_id:
                    return jsonify({"error": "自分が登録した休暇のみ編集できます"}), 403

                # 新しい日付を取得
                new_date = data.get('date', leave['date'])
                new_year_month = new_date[:7].replace('-', '')  # "2026-01-15" -> "202601"

                # 月をまたぐ変更かチェック
                if new_year_month != year_month:
                    # 月をまたぐ場合：ファイル間で移動
                    # 元のファイルから削除
                    calendar_data['leaves'] = [l for l in calendar_data['leaves'] if l['id'] != leave_id]
                    save_calendar_data(calendar_id, year_month, calendar_data)

                    # データを更新
                    leave.update({
                        "date": new_date,
                        "name": data.get('name', leave['name']),
                        "employee_number": data.get('employee_number', leave.get('employee_number', '')),
                        "leave_type": data.get('leave_type', leave['leave_type']),
                        "deputies": data.get('deputies', leave['deputies']),
                        "remarks": data.get('remarks', leave['remarks']),
                        "updated_by": user_id,
                        "updated_at": datetime.now().isoformat()
                    })

                    # 新しい月のファイルに追加
                    new_calendar_data = load_calendar_data(calendar_id, new_year_month)
                    new_calendar_data['leaves'].append(leave)
                    save_calendar_data(calendar_id, new_year_month, new_calendar_data)
                else:
                    # 同月内の変更：通常の更新
                    leave.update({
                        "date": new_date,
                        "name": data.get('name', leave['name']),
                        "employee_number": data.get('employee_number', leave.get('employee_number', '')),
                        "leave_type": data.get('leave_type', leave['leave_type']),
                        "deputies": data.get('deputies', leave['deputies']),
                        "remarks": data.get('remarks', leave['remarks']),
                        "updated_by": user_id,
                        "updated_at": datetime.now().isoformat()
                    })
                    # 保存
                    save_calendar_data(calendar_id, year_month, calendar_data)

                return jsonify({"success": True})

        # 休暇が見つからなかった場合
        if not leave_found:
            return jsonify({"error": "休暇が見つかりません"}), 404

    return jsonify({"success": True})

@leave_mgr_bp.route("/api/leave/<leave_id>/confirm", methods=["POST"])
@login_required
def confirm_leave(leave_id):
    """休暇を確認"""
    user_id = str(current_user.username)
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    # 対象の休暇を見つけて確認者を更新
    for leave in calendar_data['leaves']:
        if leave['id'] == leave_id:
            leave['confirmed_by'] = user_id
            leave['confirmed_at'] = datetime.now().isoformat()
            break
    else:
        return jsonify({"error": "休暇が見つかりません"}), 404
    
    # 保存
    save_calendar_data(calendar_id, year_month, calendar_data)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/user/<username>/name")
@login_required
def get_user_name(username):
    """ユーザーの日本語名を取得"""
    from app.models import User
    
    user = User.query.filter_by(username=username).first()
    if user:
        return jsonify({"name": user.name or "unknown"})
    else:
        return jsonify({"name": "unknown"})

@leave_mgr_bp.route("/api/cloudshift/calendars")
@login_required
def cloudshift_calendar_options():
    ensure_data_directories()
    user_id = str(current_user.username)
    return jsonify(
        {
            "calendars": get_cloudshift_calendar_options(user_id),
            "user_id": user_id,
            "is_admin": is_admin(user_id),
        }
    )


@leave_mgr_bp.route("/api/cloudshift/sync", methods=["POST"])
@login_required
def cloudshift_sync():
    ensure_data_directories()
    user_id = str(current_user.username)
    data = request.json or {}

    try:
        calendar_id = _clean_text(data.get("calendar_id"))
        target_year_month = _normalize_year_month_key(data.get("year_month") or data.get("target_year_month"))
        source_project_id = _clean_text(data.get("source_project_id"))
        source_month_key = _normalize_month_key(data.get("source_month_key") or data.get("month_key"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not calendar_id:
        return jsonify({"error": "calendar_id is required"}), 400
    if not source_project_id:
        return jsonify({"error": "source_project_id is required"}), 400

    leaves = data.get("leaves")
    if not isinstance(leaves, list):
        return jsonify({"error": "leaves must be a list"}), 400

    accessible_calendar_ids = _cloudshift_accessible_calendar_ids(user_id)
    if calendar_id not in accessible_calendar_ids:
        return jsonify({"error": "calendar is not accessible"}), 403

    try:
        result = replace_cloudshift_leaves(
            user_id=user_id,
            calendar_id=calendar_id,
            target_year_month=target_year_month,
            source_project_id=source_project_id,
            source_month_key=source_month_key,
            leaves=leaves,
        )
    except PermissionError:
        return jsonify({"error": "calendar is not accessible"}), 403

    return jsonify(
        {
            "success": True,
            "user_id": user_id,
            "calendar_id": result["calendar_id"],
            "year_month": result["year_month"],
            "removed_total": result["removed_total"],
            "removed_by_calendar": result["removed_by_calendar"],
            "created_total": result["created_total"],
            "skipped_total": result["skipped_total"],
            "skipped": result["skipped"],
        }
    )


@leave_mgr_bp.route("/api/admin/calendar", methods=["POST"])
@login_required
def create_calendar():
    """新しいカレンダーを作成（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    calendar_id = data.get('calendar_id')
    
    # バリデーション
    valid, processed_id = validate_calendar_id(calendar_id)
    if not valid:
        return jsonify({"error": processed_id}), 400
    
    calendar_id = processed_id
    
    # 既存チェック
    meta = load_calendar_meta()
    if calendar_id in meta:
        return jsonify({"error": "このカレンダーIDは既に存在します"}), 400
    
    # メタ情報を保存
    meta[calendar_id] = {
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "name": data.get('name', calendar_id)
    }
    save_calendar_meta(meta)
    
    # 作成者に権限を付与
    permissions = load_permissions()
    if user_id not in permissions['user_calendars']:
        permissions['user_calendars'][user_id] = []
    if calendar_id not in permissions['user_calendars'][user_id]:
        permissions['user_calendars'][user_id].append(calendar_id)
    save_permissions(permissions)
    
    return jsonify({"success": True, "calendar_id": calendar_id})

@leave_mgr_bp.route("/api/admin/grant", methods=["POST"])
@login_required
def grant_permission():
    """ユーザーに権限を付与（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    target_user_id = data.get('user_id')
    calendar_id = data.get('calendar_id')
    grant_type = data.get('grant_type', 'calendar')  # 'calendar' or 'admin'
    
    permissions = load_permissions()
    
    if grant_type == 'admin':
        # 管理者権限を付与
        if target_user_id not in permissions['admins']:
            permissions['admins'].append(target_user_id)
    else:
        # カレンダーアクセス権限を付与
        if target_user_id not in permissions['user_calendars']:
            permissions['user_calendars'][target_user_id] = []
        if calendar_id not in permissions['user_calendars'][target_user_id]:
            permissions['user_calendars'][target_user_id].append(calendar_id)
    
    save_permissions(permissions)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/admin/users")
@login_required
def get_users_permissions():
    """全ユーザーの権限一覧を取得（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    permissions = load_permissions()
    calendar_meta = load_calendar_meta()
    
    # ユーザー権限情報を整形
    users_info = []
    all_users = set(permissions.get('user_calendars', {}).keys()) | set(permissions.get('admins', []))
    
    for uid in all_users:
        user_info = {
            "user_id": uid,
            "is_admin": uid in permissions.get('admins', []),
            "is_protected": uid == INITIAL_ADMIN_ID,  # 初期管理者は保護対象
            "calendars": []
        }
        
        for cal_id in permissions.get('user_calendars', {}).get(uid, []):
            cal_name = calendar_meta.get(cal_id, {}).get('name', cal_id)
            user_info['calendars'].append({
                "id": cal_id,
                "name": cal_name
            })
        
        users_info.append(user_info)
    
    return jsonify(users_info)

@leave_mgr_bp.route("/api/admin/revoke", methods=["POST"])
@login_required
def revoke_permission():
    """ユーザーから権限を剥奪（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    target_user_id = data.get('user_id')
    revoke_type = data.get('revoke_type')  # 'admin', 'calendar', 'admin_and_calendars'
    calendar_id = data.get('calendar_id')  # calendar権限剥奪時のみ使用
    
    # 初期管理者の保護
    if target_user_id == INITIAL_ADMIN_ID:
        return jsonify({"error": "初期管理者の権限は変更できません"}), 403
    
    permissions = load_permissions()
    
    if revoke_type == 'admin':
        # 管理者権限のみ剥奪
        if target_user_id in permissions.get('admins', []):
            permissions['admins'].remove(target_user_id)
    
    elif revoke_type == 'admin_and_calendars':
        # 管理者権限 + 全カレンダーアクセス権限を剥奪
        if target_user_id in permissions.get('admins', []):
            permissions['admins'].remove(target_user_id)
        if target_user_id in permissions.get('user_calendars', {}):
            del permissions['user_calendars'][target_user_id]
    
    elif revoke_type == 'calendar':
        # 特定のカレンダーアクセス権限を剥奪
        if target_user_id in permissions.get('user_calendars', {}):
            if calendar_id in permissions['user_calendars'][target_user_id]:
                permissions['user_calendars'][target_user_id].remove(calendar_id)
                # 空のリストになった場合はキーごと削除
                if not permissions['user_calendars'][target_user_id]:
                    del permissions['user_calendars'][target_user_id]
    
    save_permissions(permissions)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/admin/calendar/<calendar_id>", methods=["DELETE"])
@login_required
def delete_calendar(calendar_id):
    """カレンダーを削除（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    # カレンダーメタ情報から削除
    meta = load_calendar_meta()
    if calendar_id not in meta:
        return jsonify({"error": "カレンダーが見つかりません"}), 404
    
    # 休暇データファイルの数をカウント
    calendars_path = os.path.join(get_data_path(), 'calendars')
    data_count = 0
    
    if os.path.exists(calendars_path):
        pattern = f"{calendar_id}_*.json"
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        data_count += len(data.get('leaves', []))
                except:
                    pass
    
    # 削除実行
    del meta[calendar_id]
    save_calendar_meta(meta)
    
    # 関連する権限を削除
    permissions = load_permissions()
    for uid in list(permissions.get('user_calendars', {}).keys()):
        if calendar_id in permissions['user_calendars'][uid]:
            permissions['user_calendars'][uid].remove(calendar_id)
            if not permissions['user_calendars'][uid]:
                del permissions['user_calendars'][uid]
    save_permissions(permissions)
    
    # データファイルを削除
    if os.path.exists(calendars_path):
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    os.remove(file_path)
                except:
                    pass
    
    return jsonify({"success": True, "deleted_data_count": data_count})

@leave_mgr_bp.route("/api/admin/calendar/<calendar_id>/data_count")
@login_required
def get_calendar_data_count(calendar_id):
    """カレンダーの休暇データ件数を取得（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    calendars_path = os.path.join(get_data_path(), 'calendars')
    data_count = 0
    
    if os.path.exists(calendars_path):
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        data_count += len(data.get('leaves', []))
                except:
                    pass
    
    return jsonify({"data_count": data_count})
