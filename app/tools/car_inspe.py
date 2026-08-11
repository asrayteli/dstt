"""マイカー管理ツール（旧: 現場車両の車検証ツール）。

自社社員（運転士）のマイカーについて、車検証・自賠責保険証・任意保険証・
運転免許証の有効期限と書類PDFを管理する。社員名簿PLUS（``employees``）と
社員番号で紐づけ、期限が近づくと登録メールアドレスへ自動通知する
（通知は ``app.services.driver_doc_notify`` ＋ ``app.services.mail_service``）。

データは健診PLUSと同様に「営業所（office_code）」単位でアクセス制御する。
DSTT管理者は全営業所、それ以外のユーザーは自分のアクセス可能な営業所の
データのみ閲覧・編集できる。

社員1人＝1台（1プロファイル）。初版はOCRを行わず、手動入力＋PDF保存。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app.access_control import is_admin_user, user_office_codes
from app.models import (
    utc_now,
    AccessOffice,
    DriverDocument,
    DriverVehicleProfile,
    Employee,
    Office,
    db,
)
from app.services import mail_service
from app.runtime_paths import runtime_path

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")
logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = str(
    runtime_path(
        "tools",
        "car_inspe",
        "storage",
        "driver",
        legacy=os.path.join(APP_ROOT, "..", "var", "car_inspe", "storage", "driver"),
    )
)

DOC_TYPES = DriverDocument.DOC_TYPES
DOC_LABELS = {
    "inspection": "車検証",
    "liability_insurance": "自賠責保険証",
    "voluntary_insurance": "任意保険証",
    "license": "運転免許証",
}
# 期限が近いと判定する残り日数（「期限間近」バッジ）。
EXPIRING_SOON_DAYS = 30
ALLOWED_UPLOAD_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


# --------------------------------------------------------------------------- #
# 共通ヘルパー
# --------------------------------------------------------------------------- #
def _actor() -> str:
    return getattr(current_user, "username", "") or ""


def ensure_storage_dir(profile_id):
    path = os.path.join(STORAGE_DIR, str(profile_id))
    os.makedirs(path, exist_ok=True)
    return path


def normalize_text(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_date(value) -> str:
    """各種表記の日付を YYYYMMDD（8桁）へ正規化する。解釈不能なら空文字。

    対応: YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / 和暦（令和・平成・昭和）。
    """
    text = normalize_text(value)
    if not text:
        return ""

    def _valid(year, month, day):
        try:
            datetime(year, month, day)
            return f"{year:04d}{month:02d}{day:02d}"
        except ValueError:
            return ""

    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact:
        return _valid(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))

    seireki = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    if seireki:
        return _valid(int(seireki.group(1)), int(seireki.group(2)), int(seireki.group(3)))

    era_offsets = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988, "昭和": 1925, "S": 1925}
    era_match = re.search(r"(令和|平成|昭和|R|H|S)\s*(元|\d{1,2})\s*年?\s*(\d{1,2})\s*月?\s*(\d{1,2})", text, re.I)
    if era_match:
        era, year_raw, month_raw, day_raw = era_match.groups()
        era = era.upper() if era.upper() in era_offsets else era
        era_year = 1 if year_raw == "元" else int(year_raw)
        year = era_offsets[era] + era_year
        return _valid(year, int(month_raw), int(day_raw))

    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return _valid(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    return ""


def format_date_display(value) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def doc_status(expiry_date: str, today: str) -> str:
    """書類1件の状態を返す: missing / expired / expiring / active。"""
    expiry = str(expiry_date or "").strip()
    if not re.fullmatch(r"\d{8}", expiry):
        return "missing"
    if expiry < today:
        return "expired"
    # 残り日数で「期限間近」を判定。
    try:
        days_left = (datetime.strptime(expiry, "%Y%m%d") - datetime.strptime(today, "%Y%m%d")).days
    except ValueError:
        return "missing"
    if days_left <= EXPIRING_SOON_DAYS:
        return "expiring"
    return "active"


def profile_overall_status(doc_statuses: dict) -> str:
    """プロファイル全体の状態。最も深刻なものを採用。"""
    present = [doc_statuses.get(t, "missing") for t in DOC_TYPES]
    if "expired" in present:
        return "expired"
    if "expiring" in present:
        return "expiring"
    if any(status == "missing" for status in present):
        return "incomplete"
    return "active"


def serialize_profile(profile: DriverVehicleProfile, today: str) -> dict:
    data = profile.to_dict(include_documents=True)
    documents = data.get("documents", {})
    statuses = {}
    nearest = None
    for doc_type in DOC_TYPES:
        doc = documents.get(doc_type)
        expiry = (doc or {}).get("expiry_date", "")
        status = doc_status(expiry, today)
        statuses[doc_type] = status
        if doc is not None:
            doc["status"] = status
            doc["expiry_display"] = format_date_display(expiry)
            doc["issued_display"] = format_date_display((doc or {}).get("issued_date", ""))
        if re.fullmatch(r"\d{8}", expiry or ""):
            if nearest is None or expiry < nearest:
                nearest = expiry
    # 4種すべての枠を用意する（未登録は None）。
    for doc_type in DOC_TYPES:
        documents.setdefault(doc_type, None)
    data["documents"] = documents
    data["doc_statuses"] = statuses
    data["status"] = profile_overall_status(statuses)
    data["nearest_expiry"] = nearest or ""
    data["nearest_expiry_display"] = format_date_display(nearest) if nearest else ""
    data["missing_types"] = [t for t in DOC_TYPES if statuses.get(t) == "missing"]
    return data


def employee_to_option(emp: Employee) -> dict:
    return {
        "employee_number": emp.employee_number,
        "employee_name": emp.employee_name or "",
        "employee_kana": emp.employee_kana or "",
        "office_code": emp.office_code or "",
        "office_name": emp.office_name or "",
        "email": emp.email or "",
    }


# --------------------------------------------------------------------------- #
# 営業所アクセス制御（健診PLUSと同じDSTTアクセス権に同期）
# --------------------------------------------------------------------------- #
def _is_admin() -> bool:
    """DSTT管理者か。管理者は全営業所のデータにアクセスできる。"""
    return is_admin_user(current_user)


def _office_codes() -> set[str]:
    """ログインユーザーがアクセスできる営業所コード集合（コード無しは除外）。"""
    return {c for c in (user_office_codes(current_user) or set()) if c}


def _accessible_codes_or_none() -> set[str] | None:
    """データ絞り込み用。管理者は None（=全件）、それ以外は営業所コード集合。"""
    if _is_admin():
        return None
    return _office_codes()


def _can_access_office(office_code) -> bool:
    """その営業所のデータを閲覧・編集できるか。
    - 管理者は常に可。
    - コード未設定（None/空）のデータは管理者のみ（誤公開防止）。
    - それ以外は自分のアクセス可能営業所コードに含まれていれば可。"""
    if _is_admin():
        return True
    code = (office_code or "").strip()
    if not code:
        return False
    return code in _office_codes()


def _office_options(codes=None) -> list[dict]:
    """営業所の選択肢を構築する。

    DSTTのアクセス権営業所（``AccessOffice`` の code→name）を土台に、
    社員名簿PLUSの ``Office``（office_code→office_name）で名称を補足/上書きする。
    ``codes`` を指定するとその集合に限定する（非管理者向け）。空集合なら []。
    """
    wanted = None if codes is None else {str(c).strip() for c in codes if str(c).strip()}
    if wanted is not None and not wanted:
        return []
    by_code: dict[str, dict] = {}

    access_query = AccessOffice.query.filter(AccessOffice.code.isnot(None))
    if wanted is not None:
        access_query = access_query.filter(AccessOffice.code.in_(wanted))
    for office in access_query.all():
        code = (office.code or "").strip()
        if code:
            by_code[code] = {"code": code, "name": office.name or code}

    plus_query = Office.query
    if wanted is not None:
        plus_query = plus_query.filter(Office.office_code.in_(wanted))
    for office in plus_query.all():
        code = (office.office_code or "").strip()
        if code:
            name = office.office_name or by_code.get(code, {}).get("name") or code
            by_code[code] = {"code": code, "name": name}

    return [by_code[c] for c in sorted(by_code)]


def _backfill_office_codes() -> None:
    """営業所コードが空のプロファイルを、社員名簿の現在の所属で補完する。

    旧データ（営業所スコープ導入前）でも正しく絞り込めるようにするための保険。
    対象が無ければ即return（追加コストはほぼ無し）。"""
    missing = DriverVehicleProfile.query.filter(
        or_(
            DriverVehicleProfile.office_code.is_(None),
            DriverVehicleProfile.office_code == "",
        )
    ).all()
    if not missing:
        return
    numbers = {p.employee_number for p in missing}
    employees = {
        e.employee_number: e
        for e in Employee.query.filter(Employee.employee_number.in_(numbers)).all()
    }
    changed = False
    for profile in missing:
        emp = employees.get(profile.employee_number)
        if emp and (emp.office_code or "").strip():
            profile.office_code = (emp.office_code or "").strip()
            if not (profile.office_name or "").strip():
                profile.office_name = emp.office_name or ""
            changed = True
    if changed:
        db.session.commit()


def _scoped_profile_query():
    """ユーザーのアクセス範囲に絞った DriverVehicleProfile クエリを返す。
    アクセス可能営業所が無い非管理者は None を返す（呼び出し側で空扱い）。"""
    codes = _accessible_codes_or_none()
    query = DriverVehicleProfile.query
    if codes is not None:
        if not codes:
            return None
        query = query.filter(DriverVehicleProfile.office_code.in_(codes))
    return query


def _get_profile_scoped(profile_id):
    """プロファイルを取得しつつ営業所アクセス権を検証する。"""
    profile = db.session.get(DriverVehicleProfile, profile_id)
    if profile is None:
        return None, (jsonify({"error": "対象の運転士が見つかりません。"}), 404)
    if not _can_access_office(profile.office_code):
        return None, (jsonify({"error": "この運転士へのアクセス権限がありません。"}), 403)
    return profile, None


# --------------------------------------------------------------------------- #
# 社員検索（社員名簿PLUS 連携）
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/employees", methods=["GET"])
@login_required
def api_employees():
    q = normalize_text(request.args.get("q", ""))
    office = normalize_text(request.args.get("office", ""))
    try:
        limit = int(request.args.get("limit", 30) or 30)
    except (TypeError, ValueError):
        limit = 30
    limit = min(max(limit, 1), 100)

    query = Employee.query.filter(
        Employee.is_deleted.isnot(True),
        Employee.is_retired.isnot(True),
    )

    # 営業所スコープ: 非管理者は自分のアクセス可能営業所の社員のみ。
    codes = _accessible_codes_or_none()
    if codes is not None:
        if not codes:
            return jsonify({"employees": [], "count": 0})
        query = query.filter(Employee.office_code.in_(codes))
    if office:
        if not _can_access_office(office):
            return jsonify({"error": "この営業所へのアクセス権限がありません。"}), 403
        query = query.filter(Employee.office_code == office)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Employee.employee_number.ilike(like),
                Employee.employee_name.ilike(like),
                Employee.employee_kana.ilike(like),
                Employee.email.ilike(like),
            )
        )
    rows = query.order_by(Employee.employee_number.asc()).limit(limit).all()
    linked = {
        row[0]
        for row in DriverVehicleProfile.query.with_entities(DriverVehicleProfile.employee_number).all()
    }
    employees = []
    for emp in rows:
        option = employee_to_option(emp)
        option["has_profile"] = emp.employee_number in linked
        employees.append(option)
    return jsonify({"employees": employees, "count": len(employees)})


# --------------------------------------------------------------------------- #
# 運転士プロファイル CRUD
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/drivers", methods=["GET"])
@login_required
def api_list_drivers():
    _backfill_office_codes()
    q = normalize_text(request.args.get("q", ""))
    status = normalize_text(request.args.get("status", ""))
    office = normalize_text(request.args.get("office", ""))

    empty_summary = {"drivers": 0, "expired": 0, "expiring": 0, "incomplete": 0}
    query = _scoped_profile_query()
    if query is None:
        # アクセス可能な営業所が割り当てられていない非管理者。
        return jsonify({
            "drivers": [],
            "count": 0,
            "summary": empty_summary,
            "mail_configured": mail_service.is_mail_configured(),
            "can_admin": _is_admin(),
            "no_office": True,
        })

    if office:
        if not _can_access_office(office):
            return jsonify({"error": "この営業所へのアクセス権限がありません。"}), 403
        query = query.filter(DriverVehicleProfile.office_code == office)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                DriverVehicleProfile.employee_number.ilike(like),
                DriverVehicleProfile.employee_name.ilike(like),
                DriverVehicleProfile.office_name.ilike(like),
                DriverVehicleProfile.registration_number.ilike(like),
                DriverVehicleProfile.email.ilike(like),
            )
        )
    profiles = query.order_by(
        DriverVehicleProfile.office_code.asc(),
        DriverVehicleProfile.employee_number.asc(),
    ).all()
    today = datetime.now().strftime("%Y%m%d")
    all_drivers = [serialize_profile(profile, today) for profile in profiles]

    # サマリーは絞り込み（営業所・キーワード）後・状態フィルタ前の集合で集計する。
    summary = {
        "drivers": len(all_drivers),
        "expired": sum(1 for d in all_drivers if d["status"] == "expired"),
        "expiring": sum(1 for d in all_drivers if d["status"] == "expiring"),
        "incomplete": sum(1 for d in all_drivers if d["status"] == "incomplete"),
    }
    drivers = [d for d in all_drivers if d["status"] == status] if status else all_drivers

    return jsonify({
        "drivers": drivers,
        "count": len(drivers),
        "summary": summary,
        "mail_configured": mail_service.is_mail_configured(),
        "can_admin": _is_admin(),
    })


@car_inspe_bp.route("/api/drivers", methods=["POST"])
@login_required
def api_create_driver():
    data = request.get_json(silent=True) or {}
    employee_number = normalize_text(data.get("employee_number", ""))
    if not employee_number:
        return jsonify({"error": "社員番号は必須です。社員名簿PLUSから選択してください。"}), 400

    employee = Employee.query.filter(
        Employee.employee_number == employee_number,
        Employee.is_deleted.isnot(True),
        Employee.is_retired.isnot(True),
    ).first()
    if employee is None:
        return jsonify({"error": f"社員番号 {employee_number} が社員名簿PLUSに見つかりません。"}), 404

    if not _can_access_office(employee.office_code):
        return jsonify({"error": "この社員の営業所への登録権限がありません。"}), 403

    existing = DriverVehicleProfile.query.filter_by(employee_number=employee_number).first()
    if existing is not None:
        return jsonify({"error": "この運転士のマイカーは既に登録されています。"}), 409

    profile = DriverVehicleProfile(
        employee_number=employee_number,
        employee_name=employee.employee_name or "",
        office_code=employee.office_code or "",
        office_name=employee.office_name or "",
        email=normalize_text(data.get("email", "")) or (employee.email or ""),
        notify_enabled=bool(data.get("notify_enabled", True)),
        car_maker=normalize_text(data.get("car_maker", "")),
        car_name=normalize_text(data.get("car_name", "")),
        registration_number=normalize_text(data.get("registration_number", "")),
        notes=str(data.get("notes", "") or "").strip(),
        created_by=_actor(),
    )
    db.session.add(profile)
    db.session.commit()
    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"success": True, "driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>", methods=["GET"])
@login_required
def api_get_driver(profile_id):
    profile, error = _get_profile_scoped(profile_id)
    if error:
        return error
    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>", methods=["PUT"])
@login_required
def api_update_driver(profile_id):
    profile, error = _get_profile_scoped(profile_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    if "email" in data:
        profile.email = normalize_text(data.get("email", ""))
    if "notify_enabled" in data:
        profile.notify_enabled = bool(data.get("notify_enabled"))
    if "car_maker" in data:
        profile.car_maker = normalize_text(data.get("car_maker", ""))
    if "car_name" in data:
        profile.car_name = normalize_text(data.get("car_name", ""))
    if "registration_number" in data:
        profile.registration_number = normalize_text(data.get("registration_number", ""))
    if "notes" in data:
        profile.notes = str(data.get("notes", "") or "").strip()
    db.session.commit()
    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"success": True, "driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>", methods=["DELETE"])
@login_required
def api_delete_driver(profile_id):
    profile, error = _get_profile_scoped(profile_id)
    if error:
        return error
    db.session.delete(profile)
    db.session.commit()
    # 保存済みPDFディレクトリも削除。
    try:
        shutil.rmtree(os.path.join(STORAGE_DIR, str(profile_id)), ignore_errors=True)
    except OSError:
        logger.warning("運転士書類フォルダの削除に失敗: %s", profile_id, exc_info=True)
    return jsonify({"success": True})


# --------------------------------------------------------------------------- #
# 一括起票（社員名簿PLUSから）
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/bulk/candidates", methods=["GET"])
@login_required
def api_bulk_candidates():
    """指定営業所の在籍社員一覧（未登録/登録済みフラグ付き）を返す。"""
    office = normalize_text(request.args.get("office", ""))
    if not office:
        return jsonify({"error": "営業所を指定してください。"}), 400
    if not _can_access_office(office):
        return jsonify({"error": "この営業所へのアクセス権限がありません。"}), 403

    q = normalize_text(request.args.get("q", ""))
    query = Employee.query.filter(
        Employee.is_deleted.isnot(True),
        Employee.is_retired.isnot(True),
        Employee.office_code == office,
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Employee.employee_number.ilike(like),
                Employee.employee_name.ilike(like),
                Employee.employee_kana.ilike(like),
            )
        )
    rows = query.order_by(Employee.employee_number.asc()).all()
    linked = {
        row[0]
        for row in DriverVehicleProfile.query.with_entities(DriverVehicleProfile.employee_number).all()
    }
    employees = []
    registered = 0
    for emp in rows:
        option = employee_to_option(emp)
        option["has_profile"] = emp.employee_number in linked
        if option["has_profile"]:
            registered += 1
        employees.append(option)
    return jsonify({
        "employees": employees,
        "count": len(employees),
        "registered": registered,
        "unregistered": len(employees) - registered,
    })


@car_inspe_bp.route("/api/bulk", methods=["POST"])
@login_required
def api_bulk_create():
    """社員名簿PLUSから営業所単位で運転士を一括起票する。

    body:
      office: 対象営業所コード（必須・アクセス権必要）
      employee_numbers: 起票する社員番号の配列（指定者のみ起票）
      all_in_office: true のとき、当該営業所の在籍者全員を起票
    既に登録済みの社員はスキップする。"""
    data = request.get_json(silent=True) or {}
    office = normalize_text(data.get("office", ""))
    if not office:
        return jsonify({"error": "営業所を指定してください。"}), 400
    if not _can_access_office(office):
        return jsonify({"error": "この営業所への登録権限がありません。"}), 403

    numbers = data.get("employee_numbers")
    all_in_office = bool(data.get("all_in_office"))

    query = Employee.query.filter(
        Employee.is_deleted.isnot(True),
        Employee.is_retired.isnot(True),
        Employee.office_code == office,
    )
    if isinstance(numbers, list) and numbers:
        wanted = {normalize_text(n) for n in numbers if normalize_text(n)}
        if not wanted:
            return jsonify({"error": "登録対象の社員を選択してください。"}), 400
        query = query.filter(Employee.employee_number.in_(wanted))
    elif not all_in_office:
        return jsonify({"error": "登録対象の社員を選択してください。"}), 400

    employees = query.order_by(Employee.employee_number.asc()).all()
    if not employees:
        return jsonify({"success": True, "created": 0, "skipped": 0})

    linked = {
        row[0]
        for row in DriverVehicleProfile.query.with_entities(DriverVehicleProfile.employee_number).all()
    }
    actor = _actor()
    created = 0
    skipped = 0
    for emp in employees:
        if emp.employee_number in linked:
            skipped += 1
            continue
        profile = DriverVehicleProfile(
            employee_number=emp.employee_number,
            employee_name=emp.employee_name or "",
            office_code=emp.office_code or "",
            office_name=emp.office_name or "",
            email=emp.email or "",
            notify_enabled=True,
            created_by=actor,
        )
        db.session.add(profile)
        linked.add(emp.employee_number)
        created += 1
    if created:
        db.session.commit()
    return jsonify({"success": True, "created": created, "skipped": skipped})


@car_inspe_bp.route("/api/resync", methods=["POST"])
@login_required
def api_resync_roster():
    """登録済みプロファイルの氏名・営業所を社員名簿PLUSの現在値へ同期する。

    社員の異動・改名を反映する。通知先メール等の個別設定は変更しない。
    アクセス範囲内のプロファイルのみを対象とする。"""
    query = _scoped_profile_query()
    if query is None:
        return jsonify({"success": True, "updated": 0})
    profiles = query.all()
    numbers = {p.employee_number for p in profiles}
    employees = (
        {e.employee_number: e for e in Employee.query.filter(Employee.employee_number.in_(numbers)).all()}
        if numbers
        else {}
    )
    updated = 0
    for profile in profiles:
        emp = employees.get(profile.employee_number)
        if emp is None:
            continue
        new_name = emp.employee_name or ""
        new_office_code = (emp.office_code or "").strip()
        new_office_name = emp.office_name or ""
        if (
            (profile.employee_name or "") != new_name
            or (profile.office_code or "") != new_office_code
            or (profile.office_name or "") != new_office_name
        ):
            profile.employee_name = new_name
            profile.office_code = new_office_code
            profile.office_name = new_office_name
            updated += 1
    if updated:
        db.session.commit()
    return jsonify({"success": True, "updated": updated})


# --------------------------------------------------------------------------- #
# 書類（車検証 / 自賠責 / 任意保険 / 免許証）
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/drivers/<int:profile_id>/documents/<doc_type>", methods=["POST"])
@login_required
def api_save_document(profile_id, doc_type):
    if doc_type not in DOC_TYPES:
        return jsonify({"error": "不明な書類種別です。"}), 400
    profile, error = _get_profile_scoped(profile_id)
    if error:
        return error

    # multipart/form-data（ファイル付き）と JSON の両対応。
    if request.form or request.files:
        form = request.form
        upload = request.files.get("file")
    else:
        form = request.get_json(silent=True) or {}
        upload = None

    expiry_raw = form.get("expiry_date", "")
    expiry = normalize_date(expiry_raw)
    if expiry_raw and not expiry:
        return jsonify({"error": "有効期限の日付を解釈できませんでした（例: 2027-03-31）。"}), 400

    doc = DriverDocument.query.filter_by(profile_id=profile.id, doc_type=doc_type).first()
    if doc is None:
        doc = DriverDocument(profile_id=profile.id, doc_type=doc_type)
        db.session.add(doc)

    previous_expiry = doc.expiry_date
    doc.expiry_date = expiry
    doc.issued_date = normalize_date(form.get("issued_date", ""))
    doc.document_number = normalize_text(form.get("document_number", "")) or None
    doc.issuer = normalize_text(form.get("issuer", "")) or None
    doc.notes = str(form.get("notes", "") or "").strip() or None

    if upload and upload.filename:
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            db.session.rollback()
            return jsonify({"error": "PDFまたは画像(PNG/JPG)ファイルを選択してください。"}), 400
        upload.seek(0, os.SEEK_END)
        size = upload.tell()
        upload.seek(0)
        if size > MAX_UPLOAD_BYTES:
            db.session.rollback()
            return jsonify({"error": "ファイルサイズが大きすぎます（上限15MB）。"}), 400
        # 旧ファイルを削除。
        if doc.stored_path and os.path.exists(doc.stored_path):
            try:
                os.remove(doc.stored_path)
            except OSError:
                logger.warning("旧書類ファイルの削除に失敗: %s", doc.stored_path, exc_info=True)
        storage_dir = ensure_storage_dir(profile.id)
        safe_name = secure_filename(upload.filename) or f"{doc_type}{ext}"
        stored_filename = f"{doc_type}_{uuid.uuid4().hex[:8]}_{safe_name}"
        stored_path = os.path.join(storage_dir, stored_filename)
        upload.save(stored_path)
        doc.original_filename = upload.filename
        doc.stored_filename = stored_filename
        doc.stored_path = stored_path

    # 有効期限が変わったら通知ステージをリセットして再通知できるようにする。
    if previous_expiry != doc.expiry_date:
        doc.last_notified_stage = None
        doc.last_notified_at = None

    doc.uploaded_by = _actor()
    doc.uploaded_at = utc_now()
    db.session.commit()

    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"success": True, "driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>/documents/<doc_type>", methods=["DELETE"])
@login_required
def api_delete_document(profile_id, doc_type):
    if doc_type not in DOC_TYPES:
        return jsonify({"error": "不明な書類種別です。"}), 400
    profile, error = _get_profile_scoped(profile_id)
    if error:
        return error
    doc = DriverDocument.query.filter_by(profile_id=profile.id, doc_type=doc_type).first()
    if doc is None:
        return jsonify({"error": "対象の書類が見つかりません。"}), 404
    stored_path = doc.stored_path
    db.session.delete(doc)
    db.session.commit()
    if stored_path and os.path.exists(stored_path):
        try:
            os.remove(stored_path)
        except OSError:
            logger.warning("書類ファイルの削除に失敗: %s", stored_path, exc_info=True)
    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"success": True, "driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/documents/<int:doc_id>/download", methods=["GET"])
@login_required
def api_download_document(doc_id):
    doc = db.session.get(DriverDocument, doc_id)
    if doc is None or not doc.stored_path or not os.path.exists(doc.stored_path):
        return jsonify({"error": "書類ファイルが見つかりません。"}), 404
    profile = db.session.get(DriverVehicleProfile, doc.profile_id)
    if profile is None or not _can_access_office(profile.office_code):
        return jsonify({"error": "この書類へのアクセス権限がありません。"}), 403
    download_name = doc.original_filename or doc.stored_filename or os.path.basename(doc.stored_path)
    return send_file(doc.stored_path, as_attachment=True, download_name=download_name)


# --------------------------------------------------------------------------- #
# メール設定の確認・テスト送信（管理者）
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/mail-status", methods=["GET"])
@login_required
def api_mail_status():
    settings = mail_service.mail_settings()
    return jsonify({
        "configured": mail_service.is_mail_configured(),
        "host": settings["host"],
        "from_address": settings["from_address"],
        "can_admin": _is_admin(),
    })


@car_inspe_bp.route("/api/test-mail", methods=["POST"])
@login_required
def api_test_mail():
    if not _is_admin():
        return jsonify({"error": "テスト送信は管理者のみ利用できます。"}), 403
    data = request.get_json(silent=True) or {}
    to_address = normalize_text(data.get("to", ""))
    if not to_address:
        return jsonify({"error": "送信先メールアドレスを入力してください。"}), 400
    if not mail_service.is_mail_configured():
        return jsonify({"error": "SMTPが未設定です（DSTT_SMTP_HOST / DSTT_MAIL_FROM を設定してください）。"}), 400
    message = mail_service.send_mail_now(
        to_address,
        "【テスト送信】マイカー管理ツール",
        "これは DSTT メール送信基盤のテストメールです。\nこのメールが届いていれば送信設定は正常です。",
        category="test",
        created_by=_actor(),
    )
    if message is None:
        return jsonify({"error": "送信に失敗しました。"}), 500
    if message.status == mail_service.STATUS_SENT:
        return jsonify({"success": True, "status": message.status})
    return jsonify({"error": message.last_error or "送信に失敗しました。", "status": message.status}), 502


# --------------------------------------------------------------------------- #
# 画面
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/", methods=["GET"])
@login_required
def car_inspection():
    admin = _is_admin()
    codes = _accessible_codes_or_none()
    offices = _office_options(None if admin else codes)
    can_bulk = admin or bool(codes)
    return render_template(
        "car_inspe.html",
        error=None,
        offices=offices,
        is_admin=admin,
        can_bulk=can_bulk,
        no_office=(not admin and not codes),
    )
