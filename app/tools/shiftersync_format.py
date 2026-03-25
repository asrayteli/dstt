from __future__ import annotations

import csv
import io
import secrets
import re
from calendar import monthrange
from typing import Any


COMMENT_ROW_PREFIX = "#comment"
OPTION_MAPPINGS = {
    "A": "午前",
    "P": "午後",
    "E": "早番",
    "L": "遅番",
    "M": "マイクロ",
    "C": "中型",
    "O": "大型",
    "W": "ワゴン",
    "V": "役員車両",
    "N1": "1号車",
    "N2": "2号車",
    "N3": "3号車",
    "N4": "4号車",
    "N5": "5号車",
}

ENTRY_VALUE_PATTERN = re.compile(r"^!([^!]+)!(.+)$")


def validate_year_month(year: int, month: int) -> tuple[int, int]:
    try:
        year_value = int(year)
        month_value = int(month)
    except (TypeError, ValueError) as exc:
        raise ValueError("CSV の年/月が不正です") from exc

    if year_value < 1900 or year_value > 2100:
        raise ValueError("年は 1900 から 2100 の範囲で指定してください")
    if month_value < 1 or month_value > 12:
        raise ValueError("月は 1 から 12 の範囲で指定してください")
    return year_value, month_value


def generate_entry_id() -> str:
    return secrets.token_hex(8)


def parse_entry_value(value: str) -> tuple[str | None, str]:
    text = str(value or "").strip()
    match = ENTRY_VALUE_PATTERN.match(text)
    if not match:
      return None, text
    return match.group(1), match.group(2)


def normalize_entry(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        value = str(raw.get("value", "")).strip()
        if not value:
            return {}
        return {
            "id": str(raw.get("id") or generate_entry_id()),
            "value": value,
            "comment": str(raw.get("comment", "") or "").strip(),
        }

    value = str(raw or "").strip()
    if not value:
        return {}
    return {
        "id": generate_entry_id(),
        "value": value,
        "comment": "",
    }


def empty_entries_for_month(year: int, month: int) -> dict[str, list[dict[str, str]]]:
    year, month = validate_year_month(year, month)
    return {str(day): [] for day in range(1, monthrange(year, month)[1] + 1)}


def normalize_entries_for_month(entries: Any, year: int, month: int) -> dict[str, list[dict[str, str]]]:
    normalized = empty_entries_for_month(year, month)
    if not isinstance(entries, dict):
        return normalized

    for raw_day, raw_values in entries.items():
        try:
            day = int(raw_day)
        except (TypeError, ValueError):
            continue
        key = str(day)
        if key not in normalized or not isinstance(raw_values, list):
            continue

        day_entries: list[dict[str, str]] = []
        for item in raw_values:
            entry = normalize_entry(item)
            if entry:
                day_entries.append(entry)
        normalized[key] = day_entries
    return normalized


def serialize_entry_rows(
    mode: str,
    year: int,
    month: int,
    title: str,
    required_capacity: int,
    entries_per_day: dict[str, list[dict[str, str]]],
) -> list[list[Any]]:
    year, month = validate_year_month(year, month)
    header = [mode, year, month, title]
    if required_capacity > 0:
        header.append(required_capacity)

    rows: list[list[Any]] = [
        header,
        ["日付", "出勤者" if mode == "scene" else "現場"],
    ]

    comment_rows: list[list[Any]] = []
    for day in range(1, monthrange(year, month)[1] + 1):
        key = str(day)
        day_entries = [normalize_entry(item) for item in entries_per_day.get(key, [])]
        visible_entries = [entry["value"] for entry in day_entries if entry]
        if visible_entries:
            rows.append([day, *visible_entries])
        for index, entry in enumerate(day_entries):
            if entry and entry.get("comment"):
                comment_rows.append([COMMENT_ROW_PREFIX, day, index, entry["comment"]])

    return rows + comment_rows


def serialize_csv_text(
    mode: str,
    year: int,
    month: int,
    title: str,
    required_capacity: int,
    entries_per_day: dict[str, list[dict[str, str]]],
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(serialize_entry_rows(mode, year, month, title, required_capacity, entries_per_day))
    return buffer.getvalue()


def parse_csv_text(text: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 4:
        raise ValueError("ShifterSync CSV のヘッダーが不正です")

    mode = str(rows[0][0]).strip().lower()
    if mode not in {"scene", "person"}:
        raise ValueError("mode は scene または person だけ対応しています")

    try:
        year, month = validate_year_month(rows[0][1], rows[0][2])
    except (TypeError, ValueError) as exc:
        raise ValueError("CSV の年月が不正です") from exc

    title = str(rows[0][3]).strip()
    required_capacity = 0
    if len(rows[0]) >= 5:
        try:
            required_capacity = max(0, int(rows[0][4]))
        except (TypeError, ValueError):
            required_capacity = 0

    entries_per_day = empty_entries_for_month(year, month)
    comment_rows: list[list[str]] = []
    for row in rows[2:]:
        if not row:
            continue
        head = str(row[0]).strip()
        if head.isdigit():
            day_key = str(int(head))
            if day_key not in entries_per_day:
                continue
            entries_per_day[day_key] = [
                normalize_entry({"value": cell, "comment": ""})
                for cell in row[1:]
                if str(cell).strip()
            ]
            continue
        if head == COMMENT_ROW_PREFIX:
            comment_rows.append(row)

    for row in comment_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["comment"] = str(row[3] or "").strip()

    return {
        "mode": mode,
        "year": year,
        "month": month,
        "title": title,
        "capacity_enabled": required_capacity > 0,
        "required_capacity": required_capacity,
        "entries_per_day": entries_per_day,
    }


def entry_display_text(entry: Any, *, include_comment: bool = False, comment_limit: int | None = None) -> str:
    normalized = normalize_entry(entry)
    if not normalized:
        return ""
    option_key, name = parse_entry_value(normalized["value"])
    head = f"{OPTION_MAPPINGS.get(option_key, option_key)} {name}" if option_key else name
    comment = normalized.get("comment", "").strip()
    if not include_comment or not comment:
        return head
    if comment_limit is not None and len(comment) > comment_limit:
        comment = f"{comment[:comment_limit]}..."
    return f"{head} - {comment}"


def entry_name_for_comparison(entry: Any) -> str:
    normalized = normalize_entry(entry)
    if not normalized:
        return ""
    _, name = parse_entry_value(normalized["value"])
    return name


def entry_option_and_name(entry: Any) -> tuple[str | None, str, str]:
    normalized = normalize_entry(entry)
    if not normalized:
        return None, "", ""
    option_key, name = parse_entry_value(normalized["value"])
    return option_key, name, normalized.get("comment", "")
