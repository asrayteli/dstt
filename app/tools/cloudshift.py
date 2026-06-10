from __future__ import annotations

import io
import hashlib
import json
import logging
import os
import secrets
import sys
import tempfile
import time
import traceback
from calendar import monthrange
from contextlib import contextmanager
from copy import copy
from datetime import date, datetime, timedelta, timezone
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
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app.access_control import user_office_ids
from app.models import (
    AccessOffice,
    CloudShiftHistory,
    CloudShiftMonth,
    CloudShiftProject,
    CloudShiftPwaSubscription,
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

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SECONDS = 8.0
LOCK_POLL_SECONDS = 0.05
MAX_REVISION_SNAPSHOTS = 12
OPTION_LABELS = {**SHIFT_OPTION_MAPPINGS, **LEAVE_OPTION_MAPPINGS}
SHIFT_TIME_OPTION_KEYS = {"A", "P", "E", "L", "TEMP"}
VEHICLE_OPTION_KEYS = {"M", "C", "O", "W", "V", "N1", "N2", "N3", "N4", "N5"}
LEAVE_CHANGE_REQUEST_STATUSES = {"pending", "approved", "rejected"}
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
SHIFT_SYNC_MASTER_SOURCE = "master_shift"
SHIFT_SYNC_SUBSTITUTE_SOURCE = "substitute_shift"
SHIFT_SYNC_SUBSTITUTE_REQUEST_SOURCE = "substitute_request"
SHIFT_SYNC_SOURCE_TYPES = {
    SHIFT_SYNC_SCENE_SOURCE,
    SHIFT_SYNC_PERSON_SOURCE,
    SHIFT_SYNC_MASTER_SOURCE,
    SHIFT_SYNC_SUBSTITUTE_SOURCE,
    SHIFT_SYNC_SUBSTITUTE_REQUEST_SOURCE,
}
SUBSTITUTE_MODE = "substitute"
SUBSTITUTE_TITLE = "要代務シフト帳"
PERSON_UNASSIGNED_TITLE = "未割り当て"
JST = timezone(timedelta(hours=9))


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


@cloudshift_bp.errorhandler(Exception)
def _handle_unexpected_cloudshift_error(error: Exception):
    if request.path.startswith("/tools/shiftersync/cloudshift/api/"):
        try:
            db.session.rollback()
        except Exception as rollback_error:
            print(
                f"[CloudShift API ERROR] db.session.rollback failed: {type(rollback_error).__name__}: {rollback_error}",
                file=sys.stderr,
            )
        print("[CloudShift API ERROR]", file=sys.stderr)
        print(f"{request.method} {request.path}", file=sys.stderr)
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        # 例外の詳細（型名・メッセージ）はサーバログにのみ出力し、クライアントには
        # 内部情報を含まない汎用メッセージを返す（公開編集経路からの情報漏洩を防ぐ）。
        return jsonify({"error": "CloudShift内部エラーが発生しました。時間をおいて再度お試しください。"}), 500
    raise error


def _ensure_cloudshift_runtime_schema() -> None:
    if current_app.extensions.get("cloudshift_runtime_schema_ready"):
        return

    db.create_all()
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    alters: list[str] = []
    post_updates: list[str] = []

    if "cloudshift_projects" in tables:
        project_cols = {c["name"] for c in inspector.get_columns("cloudshift_projects")}
        if "site_manager_id" not in project_cols:
            alters.append("ALTER TABLE cloudshift_projects ADD COLUMN site_manager_id VARCHAR(20)")
        if "site_manager_name" not in project_cols:
            alters.append("ALTER TABLE cloudshift_projects ADD COLUMN site_manager_name VARCHAR(200)")
        if "account_shares" not in project_cols:
            alters.append("ALTER TABLE cloudshift_projects ADD COLUMN account_shares JSON")
            post_updates.append("UPDATE cloudshift_projects SET account_shares = '{}' WHERE account_shares IS NULL")
        if "assist" not in project_cols:
            alters.append("ALTER TABLE cloudshift_projects ADD COLUMN assist JSON")
            post_updates.append("UPDATE cloudshift_projects SET assist = '{}' WHERE assist IS NULL")
        if "extra_data" not in project_cols:
            alters.append("ALTER TABLE cloudshift_projects ADD COLUMN extra_data JSON")
            post_updates.append("UPDATE cloudshift_projects SET extra_data = '{}' WHERE extra_data IS NULL")

    if "cloudshift_months" in tables:
        month_cols = {c["name"] for c in inspector.get_columns("cloudshift_months")}
        if "draft_entries_per_day" not in month_cols:
            alters.append("ALTER TABLE cloudshift_months ADD COLUMN draft_entries_per_day JSON")
            post_updates.append("UPDATE cloudshift_months SET draft_entries_per_day = '{}' WHERE draft_entries_per_day IS NULL")
        if "revision" not in month_cols:
            alters.append("ALTER TABLE cloudshift_months ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "revision_snapshots" not in month_cols:
            alters.append("ALTER TABLE cloudshift_months ADD COLUMN revision_snapshots JSON")
            post_updates.append("UPDATE cloudshift_months SET revision_snapshots = '{}' WHERE revision_snapshots IS NULL")

    if "cloudshift_history" in tables:
        history_cols = {c["name"] for c in inspector.get_columns("cloudshift_history")}
        if "changes" not in history_cols:
            alters.append("ALTER TABLE cloudshift_history ADD COLUMN changes JSON")
            post_updates.append("UPDATE cloudshift_history SET changes = '[]' WHERE changes IS NULL")
        if "payload" not in history_cols:
            alters.append("ALTER TABLE cloudshift_history ADD COLUMN payload JSON")
            post_updates.append("UPDATE cloudshift_history SET payload = '{}' WHERE payload IS NULL")

    if alters or post_updates:
        _run_cloudshift_schema_statements(alters, ignore_duplicates=True)
        _run_cloudshift_schema_statements(post_updates)

    current_app.extensions["cloudshift_runtime_schema_ready"] = True


def _cloudshift_duplicate_schema_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "duplicate column" in message
        or "already exists" in message
        or "duplicate column name" in message
    )


def _run_cloudshift_schema_statements(statements: list[str], *, ignore_duplicates: bool = False) -> None:
    for sql in statements:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
        except SQLAlchemyError as exc:
            if ignore_duplicates and _cloudshift_duplicate_schema_error(exc):
                continue
            raise


@cloudshift_bp.before_request
def _ensure_cloudshift_schema_before_request():
    _ensure_cloudshift_runtime_schema()


def _utcnow_iso() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


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


def _current_month_key() -> str:
    today = datetime.now(JST).date()
    return _month_key(today.year, today.month)


def _parse_month_key(month_key: str) -> tuple[int, int]:
    year_text, month_text = month_key.split("-", 1)
    return int(year_text), int(month_text)


def _sort_month_keys(month_keys: list[str]) -> list[str]:
    return sorted(month_keys, key=lambda value: _parse_month_key(value))


def _project_id() -> str:
    return secrets.token_hex(12)


def _share_token() -> str:
    return secrets.token_urlsafe(32)


def _ensure_pwa_token(project: dict[str, Any]) -> bool:
    """ViewPWA 用トークンが無ければ発行する。発行したら True を返す（呼び出し側で保存）。"""
    if str(project.get("pwa_token") or "").strip():
        return False
    project["pwa_token"] = _share_token()
    return True


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
    if mode not in {"scene", "person", "master", SUBSTITUTE_MODE}:
        raise CloudShiftError("mode は scene、person、master、substitute のみ対応です", 400)
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


def _latest_site_link_fields(site_row_id: Any, site_id: Any, site_name: Any) -> dict[str, str]:
    if _coerce_site_row_id(site_row_id) is None:
        site_id_text = str(site_id or "").strip()
        if site_id_text:
            site = Site.query.filter_by(site_id=site_id_text).first()
            if site:
                return {
                    "site_row_id": str(site.id),
                    "site_id": str(site.site_id or "").strip(),
                    "site_name": str(site.site_name or "").strip(),
                }
    snapshot = _linked_site_snapshot_payload(site_row_id, site_id, site_name)
    return {
        "site_row_id": str(snapshot.get("site_row_id") or ""),
        "site_id": str(snapshot.get("site_id") or "").strip(),
        "site_name": str(snapshot.get("site_name") or "").strip(),
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


def _split_master_ref_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "").strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                raw_items = parsed
            else:
                raw_items = []
                for line in text.replace("、", ",").replace("\r", "\n").split("\n"):
                    raw_items.extend(line.split(","))
        else:
            raw_items = []
            for line in text.replace("、", ",").replace("\r", "\n").split("\n"):
                raw_items.extend(line.split(","))
    items: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            candidate = str(
                item.get("employee_number")
                or item.get("site_row_id")
                or item.get("site_id")
                or item.get("id")
                or ""
            ).strip()
        else:
            candidate = str(item or "").strip()
        if candidate and candidate not in items:
            items.append(candidate)
    return items


def _sanitize_master_target_type(value: Any) -> str:
    target_type = str(value or "").strip().lower()
    if target_type in {"person", "people"}:
        return "person"
    if target_type in {"scene", "site", "sites"}:
        return "scene"
    raise CloudShiftError("マスターシフトの対象種別は 個人 または 現場 を選択してください", 400)


def _master_payload_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
    return _split_master_ref_text(value)


def _master_people_from_payload(value: Any) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    seen: set[str] = set()
    raw_items = _master_payload_items(value)
    for item in raw_items:
        if isinstance(item, dict):
            number = _sanitize_employee_number(item.get("employee_number"))
            name = str(item.get("name") or item.get("employee_name") or "").strip()
        else:
            number = _sanitize_employee_number(item)
            name = ""
        if not number or number in seen:
            continue
        seen.add(number)
        people.append({"employee_number": number, "name": name or _employee_label_for_number(number)})
    return people


def _site_reference_from_master_ref(value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    site = None
    if text.isdigit():
        site = db.session.get(Site, int(text))
    if site is None:
        site = Site.query.filter_by(site_id=text).first()
    if site is None:
        site = Site.query.filter(Site.site_name == text).first()
    if site is None:
        raise CloudShiftError(f"マスターシフトの現場 {text} が見つかりません", 400)
    if not site.is_active:
        raise CloudShiftError(f"無効化された現場は選択できません: {site.site_id or site.site_name}", 400)
    return _load_site_reference(site.id, require_active=True)


def _master_sites_from_payload(value: Any) -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    seen: set[int] = set()
    raw_items = _master_payload_items(value)
    for item in raw_items:
        if isinstance(item, dict):
            ref = item.get("site_row_id") or item.get("id") or item.get("site_id") or item.get("site_name")
        else:
            ref = item
        site_ref = _site_reference_from_master_ref(ref)
        if not site_ref:
            continue
        site_row_id = int(site_ref["site_row_id"])
        if site_row_id in seen:
            continue
        seen.add(site_row_id)
        sites.append(site_ref)
    return sites


def _master_people_text(project: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("employee_number") or "").strip()
        for item in (project.get("master_people") or [])
        if isinstance(item, dict) and str(item.get("employee_number") or "").strip()
    )


def _master_sites_text(project: dict[str, Any]) -> str:
    return "\n".join(
        str(
            _latest_site_link_fields(
                item.get("site_row_id"),
                item.get("site_id"),
                item.get("site_name"),
            ).get("site_row_id")
            or item.get("site_row_id")
            or item.get("site_id")
            or ""
        ).strip()
        for item in (project.get("master_sites") or [])
        if isinstance(item, dict) and str(item.get("site_row_id") or item.get("site_id") or "").strip()
    )


def _master_target_type_for_project(project: dict[str, Any]) -> str:
    raw_target_type = str(project.get("master_target_type") or "").strip().lower()
    if raw_target_type in {"person", "scene"}:
        return raw_target_type
    if project.get("master_people"):
        return "person"
    return "scene"


def _master_scope_from_payload(data: Any, *, existing: dict[str, Any] | None = None) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    getter = data.get if hasattr(data, "get") else lambda key, default=None: default
    raw_people = getter("master_people", _master_people_text(existing or {}))
    raw_sites = getter("master_sites", _master_sites_text(existing or {}))
    raw_target_type = getter("master_target_type", "")
    if str(raw_target_type or "").strip():
        target_type = _sanitize_master_target_type(raw_target_type)
    elif existing:
        target_type = _master_target_type_for_project(existing)
    elif _master_payload_items(raw_people):
        target_type = "person"
    elif _master_payload_items(raw_sites):
        target_type = "scene"
    else:
        target_type = "scene"
    people = _master_people_from_payload(raw_people)
    sites = _master_sites_from_payload(raw_sites)
    raw_target_type = str(getter("master_target_type", "") or "").strip().lower()
    if raw_target_type:
        target_type = _sanitize_master_target_type(raw_target_type)
    elif people and not sites:
        target_type = "person"
    elif sites and not people:
        target_type = "scene"
    else:
        target_type = _sanitize_master_target_type(
            _master_target_type_for_project(existing or {})
        )
    if target_type == "person":
        if sites:
            raise CloudShiftError("個人マスターには現場を登録できません", 400)
        if not people:
            raise CloudShiftError("個人マスターには人物を1件以上登録してください", 400)
        return target_type, people, []
    if people:
        raise CloudShiftError("現場マスターには人物を登録できません", 400)
    if not sites:
        raise CloudShiftError("現場マスターには現場を1件以上登録してください", 400)
    return target_type, [], sites


def _format_entry_value(option_key: Any, name: Any) -> str:
    safe_name = str(name or "").strip()
    if not safe_name:
        return ""
    normalized_option = str(option_key or "").strip().upper()
    return f"!{normalized_option}!{safe_name}" if normalized_option else safe_name


def _entry_option_and_name(entry: dict[str, Any]) -> tuple[str, str]:
    option_key, raw_name = parse_entry_value(str((entry or {}).get("value") or ""))
    return str(option_key or "").strip().upper(), str(raw_name or "").strip()


def _entry_employee_name(entry: dict[str, Any]) -> str:
    if not isinstance(entry, dict):
        return ""
    stored = str(entry.get("employee_name") or entry.get("employeeName") or "").strip()
    if stored:
        return stored
    _, raw_name = _entry_option_and_name(entry)
    return raw_name


def _entry_is_shift_synced(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    return str(entry.get("sync_source_type") or "").strip() in SHIFT_SYNC_SOURCE_TYPES


def _entries_in_existing_order(
    entries: list[dict[str, Any]],
    existing_day_entries: Any,
) -> list[dict[str, Any]]:
    """Reorder a rebuilt day list to match the order already stored on the day.

    Used when synced entries are regenerated (possibly from several sources in
    separate passes): each pass appends its own entries last, which would otherwise
    scramble a manually chosen order. Sorting by the existing stored positions keeps
    that order stable, while entries with no prior position (genuinely new ones) keep
    their natural order at the end.
    """
    if len(entries) < 2:
        return entries
    existing_index: dict[str, int] = {}
    for entry in existing_day_entries if isinstance(existing_day_entries, list) else []:
        entry_id = str((entry or {}).get("id") or "").strip()
        if entry_id and entry_id not in existing_index:
            existing_index[entry_id] = len(existing_index)
    if not existing_index:
        return entries

    def _sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        natural_index, entry = pair
        entry_id = str((entry or {}).get("id") or "").strip()
        if entry_id in existing_index:
            return (0, existing_index[entry_id], natural_index)
        return (1, natural_index, 0)

    return [entry for _, entry in sorted(enumerate(entries), key=_sort_key)]


def _entry_site_link_fields(entry: dict[str, Any] | None) -> dict[str, str | None]:
    if not isinstance(entry, dict):
        return {"site_row_id": None, "site_id": "", "site_name": ""}
    site_row_id = _coerce_site_row_id(entry.get("site_row_id"))
    site_id = str(entry.get("site_id") or "").strip()
    option_key, raw_name = _entry_option_and_name(entry)
    site_name = str(entry.get("site_name") or "").strip() or raw_name
    if site_row_id is not None or site_id:
        latest = _latest_site_link_fields(site_row_id, site_id, site_name)
        latest_row_id = _coerce_site_row_id(latest.get("site_row_id"))
        if latest_row_id is None and site_row_id is None:
            return {
                "site_row_id": None,
                "site_id": site_id,
                "site_name": site_name,
            }
        return {
            "site_row_id": latest_row_id,
            "site_id": latest.get("site_id") or site_id,
            "site_name": latest.get("site_name") or site_name,
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
    option_key, name = _entry_option_and_name(normalized)
    site_row_id = _coerce_site_row_id(normalized.get("site_row_id"))
    if site_row_id is not None:
        latest = _latest_site_link_fields(
            site_row_id,
            normalized.get("site_id"),
            normalized.get("site_name") or name,
        )
        normalized["site_row_id"] = latest["site_row_id"]
        normalized["site_id"] = latest["site_id"]
        normalized["site_name"] = latest["site_name"] or name
        if normalized["site_name"]:
            normalized["value"] = _format_entry_value(option_key, normalized["site_name"])
        return normalized

    if str(normalized.get("site_id") or "").strip():
        latest = _latest_site_link_fields(
            None,
            normalized.get("site_id"),
            normalized.get("site_name") or name,
        )
        if latest.get("site_row_id"):
            normalized["site_row_id"] = latest["site_row_id"]
            normalized["site_id"] = latest["site_id"]
            normalized["site_name"] = latest["site_name"] or name
            if normalized["site_name"]:
                normalized["value"] = _format_entry_value(option_key, normalized["site_name"])
            return normalized

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
    project_mode = str(project.get("mode") or "")
    project_id = str(project.get("id") or "").strip()
    for day_key, entries in combined.items():
        incoming_day = normalized_incoming.get(day_key, [])
        current_day = normalized_current.get(day_key, [])

        # Server-authoritative synced entries, keyed by id (content source of truth).
        synced_by_id: dict[str, dict[str, Any]] = {}
        synced_server_order: list[str] = []
        for entry in current_day:
            if not _entry_is_shift_synced(entry):
                continue
            entry_id = str(entry.get("id") or "").strip()
            if entry_id and entry_id not in synced_by_id:
                synced_by_id[entry_id] = dict(entry)
                synced_server_order.append(entry_id)

        # Walk the client order, keeping local content from the client and synced
        # content from the server. Local and synced entries may be freely interleaved
        # (same-day ordering), but a client can only influence the ordering of synced
        # entries — never their content, nor which day they live on.
        ordered: list[dict[str, Any]] = []
        incoming_local_ids: set[str] = set()
        used_synced_ids: set[str] = set()
        for entry in incoming_day:
            if _entry_is_shift_synced(entry):
                entry_id = str(entry.get("id") or "").strip()
                if entry_id and entry_id in synced_by_id and entry_id not in used_synced_ids:
                    ordered.append(synced_by_id[entry_id])
                    used_synced_ids.add(entry_id)
                # Synced ids unknown for this day (cross-day move / fabricated) are dropped.
                continue
            next_entry = dict(entry)
            if project_mode == "scene":
                next_entry = _scene_entry_with_siteplus_defaults(project, next_entry)
            elif project_mode == "person":
                next_entry = _normalize_person_entry_site_link(next_entry)
            if _entry_value_uses_site_link(project, next_entry):
                next_entry = _entry_with_latest_site_link(next_entry, project)
            ordered.append(next_entry)
            entry_id = str(entry.get("id") or "").strip()
            if entry_id:
                incoming_local_ids.add(entry_id)

        # Retain superseded substitute source entries the client dropped (kept hidden).
        superseded_source_ids = {
            str(entry.get("substitute_source_entry_id") or "").strip()
            for entry in current_day
            if isinstance(entry, dict)
            and str(entry.get("sync_source_type") or "") == SHIFT_SYNC_SUBSTITUTE_SOURCE
            and _entry_resolved_flag(entry)
            and str(entry.get("substitute_source_project_id") or "").strip() == project_id
            and str(entry.get("substitute_source_entry_id") or "").strip()
            and str(entry.get("substitute_source_entry_id") or "").strip() != "day"
        }
        hidden_local_entries = [
            dict(entry)
            for entry in current_day
            if not _entry_is_shift_synced(entry)
            and str(entry.get("id") or "").strip() in superseded_source_ids
            and str(entry.get("id") or "").strip() not in incoming_local_ids
        ]

        # Synced entries the client did not position (newly synced server-side) stay last.
        remaining_synced = [
            synced_by_id[entry_id]
            for entry_id in synced_server_order
            if entry_id not in used_synced_ids
        ]

        combined[day_key] = ordered + hidden_local_entries + remaining_synced
    return combined


def _entry_resolved_flag(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    value = entry.get("substitute_resolved")
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _annotate_substitute_entries_for_save(
    project: dict[str, Any],
    entries_per_day: dict[str, list[dict[str, Any]]],
    previous_entries_per_day: Any,
    *,
    actor_name: str,
    actor_user_id: str,
) -> dict[str, list[dict[str, Any]]]:
    if project.get("mode") != SUBSTITUTE_MODE:
        return entries_per_day
    previous = {
        str(entry.get("id") or ""): entry
        for entries in _json_dict(previous_entries_per_day).values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("id") or "")
    }
    timestamp = _utcnow_iso()
    annotated = {day_key: [] for day_key in entries_per_day.keys()}
    for day_key, entries in entries_per_day.items():
        next_entries: list[dict[str, Any]] = []
        for entry in entries:
            next_entry = dict(entry)
            previous_entry = previous.get(str(next_entry.get("id") or ""))
            if previous_entry:
                for key in (
                    "substitute_requester_user_id",
                    "substitute_requester_name",
                    "substitute_requested_at",
                    "substitute_source_project_id",
                    "substitute_source_project_title",
                    "substitute_source_project_mode",
                    "substitute_source_month_key",
                    "substitute_source_day",
                    "substitute_source_entry_id",
                ):
                    if not str(next_entry.get(key) or "").strip() and str(previous_entry.get(key) or "").strip():
                        next_entry[key] = previous_entry.get(key)
            if not str(next_entry.get("substitute_requester_user_id") or "").strip() and actor_user_id:
                next_entry["substitute_requester_user_id"] = actor_user_id
            if not str(next_entry.get("substitute_requester_name") or "").strip() and actor_name:
                next_entry["substitute_requester_name"] = actor_name
            if not str(next_entry.get("substitute_requested_at") or "").strip():
                next_entry["substitute_requested_at"] = timestamp

            was_resolved = _entry_resolved_flag(previous_entry)
            is_resolved = _entry_resolved_flag(next_entry)
            if is_resolved:
                if not was_resolved or not str(next_entry.get("substitute_helper_user_id") or "").strip():
                    if actor_user_id:
                        next_entry["substitute_helper_user_id"] = actor_user_id
                    if actor_name:
                        next_entry["substitute_helper_name"] = actor_name
                    next_entry["substitute_helped_at"] = timestamp
                elif previous_entry:
                    for key in ("substitute_helper_user_id", "substitute_helper_name", "substitute_helped_at"):
                        if not str(next_entry.get(key) or "").strip() and str(previous_entry.get(key) or "").strip():
                            next_entry[key] = previous_entry.get(key)
            else:
                next_entry["substitute_helper_user_id"] = ""
                next_entry["substitute_helper_name"] = ""
                next_entry["substitute_helped_at"] = ""
            next_entries.append(next_entry)
        annotated[day_key] = next_entries
    return annotated


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


def _decode_json_storage_value(value: Any) -> Any:
    current = value
    for _ in range(2):
        if not isinstance(current, str):
            return current
        text_value = current.strip()
        if not text_value:
            return None
        try:
            current = json.loads(text_value)
        except json.JSONDecodeError:
            return current
    return current


def _json_dict(value: Any) -> dict[str, Any]:
    decoded = _decode_json_storage_value(value)
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: Any) -> list[Any]:
    decoded = _decode_json_storage_value(value)
    return decoded if isinstance(decoded, list) else []


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
    master_people = [item for item in (project.get("master_people") or []) if isinstance(item, dict)]
    master_sites = [
        {
            **item,
            **_latest_site_link_fields(
                item.get("site_row_id"),
                item.get("site_id"),
                item.get("site_name"),
            ),
        }
        for item in (project.get("master_sites") or [])
        if isinstance(item, dict)
    ]
    master_target_type = _master_target_type_for_project(project)
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
        "master": {
            "target_type": master_target_type,
            "target_label": "個人" if master_target_type == "person" else "現場",
            "people": master_people,
            "sites": master_sites,
            "people_count": len(master_people),
            "site_count": len(master_sites),
            "people_text": _master_people_text(project),
            "sites_text": _master_sites_text(project),
        },
    }
    if project.get("mode") == SUBSTITUTE_MODE:
        office_id = _substitute_office_id(project)
        office_label = _office_label_map({office_id}).get(office_id, str(office_id)) if office_id else ""
        summary["substitute"] = {
            "office_id": office_id,
            "office_label": office_label,
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
    summary["shift_book"] = {
        "settings": _shift_book_settings(project),
        "pending_leave_change_request_count": _leave_change_request_pending_count(project),
        "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
    }
    return summary


def _project_public_urls(project: dict[str, Any]) -> dict[str, str]:
    urls = {
        "view_url": url_for("cloudshift.public_view", token=project["view_token"], _external=True),
        "edit_url": url_for("cloudshift.public_edit", token=project["edit_token"], _external=True),
        "pwa_url": "",
    }
    pwa_token = str(project.get("pwa_token") or "").strip()
    if pwa_token:
        urls["pwa_url"] = url_for("cloudshift.public_pwa", token=pwa_token, _external=True)
    return urls


def _entry_value_uses_site_link(project: dict[str, Any] | None, entry: dict[str, Any]) -> bool:
    if not project or not _coerce_site_row_id(entry.get("site_row_id")):
        return False
    option_key, _ = _entry_option_and_name(entry)
    if option_key in LEAVE_OPTION_MAPPINGS:
        return False
    mode = str(project.get("mode") or "").strip()
    if mode == "person":
        return True
    return mode == "master" and _master_target_type_for_project(project) == "person"


def _entry_with_latest_site_link(entry: dict[str, Any], project: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return entry
    site_row_id = _coerce_site_row_id(entry.get("site_row_id"))
    if site_row_id is None and not str(entry.get("site_id") or "").strip():
        return dict(entry)

    updated = dict(entry)
    latest = _latest_site_link_fields(
        site_row_id,
        entry.get("site_id"),
        entry.get("site_name"),
    )
    updated["site_row_id"] = latest["site_row_id"]
    updated["site_id"] = latest["site_id"]
    updated["site_name"] = latest["site_name"]
    if latest["site_name"] and _entry_value_uses_site_link(project, updated):
        option_key, _ = _entry_option_and_name(updated)
        updated["value"] = _format_entry_value(option_key, latest["site_name"])
    return updated


def _entries_with_latest_site_links(entries_per_day: Any, project: dict[str, Any] | None) -> Any:
    if not isinstance(entries_per_day, dict):
        return entries_per_day
    return {
        str(day): [
            _entry_with_latest_site_link(entry, project) if isinstance(entry, dict) else entry
            for entry in (entries if isinstance(entries, list) else [])
        ]
        for day, entries in entries_per_day.items()
    }


def _entries_without_substitute_superseded_sources(
    entries_per_day: Any,
    project: dict[str, Any] | None,
) -> Any:
    if not isinstance(entries_per_day, dict) or not project:
        return entries_per_day
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        return entries_per_day
    filtered: dict[str, list[dict[str, Any]]] = {}
    for day_key, entries in entries_per_day.items():
        day_entries = [entry for entry in (entries if isinstance(entries, list) else []) if isinstance(entry, dict)]
        superseded_source_ids = {
            str(entry.get("substitute_source_entry_id") or "").strip()
            for entry in day_entries
            if str(entry.get("sync_source_type") or "") == SHIFT_SYNC_SUBSTITUTE_SOURCE
            and _entry_resolved_flag(entry)
            and str(entry.get("substitute_source_project_id") or "").strip() == project_id
            and str(entry.get("substitute_source_entry_id") or "").strip()
            and str(entry.get("substitute_source_entry_id") or "").strip() != "day"
        }
        if not superseded_source_ids:
            filtered[str(day_key)] = day_entries
            continue
        filtered[str(day_key)] = [
            entry
            for entry in day_entries
            if str(entry.get("id") or "").strip() not in superseded_source_ids
        ]
    return filtered


def _pending_substitute_request_entries_for_month(
    project: dict[str, Any] | None,
    month_data: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    if not project or not month_data or project.get("mode") not in {"scene", "person"}:
        return {}
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        return {}
    month_key = _month_key(month_data["year"], month_data["month"])
    result = _empty_entries_for_month(month_data["year"], month_data["month"])
    for substitute_project in _iter_stored_projects():
        if str(substitute_project.get("mode") or "") != SUBSTITUTE_MODE:
            continue
        substitute_month = (substitute_project.get("months") or {}).get(month_key)
        if not substitute_month:
            continue
        entries_per_day = _normalize_entries(
            substitute_month.get("entries_per_day"),
            month_data["year"],
            month_data["month"],
        )
        for day_key, entries in entries_per_day.items():
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("substitute_source_project_id") or "") != project_id:
                    continue
                if str(entry.get("substitute_source_month_key") or "") != month_key:
                    continue
                source_entry_id = str(entry.get("substitute_source_entry_id") or "").strip()
                if _entry_resolved_flag(entry):
                    source_day_entries = (month_data.get("entries_per_day") or {}).get(str(day_key), [])
                    has_resolved_sync_entry = any(
                        isinstance(source_entry, dict)
                        and str(source_entry.get("sync_source_type") or "") == SHIFT_SYNC_SUBSTITUTE_SOURCE
                        and str(source_entry.get("substitute_source_project_id") or "") == project_id
                        and str(source_entry.get("substitute_source_entry_id") or "").strip() == source_entry_id
                        for source_entry in (source_day_entries if isinstance(source_day_entries, list) else [])
                    )
                    if has_resolved_sync_entry:
                        continue
                display_entry = dict(entry)
                display_entry["id"] = f"substitute-request-display-{display_entry.get('id') or project_id}-{day_key}"
                display_entry["sync_source_type"] = SHIFT_SYNC_SUBSTITUTE_REQUEST_SOURCE
                display_entry["substitute_resolved"] = _entry_resolved_flag(entry)
                display_entry["sync_source_project_id"] = str(substitute_project.get("id") or "")
                display_entry["sync_source_project_title"] = str(substitute_project.get("title") or SUBSTITUTE_TITLE)
                display_entry["sync_source_month_key"] = month_key
                display_entry["sync_source_day"] = str(day_key)
                display_entry["sync_source_entry_id"] = str(entry.get("id") or "")
                result.setdefault(str(day_key), []).append(display_entry)
    return {day_key: entries for day_key, entries in result.items() if entries}


def _client_month_payload(
    month_data: dict[str, Any] | None,
    *,
    include_draft: bool = False,
    project: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not month_data:
        return None
    payload = dict(month_data)
    payload.pop("revision_snapshots", None)
    payload["entries_per_day"] = _entries_with_latest_site_links(payload.get("entries_per_day"), project)
    payload["entries_per_day"] = _entries_without_substitute_superseded_sources(payload.get("entries_per_day"), project)
    payload["pending_substitute_entries_per_day"] = _entries_with_latest_site_links(
        _pending_substitute_request_entries_for_month(project, month_data),
        project,
    )
    if not include_draft:
        payload.pop("draft_entries_per_day", None)
    else:
        payload["draft_entries_per_day"] = _entries_with_latest_site_links(payload.get("draft_entries_per_day"), project)
        payload["draft_entries_per_day"] = _entries_without_substitute_superseded_sources(payload.get("draft_entries_per_day"), project)
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
        "account_shares": _json_dict(row.account_shares),
        "assist": _json_dict(row.assist),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "months": {},
    }
    for key, value in _json_dict(row.extra_data).items():
        if key not in project:
            project[key] = value
    for month_row in row.months:
        month_key = _month_key(month_row.year, month_row.month)
        project["months"][month_key] = {
            "year": month_row.year,
            "month": month_row.month,
            "capacity_enabled": bool(month_row.capacity_enabled),
            "required_capacity": int(month_row.required_capacity or 0),
            "entries_per_day": _json_dict(month_row.entries_per_day),
            "draft_entries_per_day": _json_dict(month_row.draft_entries_per_day),
            "revision": int(month_row.revision or 1),
            "created_at": month_row.created_at,
            "updated_at": month_row.updated_at,
            "revision_snapshots": _json_dict(month_row.revision_snapshots),
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
    row.account_shares = _json_dict(project.get("account_shares"))
    row.assist = _json_dict(project.get("assist"))
    row.extra_data = {key: value for key, value in project.items() if key not in _PROJECT_STORAGE_KEYS}
    row.created_at = str(project.get("created_at") or _utcnow_iso())
    row.updated_at = str(project.get("updated_at") or _utcnow_iso())

    for month_row in list(row.months):
        db.session.delete(month_row)
    db.session.flush()

    for month_key, month_data in _json_dict(project.get("months")).items():
        if not isinstance(month_data, dict):
            continue
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
                revision_snapshots=_json_dict(month_data.get("revision_snapshots")),
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
    # 要代務シフト帳の変更差分を計算するため、保存前に旧状態を取得しておく。
    old_entries_by_month: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if _is_substitute_project(project):
        try:
            from flask import has_request_context, request

            if has_request_context() and request.method != "GET":
                old = _db_project_from_id(project["id"])
                if old:
                    for mk, mdata in (old.get("months") or {}).items():
                        old_entries_by_month[str(mk)] = dict((mdata or {}).get("entries_per_day") or {})
        except Exception:
            old_entries_by_month = {}

    project["updated_at"] = _utcnow_iso()
    _upsert_project_to_db(project)
    _notify_tobell_on_substitute_save(project, old_entries_by_month)


_SUBSTITUTE_DIFF_KEYS = (
    "employee_name", "employee_number",
    "site_name", "site_id", "site_row_id",
    "option_key", "request_type", "substitute_request_type",
    "substitute_resolved",
    "substitute_helper_employee_name", "substitute_helper_employee_number",
    "substitute_helper_site_name", "substitute_helper_site_id",
    "comment", "value",
    "substitute_source_project_title", "substitute_source_project_id",
    "substitute_source_month_key", "substitute_source_day",
)


def _substitute_entries_differ(old: dict[str, Any], new: dict[str, Any]) -> bool:
    for key in _SUBSTITUTE_DIFF_KEYS:
        if str(old.get(key) or "") != str(new.get(key) or ""):
            return True
    return False


def _format_substitute_entry_label(entry: dict[str, Any]) -> str:
    """エントリから「誰の/どこの」要請かをまとめた1行ラベルを作る。"""
    parts: list[str] = []
    person = (entry.get("employee_name") or "").strip()
    site = (entry.get("site_name") or "").strip()
    request_type = (entry.get("substitute_request_type") or entry.get("request_type") or "").strip()
    option = (entry.get("option_key") or "").strip()
    if not option:
        # value="A-名前" のように埋め込まれている場合は parse_entry_value で取り出す
        try:
            parsed_option, _ = parse_entry_value(str(entry.get("value") or ""))
            option = str(parsed_option or "").strip().upper()
        except Exception:
            option = ""
    helper = (entry.get("substitute_helper_employee_name") or "").strip()
    helper_site = (entry.get("substitute_helper_site_name") or "").strip()
    is_unassigned_helper = bool(entry.get("substitute_unassigned_helper"))

    if request_type == "person":
        # 個人型: 「誰が」 「どこへ」(任意)
        if person:
            parts.append(person)
        if site:
            parts.append(f"→ {site}")
    elif request_type == "scene":
        # 現場型: 「どこへ」 「誰が代務」(任意/未設定可)
        if site:
            parts.append(site)
        if is_unassigned_helper or not helper:
            parts.append("(代務者未設定)")
        elif helper_site and helper_site != site:
            parts.append(f"← {helper}({helper_site})")
        else:
            parts.append(f"← {helper}")
    else:
        # フォールバック: 取れる情報を並べる
        if person:
            parts.append(person)
        if site:
            parts.append(site)
    if option:
        parts.append(f"[{option}]")
    return " ".join(parts).strip() or "(内容不明)"


def _notify_tobell_on_substitute_save(
    project: dict[str, Any],
    old_entries_by_month: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
) -> None:
    """要代務シフト帳のあらゆる保存経路でToBellへ通知する。失敗してもCloudShiftを止めない。

    GETリクエストや非HTTPコンテキストでは発火しない（_ensure_substitute_project_for_office_month
    のような初期化保存で誤発火しないため）。
    """
    if not _is_substitute_project(project):
        return
    try:
        from flask import has_request_context, request

        if not has_request_context() or request.method == "GET":
            return

        # 旧状態と比較して、本当に変更があったときだけ通知する
        changes: list[dict[str, Any]] = []
        old_by_month = old_entries_by_month or {}
        new_months = project.get("months") or {}
        seen_months = set(old_by_month.keys()) | set(new_months.keys())
        for mk in sorted(seen_months):
            new_entries = (new_months.get(mk) or {}).get("entries_per_day") or {}
            old_entries = old_by_month.get(mk) or {}
            day_keys = set(old_entries.keys()) | set(new_entries.keys())
            for dk in sorted(day_keys, key=lambda x: int(x) if str(x).isdigit() else 9999):
                old_list = old_entries.get(dk) or []
                new_list = new_entries.get(dk) or []
                old_by_id = {str(e.get("id") or ""): e for e in old_list if e.get("id")}
                new_by_id = {str(e.get("id") or ""): e for e in new_list if e.get("id")}
                for eid, entry in new_by_id.items():
                    if eid not in old_by_id:
                        changes.append({"action": "added", "month_key": str(mk), "day_key": str(dk),
                                        "label": _format_substitute_entry_label(entry)})
                    elif _substitute_entries_differ(old_by_id[eid], entry):
                        changes.append({"action": "modified", "month_key": str(mk), "day_key": str(dk),
                                        "label": _format_substitute_entry_label(entry)})
                for eid, entry in old_by_id.items():
                    if eid not in new_by_id:
                        changes.append({"action": "removed", "month_key": str(mk), "day_key": str(dk),
                                        "label": _format_substitute_entry_label(entry)})

        if not changes:
            # 差分なし（タイトル変更や付帯情報の更新のみ）→ 通知しない
            return

        from app.services.to_bell_hooks import on_cloudshift_substitute_updated

        on_cloudshift_substitute_updated(
            substitute_project_id=str(project.get("id") or ""),
            substitute_project_title=str(project.get("title") or SUBSTITUTE_TITLE),
            changes=changes,
        )
    except Exception:
        pass


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
            payload = _json_dict(item.payload)
            payload.update(
                {
                    "timestamp": item.timestamp,
                    "editor_name": item.editor_name,
                    "editor_type": item.editor_type,
                    "action": item.action,
                    "month_key": item.month_key,
                    "changes": _json_list(item.changes),
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


def _substitute_project_id(office_id: int) -> str:
    digest = hashlib.sha1(f"substitute-office:{int(office_id)}".encode("utf-8")).hexdigest()
    return f"sub_{digest[:16]}"


def _substitute_request_entry_id(source_project_id: str, month_key: str, day_key: str, source_entry_id: str) -> str:
    digest = hashlib.sha1(
        "::".join(
            [
                "substitute-request",
                str(source_project_id or ""),
                str(month_key or ""),
                str(day_key or ""),
                str(source_entry_id or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"subreq_{digest[:16]}"


def _substitute_office_id(project: dict[str, Any]) -> int | None:
    if project.get("mode") != SUBSTITUTE_MODE:
        return None
    try:
        office_id = int(project.get("substitute_office_id") or 0)
    except (TypeError, ValueError):
        return None
    return office_id if office_id > 0 else None


def _is_substitute_project(project: dict[str, Any]) -> bool:
    return project.get("mode") == SUBSTITUTE_MODE and _substitute_office_id(project) is not None


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


def _project_link_key(project: dict[str, Any]) -> tuple[str, str] | None:
    """個人=社員ID、現場=現場 を一意に表す紐づけキーを返す。

    紐づけが無い場合（未割り当ての個人シフト・現場未設定の現場シフト）や、
    対象外モード（マスター・要代務）は ``None`` を返す。
    """
    mode = str(project.get("mode") or "").strip()
    if mode == "person":
        employee_number = str(project.get("employee_number") or "").strip()
        return ("person", employee_number) if employee_number else None
    if mode == "scene":
        # 現場は site_id（自然キー）を優先する。新規・レガシーいずれの帳簿でも
        # site_id は保存されているため、同一現場の表現ゆれ（row 参照 / site_id）を
        # 吸収して重複を確実に検知できる。
        site_id = str(project.get("site_id") or "").strip()
        if site_id:
            return ("scene", f"sid:{site_id}")
        site_row_id = _coerce_site_row_id(project.get("site_row_id"))
        return ("scene", f"row:{site_row_id}") if site_row_id else None
    return None


def _owner_display_label(owner_user_id: Any) -> str:
    """オーナーの『社員ID（表示名）』を返す。表示名が取れない場合は社員IDのみ。"""
    owner_id = str(owner_user_id or "").strip()
    if not owner_id:
        return "不明なユーザー"
    name = ""
    user = User.query.filter_by(username=owner_id).first()
    if user and user.name and user.name != "unknown":
        name = str(user.name)
    if not name:
        employee = Employee.query.filter_by(employee_number=owner_id).first()
        if employee and employee.employee_name:
            name = str(employee.employee_name)
    return f"{owner_id}（{name}）" if name else owner_id


def _find_projects_with_link_key(
    link_key: tuple[str, str] | None, *, exclude_id: str | None = None
) -> list[dict[str, Any]]:
    """同一紐づけキーを持つ既存シフト帳を返す。"""
    if not link_key:
        return []
    matches: list[dict[str, Any]] = []
    for project in _iter_stored_projects():
        if exclude_id and str(project.get("id") or "") == str(exclude_id):
            continue
        if _project_link_key(project) == link_key:
            matches.append(project)
    return matches


def _assert_link_key_unique(
    link_key: tuple[str, str] | None, *, exclude_id: str | None = None
) -> None:
    """同一紐づけのシフト帳が既に存在する場合はエラーを送出する。

    - 他オーナーが所持 → 要件2: 既に作成済みである旨と社員ID＋表示名を案内
    - 自分が所持       → 要件3: 同じ紐づけは1つだけである旨を案内
    """
    if not link_key:
        return
    current_owner = _user_id()
    matches = _find_projects_with_link_key(link_key, exclude_id=exclude_id)
    if not matches:
        return
    others = [
        project
        for project in matches
        if str(project.get("owner_user_id") or "") != current_owner
    ]
    if others:
        owner_label = _owner_display_label(others[0].get("owner_user_id"))
        raise CloudShiftError(
            f"{owner_label}さんが既に作成済みです。閲覧したい場合は共有してもらってください。",
            409,
        )
    existing = matches[0]
    raise CloudShiftError(
        f"同じ対象のシフト帳「{existing.get('title')}」が既に存在します。"
        "同じ紐づけのシフト帳は1つだけ作成できます。",
        409,
    )


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


def _shift_book_settings(project: dict[str, Any]) -> dict[str, Any]:
    raw = project.get("shift_book_settings") if isinstance(project.get("shift_book_settings"), dict) else {}
    leave_requests = raw.get("leave_change_requests") if isinstance(raw.get("leave_change_requests"), dict) else {}
    return {
        "leave_change_requests": {
            "enabled": bool(leave_requests.get("enabled")),
        }
    }


def _leave_change_request_pending_count(project: dict[str, Any]) -> int:
    return sum(1 for item in _normalized_leave_change_requests(project) if item.get("status") == "pending")


def _leave_change_request_unviewed_count(project: dict[str, Any]) -> int:
    return sum(
        1
        for item in _normalized_leave_change_requests(project)
        if item.get("status") == "pending" and not item.get("owner_viewed_at")
    )


def _leave_change_request_enabled(project: dict[str, Any]) -> bool:
    settings = _shift_book_settings(project)
    return project.get("mode") == "person" and bool(settings["leave_change_requests"]["enabled"])


def _normalized_leave_change_requests(project: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = project.get("leave_change_requests") if isinstance(project.get("leave_change_requests"), list) else []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("id") or "").strip()
        if not request_id or request_id in seen_ids:
            continue
        seen_ids.add(request_id)
        status = str(item.get("status") or "pending").strip()
        if status not in LEAVE_CHANGE_REQUEST_STATUSES:
            status = "pending"
        day = item.get("day")
        try:
            day = int(day)
        except (TypeError, ValueError):
            day = 0
        old_option_key = str(item.get("old_option_key") or "").strip().upper()
        requested_option_key = str(item.get("requested_option_key") or "").strip().upper()
        normalized.append(
            {
                "id": request_id,
                "status": status,
                "month_key": str(item.get("month_key") or "").strip(),
                "day": day,
                "entry_id": str(item.get("entry_id") or "").strip(),
                "entry_name": str(item.get("entry_name") or "").strip(),
                "old_option_key": old_option_key,
                "old_leave_type": LEAVE_OPTION_MAPPINGS.get(old_option_key, old_option_key),
                "requested_option_key": requested_option_key,
                "requested_leave_type": LEAVE_OPTION_MAPPINGS.get(requested_option_key, requested_option_key),
                "request_comment": str(item.get("request_comment") or "").strip(),
                "requested_at": str(item.get("requested_at") or "").strip(),
                "owner_viewed_at": str(item.get("owner_viewed_at") or "").strip(),
                "decided_at": str(item.get("decided_at") or "").strip(),
                "decided_by": str(item.get("decided_by") or "").strip(),
                "decided_by_name": str(item.get("decided_by_name") or "").strip(),
                "decision_reason": str(item.get("decision_reason") or "").strip(),
            }
        )
    normalized.sort(key=lambda item: item.get("requested_at") or "", reverse=True)
    normalized.sort(key=lambda item: 0 if item["status"] == "pending" else 1)
    return normalized


def _store_leave_change_requests(project: dict[str, Any], requests: list[dict[str, Any]]) -> None:
    project["leave_change_requests"] = [
        {
            key: value
            for key, value in item.items()
            if value not in (None, "")
        }
        for item in requests
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def _pending_leave_change_request_entry_ids(project: dict[str, Any], month_key: str) -> list[str]:
    ids: list[str] = []
    for item in _normalized_leave_change_requests(project):
        entry_id = str(item.get("entry_id") or "").strip()
        if item.get("status") == "pending" and item.get("month_key") == month_key and entry_id and entry_id not in ids:
            ids.append(entry_id)
    return ids


def _mark_leave_change_requests_viewed(project: dict[str, Any]) -> bool:
    requests = _normalized_leave_change_requests(project)
    timestamp = _utcnow_iso()
    changed = False
    for item in requests:
        if item.get("status") == "pending" and not item.get("owner_viewed_at"):
            item["owner_viewed_at"] = timestamp
            changed = True
    if changed:
        _store_leave_change_requests(project, requests)
        _save_project(project)
    return changed


def _project_is_shared_with_current_user(project: dict[str, Any]) -> bool:
    if not current_user.is_authenticated:
        return False
    if _is_substitute_project(project):
        office_id = _substitute_office_id(project)
        return bool(office_id and office_id in _current_share_office_ids())
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
    if _is_substitute_project(project) and _project_is_shared_with_current_user(project):
        return project, "editor"
    if _project_is_shared_with_current_user(project):
        return project, "viewer"
    abort(404)


def _editable_project_or_404(project_id: str) -> tuple[dict[str, Any], str]:
    project, access_role = _project_for_current_user_or_404(project_id)
    if access_role not in {"owner", "editor"}:
        abort(404)
    return project, access_role


def _share_status_for_current_user(project: dict[str, Any]) -> dict[str, Any] | None:
    if _is_substitute_project(project) and _project_is_shared_with_current_user(project):
        office_id = _substitute_office_id(project)
        offices = _office_label_map({office_id}) if office_id else {}
        return {
            "role": "editor",
            "enabled": True,
            "settings": {
                "office": {
                    "enabled": True,
                    "office_ids": [office_id] if office_id else [],
                    "offices": [
                        {"id": office_id, "label": offices.get(office_id, str(office_id))}
                    ] if office_id else [],
                },
                "employees": [],
            },
        }
    if current_user.is_authenticated and project.get("owner_user_id") == _user_id():
        shares = _normalized_account_shares(project)
        enabled = bool(shares["office"].get("enabled")) or bool(shares["employees"])
        return {"role": "owner", "enabled": enabled, "settings": shares}
    if _project_is_shared_with_current_user(project):
        return {"role": "viewer", "enabled": True}
    return None


def _ensure_substitute_project_for_office_month(office_id: int, year: int, month: int) -> dict[str, Any]:
    year, month = _validate_year_month(year, month)
    office_id = int(office_id)
    project_id = _substitute_project_id(office_id)
    month_key = _month_key(year, month)
    project = _db_project_from_id(project_id)
    if project is None:
        project = {
            "id": project_id,
            "owner_user_id": "",
            "title": SUBSTITUTE_TITLE,
            "mode": SUBSTITUTE_MODE,
            "employee_number": "",
            **_site_storage_fields(None),
            "master_target_type": "",
            "master_people": [],
            "master_sites": [],
            "substitute_office_id": office_id,
            "view_token": _share_token(),
            "edit_token": _share_token(),
            "pwa_token": _share_token(),
            "account_shares": {
                "office": {
                    "enabled": True,
                    "office_ids": [office_id],
                },
                "employees": [],
                "updated_at": _utcnow_iso(),
                "updated_by": "system",
            },
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "months": {
                month_key: _build_month_payload(year, month, False, 0, {}),
            },
        }
        _save_project(project)
        db.session.expire_all()
        return _load_project(project_id)

    changed = False
    if month_key not in (project.get("months") or {}):
        project.setdefault("months", {})[month_key] = _build_month_payload(year, month, False, 0, {})
        changed = True
    project["account_shares"] = {
        "office": {
            "enabled": True,
            "office_ids": [office_id],
        },
        "employees": [],
        "updated_at": _utcnow_iso(),
        "updated_by": "system",
    }
    if changed or project.get("account_shares"):
        _save_project(project)
        db.session.expire_all()
        return _load_project(project_id)
    return project


def _ensure_substitute_projects_for_current_user() -> None:
    if not current_user.is_authenticated:
        return
    office_ids = sorted(_current_share_office_ids())
    if not office_ids:
        return
    today = datetime.now(JST).date()
    changed = False
    for office_id in office_ids:
        _ensure_substitute_project_for_office_month(int(office_id), today.year, today.month)
        changed = True
    if changed:
        db.session.expire_all()


def _find_project_by_token(token: str, token_type: str) -> dict[str, Any]:
    key = {"view": "view_token", "edit": "edit_token", "pwa": "pwa_token"}.get(token_type, "edit_token")
    token = str(token or "")
    if not token:
        abort(404)
    # view_token / edit_token は一意インデックス付きの DB カラムなので直接検索し、
    # 全プロジェクト・全月のメモリ展開（重い線形走査）を避ける。トークンは
    # 256bit 乱数のため等価検索でも実質的なタイミング攻撃の懸念はない。
    if key in ("view_token", "edit_token"):
        column = getattr(CloudShiftProject, key)
        row = CloudShiftProject.query.filter(column == token).first()
        if row is not None:
            return _db_project_to_dict(row)
        # DB に無い場合のみ、レガシー JSON ファイルを走査する（DB は再走査しない）。
        for path in _shifts_dir().glob("*.json"):
            project = _load_json(path)
            if not project:
                continue
            candidate = str(project.get(key, ""))
            if candidate and secrets.compare_digest(candidate, token):
                return project
        abort(404)
    # pwa_token は extra_data(JSON) 内のためインデックス検索できず、従来どおり走査する。
    for project in _iter_stored_projects():
        candidate = str(project.get(key, ""))
        if candidate and secrets.compare_digest(candidate, token):
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
        "month": _client_month_payload(month_data, project=project),
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
    return _client_month_payload(month_data, include_draft=False, project=None) or {}


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


def _person_assist_op_label(value: Any) -> str:
    return "OPあり" if _assist_bool(value, False) else "OPなし"


def _normalized_site_title(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).casefold()


def _iter_project_summaries() -> list[dict[str, Any]]:
    return _iter_stored_projects()


def _matching_person_project_ids_for_scene_entry(source_project: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    employee_number = str(entry.get("employee_number") or "").strip()
    employee_name = _entry_employee_name(entry)
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


def _build_person_synced_entry_from_master(
    source_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    option_key, _ = _entry_option_and_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    site_link = _entry_site_link_fields(entry)
    site_name = str(site_link.get("site_name") or "").strip()
    return {
        "id": _sync_entry_id(SHIFT_SYNC_MASTER_SOURCE, source_project.get("id"), "person", month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, site_name),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_number": "",
        "site_row_id": str(site_link.get("site_row_id") or ""),
        "site_id": str(site_link.get("site_id") or "").strip(),
        "site_name": site_name,
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": SHIFT_SYNC_MASTER_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }


def _build_scene_synced_entry_from_master(
    source_project: dict[str, Any],
    target_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    option_key, _ = _entry_option_and_name(entry)
    employee_name = _entry_employee_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    branch_fields = _scene_branch_fields_for_option(target_project, option_key)
    synced = {
        "id": _sync_entry_id(SHIFT_SYNC_MASTER_SOURCE, source_project.get("id"), "scene", month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, employee_name),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_name": employee_name,
        "employee_number": str(entry.get("employee_number") or "").strip(),
        "site_row_id": "",
        "site_id": "",
        "site_name": "",
        "site_branch_row_id": branch_fields["site_branch_row_id"],
        "site_branch": branch_fields["site_branch"],
        "sync_source_type": SHIFT_SYNC_MASTER_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }
    return _scene_entry_with_siteplus_defaults(target_project, synced)


def _matching_master_project_ids_for_person_project(person_project: dict[str, Any]) -> list[str]:
    """個人シフト帳が登録されている個人型マスターのIDリストを返す。"""
    employee_number = str(person_project.get("employee_number") or "").strip()
    employee_name = _normalized_person_title(str(person_project.get("title") or ""))
    result: list[str] = []
    for project in _iter_project_summaries():
        if project.get("mode") != "master":
            continue
        if _master_target_type_for_project(project) != "person":
            continue
        master_people = [item for item in (project.get("master_people") or []) if isinstance(item, dict)]
        people_numbers = {str(item.get("employee_number") or "").strip() for item in master_people}
        people_names = {_normalized_person_title(item.get("name")) for item in master_people if str(item.get("name") or "").strip()}
        if (employee_number and employee_number in people_numbers) or (employee_name and employee_name in people_names):
            project_id = str(project.get("id") or "").strip()
            if project_id:
                result.append(project_id)
    return result


def _person_project_matches_master_scope(master_project: dict[str, Any], person_project: dict[str, Any]) -> bool:
    if _master_target_type_for_project(master_project) != "person":
        return False
    employee_number = str(person_project.get("employee_number") or "").strip()
    employee_name = _normalized_person_title(str(person_project.get("title") or ""))
    master_people = [item for item in (master_project.get("master_people") or []) if isinstance(item, dict)]
    people_numbers = {str(item.get("employee_number") or "").strip() for item in master_people}
    people_names = {
        _normalized_person_title(item.get("name"))
        for item in master_people
        if str(item.get("name") or "").strip()
    }
    return bool(
        (employee_number and employee_number in people_numbers)
        or (employee_name and employee_name in people_names)
    )


def _matching_master_project_ids_for_scene_project(scene_project: dict[str, Any]) -> list[str]:
    """現場シフト帳が登録されている現場型マスターのIDリストを返す。"""
    site_row_id = _coerce_site_row_id(scene_project.get("site_row_id"))
    site_id = str(scene_project.get("site_id") or "").strip()
    site_name = _normalized_site_title(str(scene_project.get("site_name") or scene_project.get("title") or ""))
    result: list[str] = []
    for project in _iter_project_summaries():
        if project.get("mode") != "master":
            continue
        if _master_target_type_for_project(project) != "scene":
            continue
        master_sites = [item for item in (project.get("master_sites") or []) if isinstance(item, dict)]
        site_row_ids = {_coerce_site_row_id(item.get("site_row_id")) for item in master_sites}
        site_ids = {str(item.get("site_id") or "").strip() for item in master_sites}
        site_names = {_normalized_site_title(item.get("site_name")) for item in master_sites if str(item.get("site_name") or "").strip()}
        if (
            (site_row_id is not None and site_row_id in site_row_ids)
            or (site_id and site_id in site_ids)
            or (site_name and site_name in site_names)
        ):
            project_id = str(project.get("id") or "").strip()
            if project_id:
                result.append(project_id)
    return result


def _scene_project_matches_master_scope(master_project: dict[str, Any], scene_project: dict[str, Any]) -> bool:
    if _master_target_type_for_project(master_project) != "scene":
        return False
    site_row_id = _coerce_site_row_id(scene_project.get("site_row_id"))
    site_id = str(scene_project.get("site_id") or "").strip()
    site_name = _normalized_site_title(str(scene_project.get("site_name") or scene_project.get("title") or ""))
    master_sites = [item for item in (master_project.get("master_sites") or []) if isinstance(item, dict)]
    site_row_ids = {_coerce_site_row_id(item.get("site_row_id")) for item in master_sites}
    site_ids = {str(item.get("site_id") or "").strip() for item in master_sites}
    site_names = {
        _normalized_site_title(item.get("site_name"))
        for item in master_sites
        if str(item.get("site_name") or "").strip()
    }
    return bool(
        (site_row_id is not None and site_row_id in site_row_ids)
        or (site_id and site_id in site_ids)
        or (site_name and site_name in site_names)
    )


def _build_master_synced_entry_from_person(
    source_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    """個人シフトのネイティブエントリからマスター形式の同期エントリを生成する。"""
    option_key, _ = _entry_option_and_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    site_link = _entry_site_link_fields(entry)
    site_name = str(site_link.get("site_name") or "").strip()
    employee_name = str(source_project.get("title") or "").strip()
    return {
        "id": _sync_entry_id(SHIFT_SYNC_PERSON_SOURCE, source_project.get("id"), "master", month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, site_name),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_name": employee_name,
        "employee_number": str(source_project.get("employee_number") or "").strip(),
        "site_row_id": str(_coerce_site_row_id(site_link.get("site_row_id")) or ""),
        "site_id": str(site_link.get("site_id") or "").strip(),
        "site_name": str(site_link.get("site_name") or "").strip(),
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": SHIFT_SYNC_PERSON_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }


def _build_master_synced_entry_from_scene(
    source_project: dict[str, Any],
    entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    """現場シフトのネイティブエントリからマスター形式の同期エントリを生成する。"""
    option_key, employee_name = _entry_option_and_name(entry)
    source_entry_id = str(entry.get("id") or "").strip()
    return {
        "id": _sync_entry_id(SHIFT_SYNC_SCENE_SOURCE, source_project.get("id"), "master", month_key, day_key, source_entry_id),
        "value": _format_entry_value(option_key, employee_name),
        "comment": str(entry.get("comment") or "").strip(),
        "employee_name": employee_name,
        "employee_number": str(entry.get("employee_number") or "").strip(),
        "site_row_id": str(_coerce_site_row_id(source_project.get("site_row_id")) or ""),
        "site_id": str(source_project.get("site_id") or "").strip(),
        "site_name": str(source_project.get("site_name") or "").strip(),
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": SHIFT_SYNC_SCENE_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": source_entry_id,
    }


def _substitute_request_type(entry: dict[str, Any]) -> str:
    request_type = str(entry.get("substitute_request_type") or entry.get("substituteRequestType") or "").strip().lower()
    if request_type in {"scene", "person"}:
        return request_type
    site_link = _entry_site_link_fields(entry)
    if site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name"):
        return "scene"
    if str(entry.get("employee_number") or "").strip() or _entry_employee_name(entry):
        return "person"
    return ""


def _substitute_assignment(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("substitute_resolved") is not True and str(entry.get("substitute_resolved") or "").lower() not in {"1", "true", "yes", "on"}:
        return None
    request_type = _substitute_request_type(entry)
    option_key, value_name = _entry_option_and_name(entry)
    site_link = _entry_site_link_fields(entry)
    employee_name = _entry_employee_name(entry)
    employee_number = str(entry.get("employee_number") or "").strip()

    if request_type == "scene":
        helper_name = str(entry.get("substitute_helper_employee_name") or entry.get("substituteHelperEmployeeName") or "").strip()
        helper_number = str(entry.get("substitute_helper_employee_number") or entry.get("substituteHelperEmployeeNumber") or "").strip()
        if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
            site_link["site_name"] = value_name
        employee_name = helper_name or "未設定"
        employee_number = helper_number
        unassigned_helper = not helper_name
    elif request_type == "person":
        employee_name = employee_name or value_name
        unassigned_helper = False
        helper_site = _entry_site_link_fields(
            {
                "site_row_id": entry.get("substitute_helper_site_row_id") or entry.get("substituteHelperSiteRowId"),
                "site_id": entry.get("substitute_helper_site_id") or entry.get("substituteHelperSiteId"),
                "site_name": entry.get("substitute_helper_site_name") or entry.get("substituteHelperSiteName"),
                "value": entry.get("substitute_helper_site_name") or entry.get("substituteHelperSiteName") or "",
            }
        )
        site_link = helper_site
    else:
        return None

    if not employee_name:
        return None
    if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
        return None

    return {
        "option_key": option_key,
        "employee_name": employee_name,
        "employee_number": employee_number,
        "site_row_id": str(site_link.get("site_row_id") or ""),
        "site_id": str(site_link.get("site_id") or "").strip(),
        "site_name": str(site_link.get("site_name") or "").strip(),
        "source_entry_id": str(entry.get("id") or "").strip(),
        "comment": str(entry.get("comment") or "").strip(),
        "request_type": request_type,
        "unassigned_helper": unassigned_helper,
        "substitute_source_project_id": str(entry.get("substitute_source_project_id") or "").strip(),
        "substitute_source_project_mode": str(entry.get("substitute_source_project_mode") or "").strip(),
        "substitute_source_month_key": str(entry.get("substitute_source_month_key") or "").strip(),
        "substitute_source_day": str(entry.get("substitute_source_day") or "").strip(),
        "substitute_source_entry_id": str(entry.get("substitute_source_entry_id") or "").strip(),
        "substitute_helper_employee_name": str(entry.get("substitute_helper_employee_name") or entry.get("substituteHelperEmployeeName") or "").strip(),
        "substitute_helper_employee_number": str(entry.get("substitute_helper_employee_number") or entry.get("substituteHelperEmployeeNumber") or "").strip(),
        "substitute_helper_site_row_id": str(entry.get("substitute_helper_site_row_id") or entry.get("substituteHelperSiteRowId") or "").strip(),
        "substitute_helper_site_id": str(entry.get("substitute_helper_site_id") or entry.get("substituteHelperSiteId") or "").strip(),
        "substitute_helper_site_name": str(entry.get("substitute_helper_site_name") or entry.get("substituteHelperSiteName") or "").strip(),
    }


def _build_person_synced_entry_from_substitute(
    source_project: dict[str, Any],
    assignment: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    return {
        "id": _sync_entry_id(SHIFT_SYNC_SUBSTITUTE_SOURCE, source_project.get("id"), "person", month_key, day_key, assignment.get("source_entry_id")),
        "value": _format_entry_value(assignment.get("option_key"), assignment.get("site_name")),
        "comment": assignment.get("comment") or "",
        "employee_number": "",
        "site_row_id": str(assignment.get("site_row_id") or ""),
        "site_id": str(assignment.get("site_id") or ""),
        "site_name": str(assignment.get("site_name") or ""),
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": SHIFT_SYNC_SUBSTITUTE_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": str(assignment.get("source_entry_id") or ""),
        "substitute_request_type": str(assignment.get("request_type") or ""),
        "substitute_resolved": True,
        "substitute_source_project_id": str(assignment.get("substitute_source_project_id") or ""),
        "substitute_source_project_mode": str(assignment.get("substitute_source_project_mode") or ""),
        "substitute_source_month_key": str(assignment.get("substitute_source_month_key") or month_key),
        "substitute_source_day": str(assignment.get("substitute_source_day") or day_key),
        "substitute_source_entry_id": str(assignment.get("substitute_source_entry_id") or ""),
        "substitute_helper_site_row_id": str(assignment.get("substitute_helper_site_row_id") or ""),
        "substitute_helper_site_id": str(assignment.get("substitute_helper_site_id") or ""),
        "substitute_helper_site_name": str(assignment.get("substitute_helper_site_name") or ""),
    }


def _build_scene_synced_entry_from_substitute(
    source_project: dict[str, Any],
    target_project: dict[str, Any],
    assignment: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    branch_fields = _scene_branch_fields_for_option(target_project, assignment.get("option_key"))
    synced = {
        "id": _sync_entry_id(SHIFT_SYNC_SUBSTITUTE_SOURCE, source_project.get("id"), "scene", month_key, day_key, assignment.get("source_entry_id")),
        "value": _format_entry_value(assignment.get("option_key"), assignment.get("employee_name")),
        "comment": assignment.get("comment") or "",
        "employee_name": str(assignment.get("employee_name") or ""),
        "employee_number": str(assignment.get("employee_number") or ""),
        "substitute_unassigned_helper": bool(assignment.get("unassigned_helper")),
        "site_row_id": "",
        "site_id": "",
        "site_name": "",
        "site_branch_row_id": branch_fields["site_branch_row_id"],
        "site_branch": branch_fields["site_branch"],
        "sync_source_type": SHIFT_SYNC_SUBSTITUTE_SOURCE,
        "sync_source_project_id": str(source_project.get("id") or ""),
        "sync_source_project_title": str(source_project.get("title") or ""),
        "sync_source_month_key": month_key,
        "sync_source_day": day_key,
        "sync_source_entry_id": str(assignment.get("source_entry_id") or ""),
        "substitute_request_type": str(assignment.get("request_type") or ""),
        "substitute_resolved": True,
        "substitute_source_project_id": str(assignment.get("substitute_source_project_id") or ""),
        "substitute_source_project_mode": str(assignment.get("substitute_source_project_mode") or ""),
        "substitute_source_month_key": str(assignment.get("substitute_source_month_key") or month_key),
        "substitute_source_day": str(assignment.get("substitute_source_day") or day_key),
        "substitute_source_entry_id": str(assignment.get("substitute_source_entry_id") or ""),
        "substitute_helper_employee_name": str(assignment.get("substitute_helper_employee_name") or assignment.get("employee_name") or ""),
        "substitute_helper_employee_number": str(assignment.get("substitute_helper_employee_number") or assignment.get("employee_number") or ""),
    }
    return _scene_entry_with_siteplus_defaults(target_project, synced)


def _master_entry_is_in_scope(source_project: dict[str, Any], entry: dict[str, Any]) -> bool:
    target_type = _master_target_type_for_project(source_project)
    master_people = [item for item in (source_project.get("master_people") or []) if isinstance(item, dict)]
    if target_type == "person":
        employee_number = str(entry.get("employee_number") or "").strip()
        employee_name = _entry_employee_name(entry)
        normalized_name = _normalized_person_title(employee_name)
        people_numbers = {str(item.get("employee_number") or "").strip() for item in master_people}
        people_names = {_normalized_person_title(item.get("name")) for item in master_people if str(item.get("name") or "").strip()}
        if not ((employee_number and employee_number in people_numbers) or (normalized_name and normalized_name in people_names)):
            return False
        return True

    master_sites = [item for item in (source_project.get("master_sites") or []) if isinstance(item, dict)]
    site_link = _entry_site_link_fields(entry)
    site_row_id = _coerce_site_row_id(site_link.get("site_row_id"))
    site_id = str(site_link.get("site_id") or "").strip()
    site_name = _normalized_site_title(site_link.get("site_name"))
    site_row_ids = {_coerce_site_row_id(item.get("site_row_id")) for item in master_sites}
    site_ids = {str(item.get("site_id") or "").strip() for item in master_sites}
    site_names = {_normalized_site_title(item.get("site_name")) for item in master_sites if str(item.get("site_name") or "").strip()}
    return (
        (site_row_id is not None and site_row_id in site_row_ids)
        or (site_id and site_id in site_ids)
        or (site_name and site_name in site_names)
    )


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
            is_synced = _entry_is_shift_synced(entry)
            is_from_master = str(entry.get("sync_source_type") or "") == SHIFT_SYNC_MASTER_SOURCE
            option_key, entry_name = _entry_option_and_name(entry)
            if mode == "scene":
                if not entry_name:
                    continue
                if not is_synced:
                    for target_project_id in _matching_person_project_ids_for_scene_entry(source_project, entry):
                        desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                            _build_person_synced_entry_from_scene(source_project, entry, month_key=month_key, day_key=day_key)
                        )
                if not is_from_master:
                    for target_project_id in _matching_master_project_ids_for_scene_project(source_project):
                        desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                            _build_master_synced_entry_from_scene(source_project, entry, month_key=month_key, day_key=day_key)
                        )
            elif mode == "person":
                if option_key in LEAVE_OPTION_MAPPINGS:
                    continue
                site_link = _entry_site_link_fields(entry)
                if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
                    continue
                if not is_synced:
                    for target_project_id in _matching_scene_project_ids_for_person_entry(source_project, entry):
                        desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(entry)
                if not is_from_master:
                    for target_project_id in _matching_master_project_ids_for_person_project(source_project):
                        desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                            _build_master_synced_entry_from_person(source_project, entry, month_key=month_key, day_key=day_key)
                        )
            elif mode == "master":
                if is_synced:
                    continue
                if option_key in LEAVE_OPTION_MAPPINGS:
                    continue
                if not entry_name:
                    continue
                if not _master_entry_is_in_scope(source_project, entry):
                    continue
                site_link = _entry_site_link_fields(entry)
                if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
                    continue
                for target_project_id in _matching_person_project_ids_for_scene_entry(source_project, entry):
                    desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                        _build_person_synced_entry_from_master(
                            source_project,
                            entry,
                            month_key=month_key,
                            day_key=day_key,
                        )
                    )
                for target_project_id in _matching_scene_project_ids_for_person_entry(source_project, entry):
                    target_project = _load_project(target_project_id)
                    desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                        _build_scene_synced_entry_from_master(
                            source_project,
                            target_project,
                            entry,
                            month_key=month_key,
                            day_key=day_key,
                        )
                    )
            elif mode == SUBSTITUTE_MODE:
                if is_synced:
                    continue
                assignment = _substitute_assignment(entry)
                if not assignment:
                    continue
                person_probe = {
                    "employee_number": assignment.get("employee_number"),
                    "employee_name": assignment.get("employee_name"),
                    "value": _format_entry_value(assignment.get("option_key"), assignment.get("employee_name")),
                }
                site_probe = {
                    "site_row_id": assignment.get("site_row_id"),
                    "site_id": assignment.get("site_id"),
                    "site_name": assignment.get("site_name"),
                    "value": _format_entry_value(assignment.get("option_key"), assignment.get("site_name")),
                }
                if not assignment.get("unassigned_helper"):
                    for target_project_id in _matching_person_project_ids_for_scene_entry(source_project, person_probe):
                        desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                            _build_person_synced_entry_from_substitute(
                                source_project,
                                assignment,
                                month_key=month_key,
                                day_key=day_key,
                            )
                        )
                for target_project_id in _matching_scene_project_ids_for_person_entry(source_project, site_probe):
                    target_project = _load_project(target_project_id)
                    desired.setdefault(target_project_id, {}).setdefault(day_key, []).append(
                        _build_scene_synced_entry_from_substitute(
                            source_project,
                            target_project,
                            assignment,
                            month_key=month_key,
                            day_key=day_key,
                        )
                    )
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
        if str(source_project.get("mode") or "") == "person" and str(target_project.get("mode") or "") == "scene":
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
        next_entries_per_day[day_key] = _entries_in_existing_order(
            preserved + desired, current_entries_per_day.get(day_key, [])
        )

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
    # 他シフト帳の保存に伴う自動反映でも、このシフト帳の ViewPWA 購読者には
    # 「この月を保存」と同じ条件（実カレンダー当月・正式シフトの実変更）で通知する。
    _maybe_notify_pwa_month_change(
        target_project,
        year,
        month,
        current_entries_per_day,
        target_project["months"][month_key],
    )
    return True


def _desired_master_entries_from_source(
    master_project: dict[str, Any],
    source_project: dict[str, Any],
    month_key: str,
) -> dict[str, list[dict[str, Any]]]:
    source_month = (source_project.get("months") or {}).get(month_key)
    if not source_month:
        return {}
    source_mode = str(source_project.get("mode") or "")
    if source_mode == "person":
        if not _person_project_matches_master_scope(master_project, source_project):
            return {}
        entries_per_day = _normalize_entries(
            source_month.get("entries_per_day"),
            source_month["year"],
            source_month["month"],
        )
        desired: dict[str, list[dict[str, Any]]] = {}
        for day_key, entries in entries_per_day.items():
            for entry in entries:
                if str(entry.get("sync_source_type") or "") == SHIFT_SYNC_MASTER_SOURCE:
                    continue
                option_key, _ = _entry_option_and_name(entry)
                if option_key in LEAVE_OPTION_MAPPINGS:
                    continue
                site_link = _entry_site_link_fields(entry)
                if not (site_link.get("site_row_id") or site_link.get("site_id") or site_link.get("site_name")):
                    continue
                desired.setdefault(day_key, []).append(
                    _build_master_synced_entry_from_person(
                        source_project,
                        entry,
                        month_key=month_key,
                        day_key=day_key,
                    )
                )
        return desired

    if source_mode == "scene":
        if not _scene_project_matches_master_scope(master_project, source_project):
            return {}
        entries_per_day = _normalize_entries(
            source_month.get("entries_per_day"),
            source_month["year"],
            source_month["month"],
        )
        desired = {}
        for day_key, entries in entries_per_day.items():
            for entry in entries:
                if str(entry.get("sync_source_type") or "") == SHIFT_SYNC_MASTER_SOURCE:
                    continue
                _, entry_name = _entry_option_and_name(entry)
                if not entry_name:
                    continue
                desired.setdefault(day_key, []).append(
                    _build_master_synced_entry_from_scene(
                        source_project,
                        entry,
                        month_key=month_key,
                        day_key=day_key,
                    )
                )
        return desired

    return {}


def _refresh_master_shift_from_sources(
    target_project: dict[str, Any],
    month_key: str,
    *,
    actor_name: str,
) -> bool:
    if str(target_project.get("mode") or "") != "master":
        return False
    current_month = (target_project.get("months") or {}).get(month_key)
    if not current_month:
        return False

    master_target_type = _master_target_type_for_project(target_project)
    expected_source_mode = "person" if master_target_type == "person" else "scene"
    expected_sync_source = SHIFT_SYNC_PERSON_SOURCE if expected_source_mode == "person" else SHIFT_SYNC_SCENE_SOURCE
    year = int(current_month["year"])
    month = int(current_month["month"])
    desired_by_day: dict[str, list[dict[str, Any]]] = _empty_entries_for_month(year, month)
    source_count = 0
    desired_count = 0

    for source_project in _iter_project_summaries():
        if str(source_project.get("id") or "") == str(target_project.get("id") or ""):
            continue
        if str(source_project.get("mode") or "") != expected_source_mode:
            continue
        if month_key not in (source_project.get("months") or {}):
            continue
        source_desired = _desired_master_entries_from_source(target_project, source_project, month_key)
        if not source_desired:
            continue
        source_count += 1
        for day_key, entries in source_desired.items():
            desired_by_day.setdefault(day_key, []).extend(entries)
            desired_count += len(entries)

    current_entries = _normalize_entries(current_month.get("entries_per_day"), year, month)
    next_entries_per_day: dict[str, list[dict[str, Any]]] = {}
    for day_key in _empty_entries_for_month(year, month):
        preserved = [
            dict(entry)
            for entry in current_entries.get(day_key, [])
            if not (
                _entry_is_shift_synced(entry)
                and str(entry.get("sync_source_type") or "") == expected_sync_source
                and str(entry.get("sync_source_month_key") or "") == month_key
            )
        ]
        next_entries_per_day[day_key] = preserved + desired_by_day.get(day_key, [])

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
                f"マスター対象 {source_count} 件からシフトを自動反映",
                f"同期エントリ {desired_count} 件",
            ],
        },
    )
    _maybe_notify_pwa_month_change(target_project, year, month, current_entries, merged)
    return True


def _resync_shift_month(source_project: dict[str, Any], month_key: str, *, actor_name: str) -> None:
    month_data = (source_project.get("months") or {}).get(month_key)
    if not month_data:
        return
    desired_by_target = _desired_shift_sync_entries_by_target(source_project, month_key, month_data)
    source_mode = str(source_project.get("mode") or "")
    relevant_target_ids = set(desired_by_target.keys())
    for project in _iter_project_summaries():
        project_mode = str(project.get("mode") or "")
        if project_mode == "master" and source_mode == "master":
            continue
        if project_mode != "master" and project_mode == source_mode:
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
    if target_mode == "master":
        _refresh_master_shift_from_sources(target_project, month_key, actor_name=actor_name)
        return
    for source_project in _iter_project_summaries():
        if str(source_project.get("mode") or "") == target_mode:
            continue
        if month_key not in (source_project.get("months") or {}):
            continue
        _resync_shift_month(source_project, month_key, actor_name=actor_name)


def _refresh_shift_sync_into_target_month(
    target_project: dict[str, Any], month_key: str, *, actor_name: str
) -> None:
    """対象シフト帳『だけ』へ、他モードの既存シフトを取り込む（スコープ限定の取り込み）。

    新規作成・月追加で使う取り込み処理。従来の
    ``_refresh_shift_sync_for_target_month`` は各ソースを
    ``_resync_shift_month`` で「全ターゲット」へ再同期するため、シフト帳が増えると
    (ソース×ターゲット) 件のロック取得・プロジェクト読み込みが走り、作成リクエストが
    リバースプロキシの読み取りタイムアウトに達して、本体は保存済みなのにクライアントへ
    非JSONのエラーが返り「リクエストに失敗しました」となって編集画面へ遷移できなかった。

    新規作成時に必要なのは「いま作った対象シフト帳へ既存データを取り込む」ことだけで、
    他プロジェクト同士の再同期は不要（それぞれの保存時に維持されている）。本関数は対象
    シフト帳のみを更新し、他プロジェクトには一切書き込まないため、件数に対して線形で済む。
    """
    if not month_key or month_key not in (target_project.get("months") or {}):
        return
    target_mode = str(target_project.get("mode") or "")
    if target_mode == "master":
        _refresh_master_shift_from_sources(target_project, month_key, actor_name=actor_name)
        return
    target_id = str(target_project.get("id") or "")
    if not target_id:
        return
    for source_project in _iter_project_summaries():
        if str(source_project.get("mode") or "") == target_mode:
            continue
        if str(source_project.get("id") or "") == target_id:
            continue
        source_month = (source_project.get("months") or {}).get(month_key)
        if not source_month:
            continue
        desired_by_target = _desired_shift_sync_entries_by_target(source_project, month_key, source_month)
        desired_for_target = desired_by_target.get(target_id)
        if not desired_for_target:
            continue
        _replace_shift_synced_entries_in_target_project(
            target_project,
            source_project,
            month_key,
            desired_for_target,
            actor_name=actor_name,
        )


def _resync_shift_project(source_project: dict[str, Any], *, actor_name: str) -> None:
    for month_key in _sort_month_keys(list((source_project.get("months") or {}).keys())):
        _resync_shift_month(source_project, month_key, actor_name=actor_name)


def _refresh_shift_sync_for_target_project(target_project: dict[str, Any], *, actor_name: str) -> None:
    for month_key in _sort_month_keys(list((target_project.get("months") or {}).keys())):
        _refresh_shift_sync_for_target_month(target_project, month_key, actor_name=actor_name)


def _remove_shift_sync_for_month(source_project: dict[str, Any], month_key: str, *, actor_name: str) -> None:
    source_mode = str(source_project.get("mode") or "")
    for project in _iter_project_summaries():
        project_mode = str(project.get("mode") or "")
        if project_mode == "master" and source_mode == "master":
            continue
        if project_mode != "master" and project_mode == source_mode:
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


def _ensure_person_project(project: dict[str, Any]) -> None:
    if project.get("mode") != "person":
        raise CloudShiftError("このアシスト機能は person モード専用です", 400)


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


def _find_month_entry(
    month_data: dict[str, Any],
    *,
    day: int,
    entry_id: str,
) -> dict[str, Any] | None:
    entries = (month_data.get("entries_per_day") or {}).get(str(day))
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("id") or "").strip() == entry_id:
            return entry
    return None


def _create_leave_change_request(project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not _leave_change_request_enabled(project):
        raise CloudShiftError("このシフト帳では休暇種別変更申請が許可されていません", 403)

    month_key = str(payload.get("month_key") or "").strip()
    if not month_key:
        raise CloudShiftError("対象月を指定してください", 400)
    try:
        year, month = _parse_month_key(month_key)
        year, month = _validate_year_month(year, month)
    except Exception as exc:
        raise CloudShiftError("対象月が不正です", 400) from exc
    month_key = _month_key(year, month)
    month_data = (project.get("months") or {}).get(month_key)
    if not month_data:
        raise CloudShiftError("対象の月が存在しません", 404)

    try:
        day = int(payload.get("day"))
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("日付が不正です", 400) from exc
    if day < 1 or day > monthrange(year, month)[1]:
        raise CloudShiftError("日付が不正です", 400)

    entry_id = str(payload.get("entry_id") or "").strip()
    requested_option_key = str(payload.get("requested_option_key") or "").strip().upper()
    if requested_option_key not in LEAVE_OPTION_MAPPINGS:
        raise CloudShiftError("申請する休暇種別が不正です", 400)
    request_comment = str(payload.get("request_comment") or "").strip()[:500]
    if requested_option_key in {"COMP", "OTHER"} and not request_comment:
        raise CloudShiftError("代休、その他への変更申請ではコメントを入力してください", 400)

    entry = _find_month_entry(month_data, day=day, entry_id=entry_id)
    if not entry:
        raise CloudShiftError("対象の休暇が見つかりません", 404)
    if _entry_is_shift_synced(entry):
        raise CloudShiftError("同期反映された休暇は申請対象外です", 400)

    old_option_key, entry_name = parse_entry_value(str(entry.get("value") or ""))
    old_option_key = str(old_option_key or "").strip().upper()
    if old_option_key not in LEAVE_OPTION_MAPPINGS:
        raise CloudShiftError("休暇エントリのみ申請できます", 400)
    if requested_option_key == old_option_key:
        raise CloudShiftError("変更前と変更後の休暇種別が同じです", 400)

    for item in _normalized_leave_change_requests(project):
        if (
            item.get("status") == "pending"
            and item.get("month_key") == month_key
            and int(item.get("day") or 0) == day
            and item.get("entry_id") == entry_id
            and item.get("requested_option_key") == requested_option_key
        ):
            raise CloudShiftError("同じ休暇種別変更申請が未処理です", 409)

    request_payload = {
        "id": f"leave_req_{secrets.token_hex(8)}",
        "status": "pending",
        "month_key": month_key,
        "day": day,
        "entry_id": entry_id,
        "entry_name": entry_name,
        "old_option_key": old_option_key,
        "old_leave_type": LEAVE_OPTION_MAPPINGS.get(old_option_key, old_option_key),
        "requested_option_key": requested_option_key,
        "requested_leave_type": LEAVE_OPTION_MAPPINGS.get(requested_option_key, requested_option_key),
        "request_comment": request_comment,
        "requested_at": _utcnow_iso(),
    }
    requests = _normalized_leave_change_requests(project)
    requests.append(request_payload)
    _store_leave_change_requests(project, requests)
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": request_payload["requested_at"],
            "editor_name": "閲覧URL",
            "editor_type": "public_viewer",
            "action": "leave_change_requested",
            "month_key": month_key,
            "changes": [
                f"{day}日の休暇種別変更申請: "
                f"{request_payload['old_leave_type']} → {request_payload['requested_leave_type']}"
            ]
            + ([f"申請コメント: {request_comment}"] if request_comment else []),
        },
    )
    try:
        from app.services.to_bell_hooks import on_cloudshift_leave_change_request

        on_cloudshift_leave_change_request(
            request_payload=request_payload,
            project_title=str(project.get("title") or ""),
            project_id=str(project.get("id") or ""),
            creator_username=str(project.get("owner_user_id") or "") or None,
        )
    except Exception:
        pass
    return request_payload


def _reject_leave_change_request(
    project: dict[str, Any],
    request_id: str,
    *,
    actor_name: str,
    actor_user_id: str,
    reason: str = "",
) -> dict[str, Any]:
    requests = _normalized_leave_change_requests(project)
    target = None
    for item in requests:
        if item["id"] == request_id:
            target = item
            break
    if not target:
        raise CloudShiftError("対象の申請が見つかりません", 404)
    if target.get("status") != "pending":
        raise CloudShiftError("この申請は既に処理済みです", 400)
    target["status"] = "rejected"
    target["decided_at"] = _utcnow_iso()
    target["decided_by"] = actor_user_id
    target["decided_by_name"] = actor_name
    target["decision_reason"] = reason
    _store_leave_change_requests(project, requests)
    _save_project(project)
    _append_history(
        project["id"],
        {
            "timestamp": target["decided_at"],
            "editor_name": actor_name,
            "editor_type": "owner",
            "action": "leave_change_rejected",
            "month_key": target.get("month_key"),
            "changes": [
                f"{target.get('day')}日の休暇種別変更申請を拒否: "
                f"{target.get('old_leave_type')} → {target.get('requested_leave_type')}"
            ],
        },
    )
    return target


def _leave_change_decision_ids(payload: dict[str, Any]) -> list[str]:
    raw = (payload.get("leave_change_request_decisions") or {}).get("approved")
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for item in raw:
        request_id = str(item or "").strip()
        if request_id and request_id not in ids:
            ids.append(request_id)
    return ids


def _finalize_approved_leave_change_requests(
    project: dict[str, Any],
    month_key: str,
    merged_month: dict[str, Any],
    approved_ids: list[str],
    *,
    actor_name: str,
    actor_user_id: str,
) -> list[str]:
    if not approved_ids:
        return []
    requests = _normalized_leave_change_requests(project)
    request_map = {item["id"]: item for item in requests}
    changes: list[str] = []
    timestamp = _utcnow_iso()
    for request_id in approved_ids:
        item = request_map.get(request_id)
        if not item:
            raise CloudShiftError("承認対象の申請が見つかりません", 404)
        if item.get("status") != "pending":
            raise CloudShiftError("承認対象の申請は既に処理済みです", 400)
        if item.get("month_key") != month_key:
            raise CloudShiftError("承認対象の申請と保存中の月が一致しません", 409)
        entry = _find_month_entry(
            merged_month,
            day=int(item.get("day") or 0),
            entry_id=str(item.get("entry_id") or ""),
        )
        if not entry:
            raise CloudShiftError("承認対象の休暇が見つかりません。再読み込みしてください", 409)
        option_key, _ = parse_entry_value(str(entry.get("value") or ""))
        option_key = str(option_key or "").strip().upper()
        if option_key != item.get("requested_option_key"):
            raise CloudShiftError("承認した休暇種別が保存内容に反映されていません", 409)
        request_comment = str(item.get("request_comment") or "").strip()
        if request_comment:
            existing_comment = str(entry.get("comment") or "").strip()
            entry["comment"] = (
                f"{existing_comment}\n{request_comment}" if existing_comment else request_comment
            )
        item["status"] = "approved"
        item["decided_at"] = timestamp
        item["decided_by"] = actor_user_id
        item["decided_by_name"] = actor_name
        changes.append(
            f"{item.get('day')}日の休暇種別変更申請を承認: "
            f"{item.get('old_leave_type')} → {item.get('requested_leave_type')}"
        )
    _store_leave_change_requests(project, requests)
    return changes


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
        current_month_key = _current_month_key()
        active_month_key = selected_month_key or (current_month_key if current_month_key in month_keys else month_keys[-1])
        month_data = project["months"].get(active_month_key)
        if not month_data:
            active_month_key = current_month_key if current_month_key in month_keys else month_keys[-1]
            month_data = project["months"][active_month_key]
    return {
        "project": {
            **_project_summary(project),
            "title": project["title"],
            "mode": project["mode"],
            "urls": _project_public_urls(project),
        },
        "active_month_key": active_month_key,
        "month": _client_month_payload(month_data, include_draft=include_draft, project=project),
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


@cloudshift_bp.route("/pwa/<token>")
def public_pwa(token: str):
    """ViewPWA 専用 URL。スマホでの閲覧と更新通知の受け取りを前提にした閲覧ページ。"""
    from app.services.cloudshift_push import push_available

    project = _find_project_by_token(token, "pwa")
    return render_template(
        "cloudshift_public.html",
        access_mode="pwa",
        token=token,
        project_title=project["title"],
        authenticated_editor_name="",
        push_available=push_available(),
        shiftersync_holidays=sorted(set(JAPAN_HOLIDAYS)),
    )


@cloudshift_bp.route("/pwa/<token>/manifest.webmanifest")
def public_pwa_manifest(token: str):
    """インストール時にそのシフト帳専用アプリとして開けるよう、トークン別の manifest を返す。"""
    project = _find_project_by_token(token, "pwa")
    start_url = url_for("cloudshift.public_pwa", token=token, _external=False)
    manifest = {
        "id": start_url,
        # 通知の発信元表示（"from XXX"）を統一するため、アプリ名は固定で "DSTT" にする。
        # 各シフト帳の識別はホーム画面アイコンのラベル（short_name）で行う。
        "name": "DSTT",
        "short_name": (project.get("title") or "CloudShift")[:24],
        "description": "シフト帳の最新の内容をスマホで確認し、更新通知を受け取れます。",
        "icons": [
            {"src": "/static/img/favicon.ico", "sizes": "256x256", "type": "image/x-icon"},
            {"src": "/static/img/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
            {"src": "/static/img/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "display": "standalone",
        "start_url": start_url,
        "scope": start_url,
        "theme_color": "#2563eb",
        "background_color": "#ffffff",
    }
    response = jsonify(manifest)
    response.headers["Content-Type"] = "application/manifest+json"
    return response


@cloudshift_bp.route("/api/pwa/<token>/push/public-key")
def api_pwa_push_public_key(token: str):
    from app.services.cloudshift_push import CloudShiftPushUnavailable, vapid_public_key

    _find_project_by_token(token, "pwa")
    try:
        return jsonify({"status": "ok", "public_key": vapid_public_key()})
    except CloudShiftPushUnavailable as exc:
        return jsonify({"status": "unavailable", "public_key": "", "message": str(exc)}), 503


@cloudshift_bp.route("/api/pwa/<token>/push/subscribe", methods=["POST"])
def api_pwa_push_subscribe(token: str):
    from app.services.cloudshift_push import CloudShiftPushUnavailable, save_subscription

    project = _find_project_by_token(token, "pwa")
    payload = request.get_json(silent=True) or {}
    device_id = str(payload.get("device_id") or "").strip()
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else payload
    try:
        row = save_subscription(
            str(project["id"]),
            device_id,
            subscription,
            user_agent=request.headers.get("User-Agent", ""),
        )
        return jsonify({"status": "ok", "device_label": row.device_label})
    except (ValueError, CloudShiftPushUnavailable) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@cloudshift_bp.route("/api/pwa/<token>/push/unsubscribe", methods=["POST"])
def api_pwa_push_unsubscribe(token: str):
    from app.services.cloudshift_push import unsubscribe

    project = _find_project_by_token(token, "pwa")
    device_id = str((request.get_json(silent=True) or {}).get("device_id") or "").strip()
    return jsonify({"status": "ok", "updated": unsubscribe(str(project["id"]), device_id)})


@cloudshift_bp.route("/api/project/<project_id>/pwa/subscriptions", methods=["GET"])
@login_required
def api_project_pwa_subscriptions(project_id: str):
    """シフト帳に登録された通知先（ViewPWA 購読）の一覧。オーナーのみ。"""
    from app.services.cloudshift_push import list_project_subscriptions

    project = _owner_project_or_404(project_id)
    return jsonify({"status": "ok", "subscriptions": list_project_subscriptions(str(project["id"]))})


@cloudshift_bp.route(
    "/api/project/<project_id>/pwa/subscriptions/<int:subscription_id>",
    methods=["PUT", "DELETE"],
)
@login_required
def api_project_pwa_subscription(project_id: str, subscription_id: int):
    """通知先の名称変更・無効化・削除。オーナーのみ。"""
    from app.services.cloudshift_push import (
        deactivate_project_subscription,
        delete_project_subscription,
        update_project_subscription_label,
    )

    project = _owner_project_or_404(project_id)
    pid = str(project["id"])
    try:
        if request.method == "DELETE":
            hard = request.args.get("hard", "").lower() in ("1", "true", "yes")
            if hard:
                return jsonify({"status": "ok", "deleted": delete_project_subscription(pid, subscription_id)})
            return jsonify({"status": "ok", "updated": deactivate_project_subscription(pid, subscription_id)})
        label = str((request.get_json(silent=True) or {}).get("device_label") or "")
        row = update_project_subscription_label(pid, subscription_id, label)
        return jsonify({"status": "ok", "subscription": row})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404


@cloudshift_bp.route("/api/list")
@login_required
def api_list():
    _ensure_substitute_projects_for_current_user()
    owner_id = _user_id()
    all_projects = _iter_stored_projects()
    # 同一紐づけを所持するオーナー集合を作る（複数オーナー所持の検知に使用）。
    owners_by_link: dict[tuple[str, str], set[str]] = {}
    for project in all_projects:
        link_key = _project_link_key(project)
        if link_key:
            owners_by_link.setdefault(link_key, set()).add(str(project.get("owner_user_id") or ""))
    projects = []
    for project in all_projects:
        is_owner = project.get("owner_user_id") == owner_id
        # オーナー自身が一覧から非表示にしたシフト帳は除外する。
        if is_owner and project.get("hidden"):
            continue
        if not is_owner and not _project_is_shared_with_current_user(project):
            continue
        summary = _project_summary(project)
        # 自分が所持し、かつ同一紐づけを他オーナーも所持している場合のみ非表示操作を許可する。
        can_hide = False
        if is_owner:
            link_key = _project_link_key(project)
            if link_key:
                can_hide = bool(owners_by_link.get(link_key, set()) - {owner_id})
        summary["can_hide"] = can_hide
        projects.append(summary)
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
        if project.get("mode") == "master":
            raise CloudShiftError("マスターシフトは重複チェックの対象外です", 400)
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
        mode = parsed["mode"]
        if mode == SUBSTITUTE_MODE:
            raise CloudShiftError("要代務シフト帳は営業所ごとに自動作成されます", 400)
        raw_title = title_override or parsed["title"]
        if mode == "person" and not str(raw_title or "").strip():
            raw_title = PERSON_UNASSIGNED_TITLE
        title = _sanitize_title(raw_title)
        employee_number = parsed.get("employee_number", "")
        year, month = _validate_year_month(parsed["year"], parsed["month"])
        capacity_enabled = parsed["capacity_enabled"]
        required_capacity = parsed["required_capacity"]
        entries = parsed["entries_per_day"]
    else:
        mode = _sanitize_mode(request.form.get("mode"))
        if mode == SUBSTITUTE_MODE:
            raise CloudShiftError("要代務シフト帳は営業所ごとに自動作成されます", 400)
        raw_title = title_override
        if mode == "person" and not str(raw_title or "").strip():
            raw_title = PERSON_UNASSIGNED_TITLE
        title = _sanitize_title(raw_title)
        employee_number = _sanitize_employee_number(request.form.get("employee_number"))
        year, month = _validate_year_month(request.form.get("year"), request.form.get("month"))
        capacity_enabled, required_capacity = _sanitize_capacity(request.form.get("required_capacity"))
        entries = {}
    site_ref = None
    if mode == "scene":
        site_ref = _load_site_reference(_sanitize_site_row_id(request.form.get("site_row_id")), require_active=True)
    master_target_type = ""
    master_people: list[dict[str, str]] = []
    master_sites: list[dict[str, Any]] = []
    if mode == "master":
        master_target_type, master_people, master_sites = _master_scope_from_payload(request.form)

    project_id = _project_id()
    month_key = _month_key(year, month)
    project = {
        "id": project_id,
        "owner_user_id": _user_id(),
        "title": title,
        "mode": mode,
        "employee_number": employee_number if mode == "person" else "",
        **_site_storage_fields(site_ref if mode == "scene" else None),
        "master_target_type": master_target_type,
        "master_people": master_people,
        "master_sites": master_sites,
        "view_token": _share_token(),
        "edit_token": _share_token(),
        "pwa_token": _share_token(),
        "created_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
        "months": {
            month_key: _build_month_payload(year, month, capacity_enabled, required_capacity, entries),
        },
    }
    _assert_link_key_unique(_project_link_key(project))
    _save_project(project)
    # ここまでで「シフト帳本体」と最初の月の保存は完了している。
    # これ以降の履歴追記・現場バックフィル・自動同期はあくまで付随処理であり、
    # 失敗しても作成自体は成立している。したがって例外で作成リクエスト全体を
    # 失敗させない（保存済みなのに 4xx/5xx が返り、フロントで requestJson が
    # 例外となって編集画面への自動遷移や成功通知が出ない、という不具合を防ぐ）。
    try:
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
        if mode == "master":
            _append_history(
                project_id,
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": _user_label(),
                    "editor_type": "owner",
                    "action": "master_scope_updated",
                    "month_key": None,
                    "changes": [
                        f"{'個人' if master_target_type == 'person' else '現場'}マスター対象を "
                        f"{len(master_people) if master_target_type == 'person' else len(master_sites)}件 に設定"
                    ],
                },
            )
        if project["mode"] == "scene":
            _backfill_scene_project_from_person_experience(project, actor_name=_user_label())
            _backfill_scene_project_from_siteplus_dedicated(project, actor_name=_user_label())
        if project["mode"] != "master":
            _resync_shift_month(project, month_key, actor_name=_user_label())
        # 新規作成は「いま作った帳面へ既存データを取り込む」だけで十分。全プロジェクトを
        # 相互再同期する _refresh_shift_sync_for_target_month は (プロジェクト数)^2 の
        # ロック/読み込みになり、件数が増えると作成リクエストがタイムアウトしていたため、
        # 対象シフト帳のみを更新するスコープ限定版を使う。
        _refresh_shift_sync_into_target_month(project, month_key, actor_name=_user_label())
    except Exception:
        current_app.logger.exception(
            "CloudShift post-create steps failed after creating project %s", project_id
        )
    # 付随処理で再保存されている可能性があるため読み直すが、失敗時も
    # 作成済みのメモリ上の project にフォールバックして必ず成功応答を返す。
    try:
        project = _load_project(project_id) or project
    except Exception:
        current_app.logger.exception(
            "CloudShift reload after create failed for project %s", project_id
        )
    return jsonify({"success": True, "project": _project_detail_payload(project, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>")
@login_required
def api_project_detail(project_id: str):
    project, access_role = _project_for_current_user_or_404(project_id)
    if project.get("mode") == "master" and access_role == "owner":
        try:
            _refresh_shift_sync_for_target_project(project, actor_name=_user_label())
        except CloudShiftError:
            raise
        except Exception:
            current_app.logger.exception(
                "CloudShift master shift auto-resync failed for project %s", project_id
            )
        project, access_role = _project_for_current_user_or_404(project_id)
    # オーナーが詳細を開いた時点で ViewPWA 用トークンを発行しておく（既存帳簿の遡及対応）。
    if access_role == "owner" and not str(project.get("pwa_token") or "").strip():
        with _project_lock(project_id):
            project = _owner_project_or_404(project_id)
            if _ensure_pwa_token(project):
                _save_project(project)
    selected_month_key = request.args.get("month_key")
    payload = _project_detail_payload(project, selected_month_key, include_draft=access_role in {"owner", "editor"})
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
        raw_new_title = data.get("title", project["title"])
        if project.get("mode") == "person" and not str(raw_new_title or "").strip():
            raw_new_title = PERSON_UNASSIGNED_TITLE
        new_title = _sanitize_title(raw_new_title)
        old_title = project["title"]
        old_employee_number = str(project.get("employee_number") or "")
        old_site = _project_site_payload(project)
        old_master_target_type = _master_target_type_for_project(project)
        old_master_people = [dict(item) for item in (project.get("master_people") or []) if isinstance(item, dict)]
        old_master_sites = [dict(item) for item in (project.get("master_sites") or []) if isinstance(item, dict)]
        new_employee_number = _sanitize_employee_number(data.get("employee_number", project.get("employee_number", "")))
        if project.get("mode") != "person":
            new_employee_number = ""
        new_site_ref = None
        if project.get("mode") == "scene":
            incoming_site_row_id = data.get("site_row_id", project.get("site_row_id"))
            new_site_ref = _load_site_reference(_sanitize_site_row_id(incoming_site_row_id), require_active=True)
        new_master_target_type = old_master_target_type
        new_master_people = project.get("master_people") or []
        new_master_sites = project.get("master_sites") or []
        if project.get("mode") == "master":
            new_master_target_type, new_master_people, new_master_sites = _master_scope_from_payload(data, existing=project)
        # 紐づけ（個人=社員ID／現場）が変わる場合は重複チェックを行う。
        prospective_link_key: tuple[str, str] | None = None
        if project.get("mode") == "person":
            prospective_link_key = ("person", new_employee_number) if new_employee_number else None
        elif project.get("mode") == "scene":
            new_site_id = str(new_site_ref.get("site_id") or "").strip() if new_site_ref else ""
            if new_site_id:
                prospective_link_key = ("scene", f"sid:{new_site_id}")
            else:
                new_site_row_id = _coerce_site_row_id(new_site_ref.get("site_row_id")) if new_site_ref else None
                if new_site_row_id:
                    prospective_link_key = ("scene", f"row:{new_site_row_id}")
        if prospective_link_key and prospective_link_key != _project_link_key(project):
            _assert_link_key_unique(prospective_link_key, exclude_id=project_id)
            # 別対象へ紐づけし直す＝再び使う意思があるため、非表示状態は解除する。
            if project.get("hidden"):
                project["hidden"] = False
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
        if project.get("mode") == "master" and (
            new_master_target_type != old_master_target_type
            or new_master_people != old_master_people
            or new_master_sites != old_master_sites
        ):
            project["master_target_type"] = new_master_target_type
            project["master_people"] = new_master_people
            project["master_sites"] = new_master_sites
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
            if project.get("mode") == "master" and (
                new_master_target_type != old_master_target_type
                or new_master_people != old_master_people
                or new_master_sites != old_master_sites
            ):
                changes.append(
                    f"{'個人' if new_master_target_type == 'person' else '現場'}マスター対象を "
                    f"{len(new_master_people) if new_master_target_type == 'person' else len(new_master_sites)}件 に更新"
                )
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
    payload = request.get_json(silent=True) or {}
    reject_pending = bool(payload.get("reject_pending_leave_change_requests"))
    # どの共有URLを再発行するか。未指定なら従来通り3種すべてを再発行する。
    all_targets = ("view", "edit", "pwa")
    raw_targets = payload.get("targets")
    if raw_targets is None:
        targets = list(all_targets)
    else:
        if not isinstance(raw_targets, list):
            raise CloudShiftError("再発行対象はリストで指定してください", 400)
        seen: set[str] = set()
        targets = []
        for raw in raw_targets:
            key = str(raw or "").strip()
            if key in all_targets and key not in seen:
                seen.add(key)
                targets.append(key)
        if not targets:
            raise CloudShiftError("再発行する共有URLを指定してください", 400)
    target_set = set(targets)
    target_labels = {"view": "閲覧URL", "edit": "編集URL", "pwa": "ViewPWA URL"}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        if target_set == set(all_targets):
            changes = ["共有URLを再発行"]
        else:
            ordered = [target_labels[key] for key in all_targets if key in target_set]
            changes = ["、".join(ordered) + "を再発行"]
        # 休暇種別変更申請は閲覧URLから届くため、閲覧URLの再発行時のみ破棄を扱う。
        if reject_pending and "view" in target_set:
            requests = _normalized_leave_change_requests(project)
            rejected_count = 0
            timestamp = _utcnow_iso()
            for item in requests:
                if item.get("status") == "pending":
                    item["status"] = "rejected"
                    item["decided_at"] = timestamp
                    item["decided_by"] = _user_id()
                    item["decided_by_name"] = _user_label()
                    item["decision_reason"] = "url_regenerated"
                    rejected_count += 1
            if rejected_count:
                _store_leave_change_requests(project, requests)
                changes.append(f"未処理の休暇種別変更申請 {rejected_count}件を破棄")
        if "view" in target_set:
            project["view_token"] = _share_token()
        if "edit" in target_set:
            project["edit_token"] = _share_token()
        if "pwa" in target_set:
            project["pwa_token"] = _share_token()
            # 旧 ViewPWA URL は無効になるため、その帳簿の PWA 購読も無効化する
            # （古い端末が 404 になる URL へ通知され続けるのを防ぐ）。
            deactivated = CloudShiftPwaSubscription.query.filter_by(project_id=project_id, is_active=True).update(
                {"is_active": False}, synchronize_session=False
            )
            if deactivated:
                changes.append(f"ViewPWA通知の購読 {deactivated}件を解除")
        _save_project(project)
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "tokens_regenerated",
                "month_key": None,
                "changes": changes,
            },
        )
    return jsonify(
        {
            "success": True,
            "urls": _project_public_urls(project),
            "leave_change_requests": _normalized_leave_change_requests(project),
            "pending_leave_change_request_count": _leave_change_request_pending_count(project),
            "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
        }
    )


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


@cloudshift_bp.route("/api/project/<project_id>/settings", methods=["GET", "PUT"])
@login_required
def api_project_settings(project_id: str):
    if request.method == "GET":
        project = _owner_project_or_404(project_id)
        return jsonify(
            {
                "success": True,
                "settings": _shift_book_settings(project),
                "leave_change_requests": _normalized_leave_change_requests(project),
                "pending_leave_change_request_count": _leave_change_request_pending_count(project),
                "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
                "urls": _project_public_urls(project),
            }
        )

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("leave_change_requests_enabled"))
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        previous = _shift_book_settings(project)["leave_change_requests"]["enabled"]
        project["shift_book_settings"] = {
            **_shift_book_settings(project),
            "leave_change_requests": {
                "enabled": enabled,
            },
        }
        _save_project(project)
        if previous != enabled:
            _append_history(
                project_id,
                {
                    "timestamp": _utcnow_iso(),
                    "editor_name": _user_label(),
                    "editor_type": "owner",
                    "action": "shift_book_settings_updated",
                    "month_key": None,
                    "changes": [
                        "休暇種別変更申請を許可" if enabled else "休暇種別変更申請を停止"
                    ],
                },
            )
    return jsonify(
        {
            "success": True,
            "settings": _shift_book_settings(project),
            "leave_change_requests": _normalized_leave_change_requests(project),
            "pending_leave_change_request_count": _leave_change_request_pending_count(project),
            "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
            "urls": _project_public_urls(project),
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/leave-change-requests/<request_id>/reject", methods=["POST"])
@login_required
def api_reject_leave_change_request(project_id: str, request_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        request_payload = _reject_leave_change_request(
            project,
            request_id,
            actor_name=_user_label(),
            actor_user_id=_user_id(),
        )
    return jsonify(
        {
            "success": True,
            "request": request_payload,
            "leave_change_requests": _normalized_leave_change_requests(project),
            "pending_leave_change_request_count": _leave_change_request_pending_count(project),
            "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
        }
    )


@cloudshift_bp.route("/api/project/<project_id>/leave-change-requests/mark-viewed", methods=["POST"])
@login_required
def api_mark_leave_change_requests_viewed(project_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        _mark_leave_change_requests_viewed(project)
    return jsonify(
        {
            "success": True,
            "leave_change_requests": _normalized_leave_change_requests(project),
            "pending_leave_change_request_count": _leave_change_request_pending_count(project),
            "unviewed_leave_change_request_count": _leave_change_request_unviewed_count(project),
        }
    )


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
        project, access_role = _editable_project_or_404(project_id)
        month_payload = _create_month_in_project(project, payload, _user_label(), access_role)
    month_key = _month_key(month_payload["year"], month_payload["month"])
    _resync_shift_month(project, month_key, actor_name=_user_label())
    # 月追加も「この帳面の新しい月へ既存データを取り込む」だけで足りるため、全プロジェクト
    # 相互再同期（プロジェクト数の二乗）ではなくスコープ限定版で対象だけを更新する。
    _refresh_shift_sync_into_target_month(project, month_key, actor_name=_user_label())
    project = _load_project(project_id)
    return jsonify({"success": True, "project": _project_detail_payload(project, month_key, include_draft=True)})


def _save_month_in_project(
    project: dict[str, Any],
    year: int,
    month: int,
    payload: dict[str, Any],
    actor_name: str,
    actor_type: str,
    actor_user_id: str = "",
    approved_leave_change_request_ids: list[str] | None = None,
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
    prepared_entries = _annotate_substitute_entries_for_save(
        project,
        prepared_entries,
        current_month.get("entries_per_day") or {},
        actor_name=actor_name,
        actor_user_id=actor_user_id,
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
    decision_changes = _finalize_approved_leave_change_requests(
        project,
        month_key,
        merged,
        approved_leave_change_request_ids or [],
        actor_name=actor_name,
        actor_user_id=actor_user_id,
    )
    if not changes and not decision_changes:
        return current_month
    snapshots = dict(current_month.get("revision_snapshots") or {})
    snapshots[str(int(current_month.get("revision", 1)))] = _snapshot_month_payload(current_month)
    merged["revision_snapshots"] = _trim_revision_snapshots(snapshots)
    project["months"][month_key] = merged
    _save_project(project)
    if changes or decision_changes:
        _append_history(
            project["id"],
            {
                "timestamp": _utcnow_iso(),
                "editor_name": actor_name,
                "editor_type": actor_type,
                "action": "month_updated",
                "month_key": month_key,
                "changes": (changes + decision_changes)[:100],
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
    actor_user_id: str = "",
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
    prepared_entries = _annotate_substitute_entries_for_save(
        project,
        prepared_entries,
        current_month.get("draft_entries_per_day") or current_month.get("entries_per_day") or {},
        actor_name=actor_name,
        actor_user_id=actor_user_id,
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


def _confirmed_entries_snapshot(project: dict[str, Any], year: int, month: int) -> dict[str, Any] | None:
    """本保存（正式シフト）のエントリを保存前にスナップショットする。仮保存(draft)は対象外。"""
    month_data = (project.get("months") or {}).get(_month_key(year, month))
    if not month_data:
        return None
    return _normalize_entries(month_data.get("entries_per_day"), year, month)


def _maybe_notify_pwa_month_change(
    project: dict[str, Any],
    year: int,
    month: int,
    before_entries: dict[str, Any] | None,
    after_month: dict[str, Any] | None = None,
) -> None:
    """実カレンダーの当月の正式シフトが変わった場合だけ ViewPWA 購読者へ通知する。

    比較対象の保存後エントリは、保存処理が返した month を優先して使う
    （resync 等の後続処理が project を触っても判定がぶれないようにするため）。"""
    try:
        today = datetime.now(JST).date()
        if (year, month) != (today.year, today.month):
            return
        month_data = after_month if after_month is not None else (project.get("months") or {}).get(_month_key(year, month))
        if not month_data:
            return
        after_entries = _normalize_entries(month_data.get("entries_per_day"), year, month)
        before_norm = _normalize_entries(before_entries, year, month) if before_entries is not None else None
        if before_norm == after_entries:
            return
        changes = _collect_pwa_change_descriptions(before_norm, after_entries, year, month)
        _dispatch_pwa_shift_notification(project, year, month, changes)
    except Exception as exc:  # noqa: BLE001 - 通知失敗で保存処理を巻き込まない
        logger.warning("CloudShift PWA notify skipped for %s: %s", project.get("id"), exc)


PWA_NOTIFY_CHANGE_LIMIT = 2


def _collect_pwa_change_descriptions(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    year: int,
    month: int,
) -> list[str]:
    """通知に出すための、日単位の変更内容文字列を日付順に集める。"""
    days = monthrange(year, month)[1]
    descriptions: list[str] = []
    for day in range(1, days + 1):
        before_day = (before or {}).get(str(day), [])
        after_day = after.get(str(day), [])
        if before_day == after_day:
            continue
        descriptions.extend(_describe_day_changes(before_day, after_day, day))
    return descriptions


def _format_pwa_notification_body(year: int, month: int, changes: list[str]) -> str:
    header = f"{year}年{month}月のシフトに変更がありました。"
    if not changes:
        return f"{header}\nタップして最新の内容を確認してください。"
    # 2件まではすべて改行で並べ、3件以上は最初の2件＋「他N件」にまとめる。
    if len(changes) <= PWA_NOTIFY_CHANGE_LIMIT:
        return header + "\n" + "\n".join(changes)
    visible = changes[:PWA_NOTIFY_CHANGE_LIMIT]
    remaining = len(changes) - len(visible)
    return header + "\n" + "\n".join(visible) + f"\n他{remaining}件"


def _dispatch_pwa_shift_notification(
    project: dict[str, Any],
    year: int,
    month: int,
    changes: list[str],
) -> None:
    from app.services.cloudshift_push import push_available, send_push_to_project_async

    pwa_token = str(project.get("pwa_token") or "").strip()
    if not pwa_token or not push_available():
        return
    # 購読が 1 件も無ければ送信処理自体を起こさない。
    if not CloudShiftPwaSubscription.query.filter_by(
        project_id=str(project["id"]),
        is_active=True,
    ).first():
        return
    month_key = _month_key(year, month)
    title = f"{project.get('title') or 'シフト帳'} のシフトが更新されました"
    body = _format_pwa_notification_body(year, month, changes)
    url = url_for("cloudshift.public_pwa", token=pwa_token, month_key=month_key, _external=False)
    # 個人シフトは基本的に一人しか見ないため、最も直近に通知を有効化した端末だけに送る。
    # 現場シフトは複数人が閲覧するため制限せず、有効な購読すべてへ送る。
    latest_only = str(project.get("mode") or "") == "person"
    send_push_to_project_async(
        current_app._get_current_object(),
        str(project["id"]),
        title=title,
        body=body,
        url=url,
        latest_only=latest_only,
    )


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>", methods=["PUT"])
@login_required
def api_save_month(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project, access_role = _editable_project_or_404(project_id)
        before_entries = _confirmed_entries_snapshot(project, year, month)
        approved_ids = _leave_change_decision_ids(payload) if access_role == "owner" else []
        month_payload = _save_month_in_project(
            project,
            year,
            month,
            payload,
            _user_label(),
            access_role,
            _user_id(),
            approved_leave_change_request_ids=approved_ids,
        )
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=_user_label())
    _maybe_notify_pwa_month_change(project, year, month, before_entries, month_payload)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True, project=project), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft", methods=["PUT"])
@login_required
def api_save_month_draft(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project, access_role = _editable_project_or_404(project_id)
        month_payload = _save_draft_month_in_project(project, year, month, payload, _user_label(), access_role, _user_id())
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True, project=project), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft", methods=["DELETE"])
@login_required
def api_clear_month_draft(project_id: str, year: int, month: int):
    with _project_lock(project_id):
        project, access_role = _editable_project_or_404(project_id)
        month_payload = _clear_draft_month_in_project(project, year, month, _user_label(), access_role)
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True, project=project), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/draft/publish", methods=["POST"])
@login_required
def api_publish_month_draft(project_id: str, year: int, month: int):
    with _project_lock(project_id):
        project, access_role = _editable_project_or_404(project_id)
        before_entries = _confirmed_entries_snapshot(project, year, month)
        month_payload = _publish_draft_month_in_project(project, year, month, _user_label(), access_role)
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=_user_label())
    _maybe_notify_pwa_month_change(project, year, month, before_entries, month_payload)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True, project=project), "project": _project_detail_payload(project, month_key, include_draft=True)})


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
        # ViewPWA 購読は project へ FK を持つがリレーション未設定のため明示的に削除する
        # （外部キー制約での削除失敗・孤立行の残留を防ぐ）。
        CloudShiftPwaSubscription.query.filter_by(project_id=project_id).delete(synchronize_session=False)
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


@cloudshift_bp.route("/api/project/<project_id>/hide", methods=["POST"])
@login_required
def api_hide_project(project_id: str):
    """同一紐づけを他オーナーも所持している場合のみ、自分の一覧から非表示にする。

    DB（サーバー）からは削除せず、操作者の一覧表示からのみ除外する。これにより、
    複数オーナーが同じ対象のシフト帳を所持してしまった場合でも、削除によって
    サーバー上のデータが失われるのを防ぎつつ重複表示を解消できる。
    """
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        link_key = _project_link_key(project)
        if not link_key:
            raise CloudShiftError(
                "このシフト帳は非表示にできません（個人・現場の紐づけがありません）。", 400
            )
        others = [
            candidate
            for candidate in _find_projects_with_link_key(link_key, exclude_id=project_id)
            if str(candidate.get("owner_user_id") or "") != str(project.get("owner_user_id") or "")
        ]
        if not others:
            raise CloudShiftError(
                "同じ対象のシフト帳を他のオーナーが所持していないため、非表示にできません。"
                "（非表示にするとデータにアクセスできなくなるのを防ぐためです）",
                400,
            )
        project["hidden"] = True
        _save_project(project)
        _append_history(
            project_id,
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": "owner",
                "action": "project_hidden",
                "month_key": None,
                "changes": ["シフト帳を自分の一覧から非表示にしました"],
            },
        )
    return jsonify({"success": True, "hidden": True})


@cloudshift_bp.route("/api/project/<project_id>/history")
@login_required
def api_history(project_id: str):
    _project_for_current_user_or_404(project_id)
    return jsonify({"history": _load_history(project_id)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/revisions")
@login_required
def api_month_revisions(project_id: str, year: int, month: int):
    project, _ = _editable_project_or_404(project_id)
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
        project, access_role = _editable_project_or_404(project_id)
        before_entries = _confirmed_entries_snapshot(project, year, month)
        month_payload = _restore_month_revision_in_project(project, year, month, revision, _user_label(), access_role)
    month_key = _month_key(year, month)
    # 復元も正式シフトの変更なので、保存時と同様にリンク先シフト帳へ再同期する。
    _resync_shift_month(project, month_key, actor_name=_user_label())
    _maybe_notify_pwa_month_change(project, year, month, before_entries, month_payload)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, include_draft=True, project=project), "project": _project_detail_payload(project, month_key, include_draft=True)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>/summary", methods=["GET", "POST"])
@login_required
def api_month_summary(project_id: str, year: int, month: int):
    project, access_role = _project_for_current_user_or_404(project_id)
    payload = request.get_json(silent=True) if request.method == "POST" and access_role in {"owner", "editor"} else None
    return jsonify({"success": True, "summary": _summary_month_payload(project, year, month, payload)})


def _substitute_request_office_id(payload: dict[str, Any]) -> int:
    office_ids = sorted(_current_share_office_ids())
    if not office_ids:
        raise CloudShiftError("営業所が設定されているユーザーのみ代務要請できます", 400)
    raw_office_id = payload.get("office_id")
    if raw_office_id not in (None, ""):
        try:
            office_id = int(raw_office_id)
        except (TypeError, ValueError) as exc:
            raise CloudShiftError("営業所の指定が不正です", 400) from exc
        if office_id not in office_ids:
            raise CloudShiftError("指定した営業所で代務要請する権限がありません", 403)
        return office_id
    return int(office_ids[0])


def _source_entry_for_substitute_request(
    project: dict[str, Any],
    month_data: dict[str, Any],
    day_key: str,
    entry_id: str,
) -> dict[str, Any]:
    entries = (month_data.get("entries_per_day") or {}).get(day_key)
    if not isinstance(entries, list):
        raise CloudShiftError("指定日のシフトが見つかりません", 404)
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("id") or "") == entry_id:
            if _entry_is_shift_synced(entry):
                raise CloudShiftError("同期反映されたシフトからは代務要請できません", 400)
            option_key, _ = _entry_option_and_name(entry)
            if option_key in LEAVE_OPTION_MAPPINGS:
                raise CloudShiftError("休暇シフトからは代務要請できません", 400)
            return entry
    raise CloudShiftError("指定されたシフトが見つかりません", 404)


def _substitute_request_payload_from_source(
    source_project: dict[str, Any],
    source_entry: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
) -> dict[str, Any]:
    mode = str(source_project.get("mode") or "").strip()
    option_key, entry_name = _entry_option_and_name(source_entry)
    source_entry_id = str(source_entry.get("id") or "").strip()
    base = {
        "id": _substitute_request_entry_id(str(source_project.get("id") or ""), month_key, day_key, source_entry_id),
        "comment": str(source_entry.get("comment") or "").strip(),
        "substitute_resolved": False,
        "substitute_requester_user_id": _user_id(),
        "substitute_requester_name": _user_label(),
        "substitute_requested_at": _utcnow_iso(),
        "substitute_helper_user_id": "",
        "substitute_helper_name": "",
        "substitute_helped_at": "",
        "substitute_source_project_id": str(source_project.get("id") or ""),
        "substitute_source_project_title": str(source_project.get("title") or ""),
        "substitute_source_project_mode": mode,
        "substitute_source_month_key": month_key,
        "substitute_source_day": day_key,
        "substitute_source_entry_id": source_entry_id,
    }
    if mode == "scene":
        site_link = _project_site_payload(source_project)
        site_name = str(site_link.get("site_name") or source_project.get("site_name") or source_project.get("title") or "").strip()
        return {
            **base,
            "value": _format_entry_value(option_key, site_name),
            "employee_name": "",
            "employee_number": "",
            "site_row_id": str(site_link.get("site_row_id") or ""),
            "site_id": str(site_link.get("site_id") or ""),
            "site_name": site_name,
            "site_branch_row_id": "",
            "site_branch": "",
            "substitute_request_type": "scene",
            "substitute_helper_employee_name": "",
            "substitute_helper_employee_number": "",
            "substitute_helper_site_row_id": "",
            "substitute_helper_site_id": "",
            "substitute_helper_site_name": "",
        }
    if mode == "person":
        employee_name = str(source_project.get("title") or entry_name or "").strip()
        employee_number = str(source_project.get("employee_number") or source_entry.get("employee_number") or "").strip()
        return {
            **base,
            "value": _format_entry_value(option_key, employee_name),
            "employee_name": employee_name,
            "employee_number": employee_number,
            "site_row_id": "",
            "site_id": "",
            "site_name": "",
            "site_branch_row_id": "",
            "site_branch": "",
            "substitute_request_type": "person",
            "substitute_helper_employee_name": "",
            "substitute_helper_employee_number": "",
            "substitute_helper_site_row_id": "",
            "substitute_helper_site_id": "",
            "substitute_helper_site_name": "",
    }
    raise CloudShiftError("代務要請は個人シフト・現場シフトのみ対応です", 400)


def _substitute_request_payload_from_day(
    source_project: dict[str, Any],
    *,
    month_key: str,
    day_key: str,
    option_key: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    mode = str(source_project.get("mode") or "").strip()
    normalized_option = str(option_key or "").strip().upper()
    if normalized_option not in OPTION_LABELS:
        normalized_option = ""
    source_entry_id = "day"
    base = {
        "id": _substitute_request_entry_id(str(source_project.get("id") or ""), month_key, day_key, source_entry_id),
        "comment": str(comment or "").strip(),
        "substitute_resolved": False,
        "substitute_requester_user_id": _user_id(),
        "substitute_requester_name": _user_label(),
        "substitute_requested_at": _utcnow_iso(),
        "substitute_helper_user_id": "",
        "substitute_helper_name": "",
        "substitute_helped_at": "",
        "substitute_source_project_id": str(source_project.get("id") or ""),
        "substitute_source_project_title": str(source_project.get("title") or ""),
        "substitute_source_project_mode": mode,
        "substitute_source_month_key": month_key,
        "substitute_source_day": day_key,
        "substitute_source_entry_id": source_entry_id,
    }
    if mode == "scene":
        site_link = _project_site_payload(source_project)
        site_name = str(site_link.get("site_name") or source_project.get("site_name") or source_project.get("title") or "").strip()
        return {
            **base,
            "value": _format_entry_value(normalized_option or None, site_name),
            "employee_name": "",
            "employee_number": "",
            "site_row_id": str(site_link.get("site_row_id") or ""),
            "site_id": str(site_link.get("site_id") or ""),
            "site_name": site_name,
            "site_branch_row_id": "",
            "site_branch": "",
            "substitute_request_type": "scene",
            "substitute_helper_employee_name": "",
            "substitute_helper_employee_number": "",
            "substitute_helper_site_row_id": "",
            "substitute_helper_site_id": "",
            "substitute_helper_site_name": "",
        }
    if mode == "person":
        employee_name = str(source_project.get("title") or "").strip()
        employee_number = str(source_project.get("employee_number") or "").strip()
        return {
            **base,
            "value": _format_entry_value(normalized_option or None, employee_name),
            "employee_name": employee_name,
            "employee_number": employee_number,
            "site_row_id": "",
            "site_id": "",
            "site_name": "",
            "site_branch_row_id": "",
            "site_branch": "",
            "substitute_request_type": "person",
            "substitute_helper_employee_name": "",
            "substitute_helper_employee_number": "",
            "substitute_helper_site_row_id": "",
            "substitute_helper_site_id": "",
            "substitute_helper_site_name": "",
        }
    raise CloudShiftError("代務要請は個人シフト・現場シフトのみ対応です", 400)


@cloudshift_bp.route("/api/project/<project_id>/substitute-request", methods=["POST"])
@login_required
def api_create_substitute_request(project_id: str):
    payload = request.get_json(silent=True) or {}
    year, month = _validate_year_month(payload.get("year"), payload.get("month"))
    try:
        day = int(payload.get("day"))
    except (TypeError, ValueError) as exc:
        raise CloudShiftError("代務要請する日付を指定してください", 400) from exc
    if day < 1 or day > monthrange(year, month)[1]:
        raise CloudShiftError("代務要請する日付が不正です", 400)
    day_key = str(day)
    month_key = _month_key(year, month)
    entry_id = str(payload.get("entry_id") or "").strip()
    office_id = _substitute_request_office_id(payload)

    source_project, access_role = _editable_project_or_404(project_id)
    if source_project.get("mode") not in {"scene", "person"}:
        raise CloudShiftError("代務要請は個人シフト・現場シフトのみ対応です", 400)
    month_data = (source_project.get("months") or {}).get(month_key)
    if not month_data:
        raise CloudShiftError("対象の月が存在しません", 404)
    if entry_id:
        source_entry = _source_entry_for_substitute_request(source_project, month_data, day_key, entry_id)
        request_entry = _substitute_request_payload_from_source(
            source_project,
            source_entry,
            month_key=month_key,
            day_key=day_key,
        )
    else:
        request_entry = _substitute_request_payload_from_day(
            source_project,
            month_key=month_key,
            day_key=day_key,
            option_key=payload.get("option_key"),
            comment=payload.get("comment"),
        )

    substitute_project = _ensure_substitute_project_for_office_month(office_id, year, month)
    with _project_lock(substitute_project["id"]):
        substitute_project = _load_project(substitute_project["id"])
        substitute_month = (substitute_project.get("months") or {}).get(month_key)
        if not substitute_month:
            substitute_project.setdefault("months", {})[month_key] = _build_month_payload(year, month, False, 0, {})
            substitute_month = substitute_project["months"][month_key]
        entries_per_day = _normalize_entries(substitute_month.get("entries_per_day"), year, month)
        day_entries = list(entries_per_day.get(day_key) or [])
        existing_index = next(
            (index for index, entry in enumerate(day_entries) if str(entry.get("id") or "") == request_entry["id"]),
            None,
        )
        if existing_index is None:
            day_entries.append(request_entry)
            history_action = "substitute_request_created"
            history_changes = [f"{month_key} {day_key}日 に代務要請を登録"]
        else:
            existing = dict(day_entries[existing_index])
            preserved = {
                key: existing.get(key)
                for key in (
                    "substitute_helper_employee_name",
                    "substitute_helper_employee_number",
                    "substitute_helper_site_row_id",
                    "substitute_helper_site_id",
                    "substitute_helper_site_name",
                    "substitute_resolved",
                    "substitute_helper_user_id",
                    "substitute_helper_name",
                    "substitute_helped_at",
                )
                if key in existing
            }
            day_entries[existing_index] = {**request_entry, **preserved}
            history_action = "substitute_request_updated"
            history_changes = [f"{month_key} {day_key}日 の代務要請を更新"]
        entries_per_day[day_key] = day_entries
        substitute_month["entries_per_day"] = entries_per_day
        substitute_month["draft_entries_per_day"] = _normalize_entries(entries_per_day, year, month)
        substitute_month["revision"] = int(substitute_month.get("revision", 1) or 1) + 1
        substitute_month["updated_at"] = _utcnow_iso()
        _save_project(substitute_project)
        _append_history(
            substitute_project["id"],
            {
                "timestamp": _utcnow_iso(),
                "editor_name": _user_label(),
                "editor_type": access_role,
                "action": history_action,
                "month_key": month_key,
                "changes": history_changes,
                "source_project_id": project_id,
                "source_entry_id": entry_id or request_entry.get("substitute_source_entry_id", ""),
            },
        )

    substitute_project = _load_project(substitute_project["id"])
    _resync_shift_month(substitute_project, month_key, actor_name=_user_label())
    substitute_project = _load_project(substitute_project["id"])
    substitute_month = (substitute_project.get("months") or {}).get(month_key)
    saved_entry = next(
        (
            entry
            for entry in (substitute_month.get("entries_per_day") or {}).get(day_key, [])
            if str(entry.get("id") or "") == request_entry["id"]
        ),
        request_entry,
    )
    return jsonify(
        {
            "success": True,
            "substitute_project": _project_summary(substitute_project),
            "month": _client_month_payload(substitute_month, include_draft=True, project=substitute_project),
            "entry": saved_entry,
        }
    )


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
    export_month_data = {
        **month_data,
        "entries_per_day": _entries_with_latest_site_links(month_data.get("entries_per_day"), project),
    }
    filename_base = _safe_download_stem(
        f"{project['mode']},{month_data['year']},{month_data['month']},{project['title']}"
    )
    if export_format == "csv":
        csv_text = _csv_text_for_month(
            project["title"],
            project["mode"],
            export_month_data,
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
            export_month_data,
            str(project.get("employee_number") or ""),
        )
        return send_file(
            workbook_bytes,
            as_attachment=True,
            download_name=f"{filename_base}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if export_format == "calendar_png":
        png_bytes = _calendar_png_bytes_for_month(project["title"], project["mode"], export_month_data)
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
    if token_type not in {"view", "edit", "pwa"}:
        abort(404)
    project = _find_project_by_token(token, token_type)
    month_key = request.args.get("month_key")
    payload = _project_detail_payload(project, month_key)
    # PWA は閲覧専用。共有先に公開してよい URL（自身の PWA URL）だけ残す。
    if token_type in {"view", "pwa"}:
        payload["project"]["urls"] = {
            key: value
            for key, value in payload["project"]["urls"].items()
            if (token_type == "view" and key == "view_url") or (token_type == "pwa" and key == "pwa_url")
        }
    payload["project"]["shift_book"] = {
        "settings": {
            "leave_change_requests": {
                "enabled": _leave_change_request_enabled(project),
            }
        },
        "pending_leave_change_request_count": 0,
        "unviewed_leave_change_request_count": 0,
    }
    payload["access_mode"] = token_type
    payload["authenticated_editor_name"] = _user_label() if current_user.is_authenticated else None
    payload["viewer_capabilities"] = {
        "leave_change_request": token_type == "view" and _leave_change_request_enabled(project),
        "pending_leave_change_request_entry_ids": _pending_leave_change_request_entry_ids(
            project,
            str(payload.get("active_month_key") or ""),
        )
        if token_type == "view"
        else [],
    }
    return jsonify(payload)


@cloudshift_bp.route("/api/public/view/<token>/leave-change-requests", methods=["POST"])
def api_public_create_leave_change_request(token: str):
    payload = request.get_json(silent=True) or {}
    project = _find_project_by_token(token, "view")
    with _project_lock(project["id"]):
        project = _find_project_by_token(token, "view")
        request_payload = _create_leave_change_request(project, payload)
    return jsonify({"success": True, "request": request_payload})


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


@cloudshift_bp.route("/api/public/pwa/<token>/export/<export_format>")
def api_export_public_pwa(token: str, export_format: str):
    project = _find_project_by_token(token, "pwa")
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
        before_entries = _confirmed_entries_snapshot(project, year, month)
        month_payload = _save_month_in_project(project, year, month, payload, actor_name, actor_type)
    month_key = _month_key(year, month)
    _resync_shift_month(project, month_key, actor_name=actor_name)
    _maybe_notify_pwa_month_change(project, year, month, before_entries, month_payload)
    return jsonify({"success": True, "month": _client_month_payload(month_payload, project=project), "project": _project_detail_payload(project, month_key)})


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
