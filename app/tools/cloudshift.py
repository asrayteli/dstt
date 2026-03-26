from __future__ import annotations

import io
import json
import os
import secrets
import tempfile
import time
from calendar import monthrange
from contextlib import contextmanager
from copy import copy
from datetime import datetime
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

try:
    from .shiftersync_format import (
        LEAVE_OPTION_MAPPINGS,
        entry_display_text,
        normalize_entries_for_month,
        parse_csv_text,
        parse_entry_value,
        serialize_csv_text,
        serialize_entry_rows,
    )
    from .japan_holidays import JAPAN_HOLIDAYS
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        LEAVE_OPTION_MAPPINGS,
        entry_display_text,
        normalize_entries_for_month,
        parse_csv_text,
        parse_entry_value,
        serialize_csv_text,
        serialize_entry_rows,
    )
    from app.tools.japan_holidays import JAPAN_HOLIDAYS  # type: ignore


cloudshift_bp = Blueprint("cloudshift", __name__, url_prefix="/tools/shiftersync/cloudshift")

LOCK_TIMEOUT_SECONDS = 8.0
LOCK_POLL_SECONDS = 0.05
MAX_REVISION_SNAPSHOTS = 20


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
                raise TimeoutError("cloudshift project lock timeout")
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
        "revision": revision,
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision_snapshots": {},
    }


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    month_keys = _sort_month_keys(list((project.get("months") or {}).keys()))
    return {
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
    }


def _project_public_urls(project: dict[str, Any]) -> dict[str, str]:
    return {
        "view_url": url_for("cloudshift.public_view", token=project["view_token"], _external=True),
        "edit_url": url_for("cloudshift.public_edit", token=project["edit_token"], _external=True),
    }


def _client_month_payload(month_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not month_data:
        return None
    payload = dict(month_data)
    payload.pop("revision_snapshots", None)
    return payload


def _load_project(project_id: str) -> dict[str, Any]:
    project = _load_json(_project_path(project_id))
    if not project:
        abort(404)
    return project


def _save_project(project: dict[str, Any]) -> None:
    project["updated_at"] = _utcnow_iso()
    _safe_write_json(_project_path(project["id"]), project)


def _append_history(project_id: str, entry: dict[str, Any]) -> None:
    history_path = _history_path(project_id)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_history(project_id: str) -> list[dict[str, Any]]:
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


def _owner_project_or_404(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("owner_user_id") != _user_id():
        abort(404)
    return project


def _find_project_by_token(token: str, token_type: str) -> dict[str, Any]:
    key = "view_token" if token_type == "view" else "edit_token"
    for path in _shifts_dir().glob("*.json"):
        project = _load_json(path)
        if not project:
            continue
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
            f"{current_month['year']}-{current_month['month']:02d} 縺ｮ蠢・ｦ∽ｺｺ謨ｰ繧・"
            f"{previous_month.get('required_capacity', 0)} 縺九ｉ {current_month.get('required_capacity', 0)} 縺ｫ螟画峩"
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
        for source in (current_day, incoming_day):
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
    return _client_month_payload(month_data) or {}


def _trim_revision_snapshots(snapshots: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(snapshots.keys(), key=lambda value: int(value))
    trimmed = dict(snapshots)
    if len(keys) <= MAX_REVISION_SNAPSHOTS:
        return trimmed
    for key in keys[:-MAX_REVISION_SNAPSHOTS]:
        trimmed.pop(key, None)
    return trimmed


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


def _project_detail_payload(project: dict[str, Any], selected_month_key: str | None = None) -> dict[str, Any]:
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
        "month": _client_month_payload(month_data),
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
    for path in _shifts_dir().glob("*.json"):
        project = _load_json(path)
        if not project or project.get("owner_user_id") != owner_id:
            continue
        projects.append(_project_summary(project))
    projects.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jsonify({"projects": projects})


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

    project_id = _project_id()
    month_key = _month_key(year, month)
    project = {
        "id": project_id,
        "owner_user_id": _user_id(),
        "title": title,
        "mode": mode,
        "employee_number": employee_number if mode == "person" else "",
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
    return jsonify({"success": True, "project": _project_detail_payload(project)})


@cloudshift_bp.route("/api/project/<project_id>")
@login_required
def api_project_detail(project_id: str):
    project = _owner_project_or_404(project_id)
    selected_month_key = request.args.get("month_key")
    return jsonify(_project_detail_payload(project, selected_month_key))


@cloudshift_bp.route("/api/project/<project_id>/meta", methods=["PUT"])
@login_required
def api_project_meta(project_id: str):
    data = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        new_title = _sanitize_title(data.get("title", project["title"]))
        old_title = project["title"]
        old_employee_number = str(project.get("employee_number") or "")
        new_employee_number = _sanitize_employee_number(data.get("employee_number", project.get("employee_number", "")))
        if project.get("mode") != "person":
            new_employee_number = ""
        metadata_changed = False
        if new_title != project["title"]:
            project["title"] = new_title
            metadata_changed = True
        if new_employee_number != str(project.get("employee_number") or ""):
            project["employee_number"] = new_employee_number
            metadata_changed = True
        if metadata_changed:
            _save_project(project)
            changes = []
            if new_title != old_title:
                changes.append(f"タイトルを {old_title} から {new_title} に変更")
            if new_employee_number != old_employee_number:
                changes.append("社員IDを更新")
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
    return jsonify({"success": True, "project": _project_detail_payload(project)})


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
        month_payload = _build_month_payload(
            year,
            month,
            capacity_enabled or source_month.get("capacity_enabled", False),
            required_capacity if capacity_enabled else source_month.get("required_capacity", 0),
            source_month.get("entries_per_day", {}),
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
    return jsonify({"success": True, "project": _project_detail_payload(project, month_key)})


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
    incoming_month = {
        "year": year,
        "month": month,
        "required_capacity": _sanitize_capacity(payload.get("required_capacity"))[1],
        "entries_per_day": payload.get("entries_per_day") or {},
    }
    merged = _merge_month_payload(current_month, incoming_month, base_month)
    snapshots = dict(current_month.get("revision_snapshots") or {})
    snapshots[str(int(current_month.get("revision", 1)))] = _snapshot_month_payload(current_month)
    merged["revision_snapshots"] = _trim_revision_snapshots(snapshots)
    changes = _describe_month_changes(current_month, merged)
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


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>", methods=["PUT"])
@login_required
def api_save_month(project_id: str, year: int, month: int):
    payload = request.get_json(silent=True) or {}
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_payload = _save_month_in_project(project, year, month, payload, _user_label(), "owner")
    month_key = _month_key(year, month)
    return jsonify({"success": True, "month": month_payload, "project": _project_detail_payload(project, month_key)})


@cloudshift_bp.route("/api/project/<project_id>/month/<int:year>/<int:month>", methods=["DELETE"])
@login_required
def api_delete_month(project_id: str, year: int, month: int):
    month_key = _month_key(year, month)
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        month_keys = list((project.get("months") or {}).keys())
        if month_key not in month_keys:
            abort(404)
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
    return jsonify({"success": True})


@cloudshift_bp.route("/api/project/<project_id>", methods=["DELETE"])
@login_required
def api_delete_project(project_id: str):
    with _project_lock(project_id):
        project = _owner_project_or_404(project_id)
        project_path = _project_path(project_id)
        history_path = _history_path(project_id)
        if project_path.exists():
            project_path.unlink()
        if history_path.exists():
            history_path.unlink()
    return jsonify({"success": True, "deleted_project_id": project["id"]})


@cloudshift_bp.route("/api/project/<project_id>/history")
@login_required
def api_history(project_id: str):
    _owner_project_or_404(project_id)
    return jsonify({"history": _load_history(project_id)})


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
    project = _owner_project_or_404(project_id)
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
    return jsonify({"success": True, "month": month_payload, "project": _project_detail_payload(project, month_key)})
