from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from typing import Any

try:
    from .shiftersync_format import (
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
    )
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
    )


NUMBER_CAR_OPTIONS = {"N1", "N2", "N3", "N4", "N5"}
TEMPORARY_OPTION = "TEMP"
TIME_CONFLICT_RULES = {
    ("A", "P"): False,
    ("A", "E"): True,
    ("A", "L"): False,
    ("P", "E"): False,
    ("P", "L"): True,
    ("E", "L"): False,
}
VEHICLE_OPTIONS = {"M", "C", "O", "W", "V"}
LEAVE_OPTION_KEYS = set(LEAVE_OPTION_MAPPINGS.keys())


def is_duplicate_by_rules(
    option1: str | None, option2: str | None, *, same_site: bool = False
) -> bool:
    if option1 in LEAVE_OPTION_KEYS or option2 in LEAVE_OPTION_KEYS:
        return False

    if option1 == TEMPORARY_OPTION or option2 == TEMPORARY_OPTION:
        return same_site and option1 == option2 == TEMPORARY_OPTION

    if option1 is None or option2 is None:
        return True

    if option1 in NUMBER_CAR_OPTIONS or option2 in NUMBER_CAR_OPTIONS:
        return option1 in NUMBER_CAR_OPTIONS and option1 == option2

    if option1 in {"A", "P", "E", "L"} or option2 in {"A", "P", "E", "L"}:
        if option1 not in {"A", "P", "E", "L"} or option2 not in {"A", "P", "E", "L"}:
            return False
        key = tuple(sorted((option1, option2)))
        return TIME_CONFLICT_RULES.get(key, option1 == option2)

    if option1 in VEHICLE_OPTIONS and option2 in VEHICLE_OPTIONS:
        if option1 == "V" or option2 == "V":
            return True
        if option1 == option2:
            return True
        return False

    return option1 == option2


def compare_shift_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        raise ValueError("比較するシフトデータを選択してください")
    if len(payloads) > 50:
        raise ValueError("比較するシフトデータは50件までにしてください")

    mode = None
    year = None
    month = None
    targets: list[str] = []
    capacities: list[int | None] = []
    sources: list[dict[str, Any]] = []
    shift_data = defaultdict(lambda: [[] for _ in range(len(payloads))])

    for payload_index, payload in enumerate(payloads):
        source_label = str(payload.get("label") or payload.get("title") or f"source_{payload_index + 1}").strip()
        source_mode = str(payload.get("mode") or "").strip().lower()
        source_year = int(payload.get("year"))
        source_month = int(payload.get("month"))
        source_entries = payload.get("entries_per_day") or {}
        source_capacity = payload.get("required_capacity") or None

        if mode is None:
            mode, year, month = source_mode, source_year, source_month
        elif (mode, year, month) != (source_mode, source_year, source_month):
            raise ValueError("比較するシフトデータは同じモード・同じ年月にそろえてください")

        targets.append(source_label)
        capacities.append(source_capacity)
        sources.append(
            {
                "label": source_label,
                "title": str(payload.get("title") or source_label),
                "required_capacity": source_capacity,
                "project_id": str(payload.get("project_id") or ""),
                "month_key": str(payload.get("month_key") or ""),
                "mode": source_mode,
            }
        )

        for day_key, entries in source_entries.items():
            day = int(day_key)
            normalized_entries = []
            for entry in entries:
                option_key, name, comment = entry_option_and_name(entry)
                normalized_entries.append(
                    {
                        "original": entry["value"],
                        "display": entry_display_text(entry),
                        "comparison": entry_name_for_comparison(entry),
                        "option": option_key,
                        "name": name,
                        "comment": comment,
                    }
                )
            shift_data[day][payload_index].extend(normalized_entries)

    if mode is None or year is None or month is None:
        raise ValueError("比較するシフトデータがありません")

    conflicts = []
    same_site_conflicts = []

    for day, per_source_entries in shift_data.items():
        for source_index, entries in enumerate(per_source_entries):
            same_site_grouped = defaultdict(list)
            for entry in entries:
                if entry["name"]:
                    same_site_grouped[entry["name"]].append(entry)
            for items in same_site_grouped.values():
                if len(items) < 2:
                    continue
                for left_index, left in enumerate(items):
                    for right in items[left_index + 1 :]:
                        if is_duplicate_by_rules(left["option"], right["option"], same_site=True):
                            same_site_conflicts.append(
                                {"date": day, "entry": left["original"], "file_index": source_index}
                            )
                            same_site_conflicts.append(
                                {"date": day, "entry": right["original"], "file_index": source_index}
                            )

        grouped = defaultdict(list)
        for source_index, entries in enumerate(per_source_entries):
            for entry in entries:
                if entry["name"]:
                    grouped[entry["name"]].append({"file_index": source_index, "entry": entry})

        for items in grouped.values():
            if len(items) < 2:
                continue
            for left_index, left in enumerate(items):
                for right in items[left_index + 1 :]:
                    if left["file_index"] == right["file_index"]:
                        continue
                    if is_duplicate_by_rules(left["entry"]["option"], right["entry"]["option"]):
                        conflicts.append({"date": day, "entry": left["entry"]["original"]})
                        conflicts.append({"date": day, "entry": right["entry"]["original"]})

    conflicts = list({f'{item["date"]}-{item["entry"]}': item for item in conflicts}.values())
    same_site_conflicts = list(
        {
            f'{item["date"]}-{item["entry"]}-{item["file_index"]}': item
            for item in same_site_conflicts
        }.values()
    )

    all_dates = list(range(1, monthrange(year, month)[1] + 1))
    matrix = {
        day: shift_data.get(day, [[] for _ in range(len(payloads))])
        for day in all_dates
    }

    return {
        "mode": mode,
        "year": year,
        "month": month,
        "month_key": f"{year:04d}-{month:02d}",
        "targets": targets,
        "sources": sources,
        "capacities": capacities,
        "dates": all_dates,
        "matrix": matrix,
        "conflicts": conflicts,
        "same_site_conflicts": same_site_conflicts,
        "option_mappings": OPTION_MAPPINGS,
        "total_files": len(payloads),
    }
