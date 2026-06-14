"""運転士マイカー管理ツール（旧: 現場車両の車検証ツール）。

自社社員（運転士）のマイカーについて、車検証・自賠責保険証・任意保険証・
運転免許証の有効期限と書類PDFを管理する。社員名簿PLUS（``employees``）と
社員番号で紐づけ、期限が近づくと登録メールアドレスへ自動通知する
（通知は ``app.services.driver_doc_notify`` ＋ ``app.services.mail_service``）。

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

from app.access_control import is_admin_user
from app.models import DriverDocument, DriverVehicleProfile, Employee, db
from app.services import mail_service

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")
logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(APP_ROOT, "..", "var", "car_inspe", "storage", "driver")

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
# 社員検索（社員名簿PLUS 連携）
# --------------------------------------------------------------------------- #
@car_inspe_bp.route("/api/employees", methods=["GET"])
@login_required
def api_employees():
    q = normalize_text(request.args.get("q", ""))
    try:
        limit = int(request.args.get("limit", 30) or 30)
    except (TypeError, ValueError):
        limit = 30
    limit = min(max(limit, 1), 100)

    query = Employee.query.filter(
        Employee.is_deleted.isnot(True),
        Employee.is_retired.isnot(True),
    )
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
    q = normalize_text(request.args.get("q", ""))
    status = normalize_text(request.args.get("status", ""))
    query = DriverVehicleProfile.query
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
    profiles = query.order_by(DriverVehicleProfile.employee_number.asc()).all()
    today = datetime.now().strftime("%Y%m%d")
    drivers = [serialize_profile(profile, today) for profile in profiles]
    if status:
        drivers = [d for d in drivers if d["status"] == status]

    summary = {
        "drivers": DriverVehicleProfile.query.count(),
        "expired": sum(1 for d in drivers if d["status"] == "expired"),
        "expiring": sum(1 for d in drivers if d["status"] == "expiring"),
        "incomplete": sum(1 for d in drivers if d["status"] == "incomplete"),
    }
    return jsonify({
        "drivers": drivers,
        "count": len(drivers),
        "summary": summary,
        "mail_configured": mail_service.is_mail_configured(),
        "can_admin": is_admin_user(),
    })


@car_inspe_bp.route("/api/drivers", methods=["POST"])
@login_required
def api_create_driver():
    data = request.get_json(silent=True) or {}
    employee_number = normalize_text(data.get("employee_number", ""))
    if not employee_number:
        return jsonify({"error": "社員番号は必須です。社員名簿PLUSから選択してください。"}), 400

    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if employee is None:
        return jsonify({"error": f"社員番号 {employee_number} が社員名簿PLUSに見つかりません。"}), 404

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
    profile = db.session.get(DriverVehicleProfile, profile_id)
    if profile is None:
        return jsonify({"error": "対象の運転士が見つかりません。"}), 404
    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>", methods=["PUT"])
@login_required
def api_update_driver(profile_id):
    profile = db.session.get(DriverVehicleProfile, profile_id)
    if profile is None:
        return jsonify({"error": "対象の運転士が見つかりません。"}), 404
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
    profile = db.session.get(DriverVehicleProfile, profile_id)
    if profile is None:
        return jsonify({"error": "対象の運転士が見つかりません。"}), 404
    db.session.delete(profile)
    db.session.commit()
    # 保存済みPDFディレクトリも削除。
    try:
        shutil.rmtree(os.path.join(STORAGE_DIR, str(profile_id)), ignore_errors=True)
    except OSError:
        logger.warning("運転士書類フォルダの削除に失敗: %s", profile_id, exc_info=True)
    return jsonify({"success": True})


# --------------------------------------------------------------------------- #
# 書類（車検証 / 自賠責 / 任意保険 / 免許証）
# --------------------------------------------------------------------------- #
def _get_profile_or_404(profile_id):
    profile = db.session.get(DriverVehicleProfile, profile_id)
    if profile is None:
        return None, (jsonify({"error": "対象の運転士が見つかりません。"}), 404)
    return profile, None


@car_inspe_bp.route("/api/drivers/<int:profile_id>/documents/<doc_type>", methods=["POST"])
@login_required
def api_save_document(profile_id, doc_type):
    if doc_type not in DOC_TYPES:
        return jsonify({"error": "不明な書類種別です。"}), 400
    profile, error = _get_profile_or_404(profile_id)
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
    doc.uploaded_at = datetime.utcnow()
    db.session.commit()

    today = datetime.now().strftime("%Y%m%d")
    return jsonify({"success": True, "driver": serialize_profile(profile, today)})


@car_inspe_bp.route("/api/drivers/<int:profile_id>/documents/<doc_type>", methods=["DELETE"])
@login_required
def api_delete_document(profile_id, doc_type):
    if doc_type not in DOC_TYPES:
        return jsonify({"error": "不明な書類種別です。"}), 400
    profile, error = _get_profile_or_404(profile_id)
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
        "can_admin": is_admin_user(),
    })


@car_inspe_bp.route("/api/test-mail", methods=["POST"])
@login_required
def api_test_mail():
    if not is_admin_user():
        return jsonify({"error": "テスト送信は管理者のみ利用できます。"}), 403
    data = request.get_json(silent=True) or {}
    to_address = normalize_text(data.get("to", ""))
    if not to_address:
        return jsonify({"error": "送信先メールアドレスを入力してください。"}), 400
    if not mail_service.is_mail_configured():
        return jsonify({"error": "SMTPが未設定です（DSTT_SMTP_HOST / DSTT_MAIL_FROM を設定してください）。"}), 400
    message = mail_service.send_mail_now(
        to_address,
        "【テスト送信】運転士マイカー管理ツール",
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
    return render_template("car_inspe.html", error=None)
