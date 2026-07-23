from __future__ import annotations

import re
import secrets
from calendar import monthrange
from datetime import date
from typing import Any, Iterable, Mapping

from app.services.worktime_engine import calculate, request_from_json, result_to_dict, time_range_from_json


LARGE_MODE = "large"
DAY_TYPES = {"weekday", "saturday", "holiday"}
LEAVE_KINDS = {"legal_rest", "scheduled_rest", "rest", "substitute_rest", "paid", "special", "other"}
CHECK_DEFAULTS: dict[str, dict[str, Any]] = {
    "kaizen_monthly_bind": {"enabled": True, "warn_minutes": 16860},
    "kaizen_daily_bind": {"enabled": True, "warn_minutes": 780, "max_minutes": 900},
    "kaizen_rest_period": {"enabled": True, "min_minutes": 540},
    "overtime_monthly": {"enabled": False, "warn_minutes": 2700},
    "anei_long_work": {"enabled": False, "warn_minutes": 4800},
    "consecutive_days": {"enabled": False, "warn_days": 7},
}


def default_large_config() -> dict[str, Any]:
    leave_rows = [
        ("法定休", "法定休", "legal_rest", False, "#fee2e2"),
        ("所定休", "所定休", "scheduled_rest", False, "#e5e7eb"),
        ("振休", "振休", "substitute_rest", False, "#e5e7eb"),
        ("有休", "有休", "paid", False, "#dcfce7"),
        ("希望休", "希望休", "scheduled_rest", True, "#dbeafe"),
        ("希望有休", "希望有休", "paid", True, "#ccfbf1"),
        ("希望振休", "希望振休", "substitute_rest", True, "#ede9fe"),
    ]
    return {
        "version": 1,
        "members": [],
        "codes": [
            {
                "key": key,
                "label": label,
                "category": "leave",
                "times": {"weekday": None, "saturday": None, "holiday": None},
                "break_minutes": None,
                "leave_kind": leave_kind,
                "requested": requested,
                "color": color,
                "order": (index + 1) * 10,
                "active": True,
                "note": "",
            }
            for index, (key, label, leave_kind, requested, color) in enumerate(leave_rows)
        ],
        "settings": {
            "break_minutes": 60,
            "scheduled_bind_minutes": 570,
            "checks": {key: dict(value) for key, value in CHECK_DEFAULTS.items()},
        },
    }


def _text(value: Any, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _optional_minutes(value: Any, label: str, *, maximum: int = 100000) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}は分単位の整数で指定してください") from exc
    if result < 0 or result > maximum:
        raise ValueError(f"{label}が範囲外です")
    return result


def _required_minutes(value: Any, default: int, label: str, *, maximum: int = 100000) -> int:
    result = _optional_minutes(default if value in (None, "") else value, label, maximum=maximum)
    assert result is not None
    return result


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _normalize_color(value: Any) -> str:
    color = _text(value, 16)
    return color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#e3f2fd"


def _member_column_type(raw_member: Mapping[str, Any]) -> str:
    """Normalize the member kind while preserving pre-column_type substitute rows."""
    explicit = _text(
        raw_member.get("column_type", raw_member.get("columnType")),
        20,
    ).lower()
    if explicit == "substitute":
        return "substitute"
    if explicit == "regular":
        return "regular"

    # The first substitute-column UI already used a stable ``sub_`` id prefix,
    # but its server normalizer did not persist column_type. Treat only that
    # prefix as the legacy marker: a regular free-input member can also have
    # blank employee fields.
    member_id = _text(raw_member.get("id"), 80).lower()
    return "substitute" if member_id.startswith("sub_") else "regular"


def normalize_large_config(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    members_raw = data.get("members") or []
    codes_raw = data.get("codes") or []
    if not isinstance(members_raw, list) or len(members_raw) > 60:
        raise ValueError("メンバーは60人以内で登録してください")
    if not isinstance(codes_raw, list) or len(codes_raw) > 200:
        raise ValueError("シフトコードは200件以内で登録してください")
    members: list[dict[str, Any]] = []
    member_ids: set[str] = set()
    for index, raw_member in enumerate(members_raw):
        if not isinstance(raw_member, Mapping):
            raise ValueError("メンバーの形式が不正です")
        member_id = _text(raw_member.get("id"), 80) or f"mem_{secrets.token_hex(8)}"
        if member_id in member_ids:
            raise ValueError("メンバーIDが重複しています")
        member_ids.add(member_id)
        display_name = _text(raw_member.get("display_name"), 120)
        if not display_name:
            raise ValueError("メンバーの表示名は必須です")
        members.append({
            "id": member_id,
            "display_name": display_name,
            "column_type": _member_column_type(raw_member),
            "employee_number": _text(raw_member.get("employee_number"), 40),
            "employee_name": _text(raw_member.get("employee_name"), 120),
            "scheduled_bind_minutes": _optional_minutes(raw_member.get("scheduled_bind_minutes"), "個人所定拘束時間", maximum=1440),
            "order": int(raw_member.get("order", (index + 1) * 10) or 0),
            "active": _bool(raw_member.get("active"), True),
            "note": _text(raw_member.get("note"), 500),
        })
    codes: list[dict[str, Any]] = []
    folded_keys: set[str] = set()
    for index, raw_code in enumerate(codes_raw):
        if not isinstance(raw_code, Mapping):
            raise ValueError("シフトコードの形式が不正です")
        key = _text(raw_code.get("key"), 80)
        if not key or key.casefold() in folded_keys:
            raise ValueError("入力用の略号は空欄にできません。同じ略号は重複して登録できません")
        folded_keys.add(key.casefold())
        category = _text(raw_code.get("category"), 20).lower() or "work"
        if category not in {"work", "leave"}:
            raise ValueError(f"シフト {key} の種類（勤務/休み）が不正です")
        source_times = raw_code.get("times") if isinstance(raw_code.get("times"), Mapping) else {}
        times: dict[str, dict[str, str] | None] = {}
        for day_type in DAY_TYPES:
            value = source_times.get(day_type)
            parsed = time_range_from_json(value)
            times[day_type] = None if parsed is None else {
                "start": str(value.get("start")).strip().zfill(5),
                "end": str(value.get("end")).strip().zfill(5),
            }
        leave_kind = _text(raw_code.get("leave_kind"), 40)
        if category == "leave" and leave_kind not in LEAVE_KINDS:
            leave_kind = "other"
        codes.append({
            "key": key,
            "label": _text(raw_code.get("label"), 120) or key,
            "category": category,
            "times": times if category == "work" else {"weekday": None, "saturday": None, "holiday": None},
            "break_minutes": _optional_minutes(raw_code.get("break_minutes"), f"コード {key} の休憩", maximum=1440),
            "leave_kind": leave_kind if category == "leave" else "",
            "requested": _bool(raw_code.get("requested")) if category == "leave" else False,
            "color": _normalize_color(raw_code.get("color")),
            "order": int(raw_code.get("order", (index + 1) * 10) or 0),
            "active": _bool(raw_code.get("active"), True),
            "note": _text(raw_code.get("note"), 500),
        })
    raw_settings = data.get("settings") if isinstance(data.get("settings"), Mapping) else {}
    raw_checks = raw_settings.get("checks") if isinstance(raw_settings.get("checks"), Mapping) else {}
    checks: dict[str, dict[str, Any]] = {}
    for key, defaults in CHECK_DEFAULTS.items():
        incoming = raw_checks.get(key) if isinstance(raw_checks.get(key), Mapping) else {}
        normalized = {"enabled": _bool(incoming.get("enabled"), bool(defaults["enabled"]))}
        for setting_key, default in defaults.items():
            if setting_key == "enabled":
                continue
            normalized[setting_key] = _required_minutes(incoming.get(setting_key), int(default), setting_key, maximum=100000)
        checks[key] = normalized
    return {
        "version": 1,
        "members": sorted(members, key=lambda row: (row["order"], row["display_name"])),
        "codes": sorted(codes, key=lambda row: (row["order"], row["key"].casefold())),
        "settings": {
            "break_minutes": _required_minutes(raw_settings.get("break_minutes"), 60, "休憩", maximum=1440),
            "scheduled_bind_minutes": _required_minutes(raw_settings.get("scheduled_bind_minutes"), 570, "所定拘束時間", maximum=1440),
            "checks": checks,
        },
    }


def normalize_large_meta(raw: Any, year: int, month: int, *, allow_baseline: bool = True) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    days = monthrange(year, month)[1]
    result: dict[str, Any] = {"day_types": {}, "day_notes": {}}
    for key, value in (data.get("day_types") if isinstance(data.get("day_types"), Mapping) else {}).items():
        try:
            day = int(key)
        except (TypeError, ValueError):
            continue
        day_type = str(value or "").strip().lower()
        if 1 <= day <= days and day_type in DAY_TYPES:
            result["day_types"][str(day)] = day_type
    for key, value in (data.get("day_notes") if isinstance(data.get("day_notes"), Mapping) else {}).items():
        try:
            day = int(key)
        except (TypeError, ValueError):
            continue
        note = str(value or "").strip()[:1000]
        if 1 <= day <= days and note:
            result["day_notes"][str(day)] = note
    if allow_baseline and isinstance(data.get("baseline"), Mapping):
        result["baseline"] = dict(data["baseline"])
    return result


def day_type_for_date(year: int, month: int, day: int, overrides: Mapping[str, Any], holidays: Iterable[str]) -> str:
    override = str(overrides.get(str(day)) or "")
    if override in DAY_TYPES:
        return override
    target = date(year, month, day)
    if target.isoformat() in set(holidays) or target.weekday() == 6:
        return "holiday"
    if target.weekday() == 5:
        return "saturday"
    return "weekday"


def calculate_large_month(config: Mapping[str, Any], month_data: Mapping[str, Any], holidays: Iterable[str]) -> dict[str, Any]:
    year, month = int(month_data["year"]), int(month_data["month"])
    normalized_config = normalize_large_config(config)
    meta = normalize_large_meta(month_data.get("meta_data"), year, month)
    entries = month_data.get("entries_per_day") if isinstance(month_data.get("entries_per_day"), Mapping) else {}
    entry_maps = {
        str(day): {
            str(entry.get("member_id") or ""): entry
            for entry in (entries.get(str(day)) or [])
            if isinstance(entry, Mapping) and entry.get("member_id")
        }
        for day in range(1, monthrange(year, month)[1] + 1)
    }
    people = []
    for member in normalized_config["members"]:
        if not member["active"]:
            continue
        days = []
        for day in range(1, monthrange(year, month)[1] + 1):
            entry = entry_maps[str(day)].get(member["id"], {})
            assignments = entry.get("assignments") if isinstance(entry.get("assignments"), list) else []
            local_assignments = [
                {"code_key": str(item.get("code_key") or "")}
                for item in assignments
                if isinstance(item, Mapping)
                and str(item.get("source_type") or "local") == "local"
                and item.get("code_key")
            ]
            days.append({
                "day": day,
                "day_type": day_type_for_date(year, month, day, meta["day_types"], holidays),
                "code": entry.get("value", "") if not assignments else (local_assignments[0]["code_key"] if local_assignments else ""),
                "assignments": local_assignments,
                "holiday_kind": entry.get("holiday_kind", ""),
                "time_override": entry.get("time_override"),
                "bind_override_minutes": entry.get("bind_override_minutes"),
                "has_external_assignment": any(
                    isinstance(item, Mapping) and str(item.get("source_type") or "") != "local"
                    for item in assignments
                ),
            })
        people.append({
            "person_id": member["id"],
            "label": member["display_name"],
            "scheduled_bind_minutes": member["scheduled_bind_minutes"],
            "prev_day_end": None,
            "days": days,
        })
    worktime_request = request_from_json({
        "version": 1,
        "year": year,
        "month": month,
        "codes": normalized_config["codes"],
        "settings": normalized_config["settings"],
        "people": people,
    })
    return result_to_dict(calculate(worktime_request))
