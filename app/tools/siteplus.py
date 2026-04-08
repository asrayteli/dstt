from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app.models import Site, SiteBranch, db

try:
    from .shiftersync_format import SHIFT_OPTION_MAPPINGS
except ImportError:
    from app.tools.shiftersync_format import SHIFT_OPTION_MAPPINGS  # type: ignore


siteplus_bp = Blueprint("siteplus", __name__, url_prefix="/tools/siteplus")

PENDING_OPTION_KEY = "PENDING"
VEHICLE_OPTION_KEYS = ("M", "C", "O", "W", "V", "N1", "N2", "N3", "N4", "N5")
VEHICLE_OPTION_LABELS = {
    key: SHIFT_OPTION_MAPPINGS.get(key, key)
    for key in VEHICLE_OPTION_KEYS
}
VEHICLE_OPTION_LABELS[PENDING_OPTION_KEY] = "保留"

SITE_FIELD_LABELS = {
    "site_id": "契約番号",
    "site_name": "現場名",
    "site_manager_last": "担当者(姓)",
    "site_manager_first": "担当者(名)",
    "site_manager_id": "担当者ID",
    "is_active": "有効状態",
}

BRANCH_FIELD_LABELS = {
    "site_branch": "枝番号",
    "cloudshift_option_key": "CloudShiftオプション",
    "is_active": "有効状態",
}


@siteplus_bp.before_app_request
def ensure_siteplus_schema():
    if current_app.extensions.get("siteplus_schema_ready"):
        return
    db.create_all()
    current_app.extensions["siteplus_schema_ready"] = True


def _user_id() -> str:
    return str(getattr(current_user, "username", "") or "")


def _get_site_or_404(site_row_id: int) -> Site:
    site = db.session.get(Site, site_row_id)
    if site is None:
        abort(404)
    return site


def _get_branch_or_404(branch_id: int) -> SiteBranch:
    branch = db.session.get(SiteBranch, branch_id)
    if branch is None:
        abort(404)
    return branch


def _parse_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_required_text(value, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}は必須です")
    return text


def _normalize_site_id(value) -> str:
    text = _normalize_required_text(value, "契約番号")
    if not text.isdigit():
        raise ValueError("契約番号は数字のみで入力してください")
    if len(text) > 5:
        raise ValueError("契約番号は5桁以内で入力してください")
    return text.zfill(5)


def _normalize_site_branch(value) -> str:
    text = _normalize_required_text(value, "枝番号")
    if not text.isdigit():
        raise ValueError("枝番号は数字のみで入力してください")
    if len(text) > 3:
        raise ValueError("枝番号は3桁以内で入力してください")
    return text.zfill(3)


def _normalize_option_key(value) -> str:
    text = _normalize_required_text(value, "CloudShiftオプション").upper()
    if text not in VEHICLE_OPTION_LABELS:
        raise ValueError("CloudShiftオプションが不正です")
    return text


def _parse_updated_from(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text)


def _parse_updated_to(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text) + timedelta(days=1)


def _site_duplicate_rows(site_name: str, exclude_site_row_id: int | None = None) -> list[dict]:
    query = Site.query.filter(Site.site_name == site_name).order_by(Site.site_id.asc())
    if exclude_site_row_id is not None:
        query = query.filter(Site.id != exclude_site_row_id)

    rows: list[dict] = []
    for site in query.all():
        branches = list(site.branches)
        if not branches:
            rows.append(
                {
                    "site_row_id": site.id,
                    "branch_id": None,
                    "site_id": site.site_id,
                    "site_branch": "000",
                    "site_register": site.site_register,
                    "updated_at": site.updated_at.isoformat() if site.updated_at else None,
                    "is_active": site.is_active,
                }
            )
            continue
        for branch in branches:
            updated_at = branch.updated_at or site.updated_at
            rows.append(
                {
                    "site_row_id": site.id,
                    "branch_id": branch.id,
                    "site_id": site.site_id,
                    "site_branch": branch.site_branch,
                    "site_register": site.site_register,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                    "is_active": bool(site.is_active and branch.is_active),
                }
            )
    return rows


def _serialize_site(site: Site, *, include_inactive_branches: bool) -> dict:
    payload = site.to_dict(include_branches=True, include_inactive_branches=include_inactive_branches)
    payload["has_branches"] = bool(payload["branch_count"])
    return payload


def _changes_for_payload(before: dict, after: dict, labels: dict[str, str], formatters: dict[str, callable] | None = None) -> list[dict]:
    changes = []
    formatters = formatters or {}
    for key, label in labels.items():
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value == new_value:
            continue
        formatter = formatters.get(key)
        if formatter:
            old_value = formatter(old_value)
            new_value = formatter(new_value)
        changes.append(
            {
                "field": key,
                "label": label,
                "before": old_value,
                "after": new_value,
            }
        )
    return changes


def _site_option_label(value):
    if value is None:
        return ""
    return VEHICLE_OPTION_LABELS.get(str(value), str(value))


def _site_payload_from_request(data: dict, *, existing: Site | None = None) -> dict:
    site_id = _normalize_site_id(data.get("site_id"))
    site_name = _normalize_required_text(data.get("site_name"), "現場名")
    site_manager_last = _normalize_required_text(data.get("site_manager_last"), "担当者(姓)")
    site_manager_first = _normalize_required_text(data.get("site_manager_first"), "担当者(名)")
    site_manager_id = _normalize_required_text(data.get("site_manager_id"), "担当者ID")
    is_active = _parse_bool(data.get("is_active"), default=True if existing is None else existing.is_active)

    query = Site.query.filter(Site.site_id == site_id)
    if existing is not None:
        query = query.filter(Site.id != existing.id)
    if query.first():
        raise ValueError("その契約番号は既に登録されています")

    return {
        "site_id": site_id,
        "site_name": site_name,
        "site_manager_last": site_manager_last,
        "site_manager_first": site_manager_first,
        "site_manager_id": site_manager_id,
        "is_active": is_active,
    }


def _branch_payload_from_request(data: dict, site: Site, *, existing: SiteBranch | None = None) -> dict:
    site_branch = _normalize_site_branch(data.get("site_branch"))
    cloudshift_option_key = _normalize_option_key(data.get("cloudshift_option_key"))
    is_active = _parse_bool(data.get("is_active"), default=True if existing is None else existing.is_active)

    query = SiteBranch.query.filter(
        SiteBranch.site_row_id == site.id,
        SiteBranch.site_branch == site_branch,
    )
    if existing is not None:
        query = query.filter(SiteBranch.id != existing.id)
    if query.first():
        raise ValueError("その枝番号はこの契約番号に既に登録されています")

    return {
        "site_branch": site_branch,
        "cloudshift_option_key": cloudshift_option_key,
        "is_active": is_active,
    }


def _site_preview_response(payload: dict, *, existing: Site | None = None) -> dict:
    before = existing.to_dict(include_branches=False) if existing else {}
    duplicates = _site_duplicate_rows(payload["site_name"], exclude_site_row_id=existing.id if existing else None)
    changes = _changes_for_payload(before, payload, SITE_FIELD_LABELS)
    return {
        "normalized": payload,
        "duplicates": duplicates,
        "requires_duplicate_confirmation": bool(duplicates),
        "requires_change_confirmation": bool(existing and changes),
        "changes": changes,
    }


def _branch_preview_response(payload: dict, branch: SiteBranch | None = None) -> dict:
    before = branch.to_dict() if branch else {}
    changes = _changes_for_payload(
        before,
        payload,
        BRANCH_FIELD_LABELS,
        formatters={"cloudshift_option_key": _site_option_label},
    )
    return {
        "normalized": payload,
        "requires_change_confirmation": bool(branch and changes),
        "changes": changes,
    }


def _site_query_from_filters(args):
    include_inactive = _parse_bool(args.get("include_inactive"), default=False)
    query = Site.query.outerjoin(SiteBranch)

    if not include_inactive:
        query = query.filter(Site.is_active.is_(True))

    q = str(args.get("q", "") or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Site.site_id.ilike(like),
                Site.site_name.ilike(like),
                Site.site_manager_last.ilike(like),
                Site.site_manager_first.ilike(like),
                Site.site_manager_id.ilike(like),
                Site.site_register.ilike(like),
                Site.site_updater.ilike(like),
                SiteBranch.site_branch.ilike(like),
                SiteBranch.cloudshift_option_key.ilike(like),
            )
        )

    site_id = str(args.get("site_id", "") or "").strip()
    if site_id:
        query = query.filter(Site.site_id == _normalize_site_id(site_id))

    site_name = str(args.get("site_name", "") or "").strip()
    if site_name:
        query = query.filter(Site.site_name.ilike(f"%{site_name}%"))

    manager_name = str(args.get("manager_name", "") or "").strip()
    if manager_name:
        like = f"%{manager_name}%"
        query = query.filter(
            or_(
                Site.site_manager_last.ilike(like),
                Site.site_manager_first.ilike(like),
                (Site.site_manager_last + Site.site_manager_first).ilike(f"%{manager_name.replace(' ', '')}%"),
                (Site.site_manager_last + " " + Site.site_manager_first).ilike(like),
            )
        )

    manager_id = str(args.get("site_manager_id", "") or "").strip()
    if manager_id:
        query = query.filter(Site.site_manager_id.ilike(f"%{manager_id}%"))

    site_branch = str(args.get("site_branch", "") or "").strip()
    if site_branch:
        query = query.filter(SiteBranch.site_branch == _normalize_site_branch(site_branch))
        if not include_inactive:
            query = query.filter(SiteBranch.is_active.is_(True))

    option_key = str(args.get("cloudshift_option_key", "") or "").strip().upper()
    if option_key:
        query = query.filter(SiteBranch.cloudshift_option_key == option_key)
        if not include_inactive:
            query = query.filter(SiteBranch.is_active.is_(True))

    site_register = str(args.get("site_register", "") or "").strip()
    if site_register:
        query = query.filter(Site.site_register.ilike(f"%{site_register}%"))

    site_updater = str(args.get("site_updater", "") or "").strip()
    if site_updater:
        query = query.filter(Site.site_updater.ilike(f"%{site_updater}%"))

    updated_from = _parse_updated_from(args.get("updated_from", ""))
    if updated_from:
        query = query.filter(Site.updated_at >= updated_from)

    updated_to = _parse_updated_to(args.get("updated_to", ""))
    if updated_to:
        query = query.filter(Site.updated_at < updated_to)

    if _parse_bool(args.get("duplicate_name_only"), default=False):
        duplicate_names = (
            db.session.query(Site.site_name)
            .group_by(Site.site_name)
            .having(func.count(Site.id) > 1)
            .subquery()
        )
        query = query.filter(Site.site_name.in_(duplicate_names))

    return query.distinct(), include_inactive


@siteplus_bp.route("/")
@login_required
def index():
    return render_template(
        "siteplus.html",
        user_id=_user_id(),
        option_items=_cloudshift_option_items(),
    )


def _cloudshift_option_items() -> list[dict]:
    items = [{"key": PENDING_OPTION_KEY, "label": VEHICLE_OPTION_LABELS[PENDING_OPTION_KEY]}]
    for key in VEHICLE_OPTION_KEYS:
        items.append({"key": key, "label": VEHICLE_OPTION_LABELS[key]})
    return items


def _read_csv_rows_with_fallback(upload) -> list[list[str]]:
    raw = upload.read() or b""
    encodings = ("utf-8-sig", "cp932", "shift_jis", "utf-8")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            return list(csv.reader(io.StringIO(text)))
        except Exception as exc:  # pragma: no cover - decode failure handling
            last_error = exc
    raise ValueError("CSVの文字コードを判別できません（UTF-8 / CP932 を確認してください）") from last_error


@siteplus_bp.route("/api/options")
@login_required
def api_options():
    return jsonify({"options": _cloudshift_option_items()})


@siteplus_bp.route("/api/sites")
@login_required
def api_sites():
    query, include_inactive = _site_query_from_filters(request.args)
    sites = query.order_by(Site.site_id.asc()).all()
    payload = [_serialize_site(site, include_inactive_branches=include_inactive) for site in sites]
    duplicate_name_counts = {
        row.site_name: row.count
        for row in db.session.query(Site.site_name, func.count(Site.id).label("count")).group_by(Site.site_name).all()
    }
    for item in payload:
        item["has_duplicate_name"] = duplicate_name_counts.get(item["site_name"], 0) > 1
    return jsonify({"sites": payload, "count": len(payload)})


@siteplus_bp.route("/api/sites/<int:site_row_id>")
@login_required
def api_site_detail(site_row_id: int):
    include_inactive = _parse_bool(request.args.get("include_inactive"), default=True)
    site = _get_site_or_404(site_row_id)
    return jsonify({"site": _serialize_site(site, include_inactive_branches=include_inactive)})


@siteplus_bp.route("/api/sites/preview", methods=["POST"])
@login_required
def api_site_preview():
    data = request.get_json(silent=True) or {}
    try:
        payload = _site_payload_from_request(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_site_preview_response(payload))


@siteplus_bp.route("/api/sites", methods=["POST"])
@login_required
def api_site_create():
    data = request.get_json(silent=True) or {}
    try:
        payload = _site_payload_from_request(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    preview = _site_preview_response(payload)
    if preview["requires_duplicate_confirmation"] and not _parse_bool(data.get("confirm_duplicate"), default=False):
        return jsonify({"error": "同名の現場が既に存在します", **preview}), 409

    user_id = _user_id()
    site = Site(
        **payload,
        site_register=user_id,
        site_updater=user_id,
    )
    db.session.add(site)
    db.session.commit()
    return jsonify({"success": True, "site": _serialize_site(site, include_inactive_branches=True)})


@siteplus_bp.route("/api/sites/<int:site_row_id>/preview", methods=["PUT"])
@login_required
def api_site_update_preview(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _site_payload_from_request(data, existing=site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_site_preview_response(payload, existing=site))


@siteplus_bp.route("/api/sites/<int:site_row_id>", methods=["PUT"])
@login_required
def api_site_update(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _site_payload_from_request(data, existing=site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    preview = _site_preview_response(payload, existing=site)
    if preview["requires_change_confirmation"] and not _parse_bool(data.get("confirm_changes"), default=False):
        return jsonify({"error": "変更確認が必要です", **preview}), 409
    if preview["requires_duplicate_confirmation"] and not _parse_bool(data.get("confirm_duplicate"), default=False):
        return jsonify({"error": "同名の現場が既に存在します", **preview}), 409

    for key, value in payload.items():
        setattr(site, key, value)
    site.site_updater = _user_id()
    if not site.is_active:
        for branch in site.branches:
            branch.is_active = False
            branch.site_updater = _user_id()
    db.session.commit()
    return jsonify({"success": True, "site": _serialize_site(site, include_inactive_branches=True)})


@siteplus_bp.route("/api/sites/<int:site_row_id>", methods=["DELETE"])
@login_required
def api_site_delete(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    if not _parse_bool(data.get("confirm"), default=False):
        return jsonify(
            {
                "error": "削除確認が必要です",
                "requires_confirmation": True,
                "site": _serialize_site(site, include_inactive_branches=True),
            }
        ), 409

    site.is_active = False
    site.site_updater = _user_id()
    for branch in site.branches:
        branch.is_active = False
        branch.site_updater = _user_id()
    db.session.commit()
    return jsonify({"success": True})


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches/preview", methods=["POST"])
@login_required
def api_branch_create_preview(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_branch_preview_response(payload))


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches", methods=["GET"])
@login_required
def api_branch_list(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    include_inactive = _parse_bool(request.args.get("include_inactive"), default=True)
    branches = [branch.to_dict() for branch in site.branches if include_inactive or branch.is_active]
    return jsonify({"site_row_id": site.id, "branches": branches})


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches", methods=["POST"])
@login_required
def api_branch_create(site_row_id: int):
    site = _get_site_or_404(site_row_id)
    if not site.is_active:
        return jsonify({"error": "無効化された現場には枝番号を追加できません"}), 400

    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_id = _user_id()
    branch = SiteBranch(
        site_row_id=site.id,
        site_register=user_id,
        site_updater=user_id,
        **payload,
    )
    db.session.add(branch)
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict()})


@siteplus_bp.route("/api/branches/<int:branch_id>/preview", methods=["PUT"])
@login_required
def api_branch_update_preview(branch_id: int):
    branch = _get_branch_or_404(branch_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, branch.site, existing=branch)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_branch_preview_response(payload, branch=branch))


@siteplus_bp.route("/api/branches/<int:branch_id>", methods=["PUT"])
@login_required
def api_branch_update(branch_id: int):
    branch = _get_branch_or_404(branch_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, branch.site, existing=branch)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    preview = _branch_preview_response(payload, branch=branch)
    if preview["requires_change_confirmation"] and not _parse_bool(data.get("confirm_changes"), default=False):
        return jsonify({"error": "変更確認が必要です", **preview}), 409

    for key, value in payload.items():
        setattr(branch, key, value)
    branch.site_updater = _user_id()
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict()})


@siteplus_bp.route("/api/branches/<int:branch_id>", methods=["DELETE"])
@login_required
def api_branch_delete(branch_id: int):
    branch = _get_branch_or_404(branch_id)
    data = request.get_json(silent=True) or {}
    if not _parse_bool(data.get("confirm"), default=False):
        return jsonify(
            {
                "error": "削除確認が必要です",
                "requires_confirmation": True,
                "branch": branch.to_dict(),
            }
        ), 409

    branch.is_active = False
    branch.site_updater = _user_id()
    db.session.commit()
    return jsonify({"success": True})


@siteplus_bp.route("/api/import-site-table", methods=["POST"])
@login_required
def api_import_site_table():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "CSVファイルを選択してください"}), 400

    try:
        rows = _read_csv_rows_with_fallback(upload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not rows:
        return jsonify({"error": "CSVにデータがありません"}), 400

    actor = _user_id()
    summary = {
        "total_rows": 0,
        "processed_rows": 0,
        "site_created": 0,
        "site_updated": 0,
        "branch_created": 0,
        "branch_skipped": 0,
        "errors": [],
    }

    for row_index, row in enumerate(rows[1:], start=2):
        if not row or not any(str(cell or "").strip() for cell in row):
            continue
        summary["total_rows"] += 1
        try:
            contract_code = str(row[0] if len(row) > 0 else "").strip()
            site_manager_last = _normalize_required_text(row[2] if len(row) > 2 else "", "担当者(姓)")
            site_manager_first = _normalize_required_text(row[3] if len(row) > 3 else "", "担当者(名)")
            site_manager_id = _normalize_required_text(row[4] if len(row) > 4 else "", "担当者番号")
            site_name = _normalize_required_text(row[5] if len(row) > 5 else "", "現場名")

            if len(contract_code) != 8 or not contract_code.isdigit():
                raise ValueError("契約コードは8桁の数字で入力してください")

            site_id = _normalize_site_id(contract_code[:5])
            site_branch = _normalize_site_branch(contract_code[5:])

            site = Site.query.filter(Site.site_id == site_id).first()
            if site is None:
                site = Site(
                    site_id=site_id,
                    site_name=site_name,
                    site_manager_last=site_manager_last,
                    site_manager_first=site_manager_first,
                    site_manager_id=site_manager_id,
                    site_register=actor,
                    site_updater=actor,
                    is_active=True,
                )
                db.session.add(site)
                db.session.flush()
                summary["site_created"] += 1
            else:
                updated = False
                if site.site_name != site_name:
                    site.site_name = site_name
                    updated = True
                if site.site_manager_last != site_manager_last:
                    site.site_manager_last = site_manager_last
                    updated = True
                if site.site_manager_first != site_manager_first:
                    site.site_manager_first = site_manager_first
                    updated = True
                if site.site_manager_id != site_manager_id:
                    site.site_manager_id = site_manager_id
                    updated = True
                if not site.is_active:
                    site.is_active = True
                    updated = True
                if updated:
                    site.site_updater = actor
                    summary["site_updated"] += 1

            branch = SiteBranch.query.filter(
                SiteBranch.site_row_id == site.id,
                SiteBranch.site_branch == site_branch,
            ).first()
            if branch:
                summary["branch_skipped"] += 1
            else:
                db.session.add(
                    SiteBranch(
                        site_row_id=site.id,
                        site_branch=site_branch,
                        cloudshift_option_key=PENDING_OPTION_KEY,
                        site_register=actor,
                        site_updater=actor,
                        is_active=True,
                    )
                )
                summary["branch_created"] += 1

            summary["processed_rows"] += 1
        except ValueError as exc:
            summary["errors"].append({"row": row_index, "message": str(exc)})

    db.session.commit()
    return jsonify({"success": True, "summary": summary})


@siteplus_bp.route("/api/cloudshift/sites")
@login_required
def api_cloudshift_sites():
    q = str(request.args.get("q", "") or "").strip()
    query = Site.query.filter(Site.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Site.site_id.ilike(like), Site.site_name.ilike(like)))
    sites = query.order_by(Site.site_id.asc()).all()
    return jsonify(
        {
            "sites": [
                {
                    "id": site.id,
                    "site_id": site.site_id,
                    "site_name": site.site_name,
                    "active_branch_count": len([branch for branch in site.branches if branch.is_active]),
                }
                for site in sites
            ]
        }
    )


@siteplus_bp.route("/api/cloudshift/sites/<site_id>/branches")
@login_required
def api_cloudshift_branches(site_id: str):
    try:
        normalized_site_id = _normalize_site_id(site_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    site = Site.query.filter_by(site_id=normalized_site_id, is_active=True).first_or_404()
    branches = [
        {
            **branch.to_dict(),
            "option_label": _site_option_label(branch.cloudshift_option_key),
        }
        for branch in site.branches
        if branch.is_active
    ]
    return jsonify(
        {
            "site": {
                "id": site.id,
                "site_id": site.site_id,
                "site_name": site.site_name,
            },
            "branches": branches,
        }
    )
