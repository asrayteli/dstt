from __future__ import annotations

import io
import hashlib
import json
import os
import secrets
import tempfile
import time
from calendar import monthrange
from contextlib import contextmanager
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from openpyxl import Workbook
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app.access_control import user_office_ids
from app.models import (
    AccessOffice,
    CloudShiftHistory,
    CloudShiftMonth,
    CloudShiftProject,
    Employee,
    Site,
    SiteBranch,
    SiteContractMaster,
    User,
    db,
)

try:
    from .shiftersync_format import (
        LEAVE_OPTION_MAPPINGS,
        SHIFT_OPTION_MAPPINGS,
        entry_display_text,
        normalize_entries_for_month,
        parse_csv_text,
        parse_entry_value,
        serialize_csv_text,
        serialize_entry_rows,
    )
    from .shiftersync_check import compare_shift_payloads, is_duplicate_by_rules
    from .japan_holidays import JAPAN_HOLIDAYS
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        LEAVE_OPTION_MAPPINGS,
        SHIFT_OPTION_MAPPINGS,
        entry_display_text,
        normalize_entries_for_month,
        parse_csv_text,
        parse_entry_value,
        serialize_csv_text,
        serialize_entry_rows,
    )
    from app.tools.shiftersync_check import compare_shift_payloads, is_duplicate_by_rules  # type: ignore
    from app.tools.japan_holidays import JAPAN_HOLIDAYS  # type: ignore


cloudshift_bp = Blueprint("cloudshift", __name__, url_prefix="/tools/shiftersync/cloudshift")

LOCK_TIMEOUT_SECONDS = 8.0
LOCK_POLL_SECONDS = 0.05
MAX_REVISION_SNAPSHOTS = 12
OPTION_LABELS = {**SHIFT_OPTION_MAPPINGS, **LEAVE_OPTION_MAPPINGS}
SHIFT_TIME_OPTION_KEYS = {"A", "P", "E", "L", "TEMP"}
VEHICLE_OPTION_KEYS = {"M", "C", "O", "W", "V", "N1", "N2", "N3", "N4", "N5"}
ASSIST_ROLE_LABELS = {
    "normal": "通常",
    "dedicated": "専従者",
    "backup": "代務者",
}
ASSIST_ROLE_SCORES = {
    "normal": 100,
    "dedicated": 300,
    "backup": 200,
}
ASSIST_MATCH_SCOPE_LABELS = {
    "exact": "曜日・オプション一致",
    "weekday": "曜日一致",
    "other_weekday": "曜日不一致（実績のみ）",
}
ASSIST_WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]
ASSIST_WEEKDAYLESS_LABEL = "曜日なし"
ASSIST_OPTIONLESS_LABEL = "オプションなし"
ASSIST_CUSTOM_POINTS_MIN = -1000
ASSIST_CUSTOM_POINTS_MAX = 1000
ASSIST_PREFERRED_WEEKDAY_BONUS = 30
ASSIST_OPTION_APTITUDE_MAX_BONUS = 25
ASSIST_OPTION_APTITUDE_MID_BONUS = 15
ASSIST_OPTION_APTITUDE_MIN_BONUS = 5
ASSIST_OPTION_APTITUDE_ZERO_PENALTY = -10
SITEPLUS_DEDICATED_RULE_SOURCE = "siteplus_dedicated"
PERSON_ASSIST_SITE_LABELS = {
    "experienced": "経験済現場",
    "training": "研修要現場",
}
PERSON_ASSIST_AUTO_SOURCE = "person_experience"
PERSON_ASSIST_EXPERIENCE_KIND = "experienced"
PERSON_ASSIST_TRAINING_KIND = "training"
PERSON_ASSIST_COLLECTION_KEYS = {
    PERSON_ASSIST_EXPERIENCE_KIND: "experienced_sites",
    PERSON_ASSIST_TRAINING_KIND: "training_sites",
}
PERSON_ASSIST_KIND_LABELS = {
    PERSON_ASSIST_EXPERIENCE_KIND: "経験済現場",
    PERSON_ASSIST_TRAINING_KIND: "研修要現場",
}
PERSON_ASSIST_AUTO_ROLE_TYPE = "backup"
SHIFT_SYNC_SCENE_SOURCE = "scene_shift"
SHIFT_SYNC_PERSON_SOURCE = "person_shift"
SHIFT_SYNC_SOURCE_TYPES = {SHIFT_SYNC_SCENE_SOURCE, SHIFT_SYNC_PERSON_SOURCE}


class CloudShiftError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@cloudshift_bp.errorhandler(CloudShiftError)
def _handle_cloudshift_error(error: CloudShiftError):
    return jsonify({"error": error.message}), error.status_code


@cloudshift_bp.errorhandler(HTTPException)
def _handle_http_error(error: HTTPException):
    if request.path.startswith("/tools/shiftersync/cloudshift/api/"):
        return jsonify({"error": error.description or error.name}), error.code or 500
    return error


def _utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _runtime_root() -> Path:
    configured = current_app.config.get("CLOUDSHIFT_DATA_DIR") or os.environ.get("CLOUDSHIFT_DATA_DIR")
    base = Path(configured) if configured else Path(current_app.instance_path) / "cloudshift"
    shifts = base / "shifts"
    histories = base / "histories"
    locks = base / "locks"
    for path in (base, shifts, histories, locks):
        path.mkdir(parents=True, exist_ok=True)
    return base


def _shifts_dir() -> Path:
    return _runtime_root() / "shifts"


def _histories_dir() -> Path:
    return _runtime_root() / "histories"


def _locks_dir() -> Path:
    return _runtime_root() / "locks"


def _project_path(project_id: str) -> Path:
    return _shifts_dir() / f"{project_id}.json"


def _history_path(project_id: str) -> Path:
    return _histories_dir() / f"{project_id}.jsonl"


def _safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _safe_write_json(path: Path, payload: dict[str, Any]) -> None:
    _safe_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


@contextmanager
def _project_lock(project_id: str):
    lock_path = _locks_dir() / f"{project_id}.lock"
    start = time.monotonic()
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            if time.monotonic() - start > LOCK_TIMEOUT_SECONDS:
                raise CloudShiftError("別の更新処理が進行中です。少し待ってから再度お試しください", 423)
            time.sleep(LOCK_POLL_SECONDS)
    try:
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        yield
    finally:
        os.close(fd)
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _parse_month_key(month_key: str) -> tuple[int, int]:
    year_text, month_text = month_key.split("-", 1)
    return int(year_text), int(month_text)


def _sort_month_keys(month_keys: list[str]) -> list[str]:
    return sorted(month_keys, key=lambda value: _parse_month_key(value))


def _project_id() -> str:
    return secrets.token_hex(12)


def _share_token() -> str:
    return secrets.token_urlsafe(32)


def _user_id() -> str:
    if not current_user.is_authenticated:
        raise CloudShiftError("ログインが必要です", 401)
    return str(current_user.username)


def _user_label() -> str:
    if not current_user.is_authenticated:
        return "guest"
    name = getattr(current_user, "name", None)
    if name and name != "unknown":
        return str(name)
    return str(getattr(current_user, "username", "user"))


def _sanitize_title(value: str) -> str:
    title = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not title:
        raise CloudShiftError("タイトルは必須です", 400)
    return title[:120]


def _sanitize_mode(value: str) -> str:
    mode = (value or "").strip().lower()
    if mode not in {"scene", "person"}:
        raise CloudShiftError("mode は scene または person のみ対応です", 400)
    return mode


def _sanitize_employee_number(value: Any) -> str:
    return str(value or "").strip()


def _sanitize_site_row_id(value: Any) -> int | None:
    if value in (None, "", False):
        return None
    try:
        site_row_id = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("現場の指定が不正です", 400) from exc
    if site_row_id <= 0:
        raise CloudShiftError("現場の指定が不正です", 400)
    return site_row_id


def _coerce_site_row_id(value: Any) -> int | None:
    try:
        return _sanitize_site_row_id(value)
    except CloudShiftError:
        return None


def _load_site_reference(site_row_id: int | None, *, require_active: bool) -> dict[str, Any] | None:
    if not site_row_id:
        return None
    site = db.session.get(Site, site_row_id)
    if not site:
        raise CloudShiftError("指定された現場が見つかりません", 400)
    if require_active and not site.is_active:
        raise CloudShiftError("無効化された現場は選択できません", 400)
    return {
        "site_row_id": site.id,
        "site_id": site.site_id,
        "site_name": site.site_name,
        "is_active": bool(site.is_active),
        "branch_count": len(site.branches),
        "active_branch_count": len([branch for branch in site.branches if branch.is_active]),
    }


def _linked_site_snapshot_payload(site_row_id: Any, site_id: Any, site_name: Any) -> dict[str, Any]:
    normalized_site_row_id = _coerce_site_row_id(site_row_id)
    snapshot = {
        "site_row_id": normalized_site_row_id,
        "site_id": str(site_id or "").strip(),
        "site_name": str(site_name or "").strip(),
    }
    if not normalized_site_row_id:
        return {
            **snapshot,
            "is_linked": False,
            "is_active": None,
            "is_missing": False,
            "branch_count": 0,
            "active_branch_count": 0,
        }
    try:
        linked = _load_site_reference(int(normalized_site_row_id), require_active=False)
    except CloudShiftError:
        linked = None
    if not linked:
        return {
            **snapshot,
            "is_linked": True,
            "is_active": None,
            "is_missing": True,
            "branch_count": 0,
            "active_branch_count": 0,
        }
    return {
        **linked,
        "is_linked": True,
        "is_missing": False,
    }


def _project_site_payload(project: dict[str, Any]) -> dict[str, Any]:
    return _linked_site_snapshot_payload(
        project.get("site_row_id"),
        project.get("site_id"),
        project.get("site_name"),
    )


def _project_registered_dedicated_candidates(project: dict[str, Any]) -> list[dict[str, Any]]:
    if str(project.get("mode") or "") != "scene":
        return []
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    if not site_row_id:
        return []
    if "sqlalchemy" not in current_app.extensions:
        return []

    try:
        rows = (
            SiteContractMaster.query
            .filter(
                SiteContractMaster.site_row_id == int(site_row_id),
                SiteContractMaster.is_active.is_(True),
                SiteContractMaster.dedicated_employee_number.isnot(None),
            )
            .order_by(SiteContractMaster.contract_code.asc())
            .all()
        )
    except Exception:
        return []

    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        employee_number = str(row.dedicated_employee_number or "").strip()
        employee_name = str(row.dedicated_employee_name or "").strip()
        if not employee_number and not employee_name:
            continue
        key = employee_number or employee_name
        item = candidates.get(key)
        if item is None:
            item = {
                "employee_number": employee_number,
                "name": employee_name or employee_number,
                "contract_codes": [],
                "site_branches": [],
            }
            candidates[key] = item
        contract_code = str(row.contract_code or "").strip()
        if contract_code and contract_code not in item["contract_codes"]:
            item["contract_codes"].append(contract_code)
        site_branch = str(row.site_branch or "").strip()
        if site_branch and site_branch not in item["site_branches"]:
            item["site_branches"].append(site_branch)

    return list(candidates.values())


def _siteplus_dedicated_rows_for_site_row_id(site_row_id: int | None) -> list[SiteContractMaster]:
    normalized_site_row_id = _coerce_site_row_id(site_row_id)
    if not normalized_site_row_id or "sqlalchemy" not in current_app.extensions:
        return []
    try:
        return (
            SiteContractMaster.query
            .filter(SiteContractMaster.site_row_id == int(normalized_site_row_id))
            .order_by(SiteContractMaster.contract_code.asc())
            .all()
        )
    except Exception:
        return []


def _is_siteplus_dedicated_rule(rule: dict[str, Any]) -> bool:
    return str(rule.get("source_type") or "").strip() == SITEPLUS_DEDICATED_RULE_SOURCE


def _siteplus_dedicated_rule_context(row: SiteContractMaster) -> tuple[str, str]:
    branch_label = str(row.site_branch or "").strip() or "-"
    contract_code = str(row.contract_code or "").strip()
    label = f"枝番号 {branch_label}"
    if contract_code:
        label += f" / 契約コード {contract_code}"
    notes = f"現場リストPLUSの専従者登録から自動同期: {label}"
    return label, notes


def _siteplus_dedicated_rule_matches_row(rule: dict[str, Any], row: SiteContractMaster) -> bool:
    if not _is_siteplus_dedicated_rule(rule):
        return False
    if str(rule.get("source_contract_code") or "").strip() != str(row.contract_code or "").strip():
        return False

    assignments = rule.get("assignments") or []
    if len(assignments) != 1:
        return False
    assignment = assignments[0] if isinstance(assignments[0], dict) else {}
    _, expected_notes = _siteplus_dedicated_rule_context(row)
    return (
        _assist_rule_weekday_value(rule.get("weekday")) is None
        and str(rule.get("shift_key") or "") == ""
        and bool(rule.get("enabled", True))
        and str(rule.get("effective_from") or "") == ""
        and str(rule.get("effective_to") or "") == ""
        and str(rule.get("notes") or "") == expected_notes
        and str(rule.get("source_site_branch") or "").strip() == str(row.site_branch or "").strip()
        and _coerce_site_row_id(rule.get("source_site_row_id")) == _coerce_site_row_id(row.site_row_id)
        and str(assignment.get("candidate_name") or "").strip() == str(row.dedicated_employee_name or "").strip()
        and str(assignment.get("employee_number") or "").strip() == str(row.dedicated_employee_number or "").strip()
        and str(assignment.get("role_type") or "").strip() == "dedicated"
        and int(assignment.get("priority") or 0) == 1
        and int(assignment.get("custom_points") or 0) == 0
    )


def _build_siteplus_dedicated_rule(
    assist: dict[str, Any],
    row: SiteContractMaster,
    *,
    actor_name: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    branch_context, notes = _siteplus_dedicated_rule_context(row)
    rule = _assist_rule_from_payload(
        assist,
        {
            "weekday": None,
            "shift_key": "",
            "enabled": True,
            "notes": notes,
            "assignments": [
                {
                    "candidate_name": str(row.dedicated_employee_name or "").strip(),
                    "employee_number": str(row.dedicated_employee_number or "").strip(),
                    "role_type": "dedicated",
                    "priority": 1,
                    "custom_points": 0,
                }
            ],
        },
        existing=existing,
        actor_name=actor_name,
    )
    rule["source_type"] = SITEPLUS_DEDICATED_RULE_SOURCE
    rule["source_contract_code"] = str(row.contract_code or "").strip()
    rule["source_site_row_id"] = _coerce_site_row_id(row.site_row_id)
    rule["source_site_branch"] = str(row.site_branch or "").strip()
    rule["source_site_branch_row_id"] = int(row.site_branch_row_id) if row.site_branch_row_id is not None else None
    rule["source_label"] = branch_context
    return rule


def _sync_siteplus_dedicated_rules_in_project(
    project: dict[str, Any],
    rows: list[SiteContractMaster],
    *,
    actor_name: str,
) -> list[str]:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    desired_rows = {
        str(row.contract_code or "").strip(): row
        for row in rows
        if (
            row
            and row.is_active
            and str(row.dedicated_employee_number or "").strip()
            and str(row.dedicated_employee_name or "").strip()
        )
    }

    preserved_rules: list[dict[str, Any]] = []
    auto_rules_by_contract: dict[str, list[dict[str, Any]]] = {}
    for rule in assist.get("rules") or []:
        if _is_siteplus_dedicated_rule(rule):
            contract_code = str(rule.get("source_contract_code") or "").strip()
            auto_rules_by_contract.setdefault(contract_code, []).append(rule)
            continue
        preserved_rules.append(rule)

    next_rules = list(preserved_rules)
    changes: list[str] = []
    for contract_code, row in desired_rows.items():
        existing_rules = auto_rules_by_contract.pop(contract_code, [])
        primary = existing_rules[0] if existing_rules else None
        for duplicate in existing_rules[1:]:
            duplicate_branch = str(duplicate.get("source_site_branch") or "").strip() or "-"
            changes.append(f"CloudShift 自動専従ルールの重複を整理: 枝番号 {duplicate_branch}")
        if primary and _siteplus_dedicated_rule_matches_row(primary, row) and len(existing_rules) == 1:
            next_rules.append(primary)
            continue
        next_rules.append(_build_siteplus_dedicated_rule(assist, row, actor_name=actor_name, existing=primary))
        branch_label, _ = _siteplus_dedicated_rule_context(row)
        employee_label = str(row.dedicated_employee_name or "").strip() or str(row.dedicated_employee_number or "").strip()
        if primary:
            changes.append(f"CloudShift 専従ルールを同期更新: {branch_label} / {employee_label}")
        else:
            changes.append(f"CloudShift 専従ルールを同期登録: {branch_label} / {employee_label}")

    for contract_code, stale_rules in auto_rules_by_contract.items():
        for stale in stale_rules:
            branch_label = str(stale.get("source_site_branch") or "").strip() or "-"
            changes.append(
                f"CloudShift 専従ルールを解除: 枝番号 {branch_label}"
                + (f" / 契約コード {contract_code}" if contract_code else "")
            )

    if changes:
        assist["rules"] = next_rules
    return changes


def _backfill_scene_project_from_siteplus_dedicated(
    scene_project: dict[str, Any],
    *,
    actor_name: str,
) -> None:
    _ensure_scene_project(scene_project)
    rows = _siteplus_dedicated_rows_for_site_row_id(_coerce_site_row_id(scene_project.get("site_row_id")))
    changes = _sync_siteplus_dedicated_rules_in_project(scene_project, rows, actor_name=actor_name)
    if not changes:
        return
    _save_project(scene_project)
    _append_history(
        scene_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "auto",
            "action": "siteplus_dedicated_backfill",
            "month_key": None,
            "changes": changes[:100],
        },
    )


def _resync_siteplus_dedicated_projects_for_site_row(
    site_row_id: int | None,
    *,
    actor_name: str,
) -> None:
    normalized_site_row_id = _coerce_site_row_id(site_row_id)
    if not normalized_site_row_id:
        return
    rows = _siteplus_dedicated_rows_for_site_row_id(normalized_site_row_id)
    for target_summary in _iter_stored_projects():
        if not target_summary or target_summary.get("mode") != "scene":
            continue
        if _coerce_site_row_id(target_summary.get("site_row_id")) != normalized_site_row_id:
            continue
        target_project_id = str(target_summary.get("id") or "")
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            if target_project.get("mode") != "scene":
                continue
            changes = _sync_siteplus_dedicated_rules_in_project(target_project, rows, actor_name=actor_name)
            if not changes:
                continue
            _save_project(target_project)
            _append_history(
                target_project["id"],
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": actor_name,
                    "editor_type": "auto",
                    "action": "siteplus_dedicated_sync",
                    "month_key": None,
                    "changes": changes[:100],
                },
            )


def _site_storage_fields(site_ref: dict[str, Any] | None) -> dict[str, Any]:
    if not site_ref:
        return {
            "site_row_id": None,
            "site_id": "",
            "site_name": "",
        }
    return {
        "site_row_id": site_ref["site_row_id"],
        "site_id": site_ref["site_id"],
        "site_name": site_ref["site_name"],
    }


def _format_entry_value(option_key: Any, name: Any) -> str:
    safe_name = str(name or "").strip()
    if not safe_name:
        return ""
    normalized_option = str(option_key or "").strip().upper()
    return f"!{normalized_option}!{safe_name}" if normalized_option else safe_name


def _entry_option_and_name(entry: dict[str, Any]) -> tuple[str, str]:
    option_key, raw_name = parse_entry_value(str((entry or {}).get("value") or ""))
    return str(option_key or "").strip().upper(), str(raw_name or "").strip()


def _entry_is_shift_synced(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    return str(entry.get("sync_source_type") or "").strip() in SHIFT_SYNC_SOURCE_TYPES


def _entry_site_link_fields(entry: dict[str, Any] | None) -> dict[str, str | None]:
    if not isinstance(entry, dict):
        return {"site_row_id": None, "site_id": "", "site_name": ""}
    site_row_id = _coerce_site_row_id(entry.get("site_row_id"))
    site_id = str(entry.get("site_id") or "").strip()
    option_key, raw_name = _entry_option_and_name(entry)
    site_name = str(entry.get("site_name") or "").strip() or raw_name
    if site_row_id is not None:
        return {
            "site_row_id": site_row_id,
            "site_id": site_id,
            "site_name": site_name,
        }
    if site_id or site_name:
        return {
            "site_row_id": None,
            "site_id": site_id,
            "site_name": site_name,
        }
    return {"site_row_id": None, "site_id": "", "site_name": ""}


def _normalized_person_title(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).casefold()


def _sync_entry_id(*parts: Any) -> str:
    digest = hashlib.sha1("::".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()
    return f"sync_{digest[:16]}"


def _active_scene_branches_for_project(project: dict[str, Any]) -> list[SiteBranch]:
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    if not site_row_id:
        return []
    site = db.session.get(Site, int(site_row_id))
    if not site:
        return []
    return [branch for branch in site.branches if branch.is_active]


def _scene_branch_fields_for_option(project: dict[str, Any], option_key: Any) -> dict[str, str]:
    normalized_option = str(option_key or "").strip().upper()
    if not normalized_option:
        return {"site_branch_row_id": "", "site_branch": ""}
    matched = [
        branch for branch in _active_scene_branches_for_project(project)
        if str(branch.cloudshift_option_key or "").strip().upper() == normalized_option
    ]
    if len(matched) != 1:
        return {"site_branch_row_id": "", "site_branch": ""}
    return {
        "site_branch_row_id": str(int(matched[0].id)),
        "site_branch": str(matched[0].site_branch or "").strip(),
    }


def _scene_entry_with_siteplus_defaults(project: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry or {})
    option_key, name = _entry_option_and_name(normalized)
    branch_row_id = _coerce_site_row_id(normalized.get("site_branch_row_id"))
    if branch_row_id:
        branch = db.session.get(SiteBranch, int(branch_row_id))
        if branch and branch.is_active:
            branch_option = str(branch.cloudshift_option_key or "").strip().upper()
            if branch_option in VEHICLE_OPTION_KEYS:
                normalized["value"] = _format_entry_value(branch_option, name)
                normalized["site_branch_row_id"] = str(int(branch.id))
                normalized["site_branch"] = str(branch.site_branch or "").strip()
                return normalized
    active_branches = _active_scene_branches_for_project(project)
    if not active_branches:
        return normalized
    if not branch_row_id and option_key:
        matched = [
            branch for branch in active_branches
            if str(branch.cloudshift_option_key or "").strip().upper() == option_key
        ]
        if len(matched) == 1:
            normalized["site_branch_row_id"] = str(int(matched[0].id))
            normalized["site_branch"] = str(matched[0].site_branch or "").strip()
            return normalized
    if not branch_row_id and not option_key and len(active_branches) == 1:
        branch_option = str(active_branches[0].cloudshift_option_key or "").strip().upper()
        if branch_option in VEHICLE_OPTION_KEYS:
            normalized["value"] = _format_entry_value(branch_option, name)
            normalized["site_branch_row_id"] = str(int(active_branches[0].id))
            normalized["site_branch"] = str(active_branches[0].site_branch or "").strip()
    return normalized


def _normalize_person_entry_site_link(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry or {})
    _, name = _entry_option_and_name(normalized)
    site_link = _entry_site_link_fields(normalized)
    stored_site_name = str(site_link.get("site_name") or "").strip()
    if stored_site_name and _normalized_site_title(stored_site_name) != _normalized_site_title(name):
        normalized["site_row_id"] = ""
        normalized["site_id"] = ""
        normalized["site_name"] = ""
        return normalized
    if site_link.get("site_row_id") or site_link.get("site_id"):
        normalized["site_row_id"] = str(site_link.get("site_row_id") or "")
        normalized["site_id"] = str(site_link.get("site_id") or "")
        normalized["site_name"] = stored_site_name or name
    return normalized


def _prepared_local_entries_for_month(
    project: dict[str, Any],
    current_month: dict[str, Any],
    incoming_entries: Any,
    *,
    year: int,
    month: int,
) -> dict[str, list[dict[str, Any]]]:
    normalized_incoming = _normalize_entries(incoming_entries, year, month)
    normalized_current = _normalize_entries(current_month.get("entries_per_day"), year, month)
    combined = _empty_entries_for_month(year, month)
    for day_key, entries in combined.items():
        local_entries: list[dict[str, Any]] = []
        for entry in normalized_incoming.get(day_key, []):
            if _entry_is_shift_synced(entry):
                continue
            next_entry = dict(entry)
            if str(project.get("mode") or "") == "scene":
                next_entry = _scene_entry_with_siteplus_defaults(project, next_entry)
            elif str(project.get("mode") or "") == "person":
                next_entry = _normalize_person_entry_site_link(next_entry)
            local_entries.append(next_entry)
        synced_entries = [dict(entry) for entry in normalized_current.get(day_key, []) if _entry_is_shift_synced(entry)]
        combined[day_key] = local_entries + synced_entries
    return combined
def _sanitize_capacity(raw: Any) -> tuple[bool, int]:
    if raw in (None, "", False):
        return False, 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return False, 0
    if value <= 0:
        return False, 0
    return True, min(value, 99)


def _validate_year_month(year: Any, month: Any) -> tuple[int, int]:
    try:
        year_value = int(year)
        month_value = int(month)
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("年と月が不正です", 400) from exc

    if year_value < 1900 or year_value > 2100:
        raise CloudShiftError("年は 1900 から 2100 の範囲で指定してください", 400)
    if month_value < 1 or month_value > 12:
        raise CloudShiftError("月は 1 から 12 の範囲で指定してください", 400)
    return year_value, month_value


def _empty_entries_for_month(year: int, month: int) -> dict[str, list[dict[str, str]]]:
    return {str(day): [] for day in range(1, monthrange(year, month)[1] + 1)}


def _normalize_entries(entries: Any, year: int, month: int) -> dict[str, list[dict[str, str]]]:
    return normalize_entries_for_month(entries, year, month)


def _build_month_payload(
    year: int,
    month: int,
    capacity_enabled: bool,
    required_capacity: int,
    entries: Any,
    *,
    revision: int = 1,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    timestamp = _utcnow_iso()
    return {
        "year": year,
        "month": month,
        "capacity_enabled": bool(capacity_enabled and required_capacity > 0),
        "required_capacity": required_capacity if capacity_enabled and required_capacity > 0 else 0,
        "entries_per_day": _normalize_entries(entries, year, month),
        "draft_entries_per_day": _normalize_entries(entries, year, month),
        "revision": revision,
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision_snapshots": {},
    }


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    month_keys = _sort_month_keys(list((project.get("months") or {}).keys()))
    summary = {
        "id": project["id"],
        "title": project["title"],
        "mode": project["mode"],
        "employee_number": str(project.get("employee_number") or ""),
        "owner_user_id": project["owner_user_id"],
        "month_keys": month_keys,
        "month_count": len(month_keys),
        "latest_month_key": month_keys[-1] if month_keys else None,
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "site": _project_site_payload(project),
    }
    share_status = _share_status_for_current_user(project)
    if share_status:
        summary["share"] = share_status
        summary["access_role"] = share_status["role"]
    else:
        summary["access_role"] = (
            "owner"
            if current_user.is_authenticated and project.get("owner_user_id") == _user_id()
            else "none"
        )
    return summary


def _project_public_urls(project: dict[str, Any]) -> dict[str, str]:
    return {
        "view_url": url_for("cloudshift.public_view", token=project["view_token"], _external=True),
        "edit_url": url_for("cloudshift.public_edit", token=project["edit_token"], _external=True),
    }


def _client_month_payload(month_data: dict[str, Any] | None, *, include_draft: bool = False) -> dict[str, Any] | None:
    if not month_data:
        return None
    payload = dict(month_data)
    payload.pop("revision_snapshots", None)
    if not include_draft:
        payload.pop("draft_entries_per_day", None)
    return payload


_PROJECT_STORAGE_KEYS = {
    "id",
    "owner_user_id",
    "title",
    "mode",
    "employee_number",
    "site_row_id",
    "site_id",
    "site_name",
    "site_manager_id",
    "site_manager_name",
    "view_token",
    "edit_token",
    "account_shares",
    "assist",
    "created_at",
    "updated_at",
    "months",
}


def _db_project_to_dict(row: CloudShiftProject) -> dict[str, Any]:
    project = {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "title": row.title,
        "mode": row.mode,
        "employee_number": row.employee_number or "",
        "site_row_id": row.site_row_id,
        "site_id": row.site_id or "",
        "site_name": row.site_name or "",
        "site_manager_id": row.site_manager_id or "",
        "site_manager_name": row.site_manager_name or "",
        "view_token": row.view_token,
        "edit_token": row.edit_token,
        "account_shares": row.account_shares or {},
        "assist": row.assist or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "months": {},
    }
    for key, value in (row.extra_data or {}).items():
        if key not in project:
            project[key] = value
    for month_row in row.months:
        month_key = _month_key(month_row.year, month_row.month)
        project["months"][month_key] = {
            "year": month_row.year,
            "month": month_row.month,
            "capacity_enabled": bool(month_row.capacity_enabled),
            "required_capacity": int(month_row.required_capacity or 0),
            "entries_per_day": month_row.entries_per_day or {},
            "draft_entries_per_day": month_row.draft_entries_per_day or {},
            "revision": int(month_row.revision or 1),
            "created_at": month_row.created_at,
            "updated_at": month_row.updated_at,
            "revision_snapshots": month_row.revision_snapshots or {},
        }
    return project


def _db_project_from_id(project_id: str) -> dict[str, Any] | None:
    row = db.session.get(CloudShiftProject, project_id)
    if not row:
        return None
    return _db_project_to_dict(row)


def _upsert_project_to_db(project: dict[str, Any]) -> None:
    row = db.session.get(CloudShiftProject, project["id"])
    if row is None:
        row = CloudShiftProject(id=project["id"])
        db.session.add(row)
    row.owner_user_id = str(project.get("owner_user_id") or "")
    row.title = str(project.get("title") or "")
    row.mode = str(project.get("mode") or "")
    row.employee_number = str(project.get("employee_number") or "")
    row.site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    row.site_id = str(project.get("site_id") or "")
    row.site_name = str(project.get("site_name") or "")
    row.site_manager_id = str(project.get("site_manager_id") or "")
    row.site_manager_name = str(project.get("site_manager_name") or "")
    row.view_token = str(project.get("view_token") or "")
    row.edit_token = str(project.get("edit_token") or "")
    row.account_shares = project.get("account_shares") if isinstance(project.get("account_shares"), dict) else {}
    row.assist = project.get("assist") if isinstance(project.get("assist"), dict) else {}
    row.extra_data = {key: value for key, value in project.items() if key not in _PROJECT_STORAGE_KEYS}
    row.created_at = str(project.get("created_at") or _utcnow_iso())
    row.updated_at = str(project.get("updated_at") or _utcnow_iso())

    for month_row in list(row.months):
        db.session.delete(month_row)
    db.session.flush()

    for month_key, month_data in (project.get("months") or {}).items():
        try:
            year = int(month_data.get("year"))
            month = int(month_data.get("month"))
        except (TypeError, ValueError):
            year, month = _parse_month_key(month_key)
        normalized_entries = _normalize_entries(month_data.get("entries_per_day"), year, month)
        draft_source = (
            month_data.get("draft_entries_per_day")
            if "draft_entries_per_day" in month_data
            else normalized_entries
        )
        normalized_draft_entries = _normalize_entries(draft_source, year, month)
        db.session.add(
            CloudShiftMonth(
                project_id=project["id"],
                year=year,
                month=month,
                capacity_enabled=bool(month_data.get("capacity_enabled")),
                required_capacity=int(month_data.get("required_capacity", 0) or 0),
                entries_per_day=normalized_entries,
                draft_entries_per_day=normalized_draft_entries,
                revision=int(month_data.get("revision", 1) or 1),
                revision_snapshots=month_data.get("revision_snapshots") or {},
                created_at=str(month_data.get("created_at") or _utcnow_iso()),
                updated_at=str(month_data.get("updated_at") or _utcnow_iso()),
            )
        )
    db.session.commit()


def _load_project(project_id: str) -> dict[str, Any]:
    project = _db_project_from_id(project_id)
    if project:
        return project
    project = _load_json(_project_path(project_id))
    if not project:
        abort(404)
    return project


def _save_project(project: dict[str, Any]) -> None:
    project["updated_at"] = _utcnow_iso()
    _upsert_project_to_db(project)


def _append_history(project_id: str, entry: dict[str, Any]) -> None:
    row = db.session.get(CloudShiftProject, project_id)
    if row is not None:
        payload = {
            key: value
            for key, value in entry.items()
            if key not in {"timestamp", "editor_name", "editor_type", "action", "month_key", "changes"}
        }
        db.session.add(
            CloudShiftHistory(
                project_id=project_id,
                timestamp=str(entry.get("timestamp") or _utcnow_iso()),
                editor_name=str(entry.get("editor_name") or ""),
                editor_type=str(entry.get("editor_type") or ""),
                action=str(entry.get("action") or ""),
                month_key=entry.get("month_key"),
                changes=entry.get("changes") if isinstance(entry.get("changes"), list) else [],
                payload=payload,
            )
        )
        db.session.commit()
        return

    history_path = _history_path(project_id)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_history(project_id: str) -> list[dict[str, Any]]:
    row = db.session.get(CloudShiftProject, project_id)
    if row is not None:
        rows = []
        for item in CloudShiftHistory.query.filter_by(project_id=project_id).order_by(CloudShiftHistory.timestamp.desc(), CloudShiftHistory.id.desc()).all():
            payload = dict(item.payload or {})
            payload.update(
                {
                    "timestamp": item.timestamp,
                    "editor_name": item.editor_name,
                    "editor_type": item.editor_type,
                    "action": item.action,
                    "month_key": item.month_key,
                    "changes": item.changes or [],
                }
            )
            rows.append(payload)
        return rows

    history_path = _history_path(project_id)
    if not history_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with history_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return rows


def _iter_stored_projects() -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in CloudShiftProject.query.order_by(CloudShiftProject.updated_at.desc()).all():
        project = _db_project_to_dict(row)
        projects.append(project)
        seen_ids.add(str(project.get("id") or ""))
    for path in _shifts_dir().glob("*.json"):
        project = _load_json(path)
        if not project:
            continue
        project_id = str(project.get("id") or path.stem)
        if project_id in seen_ids:
            continue
        projects.append(project)
        seen_ids.add(project_id)
    return projects


def migrate_cloudshift_json_to_db(*, dry_run: bool = False) -> dict[str, Any]:
    """Import existing JSON-backed CloudShift projects into the configured DB.

    The source JSON and JSONL files are never deleted or modified. Re-running this
    migration updates the DB copy for the same project id.
    """
    result = {"projects_seen": 0, "projects_imported": 0, "histories_imported": 0, "errors": []}
    for path in _shifts_dir().glob("*.json"):
        result["projects_seen"] += 1
        project = _load_json(path)
        if not project:
            result["errors"].append({"path": str(path), "error": "invalid_json"})
            continue
        project_id = str(project.get("id") or path.stem)
        project["id"] = project_id
        if dry_run:
            result["projects_imported"] += 1
            history_path = _history_path(project_id)
            if history_path.exists():
                result["histories_imported"] += sum(1 for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip())
            continue
        try:
            _upsert_project_to_db(project)
            CloudShiftHistory.query.filter_by(project_id=project_id).delete()
            history_path = _history_path(project_id)
            if history_path.exists():
                with history_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        db.session.add(
                            CloudShiftHistory(
                                project_id=project_id,
                                timestamp=str(entry.get("timestamp") or _utcnow_iso()),
                                editor_name=str(entry.get("editor_name") or ""),
                                editor_type=str(entry.get("editor_type") or ""),
                                action=str(entry.get("action") or ""),
                                month_key=entry.get("month_key"),
                                changes=entry.get("changes") if isinstance(entry.get("changes"), list) else [],
                                payload={
                                    key: value
                                    for key, value in entry.items()
                                    if key not in {"timestamp", "editor_name", "editor_type", "action", "month_key", "changes"}
                                },
                            )
                        )
                        result["histories_imported"] += 1
            db.session.commit()
            result["projects_imported"] += 1
        except Exception as exc:
            db.session.rollback()
            result["errors"].append({"path": str(path), "error": str(exc)})
    return result


def _owner_project_or_404(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("owner_user_id") != _user_id():
        abort(404)
    return project


def _current_share_office_ids() -> set[int]:
    return {int(office_id) for office_id in user_office_ids(current_user) if office_id is not None}


def _office_label_map(office_ids: set[int]) -> dict[int, str]:
    if not office_ids:
        return {}
    rows = AccessOffice.query.filter(AccessOffice.id.in_(office_ids)).all()
    labels: dict[int, str] = {}
    for row in rows:
        branch_name = row.branch.name if row.branch else ""
        labels[int(row.id)] = f"{branch_name} / {row.name}" if branch_name else row.name
    return labels


def _employee_label_for_number(employee_number: str) -> str:
    employee = Employee.query.filter_by(employee_number=employee_number).first()
    if employee and employee.employee_name:
        return employee.employee_name
    user = User.query.filter_by(username=employee_number).first()
    if user and user.name and user.name != "unknown":
        return user.name
    return employee_number


def _normalized_account_shares(project: dict[str, Any]) -> dict[str, Any]:
    raw = project.get("account_shares") if isinstance(project.get("account_shares"), dict) else {}
    office = raw.get("office") if isinstance(raw.get("office"), dict) else {}
    employees = raw.get("employees") if isinstance(raw.get("employees"), list) else []

    office_ids: list[int] = []
    for value in office.get("office_ids") or []:
        try:
            office_id = int(value)
        except (TypeError, ValueError):
            continue
        if office_id > 0 and office_id not in office_ids:
            office_ids.append(office_id)

    employee_rows: list[dict[str, str]] = []
    seen_numbers: set[str] = set()
    for item in employees:
        if isinstance(item, dict):
            number = _sanitize_employee_number(item.get("employee_number"))
            label = str(item.get("name") or "").strip()
        else:
            number = _sanitize_employee_number(item)
            label = ""
        if not number or number in seen_numbers:
            continue
        seen_numbers.add(number)
        employee_rows.append({"employee_number": number, "name": label or _employee_label_for_number(number)})

    office_labels = _office_label_map(set(office_ids))
    return {
        "office": {
            "enabled": bool(office.get("enabled")) and bool(office_ids),
            "office_ids": office_ids,
            "offices": [
                {"id": office_id, "label": office_labels.get(office_id, str(office_id))}
                for office_id in office_ids
            ],
        },
        "employees": employee_rows,
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by"),
    }


def _project_is_shared_with_current_user(project: dict[str, Any]) -> bool:
    if not current_user.is_authenticated:
        return False
    if project.get("owner_user_id") == _user_id():
        return False
    shares = _normalized_account_shares(project)
    username = str(getattr(current_user, "username", "") or "").strip()
    if username and any(item["employee_number"] == username for item in shares["employees"]):
        return True
    office_share = shares["office"]
    if office_share.get("enabled"):
        target_offices = {int(value) for value in office_share.get("office_ids") or []}
        if target_offices & _current_share_office_ids():
            return True
    return False


def _project_for_current_user_or_404(project_id: str) -> tuple[dict[str, Any], str]:
    project = _load_project(project_id)
    if project.get("owner_user_id") == _user_id():
        return project, "owner"
    if _project_is_shared_with_current_user(project):
        return project, "viewer"
    abort(404)


def _share_status_for_current_user(project: dict[str, Any]) -> dict[str, Any] | None:
    if current_user.is_authenticated and project.get("owner_user_id") == _user_id():
        shares = _normalized_account_shares(project)
        enabled = bool(shares["office"].get("enabled")) or bool(shares["employees"])
        return {"role": "owner", "enabled": enabled, "settings": shares}
    if _project_is_shared_with_current_user(project):
        return {"role": "viewer", "enabled": True}
    return None


def _find_project_by_token(token: str, token_type: str) -> dict[str, Any]:
    key = "view_token" if token_type == "view" else "edit_token"
    for project in _iter_stored_projects():
        if secrets.compare_digest(str(project.get(key, "")), token):
            return project
    abort(404)


def _month_detail(project: dict[str, Any], month_key: str) -> dict[str, Any]:
    month_data = (project.get("months") or {}).get(month_key)
    if not month_data:
        abort(404)
    return {
        "project": {
            "id": project["id"],
            "title": project["title"],
            "mode": project["mode"],
            "employee_number": str(project.get("employee_number") or ""),
            "site": _project_site_payload(project),
            "month_keys": _sort_month_keys(list((project.get("months") or {}).keys())),
            "urls": _project_public_urls(project),
        },
        "month": _client_month_payload(month_data),
        "month_key": month_key,
    }


def _csv_lines_for_month(
    project_title: str,
    project_mode: str,
    month_data: dict[str, Any],
    project_employee_number: str = "",
) -> list[list[Any]]:
    return serialize_entry_rows(
        project_mode,
        month_data["year"],
        month_data["month"],
        project_title,
        month_data.get("required_capacity", 0) if month_data.get("capacity_enabled") else 0,
        month_data.get("entries_per_day", {}),
        project_employee_number,
    )


def _safe_download_stem(value: str) -> str:
    safe = str(value)
    for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        safe = safe.replace(char, "_")
    return safe


def _csv_text_for_month(
    project_title: str,
    project_mode: str,
    month_data: dict[str, Any],
    project_employee_number: str = "",
) -> str:
    return serialize_csv_text(
        project_mode,
        month_data["year"],
        month_data["month"],
        project_title,
        month_data.get("required_capacity", 0) if month_data.get("capacity_enabled") else 0,
        month_data.get("entries_per_day", {}),
        project_employee_number,
    )


def _xlsx_bytes_for_month(
    project_title: str,
    project_mode: str,
    month_data: dict[str, Any],
    project_employee_number: str = "",
) -> io.BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = f"{month_data['year']}-{month_data['month']:02d}"
    for row in _csv_lines_for_month(project_title, project_mode, month_data, project_employee_number):
        sheet.append(row)
    for cell in sheet[1]:
        font = copy(cell.font)
        font.bold = True
        cell.font = font
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _parse_shiftersync_csv(file_storage) -> dict[str, Any]:
    raw = file_storage.read()
    content = None
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if content is None:
        raise CloudShiftError(f"CSV の読み込みに失敗しました: {last_error}", 400)
    try:
        parsed = parse_csv_text(content)
    except Exception as exc:
        raise CloudShiftError(str(exc), 400) from exc

    return {
        "title": _sanitize_title(parsed["title"]),
        "mode": _sanitize_mode(parsed["mode"]),
        "employee_number": _sanitize_employee_number(parsed.get("employee_number")),
        "year": parsed["year"],
        "month": parsed["month"],
        "capacity_enabled": parsed["capacity_enabled"],
        "required_capacity": parsed["required_capacity"],
        "entries_per_day": parsed["entries_per_day"],
    }


def _entry_history_label(entry: dict[str, str]) -> str:
    return entry_display_text(entry, include_comment=True, comment_limit=18)


def _describe_day_changes(previous: list[dict[str, str]], current: list[dict[str, str]], day: int) -> list[str]:
    changes: list[str] = []
    previous_by_id = {entry["id"]: entry for entry in previous}
    current_by_id = {entry["id"]: entry for entry in current}

    for entry_id, entry in current_by_id.items():
        if entry_id not in previous_by_id:
            changes.append(f"{day}日に {_entry_history_label(entry)} を追加")
            continue
        if entry != previous_by_id[entry_id]:
            changes.append(f"{day}日の {_entry_history_label(entry)} を更新")

    for entry_id, entry in previous_by_id.items():
        if entry_id not in current_by_id:
            changes.append(f"{day}日から {_entry_history_label(entry)} を削除")

    common_previous_order = [entry["id"] for entry in previous if entry["id"] in current_by_id]
    common_current_order = [entry["id"] for entry in current if entry["id"] in previous_by_id]
    if len(common_previous_order) >= 2 and common_previous_order != common_current_order:
        changes.append(f"{day}日の並び順を変更")
    return changes


def _describe_month_changes(previous_month: dict[str, Any], current_month: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    days = monthrange(current_month["year"], current_month["month"])[1]
    for day in range(1, days + 1):
        previous_entries = previous_month["entries_per_day"].get(str(day), [])
        current_entries = current_month["entries_per_day"].get(str(day), [])
        if previous_entries != current_entries:
            changes.extend(_describe_day_changes(previous_entries, current_entries, day))
    if previous_month.get("required_capacity", 0) != current_month.get("required_capacity", 0):
        changes.append(
            f"{current_month['year']}-{current_month['month']:02d} の必要人数を "
            f"{previous_month.get('required_capacity', 0)} から {current_month.get('required_capacity', 0)} に変更"
        )
    return changes


def _merge_month_payload(
    current_month: dict[str, Any],
    incoming_month: dict[str, Any],
    base_month: dict[str, Any],
) -> dict[str, Any]:
    year = current_month["year"]
    month = current_month["month"]
    merged_entries = _empty_entries_for_month(year, month)
    current_entries = _normalize_entries(current_month.get("entries_per_day"), year, month)
    incoming_entries = _normalize_entries(incoming_month.get("entries_per_day"), year, month)
    base_entries = _normalize_entries(base_month.get("entries_per_day"), year, month)

    for day in range(1, monthrange(year, month)[1] + 1):
        key = str(day)
        current_day = current_entries[key]
        incoming_day = incoming_entries[key]
        base_day = base_entries[key]
        base_map = {entry["id"]: entry for entry in base_day}
        current_map = {entry["id"]: entry for entry in current_day}
        incoming_map = {entry["id"]: entry for entry in incoming_day}

        merged_map: dict[str, dict[str, str]] = {}
        ordered_ids = []
        for source in (incoming_day, current_day):
            for entry in source:
                if entry["id"] not in ordered_ids:
                    ordered_ids.append(entry["id"])

        all_ids = set(base_map) | set(current_map) | set(incoming_map)
        for entry_id in all_ids:
            base_entry = base_map.get(entry_id)
            current_entry = current_map.get(entry_id)
            incoming_entry = incoming_map.get(entry_id)

            if incoming_entry != base_entry:
                chosen = incoming_entry
            else:
                chosen = current_entry

            if chosen:
                merged_map[entry_id] = chosen

        merged_entries[key] = [merged_map[entry_id] for entry_id in ordered_ids if entry_id in merged_map]

    current_capacity = current_month.get("required_capacity", 0)
    base_capacity = base_month.get("required_capacity", 0)
    incoming_capacity = incoming_month.get("required_capacity", 0)
    if incoming_capacity != base_capacity:
        merged_capacity = incoming_capacity
    else:
        merged_capacity = current_capacity
    capacity_enabled = merged_capacity > 0

    merged = {
        "year": year,
        "month": month,
        "capacity_enabled": capacity_enabled,
        "required_capacity": merged_capacity if capacity_enabled else 0,
        "entries_per_day": merged_entries,
        "revision": int(current_month.get("revision", 1)) + 1,
        "created_at": current_month.get("created_at", _utcnow_iso()),
        "updated_at": _utcnow_iso(),
    }
    return merged


def _snapshot_month_payload(month_data: dict[str, Any]) -> dict[str, Any]:
    return _client_month_payload(month_data, include_draft=False) or {}


def _trim_revision_snapshots(snapshots: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(snapshots.keys(), key=lambda value: int(value))
    trimmed = dict(snapshots)
    if len(keys) <= MAX_REVISION_SNAPSHOTS:
        return trimmed
    for key in keys[:-MAX_REVISION_SNAPSHOTS]:
        trimmed.pop(key, None)
    return trimmed


def _month_entry_metrics(month_data: dict[str, Any]) -> dict[str, int]:
    entries_per_day = month_data.get("entries_per_day") or {}
    entry_count = 0
    day_count = 0
    comment_count = 0
    for entries in entries_per_day.values():
        normalized_entries = entries if isinstance(entries, list) else []
        if normalized_entries:
            day_count += 1
        entry_count += len(normalized_entries)
        comment_count += sum(1 for entry in normalized_entries if str(entry.get("comment") or "").strip())
    return {
        "entry_count": entry_count,
        "day_count": day_count,
        "comment_count": comment_count,
    }


def _revision_summary_item(
    month_data: dict[str, Any],
    *,
    revision: int,
    is_current: bool,
) -> dict[str, Any]:
    metrics = _month_entry_metrics(month_data)
    return {
        "revision": revision,
        "is_current": is_current,
        "updated_at": month_data.get("updated_at"),
        "required_capacity": month_data.get("required_capacity", 0),
        "capacity_enabled": bool(month_data.get("capacity_enabled")),
        "entry_count": metrics["entry_count"],
        "day_count": metrics["day_count"],
        "comment_count": metrics["comment_count"],
    }


def _month_revision_catalog(month_data: dict[str, Any]) -> list[dict[str, Any]]:
    current_revision = int(month_data.get("revision", 1))
    snapshots = month_data.get("revision_snapshots") or {}
    items = [_revision_summary_item(month_data, revision=current_revision, is_current=True)]
    for key in sorted(snapshots.keys(), key=lambda value: int(value), reverse=True):
        snapshot = snapshots.get(key)
        if not snapshot:
            continue
        try:
            revision = int(key)
        except (TypeError, ValueError):
            continue
        items.append(_revision_summary_item(snapshot, revision=revision, is_current=False))
    items.sort(key=lambda item: item["revision"], reverse=True)
    return items


def _restore_month_revision_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    revision: int,
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)

    current_revision = int(current_month.get("revision", 1))
    if revision == current_revision:
        raise CloudShiftError("現在のリビジョンは復元できません", 400)

    snapshot = (current_month.get("revision_snapshots") or {}).get(str(revision))
    if not snapshot:
        raise CloudShiftError("指定したリビジョンが見つかりません", 404)

    restored = _snapshot_month_payload(snapshot)
    restored["year"] = year
    restored["month"] = month
    restored["capacity_enabled"] = bool(restored.get("required_capacity", 0) > 0)
    restored["entries_per_day"] = _normalize_entries(restored.get("entries_per_day"), year, month)
    restored["revision"] = current_revision + 1
    restored["created_at"] = current_month.get("created_at", _utcnow_iso())
    restored["updated_at"] = _utcnow_iso()

    snapshots = dict(current_month.get("revision_snapshots") or {})
    snapshots[str(current_revision)] = _snapshot_month_payload(current_month)
    restored["revision_snapshots"] = _trim_revision_snapshots(snapshots)

    changes = [f"{month_key} をリビジョン {revision} の内容で復元"]
    changes.extend(_describe_month_changes(current_month, restored)[:20])

    project["months"][month_key] = restored
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "month_restored",
            "month_key": month_key,
            "changes": changes[:100],
        },
    )
    return restored


def _counter_items(counter: dict[str, int], *, limit: int | None = None) -> list[dict[str, Any]]:
    items = [{"label": label, "count": count} for label, count in counter.items() if count > 0]
    items.sort(key=lambda item: (-item["count"], item["label"]))
    if limit is not None:
        return items[:limit]
    return items


def _named_frequency_items(
    rows: dict[tuple[str, str], dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    items = []
    for payload in rows.values():
        label = payload["label"]
        day_count = len(payload["days"])
        items.append(
            {
                "label": label,
                "count": payload["count"],
                "meta": f"{day_count}日 / コメント {payload['comment_count']}件",
            }
        )
    items.sort(key=lambda item: (-item["count"], item["label"]))
    if limit is not None:
        return items[:limit]
    return items


def _month_summary_from_payload(project: dict[str, Any], month_data: dict[str, Any]) -> dict[str, Any]:
    mode = project.get("mode", "scene")
    days_in_month = monthrange(month_data["year"], month_data["month"])[1]
    entry_count = 0
    active_days = 0
    empty_days = 0
    comment_count = 0
    comment_days = 0
    shortage_days = 0
    max_entries_in_day = 0

    primary_rows: dict[tuple[str, str], dict[str, Any]] = {}
    time_counter: dict[str, int] = {}
    vehicle_counter: dict[str, int] = {}
    leave_counter: dict[str, int] = {}
    comment_day_counter: dict[str, int] = {}
    work_entry_count = 0
    leave_entry_count = 0

    entries_per_day = _normalize_entries(month_data.get("entries_per_day"), month_data["year"], month_data["month"])
    for day in range(1, days_in_month + 1):
        day_key = str(day)
        entries = entries_per_day.get(day_key, [])
        entry_total = len(entries)
        entry_count += entry_total
        max_entries_in_day = max(max_entries_in_day, entry_total)
        if entry_total:
            active_days += 1
        else:
            empty_days += 1

        if month_data.get("capacity_enabled") and month_data.get("required_capacity", 0) > 0:
            if entry_total < int(month_data.get("required_capacity", 0)):
                shortage_days += 1

        day_comment_count = 0
        for entry in entries:
            option_key, raw_name = parse_entry_value(entry.get("value") or "")
            name = str(raw_name or "").strip()
            employee_number = str(entry.get("employee_number") or "").strip()
            comment = str(entry.get("comment") or "").strip()
            if comment:
                comment_count += 1
                day_comment_count += 1

            include_primary_row = not (mode == "person" and option_key in LEAVE_OPTION_MAPPINGS)
            if include_primary_row:
                key = (name, employee_number)
                if key not in primary_rows:
                    label = name or "名称未設定"
                    if employee_number:
                        label = f"{label} / {employee_number}"
                    primary_rows[key] = {
                        "label": label,
                        "count": 0,
                        "days": set(),
                        "comment_count": 0,
                    }
                primary_rows[key]["count"] += 1
                primary_rows[key]["days"].add(day)
                if comment:
                    primary_rows[key]["comment_count"] += 1

            if option_key in SHIFT_TIME_OPTION_KEYS:
                time_counter[OPTION_LABELS.get(option_key, option_key)] = (
                    time_counter.get(OPTION_LABELS.get(option_key, option_key), 0) + 1
                )
                work_entry_count += 1
            elif option_key in LEAVE_OPTION_MAPPINGS:
                leave_counter[OPTION_LABELS.get(option_key, option_key)] = (
                    leave_counter.get(OPTION_LABELS.get(option_key, option_key), 0) + 1
                )
                leave_entry_count += 1
            elif option_key:
                work_entry_count += 1
            else:
                work_entry_count += 1

            if option_key in VEHICLE_OPTION_KEYS:
                vehicle_counter[OPTION_LABELS.get(option_key, option_key)] = (
                    vehicle_counter.get(OPTION_LABELS.get(option_key, option_key), 0) + 1
                )

        if day_comment_count:
            comment_days += 1
            comment_day_counter[f"{day}日"] = day_comment_count

    average_entries = round(entry_count / active_days, 1) if active_days else 0
    overview = [
        {"label": "登録件数", "value": f"{entry_count}件"},
        {"label": "入力日数", "value": f"{active_days}日"},
        {"label": "空欄日数", "value": f"{empty_days}日"},
        {"label": "平均件数", "value": f"{average_entries}件/日"},
        {"label": "コメント", "value": f"{comment_count}件 / {comment_days}日"},
        {"label": "最大件数日", "value": f"{max_entries_in_day}件"},
    ]
    if month_data.get("capacity_enabled") and month_data.get("required_capacity", 0) > 0:
        overview.append({"label": "必要人数", "value": str(month_data["required_capacity"])})
        overview.append({"label": "不足日数", "value": f"{shortage_days}日"})

    if mode == "scene":
        sections = [
            {
                "title": "人物別回数",
                "items": _named_frequency_items(primary_rows),
                "empty_message": "人物の登録はまだありません",
            },
            {
                "title": "時間帯",
                "items": _counter_items(time_counter),
                "empty_message": "時間帯オプションはまだありません",
            },
            {
                "title": "車両",
                "items": _counter_items(vehicle_counter),
                "empty_message": "車両オプションはまだありません",
            },
            {
                "title": "コメントが多い日",
                "items": _counter_items(comment_day_counter, limit=10),
                "empty_message": "コメント付きの登録はまだありません",
            },
        ]
    else:
        overview.extend(
            [
                {"label": "勤務登録", "value": f"{work_entry_count}件"},
                {"label": "休暇登録", "value": f"{leave_entry_count}件"},
            ]
        )
        sections = [
            {
                "title": "現場別回数",
                "items": _named_frequency_items(primary_rows),
                "empty_message": "現場の登録はまだありません",
            },
            {
                "title": "勤務帯",
                "items": _counter_items(time_counter),
                "empty_message": "勤務帯オプションはまだありません",
            },
            {
                "title": "休暇種別",
                "items": _counter_items(leave_counter),
                "empty_message": "休暇登録はまだありません",
            },
            {
                "title": "車両",
                "items": _counter_items(vehicle_counter),
                "empty_message": "車両オプションはまだありません",
            },
        ]

    return {
        "month_key": _month_key(month_data["year"], month_data["month"]),
        "title": project.get("title", ""),
        "mode": mode,
        "revision": int(month_data.get("revision", 1)),
        "overview": overview,
        "sections": sections,
    }


def _summary_month_payload(project: dict[str, Any], year: int, month: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)

    if not payload:
        return _month_summary_from_payload(project, current_month)

    required_capacity = (
        _sanitize_capacity(payload.get("required_capacity"))[1]
        if "required_capacity" in payload
        else int(current_month.get("required_capacity", 0))
    )
    entries_per_day = (
        payload.get("entries_per_day")
        if "entries_per_day" in payload
        else current_month.get("entries_per_day") or {}
    )
    month_data = {
        **_snapshot_month_payload(current_month),
        "year": year,
        "month": month,
        "capacity_enabled": required_capacity > 0,
        "required_capacity": required_capacity,
        "entries_per_day": _normalize_entries(entries_per_day, year, month),
        "revision": int(current_month.get("revision", 1)),
        "created_at": current_month.get("created_at"),
        "updated_at": current_month.get("updated_at"),
    }
    return _month_summary_from_payload(project, month_data)


def _base_month_signature(month_data: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    return {
        "year": year,
        "month": month,
        "required_capacity": _sanitize_capacity(month_data.get("required_capacity"))[1],
        "entries_per_day": _normalize_entries(month_data.get("entries_per_day"), year, month),
    }


def _resolve_base_month_without_revision(
    current_month: dict[str, Any], request_base_month: dict[str, Any]
) -> dict[str, Any] | None:
    year = current_month["year"]
    month = current_month["month"]
    requested_signature = _base_month_signature(request_base_month or {}, year, month)

    candidates: list[dict[str, Any]] = [_snapshot_month_payload(current_month)]
    snapshots = current_month.get("revision_snapshots") or {}
    for key in sorted(snapshots.keys(), key=lambda value: int(value), reverse=True):
        snapshot = snapshots.get(key)
        if snapshot:
            candidates.append(snapshot)

    for candidate in candidates:
        if _base_month_signature(candidate, year, month) == requested_signature:
            return candidate
    return None


def _trusted_base_month(current_month: dict[str, Any], request_base_month: dict[str, Any]) -> dict[str, Any]:
    try:
        requested_revision = int((request_base_month or {}).get("revision", 0))
    except (TypeError, ValueError):
        requested_revision = 0

    if requested_revision <= 0:
        fallback_snapshot = _resolve_base_month_without_revision(current_month, request_base_month or {})
        if fallback_snapshot:
            return fallback_snapshot
        raise CloudShiftError("編集対象が古い可能性があります。再読み込みしてから保存してください", 409)

    current_revision = int(current_month.get("revision", 1))
    if requested_revision == current_revision:
        return _snapshot_month_payload(current_month)

    snapshot = (current_month.get("revision_snapshots") or {}).get(str(requested_revision))
    if snapshot:
        return snapshot

    raise CloudShiftError("他のユーザーが更新しました。再読み込みしてから保存してください", 409)


def _editor_identity(editor_name: str | None = None) -> tuple[str, str]:
    if current_user.is_authenticated:
        return _user_label(), "dstt_user"
    label = (editor_name or "").strip()
    if not label:
        raise CloudShiftError("編集者名を入力してください", 400)
    return label[:80], "guest"


def _assist_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _assist_weekday_label(index: int) -> str:
    if 0 <= index < len(ASSIST_WEEKDAY_LABELS):
        return ASSIST_WEEKDAY_LABELS[index]
    return str(index)


def _assist_rule_weekday_value(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _assist_weekday_index(text)


def _assist_rule_weekday_label(value: Any) -> str:
    if value in (None, ""):
        return ASSIST_WEEKDAYLESS_LABEL
    try:
        return _assist_weekday_label(int(value))
    except (TypeError, ValueError):
        return str(value)


def _assist_date_parts(value: Any) -> tuple[str, date]:
    text = str(value or "").strip()
    if not text:
        raise CloudShiftError("日付は必須です", 400)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CloudShiftError("日付は YYYY-MM-DD 形式で入力してください", 400) from exc
    return parsed.isoformat(), parsed


def _assist_weekday_index(value: Any) -> int:
    try:
        weekday = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("曜日が不正です", 400) from exc
    if weekday < 0 or weekday > 6:
        raise CloudShiftError("曜日が不正です", 400)
    return weekday


def _assist_short_text(value: Any, label: str, *, required: bool = False, limit: int = 80) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if required and not text:
        raise CloudShiftError(f"{label}は必須です", 400)
    return text[:limit]


def _assist_long_text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").replace("\r", "").strip()[:limit]


def _assist_bool(value: Any, default: bool = False) -> bool:
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


def _assist_shift_key(value: Any, *, required: bool = False) -> str:
    key = str(value or "").strip().upper()
    if not key:
        if required:
            raise CloudShiftError("オプションは必須です", 400)
        return ""
    if key not in OPTION_LABELS:
        raise CloudShiftError("オプション種別が不正です", 400)
    return key


def _assist_shift_label(value: Any) -> str:
    key = str(value or "").strip().upper()
    return OPTION_LABELS.get(key, key) if key else ASSIST_OPTIONLESS_LABEL


def _assist_role_type(value: Any) -> str:
    role_type = str(value or "").strip().lower()
    if role_type not in ASSIST_ROLE_LABELS:
        raise CloudShiftError("役割種別が不正です", 400)
    return role_type


def _assist_custom_points(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        points = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("独自点数が不正です", 400) from exc
    if points < ASSIST_CUSTOM_POINTS_MIN or points > ASSIST_CUSTOM_POINTS_MAX:
        raise CloudShiftError(
            f"独自点数は {ASSIST_CUSTOM_POINTS_MIN} から {ASSIST_CUSTOM_POINTS_MAX} の範囲で入力してください",
            400,
        )
    return points


def _assist_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("優先順位が不正です", 400) from exc
    if priority < 1 or priority > 99:
        raise CloudShiftError("優先順位は 1 から 99 の範囲で入力してください", 400)
    return priority


def _assist_period_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _assist_date_parts(text)[0]


def _assist_weekday_values(value: Any, label: str) -> list[int]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, list) else [value]
    weekdays: list[int] = []
    for item in items:
        weekday = _assist_weekday_index(item)
        if weekday not in weekdays:
            weekdays.append(weekday)
    return sorted(weekdays)


def _assist_candidate_lookup_key(candidate_id: Any, employee_number: Any, candidate_name: Any) -> str:
    normalized_id = str(candidate_id or "").strip()
    if normalized_id:
        return normalized_id
    return f"{str(employee_number or '').strip()}:{str(candidate_name or '').strip()}"


def _assist_option_aptitude_category(shift_key: Any) -> str | None:
    key = str(shift_key or "").strip().upper()
    if key in SHIFT_TIME_OPTION_KEYS:
        return "time"
    if key in VEHICLE_OPTION_KEYS:
        return "vehicle"
    return None


def _assist_option_aptitude_label(category: str | None) -> str:
    if category == "time":
        return "時間帯適性"
    if category == "vehicle":
        return "車両適性"
    return "オプション適性"


def _assist_option_aptitude_points(exact_count: int, category_total: int) -> int:
    if exact_count >= 5:
        return ASSIST_OPTION_APTITUDE_MAX_BONUS
    if exact_count >= 3:
        return ASSIST_OPTION_APTITUDE_MID_BONUS
    if exact_count >= 1:
        return ASSIST_OPTION_APTITUDE_MIN_BONUS
    if category_total > 0:
        return ASSIST_OPTION_APTITUDE_ZERO_PENALTY
    return 0


def _assist_option_aptitude_bucket_label(exact_count: int, category_total: int) -> str:
    if exact_count >= 5:
        return "5件以上"
    if exact_count >= 3:
        return "3-4件"
    if exact_count >= 1:
        return "1-2件"
    if category_total > 0:
        return "同カテゴリ実績あり / 対象0件"
    return "学習データなし"


def _ensure_scene_project(project: dict[str, Any]) -> None:
    if project.get("mode") != "scene":
        raise CloudShiftError("アシスト機能は scene モード専用です", 400)


def _ensure_person_project(project: dict[str, Any]) -> None:
    if project.get("mode") != "person":
        raise CloudShiftError("このアシスト機能は person モード専用です", 400)


def _ensure_assist(project: dict[str, Any]) -> dict[str, Any]:
    assist = project.get("assist")
    if not isinstance(assist, dict):
        assist = {}
    payload = {
        "version": int(assist.get("version", 1) or 1),
        "profiles": [item for item in (assist.get("profiles") or []) if isinstance(item, dict)],
        "records": [item for item in (assist.get("records") or []) if isinstance(item, dict)],
        "rules": [item for item in (assist.get("rules") or []) if isinstance(item, dict)],
        "experienced_sites": [item for item in (assist.get("experienced_sites") or []) if isinstance(item, dict)],
        "training_sites": [item for item in (assist.get("training_sites") or []) if isinstance(item, dict)],
    }
    project["assist"] = payload
    return payload


def _person_assist_kind_key(kind: str) -> str:
    if kind == "experienced":
        return "experienced_sites"
    if kind == "training":
        return "training_sites"
    raise CloudShiftError("person assist 種別が不正です", 400)


def _person_assist_kind_label(kind: str) -> str:
    if kind == "experienced":
        return "経験済現場"
    if kind == "training":
        return "研修要現場"
    raise CloudShiftError("person assist 種別が不正です", 400)


def _person_assist_op_label(value: Any) -> str:
    return "OPあり" if _assist_bool(value, False) else "OPなし"


def _person_assist_site_payload(site: dict[str, Any]) -> dict[str, Any]:
    weekday = int(site.get("weekday", 0) or 0)
    return {
        "id": str(site.get("id") or ""),
        "date": str(site.get("date") or ""),
        "weekday": weekday,
        "weekday_label": _assist_weekday_label(weekday),
        "site_name": str(site.get("site_name") or ""),
        "has_op": bool(site.get("has_op", False)),
        "op_label": _person_assist_op_label(site.get("has_op")),
        "notes": str(site.get("notes") or ""),
        "created_at": site.get("created_at"),
        "updated_at": site.get("updated_at"),
        "created_by": site.get("created_by"),
        "updated_by": site.get("updated_by"),
    }


def _normalized_site_title(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).casefold()


def _iter_project_summaries() -> list[dict[str, Any]]:
    return _iter_stored_projects()


def _matching_person_project_ids_for_scene_entry(source_project: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    employee_number = str(entry.get("employee_number") or "").strip()
    _, employee_name = _entry_option_and_name(entry)
    matches: list[str] = []
    fallback_matches: list[str] = []
    normalized_employee_name = _normalized_person_title(employee_name)
    for project in _iter_project_summaries():
        if project.get("mode") != "person":
            continue
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            continue
        if employee_number and str(project.get("employee_number") or "").strip() == employee_number:
            matches.append(project_id)
            continue
        if not employee_number and normalized_employee_name and _normalized_person_title(project.get("title")) == normalized_employee_name:
            fallback_matches.append(project_id)
    if matches:
        return matches
    return fallback_matches if len(fallback_matches) == 1 else []


def _matching_scene_project_ids_for_person_entry(source_project: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    option_key, site_name_from_value = _entry_option_and_name(entry)
    if option_key in LEAVE_OPTION_MAPPINGS:
        return []
    site_link = _entry_site_link_fields(entry)
    if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
        site_link["site_name"] = site_name_from_value
    matches: list[str] = []
    for project in _iter_project_summaries():
        if project.get("mode") != "scene":
            continue
        if _person_experience_matches_scene_project(site_link, project):
            project_id = str(project.get("id") or "").strip()
            if project_id:
                matches.append(project_id)
    return matches


def _build_person_synced_entry_from_scene(
    source_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    option_key, _ = _entry_option_and_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    site_name = str(source_project.get("site_name") or source_project.get("title") or "").strip()
    return {
        "id": _sync_entry_id(SHIFT_SYNC_SCENE_SOURCE, source_project.get("id"), month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, site_name),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_number": "",
        "site_row_id": str(_coerce_site_row_id(source_project.get("site_row_id")) or ""),
        "site_id": str(source_project.get("site_id") or "").strip(),
        "site_name": site_name,
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": SHIFT_SYNC_SCENE_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }


def _build_scene_synced_entry_from_person(
    source_project: dict[str, Any],
    target_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    option_key, _ = _entry_option_and_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    branch_fields = _scene_branch_fields_for_option(target_project, option_key)
    synced = {
        "id": _sync_entry_id(SHIFT_SYNC_PERSON_SOURCE, source_project.get("id"), month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, str(source_project.get("title") or "").strip()),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_number": str(source_project.get("employee_number") or "").strip(),
        "site_row_id": "",
        "site_id": "",
        "site_name": "",
        "site_branch_row_id": branch_fields["site_branch_row_id"],
        "site_branch": branch_fields["site_branch"],
        "sync_source_type": SHIFT_SYNC_PERSON_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }
    return _scene_entry_with_siteplus_defaults(target_project, synced)


def _desired_shift_sync_entries_by_target(
    source_project: dict[str, Any],
    month_key: str,
    month_data: dict[str, Any],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    desired: dict[str, dict[str, list[dict[str, Any]]]] = {}
    mode = str(source_project.get("mode") or "")
    entries_per_day = _normalize_entries(month_data.get("entries_per_day"), month_data["year"], month_data["month"])
    for day_key, entries in entries_per_day.items():
        for entry in entries:
            if _entry_is_shift_synced(entry):
                continue
            option_key, entry_name = _entry_option_and_name(entry)
            if mode == "scene":
                if not entry_name:
                    continue
                for target_project_id in _matching_person_project_ids_for_scene_entry(source_project, entry):
                    desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                        _build_person_synced_entry_from_scene(source_project, entry, month_key=month_key, day_key=day_key)
                    )
            elif mode == "person":
                if option_key in LEAVE_OPTION_MAPPINGS:
                    continue
                site_link = _entry_site_link_fields(entry)
                if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
                    continue
                for target_project_id in _matching_scene_project_ids_for_person_entry(source_project, entry):
                    desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(entry)
    return desired


def _sync_entry_matches_source(entry: dict[str, Any], source_project_id: str, month_key: str) -> bool:
    return (
        _entry_is_shift_synced(entry)
        and str(entry.get("sync_source_project_id") or "") == str(source_project_id or "")
        and str(entry.get("sync_source_month_key") or "") == str(month_key or "")
    )


def _replace_shift_synced_entries_in_target_project(
    target_project: dict[str, Any],
    source_project: dict[str, Any],
    month_key: str,
    desired_entries_by_day: dict[str, list[dict[str, Any]]],
    *,
    actor_name: str,
) -> bool:
    year, month = _parse_month_key(month_key)
    current_month = (target_project.get("months") or {}).get(month_key)
    current_entries_per_day = _normalize_entries(
        (current_month or {}).get("entries_per_day"),
        year,
        month,
    )
    next_entries_per_day = _empty_entries_for_month(year, month)
    has_desired_entries = any(desired_entries_by_day.get(day_key) for day_key in next_entries_per_day.keys())

    for day_key in next_entries_per_day.keys():
        preserved = [
            dict(entry) for entry in current_entries_per_day.get(day_key, [])
            if not _sync_entry_matches_source(entry, str(source_project.get("id") or ""), month_key)
        ]
        desired_for_day = desired_entries_by_day.get(day_key, [])
        if str(source_project.get("mode") or "") == "person":
            desired = [
                _build_scene_synced_entry_from_person(
                    source_project,
                    target_project,
                    entry,
                    month_key=month_key,
                    day_key=day_key,
                )
                for entry in desired_for_day
            ]
        else:
            desired = [dict(entry) for entry in desired_for_day]
        next_entries_per_day[day_key] = preserved + desired

    if not current_month and not has_desired_entries:
        return False

    if current_month:
        incoming_month = {
            "year": year,
            "month": month,
            "required_capacity": int(current_month.get("required_capacity", 0) or 0),
            "entries_per_day": next_entries_per_day,
        }
        merged = _merge_month_payload(current_month, incoming_month, _snapshot_month_payload(current_month))
        changes = _describe_month_changes(current_month, merged)
        if not changes:
            return False
        snapshots = dict(current_month.get("revision_snapshots") or {})
        snapshots[str(int(current_month.get("revision", 1)))] = _snapshot_month_payload(current_month)
        merged["revision_snapshots"] = _trim_revision_snapshots(snapshots)
        target_project.setdefault("months", {})[month_key] = merged
    else:
        target_project.setdefault("months", {})[month_key] = _build_month_payload(
            year,
            month,
            False,
            0,
            next_entries_per_day,
        )

    _save_project(target_project)
    _append_history(
        target_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "auto",
            "action": "shift_sync",
            "month_key": month_key,
            "changes": [
                f"{source_project.get('title')} からシフトを自動反映"
            ],
        },
    )
    return True


def _resync_shift_month(source_project: dict[str, Any], month_key: str, *, actor_name: str) -> None:
    month_data = (source_project.get("months") or {}).get(month_key)
    if not month_data:
        return
    desired_by_target = _desired_shift_sync_entries_by_target(source_project, month_key, month_data)
    relevant_target_ids = set(desired_by_target.keys())
    for project in _iter_project_summaries():
        if project.get("mode") == source_project.get("mode"):
            continue
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            continue
        target_month = (project.get("months") or {}).get(month_key)
        target_entries = _normalize_entries((target_month or {}).get("entries_per_day"), month_data["year"], month_data["month"])
        has_existing_sync = any(
            _sync_entry_matches_source(entry, str(source_project.get("id") or ""), month_key)
            for entries in target_entries.values()
            for entry in entries
        )
        if has_existing_sync:
            relevant_target_ids.add(project_id)
    for target_project_id in sorted(relevant_target_ids):
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            _replace_shift_synced_entries_in_target_project(
                target_project,
                source_project,
                month_key,
                desired_by_target.get(target_project_id, {}),
                actor_name=actor_name,
            )


def _refresh_shift_sync_for_target_month(target_project: dict[str, Any], month_key: str, *, actor_name: str) -> None:
    if not month_key or month_key not in (target_project.get("months") or {}):
        return
    target_mode = str(target_project.get("mode") or "")
    for source_project in _iter_project_summaries():
        if str(source_project.get("mode") or "") == target_mode:
            continue
        if month_key not in (source_project.get("months") or {}):
            continue
        _resync_shift_month(source_project, month_key, actor_name=actor_name)


def _resync_shift_project(source_project: dict[str, Any], *, actor_name: str) -> None:
    for month_key in _sort_month_keys(list((source_project.get("months") or {}).keys())):
        _resync_shift_month(source_project, month_key, actor_name=actor_name)


def _refresh_shift_sync_for_target_project(target_project: dict[str, Any], *, actor_name: str) -> None:
    for month_key in _sort_month_keys(list((target_project.get("months") or {}).keys())):
        _refresh_shift_sync_for_target_month(target_project, month_key, actor_name=actor_name)


def _remove_shift_sync_for_month(source_project: dict[str, Any], month_key: str, *, actor_name: str) -> None:
    for project in _iter_project_summaries():
        if project.get("mode") == source_project.get("mode"):
            continue
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            continue
        with _project_lock(project_id):
            target_project = _load_project(project_id)
            _replace_shift_synced_entries_in_target_project(
                target_project,
                source_project,
                month_key,
                {},
                actor_name=actor_name,
            )


def _remove_shift_sync_for_project(source_project: dict[str, Any], *, actor_name: str) -> None:
    for month_key in _sort_month_keys(list((source_project.get("months") or {}).keys())):
        _remove_shift_sync_for_month(source_project, month_key, actor_name=actor_name)

def _person_experience_available_from(date_text: str) -> str:
    _, parsed = _assist_date_parts(date_text)
    return (parsed + timedelta(days=1)).isoformat()


def _person_assist_site_history_label(site: dict[str, Any]) -> str:
    return (
        f"{site.get('date')}({_assist_weekday_label(int(site.get('weekday', 0) or 0))}) / "
        f"{site.get('site_name')} / "
        f"{_person_assist_op_label(site.get('has_op'))}"
    )


def _person_assist_site_from_payload(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    actor_name: str,
) -> dict[str, Any]:
    date_text, parsed_date = _assist_date_parts(payload.get("date"))
    site_name = _assist_short_text(payload.get("site_name"), "現場名", required=True, limit=120)
    notes = _assist_long_text(payload.get("notes"))
    has_op = _assist_bool(payload.get("has_op"), False)
    timestamp = _utcnow_iso()
    if existing:
        created_at = existing.get("created_at", timestamp)
        created_by = existing.get("created_by", actor_name)
        site_id = str(existing.get("id") or _assist_id("psite"))
    else:
        created_at = timestamp
        created_by = actor_name
        site_id = _assist_id("psite")
    return {
        "id": site_id,
        "date": date_text,
        "weekday": parsed_date.weekday(),
        "site_name": site_name,
        "has_op": has_op,
        "notes": notes,
        "created_at": created_at,
        "updated_at": timestamp,
        "created_by": created_by,
        "updated_by": actor_name,
    }


def _ensure_person_project(project: dict[str, Any]) -> None:
    if project.get("mode") != "person":
        raise CloudShiftError("このアシスト機能は person モード専用です", 400)


def _person_assist_collection_key(kind: str) -> str:
    key = PERSON_ASSIST_COLLECTION_KEYS.get(str(kind or "").strip().lower())
    if not key:
        raise CloudShiftError("person assist 種別が不正です", 400)
    return key


def _person_assist_kind_label(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    return PERSON_ASSIST_KIND_LABELS.get(normalized, normalized or "person assist")


def _ensure_person_assist(project: dict[str, Any]) -> dict[str, Any]:
    assist = project.get("assist")
    if not isinstance(assist, dict):
        assist = {}
    payload = {
        "version": int(assist.get("version", 1) or 1),
        "experienced_sites": [
            item for item in (assist.get("experienced_sites") or []) if isinstance(item, dict)
        ],
        "training_sites": [
            item for item in (assist.get("training_sites") or []) if isinstance(item, dict)
        ],
    }
    project["assist"] = payload
    return payload


def _person_assist_site_payload(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "kind": str(kind or ""),
        "kind_label": _person_assist_kind_label(kind),
        "date": str(item.get("date") or ""),
        "effective_from": str(item.get("effective_from") or ""),
        "site_name": str(item.get("site_name") or ""),
        "shift_key": str(item.get("shift_key") or ""),
        "shift_label": _assist_shift_label(item.get("shift_key")),
        "notes": str(item.get("notes") or ""),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "created_by": item.get("created_by"),
        "updated_by": item.get("updated_by"),
    }


def _person_assist_bootstrap_payload(project: dict[str, Any]) -> dict[str, Any]:
    assist = _ensure_person_assist(project)
    experienced = [
        _person_assist_site_payload(item, kind=PERSON_ASSIST_EXPERIENCE_KIND)
        for item in assist["experienced_sites"]
    ]
    trainings = [
        _person_assist_site_payload(item, kind=PERSON_ASSIST_TRAINING_KIND)
        for item in assist["training_sites"]
    ]
    experienced.sort(key=lambda item: (item["date"], item["site_name"], item["shift_key"]), reverse=True)
    trainings.sort(key=lambda item: (item["date"], item["site_name"], item["shift_key"]), reverse=True)
    return {
        "success": True,
        "assist_mode": "person",
        "assist": {
            "version": assist["version"],
            "experienced_sites": experienced,
            "training_sites": trainings,
        },
        "permissions": {
            "can_edit_experienced": True,
            "can_edit_training": True,
        },
    }


def _person_assist_site_from_payload(
    payload: dict[str, Any],
    *,
    kind: str,
    existing: dict[str, Any] | None = None,
    actor_name: str,
) -> dict[str, Any]:
    date_text, parsed_date = _assist_date_parts(payload.get("date"))
    site_name = _assist_short_text(payload.get("site_name"), "現場名", required=True, limit=120)
    shift_key = _assist_shift_key(payload.get("shift_key"), required=False)
    notes = _assist_long_text(payload.get("notes"))
    timestamp = _utcnow_iso()
    if existing:
        created_at = existing.get("created_at", timestamp)
        created_by = existing.get("created_by", actor_name)
        site_id = str(existing.get("id") or _assist_id("psite"))
    else:
        created_at = timestamp
        created_by = actor_name
        site_id = _assist_id("psite")
    return {
        "id": site_id,
        "kind": kind,
        "date": date_text,
        "effective_from": (parsed_date + timedelta(days=1)).isoformat(),
        "site_name": site_name,
        "shift_key": shift_key,
        "notes": notes,
        "created_at": created_at,
        "updated_at": timestamp,
        "created_by": created_by,
        "updated_by": actor_name,
    }


def _person_assist_site_history_label(item: dict[str, Any]) -> str:
    return (
        f"{item.get('date')} / {item.get('site_name')} / "
        f"{_assist_shift_label(item.get('shift_key'))}"
    )


def _person_assist_site_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    kind: str,
    actor_name: str,
    actor_type: str,
    site_id: str | None = None,
) -> dict[str, Any]:
    _ensure_person_project(project)
    assist = _ensure_person_assist(project)
    collection_key = _person_assist_collection_key(kind)
    existing = None
    if site_id:
        existing = next(
            (
                item
                for item in assist[collection_key]
                if str(item.get("id") or "") == str(site_id or "")
            ),
            None,
        )
        if not existing:
            raise CloudShiftError("対象の person assist が見つかりません", 404)
    item = _person_assist_site_from_payload(
        payload,
        kind=kind,
        existing=existing,
        actor_name=actor_name,
    )
    if existing:
        index = assist[collection_key].index(existing)
        assist[collection_key][index] = item
        changes = [f"{_person_assist_kind_label(kind)}を更新: {_person_assist_site_history_label(item)}"]
    else:
        assist[collection_key].append(item)
        changes = [f"{_person_assist_kind_label(kind)}を登録: {_person_assist_site_history_label(item)}"]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "person_assist_site_saved",
            "month_key": None,
            "changes": changes,
        },
    )
    return item


def _person_assist_site_delete(
    project: dict[str, Any],
    site_id: str,
    *,
    kind: str,
    actor_name: str,
    actor_type: str,
) -> None:
    _ensure_person_project(project)
    assist = _ensure_person_assist(project)
    collection_key = _person_assist_collection_key(kind)
    existing = next(
        (
            item
            for item in assist[collection_key]
            if str(item.get("id") or "") == str(site_id or "")
        ),
        None,
    )
    if not existing:
        raise CloudShiftError("対象の person assist が見つかりません", 404)
    assist[collection_key] = [
        item for item in assist[collection_key] if str(item.get("id") or "") != str(site_id or "")
    ]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "person_assist_site_deleted",
            "month_key": None,
            "changes": [f"{_person_assist_kind_label(kind)}を削除: {_person_assist_site_history_label(existing)}"],
        },
    )


def _person_experience_source_item(project: dict[str, Any], experience_id: str) -> dict[str, Any] | None:
    if project.get("mode") != "person":
        return None
    assist = _ensure_person_assist(project)
    return next(
        (
            item
            for item in assist["experienced_sites"]
            if str(item.get("id") or "") == str(experience_id or "")
        ),
        None,
    )


def _person_experience_sync_note(actor_name: str, notes: str) -> str:
    base = f"{str(actor_name or '').strip() or 'user'} からの自動実績登録"
    extra = str(notes or "").strip()
    return f"{base}\n{extra}" if extra else base


def _person_experience_sync_source(person_project: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "person_experience",
        "person_project_id": str(person_project.get("id") or ""),
        "experience_id": str(item.get("id") or ""),
        "site_name": str(item.get("site_name") or ""),
    }


def _is_person_experience_synced_record(
    record: dict[str, Any], person_project_id: str, experience_id: str
) -> bool:
    source = record.get("sync_source")
    if not isinstance(source, dict):
        return False
    return (
        str(source.get("type") or "") == "person_experience"
        and str(source.get("person_project_id") or "") == str(person_project_id or "")
        and str(source.get("experience_id") or "") == str(experience_id or "")
    )


def _remove_person_experience_synced_records(
    scene_project: dict[str, Any],
    person_project_id: str,
    experience_id: str,
    *,
    actor_name: str,
) -> bool:
    assist = _ensure_assist(scene_project)
    existing = [
        item
        for item in (assist.get("records") or [])
        if _is_person_experience_synced_record(item, person_project_id, experience_id)
    ]
    if not existing:
        return False
    assist["records"] = [
        item
        for item in (assist.get("records") or [])
        if not _is_person_experience_synced_record(item, person_project_id, experience_id)
    ]
    _save_project(scene_project)
    _append_history(
        scene_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "system",
            "action": "assist_record_deleted",
            "month_key": None,
            "changes": [
                f"person 経験済現場との自動連携を解除: {_assist_record_history_label(item)}"
                for item in existing
            ][:20],
        },
    )
    return True


def _upsert_person_experience_synced_record(
    scene_project: dict[str, Any],
    person_project: dict[str, Any],
    experience: dict[str, Any],
    *,
    actor_name: str,
) -> bool:
    assist = _ensure_assist(scene_project)
    existing = next(
        (
            item
            for item in (assist.get("records") or [])
            if _is_person_experience_synced_record(
                item,
                str(person_project.get("id") or ""),
                str(experience.get("id") or ""),
            )
        ),
        None,
    )
    record = _assist_record_from_payload(
        assist,
        {
            "date": str(experience.get("effective_from") or experience.get("date") or ""),
            "candidate_name": person_project.get("title"),
            "employee_number": person_project.get("employee_number"),
            "shift_key": experience.get("shift_key"),
            "role_type": PERSON_ASSIST_AUTO_ROLE_TYPE,
            "notes": _person_experience_sync_note(actor_name, str(experience.get("notes") or "")),
        },
        existing=existing,
        actor_name=actor_name,
    )
    record["sync_source"] = _person_experience_sync_source(person_project, experience)
    changed = False
    if existing:
        index = assist["records"].index(existing)
        if assist["records"][index] != record:
            assist["records"][index] = record
            changed = True
    else:
        assist["records"].append(record)
        changed = True
    if not changed:
        return False
    _save_project(scene_project)
    _append_history(
        scene_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "system",
            "action": "assist_record_saved",
            "month_key": None,
            "changes": [f"person 経験済現場から自動実績登録: {_assist_record_history_label(record)}"],
        },
    )
    return True


def _resync_person_experience_targets(person_project_id: str, experience_id: str, actor_name: str) -> None:
    person_project = _load_project(person_project_id)
    experience = (
        _person_experience_source_item(person_project, experience_id)
        if isinstance(person_project, dict)
        else None
    )
    experience_site_name = str(experience.get("site_name") or "").strip() if experience else ""
    for scene_project in _iter_stored_projects():
        if not scene_project or scene_project.get("mode") != "scene":
            continue
        scene_project_id = str(scene_project.get("id") or "")
        with _project_lock(scene_project_id):
            scene_project = _load_project(scene_project_id)
            if scene_project.get("mode") != "scene":
                continue
            title_matches = (
                bool(experience)
                and str(scene_project.get("title") or "").strip() == experience_site_name
            )
            if title_matches:
                _upsert_person_experience_synced_record(
                    scene_project,
                    person_project,
                    experience,
                    actor_name=actor_name,
                )
            else:
                _remove_person_experience_synced_records(
                    scene_project,
                    person_project_id,
                    experience_id,
                    actor_name=actor_name,
                )


def _sync_scene_project_from_person_experiences(scene_project_id: str, actor_name: str) -> None:
    with _project_lock(scene_project_id):
        scene_project = _load_project(scene_project_id)
        if scene_project.get("mode") != "scene":
            return
        target_title = str(scene_project.get("title") or "").strip()
        for person_project in _iter_stored_projects():
            if not person_project or person_project.get("mode") != "person":
                continue
            assist = _ensure_person_assist(person_project)
            for experience in assist.get("experienced_sites") or []:
                if str(experience.get("site_name") or "").strip() != target_title:
                    continue
                _upsert_person_experience_synced_record(
                    scene_project,
                    person_project,
                    experience,
                    actor_name=actor_name,
                )


def _resync_person_project_experiences(person_project_id: str, actor_name: str) -> None:
    person_project = _load_json(_project_path(person_project_id))
    if not person_project or person_project.get("mode") != "person":
        return
    assist = _ensure_person_assist(person_project)
    for experience in assist.get("experienced_sites") or []:
        _resync_person_experience_targets(
            str(person_project.get("id") or ""),
            str(experience.get("id") or ""),
            actor_name,
        )


def _assist_profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    preferred_weekdays = _assist_weekday_values(profile.get("preferred_weekdays"), "希望曜日")
    blocked_weekdays = _assist_weekday_values(profile.get("blocked_weekdays"), "NG曜日")
    return {
        "id": str(profile.get("id") or ""),
        "name": str(profile.get("name") or ""),
        "employee_number": str(profile.get("employee_number") or ""),
        "aliases": [str(item).strip() for item in (profile.get("aliases") or []) if str(item).strip()],
        "active": bool(profile.get("active", True)),
        "notes": str(profile.get("notes") or ""),
        "preferred_weekdays": preferred_weekdays,
        "preferred_weekday_labels": [_assist_weekday_label(item) for item in preferred_weekdays],
        "blocked_weekdays": blocked_weekdays,
        "blocked_weekday_labels": [_assist_weekday_label(item) for item in blocked_weekdays],
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
    }


def _assist_record_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "date": str(record.get("date") or ""),
        "weekday": int(record.get("weekday", 0) or 0),
        "weekday_label": _assist_weekday_label(int(record.get("weekday", 0) or 0)),
        "candidate_id": str(record.get("candidate_id") or ""),
        "candidate_name": str(record.get("candidate_name") or ""),
        "employee_number": str(record.get("employee_number") or ""),
        "shift_key": str(record.get("shift_key") or ""),
        "shift_label": _assist_shift_label(record.get("shift_key")),
        "role_type": str(record.get("role_type") or "normal"),
        "role_label": ASSIST_ROLE_LABELS.get(str(record.get("role_type") or "normal"), "通常"),
        "notes": str(record.get("notes") or ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "created_by": record.get("created_by"),
        "updated_by": record.get("updated_by"),
    }


def _assist_rule_payload(rule: dict[str, Any]) -> dict[str, Any]:
    weekday = _assist_rule_weekday_value(rule.get("weekday"))
    assignments = []
    for item in (rule.get("assignments") or []):
        if not isinstance(item, dict):
            continue
        role_type = str(item.get("role_type") or "normal")
        assignments.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "candidate_name": str(item.get("candidate_name") or ""),
                "employee_number": str(item.get("employee_number") or ""),
                "role_type": role_type,
                "role_label": ASSIST_ROLE_LABELS.get(role_type, "通常"),
                "priority": int(item.get("priority", 1) or 1),
                "custom_points": int(item.get("custom_points", 0) or 0),
            }
        )
    assignments.sort(
        key=lambda item: (
            -_assist_rule_points(item["role_type"], item["priority"], int(item.get("custom_points", 0) or 0)),
            item["priority"],
            item["candidate_name"],
        )
    )
    return {
        "id": str(rule.get("id") or ""),
        "weekday": weekday,
        "weekday_label": _assist_rule_weekday_label(weekday),
        "shift_key": str(rule.get("shift_key") or ""),
        "shift_label": _assist_shift_label(rule.get("shift_key")),
        "enabled": bool(rule.get("enabled", True)),
        "notes": str(rule.get("notes") or ""),
        "effective_from": rule.get("effective_from"),
        "effective_to": rule.get("effective_to"),
        "assignments": assignments,
        "created_at": rule.get("created_at"),
        "updated_at": rule.get("updated_at"),
        "created_by": rule.get("created_by"),
        "updated_by": rule.get("updated_by"),
        "source_type": str(rule.get("source_type") or ""),
        "source_contract_code": str(rule.get("source_contract_code") or ""),
        "source_site_branch": str(rule.get("source_site_branch") or ""),
        "source_site_row_id": _coerce_site_row_id(rule.get("source_site_row_id")),
    }


def _assist_bootstrap_payload(project: dict[str, Any], *, can_edit_records: bool, can_edit_rules: bool) -> dict[str, Any]:
    assist = _ensure_assist(project)
    profiles = [_assist_profile_payload(item) for item in assist["profiles"]]
    records = [_assist_record_payload(item) for item in assist["records"]]
    rules = [_assist_rule_payload(item) for item in assist["rules"]]
    experienced_sites = [_person_assist_site_payload(item) for item in assist["experienced_sites"]]
    training_sites = [_person_assist_site_payload(item) for item in assist["training_sites"]]
    profiles.sort(key=lambda item: (not item["active"], item["name"], item["employee_number"]))
    records.sort(key=lambda item: (item["date"], item["shift_key"], item["candidate_name"]), reverse=True)
    rules.sort(
        key=lambda item: (
            item["weekday"] is None,
            item["weekday"] if item["weekday"] is not None else 99,
            item["shift_key"],
            item["id"],
        )
    )
    experienced_sites.sort(key=lambda item: (item["date"], item["site_name"]), reverse=True)
    training_sites.sort(key=lambda item: (item["date"], item["site_name"]), reverse=True)
    return {
        "success": True,
        "assist": {
            "version": assist["version"],
            "profiles": profiles,
            "records": records,
            "rules": rules,
            "experienced_sites": experienced_sites,
            "training_sites": training_sites,
        },
        "permissions": {
            "can_edit_records": bool(can_edit_records),
            "can_edit_rules": bool(can_edit_rules),
            "can_edit_profiles": bool(can_edit_rules),
        },
    }


def _find_assist_profile(
    assist: dict[str, Any],
    *,
    candidate_id: str = "",
    employee_number: str = "",
    candidate_name: str = "",
) -> dict[str, Any] | None:
    profiles = assist.get("profiles") or []
    normalized_id = str(candidate_id or "").strip()
    normalized_number = str(employee_number or "").strip()
    normalized_name = str(candidate_name or "").strip()
    if normalized_id:
        for profile in profiles:
            if str(profile.get("id") or "") == normalized_id:
                return profile
    if normalized_number:
        for profile in profiles:
            if str(profile.get("employee_number") or "") == normalized_number:
                return profile
    if normalized_name:
        for profile in profiles:
            if str(profile.get("name") or "") == normalized_name and not str(profile.get("employee_number") or ""):
                return profile
    return None


def _upsert_assist_profile(
    assist: dict[str, Any],
    *,
    candidate_id: str = "",
    candidate_name: str,
    employee_number: str = "",
) -> dict[str, Any]:
    name = _assist_short_text(candidate_name, "候補者名", required=True, limit=80)
    number = _sanitize_employee_number(employee_number)
    profile = _find_assist_profile(
        assist,
        candidate_id=candidate_id,
        employee_number=number,
        candidate_name=name,
    )
    timestamp = _utcnow_iso()
    if profile:
        profile["name"] = name
        if number:
            profile["employee_number"] = number
        profile["updated_at"] = timestamp
        profile["active"] = True
        profile["preferred_weekdays"] = _assist_weekday_values(profile.get("preferred_weekdays"), "希望曜日")
        profile["blocked_weekdays"] = _assist_weekday_values(profile.get("blocked_weekdays"), "NG曜日")
        return profile
    created = {
        "id": _assist_id("cand"),
        "name": name,
        "employee_number": number,
        "aliases": [],
        "active": True,
        "notes": "",
        "preferred_weekdays": [],
        "blocked_weekdays": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    assist["profiles"].append(created)
    return created


def _assist_profile_history_label(profile: dict[str, Any]) -> str:
    label = str(profile.get("name") or "-")
    employee_number = str(profile.get("employee_number") or "").strip()
    if employee_number:
        label = f"{label} / {employee_number}"
    return label


def _assist_profile_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor_name: str,
    actor_type: str,
    profile_id: str,
) -> dict[str, Any]:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    existing = next((item for item in assist["profiles"] if str(item.get("id") or "") == profile_id), None)
    if not existing:
        raise CloudShiftError("対象の候補者プロファイルが見つかりません", 404)
    preferred_weekdays = _assist_weekday_values(payload.get("preferred_weekdays"), "希望曜日")
    blocked_weekdays = _assist_weekday_values(payload.get("blocked_weekdays"), "NG曜日")
    duplicated = sorted(set(preferred_weekdays) & set(blocked_weekdays))
    if duplicated:
        duplicated_labels = " / ".join(_assist_weekday_label(item) for item in duplicated)
        raise CloudShiftError(f"希望曜日とNG曜日が重複しています: {duplicated_labels}", 400)
    existing["active"] = _assist_bool(payload.get("active"), bool(existing.get("active", True)))
    existing["preferred_weekdays"] = preferred_weekdays
    existing["blocked_weekdays"] = blocked_weekdays
    existing["updated_at"] = _utcnow_iso()
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "assist_profile_saved",
            "month_key": None,
            "changes": [f"候補者プロファイルを更新: {_assist_profile_history_label(existing)}"],
        },
    )
    return existing


def _assist_record_from_payload(
    assist: dict[str, Any],
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    actor_name: str,
) -> dict[str, Any]:
    date_text, parsed_date = _assist_date_parts(payload.get("date"))
    shift_key = _assist_shift_key(payload.get("shift_key"), required=False)
    role_type = _assist_role_type(payload.get("role_type") or "normal")
    notes = _assist_long_text(payload.get("notes"))
    profile = _upsert_assist_profile(
        assist,
        candidate_id=str(payload.get("candidate_id") or ""),
        candidate_name=payload.get("candidate_name"),
        employee_number=payload.get("employee_number"),
    )
    timestamp = _utcnow_iso()
    if existing:
        created_at = existing.get("created_at", timestamp)
        created_by = existing.get("created_by", actor_name)
        record_id = str(existing.get("id") or _assist_id("rec"))
    else:
        created_at = timestamp
        created_by = actor_name
        record_id = _assist_id("rec")
    return {
        "id": record_id,
        "date": date_text,
        "weekday": parsed_date.weekday(),
        "candidate_id": profile["id"],
        "candidate_name": profile["name"],
        "employee_number": profile.get("employee_number", ""),
        "shift_key": shift_key,
        "role_type": role_type,
        "notes": notes,
        "created_at": created_at,
        "updated_at": timestamp,
        "created_by": created_by,
        "updated_by": actor_name,
    }


def _assist_rule_assignments_from_payload(assist: dict[str, Any], payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else []
    assignments = []
    for item in items:
        if not isinstance(item, dict):
            continue
        profile = _upsert_assist_profile(
            assist,
            candidate_id=str(item.get("candidate_id") or ""),
            candidate_name=item.get("candidate_name"),
            employee_number=item.get("employee_number"),
        )
        assignments.append(
            {
                "candidate_id": profile["id"],
                "candidate_name": profile["name"],
                "employee_number": profile.get("employee_number", ""),
                "role_type": _assist_role_type(item.get("role_type") or "normal"),
                "priority": _assist_priority(item.get("priority") or 1),
                "custom_points": _assist_custom_points(item.get("custom_points")),
            }
        )
    if not assignments:
        raise CloudShiftError("ルール候補を1件以上入力してください", 400)
    assignments.sort(
        key=lambda item: (
            -_assist_rule_points(item["role_type"], item["priority"], int(item.get("custom_points", 0) or 0)),
            item["priority"],
            item["candidate_name"],
        )
    )
    return assignments


def _assist_rule_from_payload(
    assist: dict[str, Any],
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    actor_name: str,
) -> dict[str, Any]:
    weekday = _assist_rule_weekday_value(payload.get("weekday"))
    shift_key = _assist_shift_key(payload.get("shift_key"), required=False)
    notes = _assist_long_text(payload.get("notes"))
    effective_from = _assist_period_value(payload.get("effective_from"))
    effective_to = _assist_period_value(payload.get("effective_to"))
    if effective_from and effective_to and effective_from > effective_to:
        raise CloudShiftError("ルールの適用期間が不正です", 400)
    timestamp = _utcnow_iso()
    if existing:
        created_at = existing.get("created_at", timestamp)
        created_by = existing.get("created_by", actor_name)
        rule_id = str(existing.get("id") or _assist_id("rule"))
    else:
        created_at = timestamp
        created_by = actor_name
        rule_id = _assist_id("rule")
    return {
        "id": rule_id,
        "weekday": weekday,
        "shift_key": shift_key,
        "enabled": bool(payload.get("enabled", True)),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "assignments": _assist_rule_assignments_from_payload(assist, payload.get("assignments")),
        "notes": notes,
        "created_at": created_at,
        "updated_at": timestamp,
        "created_by": created_by,
        "updated_by": actor_name,
    }


def _assist_match_scope(target_shift_key: Any, source_shift_key: Any) -> str:
    return "exact" if str(target_shift_key or "").strip().upper() == str(source_shift_key or "").strip().upper() else "weekday"


def _assist_rule_priority_bonus(priority: int, match_scope: str = "exact") -> int:
    if match_scope == "weekday":
        return max(0, 20 - ((priority - 1) * 3))
    return max(0, 40 - ((priority - 1) * 5))


def _assist_rule_points(role_type: str, priority: int, custom_points: int = 0, match_scope: str = "exact") -> int:
    base_points = ASSIST_ROLE_SCORES.get(role_type, 0)
    if match_scope == "weekday":
        return base_points + _assist_rule_priority_bonus(priority, match_scope) + custom_points
    return (base_points * 3) + _assist_rule_priority_bonus(priority, match_scope) + custom_points


def _assist_record_recency_bonus(days_ago: int, match_scope: str = "exact") -> int:
    if match_scope == "other_weekday":
        if days_ago <= 30:
            return 10
        if days_ago <= 90:
            return 5
        return 0
    if match_scope == "weekday":
        if days_ago <= 30:
            return 15
        if days_ago <= 90:
            return 10
        if days_ago <= 180:
            return 5
        return 0
    if days_ago <= 30:
        return 30
    if days_ago <= 90:
        return 20
    if days_ago <= 180:
        return 10
    return 0


def _assist_record_points(role_type: str, days_ago: int, match_scope: str = "exact") -> int:
    base_points = ASSIST_ROLE_SCORES.get(role_type, 0)
    if match_scope == "other_weekday":
        return max(10, base_points // 4) + _assist_record_recency_bonus(days_ago, match_scope)
    if match_scope == "weekday":
        return max(20, base_points // 2) + _assist_record_recency_bonus(days_ago, match_scope)
    return base_points + _assist_record_recency_bonus(days_ago, match_scope)


def _assist_record_history_label(record: dict[str, Any]) -> str:
    return (
        f"{record.get('date')}({_assist_weekday_label(int(record.get('weekday', 0) or 0))}) / "
        f"{_assist_shift_label(record.get('shift_key'))} / "
        f"{record.get('candidate_name')} / "
        f"{ASSIST_ROLE_LABELS.get(str(record.get('role_type') or 'normal'), '通常')}"
    )


def _assist_rule_history_label(rule: dict[str, Any]) -> str:
    weekday = _assist_rule_weekday_value(rule.get("weekday"))
    weekday_label = _assist_rule_weekday_label(weekday)
    return (
        f"{weekday_label if weekday is None else f'{weekday_label}曜日'} / "
        f"{_assist_shift_label(rule.get('shift_key'))}"
    )


def _assist_record_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor_name: str,
    actor_type: str,
    record_id: str | None = None,
) -> dict[str, Any]:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    existing = None
    if record_id:
        existing = next((item for item in assist["records"] if str(item.get("id") or "") == record_id), None)
        if not existing:
            raise CloudShiftError("対象の実績が見つかりません", 404)
    record = _assist_record_from_payload(assist, payload, existing=existing, actor_name=actor_name)
    if existing:
        index = assist["records"].index(existing)
        assist["records"][index] = record
        changes = [f"アシスト実績を更新: {_assist_record_history_label(record)}"]
    else:
        assist["records"].append(record)
        changes = [f"アシスト実績を登録: {_assist_record_history_label(record)}"]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "assist_record_saved",
            "month_key": None,
            "changes": changes,
        },
    )
    return record


def _assist_record_delete(project: dict[str, Any], record_id: str, *, actor_name: str, actor_type: str) -> None:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    existing = next((item for item in assist["records"] if str(item.get("id") or "") == record_id), None)
    if not existing:
        raise CloudShiftError("対象の実績が見つかりません", 404)
    assist["records"] = [item for item in assist["records"] if str(item.get("id") or "") != record_id]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "assist_record_deleted",
            "month_key": None,
            "changes": [f"アシスト実績を削除: {_assist_record_history_label(existing)}"],
        },
    )


def _assist_rule_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor_name: str,
    actor_type: str,
    rule_id: str | None = None,
) -> dict[str, Any]:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    existing = None
    if rule_id:
        existing = next((item for item in assist["rules"] if str(item.get("id") or "") == rule_id), None)
        if not existing:
            raise CloudShiftError("対象のルールが見つかりません", 404)
    rule = _assist_rule_from_payload(assist, payload, existing=existing, actor_name=actor_name)
    if existing:
        index = assist["rules"].index(existing)
        assist["rules"][index] = rule
        changes = [f"シフトルールを更新: {_assist_rule_history_label(rule)}"]
    else:
        assist["rules"].append(rule)
        changes = [f"シフトルールを登録: {_assist_rule_history_label(rule)}"]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "assist_rule_saved",
            "month_key": None,
            "changes": changes,
        },
    )
    return rule


def _assist_rule_delete(project: dict[str, Any], rule_id: str, *, actor_name: str, actor_type: str) -> None:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    existing = next((item for item in assist["rules"] if str(item.get("id") or "") == rule_id), None)
    if not existing:
        raise CloudShiftError("対象のルールが見つかりません", 404)
    assist["rules"] = [item for item in assist["rules"] if str(item.get("id") or "") != rule_id]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "assist_rule_deleted",
            "month_key": None,
            "changes": [f"シフトルールを削除: {_assist_rule_history_label(existing)}"],
        },
    )


def _person_assist_site_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    kind: str,
    actor_name: str,
    actor_type: str,
    site_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _ensure_person_project(project)
    assist = _ensure_assist(project)
    key = _person_assist_kind_key(kind)
    existing = None
    previous = None
    if site_id:
        existing = next((item for item in assist[key] if str(item.get("id") or "") == site_id), None)
        if not existing:
            raise CloudShiftError(f"対象の{_person_assist_kind_label(kind)}が見つかりません", 404)
        previous = copy(existing)
    site = _person_assist_site_from_payload(payload, existing=existing, actor_name=actor_name)
    if existing:
        index = assist[key].index(existing)
        assist[key][index] = site
        changes = [f"{_person_assist_kind_label(kind)}を更新: {_person_assist_site_history_label(site)}"]
    else:
        assist[key].append(site)
        changes = [f"{_person_assist_kind_label(kind)}を登録: {_person_assist_site_history_label(site)}"]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": f"person_assist_{kind}_saved",
            "month_key": None,
            "changes": changes,
        },
    )
    return site, previous


def _person_assist_site_delete(
    project: dict[str, Any],
    site_id: str,
    *,
    kind: str,
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    _ensure_person_project(project)
    assist = _ensure_assist(project)
    key = _person_assist_kind_key(kind)
    existing = next((item for item in assist[key] if str(item.get("id") or "") == site_id), None)
    if not existing:
        raise CloudShiftError(f"対象の{_person_assist_kind_label(kind)}が見つかりません", 404)
    assist[key] = [item for item in assist[key] if str(item.get("id") or "") != site_id]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": f"person_assist_{kind}_deleted",
            "month_key": None,
            "changes": [f"{_person_assist_kind_label(kind)}を削除: {_person_assist_site_history_label(existing)}"],
        },
    )
    return existing


def _find_auto_scene_assist_record(
    assist: dict[str, Any], *, source_project_id: str, source_site_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in (assist.get("records") or [])
            if str(item.get("source_type") or "") == "person_experience"
            and str(item.get("source_project_id") or "") == source_project_id
            and str(item.get("source_site_id") or "") == source_site_id
        ),
        None,
    )


def _person_experience_scene_notes(
    source_project: dict[str, Any],
    site: dict[str, Any],
    *,
    actor_name: str,
    target_project: dict[str, Any],
) -> str:
    parts: list[str] = [_person_assist_op_label(site.get("has_op"))]
    notes = str(site.get("notes") or "").strip()
    if notes:
        parts.append(notes)
    if str(target_project.get("owner_user_id") or "") != str(source_project.get("owner_user_id") or ""):
        parts.append(f"{actor_name} からの自動実績登録")
    return "\n".join(part for part in parts if part)


def _upsert_person_experience_scene_record(
    target_project: dict[str, Any],
    source_project: dict[str, Any],
    site: dict[str, Any],
    *,
    actor_name: str,
) -> str | None:
    assist = _ensure_assist(target_project)
    existing = _find_auto_scene_assist_record(
        assist,
        source_project_id=str(source_project.get("id") or ""),
        source_site_id=str(site.get("id") or ""),
    )
    record = _assist_record_from_payload(
        assist,
        {
            "date": _person_experience_available_from(str(site.get("date") or "")),
            "candidate_name": source_project.get("title"),
            "employee_number": source_project.get("employee_number"),
            "shift_key": "",
            "role_type": "backup",
            "notes": _person_experience_scene_notes(
                source_project,
                site,
                actor_name=actor_name,
                target_project=target_project,
            ),
        },
        existing=existing,
        actor_name=actor_name,
    )
    record["source_type"] = "person_experience"
    record["source_project_id"] = str(source_project.get("id") or "")
    record["source_site_id"] = str(site.get("id") or "")
    record["source_site_name"] = str(site.get("site_name") or "")
    record["source_date"] = str(site.get("date") or "")
    record["source_has_op"] = bool(site.get("has_op", False))
    record["source_available_from"] = _person_experience_available_from(str(site.get("date") or ""))
    if existing:
        index = assist["records"].index(existing)
        assist["records"][index] = record
        return f"person経験済現場から自動実績を更新: {_assist_record_history_label(record)}"
    assist["records"].append(record)
    return f"person経験済現場から自動実績を登録: {_assist_record_history_label(record)}"


def _remove_person_experience_scene_record(
    target_project: dict[str, Any],
    *,
    source_project_id: str,
    source_site_id: str,
) -> str | None:
    assist = _ensure_assist(target_project)
    existing = _find_auto_scene_assist_record(
        assist,
        source_project_id=source_project_id,
        source_site_id=source_site_id,
    )
    if not existing:
        return None
    assist["records"] = [
        item
        for item in assist["records"]
        if str(item.get("id") or "") != str(existing.get("id") or "")
    ]
    return f"person経験済現場の自動実績を削除: {_assist_record_history_label(existing)}"


def _person_experience_matches_scene_project(site: dict[str, Any], scene_project: dict[str, Any]) -> bool:
    site_row_id = _coerce_site_row_id(site.get("site_row_id"))
    scene_site_row_id = _coerce_site_row_id(scene_project.get("site_row_id"))
    if site_row_id and scene_site_row_id:
        return site_row_id == scene_site_row_id

    site_contract_id = str(site.get("site_id") or "").strip()
    scene_contract_id = str(scene_project.get("site_id") or "").strip()
    if site_contract_id and scene_contract_id:
        return site_contract_id == scene_contract_id

    normalized_site = _normalized_site_title(site.get("site_name"))
    if not normalized_site:
        return False

    scene_site_name = str(scene_project.get("site_name") or "").strip()
    if scene_site_name:
        return _normalized_site_title(scene_site_name) == normalized_site

    return _normalized_site_title(scene_project.get("title")) == normalized_site


def _sync_person_experience_to_scene_projects(
    source_project: dict[str, Any],
    site: dict[str, Any],
    *,
    actor_name: str,
) -> None:
    normalized_site = _normalized_site_title(site.get("site_name"))
    for target_summary in _iter_stored_projects():
        if not target_summary or target_summary.get("mode") != "scene":
            continue
        target_project_id = str(target_summary.get("id") or "")
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            if target_project.get("mode") != "scene":
                continue
            if _normalized_site_title(target_project.get("title")) == normalized_site:
                change = _upsert_person_experience_scene_record(
                    target_project,
                    source_project,
                    site,
                    actor_name=actor_name,
                )
            else:
                change = _remove_person_experience_scene_record(
                    target_project,
                    source_project_id=str(source_project.get("id") or ""),
                    source_site_id=str(site.get("id") or ""),
                )
            if not change:
                continue
            _save_project(target_project)
            _append_history(
                target_project["id"],
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": actor_name,
                    "editor_type": "auto",
                    "action": "person_experience_sync",
                    "month_key": None,
                    "changes": [change],
                },
            )


def _delete_person_experience_from_scene_projects(
    source_project_id: str,
    source_site_id: str,
    *,
    actor_name: str,
) -> None:
    for target_summary in _iter_stored_projects():
        if not target_summary or target_summary.get("mode") != "scene":
            continue
        target_project_id = str(target_summary.get("id") or "")
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            if target_project.get("mode") != "scene":
                continue
            change = _remove_person_experience_scene_record(
                target_project,
                source_project_id=source_project_id,
                source_site_id=source_site_id,
            )
            if not change:
                continue
            _save_project(target_project)
            _append_history(
                target_project["id"],
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": actor_name,
                    "editor_type": "auto",
                    "action": "person_experience_sync_deleted",
                    "month_key": None,
                    "changes": [change],
                },
            )


def _resync_person_experience_project(source_project: dict[str, Any], *, actor_name: str) -> None:
    _ensure_person_project(source_project)
    assist = _ensure_assist(source_project)
    for site in assist.get("experienced_sites") or []:
        _sync_person_experience_to_scene_projects(source_project, site, actor_name=actor_name)


def _backfill_scene_project_from_person_experience(
    scene_project: dict[str, Any], *, actor_name: str
) -> None:
    _ensure_scene_project(scene_project)
    normalized_title = _normalized_site_title(scene_project.get("title"))
    if not normalized_title:
        return
    changes: list[str] = []
    for source_project in _iter_stored_projects():
        if not source_project or source_project.get("mode") != "person":
            continue
        source_assist = _ensure_assist(source_project)
        for site in source_assist.get("experienced_sites") or []:
            if _normalized_site_title(site.get("site_name")) != normalized_title:
                continue
            change = _upsert_person_experience_scene_record(
                scene_project,
                source_project,
                site,
                actor_name=actor_name,
            )
            if change:
                changes.append(change)
    if not changes:
        return
    _save_project(scene_project)
    _append_history(
        scene_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "auto",
            "action": "person_experience_backfill",
            "month_key": None,
            "changes": changes[:100],
        },
    )


def _person_assist_collection_key(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    key = PERSON_ASSIST_COLLECTION_KEYS.get(normalized)
    if not key:
        raise CloudShiftError("person assist 種別が不正です", 400)
    return key


def _person_assist_kind_label(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    return PERSON_ASSIST_KIND_LABELS.get(normalized, normalized or "person assist")


def _ensure_person_assist(project: dict[str, Any]) -> dict[str, Any]:
    assist = _ensure_assist(project)
    assist["experienced_sites"] = [
        item for item in (assist.get("experienced_sites") or []) if isinstance(item, dict)
    ]
    assist["training_sites"] = [
        item for item in (assist.get("training_sites") or []) if isinstance(item, dict)
    ]
    return assist


def _person_assist_site_payload(item: dict[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    site_type = str(kind or item.get("kind") or PERSON_ASSIST_EXPERIENCE_KIND).strip().lower()
    if site_type not in PERSON_ASSIST_COLLECTION_KEYS:
        site_type = PERSON_ASSIST_EXPERIENCE_KIND
    site_link = _linked_site_snapshot_payload(
        item.get("site_row_id"),
        item.get("site_id"),
        item.get("site_name"),
    )
    return {
        "id": str(item.get("id") or ""),
        "site_type": site_type,
        "kind": site_type,
        "kind_label": _person_assist_kind_label(site_type),
        "date": str(item.get("date") or ""),
        "effective_from": str(item.get("effective_from") or ""),
        "site_row_id": site_link["site_row_id"],
        "site_id": site_link["site_id"],
        "site_name": site_link["site_name"],
        "site": site_link,
        "shift_key": str(item.get("shift_key") or ""),
        "shift_label": _assist_shift_label(item.get("shift_key")),
        "notes": str(item.get("notes") or ""),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "created_by": item.get("created_by"),
        "updated_by": item.get("updated_by"),
    }


def _person_assist_bootstrap_payload(project: dict[str, Any], *, can_edit_sites: bool = True) -> dict[str, Any]:
    assist = _ensure_person_assist(project)
    experienced = [
        _person_assist_site_payload(item, kind=PERSON_ASSIST_EXPERIENCE_KIND)
        for item in assist["experienced_sites"]
    ]
    training = [
        _person_assist_site_payload(item, kind=PERSON_ASSIST_TRAINING_KIND)
        for item in assist["training_sites"]
    ]
    experienced.sort(key=lambda item: (item["date"], item["site_name"], item["shift_key"]), reverse=True)
    training.sort(key=lambda item: (item["date"], item["site_name"], item["shift_key"]), reverse=True)
    return {
        "success": True,
        "assist_mode": "person",
        "assist": {
            "version": assist["version"],
            "experienced_sites": experienced,
            "training_sites": training,
        },
        "permissions": {
            "can_edit_sites": bool(can_edit_sites),
            "can_edit_experienced": bool(can_edit_sites),
            "can_edit_training": bool(can_edit_sites),
        },
    }


def _person_assist_site_from_payload(
    payload: dict[str, Any],
    *,
    kind: str | None = None,
    existing: dict[str, Any] | None = None,
    actor_name: str,
) -> dict[str, Any]:
    site_type = str(kind or payload.get("site_type") or (existing or {}).get("kind") or "").strip().lower()
    if not site_type:
        raise CloudShiftError("person assist 種別を指定してください", 400)
    collection_key = _person_assist_collection_key(site_type)
    date_text, parsed_date = _assist_date_parts(payload.get("date"))
    timestamp = _utcnow_iso()
    if existing:
        created_at = existing.get("created_at", timestamp)
        created_by = existing.get("created_by", actor_name)
        site_id = str(existing.get("id") or _assist_id("psite"))
    else:
        created_at = timestamp
        created_by = actor_name
        site_id = _assist_id("psite")
    site_ref = _load_site_reference(_sanitize_site_row_id(payload.get("site_row_id")), require_active=True)
    site_name = (
        site_ref["site_name"]
        if site_ref
        else _assist_short_text(payload.get("site_name"), "現場名", required=True, limit=120)
    )
    return {
        "id": site_id,
        "kind": PERSON_ASSIST_EXPERIENCE_KIND
        if collection_key == "experienced_sites"
        else PERSON_ASSIST_TRAINING_KIND,
        "date": date_text,
        "effective_from": (parsed_date + timedelta(days=1)).isoformat(),
        "site_row_id": site_ref["site_row_id"] if site_ref else None,
        "site_id": site_ref["site_id"] if site_ref else str(payload.get("site_id") or "").strip(),
        "site_name": site_name,
        "shift_key": _assist_shift_key(payload.get("shift_key"), required=False),
        "notes": _assist_long_text(payload.get("notes")),
        "created_at": created_at,
        "updated_at": timestamp,
        "created_by": created_by,
        "updated_by": actor_name,
    }


def _person_assist_site_history_label(item: dict[str, Any]) -> str:
    site_label = (
        f"{item.get('site_id')} / {item.get('site_name')}"
        if str(item.get("site_id") or "").strip()
        else str(item.get("site_name") or "")
    )
    return f"{item.get('date')} / {site_label} / {_assist_shift_label(item.get('shift_key'))}"


def _person_assist_site_mutation(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    kind: str | None = None,
    actor_name: str,
    actor_type: str,
    site_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _ensure_person_project(project)
    assist = _ensure_person_assist(project)
    existing = None
    existing_key = ""
    previous = None
    if site_id:
        for key in ("experienced_sites", "training_sites"):
            existing = next((item for item in assist[key] if str(item.get("id") or "") == str(site_id or "")), None)
            if existing:
                existing_key = key
                previous = copy(existing)
                break
        if not existing:
            raise CloudShiftError("対象の現場メモが見つかりません", 404)
    site = _person_assist_site_from_payload(payload, kind=kind, existing=existing, actor_name=actor_name)
    target_key = _person_assist_collection_key(site.get("kind"))
    if existing and existing_key == target_key:
        index = assist[target_key].index(existing)
        assist[target_key][index] = site
    else:
        if existing and existing_key:
            assist[existing_key] = [
                item for item in assist[existing_key] if str(item.get("id") or "") != str(existing.get("id") or "")
            ]
        assist[target_key].append(site)
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "person_assist_site_saved",
            "month_key": None,
            "changes": [
                f"{_person_assist_kind_label(site.get('kind'))}を更新: {_person_assist_site_history_label(site)}"
                if existing
                else f"{_person_assist_kind_label(site.get('kind'))}を登録: {_person_assist_site_history_label(site)}"
            ],
        },
    )
    return site, previous


def _person_assist_site_delete(
    project: dict[str, Any],
    site_id: str,
    *,
    kind: str | None = None,
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    _ensure_person_project(project)
    assist = _ensure_person_assist(project)
    search_keys = [_person_assist_collection_key(kind)] if kind else ["experienced_sites", "training_sites"]
    existing = None
    existing_key = ""
    for key in search_keys:
        existing = next((item for item in assist[key] if str(item.get("id") or "") == str(site_id or "")), None)
        if existing:
            existing_key = key
            break
    if not existing or not existing_key:
        raise CloudShiftError("対象の現場メモが見つかりません", 404)
    assist[existing_key] = [item for item in assist[existing_key] if str(item.get("id") or "") != str(site_id or "")]
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "person_assist_site_deleted",
            "month_key": None,
            "changes": [f"{_person_assist_kind_label(existing.get('kind'))}を削除: {_person_assist_site_history_label(existing)}"],
        },
    )
    return copy(existing)


def _find_auto_scene_assist_record(
    assist: dict[str, Any], *, source_project_id: str, source_site_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in (assist.get("records") or [])
            if str(item.get("source_type") or "") == PERSON_ASSIST_AUTO_SOURCE
            and str(item.get("source_project_id") or "") == str(source_project_id or "")
            and str(item.get("source_site_id") or "") == str(source_site_id or "")
        ),
        None,
    )


def _person_experience_scene_notes(
    source_project: dict[str, Any], site: dict[str, Any], *, target_project: dict[str, Any]
) -> str:
    parts: list[str] = []
    notes = str(site.get("notes") or "").strip()
    if notes:
        parts.append(notes)
    if str(target_project.get("owner_user_id") or "") != str(source_project.get("owner_user_id") or ""):
        user_label = str(source_project.get("title") or "").strip() or "ユーザー"
        parts.append(f"{user_label} からの自動実績登録")
    return "\n".join(parts)


def _upsert_person_experience_scene_record(
    target_project: dict[str, Any],
    source_project: dict[str, Any],
    site: dict[str, Any],
    *,
    actor_name: str,
) -> str | None:
    assist = _ensure_assist(target_project)
    existing = _find_auto_scene_assist_record(
        assist,
        source_project_id=str(source_project.get("id") or ""),
        source_site_id=str(site.get("id") or ""),
    )
    record = _assist_record_from_payload(
        assist,
        {
            "date": str(site.get("effective_from") or _person_experience_available_from(str(site.get("date") or ""))),
            "candidate_name": source_project.get("title"),
            "employee_number": source_project.get("employee_number"),
            "shift_key": site.get("shift_key"),
            "role_type": PERSON_ASSIST_AUTO_ROLE_TYPE,
            "notes": _person_experience_scene_notes(source_project, site, target_project=target_project),
        },
        existing=existing,
        actor_name=actor_name,
    )
    record["source_type"] = PERSON_ASSIST_AUTO_SOURCE
    record["source_project_id"] = str(source_project.get("id") or "")
    record["source_site_id"] = str(site.get("id") or "")
    record["source_site_name"] = str(site.get("site_name") or "")
    record["source_date"] = str(site.get("date") or "")
    record["source_shift_key"] = str(site.get("shift_key") or "")
    record["source_available_from"] = str(record.get("date") or "")
    if existing:
        index = assist["records"].index(existing)
        if assist["records"][index] == record:
            return None
        assist["records"][index] = record
        return f"person経験済現場から自動実績を更新: {_assist_record_history_label(record)}"
    assist["records"].append(record)
    return f"person経験済現場から自動実績を登録: {_assist_record_history_label(record)}"


def _remove_person_experience_scene_record(
    target_project: dict[str, Any],
    *,
    source_project_id: str,
    source_site_id: str,
) -> str | None:
    assist = _ensure_assist(target_project)
    existing = _find_auto_scene_assist_record(
        assist,
        source_project_id=source_project_id,
        source_site_id=source_site_id,
    )
    if not existing:
        return None
    assist["records"] = [
        item
        for item in (assist.get("records") or [])
        if str(item.get("id") or "") != str(existing.get("id") or "")
    ]
    return f"person経験済現場の自動実績を削除: {_assist_record_history_label(existing)}"


def _sync_person_experience_to_scene_projects(
    source_project: dict[str, Any],
    site: dict[str, Any],
    *,
    actor_name: str,
) -> None:
    if str(site.get("kind") or "") != PERSON_ASSIST_EXPERIENCE_KIND:
        return
    if not (_coerce_site_row_id(site.get("site_row_id")) or str(site.get("site_id") or "").strip() or _normalized_site_title(site.get("site_name"))):
        return
    for target_summary in _iter_stored_projects():
        if not target_summary or target_summary.get("mode") != "scene":
            continue
        target_project_id = str(target_summary.get("id") or "")
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            if target_project.get("mode") != "scene":
                continue
            if _person_experience_matches_scene_project(site, target_project):
                change = _upsert_person_experience_scene_record(
                    target_project,
                    source_project,
                    site,
                    actor_name=actor_name,
                )
            else:
                change = _remove_person_experience_scene_record(
                    target_project,
                    source_project_id=str(source_project.get("id") or ""),
                    source_site_id=str(site.get("id") or ""),
                )
            if not change:
                continue
            _save_project(target_project)
            _append_history(
                target_project["id"],
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": actor_name,
                    "editor_type": "auto",
                    "action": "person_experience_sync",
                    "month_key": None,
                    "changes": [change],
                },
            )


def _delete_person_experience_from_scene_projects(
    source_project_id: str,
    source_site_id: str,
    *,
    actor_name: str,
) -> None:
    for target_summary in _iter_stored_projects():
        if not target_summary or target_summary.get("mode") != "scene":
            continue
        target_project_id = str(target_summary.get("id") or "")
        with _project_lock(target_project_id):
            target_project = _load_project(target_project_id)
            if target_project.get("mode") != "scene":
                continue
            change = _remove_person_experience_scene_record(
                target_project,
                source_project_id=source_project_id,
                source_site_id=source_site_id,
            )
            if not change:
                continue
            _save_project(target_project)
            _append_history(
                target_project["id"],
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": actor_name,
                    "editor_type": "auto",
                    "action": "person_experience_sync_deleted",
                    "month_key": None,
                    "changes": [change],
                },
            )


def _resync_person_experience_project(source_project: dict[str, Any], *, actor_name: str) -> None:
    _ensure_person_project(source_project)
    assist = _ensure_person_assist(source_project)
    for site in assist.get("experienced_sites") or []:
        _sync_person_experience_to_scene_projects(source_project, site, actor_name=actor_name)


def _backfill_scene_project_from_person_experience(
    scene_project: dict[str, Any], *, actor_name: str
) -> None:
    _ensure_scene_project(scene_project)
    assist = _ensure_assist(scene_project)
    changes: list[str] = []
    matched_keys: set[tuple[str, str]] = set()
    for source_project in _iter_stored_projects():
        if not source_project or source_project.get("mode") != "person":
            continue
        source_assist = _ensure_person_assist(source_project)
        for site in source_assist.get("experienced_sites") or []:
            if not _person_experience_matches_scene_project(site, scene_project):
                continue
            matched_keys.add((str(source_project.get("id") or ""), str(site.get("id") or "")))
            change = _upsert_person_experience_scene_record(
                scene_project,
                source_project,
                site,
                actor_name=actor_name,
            )
            if change:
                changes.append(change)
    for record in list(assist.get("records") or []):
        if str(record.get("source_type") or "") != PERSON_ASSIST_AUTO_SOURCE:
            continue
        source_key = (
            str(record.get("source_project_id") or ""),
            str(record.get("source_site_id") or ""),
        )
        if source_key not in matched_keys:
            change = _remove_person_experience_scene_record(
                scene_project,
                source_project_id=source_key[0],
                source_site_id=source_key[1],
            )
            if change:
                changes.append(change)
    if not changes:
        return
    _save_project(scene_project)
    _append_history(
        scene_project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": "auto",
            "action": "person_experience_backfill",
            "month_key": None,
            "changes": changes[:100],
        },
    )


def _assist_bootstrap_for_project(
    project: dict[str, Any],
    *,
    can_edit_records: bool,
    can_edit_rules: bool,
    can_edit_sites: bool,
) -> dict[str, Any]:
    if project.get("mode") == "person":
        return _person_assist_bootstrap_payload(project, can_edit_sites=can_edit_sites)
    return _assist_bootstrap_payload(project, can_edit_records=can_edit_records, can_edit_rules=can_edit_rules)


def _rule_effective_for_date(rule: dict[str, Any], target_date: date) -> bool:
    effective_from = str(rule.get("effective_from") or "").strip()
    effective_to = str(rule.get("effective_to") or "").strip()
    if effective_from:
        _, start_date = _assist_date_parts(effective_from)
        if target_date < start_date:
            return False
    if effective_to:
        _, end_date = _assist_date_parts(effective_to)
        if target_date > end_date:
            return False
    return True


def _assist_scene_conflict_entries(project: dict[str, Any], target_date: date) -> list[dict[str, str]]:
    owner_user_id = str(project.get("owner_user_id") or "").strip()
    project_id = str(project.get("id") or "").strip()
    month_key = _month_key(target_date.year, target_date.month)
    day_key = str(target_date.day)
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for other_project in _iter_stored_projects():
        if not other_project:
            continue
        if str(other_project.get("id") or "").strip() == project_id:
            continue
        if str(other_project.get("mode") or "").strip() != "scene":
            continue
        if owner_user_id and str(other_project.get("owner_user_id") or "").strip() != owner_user_id:
            continue
        month_data = (other_project.get("months") or {}).get(month_key)
        if not isinstance(month_data, dict):
            continue
        day_entries = (month_data.get("entries_per_day") or {}).get(day_key)
        if not isinstance(day_entries, list):
            continue
        project_title = str(other_project.get("title") or "").strip() or "名称未設定"
        other_project_id = str(other_project.get("id") or "").strip()
        for entry in day_entries:
            if not isinstance(entry, dict):
                continue
            shift_key, entry_name = parse_entry_value(entry.get("value") or "")
            employee_number = str(entry.get("employee_number") or "").strip()
            normalized_name = str(entry_name or "").strip()
            key = (other_project_id, str(shift_key or "").strip(), employee_number, normalized_name)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "project_id": other_project_id,
                    "project_title": project_title,
                    "shift_key": str(shift_key or "").strip(),
                    "shift_label": _assist_shift_label(shift_key),
                    "entry_name": normalized_name,
                    "employee_number": employee_number,
                }
            )
    entries.sort(
        key=lambda item: (
            item["project_title"],
            item["shift_label"],
            item["entry_name"],
            item["employee_number"],
        )
    )
    return entries


def _assist_candidate_matches_scene_entry(
    *,
    candidate_name: str,
    candidate_employee_number: str,
    entry_name: str,
    entry_employee_number: str,
) -> bool:
    normalized_candidate_number = str(candidate_employee_number or "").strip()
    normalized_entry_number = str(entry_employee_number or "").strip()
    if normalized_candidate_number and normalized_entry_number:
        return normalized_candidate_number == normalized_entry_number
    normalized_candidate_name = str(candidate_name or "").strip()
    normalized_entry_name = str(entry_name or "").strip()
    return bool(normalized_candidate_name and normalized_entry_name and normalized_candidate_name == normalized_entry_name)


def _assist_scene_conflicts_for_candidate(
    conflict_entries: list[dict[str, str]],
    *,
    shift_key: str,
    candidate_name: str,
    employee_number: str,
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    normalized_shift_key = str(shift_key or "").strip() or None
    for entry in conflict_entries:
        if not _assist_candidate_matches_scene_entry(
            candidate_name=candidate_name,
            candidate_employee_number=employee_number,
            entry_name=str(entry.get("entry_name") or ""),
            entry_employee_number=str(entry.get("employee_number") or ""),
        ):
            continue
        other_shift_key = str(entry.get("shift_key") or "").strip() or None
        if not is_duplicate_by_rules(normalized_shift_key, other_shift_key):
            continue
        key = (
            str(entry.get("project_id") or "").strip(),
            str(other_shift_key or ""),
            str(entry.get("employee_number") or "").strip(),
            str(entry.get("entry_name") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        conflicts.append(
            {
                "project_id": str(entry.get("project_id") or "").strip(),
                "project_title": str(entry.get("project_title") or "").strip(),
                "shift_key": str(other_shift_key or ""),
                "shift_label": str(entry.get("shift_label") or _assist_shift_label(other_shift_key)),
                "entry_name": str(entry.get("entry_name") or "").strip(),
                "employee_number": str(entry.get("employee_number") or "").strip(),
            }
        )
    conflicts.sort(
        key=lambda item: (
            item["project_title"],
            item["shift_label"],
            item["entry_name"],
            item["employee_number"],
        )
    )
    return conflicts


def _assist_search(project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_scene_project(project)
    assist = _ensure_assist(project)
    target_date_text, target_date = _assist_date_parts(payload.get("target_date"))
    weekday = target_date.weekday()
    shift_key = _assist_shift_key(payload.get("shift_key"), required=False)
    target_aptitude_category = _assist_option_aptitude_category(shift_key)
    try:
        limit = int(payload.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 30))
    scene_conflict_entries = _assist_scene_conflict_entries(project, target_date)

    candidates: dict[str, dict[str, Any]] = {}
    option_aptitude_counts: dict[str, dict[str, int]] = {}
    option_aptitude_category_totals: dict[str, dict[str, int]] = {}

    def upsert_result(candidate_id: str, name: str, employee_number: str) -> dict[str, Any]:
        key = _assist_candidate_lookup_key(candidate_id, employee_number, name)
        item = candidates.get(key)
        if item:
            return item
        item = {
            "candidate_key": key,
            "candidate_id": candidate_id,
            "name": name,
            "employee_number": employee_number,
            "score": 0,
            "reasons": [],
            "matched_rule_count": 0,
            "matched_record_count": 0,
            "breakdown": [],
        }
        candidates[key] = item
        return item

    for dedicated in _project_registered_dedicated_candidates(project):
        item = upsert_result(
            "",
            str(dedicated.get("name") or ""),
            str(dedicated.get("employee_number") or ""),
        )
        points = 500
        branch_labels = ", ".join(str(value) for value in (dedicated.get("site_branches") or []) if str(value).strip())
        contract_labels = ", ".join(str(value) for value in (dedicated.get("contract_codes") or []) if str(value).strip())
        context = "最新マスタ登録"
        if branch_labels:
            context += f" / 枝番号 {branch_labels}"
        item["score"] += points
        item["reasons"].append("最新マスタで専従者に登録済み")
        item["breakdown"].append(
            {
                "category": "master",
                "label": "専従者マスタ",
                "match_scope": "contract_master",
                "match_label": "最新マスタ",
                "role_type": "dedicated",
                "role_label": ASSIST_ROLE_LABELS.get("dedicated", "専従者"),
                "points": points,
                "formula": f"{points}",
                "context": context,
                "contract_codes": list(dedicated.get("contract_codes") or []),
                "site_branches": list(dedicated.get("site_branches") or []),
                "contract_code_label": contract_labels,
            }
        )

    for rule in assist.get("rules") or []:
        if not rule.get("enabled", True):
            continue
        rule_weekday = _assist_rule_weekday_value(rule.get("weekday"))
        if rule_weekday is not None and rule_weekday != weekday:
            continue
        if not _rule_effective_for_date(rule, target_date):
            continue
        rule_shift_key = str(rule.get("shift_key") or "")
        rule_shift_label = _assist_shift_label(rule_shift_key)
        rule_weekday_label = _assist_rule_weekday_label(rule_weekday)
        match_scope = (
            "exact"
            if rule_weekday is not None and _assist_match_scope(shift_key, rule_shift_key) == "exact"
            else "weekday"
        )
        match_label = (
            ASSIST_MATCH_SCOPE_LABELS["exact"]
            if match_scope == "exact"
            else (ASSIST_WEEKDAYLESS_LABEL if rule_weekday is None else ASSIST_MATCH_SCOPE_LABELS["weekday"])
        )
        rule_breakdown_label = (
            "ルール一致"
            if match_scope == "exact"
            else ("曜日なしルール" if rule_weekday is None else "曜日一致ルール")
        )
        rule_context = (
            f"{rule_weekday_label if rule_weekday is None else f'{rule_weekday_label}曜'} / {rule_shift_label}"
        )
        for assignment in rule.get("assignments") or []:
            if not isinstance(assignment, dict):
                continue
            role_type = str(assignment.get("role_type") or "normal")
            priority = int(assignment.get("priority", 1) or 1)
            custom_points = int(assignment.get("custom_points", 0) or 0)
            item = upsert_result(
                str(assignment.get("candidate_id") or ""),
                str(assignment.get("candidate_name") or ""),
                str(assignment.get("employee_number") or ""),
            )
            base_points = ASSIST_ROLE_SCORES.get(role_type, 0)
            priority_bonus = _assist_rule_priority_bonus(priority, match_scope)
            points = _assist_rule_points(role_type, priority, custom_points, match_scope)
            item["score"] += points
            item["matched_rule_count"] += 1
            item["reasons"].append(
                f"{rule_context} の{match_label} "
                f"{ASSIST_ROLE_LABELS.get(role_type, '通常')}ルール"
                + (f" (優先 {priority})" if role_type != "dedicated" else "")
            )
            item["breakdown"].append(
                {
                    "category": "rule",
                    "label": rule_breakdown_label,
                    "match_scope": match_scope,
                    "match_label": match_label,
                    "role_type": role_type,
                    "role_label": ASSIST_ROLE_LABELS.get(role_type, "通常"),
                    "priority": priority,
                    "base_points": base_points,
                    "priority_bonus": priority_bonus,
                    "custom_points": custom_points,
                    "points": points,
                    "formula": (
                        f"({base_points} x 3) + {priority_bonus}"
                        if match_scope == "exact"
                        else f"{base_points} + {priority_bonus}"
                    ) + (f" + {custom_points}" if custom_points else ""),
                    "context": rule_context,
                }
            )

    for record in assist.get("records") or []:
        record_date_text = str(record.get("date") or "").strip()
        if not record_date_text:
            continue
        _, record_date = _assist_date_parts(record_date_text)
        if record_date > target_date:
            continue
        candidate_key = _assist_candidate_lookup_key(
            record.get("candidate_id"),
            record.get("employee_number"),
            record.get("candidate_name"),
        )
        days_ago = (target_date - record_date).days
        record_weekday = int(record.get("weekday", -1) or -1)
        weekday_matches = record_weekday == weekday
        record_shift_key = str(record.get("shift_key") or "")
        record_shift_label = _assist_shift_label(record_shift_key)
        record_aptitude_category = _assist_option_aptitude_category(record_shift_key)
        if record_aptitude_category:
            candidate_option_counts = option_aptitude_counts.setdefault(candidate_key, {})
            candidate_option_counts[record_shift_key] = candidate_option_counts.get(record_shift_key, 0) + 1
            candidate_category_totals = option_aptitude_category_totals.setdefault(candidate_key, {})
            candidate_category_totals[record_aptitude_category] = (
                candidate_category_totals.get(record_aptitude_category, 0) + 1
            )
        if weekday_matches:
            match_scope = _assist_match_scope(shift_key, record_shift_key)
        else:
            if days_ago > 90:
                continue
            match_scope = "other_weekday"
        recency_bonus = _assist_record_recency_bonus(days_ago, match_scope)
        role_type = str(record.get("role_type") or "normal")
        base_points = ASSIST_ROLE_SCORES.get(role_type, 0)
        points = _assist_record_points(role_type, days_ago, match_scope)
        item = upsert_result(
            str(record.get("candidate_id") or ""),
            str(record.get("candidate_name") or ""),
            str(record.get("employee_number") or ""),
        )
        item["score"] += points
        item["matched_record_count"] += 1
        if match_scope == "other_weekday":
            record_weekday_label = _assist_weekday_label(record_weekday) if record_weekday >= 0 else "?"
            label_text = f"曜日不一致実績({record_weekday_label}曜)"
        else:
            label_text = "実績一致" if match_scope == "exact" else "曜日一致実績"
        item["reasons"].append(
            f"{record_date_text} の{ASSIST_MATCH_SCOPE_LABELS.get(match_scope, '一致')}実績 "
            f"({ASSIST_ROLE_LABELS.get(role_type, '通常')})"
        )
        if match_scope == "other_weekday":
            formula = f"max(10, {base_points} // 4) + {recency_bonus}"
        elif match_scope == "exact":
            formula = f"{base_points} + {recency_bonus}"
        else:
            formula = f"max(20, {base_points} // 2) + {recency_bonus}"
        item["breakdown"].append(
            {
                "category": "record",
                "label": label_text,
                "match_scope": match_scope,
                "match_label": ASSIST_MATCH_SCOPE_LABELS.get(match_scope, "一致"),
                "role_type": role_type,
                "role_label": ASSIST_ROLE_LABELS.get(role_type, "通常"),
                "base_points": base_points,
                "recency_bonus": recency_bonus,
                "days_ago": days_ago,
                "points": points,
                "formula": formula,
                "context": f"{record_date_text} / {record_shift_label}",
            }
        )

    results: list[dict[str, Any]] = []
    category_order = {"master": 0, "profile": 1, "aptitude": 2, "rule": 3, "record": 4}
    for item in candidates.values():
        profile = _find_assist_profile(
            assist,
            candidate_id=str(item.get("candidate_id") or ""),
            employee_number=str(item.get("employee_number") or ""),
            candidate_name=str(item.get("name") or ""),
        )
        if profile:
            if not bool(profile.get("active", True)):
                continue
            blocked_weekdays = _assist_weekday_values(profile.get("blocked_weekdays"), "NG曜日")
            if weekday in blocked_weekdays:
                continue
            preferred_weekdays = _assist_weekday_values(profile.get("preferred_weekdays"), "希望曜日")
            if weekday in preferred_weekdays:
                weekday_label = _assist_weekday_label(weekday)
                item["score"] += ASSIST_PREFERRED_WEEKDAY_BONUS
                item["reasons"].append(f"{weekday_label}曜が希望曜日のため加点")
                item["breakdown"].append(
                    {
                        "category": "profile",
                        "label": "希望曜日一致",
                        "match_scope": "preferred_weekday",
                        "match_label": "希望曜日",
                        "points": ASSIST_PREFERRED_WEEKDAY_BONUS,
                        "formula": f"{ASSIST_PREFERRED_WEEKDAY_BONUS}",
                        "context": f"{weekday_label}曜",
                    }
                )
        if target_aptitude_category:
            candidate_key = str(item.get("candidate_key") or "")
            exact_count = int((option_aptitude_counts.get(candidate_key) or {}).get(shift_key, 0) or 0)
            category_total = int(
                (option_aptitude_category_totals.get(candidate_key) or {}).get(target_aptitude_category, 0) or 0
            )
            aptitude_points = _assist_option_aptitude_points(exact_count, category_total)
            if aptitude_points:
                bucket_label = _assist_option_aptitude_bucket_label(exact_count, category_total)
                aptitude_label = _assist_option_aptitude_label(target_aptitude_category)
                item["score"] += aptitude_points
                item["reasons"].append(
                    f"{_assist_shift_label(shift_key)} の{aptitude_label}補正 "
                    f"({bucket_label} / 実績 {exact_count}件)"
                )
                item["breakdown"].append(
                    {
                        "category": "aptitude",
                        "label": aptitude_label,
                        "match_scope": target_aptitude_category,
                        "match_label": "自動学習",
                        "record_count": exact_count,
                        "category_total": category_total,
                        "points": aptitude_points,
                        "formula": f"{bucket_label} -> {aptitude_points:+d}",
                        "context": f"{_assist_shift_label(shift_key)} / 実績 {exact_count}件",
                    }
                )
        unique_reasons = []
        for reason in item["reasons"]:
            if reason not in unique_reasons:
                unique_reasons.append(reason)
        item["reasons"] = unique_reasons[:6]
        scene_conflicts = _assist_scene_conflicts_for_candidate(
            scene_conflict_entries,
            shift_key=shift_key,
            candidate_name=str(item.get("name") or ""),
            employee_number=str(item.get("employee_number") or ""),
        )
        scene_conflict_site_names: list[str] = []
        for conflict in scene_conflicts:
            project_title = str(conflict.get("project_title") or "").strip()
            if project_title and project_title not in scene_conflict_site_names:
                scene_conflict_site_names.append(project_title)
        item["has_scene_conflict"] = bool(scene_conflicts)
        item["scene_conflicts"] = scene_conflicts
        item["scene_conflict_site_names"] = scene_conflict_site_names
        item["scene_conflict_site_count"] = len(scene_conflict_site_names)
        item["breakdown"].sort(
            key=lambda part: (
                -int(part.get("points", 0) or 0),
                category_order.get(str(part.get("category") or ""), 9),
                str(part.get("context") or ""),
            )
        )
        results.append(item)
    results.sort(
        key=lambda item: (
            -int(item["score"]),
            -int(item["matched_rule_count"]),
            -int(item["matched_record_count"]),
            item["name"],
            item["employee_number"],
        )
    )
    return {
        "success": True,
        "query": {
            "target_date": target_date_text,
            "weekday": weekday,
            "weekday_label": _assist_weekday_label(weekday),
            "shift_key": shift_key,
            "shift_label": _assist_shift_label(shift_key),
        },
        "score_reference": {
            "theoretical_max": None,
            "theoretical_max_label": "上限なし",
            "baseline_score": 0,
            "single_rule_max": _assist_rule_points("dedicated", 1, ASSIST_CUSTOM_POINTS_MAX),
            "single_rule_weekday_max": _assist_rule_points("dedicated", 1, ASSIST_CUSTOM_POINTS_MAX, "weekday"),
            "single_record_max": _assist_record_points("dedicated", 0),
            "single_record_weekday_max": _assist_record_points("dedicated", 0, "weekday"),
            "single_record_other_weekday_max": _assist_record_points("dedicated", 0, "other_weekday"),
            "role_scores": ASSIST_ROLE_SCORES,
            "match_scopes": ASSIST_MATCH_SCOPE_LABELS,
            "priority_bonus": {
                "formula": "max(0, 40 - ((priority - 1) * 5))",
                "first_priority": 40,
                "step": 5,
            },
            "weekday_priority_bonus": {
                "formula": "max(0, 20 - ((priority - 1) * 3))",
                "first_priority": 20,
                "step": 3,
            },
            "recency_bonus": {
                "within_30_days": 30,
                "within_90_days": 20,
                "within_180_days": 10,
                "older": 0,
            },
            "weekday_recency_bonus": {
                "within_30_days": 15,
                "within_90_days": 10,
                "within_180_days": 5,
                "older": 0,
            },
            "other_weekday_recency_bonus": {
                "within_30_days": 10,
                "within_90_days": 5,
                "older": 0,
            },
            "custom_points": {
                "formula": "rule_points + custom_points",
                "default": 0,
                "min": ASSIST_CUSTOM_POINTS_MIN,
                "max": ASSIST_CUSTOM_POINTS_MAX,
            },
            "preferred_weekday_bonus": ASSIST_PREFERRED_WEEKDAY_BONUS,
            "option_aptitude": {
                "source": "assist_records",
                "applies_to": ["time", "vehicle"],
                "tiers": [
                    {"label": "5件以上", "minimum_count": 5, "points": ASSIST_OPTION_APTITUDE_MAX_BONUS},
                    {"label": "3-4件", "minimum_count": 3, "maximum_count": 4, "points": ASSIST_OPTION_APTITUDE_MID_BONUS},
                    {"label": "1-2件", "minimum_count": 1, "maximum_count": 2, "points": ASSIST_OPTION_APTITUDE_MIN_BONUS},
                    {
                        "label": "同カテゴリ実績あり / 対象0件",
                        "minimum_count": 0,
                        "same_category_history_required": True,
                        "points": ASSIST_OPTION_APTITUDE_ZERO_PENALTY,
                    },
                    {
                        "label": "学習データなし",
                        "minimum_count": 0,
                        "same_category_history_required": False,
                        "points": 0,
                    },
                ],
                "max_bonus": ASSIST_OPTION_APTITUDE_MAX_BONUS,
                "min_penalty": ASSIST_OPTION_APTITUDE_ZERO_PENALTY,
            },
            "blocked_weekday_policy": "exclude",
            "inactive_profile_policy": "exclude",
        },
        "results": results[:limit],
    }


def _leave_sync_bridge():
    try:
        from .leave_mgr import (
            ensure_data_directories,
            get_cloudshift_calendar_options,
            replace_cloudshift_leaves,
        )
    except ImportError:
        from app.tools.leave_mgr import (  # type: ignore
            ensure_data_directories,
            get_cloudshift_calendar_options,
            replace_cloudshift_leaves,
        )
    return ensure_data_directories, get_cloudshift_calendar_options, replace_cloudshift_leaves


def _calendar_export_bridge():
    try:
        from .shiftersync import generate_png_calendar
    except ImportError:
        from app.tools.shiftersync import generate_png_calendar  # type: ignore
    return generate_png_calendar


def _leave_option_label(option_key: str | None) -> str | None:
    if not option_key:
        return None
    return LEAVE_OPTION_MAPPINGS.get(option_key)


def _cloudshift_leave_rows(
    project: dict[str, Any], month_data: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    leaves: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    project_name = str(project.get("title") or "").strip()
    project_employee_number = str(project.get("employee_number") or "").strip()

    for day_key, entries in (month_data.get("entries_per_day") or {}).items():
        try:
            day = int(day_key)
        except (TypeError, ValueError):
            continue

        date_text = f"{month_data['year']:04d}-{month_data['month']:02d}-{day:02d}"
        for index, entry in enumerate(entries or []):
            option_key, name = parse_entry_value((entry or {}).get("value", ""))
            leave_type = _leave_option_label(option_key)
            if not leave_type:
                continue

            normalized_name = project_name
            employee_number = project_employee_number
            payload = {
                "date": date_text,
                "name": normalized_name,
                "employee_number": employee_number,
                "leave_type": leave_type,
                "comment": str((entry or {}).get("comment") or "").strip(),
                "source_entry_id": str((entry or {}).get("id") or "").strip(),
            }
            if not employee_number:
                skipped.append(
                    {
                        "day": day,
                        "entry_id": payload["source_entry_id"],
                        "name": normalized_name,
                        "leave_type": leave_type,
                        "reason": "employee_number_missing",
                    }
                )
                continue
            leaves.append(payload)

    return leaves, skipped


def _calendar_day_map(month_data: dict[str, Any]) -> dict[int, list[dict[str, str]]]:
    return {
        int(day): [
            {
                "title": entry_display_text(entry),
                "comment": str(entry.get("comment", "")),
            }
            for entry in entries
        ]
        for day, entries in (month_data.get("entries_per_day") or {}).items()
    }


def _calendar_download_name(title: str, year: int, month: int) -> str:
    safe_title = secure_filename(title) or "calendar"
    return f"{year}-{str(month).zfill(2)}_calendar_{safe_title}.png"


def _calendar_png_bytes_for_month(
    project_title: str, project_mode: str, month_data: dict[str, Any]
) -> io.BytesIO:
    generate_png_calendar = _calendar_export_bridge()
    temp_dir = _runtime_root() / "exports"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"calendar_{secrets.token_hex(12)}.png"
    try:
        generate_png_calendar(
            temp_path,
            month_data["year"],
            month_data["month"],
            project_mode,
            project_title,
            _calendar_day_map(month_data),
            month_data.get("required_capacity", 0) if month_data.get("capacity_enabled") else None,
        )
        buffer = io.BytesIO(temp_path.read_bytes())
        buffer.seek(0)
        return buffer
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _project_detail_payload(
    project: dict[str, Any],
    selected_month_key: str | None = None,
    *,
    include_draft: bool = False,
) -> dict[str, Any]:
    month_keys = _sort_month_keys(list((project.get("months") or {}).keys()))
    active_month_key = None
    month_data = None
    if month_keys:
        active_month_key = selected_month_key or month_keys[-1]
        month_data = project["months"].get(active_month_key)
        if not month_data:
            active_month_key = month_keys[-1]
            month_data = project["months"][active_month_key]
    return {
        "project": {
            **_project_summary(project),
            "title": project["title"],
            "mode": project["mode"],
            "urls": _project_public_urls(project),
        },
        "active_month_key": active_month_key,
        "month": _client_month_payload(month_data, include_draft=include_draft),
    }


@cloudshift_bp.route("/")
@login_required
def index():
    return render_template(
        "cloudshift.html",
        user_name=_user_label(),
        shiftersync_holidays=sorted(set(JAPAN_HOLIDAYS)),
    )


@cloudshift_bp.route("/view/<token>")
def public_view(token: str):
    project = _find_project_by_token(token, "view")
    return render_template(
        "cloudshift_public.html",
        access_mode="view",
        token=token,
        project_title=project["title"],
        authenticated_editor_name=_user_label() if current_user.is_authenticated else "",
        shiftersync_holidays=sorted(set(JAPAN_HOLIDAYS)),
    )


@cloudshift_bp.route("/edit/<token>")
def public_edit(token: str):
    project = _find_project_by_token(token, "edit")
    return render_template(
        "cloudshift_public.html",
        access_mode="edit",
        token=token,
        project_title=project["title"],
        authenticated_editor_name=_user_label() if current_user.is_authenticated else "",
        shiftersync_holidays=sorted(set(JAPAN_HOLIDAYS)),
    )


@cloudshift_bp.route("/api/list")
@login_required
def api_list():
    owner_id = _user_id()
    projects = []
    for project in _iter_stored_projects():
        is_owner = project.get("owner_user_id") == owner_id
        if not is_owner and not _project_is_shared_with_current_user(project):
            continue
        projects.append(_project_summary(project))
    projects.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jsonify({"projects": projects})


@cloudshift_bp.route("/api/conflict-check", methods=["POST"])
@login_required
def api_conflict_check():
    payload = request.get_json(silent=True) or {}
    month_key = str(payload.get("month_key") or "").strip()
    if not month_key:
        raise CloudShiftError("比較する年月を選択してください", 400)

    try:
        year, month = _parse_month_key(month_key)
        year, month = _validate_year_month(year, month)
    except Exception as exc:
        raise CloudShiftError("年月の形式が不正です", 400) from exc

    raw_project_ids = payload.get("project_ids")
    if not isinstance(raw_project_ids, list):
        raise CloudShiftError("比較するシフト帳を選択してください", 400)

    project_ids: list[str] = []
    for item in raw_project_ids:
        project_id = str(item or "").strip()
        if project_id and project_id not in project_ids:
            project_ids.append(project_id)

    if not project_ids:
        raise CloudShiftError("比較するシフト帳を選択してください", 400)

    compare_payloads: list[dict[str, Any]] = []
    for project_id in project_ids:
        project = _owner_project_or_404(project_id)
        month_data = (project.get("months") or {}).get(month_key)
        if not month_data:
            raise CloudShiftError(f"{project['title']} に {month_key} の月データがありません", 400)
        compare_payloads.append(
            {
                "project_id": project["id"],
                "month_key": month_key,
                "label": project["title"],
                "title": project["title"],
                "mode": project["mode"],
                "year": year,
                "month": month,
                "required_capacity": month_data.get("required_capacity", 0) if month_data.get("capacity_enabled") else 0,
                "entries_per_day": month_data.get("entries_per_day") or {},
            }
        )

    try:
        result = compare_shift_payloads(compare_payloads)
    except ValueError as exc:
        raise CloudShiftError(str(exc), 400) from exc
    return jsonify({"success": True, **result})


@cloudshift_bp.route("/api/create", methods=["POST"])
@login_required
def api_create():
    title_override = request.form.get("title", "").strip()
    csv_file = request.files.get("csv_file")
    if csv_file and csv_file.filename:
        parsed = _parse_shiftersync_csv(csv_file)
        title = _sanitize_title(title_override or parsed["title"])
        mode = parsed["mode"]
        employee_number = parsed.get("employee_number", "")
        year, month = _validate_year_month(parsed["year"], parsed["month"])
        capacity_enabled = parsed["capacity_enabled"]
        required_capacity = parsed["required_capacity"]
        entries = parsed["entries_per_day"]
    else:
        title = _sanitize_title(title_override)
        mode = _sanitize_mode(request.form.get("mode"))
        employee_number = _sanitize_employee_number(request.form.get("employee_number"))
        year, month = _validate_year_month(request.form.get("year"), request.form.get("month"))
        capacity_enabled, required_capacity = _sanitize_capacity(request.form.get("required_capacity"))
        entries = {}
    site_ref = None
    if mode == "scene":
        site_ref = _load_site_reference(_sanitize_site_row_id(request.form.get("site_row_id")), require_active=True)

    project_id = _project_id()
    month_key = _month_key(year, month)
    project = {
        "id": project_id,
        "owner_user_id": _user_id(),
        "title": title,
        "mode": mode,
        "employee_number": employee_number if mode == "person" else "",
        **_site_storage_fields(site_ref if mode == "scene" else None),
        "view_token": _share_token(),
        "edit_token": _share_token(),
        "created_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
        "months": {
            month_key: _build_month_payload(year, month, capacity_enabled, required_capacity, entries),
        },
    }
    _save_project(project)
    _append_history(
        project_id,
        {
            "timestamp": _utcnow_iso(),
            "editor_name": _user_label(),
            "editor_type": "owner",
            "action": "project_created",
            "month_key": month_key,
            "changes": [f"{month_key} を作成"],
        },
    )
    if site_ref:
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "site_linked",
                "month_key": None,
                "changes": [f"現場を {site_ref['site_id']} / {site_ref['site_name']} に設定"],
            },
        )
    if project["mode"] == "scene":
        _backfill_scene_project_from_person_experience(project, actor_name=_user_label())
        _backfill_scene_project_from_siteplus_dedicated(project, actor_name=_user_label())
    _resync_shift_month(project, month_key, actor_name=_user_label())
    _refresh_shift_sync_for_target_month(project, month_key, actor_name=_user_label())
    project = _load_project(project_id)
    return jsonify({"success": True, "project": _project_detail_payload(project, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>")
@login_required
def api_project_detail(project_id: str):
    project, access_role = _project_for_current_user_or_404(project_id)
    selected_month_key = request.args.get("month_key")
    payload = _project_detail_payload(project, selected_month_key, include_draft=access_role == "owner")
    payload["access_role"] = access_role
    payload["project"]["access_role"] = access_role
    if access_role != "owner":
        payload["project"]["urls"] = {}
    return jsonify(payload)


@cloudshift_bp.route("/api/project/<project_id>/meta", methods=["PUT"])
@login_required
def api_project_meta(project_id: str):
    data = request.get_json(silent=True) or {}
    should_resync_person_experience = False
    should_backfill_scene_person_experience = False
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        new_title = _sanitize_title(data.get("title", project["title"]))
        old_title = project["title"]
        old_employee_number = str(project.get("employee_number") or "")
        old_site = _project_site_payload(project)
        new_employee_number = _sanitize_employee_number(data.get("employee_number", project.get("employee_number", "")))
        if project.get("mode") != "person":
            new_employee_number = ""
        new_site_ref = None
        if project.get("mode") == "scene":
            incoming_site_row_id = data.get("site_row_id", project.get("site_row_id"))
            new_site_ref = _load_site_reference(_sanitize_site_row_id(incoming_site_row_id), require_active=True)
        metadata_changed = False
        if new_title != project["title"]:
            project["title"] = new_title
            metadata_changed = True
        if new_employee_number != str(project.get("employee_number") or ""):
            project["employee_number"] = new_employee_number
            metadata_changed = True
        next_site_fields = _site_storage_fields(new_site_ref if project.get("mode") == "scene" else None)
        if (
            next_site_fields["site_row_id"] != project.get("site_row_id")
            or next_site_fields["site_id"] != str(project.get("site_id") or "")
            or next_site_fields["site_name"] != str(project.get("site_name") or "")
        ):
            project.update(next_site_fields)
            metadata_changed = True
        if metadata_changed:
            _save_project(project)
            changes = []
            if new_title != old_title:
                changes.append(f"タイトルを {old_title} から {new_title} に変更")
            if new_employee_number != old_employee_number:
                changes.append("社員IDを更新")
            next_site = _project_site_payload(project)
            if old_site.get("site_row_id") != next_site.get("site_row_id"):
                if next_site.get("site_row_id"):
                    changes.append(f"現場を {next_site.get('site_id')} / {next_site.get('site_name')} に設定")
                elif old_site.get("site_row_id"):
                    changes.append("現場設定を解除")
            if not changes:
                changes.append("メタ情報を更新")
            _append_history(
                project_id,
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": _user_label(),
                    "editor_type": "owner",
                    "action": "title_updated",
                    "month_key": None,
                    "changes": changes,
                },
            )
            should_resync_person_experience = project.get("mode") == "person" and (
                new_title != old_title or new_employee_number != old_employee_number
            )
            should_backfill_scene_person_experience = project.get("mode") == "scene" and (
                new_title != old_title
                or old_site.get("site_row_id") != next_site.get("site_row_id")
                or str(old_site.get("site_id") or "") != str(next_site.get("site_id") or "")
            )
    if should_resync_person_experience:
        _resync_person_experience_project(project, actor_name=_user_label())
    if should_backfill_scene_person_experience:
        _backfill_scene_project_from_person_experience(project, actor_name=_user_label())
        _backfill_scene_project_from_siteplus_dedicated(project, actor_name=_user_label())
    if metadata_changed:
        _resync_shift_project(project, actor_name=_user_label())
        _refresh_shift_sync_for_target_project(project, actor_name=_user_label())
        project = _load_project(project_id)
    return jsonify({"success": True, "project": _project_detail_payload(project, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/tokens/regenerate", methods=["POST"])
@login_required
def api_regenerate_tokens(project_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        project["view_token"] = _share_token()
        project["edit_token"] = _share_token()
        _save_project(project)
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "tokens_regenerated",
                "month_key": None,
                "changes": ["共有URLを再発行"],
            },
        )
    return jsonify({"success": True, "urls": _project_public_urls(project)})


@cloudshift_bp.route("/api/project/<project_id>/account-shares", methods=["GET", "PUT"])
@login_required
def api_project_account_shares(project_id: str):
    if request.method == "GET":
        project = _owner_project_or_404(project_id)
        owner_office_ids = _current_share_office_ids()
        office_labels = _office_label_map(owner_office_ids)
        return jsonify(
            {
                "success": True,
                "shares": _normalized_account_shares(project),
                "owner_offices": [
                    {"id": office_id, "label": office_labels.get(office_id, str(office_id))}
                    for office_id in sorted(owner_office_ids)
                ],
            }
        )

    data = request.get_json(silent=True) or {}
    share_office = bool(data.get("share_office"))
    employee_numbers_raw = data.get("employee_numbers") or []
    if not isinstance(employee_numbers_raw, list):
        raise CloudShiftError("社員番号はリストで指定してください", 400)

    employee_rows: list[dict[str, str]] = []
    seen_numbers: set[str] = set()
    for raw_number in employee_numbers_raw:
        number = _sanitize_employee_number(raw_number)
        if not number or number in seen_numbers:
            continue
        seen_numbers.add(number)
        user = User.query.filter_by(username=number).first()
        if not user:
            raise CloudShiftError(f"社員番号 {number} のDSTTアカウントが見つかりません", 404)
        employee_rows.append(
            {
                "employee_number": number,
                "name": _employee_label_for_number(number),
            }
        )

    owner_office_ids = _current_share_office_ids()
    if share_office and not owner_office_ids:
        raise CloudShiftError("共有に使える営業所権限がありません", 400)

    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        project["account_shares"] = {
            "office": {
                "enabled": share_office,
                "office_ids": sorted(owner_office_ids) if share_office else [],
            },
            "employees": employee_rows,
            "updated_at": _utcnow_iso(),
            "updated_by": _user_id(),
        }
        _save_project(project)
        changes = []
        changes.append("同じ営業所内への共有を有効化" if share_office else "同じ営業所内への共有を無効化")
        if employee_rows:
            changes.append("特定社員への共有: " + ", ".join(row["employee_number"] for row in employee_rows))
        else:
            changes.append("特定社員への共有を解除")
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "account_share_updated",
                "month_key": None,
                "changes": changes,
            },
        )
    return jsonify({"success": True, "shares": _normalized_account_shares(project)})


def _create_month_in_project(project: dict[str, Any], payload: dict[str, Any], actor_name: str, actor_type: str) -> dict[str, Any]:
    year, month = _validate_year_month(payload.get("year"), payload.get("month"))

    month_key = _month_key(year, month)
    if month_key in project.get("months", {}):
        raise CloudShiftError("その月は既に存在します", 400)

    init_mode = (payload.get("init_mode") or "blank").strip().lower()
    capacity_enabled, required_capacity = _sanitize_capacity(payload.get("required_capacity"))
    if init_mode == "copy":
        source_key = payload.get("source_month_key")
        source_month = (project.get("months") or {}).get(source_key)
        if not source_month:
            raise CloudShiftError("コピー元の月が見つかりません", 400)
        source_entries = _normalize_entries(source_month.get("entries_per_day"), source_month["year"], source_month["month"])
        copied_entries = {
            day_key: [dict(entry) for entry in entries if not _entry_is_shift_synced(entry)]
            for day_key, entries in source_entries.items()
        }
        month_payload = _build_month_payload(
            year,
            month,
            capacity_enabled or source_month.get("capacity_enabled", False),
            required_capacity if capacity_enabled else source_month.get("required_capacity", 0),
            copied_entries,
        )
    else:
        month_payload = _build_month_payload(year, month, capacity_enabled, required_capacity, {})

    project.setdefault("months", {})[month_key] = month_payload
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "month_created",
            "month_key": month_key,
            "changes": [f"{month_key} を作成 ({'コピー' if init_mode == 'copy' else '空で作成'})"],
        },
    )
    return month_payload


@cloudshift_bp.route("/api/project/<project_id>/month", methods=["POST"])
@login_required
def api_create_month(project_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _create_month_in_project(project, payload, _user_label(), "owner")
    month_key = _month_key(month_payload["year"], month_payload["month"])
    _resync_shift_month(project, month_key, actor_name=_user_label())
    _refresh_shift_sync_for_target_month(project, month_key, actor_name=_user_label())
    project = _load_project(project_id)
    return jsonify({"success": True, "project": _project_detail_payload(project, month_key, include_draft=True)})


def _save_month_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    payload: dict[str, Any],
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)

    base_month = _trusted_base_month(current_month, payload.get("base_month") or {})
    prepared_entries = _prepared_local_entries_for_month(
        project,
        current_month,
        payload.get("entries_per_day") or {},
        year=year,
        month=month,
    )
    incoming_month = {
        "year": year,
        "month": month,
        "required_capacity": _sanitize_capacity(payload.get("required_capacity"))[1],
        "entries_per_day": prepared_entries,
    }
    merged = _merge_month_payload(current_month, incoming_month, base_month)
    merged["draft_entries_per_day"] = _normalize_entries(merged.get("entries_per_day"), year, month)
    changes = _describe_month_changes(current_month, merged)
    if not changes:
        return current_month
    snapshots = dict(current_month.get("revision_snapshots") or {})
    snapshots[str(int(current_month.get("revision", 1)))] = _snapshot_month_payload(current_month)
    merged["revision_snapshots"] = _trim_revision_snapshots(snapshots)
    project["months"][month_key] = merged
    _save_project(project)
    if changes:
        _append_history(
            project["id"],
            {
                "timestamp": _utcnow_iso(),
                "editor_name": actor_name,
                "editor_type": actor_type,
                "action": "month_updated",
                "month_key": month_key,
                "changes": changes[:100],
            },
        )
    return merged


def _draft_entry_count(month_data: dict[str, Any]) -> int:
    return sum(
        len(entries)
        for entries in (month_data.get("draft_entries_per_day") or {}).values()
        if isinstance(entries, list)
    )


def _month_draft_has_changes(month_data: dict[str, Any], year: int, month: int) -> bool:
    return _normalize_entries(month_data.get("entries_per_day"), year, month) != _normalize_entries(
        month_data.get("draft_entries_per_day"), year, month
    )


def _save_draft_month_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    payload: dict[str, Any],
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)
    prepared_entries = _prepared_local_entries_for_month(
        project,
        current_month,
        payload.get("entries_per_day") or {},
        year=year,
        month=month,
    )
    previous_count = _draft_entry_count(current_month)
    current_month["draft_entries_per_day"] = prepared_entries
    current_month["updated_at"] = _utcnow_iso()
    _save_project(project)
    next_count = _draft_entry_count(current_month)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "month_draft_saved",
            "month_key": month_key,
            "changes": [f"{month_key} の仮保存を {previous_count}件 から {next_count}件 に更新"],
        },
    )
    return current_month


def _clear_draft_month_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)
    previous_count = _draft_entry_count(current_month)
    current_month["draft_entries_per_day"] = _normalize_entries(current_month.get("entries_per_day"), year, month)
    current_month["updated_at"] = _utcnow_iso()
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "month_draft_cleared",
            "month_key": month_key,
            "changes": [f"{month_key} の仮保存を正式シフトの内容に戻しました ({previous_count}件)"],
        },
    )
    return current_month


def _publish_draft_month_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    actor_name: str,
    actor_type: str,
) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    month_key = _month_key(year, month)
    current_month = (project.get("months") or {}).get(month_key)
    if not current_month:
        raise CloudShiftError("対象の月が存在しません", 404)
    draft_entries = _normalize_entries(current_month.get("draft_entries_per_day"), year, month)
    draft_count = _draft_entry_count({"draft_entries_per_day": draft_entries})
    if not _month_draft_has_changes(current_month, year, month):
        return current_month

    published = {
        **current_month,
        "entries_per_day": draft_entries,
        "draft_entries_per_day": draft_entries,
        "revision": int(current_month.get("revision", 1) or 1) + 1,
        "updated_at": _utcnow_iso(),
    }
    snapshots = dict(current_month.get("revision_snapshots") or {})
    snapshots[str(int(current_month.get("revision", 1) or 1))] = _snapshot_month_payload(current_month)
    published["revision_snapshots"] = _trim_revision_snapshots(snapshots)
    changes = _describe_month_changes(current_month, published)
    changes.insert(0, f"{month_key} の仮保存 {draft_count}件を正式シフトへ反映")
    project["months"][month_key] = published
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": _utcnow_iso(),
            "editor_name": actor_name,
            "editor_type": actor_type,
            "action": "month_draft_published",
            "month_key": month_key,
            "changes": changes[:100],
        },
    )
    return published


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>", methods=["PUT"])
@login_required
def api_save_month(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _save_month_in_project(project, year, month, payload, _user_label(), "owner")
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=_user_label())
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft", methods=["PUT"])
@login_required
def api_save_month_draft(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _save_draft_month_in_project(project, year, month, payload, _user_label(), "owner")
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft", methods=["DELETE"])
@login_required
def api_clear_month_draft(project_id: str, year: int, month: int):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _clear_draft_month_in_project(project, year, month, _user_label(), "owner")
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft/publish", methods=["POST"])
@login_required
def api_publish_month_draft(project_id: str, year: int, month: int):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _publish_draft_month_in_project(project, year, month, _user_label(), "owner")
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=_user_label())
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>", methods=["DELETE"])
@login_required
def api_delete_month(project_id: str, year: int, month: int):
    month_key = _month_key(year, month)
    source_project = None
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_keys = list((project.get("months") or {}).keys())
        if month_key not in month_keys:
            abort(404)
        source_project = json.loads(json.dumps(project, ensure_ascii=False))
        del project["months"][month_key]
        _save_project(project)
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "month_deleted",
                "month_key": month_key,
                "changes": [f"{month_key} を削除"],
            },
        )
    if source_project:
        _remove_shift_sync_for_month(source_project, month_key, actor_name=_user_label())
    return jsonify({"success": True})


@cloudshift_bp.route("/api/project/<project_id>", methods=["DELETE"])
@login_required
def api_delete_project(project_id: str):
    deleted_experience_site_ids: list[str] = []
    source_project = None
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        source_project = json.loads(json.dumps(project, ensure_ascii=False))
        if project.get("mode") == "person":
            assist = _ensure_assist(project)
            deleted_experience_site_ids = [
                str(item.get("id") or "")
                for item in (assist.get("experienced_sites") or [])
                if str(item.get("id") or "")
            ]
        project_path = _project_path(project_id)
        history_path = _history_path(project_id)
        project_row = db.session.get(CloudShiftProject, project_id)
        if project_row is not None:
            db.session.delete(project_row)
            db.session.commit()
        if project_path.exists():
            project_path.unlink()
        if history_path.exists():
            history_path.unlink()
    for site_id in deleted_experience_site_ids:
        _delete_person_experience_from_scene_projects(project_id, site_id, actor_name=_user_label())
    if source_project:
        _remove_shift_sync_for_project(source_project, actor_name=_user_label())
    return jsonify({"success": True, "deleted_project_id": project["id"]})


@cloudshift_bp.route("/api/project/<project_id>/history")
@login_required
def api_history(project_id: str):
    _owner_project_or_404(project_id)
    return jsonify({"history": _load_history(project_id)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/revisions")
@login_required
def api_month_revisions(project_id: str, year: int, month: int):
    project = _owner_project_or_404(project_id)
    month_key = _month_key(*_validate_year_month(year, month))
    month_data = (project.get("months") or {}).get(month_key)
    if not month_data:
        raise CloudShiftError("対象の月が存在しません", 404)
    return jsonify(
        {
            "success": True,
            "month_key": month_key,
            "current_revision": int(month_data.get("revision", 1)),
            "revisions": _month_revision_catalog(month_data),
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/restore", methods=["POST"])
@login_required
def api_restore_month_revision(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    try:
        revision = int(payload.get("revision"))
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("復元するリビジョンを指定してください", 400) from exc

    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _restore_month_revision_in_project(project, year, month, revision, _user_label(), "owner")
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/summary", methods=["GET", "POST"])
@login_required
def api_month_summary(project_id: str, year: int, month: int):
    project, access_role = _project_for_current_user_or_404(project_id)
    payload = request.get_json(silent=True) if request.method == "POST" and access_role == "owner" else None
    return jsonify({"success": True, "summary": _summary_month_payload(project, year, month, payload)})


@cloudshift_bp.route("/api/project/<project_id>/leave-sync/calendars")
@login_required
def api_leave_sync_calendars(project_id: str):
    _owner_project_or_404(project_id)
    ensure_data_directories, get_cloudshift_calendar_options, _ = _leave_sync_bridge()
    ensure_data_directories()
    return jsonify(
        {
            "success": True,
            "calendars": get_cloudshift_calendar_options(_user_id()),
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/leave-sync/<int:year>/<int:month>", methods=["POST"])
@login_required
def api_leave_sync(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    calendar_id = str(payload.get("calendar_id") or "").strip()
    if not calendar_id:
        raise CloudShiftError("営業所を選択してください", 400)

    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        if project.get("mode") != "person":
            raise CloudShiftError("休暇反映は person モードのみ対応です", 400)

        month_key = _month_key(year, month)
        month_data = (project.get("months") or {}).get(month_key)
        if not month_data:
            raise CloudShiftError("対象の月が存在しません", 404)

        leaves, skipped = _cloudshift_leave_rows(project, month_data)
        ensure_data_directories, _, replace_cloudshift_leaves = _leave_sync_bridge()
        ensure_data_directories()
        try:
            result = replace_cloudshift_leaves(
                user_id=_user_id(),
                calendar_id=calendar_id,
                target_year_month=f"{year:04d}{month:02d}",
                source_project_id=project_id,
                source_month_key=month_key,
                leaves=leaves,
            )
        except PermissionError as exc:
            raise CloudShiftError("選択した営業所に反映する権限がありません", 403) from exc

    skipped_items = [
        {
            "day": item["day"],
            "entry_id": item["entry_id"],
            "name": item["name"],
            "leave_type": item["leave_type"],
            "reason": "社員ID未設定のためスキップ",
        }
        for item in skipped
    ]
    if result.get("skipped"):
        skipped_items.extend(result["skipped"])

    history_changes = [
        f"{month_key} の休暇を営業所 {calendar_id} に反映",
        f"登録 {result['created_total']} 件 / 置換削除 {result['removed_total']} 件 / スキップ {len(skipped_items)} 件",
    ]
    _append_history(
        project_id,
        {
            "timestamp": _utcnow_iso(),
            "editor_name": _user_label(),
            "editor_type": "owner",
            "action": "leave_sync",
            "month_key": month_key,
            "changes": history_changes,
        },
    )

    return jsonify(
        {
            "success": True,
            "calendar_id": calendar_id,
            "month_key": month_key,
            "created_total": result["created_total"],
            "removed_total": result["removed_total"],
            "removed_by_calendar": result["removed_by_calendar"],
            "skipped_total": len(skipped_items),
            "skipped": skipped_items,
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist")
@login_required
def api_assist_owner(project_id: str):
    project = _owner_project_or_404(project_id)
    return jsonify(_assist_bootstrap_for_project(project, can_edit_records=True, can_edit_rules=True, can_edit_sites=True))


@cloudshift_bp.route("/api/project/<project_id>/dedicated-candidate")
@login_required
def api_project_dedicated_candidate(project_id: str):
    project = _owner_project_or_404(project_id)
    return jsonify(
        {
            "success": True,
            "project_id": project["id"],
            "site": _project_site_payload(project),
            "candidates": _project_registered_dedicated_candidates(project),
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/sites", methods=["POST"])
@login_required
def api_assist_owner_create_site(project_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name = _user_label()
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, previous = _person_assist_site_mutation(
            project,
            payload,
            actor_name=actor_name,
            actor_type="owner",
        )
    previous_kind = str((previous or {}).get("kind") or "")
    current_kind = str(site.get("kind") or "")
    if current_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    elif previous_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project_id, str(site.get("id") or ""), actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/sites/<site_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_site(project_id: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name = _user_label()
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, previous = _person_assist_site_mutation(
            project,
            payload,
            actor_name=actor_name,
            actor_type="owner",
            site_id=site_id,
        )
    previous_kind = str((previous or {}).get("kind") or "")
    current_kind = str(site.get("kind") or "")
    if current_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    elif previous_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project_id, str(site.get("id") or ""), actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/sites/<site_id>", methods=["DELETE"])
@login_required
def api_assist_owner_delete_site(project_id: str, site_id: str):
    actor_name = _user_label()
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        deleted = _person_assist_site_delete(
            project,
            site_id,
            actor_name=actor_name,
            actor_type="owner",
        )
    if str(deleted.get("kind") or "") == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project_id, site_id, actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/experienced-sites", methods=["POST"])
@login_required
def api_assist_owner_create_experienced_site(project_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="experienced",
            actor_name=_user_label(),
            actor_type="owner",
        )
    _sync_person_experience_to_scene_projects(project, site, actor_name=_user_label())
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/experienced-sites/<site_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_experienced_site(project_id: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="experienced",
            actor_name=_user_label(),
            actor_type="owner",
            site_id=site_id,
        )
    _sync_person_experience_to_scene_projects(project, site, actor_name=_user_label())
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/experienced-sites/<site_id>", methods=["DELETE"])
@login_required
def api_assist_owner_delete_experienced_site(project_id: str, site_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        _person_assist_site_delete(
            project,
            site_id,
            kind="experienced",
            actor_name=_user_label(),
            actor_type="owner",
        )
    _delete_person_experience_from_scene_projects(project_id, site_id, actor_name=_user_label())
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/training-sites", methods=["POST"])
@login_required
def api_assist_owner_create_training_site(project_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="training",
            actor_name=_user_label(),
            actor_type="owner",
        )
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/training-sites/<site_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_training_site(project_id: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="training",
            actor_name=_user_label(),
            actor_type="owner",
            site_id=site_id,
        )
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/training-sites/<site_id>", methods=["DELETE"])
@login_required
def api_assist_owner_delete_training_site(project_id: str, site_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        _person_assist_site_delete(
            project,
            site_id,
            kind="training",
            actor_name=_user_label(),
            actor_type="owner",
        )
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/records", methods=["POST"])
@login_required
def api_assist_owner_create_record(project_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        record = _assist_record_mutation(project, payload, actor_name=_user_label(), actor_type="owner")
    return jsonify(
        {
            "success": True,
            "record": _assist_record_payload(record),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/records/<record_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_record(project_id: str, record_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        record = _assist_record_mutation(project, payload, actor_name=_user_label(), actor_type="owner", record_id=record_id)
    return jsonify(
        {
            "success": True,
            "record": _assist_record_payload(record),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/records/<record_id>", methods=["DELETE"])
@login_required
def api_assist_owner_delete_record(project_id: str, record_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        _assist_record_delete(project, record_id, actor_name=_user_label(), actor_type="owner")
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/rules", methods=["POST"])
@login_required
def api_assist_owner_create_rule(project_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        rule = _assist_rule_mutation(project, payload, actor_name=_user_label(), actor_type="owner")
    return jsonify(
        {
            "success": True,
            "rule": _assist_rule_payload(rule),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/rules/<rule_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_rule(project_id: str, rule_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        rule = _assist_rule_mutation(project, payload, actor_name=_user_label(), actor_type="owner", rule_id=rule_id)
    return jsonify(
        {
            "success": True,
            "rule": _assist_rule_payload(rule),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/rules/<rule_id>", methods=["DELETE"])
@login_required
def api_assist_owner_delete_rule(project_id: str, rule_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        _assist_rule_delete(project, rule_id, actor_name=_user_label(), actor_type="owner")
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/profiles/<profile_id>", methods=["PUT"])
@login_required
def api_assist_owner_update_profile(project_id: str, profile_id: str):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        profile = _assist_profile_mutation(
            project,
            payload,
            actor_name=_user_label(),
            actor_type="owner",
            profile_id=profile_id,
        )
    return jsonify(
        {
            "success": True,
            "profile": _assist_profile_payload(profile),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=True)["assist"],
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/assist/search", methods=["POST"])
@login_required
def api_assist_owner_search(project_id: str):
    payload = request.get_json(silent=True) or {}
    project = _owner_project_or_404(project_id)
    return jsonify(_assist_search(project, payload))


def _send_month_export(project: dict[str, Any], month_key: str, export_format: str):
    month_data = (project.get("months") or {}).get(month_key)
    if not month_data:
        abort(404)
    filename_base = _safe_download_stem(
        f"{project['mode']},{month_data['year']},{month_data['month']},{project['title']}"
    )
    if export_format == "csv":
        csv_text = _csv_text_for_month(
            project["title"],
            project["mode"],
            month_data,
            str(project.get("employee_number") or ""),
        )
        return send_file(
            io.BytesIO(csv_text.encode("utf-8-sig")),
            as_attachment=True,
            download_name=f"{filename_base}.csv",
            mimetype="text/csv; charset=utf-8",
        )
    if export_format == "xlsx":
        workbook_bytes = _xlsx_bytes_for_month(
            project["title"],
            project["mode"],
            month_data,
            str(project.get("employee_number") or ""),
        )
        return send_file(
            workbook_bytes,
            as_attachment=True,
            download_name=f"{filename_base}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if export_format == "calendar_png":
        png_bytes = _calendar_png_bytes_for_month(project["title"], project["mode"], month_data)
        return send_file(
            png_bytes,
            as_attachment=True,
            download_name=_calendar_download_name(project["title"], month_data["year"], month_data["month"]),
            mimetype="image/png",
        )
    abort(404)


@cloudshift_bp.route("/api/project/<project_id>/export/<export_format>")
@login_required
def api_export_owner(project_id: str, export_format: str):
    project, _ = _project_for_current_user_or_404(project_id)
    month_key = request.args.get("month_key", "")
    return _send_month_export(project, month_key, export_format)


@cloudshift_bp.route("/api/public/<token_type>/<token>")
def api_public_detail(token_type: str, token: str):
    if token_type not in {"view", "edit"}:
        abort(404)
    project = _find_project_by_token(token, token_type)
    month_key = request.args.get("month_key")
    payload = _project_detail_payload(project, month_key)
    if token_type == "view":
        payload["project"]["urls"] = {
            "view_url": payload["project"]["urls"]["view_url"],
        }
    payload["access_mode"] = token_type
    payload["authenticated_editor_name"] = _user_label() if current_user.is_authenticated else None
    return jsonify(payload)


@cloudshift_bp.route("/api/public/view/<token>/export/<export_format>")
def api_export_public_view(token: str, export_format: str):
    project = _find_project_by_token(token, "view")
    month_key = request.args.get("month_key", "")
    return _send_month_export(project, month_key, export_format)


@cloudshift_bp.route("/api/public/edit/<token>/export/<export_format>")
def api_export_public_edit(token: str, export_format: str):
    project = _find_project_by_token(token, "edit")
    month_key = request.args.get("month_key", "")
    return _send_month_export(project, month_key, export_format)


@cloudshift_bp.route("/api/public/edit/<token>/month", methods=["POST"])
def api_public_create_month(token: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        month_payload = _create_month_in_project(project, payload, actor_name, actor_type)
    month_key = _month_key(month_payload["year"], month_payload["month"])
    _resync_shift_month(project, month_key, actor_name=actor_name)
    _refresh_shift_sync_for_target_month(project, month_key, actor_name=actor_name)
    project = _find_project_by_token(token, "edit")
    return jsonify({"success": True, "project": _project_detail_payload(project, month_key)})


@cloudshift_bp.route("/api/public/edit/<token>/month/<int:year>/<int:month>", methods=["PUT"])
def api_public_save_month(token: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        month_payload = _save_month_in_project(project, year, month, payload, actor_name, actor_type)
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=actor_name)
    return jsonify({"success": True, "month": _client_month_payload(month_payload), "project": _project_detail_payload(project, month_key)})


@cloudshift_bp.route("/api/public/edit/<token>/assist")
def api_public_assist(token: str):
    project = _find_project_by_token(token, "edit")
    return jsonify(_assist_bootstrap_for_project(project, can_edit_records=True, can_edit_rules=False, can_edit_sites=True))


@cloudshift_bp.route("/api/public/edit/<token>/assist/sites", methods=["POST"])
def api_public_create_site(token: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, previous = _person_assist_site_mutation(
            project,
            payload,
            actor_name=actor_name,
            actor_type=actor_type,
        )
    previous_kind = str((previous or {}).get("kind") or "")
    current_kind = str(site.get("kind") or "")
    if current_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    elif previous_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project["id"], str(site.get("id") or ""), actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/sites/<site_id>", methods=["PUT"])
def api_public_update_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, previous = _person_assist_site_mutation(
            project,
            payload,
            actor_name=actor_name,
            actor_type=actor_type,
            site_id=site_id,
        )
    previous_kind = str((previous or {}).get("kind") or "")
    current_kind = str(site.get("kind") or "")
    if current_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    elif previous_kind == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project["id"], str(site.get("id") or ""), actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/sites/<site_id>", methods=["DELETE"])
def api_public_delete_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        deleted = _person_assist_site_delete(
            project,
            site_id,
            actor_name=actor_name,
            actor_type=actor_type,
        )
    if str(deleted.get("kind") or "") == PERSON_ASSIST_EXPERIENCE_KIND:
        _delete_person_experience_from_scene_projects(project["id"], site_id, actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_for_project(
                project,
                can_edit_records=True,
                can_edit_rules=False,
                can_edit_sites=True,
            )["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/experienced-sites", methods=["POST"])
def api_public_create_experienced_site(token: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="experienced",
            actor_name=actor_name,
            actor_type=actor_type,
        )
    _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/experienced-sites/<site_id>", methods=["PUT"])
def api_public_update_experienced_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="experienced",
            actor_name=actor_name,
            actor_type=actor_type,
            site_id=site_id,
        )
    _sync_person_experience_to_scene_projects(project, site, actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/experienced-sites/<site_id>", methods=["DELETE"])
def api_public_delete_experienced_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        _person_assist_site_delete(
            project,
            site_id,
            kind="experienced",
            actor_name=actor_name,
            actor_type=actor_type,
        )
    _delete_person_experience_from_scene_projects(project["id"], site_id, actor_name=actor_name)
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/training-sites", methods=["POST"])
def api_public_create_training_site(token: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="training",
            actor_name=actor_name,
            actor_type=actor_type,
        )
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/training-sites/<site_id>", methods=["PUT"])
def api_public_update_training_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        site, _ = _person_assist_site_mutation(
            project,
            payload,
            kind="training",
            actor_name=actor_name,
            actor_type=actor_type,
            site_id=site_id,
        )
    return jsonify(
        {
            "success": True,
            "site": _person_assist_site_payload(site),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/training-sites/<site_id>", methods=["DELETE"])
def api_public_delete_training_site(token: str, site_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        _person_assist_site_delete(
            project,
            site_id,
            kind="training",
            actor_name=actor_name,
            actor_type=actor_type,
        )
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/records", methods=["POST"])
def api_public_create_assist_record(token: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        record = _assist_record_mutation(project, payload, actor_name=actor_name, actor_type=actor_type)
    return jsonify(
        {
            "success": True,
            "record": _assist_record_payload(record),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/records/<record_id>", methods=["PUT"])
def api_public_update_assist_record(token: str, record_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        record = _assist_record_mutation(project, payload, actor_name=actor_name, actor_type=actor_type, record_id=record_id)
    return jsonify(
        {
            "success": True,
            "record": _assist_record_payload(record),
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/records/<record_id>", methods=["DELETE"])
def api_public_delete_assist_record(token: str, record_id: str):
    payload = request.get_json(silent=True) or {}
    actor_name, actor_type = _editor_identity(payload.get("editor_name"))
    project = _find_project_by_token(token, "edit")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "edit")
        _assist_record_delete(project, record_id, actor_name=actor_name, actor_type=actor_type)
    return jsonify(
        {
            "success": True,
            "assist": _assist_bootstrap_payload(project, can_edit_records=True, can_edit_rules=False)["assist"],
        }
    )


@cloudshift_bp.route("/api/public/edit/<token>/assist/rules", methods=["POST", "PUT", "DELETE"])
@cloudshift_bp.route("/api/public/edit/<token>/assist/rules/<rule_id>", methods=["PUT", "DELETE"])
def api_public_rules_readonly(token: str, rule_id: str | None = None):
    _find_project_by_token(token, "edit")
    raise CloudShiftError("編集者はシフトルールを変更できません", 403)


@cloudshift_bp.route("/api/public/edit/<token>/assist/profiles/<profile_id>", methods=["PUT"])
def api_public_profiles_readonly(token: str, profile_id: str):
    _find_project_by_token(token, "edit")
    raise CloudShiftError("編集者は候補者プロファイルを変更できません", 403)


@cloudshift_bp.route("/api/public/edit/<token>/assist/search", methods=["POST"])
def api_public_assist_search(token: str):
    payload = request.get_json(silent=True) or {}
    project = _find_project_by_token(token, "edit")
    return jsonify(_assist_search(project, payload))


@cloudshift_bp.route("/api/public/<token_type>/<token>/month/<int:year>/<int:month>/summary", methods=["GET", "POST"])
def api_public_month_summary(token_type: str, token: str, year: int, month: int):
    if token_type not in {"view", "edit"}:
        abort(404)
    project = _find_project_by_token(token, token_type)
    payload = request.get_json(silent=True) if request.method == "POST" else None
    return jsonify({"success": True, "summary": _summary_month_payload(project, year, month, payload)})
