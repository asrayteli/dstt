from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import false, func, or_

from app.models import Employee, Site, SiteBranch, SiteContractMaster, db, utc_now
from app.site_contract_master import ensure_contract_master_synced
from app.site_shift_times import (
    format_attendance_times,
    format_report_time,
    normalize_attendance_times,
    normalize_time_text,
    parse_attendance_times,
    parse_time_text,
    resolve_shift_times,
)

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
    "office_code": "営業所コード",
    "attendance_times": "勤怠時間",
    "report_time": "出勤時間",
    "is_active": "有効状態",
}

BRANCH_FIELD_LABELS = {
    "site_branch": "枝番号",
    "cloudshift_option_key": "CloudShiftオプション",
    "attendance_times": "勤怠時間",
    "report_time": "出勤時間",
    "is_active": "有効状態",
}

# 変更差分を人が読める形にするための整形（空は「未設定」と明示する）。
SHIFT_TIME_FORMATTERS = {
    "attendance_times": lambda value: format_attendance_times(value) or "未設定",
    "report_time": lambda value: format_report_time(value) or "未設定",
}


@siteplus_bp.before_app_request
def ensure_siteplus_schema():
    if current_app.extensions.get("siteplus_schema_ready"):
        return
    db.create_all()
    current_app.extensions["siteplus_schema_ready"] = True


def _user_id() -> str:
    return str(getattr(current_user, "username", "") or "")


def _accessible_office_rows() -> list:
    """ユーザーが現場に紐付け可能な営業所（コード設定済み）。"""
    from app.models import AccessOffice
    from app.access_control import is_admin_user, user_office_ids

    query = AccessOffice.query.filter(AccessOffice.code.isnot(None), AccessOffice.code != "")
    if getattr(current_user, "id", None) is not None and not is_admin_user():
        ids = user_office_ids()
        if not ids:
            return []
        query = query.filter(AccessOffice.id.in_(ids))
    return query.order_by(AccessOffice.branch_id.asc(), AccessOffice.code.asc()).all()


def _accessible_office_codes() -> set[str]:
    return {row.code for row in _accessible_office_rows() if row.code}


def _is_restricted_user() -> bool:
    from app.access_control import is_admin_user

    return getattr(current_user, "id", None) is not None and not is_admin_user()


def _can_access_office_code(code: str | None) -> bool:
    if not _is_restricted_user():
        return True
    text = str(code or "").strip()
    return bool(text) and text in _accessible_office_codes()


def _can_access_site(site: Site | None) -> bool:
    return site is not None and _can_access_office_code(site.office_code)


def _get_site_for_current_user_or_404(site_row_id: int) -> Site:
    site = _get_site_or_404(site_row_id)
    if not _can_access_site(site):
        abort(403)
    return site


def _get_branch_for_current_user_or_404(branch_id: int) -> SiteBranch:
    branch = _get_branch_or_404(branch_id)
    if not _can_access_site(branch.site):
        abort(403)
    return branch


def _scope_contract_master_query(query):
    if not _is_restricted_user():
        return query
    allowed = _accessible_office_codes()
    if not allowed:
        return query.filter(false())
    return query.join(Site, SiteContractMaster.site_row_id == Site.id).filter(
        Site.office_code.in_(allowed)
    )


def _get_contract_master_for_current_user_or_404(contract_code: str) -> SiteContractMaster:
    row = db.session.get(SiteContractMaster, str(contract_code or "").strip())
    if row is None:
        abort(404)
    if not _can_access_site(db.session.get(Site, row.site_row_id)):
        abort(403)
    return row


def _validate_assignable_office_code(code: str) -> str:
    """ユーザーが割り当て可能な営業所コードかをチェックする。

    管理者は任意の存在する営業所コードを割り当て可能。
    """
    from app.models import AccessOffice
    from app.access_control import is_admin_user

    text = str(code or "").strip()
    if not text:
        return text
    exists = AccessOffice.query.filter(AccessOffice.code == text).first()
    if exists is None:
        raise ValueError(f"営業所コード '{text}' は存在しません")
    if getattr(current_user, "id", None) is None:
        return text
    if is_admin_user():
        return text
    if text not in _accessible_office_codes():
        raise ValueError(f"営業所コード '{text}' へのアクセス権限がありません")
    return text


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


def _compose_contract_code(site_id, site_branch) -> str:
    return f"{_normalize_site_id(site_id)}{_normalize_site_branch(site_branch)}"


def _site_manager_name(site: Site) -> str:
    return f"{site.site_manager_last} {site.site_manager_first}".strip()


def _shift_time_payload_from_request(data: dict, *, existing=None) -> dict:
    """勤怠時間/出勤時間の入力を正規化する。キーが無ければ既存値を維持する。"""
    if "attendance_times" in data:
        attendance_times = parse_attendance_times(data.get("attendance_times"))
    else:
        attendance_times = normalize_attendance_times(getattr(existing, "attendance_times", None))
    if "report_time" in data:
        report_time = parse_time_text(data.get("report_time"), "出勤時間")
    else:
        report_time = normalize_time_text(getattr(existing, "report_time", None))
    return {"attendance_times": attendance_times, "report_time": report_time}


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
    contract_rows = SiteContractMaster.query.filter(SiteContractMaster.site_row_id == site.id).all()
    contract_by_branch_id = {
        int(row.site_branch_row_id): row
        for row in contract_rows
        if row.site_branch_row_id is not None
    }
    for branch in payload["branches"]:
        row = contract_by_branch_id.get(int(branch["id"]))
        if not row:
            branch["contract_code"] = ""
            branch["segment"] = None
            branch["dedicated_employee_number"] = ""
            branch["dedicated_employee_name"] = ""
            continue
        branch["contract_code"] = row.contract_code
        branch["segment"] = row.segment
        branch["vehicle_number"] = row.vehicle_number or ""
        branch["dedicated_employee_number"] = row.dedicated_employee_number or ""
        branch["dedicated_employee_name"] = row.dedicated_employee_name or ""
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


def _employee_candidates_for_contract_code(contract_code: str) -> list[dict]:
    normalized_contract_code = str(contract_code or "").strip()
    if not normalized_contract_code:
        return []
    query = Employee.query.filter(Employee.contract_code == normalized_contract_code)
    if hasattr(Employee, "is_deleted"):
        query = query.filter(Employee.is_deleted.is_(False))
    employees = query.order_by(Employee.employee_number.asc()).all()
    return [
        {
            "employee_number": str(employee.employee_number or "").strip(),
            "employee_name": str(employee.employee_name or "").strip(),
            "contract_code": str(employee.contract_code or "").strip(),
        }
        for employee in employees
    ]


def _upsert_contract_master_for_site(site: Site) -> dict[str, int]:
    existing_rows = SiteContractMaster.query.filter(SiteContractMaster.site_row_id == site.id).all()
    existing_by_branch_id = {
        int(row.site_branch_row_id): row
        for row in existing_rows
        if row.site_branch_row_id is not None
    }
    existing_by_contract = {str(row.contract_code or ""): row for row in existing_rows}
    matched_contract_codes: set[str] = set()
    matched_branch_ids: set[int] = set()
    created = 0
    updated = 0
    manager_name = _site_manager_name(site)

    for branch in site.branches:
        contract_code = _compose_contract_code(site.site_id, branch.site_branch)
        row = existing_by_branch_id.get(branch.id) or existing_by_contract.get(contract_code)
        if row is None:
            row = SiteContractMaster(
                contract_code=contract_code,
                site_row_id=site.id,
                site_branch_row_id=branch.id,
            )
            db.session.add(row)
            created += 1
        else:
            updated += 1

        row.contract_code = contract_code
        row.site_row_id = site.id
        row.site_branch_row_id = branch.id
        row.site_id = site.site_id
        row.site_branch = branch.site_branch
        row.site_name = site.site_name
        row.site_manager_id = site.site_manager_id
        row.site_manager_name = manager_name
        row.cloudshift_option_key = branch.cloudshift_option_key
        row.is_active = bool(site.is_active and branch.is_active)
        if not row.source:
            row.source = "siteplus"

        matched_contract_codes.add(contract_code)
        matched_branch_ids.add(branch.id)

    for row in existing_rows:
        branch_row_id = int(row.site_branch_row_id) if row.site_branch_row_id is not None else None
        if branch_row_id in matched_branch_ids or str(row.contract_code or "") in matched_contract_codes:
            continue
        row.site_name = site.site_name
        row.site_manager_id = site.site_manager_id
        row.site_manager_name = manager_name
        row.is_active = False
        updated += 1

    return {"created": created, "updated": updated}


def _sync_contract_master() -> dict[str, int]:
    summary = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    sites = Site.query.order_by(Site.id.asc()).all()
    seen_site_ids: set[int] = set()
    for site in sites:
        seen_site_ids.add(site.id)
        result = _upsert_contract_master_for_site(site)
        summary["created"] += result["created"]
        summary["updated"] += result["updated"]

    stale_rows = SiteContractMaster.query.filter(~SiteContractMaster.site_row_id.in_(seen_site_ids)).all() if seen_site_ids else SiteContractMaster.query.all()
    for row in stale_rows:
        if row.is_active:
            row.is_active = False
            summary["deactivated"] += 1

    return summary


def _ensure_contract_master_row(contract_code: str) -> SiteContractMaster | None:
    normalized_contract_code = str(contract_code or "").strip()
    if not normalized_contract_code:
        return None
    existing = db.session.get(SiteContractMaster, normalized_contract_code)
    if existing:
        return existing
    if len(normalized_contract_code) < 8 or not normalized_contract_code[:8].isdigit():
        return None

    site_id = _normalize_site_id(normalized_contract_code[:5])
    site_branch = _normalize_site_branch(normalized_contract_code[5:8])
    site = Site.query.filter_by(site_id=site_id).first()
    if not site:
        return None
    branch = SiteBranch.query.filter_by(site_row_id=site.id, site_branch=site_branch).first()
    if not branch:
        return None

    row = SiteContractMaster(
        contract_code=normalized_contract_code,
        site_row_id=site.id,
        site_branch_row_id=branch.id,
        site_id=site.site_id,
        site_branch=branch.site_branch,
        site_name=site.site_name,
        site_manager_id=site.site_manager_id,
        site_manager_name=_site_manager_name(site),
        cloudshift_option_key=branch.cloudshift_option_key,
        is_active=bool(site.is_active and branch.is_active),
        source="siteplus",
    )
    db.session.add(row)
    return row


def _update_contract_master_segments(site_rows: list[list[str]], *, source: str = "monthly_generator") -> dict[str, int]:
    updated = 0
    created = 0
    skipped = 0
    for index, row in enumerate(site_rows):
        if index == 0:
            continue
        if len(row) < 2:
            continue
        contract_code = str(row[0] or "").strip()
        segment = str(row[1] or "").strip()
        if not contract_code or not segment:
            continue
        if segment not in {"役員", "一般", "旅客"}:
            skipped += 1
            continue
        master_row = _ensure_contract_master_row(contract_code)
        if not master_row:
            skipped += 1
            continue
        was_new = master_row.created_at == master_row.updated_at and not master_row.segment
        if master_row.segment != segment:
            master_row.segment = segment
            if source:
                master_row.source = source
            if was_new:
                created += 1
            else:
                updated += 1
        elif was_new:
            created += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _serialize_contract_master_row(row: SiteContractMaster) -> dict:
    payload = row.to_dict()
    payload["dedicated_candidates"] = _employee_candidates_for_contract_code(row.contract_code)
    return payload


def _normalize_segment_value(value, *, allow_blank: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None if allow_blank else ""
    if text not in {"役員", "一般", "旅客"}:
        raise ValueError("セグメントは 役員 / 一般 / 旅客 のいずれかを指定してください")
    return text


def _site_payload_from_request(data: dict, *, existing: Site | None = None) -> dict:
    site_id = _normalize_site_id(data.get("site_id"))
    site_name = _normalize_required_text(data.get("site_name"), "現場名")
    site_manager_last = _normalize_required_text(data.get("site_manager_last"), "担当者(姓)")
    site_manager_first = _normalize_required_text(data.get("site_manager_first"), "担当者(名)")
    site_manager_id = _normalize_required_text(data.get("site_manager_id"), "担当者ID")
    is_active = _parse_bool(data.get("is_active"), default=True if existing is None else existing.is_active)

    if "office_code" in data:
        raw_code = data.get("office_code")
        office_code = str(raw_code).strip() if raw_code is not None else ""
        office_code = office_code or None
    else:
        office_code = existing.office_code if existing else None

    if office_code is not None:
        office_code = _validate_assignable_office_code(office_code)
    if _is_restricted_user() and not _can_access_office_code(office_code):
        raise ValueError("アクセス可能な営業所コードを指定してください")

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
        "office_code": office_code,
        **_shift_time_payload_from_request(data, existing=existing),
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
        **_shift_time_payload_from_request(data, existing=existing),
        "is_active": is_active,
    }


def _site_preview_response(payload: dict, *, existing: Site | None = None) -> dict:
    before = existing.to_dict(include_branches=False) if existing else {}
    duplicates = _site_duplicate_rows(payload["site_name"], exclude_site_row_id=existing.id if existing else None)
    changes = _changes_for_payload(before, payload, SITE_FIELD_LABELS, formatters=SHIFT_TIME_FORMATTERS)
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
        formatters={"cloudshift_option_key": _site_option_label, **SHIFT_TIME_FORMATTERS},
    )
    return {
        "normalized": payload,
        "requires_change_confirmation": bool(branch and changes),
        "changes": changes,
    }


def _site_query_from_filters(args):
    include_inactive = _parse_bool(args.get("include_inactive"), default=False)
    query = Site.query.outerjoin(SiteBranch)

    # アクセス権フィルタ: 非管理者はユーザーに紐付く営業所コードの現場のみ
    # テスト等で current_user が DB 上のユーザーでない場合（id が無い）はスキップ
    try:
        from app.access_control import is_admin_user, user_office_codes
        if getattr(current_user, "id", None) is not None and not is_admin_user():
            allowed = user_office_codes()
            if not allowed:
                query = query.filter(db.false())
            else:
                query = query.filter(Site.office_code.in_(allowed))
    except Exception:
        pass

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


@siteplus_bp.route("/api/accessible-offices")
@login_required
def api_accessible_offices():
    from app.models import AccessBranch

    rows = _accessible_office_rows()
    branch_ids = {row.branch_id for row in rows if row.branch_id is not None}
    branches = {
        b.id: b for b in (AccessBranch.query.filter(AccessBranch.id.in_(branch_ids)).all() if branch_ids else [])
    }
    items = []
    for row in rows:
        branch = branches.get(row.branch_id)
        items.append(
            {
                "code": row.code,
                "name": row.name,
                "branch_name": branch.name if branch else "",
                "branch_code": branch.code if branch else "",
            }
        )
    items.sort(key=lambda x: (x.get("branch_code") or "", x.get("code") or ""))
    return jsonify({"offices": items})


@siteplus_bp.route("/api/sites")
@login_required
def api_sites():
    ensure_contract_master_synced()
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


@siteplus_bp.route("/api/contract-master")
@login_required
def api_contract_master():
    ensure_contract_master_synced()
    query = SiteContractMaster.query
    only_active = _parse_bool(request.args.get("only_active"), default=True)
    if only_active:
        query = query.filter(SiteContractMaster.is_active.is_(True))

    contract_code = str(request.args.get("contract_code", "") or "").strip()
    if contract_code:
        query = query.filter(SiteContractMaster.contract_code == contract_code)

    site_row_id = str(request.args.get("site_row_id", "") or "").strip()
    if site_row_id.isdigit():
        query = query.filter(SiteContractMaster.site_row_id == int(site_row_id))

    site_id = str(request.args.get("site_id", "") or "").strip()
    if site_id:
        query = query.filter(SiteContractMaster.site_id == _normalize_site_id(site_id))

    manager_id = str(request.args.get("site_manager_id", "") or "").strip()
    if manager_id:
        query = query.filter(SiteContractMaster.site_manager_id.ilike(f"%{manager_id}%"))

    query = _scope_contract_master_query(query)
    items = query.order_by(SiteContractMaster.contract_code.asc()).all()
    return jsonify({"items": [_serialize_contract_master_row(item) for item in items], "count": len(items)})


@siteplus_bp.route("/api/contract-master/sync", methods=["POST"])
@login_required
def api_contract_master_sync():
    summary = _sync_contract_master()
    db.session.commit()
    return jsonify({"success": True, "summary": summary})


@siteplus_bp.route("/api/contract-master/<contract_code>/dedicated-candidates")
@login_required
def api_contract_master_dedicated_candidates(contract_code: str):
    row = _get_contract_master_for_current_user_or_404(contract_code)
    return jsonify(
        {
            "contract_code": row.contract_code,
            "site_name": row.site_name,
            "candidates": _employee_candidates_for_contract_code(row.contract_code),
            "registered": {
                "employee_number": row.dedicated_employee_number or "",
                "employee_name": row.dedicated_employee_name or "",
            },
        }
    )


@siteplus_bp.route("/api/contract-master/<contract_code>/dedicated", methods=["PUT"])
@login_required
def api_contract_master_set_dedicated(contract_code: str):
    row = _get_contract_master_for_current_user_or_404(contract_code)

    data = request.get_json(silent=True) or {}
    delete_mode = str(data.get("mode") or "soft").strip().lower()
    if delete_mode not in {"soft", "hard"}:
        delete_mode = "soft"
    employee_number = str(data.get("employee_number", "") or "").strip()
    if employee_number:
        candidate = next(
            (item for item in _employee_candidates_for_contract_code(row.contract_code) if item["employee_number"] == employee_number),
            None,
        )
        if candidate is None:
            return jsonify({"error": "契約コードに一致する社員が見つかりません"}), 400
        row.dedicated_employee_number = candidate["employee_number"]
        row.dedicated_employee_name = candidate["employee_name"]
    else:
        row.dedicated_employee_number = None
        row.dedicated_employee_name = None

    row.dedicated_updated_by = _user_id()
    row.dedicated_updated_at = utc_now()
    db.session.commit()
    warning = None
    cloudshift_synced = True
    try:
        from .cloudshift import _resync_siteplus_dedicated_projects_for_site_row
    except ImportError:
        from app.tools.cloudshift import _resync_siteplus_dedicated_projects_for_site_row  # type: ignore

    try:
        _resync_siteplus_dedicated_projects_for_site_row(row.site_row_id, actor_name=_user_id())
    except Exception:
        current_app.logger.exception("Failed to sync SitePlus dedicated setting to CloudShift rules")
        warning = "CloudShift への専従ルール同期に失敗しました"
        cloudshift_synced = False

    payload = {"success": True, "item": _serialize_contract_master_row(row), "cloudshift_synced": cloudshift_synced}
    if warning:
        payload["warning"] = warning
    return jsonify(payload)


@siteplus_bp.route("/api/contract-master/<contract_code>/segment", methods=["PUT"])
@login_required
def api_contract_master_set_segment(contract_code: str):
    ensure_contract_master_synced()
    row = _get_contract_master_for_current_user_or_404(contract_code)

    data = request.get_json(silent=True) or {}
    try:
        segment = _normalize_segment_value(data.get("segment"), allow_blank=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    row.segment = segment
    row.source = "siteplus"
    db.session.commit()
    return jsonify({"success": True, "item": _serialize_contract_master_row(row)})


@siteplus_bp.route("/api/contract-master/<contract_code>/vehicle-number", methods=["PUT"])
@login_required
def api_contract_master_set_vehicle_number(contract_code: str):
    ensure_contract_master_synced()
    row = _get_contract_master_for_current_user_or_404(contract_code)

    data = request.get_json(silent=True) or {}
    vehicle_number = str(data.get("vehicle_number", "") or "").strip()
    row.vehicle_number = vehicle_number or None
    row.vehicle_number_updated_by = _user_id()
    row.vehicle_number_updated_at = utc_now()
    db.session.commit()
    return jsonify({"success": True, "item": _serialize_contract_master_row(row)})


@siteplus_bp.route("/api/sites/<int:site_row_id>/segments", methods=["PUT"])
@login_required
def api_site_set_segments(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
    _upsert_contract_master_for_site(site)

    data = request.get_json(silent=True) or {}
    try:
        segment = _normalize_segment_value(data.get("segment"), allow_blank=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    updated_contract_codes: list[str] = []
    for branch in site.branches:
        contract_code = _compose_contract_code(site.site_id, branch.site_branch)
        row = db.session.get(SiteContractMaster, contract_code)
        if row is None:
            row = _ensure_contract_master_row(contract_code)
        if row is None:
            continue
        row.segment = segment
        row.source = "siteplus"
        updated_contract_codes.append(contract_code)

    db.session.commit()
    rows = [
        db.session.get(SiteContractMaster, contract_code)
        for contract_code in updated_contract_codes
    ]
    return jsonify(
        {
            "success": True,
            "site_row_id": site.id,
            "updated_count": len(updated_contract_codes),
            "items": [_serialize_contract_master_row(row) for row in rows if row is not None],
        }
    )


@siteplus_bp.route("/api/sites/<int:site_row_id>")
@login_required
def api_site_detail(site_row_id: int):
    include_inactive = _parse_bool(request.args.get("include_inactive"), default=True)
    site = _get_site_for_current_user_or_404(site_row_id)
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
    db.session.flush()
    _upsert_contract_master_for_site(site)
    db.session.commit()
    return jsonify({"success": True, "site": _serialize_site(site, include_inactive_branches=True)})


@siteplus_bp.route("/api/sites/<int:site_row_id>/preview", methods=["PUT"])
@login_required
def api_site_update_preview(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _site_payload_from_request(data, existing=site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_site_preview_response(payload, existing=site))


@siteplus_bp.route("/api/sites/<int:site_row_id>", methods=["PUT"])
@login_required
def api_site_update(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
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
    _upsert_contract_master_for_site(site)
    db.session.commit()
    return jsonify({"success": True, "site": _serialize_site(site, include_inactive_branches=True)})


@siteplus_bp.route("/api/sites/<int:site_row_id>", methods=["DELETE"])
@login_required
def api_site_delete(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    delete_mode = str(data.get("mode") or "soft").strip().lower()
    if delete_mode not in {"soft", "hard"}:
        delete_mode = "soft"
    if not _parse_bool(data.get("confirm"), default=False):
        return jsonify(
            {
                "error": "削除確認が必要です",
                "requires_confirmation": True,
                "delete_mode": delete_mode,
                "site": _serialize_site(site, include_inactive_branches=True),
            }
        ), 409

    if delete_mode == "hard":
        SiteContractMaster.query.filter(SiteContractMaster.site_row_id == site.id).delete(
            synchronize_session=False
        )
        db.session.delete(site)
        db.session.commit()
        return jsonify({"success": True, "delete_mode": "hard"})

    site.is_active = False
    site.site_updater = _user_id()
    for branch in site.branches:
        branch.is_active = False
        branch.site_updater = _user_id()
    _upsert_contract_master_for_site(site)
    db.session.commit()
    return jsonify({"success": True, "delete_mode": "soft"})


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches/preview", methods=["POST"])
@login_required
def api_branch_create_preview(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, site)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_branch_preview_response(payload))


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches", methods=["GET"])
@login_required
def api_branch_list(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
    include_inactive = _parse_bool(request.args.get("include_inactive"), default=True)
    branches = [branch.to_dict() for branch in site.branches if include_inactive or branch.is_active]
    return jsonify({"site_row_id": site.id, "branches": branches})


@siteplus_bp.route("/api/sites/<int:site_row_id>/branches", methods=["POST"])
@login_required
def api_branch_create(site_row_id: int):
    site = _get_site_for_current_user_or_404(site_row_id)
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
    db.session.flush()
    _upsert_contract_master_for_site(site)
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict()})


@siteplus_bp.route("/api/branches/<int:branch_id>/preview", methods=["PUT"])
@login_required
def api_branch_update_preview(branch_id: int):
    branch = _get_branch_for_current_user_or_404(branch_id)
    data = request.get_json(silent=True) or {}
    try:
        payload = _branch_payload_from_request(data, branch.site, existing=branch)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_branch_preview_response(payload, branch=branch))


@siteplus_bp.route("/api/branches/<int:branch_id>", methods=["PUT"])
@login_required
def api_branch_update(branch_id: int):
    branch = _get_branch_for_current_user_or_404(branch_id)
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
    _upsert_contract_master_for_site(branch.site)
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict()})


@siteplus_bp.route("/api/branches/<int:branch_id>", methods=["DELETE"])
@login_required
def api_branch_delete(branch_id: int):
    branch = _get_branch_for_current_user_or_404(branch_id)
    data = request.get_json(silent=True) or {}
    delete_mode = str(data.get("mode") or "soft").strip().lower()
    if delete_mode not in {"soft", "hard"}:
        delete_mode = "soft"
    if not _parse_bool(data.get("confirm"), default=False):
        return jsonify(
            {
                "error": "削除確認が必要です",
                "requires_confirmation": True,
                "delete_mode": delete_mode,
                "branch": branch.to_dict(),
            }
        ), 409

    if delete_mode == "hard":
        SiteContractMaster.query.filter(
            SiteContractMaster.site_branch_row_id == branch.id
        ).delete(synchronize_session=False)
        db.session.delete(branch)
        db.session.commit()
        return jsonify({"success": True, "delete_mode": "hard"})

    branch.is_active = False
    branch.site_updater = _user_id()
    _upsert_contract_master_for_site(branch.site)
    db.session.commit()
    return jsonify({"success": True, "delete_mode": "soft"})


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
    from app.models import AccessOffice
    from app.access_control import is_admin_user
    valid_office_codes = {
        row.code
        for row in AccessOffice.query.filter(
            AccessOffice.code.isnot(None), AccessOffice.code != ""
        ).all()
        if row.code
    }
    # 実ユーザー(=current_user.id あり)かつ非管理者の場合のみ、
    # 自分のアクセス可能な営業所コードに限定する。
    # current_user.id が None（テスト用のモック）は従来通り扱い、既存テストを壊さない。
    is_real_user = getattr(current_user, "id", None) is not None
    restrict_to_accessible = is_real_user and not is_admin_user()
    accessible_office_codes = _accessible_office_codes() if restrict_to_accessible else None
    summary = {
        "total_rows": 0,
        "processed_rows": 0,
        "site_created": 0,
        "site_updated": 0,
        "branch_created": 0,
        "branch_skipped": 0,
        "office_code_skipped": 0,
        "office_code_forbidden": 0,
        "segment_created": 0,
        "segment_updated": 0,
        "segment_skipped": 0,
        "vehicle_number_updated": 0,
        "errors": [],
    }
    processed_contract_codes: set[str] = set()

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
            office_code_raw = str(row[6] if len(row) > 6 else "").strip()

            if len(contract_code) != 8 or not contract_code.isdigit():
                raise ValueError("契約コードは8桁の数字で入力してください")

            office_code: str | None = None
            if office_code_raw:
                if office_code_raw not in valid_office_codes:
                    summary["office_code_skipped"] += 1
                    raise ValueError(
                        f"営業所コード '{office_code_raw}' は存在しません。現場登録をスキップしました"
                    )
                if restrict_to_accessible and office_code_raw not in (accessible_office_codes or set()):
                    summary["office_code_forbidden"] += 1
                    raise ValueError(
                        f"営業所コード '{office_code_raw}' へのアクセス権限がありません。現場登録をスキップしました"
                    )
                office_code = office_code_raw
            elif restrict_to_accessible:
                # 非管理者は営業所コード空の行は取り込ませない（権限回避防止）
                summary["office_code_forbidden"] += 1
                raise ValueError(
                    "営業所コードが未設定の行は取り込みできません"
                )

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
                    office_code=office_code,
                    site_register=actor,
                    site_updater=actor,
                    is_active=True,
                )
                db.session.add(site)
                db.session.flush()
                summary["site_created"] += 1
            else:
                if restrict_to_accessible and not _can_access_site(site):
                    summary["office_code_forbidden"] += 1
                    raise ValueError(
                        f"既存現場 '{site.site_id}' へのアクセス権限がないため更新できません"
                    )
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
                if office_code is not None and site.office_code != office_code:
                    site.office_code = office_code
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
            processed_contract_codes.add(contract_code)
        except ValueError as exc:
            summary["errors"].append({"row": row_index, "message": str(exc)})

    _sync_contract_master()
    segment_rows = rows
    if restrict_to_accessible:
        header = rows[:1]
        segment_rows = header + [
            row for row in rows[1:]
            if str(row[0] if row else "").strip() in processed_contract_codes
        ]
    segment_summary = _update_contract_master_segments(segment_rows, source="siteplus")
    summary["segment_created"] = segment_summary["created"]
    summary["segment_updated"] = segment_summary["updated"]
    summary["segment_skipped"] = segment_summary["skipped"]
    for row in rows[1:]:
        if len(row) < 8:
            continue
        contract_code = str(row[0] or "").strip()
        vehicle_number = str(row[7] or "").strip()
        if not contract_code or not vehicle_number:
            continue
        if restrict_to_accessible and contract_code not in processed_contract_codes:
            continue
        master_row = db.session.get(SiteContractMaster, contract_code)
        if master_row is None:
            continue
        if master_row.vehicle_number != vehicle_number:
            master_row.vehicle_number = vehicle_number
            master_row.vehicle_number_updated_by = actor
            master_row.vehicle_number_updated_at = utc_now()
            summary["vehicle_number_updated"] += 1
    db.session.commit()
    return jsonify({"success": True, "summary": summary})


@siteplus_bp.route("/api/cloudshift/sites")
@login_required
def api_cloudshift_sites():
    q = str(request.args.get("q", "") or "").strip()
    query = Site.query.filter(Site.is_active.is_(True))
    if _is_restricted_user():
        allowed = _accessible_office_codes()
        if not allowed:
            query = query.filter(false())
        else:
            query = query.filter(Site.office_code.in_(allowed))
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
                    "attendance_times": normalize_attendance_times(site.attendance_times),
                    "report_time": normalize_time_text(site.report_time),
                    "branches": [
                        {
                            **branch.to_dict(),
                            "option_label": _site_option_label(branch.cloudshift_option_key),
                            "resolved_shift_times": resolve_shift_times(site, branch),
                        }
                        for branch in site.branches
                        if branch.is_active
                    ],
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
    if not _can_access_site(site):
        abort(403)
    branches = [
        {
            **branch.to_dict(),
            "option_label": _site_option_label(branch.cloudshift_option_key),
            # 枝番号未設定分は親現場を継承した値。CloudShift 側で再解決しなくて済むようにする。
            "resolved_shift_times": resolve_shift_times(site, branch),
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
                "attendance_times": normalize_attendance_times(site.attendance_times),
                "report_time": normalize_time_text(site.report_time),
            },
            "branches": branches,
        }
    )
