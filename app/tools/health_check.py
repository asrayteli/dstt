"""健診PLUS（health_check）

健康診断の進捗を「予約 → 受診 → 再検査 → 二次検査完了」まで追跡する管理ツール。
社員名簿PLUS（Employee）と連携し、対象者×健診年度で1レコードを保持する。

アクセス制御は pluslist と同じ方針:
  - DSTT管理者は全営業所。
  - 一般利用者は「所属する支店/営業所のコード（＝既定スコープ）」＋
    「health_check 独自の個別付与」の和集合。
通知（ToBell連携）は担当者個人のオプトイン時のみ起票する。
"""
from __future__ import annotations

import os
import json
import csv
import unicodedata
from io import BytesIO, StringIO
from datetime import datetime, date

from flask import Blueprint, render_template, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import selectinload

from app.models import (
    db,
    Employee,
    Office,
    User,
    HealthCheckRecord,
    HealthCheckAttachment,
    HealthCheckEditHistory,
)
from app.access_control import (
    is_admin_user as _is_dstt_admin,
    user_office_codes as _dstt_user_office_codes,
)
from app.services.to_bell_hooks import (
    ensure_health_check_reminders,
    close_health_check_reminders,
    HEALTH_CHECK_DEFAULT_LEAD_DAYS,
)

health_check_bp = Blueprint("health_check", __name__, url_prefix="/tools/health_check")

ALLOWED_ATTACHMENT_EXT = {"pdf", "jpg", "jpeg", "png"}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB
MAX_EDIT_HISTORY = 200

# レコードの編集可能フィールド（モード共通の手入力項目）
DATE_FIELDS = {
    "hire_date",
    "reservation_date",
    "exam_date",
    "exam_date_2",
    "exam_date_2_target",
    "secondary_recommended_date",
    "secondary_exam_date",
    "secondary_guide_sent_date",
    "nasva_reservation_date",
    "nasva_exam_date",
}
BOOL_FIELDS = {"is_night_worker", "needs_recheck"}
TEXT_FIELDS = {
    "employee_number",
    "employee_name",
    "employee_type",
    "assignment_site",
    "manager_name",
    "retirement_date",
    "medical_institution",
    "recheck_items",
    "secondary_result",
    "remarks",
}
# 名簿連携レコードでは Employee から同期するため、手入力では上書きしない項目
SYNCED_FIELDS = {
    "employee_number",
    "employee_name",
    "employee_type",
    "assignment_site",
    "manager_name",
    "retirement_date",
    "hire_date",
    "birth_date",
}

ATTACHMENT_CATEGORIES = {"health", "nasva"}


# ============================================================
# データ領域・設定・権限
# ============================================================

def get_data_path() -> str:
    return os.path.join(current_app.root_path, "static", "health_check")


def get_uploads_path() -> str:
    return os.path.join(get_data_path(), "uploads")


def ensure_data_directories() -> None:
    os.makedirs(get_uploads_path(), exist_ok=True)
    permissions_file = os.path.join(get_data_path(), "permissions.json")
    if not os.path.exists(permissions_file):
        with open(permissions_file, "w", encoding="utf-8") as f:
            json.dump({"admins": [], "user_offices": {}}, f, ensure_ascii=False, indent=2)
    settings_file = os.path.join(get_data_path(), "settings.json")
    if not os.path.exists(settings_file):
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"global_lead_days": HEALTH_CHECK_DEFAULT_LEAD_DAYS}, f, ensure_ascii=False, indent=2)


def load_permissions() -> dict:
    try:
        with open(os.path.join(get_data_path(), "permissions.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"admins": [], "user_offices": {}}


def save_permissions(permissions: dict) -> None:
    with open(os.path.join(get_data_path(), "permissions.json"), "w", encoding="utf-8") as f:
        json.dump(permissions, f, ensure_ascii=False, indent=2)


def load_settings() -> dict:
    try:
        with open(os.path.join(get_data_path(), "settings.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"global_lead_days": HEALTH_CHECK_DEFAULT_LEAD_DAYS}


def save_settings(settings: dict) -> None:
    with open(os.path.join(get_data_path(), "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_global_lead_days() -> int:
    """全体既定の事前通知リードタイム（日数）。ToBellフックからも参照される。"""
    try:
        with open(os.path.join(current_app.root_path, "static", "health_check", "settings.json"), "r", encoding="utf-8") as f:
            value = json.load(f).get("global_lead_days")
            if isinstance(value, int) and value >= 0:
                return value
    except Exception:
        pass
    return HEALTH_CHECK_DEFAULT_LEAD_DAYS


def is_admin(user_id: str) -> bool:
    """DSTT管理者 か health_check 独自管理者。"""
    if _is_dstt_admin():
        return True
    return user_id in load_permissions().get("admins", [])


def get_user_offices(user_id: str) -> list[str]:
    """アクセス可能な営業所コード一覧。
    DSTT管理者は全営業所。それ以外は「所属営業所コード（既定）」＋「独自付与」の和集合。"""
    if is_admin(user_id):
        return [o.office_code for o in Office.query.all()]
    permissions = load_permissions()
    codes = set(permissions.get("user_offices", {}).get(user_id, []))
    try:
        if getattr(current_user, "is_authenticated", False) and getattr(current_user, "username", None) == user_id:
            codes |= set(_dstt_user_office_codes(current_user))
    except Exception:
        pass
    return sorted(codes)


def has_office_access(user_id: str, office_code: str | None) -> bool:
    if is_admin(user_id):
        return True
    code = (office_code or "").strip()
    if not code:
        return False
    return code in get_user_offices(user_id)


# ============================================================
# 補助ロジック
# ============================================================

def current_fiscal_year(today: date | None = None) -> int:
    """健診年度（4月開始）。4〜12月はその年、1〜3月は前年。"""
    today = today or date.today()
    return today.year if today.month >= 4 else today.year - 1


def parse_date_value(value):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_name(value: str | None) -> str:
    """担当者名の表記ゆれを吸収する（NFKC・空白除去・小文字化）。"""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(text.split())  # 全/半角空白をすべて除去
    return text.lower()


def _build_user_name_index() -> dict[str, list[str]]:
    """正規化した表示名 → username 群 のインデックス。"""
    index: dict[str, list[str]] = {}
    for user in User.query.all():
        key = _normalize_name(user.name)
        if not key:
            continue
        index.setdefault(key, []).append(user.username)
    return index


def resolve_manager_user(manager_name: str | None, index: dict[str, list[str]] | None = None) -> str | None:
    """管理担当名を DSTTユーザー(username)に一意解決する。曖昧/不一致は None。"""
    key = _normalize_name(manager_name)
    if not key:
        return None
    if index is None:
        index = _build_user_name_index()
    matches = index.get(key, [])
    return matches[0] if len(matches) == 1 else None


def sync_from_employee(record: HealthCheckRecord, employee: Employee, *, resolve_manager: bool = True) -> None:
    """名簿（Employee）の値をレコードへ同期する。専従先名は法人名(company_name)。"""
    record.office_code = employee.office_code
    record.employee_number = employee.employee_number
    record.employee_name = employee.employee_name
    record.employee_type = employee.employee_type
    record.assignment_site = employee.company_name
    record.manager_name = employee.manager_name
    record.hire_date = employee.hire_date
    record.retirement_date = employee.retirement_date
    record.birth_date = employee.birth_date
    # 担当者は未解決のときのみ自動解決（手動上書きを尊重）
    if resolve_manager and not record.manager_user and employee.manager_name:
        record.manager_user = resolve_manager_user(employee.manager_name)


def record_history(record: HealthCheckRecord | None, user_id: str, action: str,
                   field_name: str | None = None, old_value=None, new_value=None,
                   *, year=None, name=None) -> None:
    history = HealthCheckEditHistory(
        record_id=record.id if record else None,
        target_year=(record.target_year if record else year),
        employee_name=(record.employee_name if record else name),
        edited_by=user_id,
        action=action,
        field_name=field_name,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
    )
    db.session.add(history)
    count = HealthCheckEditHistory.query.count()
    if count > MAX_EDIT_HISTORY:
        old = (HealthCheckEditHistory.query
               .order_by(HealthCheckEditHistory.edited_at.asc())
               .limit(count - MAX_EDIT_HISTORY).all())
        for h in old:
            db.session.delete(h)


def _allowed_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXT


# ============================================================
# 画面
# ============================================================

@health_check_bp.route("/")
@login_required
def index():
    ensure_data_directories()
    user_id = str(current_user.username)
    offices = [{"code": o.office_code, "name": o.office_name}
               for o in Office.query.order_by(Office.office_code).all()]
    user_offices = get_user_offices(user_id)
    accessible_offices = [o for o in offices if is_admin(user_id) or o["code"] in user_offices]
    return render_template(
        "health_check.html",
        user_id=user_id,
        user_name=getattr(current_user, "name", "") or user_id,
        is_admin=is_admin(user_id),
        offices=accessible_offices,
        current_year=current_fiscal_year(),
        global_lead_days=get_global_lead_days(),
    )


# ============================================================
# レコード API
# ============================================================

def _scoped_query(user_id: str):
    """一覧・ダッシュボード・エクスポートで共有する絞り込み済みクエリを返す。
    戻り値は (query, error_response)。error_response が None でなければそれを返す。
    年度・営業所・区分・各フラグ・フリーワードを request.args から適用する。"""
    user_offices = get_user_offices(user_id)
    if not user_offices and not is_admin(user_id):
        return None, (jsonify({"error": "アクセス権限がありません"}), 403)

    query = HealthCheckRecord.query
    if not is_admin(user_id):
        query = query.filter(HealthCheckRecord.office_code.in_(user_offices))

    year = request.args.get("year", type=int)
    if year:
        query = query.filter(HealthCheckRecord.target_year == year)

    office = request.args.get("office", "").strip()
    if office:
        if not has_office_access(user_id, office):
            return None, (jsonify({"error": "この営業所への権限がありません"}), 403)
        query = query.filter(HealthCheckRecord.office_code == office)

    record_type = request.args.get("record_type", "").strip()
    if record_type in ("linked", "pre_hire", "internal"):
        query = query.filter(HealthCheckRecord.record_type == record_type)

    if request.args.get("night_only") == "true":
        query = query.filter(HealthCheckRecord.is_night_worker.is_(True))
    if request.args.get("recheck_only") == "true":
        query = query.filter(HealthCheckRecord.needs_recheck.is_(True))
    if request.args.get("unassigned_only") == "true":
        query = query.filter(
            db.or_(HealthCheckRecord.manager_user.is_(None), HealthCheckRecord.manager_user == "")
        )

    search = request.args.get("search", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            HealthCheckRecord.employee_name.like(like),
            HealthCheckRecord.employee_number.like(like),
            HealthCheckRecord.assignment_site.like(like),
            HealthCheckRecord.manager_name.like(like),
            HealthCheckRecord.medical_institution.like(like),
        ))

    return query, None


@health_check_bp.route("/api/records")
@login_required
def api_records():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error

    sort_by = request.args.get("sort_by", "employee_number")
    sort_order = request.args.get("sort_order", "asc")
    if hasattr(HealthCheckRecord, sort_by):
        col = getattr(HealthCheckRecord, sort_by)
        query = query.order_by(col.desc() if sort_order == "desc" else col.asc())

    records = query.options(selectinload(HealthCheckRecord.attachments)).all()
    # ステータス絞り込み（算出値のため取得後にフィルタ）
    status_filter = request.args.get("status", "").strip()
    items = [r.to_dict() for r in records]
    if status_filter:
        items = [r for r in items if r["status"] == status_filter]

    return jsonify({"records": items, "count": len(items)})


@health_check_bp.route("/api/record/<int:record_id>")
@login_required
def api_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403
    data = record.to_dict()
    data["attachments"] = [a.to_dict() for a in record.attachments]
    return jsonify(data)


def _apply_payload(record: HealthCheckRecord, payload: dict, user_id: str) -> None:
    """編集可能フィールドを payload から反映し、履歴を記録する。
    名簿連携レコードでは同期項目（氏名・専従先など）は手入力で上書きしない。"""
    skip = SYNCED_FIELDS if record.record_type == "linked" else set()
    for field in TEXT_FIELDS:
        if field in skip or field not in payload:
            continue
        new_value = (str(payload.get(field)).strip() if payload.get(field) is not None else "") or None
        # 氏名は必須のため空での上書きはしない
        if field == "employee_name" and not new_value:
            continue
        old_value = getattr(record, field)
        if (old_value or None) != (new_value or None):
            record_history(record, user_id, "update", field, old_value, new_value)
            setattr(record, field, new_value)
    for field in DATE_FIELDS:
        if field in skip or field not in payload:
            continue
        new_value = parse_date_value(payload.get(field))
        old_value = getattr(record, field)
        if old_value != new_value:
            record_history(record, user_id, "update", field, old_value, new_value)
            setattr(record, field, new_value)
    for field in BOOL_FIELDS:
        if field in payload:
            new_value = bool(payload.get(field))
            old_value = bool(getattr(record, field))
            if old_value != new_value:
                record_history(record, user_id, "update", field, old_value, new_value)
                setattr(record, field, new_value)
    if "reminder_lead_days" in payload:
        raw = payload.get("reminder_lead_days")
        new_value = None
        if raw not in (None, ""):
            try:
                new_value = max(0, int(raw))
            except (TypeError, ValueError):
                new_value = None
        if record.reminder_lead_days != new_value:
            record_history(record, user_id, "update", "reminder_lead_days", record.reminder_lead_days, new_value)
            record.reminder_lead_days = new_value


@health_check_bp.route("/api/record", methods=["POST"])
@login_required
def api_create_record():
    user_id = str(current_user.username)
    payload = request.json or {}

    target_year = payload.get("target_year")
    try:
        target_year = int(target_year)
    except (TypeError, ValueError):
        return jsonify({"error": "対象年度が不正です"}), 400

    record_type = payload.get("record_type", "linked")
    if record_type not in ("linked", "pre_hire", "internal"):
        return jsonify({"error": "区分が不正です"}), 400

    record = HealthCheckRecord(target_year=target_year, record_type=record_type, created_by=user_id, updated_by=user_id)

    if record_type == "linked":
        employee = None
        if payload.get("employee_id"):
            employee = db.session.get(Employee, int(payload["employee_id"]))
        elif payload.get("employee_number"):
            employee = Employee.query.filter_by(
                employee_number=str(payload["employee_number"]).strip(), is_deleted=False
            ).first()
        if not employee:
            return jsonify({"error": "対象の社員が名簿に見つかりません"}), 404
        if not has_office_access(user_id, employee.office_code):
            return jsonify({"error": "この社員の営業所への権限がありません"}), 403
        existing = HealthCheckRecord.query.filter_by(
            target_year=target_year, employee_id=employee.id
        ).first()
        if existing:
            return jsonify({"error": "この社員の当年度レコードは既に存在します", "record_id": existing.id}), 409
        record.employee_id = employee.id
        sync_from_employee(record, employee)
    else:
        name = str(payload.get("employee_name", "")).strip()
        if not name:
            return jsonify({"error": "社員名は必須です"}), 400
        office_code = str(payload.get("office_code", "")).strip()
        if not has_office_access(user_id, office_code):
            return jsonify({"error": "この営業所への権限がありません"}), 403
        record.office_code = office_code
        record.employee_name = name
        record.manager_user = resolve_manager_user(payload.get("manager_name"))

    _apply_payload(record, payload, user_id)
    db.session.add(record)
    db.session.flush()
    record_history(record, user_id, "create", "all", None, "新規作成")
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>", methods=["PUT"])
@login_required
def api_update_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    payload = request.json or {}
    # 名簿連携レコードは固定情報を最新へ同期（手動モードは手入力を反映）
    if record.record_type == "linked" and record.employee_id:
        employee = db.session.get(Employee, record.employee_id)
        if employee:
            sync_from_employee(record, employee)

    _apply_payload(record, payload, user_id)
    record.updated_by = user_id
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>", methods=["DELETE"])
@login_required
def api_delete_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    close_health_check_reminders(record.id, commit=False)
    # 添付ファイルの実体も削除
    for attachment in record.attachments:
        try:
            os.remove(os.path.join(get_uploads_path(), attachment.stored_path))
        except OSError:
            pass
    record_history(record, user_id, "delete", "all", record.employee_name, None)
    db.session.delete(record)
    db.session.commit()
    return jsonify({"success": True})


@health_check_bp.route("/api/record/<int:record_id>/manager", methods=["PUT"])
@login_required
def api_set_manager(record_id):
    """担当者(manager_user)の手動上書き。"""
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    payload = request.json or {}
    username = (payload.get("manager_user") or "").strip()
    if username:
        user = User.query.filter_by(username=username).first()
        if not user:
            return jsonify({"error": "指定のユーザーが存在しません"}), 400
    old = record.manager_user
    record.manager_user = username or None
    record_history(record, user_id, "update", "manager_user", old, record.manager_user)
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


# ============================================================
# 一括操作
# ============================================================

@health_check_bp.route("/api/bulk_create", methods=["POST"])
@login_required
def api_bulk_create():
    """名簿から対象年度の在籍者全員を一括起票する。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    try:
        target_year = int(payload.get("target_year"))
    except (TypeError, ValueError):
        return jsonify({"error": "対象年度が不正です"}), 400

    requested_offices = payload.get("offices") or []
    accessible = set(get_user_offices(user_id))
    if requested_offices:
        offices = [c for c in requested_offices if c in accessible]
    else:
        offices = sorted(accessible)
    if not offices:
        return jsonify({"error": "対象営業所への権限がありません"}), 403

    employees = Employee.query.filter(
        Employee.office_code.in_(offices),
        Employee.is_deleted.is_(False),
    ).all()

    existing_ids = {
        r.employee_id
        for r in HealthCheckRecord.query.filter(
            HealthCheckRecord.target_year == target_year,
            HealthCheckRecord.employee_id.isnot(None),
        ).all()
    }

    name_index = _build_user_name_index()
    created = 0
    for employee in employees:
        if employee.id in existing_ids:
            continue
        record = HealthCheckRecord(
            target_year=target_year,
            record_type="linked",
            employee_id=employee.id,
            created_by=user_id,
            updated_by=user_id,
        )
        sync_from_employee(record, employee, resolve_manager=False)
        if employee.manager_name:
            record.manager_user = resolve_manager_user(employee.manager_name, name_index)
        db.session.add(record)
        created += 1

    record_history(None, user_id, "bulk_create", "target_year", None, f"{target_year}年度 {created}件", year=target_year)
    db.session.commit()
    return jsonify({"success": True, "created": created, "offices": offices})


@health_check_bp.route("/api/carryover", methods=["POST"])
@login_required
def api_carryover():
    """前年度レコードを新年度へ複製（固定情報のみ。受診系日付はクリア）。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    try:
        from_year = int(payload.get("from_year"))
        to_year = int(payload.get("to_year"))
    except (TypeError, ValueError):
        return jsonify({"error": "年度の指定が不正です"}), 400
    if from_year == to_year:
        return jsonify({"error": "複製元と複製先の年度が同じです"}), 400

    accessible = set(get_user_offices(user_id))
    query = HealthCheckRecord.query.filter(
        HealthCheckRecord.target_year == from_year,
        HealthCheckRecord.record_type == "linked",
        HealthCheckRecord.employee_id.isnot(None),
    )
    if not is_admin(user_id):
        query = query.filter(HealthCheckRecord.office_code.in_(accessible or ["__none__"]))

    existing_ids = {
        r.employee_id
        for r in HealthCheckRecord.query.filter(
            HealthCheckRecord.target_year == to_year,
            HealthCheckRecord.employee_id.isnot(None),
        ).all()
    }

    created = 0
    for source in query.all():
        if source.employee_id in existing_ids:
            continue
        record = HealthCheckRecord(
            target_year=to_year,
            record_type="linked",
            employee_id=source.employee_id,
            office_code=source.office_code,
            employee_number=source.employee_number,
            employee_name=source.employee_name,
            employee_type=source.employee_type,
            assignment_site=source.assignment_site,
            manager_name=source.manager_name,
            manager_user=source.manager_user,
            hire_date=source.hire_date,
            retirement_date=source.retirement_date,
            is_night_worker=source.is_night_worker,
            reminder_lead_days=source.reminder_lead_days,
            created_by=user_id,
            updated_by=user_id,
        )
        # 名簿連携は最新へ同期（改姓・退職等に追従）
        if record.employee_id:
            employee = db.session.get(Employee, record.employee_id)
            if employee:
                sync_from_employee(record, employee, resolve_manager=False)
        db.session.add(record)
        created += 1

    record_history(None, user_id, "carryover", "target_year", str(from_year), f"{to_year}年度 {created}件", year=to_year)
    db.session.commit()
    return jsonify({"success": True, "created": created})


@health_check_bp.route("/api/resolve_managers", methods=["POST"])
@login_required
def api_resolve_managers():
    """担当者未解決のレコードを一括で自動紐付けする。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    year = payload.get("year")

    accessible = set(get_user_offices(user_id))
    query = HealthCheckRecord.query.filter(
        db.or_(HealthCheckRecord.manager_user.is_(None), HealthCheckRecord.manager_user == "")
    )
    if year:
        query = query.filter(HealthCheckRecord.target_year == int(year))
    if not is_admin(user_id):
        query = query.filter(HealthCheckRecord.office_code.in_(accessible or ["__none__"]))

    name_index = _build_user_name_index()
    resolved = 0
    for record in query.all():
        username = resolve_manager_user(record.manager_name, name_index)
        if username:
            record.manager_user = username
            ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
            resolved += 1
    db.session.commit()
    return jsonify({"success": True, "resolved": resolved})


# ============================================================
# 添付ファイル
# ============================================================

@health_check_bp.route("/api/record/<int:record_id>/attachment", methods=["POST"])
@login_required
def api_upload_attachment(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403

    if "file" not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    if not _allowed_attachment(file.filename):
        return jsonify({"error": "pdf / jpg / png のみアップロードできます"}), 400

    category = (request.form.get("category") or "health").strip()
    if category not in ATTACHMENT_CATEGORIES:
        category = "health"

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "ファイルサイズは10MBまでです"}), 400

    import uuid
    ext = file.filename.rsplit(".", 1)[1].lower()
    year_dir = str(record.target_year)
    rel_path = os.path.join(year_dir, f"{uuid.uuid4().hex}.{ext}")
    abs_dir = os.path.join(get_uploads_path(), year_dir)
    os.makedirs(abs_dir, exist_ok=True)
    file.save(os.path.join(get_uploads_path(), rel_path))

    attachment = HealthCheckAttachment(
        record_id=record.id,
        category=category,
        stored_path=rel_path,
        original_name=secure_filename(file.filename) or f"file.{ext}",
        content_type=file.mimetype,
        file_size=size,
        uploaded_by=user_id,
    )
    db.session.add(attachment)
    db.session.commit()
    return jsonify({"success": True, "attachment": attachment.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>/attachment/<int:attachment_id>")
@login_required
def api_download_attachment(record_id, attachment_id):
    user_id = str(current_user.username)
    attachment = db.session.get(HealthCheckAttachment, attachment_id)
    if not attachment or attachment.record_id != record_id:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    record = db.session.get(HealthCheckRecord, record_id)
    if not record or not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403
    abs_path = os.path.join(get_uploads_path(), attachment.stored_path)
    if not os.path.exists(abs_path):
        return jsonify({"error": "ファイルが見つかりません"}), 404
    return send_file(abs_path, as_attachment=True, download_name=attachment.original_name)


@health_check_bp.route("/api/record/<int:record_id>/attachment/<int:attachment_id>", methods=["DELETE"])
@login_required
def api_delete_attachment(record_id, attachment_id):
    user_id = str(current_user.username)
    attachment = db.session.get(HealthCheckAttachment, attachment_id)
    if not attachment or attachment.record_id != record_id:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    record = db.session.get(HealthCheckRecord, record_id)
    if not record or not has_office_access(user_id, record.office_code):
        return jsonify({"error": "アクセス権限がありません"}), 403
    try:
        os.remove(os.path.join(get_uploads_path(), attachment.stored_path))
    except OSError:
        pass
    db.session.delete(attachment)
    db.session.commit()
    return jsonify({"success": True})


# ============================================================
# 名簿候補・ユーザー候補・ダッシュボード
# ============================================================

@health_check_bp.route("/api/employees")
@login_required
def api_employees():
    """名簿連携レコード作成用の社員候補（自分のスコープ内・在籍者）。"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)
    if not user_offices:
        return jsonify({"employees": []})
    search = request.args.get("search", "").strip()
    query = Employee.query.filter(
        Employee.office_code.in_(user_offices),
        Employee.is_deleted.is_(False),
    )
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Employee.employee_name.like(like),
            Employee.employee_number.like(like),
        ))
    employees = query.order_by(Employee.employee_number).limit(50).all()
    return jsonify({"employees": [{
        "id": e.id,
        "employee_number": e.employee_number,
        "employee_name": e.employee_name,
        "office_code": e.office_code,
        "office_name": e.office_name,
        "company_name": e.company_name,
        "manager_name": e.manager_name,
    } for e in employees]})


@health_check_bp.route("/api/users")
@login_required
def api_users():
    """担当者割当用の DSTTユーザー候補。"""
    users = User.query.order_by(User.name).all()
    return jsonify({"users": [{"username": u.username, "name": u.name or u.username} for u in users]})


@health_check_bp.route("/api/dashboard")
@login_required
def api_dashboard():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error
    records = query.all()
    status_counts: dict[str, int] = {}
    night_pending = 0
    unassigned = 0
    for record in records:
        status = record.compute_status()
        status_counts[status] = status_counts.get(status, 0) + 1
        if record.is_night_worker and not record.exam_date_2:
            night_pending += 1
        if not record.manager_user:
            unassigned += 1

    total = len(records)
    examined = status_counts.get("受診済", 0) + status_counts.get("再検査対象", 0) \
        + status_counts.get("二次案内済", 0) + status_counts.get("二次完了", 0)
    return jsonify({
        "total": total,
        "examined": examined,
        "exam_rate": round(examined / total * 100, 1) if total else 0.0,
        "status_counts": status_counts,
        "recheck_pending": status_counts.get("再検査対象", 0) + status_counts.get("二次案内済", 0),
        "night_pending": night_pending,
        "unassigned": unassigned,
    })


@health_check_bp.route("/api/years")
@login_required
def api_years():
    user_id = str(current_user.username)
    query = db.session.query(HealthCheckRecord.target_year).distinct()
    if not is_admin(user_id):
        user_offices = get_user_offices(user_id)
        query = query.filter(HealthCheckRecord.office_code.in_(user_offices or ["__none__"]))
    years = sorted({row[0] for row in query.all()}, reverse=True)
    return jsonify({"years": years})


@health_check_bp.route("/api/history")
@login_required
def api_history():
    record_id = request.args.get("record_id", type=int)
    if record_id:
        record = db.session.get(HealthCheckRecord, record_id)
        if not record or not has_office_access(str(current_user.username), record.office_code):
            return jsonify({"error": "アクセス権限がありません"}), 403
        rows = (HealthCheckEditHistory.query.filter_by(record_id=record_id)
                .order_by(HealthCheckEditHistory.edited_at.desc()).limit(100).all())
    elif is_admin(str(current_user.username)):
        rows = (HealthCheckEditHistory.query
                .order_by(HealthCheckEditHistory.edited_at.desc()).limit(100).all())
    else:
        return jsonify({"histories": []})
    return jsonify({"histories": [h.to_dict() for h in rows]})


# ============================================================
# エクスポート
# ============================================================

_EXPORT_COLUMNS = [
    ("target_year", "健診年度"),
    ("record_type", "区分"),
    ("employee_number", "社員番号"),
    ("employee_name", "社員名"),
    ("employee_type", "社員区分"),
    ("assignment_site", "専従先名"),
    ("manager_name", "管理担当名"),
    ("hire_date", "入社日付"),
    ("retirement_date", "退職日付"),
    ("reservation_date", "予約日"),
    ("exam_date", "受診日"),
    ("exam_date_2", "受診日②(深夜2回目)"),
    ("medical_institution", "受診医療機関名"),
    ("is_night_worker", "深夜従事者"),
    ("needs_recheck", "再検査有無"),
    ("recheck_items", "再検査項目"),
    ("secondary_recommended_date", "二次検査受診推奨日"),
    ("secondary_exam_date", "二次検診受診日"),
    ("secondary_guide_sent_date", "二次検査案内送付日"),
    ("secondary_result", "二次検査結果"),
    ("birth_date", "生年月日"),
    ("nasva_reservation_date", "NASVA予約日"),
    ("nasva_exam_date", "NASVA受診日"),
    ("status", "受診ステータス"),
    ("remarks", "備考"),
]
_RECORD_TYPE_LABEL = {"linked": "名簿連携", "pre_hire": "入社前", "internal": "内勤者"}


@health_check_bp.route("/api/export")
@login_required
def api_export():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error
    records = query.order_by(HealthCheckRecord.employee_number).all()
    status_filter = request.args.get("status", "").strip()
    if status_filter:
        records = [r for r in records if r.compute_status() == status_filter]
    year = request.args.get("year", type=int)

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in _EXPORT_COLUMNS])
    for record in records:
        data = record.to_dict()
        row = []
        for key, _ in _EXPORT_COLUMNS:
            if key == "record_type":
                row.append(_RECORD_TYPE_LABEL.get(record.record_type, record.record_type))
            elif key in ("is_night_worker", "needs_recheck"):
                row.append("○" if data.get(key) else "")
            else:
                row.append(data.get(key) if data.get(key) is not None else "")
        writer.writerow(row)

    output = BytesIO(buffer.getvalue().encode("utf-8-sig"))
    output.seek(0)
    filename = f"健診PLUS_{year or 'all'}_{datetime.now().strftime('%Y%m%d')}.csv"
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name=filename)


# ============================================================
# 設定・連携・管理
# ============================================================

@health_check_bp.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    user_id = str(current_user.username)
    if request.method == "POST":
        if not is_admin(user_id):
            return jsonify({"error": "管理者権限が必要です"}), 403
        payload = request.json or {}
        settings = load_settings()
        if "global_lead_days" in payload:
            try:
                settings["global_lead_days"] = max(0, int(payload["global_lead_days"]))
            except (TypeError, ValueError):
                return jsonify({"error": "リードタイムが不正です"}), 400
        save_settings(settings)
        return jsonify({"success": True, "settings": settings})
    return jsonify({"settings": load_settings()})


@health_check_bp.route("/api/integration", methods=["GET", "POST"])
@login_required
def api_integration():
    """ログインユーザー個人の ToBell 連携オプトイン設定。"""
    from app.services.to_bell_integrations import get_settings, update_integrations
    user_id = str(current_user.username)
    if request.method == "POST":
        payload = request.json or {}
        enabled = bool(payload.get("enabled"))
        result = update_integrations(user_id, {"integrations": {"health_check.linkage": enabled}})
        return jsonify({"success": True, "enabled": result["integrations"].get("health_check.linkage", False)})
    settings = get_settings(user_id)
    return jsonify({"enabled": settings["integrations"].get("health_check.linkage", False)})


@health_check_bp.route("/api/admin/permissions")
@login_required
def api_admin_permissions():
    user_id = str(current_user.username)
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    permissions = load_permissions()
    offices = {o.office_code: o.office_name for o in Office.query.all()}
    all_users = set(permissions.get("user_offices", {}).keys()) | set(permissions.get("admins", []))
    result = []
    for uid in sorted(all_users):
        result.append({
            "user_id": uid,
            "is_admin": uid in permissions.get("admins", []),
            "offices": [{"code": c, "name": offices.get(c, c)}
                        for c in permissions.get("user_offices", {}).get(uid, [])],
        })
    return jsonify({"users": result})


@health_check_bp.route("/api/admin/grant", methods=["POST"])
@login_required
def api_admin_grant():
    user_id = str(current_user.username)
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    payload = request.json or {}
    target = (payload.get("user_id") or "").strip()
    if not target:
        return jsonify({"error": "ユーザーIDが必要です"}), 400
    grant_type = payload.get("grant_type", "office")
    permissions = load_permissions()
    if grant_type == "admin":
        if target not in permissions.setdefault("admins", []):
            permissions["admins"].append(target)
    else:
        office_code = (payload.get("office_code") or "").strip()
        if not office_code:
            return jsonify({"error": "営業所コードが必要です"}), 400
        offices = permissions.setdefault("user_offices", {})
        offices.setdefault(target, [])
        if office_code not in offices[target]:
            offices[target].append(office_code)
    save_permissions(permissions)
    return jsonify({"success": True})


@health_check_bp.route("/api/admin/revoke", methods=["POST"])
@login_required
def api_admin_revoke():
    user_id = str(current_user.username)
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    payload = request.json or {}
    target = (payload.get("user_id") or "").strip()
    revoke_type = payload.get("revoke_type", "office")
    permissions = load_permissions()
    if revoke_type == "admin":
        if target in permissions.get("admins", []):
            permissions["admins"].remove(target)
    else:
        office_code = (payload.get("office_code") or "").strip()
        offices = permissions.get("user_offices", {})
        if target in offices and office_code in offices[target]:
            offices[target].remove(office_code)
            if not offices[target]:
                del offices[target]
    save_permissions(permissions)
    return jsonify({"success": True})
