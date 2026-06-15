from __future__ import annotations

import csv
import io
import re
import secrets
from calendar import monthrange
from typing import Any


COMMENT_ROW_PREFIX = "#comment"
EMPLOYEE_NAME_ROW_PREFIX = "#employee_name"
EMPLOYEE_NUMBER_ROW_PREFIX = "#employee_number"
PROJECT_EMPLOYEE_NUMBER_ROW_PREFIX = "#project_employee_number"
SITE_ROW_ID_ROW_PREFIX = "#site_row_id"
SITE_ID_ROW_PREFIX = "#site_id"
SITE_NAME_ROW_PREFIX = "#site_name"
SITE_BRANCH_ROW_ID_ROW_PREFIX = "#site_branch_row_id"
SITE_BRANCH_ROW_PREFIX = "#site_branch"
SUBSTITUTE_REQUEST_TYPE_ROW_PREFIX = "#substitute_request_type"
SUBSTITUTE_HELPER_EMPLOYEE_NAME_ROW_PREFIX = "#substitute_helper_employee_name"
SUBSTITUTE_HELPER_EMPLOYEE_NUMBER_ROW_PREFIX = "#substitute_helper_employee_number"
SUBSTITUTE_HELPER_SITE_ROW_ID_ROW_PREFIX = "#substitute_helper_site_row_id"
SUBSTITUTE_HELPER_SITE_ID_ROW_PREFIX = "#substitute_helper_site_id"
SUBSTITUTE_HELPER_SITE_NAME_ROW_PREFIX = "#substitute_helper_site_name"
SUBSTITUTE_RESOLVED_ROW_PREFIX = "#substitute_resolved"
SUBSTITUTE_REQUESTER_USER_ID_ROW_PREFIX = "#substitute_requester_user_id"
SUBSTITUTE_REQUESTER_NAME_ROW_PREFIX = "#substitute_requester_name"
SUBSTITUTE_REQUESTED_AT_ROW_PREFIX = "#substitute_requested_at"
SUBSTITUTE_HELPER_USER_ID_ROW_PREFIX = "#substitute_helper_user_id"
SUBSTITUTE_HELPER_NAME_ROW_PREFIX = "#substitute_helper_name"
SUBSTITUTE_HELPED_AT_ROW_PREFIX = "#substitute_helped_at"
SUBSTITUTE_UNASSIGNED_HELPER_ROW_PREFIX = "#substitute_unassigned_helper"
SUBSTITUTE_SOURCE_PROJECT_ID_ROW_PREFIX = "#substitute_source_project_id"
SUBSTITUTE_SOURCE_PROJECT_TITLE_ROW_PREFIX = "#substitute_source_project_title"
SUBSTITUTE_SOURCE_PROJECT_MODE_ROW_PREFIX = "#substitute_source_project_mode"
SUBSTITUTE_SOURCE_MONTH_KEY_ROW_PREFIX = "#substitute_source_month_key"
SUBSTITUTE_SOURCE_DAY_ROW_PREFIX = "#substitute_source_day"
SUBSTITUTE_SOURCE_ENTRY_ID_ROW_PREFIX = "#substitute_source_entry_id"
# \u7b2c\u4e8c\u30aa\u30d7\u30b7\u30e7\u30f3\uff08\u4ee3\u52d9\uff0f\u7814\u4fee\uff09\u306f entry \u306e\u5024\u3068\u306f\u5225\u30d5\u30a3\u30fc\u30eb\u30c9\u3068\u3057\u3066\u4fdd\u6301\u3059\u308b\u3002
SECOND_OPTION_ROW_PREFIX = "#second_option"

SHIFT_OPTION_MAPPINGS = {
    "A": "\u5348\u524d",
    "P": "\u5348\u5f8c",
    "E": "\u65e9\u756a",
    "L": "\u9045\u756a",
    "TEMP": "\u81e8\u6642\u4fbf",
    "M": "\u30de\u30a4\u30af\u30ed",
    "C": "\u4e2d\u578b",
    "O": "\u5927\u578b",
    "W": "\u30ef\u30b4\u30f3",
    "V": "\u5f79\u54e1\u8eca\u4e21",
    "N1": "1\u53f7\u8eca",
    "N2": "2\u53f7\u8eca",
    "N3": "3\u53f7\u8eca",
    "N4": "4\u53f7\u8eca",
    "N5": "5\u53f7\u8eca",
}

# \u7b2c\u4e8c\u30aa\u30d7\u30b7\u30e7\u30f3\uff08\u4ee3\u52d9 SUB\uff0f\u7814\u4fee TRAIN\uff09\u3002
# \u901a\u5e38\u306e\u30b7\u30d5\u30c8\u30aa\u30d7\u30b7\u30e7\u30f3\u3068\u306f\u5225\u8ef8\u306e\u300c\u7b2c\u4e8c\u30aa\u30d7\u30b7\u30e7\u30f3\u300d\u3068\u3057\u3066 entry \u306b\u4ed8\u4e0e\u3059\u308b\u3002
# \u30a2\u30b7\u30b9\u30c8\u306e\u7d4c\u9a13\u6e08\u307f\u73fe\u5834\uff0f\u7814\u4fee\u8981\u73fe\u5834\u30fb\u81ea\u52d5\u4f5c\u6210\u30a8\u30f3\u30b8\u30f3\u30fb\u8868\u793a\u306b\u306e\u307f\u7528\u3044\u3001
# \u91cd\u8907\u30c1\u30a7\u30c3\u30af\uff08is_duplicate_by_rules\uff09\u306b\u306f\u4e00\u5207\u5f71\u97ff\u3055\u305b\u306a\u3044\u3002
SECOND_OPTION_MAPPINGS = {
    "SUB": "\u4ee3\u52d9",
    "TRAIN": "\u7814\u4fee",
}
SECOND_OPTION_KEYS = set(SECOND_OPTION_MAPPINGS.keys())

# \u65e7\u540d\uff08\u5f79\u5272\u30aa\u30d7\u30b7\u30e7\u30f3\uff09\u306e\u4e92\u63db\u30a8\u30a4\u30ea\u30a2\u30b9\u3002\u7b2c\u4e8c\u30aa\u30d7\u30b7\u30e7\u30f3\u3068\u540c\u4e00\u3002
ROLE_OPTION_MAPPINGS = SECOND_OPTION_MAPPINGS

LEAVE_OPTION_MAPPINGS = {
    "PAID": "\u6709\u4f11",
    "COMP": "\u4ee3\u4f11",
    "PUBLIC": "\u516c\u4f11",
    "CONDOLENCE": "\u6176\u5f14\u4f11\u6687",
    "CARE": "\u4ecb\u8b77\u4f11\u6687",
    "REFRESH": "\u30ea\u30d5\u30ec\u30c3\u30b7\u30e5\u4f11\u6687",
    "OTHER": "\u305d\u306e\u4ed6",
}

OPTION_MAPPINGS = {**SHIFT_OPTION_MAPPINGS, **LEAVE_OPTION_MAPPINGS, **SECOND_OPTION_MAPPINGS}

ENTRY_VALUE_PATTERN = re.compile(r"^!([^!]+)!(.+)$")


def normalize_second_option(value: Any) -> str:
    """第二オプション値を正規化する（SUB/TRAIN のみ有効、それ以外は ""）。"""
    key = str(value or "").strip().upper()
    return key if key in SECOND_OPTION_KEYS else ""


def _split_second_option(value: str, existing_second: Any) -> tuple[str, str]:
    """entry の value と第二オプションを分離する。

    新形式: value はシフトオプション、second_option は別フィールド。
    旧形式（互換）: value が `!SUB!名前` / `!TRAIN!名前` の場合は第二オプションへ移し、
    value は名前だけ（または素のシフト）に書き換える。
    戻り値は (正規化後 value, 第二オプション)。
    """
    second = normalize_second_option(existing_second)
    option_key, name = parse_entry_value(value)
    if not second and option_key and str(option_key).upper() in SECOND_OPTION_KEYS:
        return name, str(option_key).upper()
    return value, second


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


def _normalize_site_branch_row_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.isdigit():
        return ""
    return text if int(text) > 0 else ""


def _normalize_site_row_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.isdigit():
        return ""
    return text if int(text) > 0 else ""


def _normalize_sync_text(value: Any, *, limit: int = 120) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def normalize_entry(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        value = str(raw.get("value", "")).strip()
        if not value:
            return {}
        value, second_option = _split_second_option(
            value, raw.get("second_option", raw.get("secondOption", ""))
        )
        employee_name = str(raw.get("employee_name", raw.get("employeeName", "")) or "").strip()
        employee_number = str(raw.get("employee_number", raw.get("employeeNumber", "")) or "").strip()
        site_branch_row_id = _normalize_site_branch_row_id(
            raw.get("site_branch_row_id", raw.get("siteBranchRowId", ""))
        )
        site_row_id = _normalize_site_row_id(raw.get("site_row_id", raw.get("siteRowId", "")))
        site_id = str(raw.get("site_id", raw.get("siteId", "")) or "").strip()
        site_name = str(raw.get("site_name", raw.get("siteName", "")) or "").strip()
        site_branch = str(raw.get("site_branch", raw.get("siteBranch", "")) or "").strip()
        substitute_request_type = str(
            raw.get("substitute_request_type", raw.get("substituteRequestType", ""))
            or ""
        ).strip().lower()
        if substitute_request_type not in {"scene", "person"}:
            substitute_request_type = ""
        return {
            "id": str(raw.get("id") or generate_entry_id()),
            "value": value,
            "second_option": second_option,
            "comment": str(raw.get("comment", "") or "").strip(),
            "employee_name": employee_name,
            "employee_number": employee_number,
            "site_row_id": site_row_id,
            "site_id": site_id,
            "site_name": site_name,
            "site_branch_row_id": site_branch_row_id,
            "site_branch": site_branch,
            "sync_source_type": _normalize_sync_text(raw.get("sync_source_type", raw.get("syncSourceType", "")), limit=40),
            "sync_source_project_id": _normalize_sync_text(raw.get("sync_source_project_id", raw.get("syncSourceProjectId", "")), limit=80),
            "sync_source_project_title": _normalize_sync_text(raw.get("sync_source_project_title", raw.get("syncSourceProjectTitle", "")), limit=120),
            "sync_source_month_key": _normalize_sync_text(raw.get("sync_source_month_key", raw.get("syncSourceMonthKey", "")), limit=20),
            "sync_source_day": _normalize_sync_text(raw.get("sync_source_day", raw.get("syncSourceDay", "")), limit=10),
            "sync_source_entry_id": _normalize_sync_text(raw.get("sync_source_entry_id", raw.get("syncSourceEntryId", "")), limit=80),
            "substitute_request_type": substitute_request_type,
            "substitute_helper_employee_name": _normalize_sync_text(
                raw.get("substitute_helper_employee_name", raw.get("substituteHelperEmployeeName", "")),
                limit=120,
            ),
            "substitute_helper_employee_number": _normalize_sync_text(
                raw.get("substitute_helper_employee_number", raw.get("substituteHelperEmployeeNumber", "")),
                limit=40,
            ),
            "substitute_helper_site_row_id": _normalize_site_row_id(
                raw.get("substitute_helper_site_row_id", raw.get("substituteHelperSiteRowId", ""))
            ),
            "substitute_helper_site_id": _normalize_sync_text(
                raw.get("substitute_helper_site_id", raw.get("substituteHelperSiteId", "")),
                limit=40,
            ),
            "substitute_helper_site_name": _normalize_sync_text(
                raw.get("substitute_helper_site_name", raw.get("substituteHelperSiteName", "")),
                limit=200,
            ),
            "substitute_resolved": _normalize_bool(raw.get("substitute_resolved", raw.get("substituteResolved", False))),
            "substitute_requester_user_id": _normalize_sync_text(raw.get("substitute_requester_user_id", raw.get("substituteRequesterUserId", "")), limit=80),
            "substitute_requester_name": _normalize_sync_text(raw.get("substitute_requester_name", raw.get("substituteRequesterName", "")), limit=120),
            "substitute_requested_at": _normalize_sync_text(raw.get("substitute_requested_at", raw.get("substituteRequestedAt", "")), limit=40),
            "substitute_helper_user_id": _normalize_sync_text(raw.get("substitute_helper_user_id", raw.get("substituteHelperUserId", "")), limit=80),
            "substitute_helper_name": _normalize_sync_text(raw.get("substitute_helper_name", raw.get("substituteHelperName", "")), limit=120),
            "substitute_helped_at": _normalize_sync_text(raw.get("substitute_helped_at", raw.get("substituteHelpedAt", "")), limit=40),
            "substitute_unassigned_helper": _normalize_bool(raw.get("substitute_unassigned_helper", raw.get("substituteUnassignedHelper", False))),
            "substitute_source_project_id": _normalize_sync_text(raw.get("substitute_source_project_id", raw.get("substituteSourceProjectId", "")), limit=80),
            "substitute_source_project_title": _normalize_sync_text(raw.get("substitute_source_project_title", raw.get("substituteSourceProjectTitle", "")), limit=120),
            "substitute_source_project_mode": _normalize_sync_text(raw.get("substitute_source_project_mode", raw.get("substituteSourceProjectMode", "")), limit=40),
            "substitute_source_month_key": _normalize_sync_text(raw.get("substitute_source_month_key", raw.get("substituteSourceMonthKey", "")), limit=20),
            "substitute_source_day": _normalize_sync_text(raw.get("substitute_source_day", raw.get("substituteSourceDay", "")), limit=10),
            "substitute_source_entry_id": _normalize_sync_text(raw.get("substitute_source_entry_id", raw.get("substituteSourceEntryId", "")), limit=80),
        }

    value = str(raw or "").strip()
    if not value:
        return {}
    value, second_option = _split_second_option(value, "")
    return {
        "id": generate_entry_id(),
        "value": value,
        "second_option": second_option,
        "comment": "",
        "employee_name": "",
        "employee_number": "",
        "site_row_id": "",
        "site_id": "",
        "site_name": "",
        "site_branch_row_id": "",
        "site_branch": "",
        "sync_source_type": "",
        "sync_source_project_id": "",
        "sync_source_project_title": "",
        "sync_source_month_key": "",
        "sync_source_day": "",
        "sync_source_entry_id": "",
        "substitute_request_type": "",
        "substitute_helper_employee_name": "",
        "substitute_helper_employee_number": "",
        "substitute_helper_site_row_id": "",
        "substitute_helper_site_id": "",
        "substitute_helper_site_name": "",
        "substitute_resolved": False,
        "substitute_requester_user_id": "",
        "substitute_requester_name": "",
        "substitute_requested_at": "",
        "substitute_helper_user_id": "",
        "substitute_helper_name": "",
        "substitute_helped_at": "",
        "substitute_unassigned_helper": False,
        "substitute_source_project_id": "",
        "substitute_source_project_title": "",
        "substitute_source_project_mode": "",
        "substitute_source_month_key": "",
        "substitute_source_day": "",
        "substitute_source_entry_id": "",
    }


def empty_entries_for_month(year: int, month: int) -> dict[str, list[dict[str, Any]]]:
    year, month = validate_year_month(year, month)
    return {str(day): [] for day in range(1, monthrange(year, month)[1] + 1)}


def normalize_entries_for_month(entries: Any, year: int, month: int) -> dict[str, list[dict[str, Any]]]:
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

        day_entries: list[dict[str, Any]] = []
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
    entries_per_day: dict[str, list[dict[str, Any]]],
    project_employee_number: str = "",
) -> list[list[Any]]:
    year, month = validate_year_month(year, month)
    header = [mode, year, month, title]
    if required_capacity > 0:
        header.append(required_capacity)

    rows: list[list[Any]] = [
        header,
        ["日付", "出勤者" if mode in {"scene", "master"} else "現場"],
    ]

    comment_rows: list[list[Any]] = []
    second_option_rows: list[list[Any]] = []
    employee_name_rows: list[list[Any]] = []
    employee_number_rows: list[list[Any]] = []
    site_row_id_rows: list[list[Any]] = []
    site_id_rows: list[list[Any]] = []
    site_name_rows: list[list[Any]] = []
    site_branch_row_id_rows: list[list[Any]] = []
    site_branch_rows: list[list[Any]] = []
    substitute_rows: list[list[Any]] = []
    for day in range(1, monthrange(year, month)[1] + 1):
        key = str(day)
        # 空 value の entry は value 行・メタデータ行の双方から同じ基準で除外し、
        # 列位置とメタデータ index（#second_option 等）を常に一致させる（添字ズレ防止）。
        day_entries = [
            entry for entry in (normalize_entry(item) for item in entries_per_day.get(key, []))
            if entry
        ]
        if day_entries:
            rows.append([day, *[entry["value"] for entry in day_entries]])
        for index, entry in enumerate(day_entries):
            if entry.get("comment"):
                comment_rows.append([COMMENT_ROW_PREFIX, day, index, entry["comment"]])
            if entry.get("second_option"):
                second_option_rows.append(
                    [SECOND_OPTION_ROW_PREFIX, day, index, entry["second_option"]]
                )
            if entry.get("employee_name"):
                employee_name_rows.append(
                    [EMPLOYEE_NAME_ROW_PREFIX, day, index, entry["employee_name"]]
                )
            if entry.get("employee_number"):
                employee_number_rows.append(
                    [EMPLOYEE_NUMBER_ROW_PREFIX, day, index, entry["employee_number"]]
                )
            if entry.get("site_row_id"):
                site_row_id_rows.append(
                    [SITE_ROW_ID_ROW_PREFIX, day, index, entry["site_row_id"]]
                )
            if entry.get("site_id"):
                site_id_rows.append(
                    [SITE_ID_ROW_PREFIX, day, index, entry["site_id"]]
                )
            if entry.get("site_name"):
                site_name_rows.append(
                    [SITE_NAME_ROW_PREFIX, day, index, entry["site_name"]]
                )
            if entry.get("site_branch_row_id"):
                site_branch_row_id_rows.append(
                    [SITE_BRANCH_ROW_ID_ROW_PREFIX, day, index, entry["site_branch_row_id"]]
                )
            if entry.get("site_branch"):
                site_branch_rows.append([SITE_BRANCH_ROW_PREFIX, day, index, entry["site_branch"]])
            if entry.get("substitute_request_type"):
                substitute_rows.append([SUBSTITUTE_REQUEST_TYPE_ROW_PREFIX, day, index, entry["substitute_request_type"]])
            if entry.get("substitute_helper_employee_name"):
                substitute_rows.append([SUBSTITUTE_HELPER_EMPLOYEE_NAME_ROW_PREFIX, day, index, entry["substitute_helper_employee_name"]])
            if entry.get("substitute_helper_employee_number"):
                substitute_rows.append([SUBSTITUTE_HELPER_EMPLOYEE_NUMBER_ROW_PREFIX, day, index, entry["substitute_helper_employee_number"]])
            if entry.get("substitute_helper_site_row_id"):
                substitute_rows.append([SUBSTITUTE_HELPER_SITE_ROW_ID_ROW_PREFIX, day, index, entry["substitute_helper_site_row_id"]])
            if entry.get("substitute_helper_site_id"):
                substitute_rows.append([SUBSTITUTE_HELPER_SITE_ID_ROW_PREFIX, day, index, entry["substitute_helper_site_id"]])
            if entry.get("substitute_helper_site_name"):
                substitute_rows.append([SUBSTITUTE_HELPER_SITE_NAME_ROW_PREFIX, day, index, entry["substitute_helper_site_name"]])
            if entry.get("substitute_resolved"):
                substitute_rows.append([SUBSTITUTE_RESOLVED_ROW_PREFIX, day, index, "1"])
            if entry.get("substitute_requester_user_id"):
                substitute_rows.append([SUBSTITUTE_REQUESTER_USER_ID_ROW_PREFIX, day, index, entry["substitute_requester_user_id"]])
            if entry.get("substitute_requester_name"):
                substitute_rows.append([SUBSTITUTE_REQUESTER_NAME_ROW_PREFIX, day, index, entry["substitute_requester_name"]])
            if entry.get("substitute_requested_at"):
                substitute_rows.append([SUBSTITUTE_REQUESTED_AT_ROW_PREFIX, day, index, entry["substitute_requested_at"]])
            if entry.get("substitute_helper_user_id"):
                substitute_rows.append([SUBSTITUTE_HELPER_USER_ID_ROW_PREFIX, day, index, entry["substitute_helper_user_id"]])
            if entry.get("substitute_helper_name"):
                substitute_rows.append([SUBSTITUTE_HELPER_NAME_ROW_PREFIX, day, index, entry["substitute_helper_name"]])
            if entry.get("substitute_helped_at"):
                substitute_rows.append([SUBSTITUTE_HELPED_AT_ROW_PREFIX, day, index, entry["substitute_helped_at"]])
            if entry.get("substitute_unassigned_helper"):
                substitute_rows.append([SUBSTITUTE_UNASSIGNED_HELPER_ROW_PREFIX, day, index, "1"])
            if entry.get("substitute_source_project_id"):
                substitute_rows.append([SUBSTITUTE_SOURCE_PROJECT_ID_ROW_PREFIX, day, index, entry["substitute_source_project_id"]])
            if entry.get("substitute_source_project_title"):
                substitute_rows.append([SUBSTITUTE_SOURCE_PROJECT_TITLE_ROW_PREFIX, day, index, entry["substitute_source_project_title"]])
            if entry.get("substitute_source_project_mode"):
                substitute_rows.append([SUBSTITUTE_SOURCE_PROJECT_MODE_ROW_PREFIX, day, index, entry["substitute_source_project_mode"]])
            if entry.get("substitute_source_month_key"):
                substitute_rows.append([SUBSTITUTE_SOURCE_MONTH_KEY_ROW_PREFIX, day, index, entry["substitute_source_month_key"]])
            if entry.get("substitute_source_day"):
                substitute_rows.append([SUBSTITUTE_SOURCE_DAY_ROW_PREFIX, day, index, entry["substitute_source_day"]])
            if entry.get("substitute_source_entry_id"):
                substitute_rows.append([SUBSTITUTE_SOURCE_ENTRY_ID_ROW_PREFIX, day, index, entry["substitute_source_entry_id"]])

    metadata_rows: list[list[Any]] = []
    if str(project_employee_number or "").strip():
        metadata_rows.append(
            [PROJECT_EMPLOYEE_NUMBER_ROW_PREFIX, str(project_employee_number).strip()]
        )

    return (
        rows
        + comment_rows
        + second_option_rows
        + employee_name_rows
        + employee_number_rows
        + site_row_id_rows
        + site_id_rows
        + site_name_rows
        + site_branch_row_id_rows
        + site_branch_rows
        + substitute_rows
        + metadata_rows
    )


def serialize_csv_text(
    mode: str,
    year: int,
    month: int,
    title: str,
    required_capacity: int,
    entries_per_day: dict[str, list[dict[str, Any]]],
    project_employee_number: str = "",
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(
        serialize_entry_rows(
            mode,
            year,
            month,
            title,
            required_capacity,
            entries_per_day,
            project_employee_number,
        )
    )
    return buffer.getvalue()


def parse_csv_text(text: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 4:
        raise ValueError("ShifterSync CSV のヘッダーが不正です")

    mode = str(rows[0][0]).strip().lower()
    if mode not in {"scene", "person", "master", "substitute"}:
        raise ValueError("mode は scene、person、master、substitute だけ対応しています")

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
    second_option_rows: list[list[str]] = []
    employee_name_rows: list[list[str]] = []
    employee_number_rows: list[list[str]] = []
    site_row_id_rows: list[list[str]] = []
    site_id_rows: list[list[str]] = []
    site_name_rows: list[list[str]] = []
    site_branch_row_id_rows: list[list[str]] = []
    site_branch_rows: list[list[str]] = []
    substitute_rows: list[list[str]] = []
    project_employee_number = ""
    substitute_row_prefixes = {
        SUBSTITUTE_REQUEST_TYPE_ROW_PREFIX,
        SUBSTITUTE_HELPER_EMPLOYEE_NAME_ROW_PREFIX,
        SUBSTITUTE_HELPER_EMPLOYEE_NUMBER_ROW_PREFIX,
        SUBSTITUTE_HELPER_SITE_ROW_ID_ROW_PREFIX,
        SUBSTITUTE_HELPER_SITE_ID_ROW_PREFIX,
        SUBSTITUTE_HELPER_SITE_NAME_ROW_PREFIX,
        SUBSTITUTE_RESOLVED_ROW_PREFIX,
        SUBSTITUTE_REQUESTER_USER_ID_ROW_PREFIX,
        SUBSTITUTE_REQUESTER_NAME_ROW_PREFIX,
        SUBSTITUTE_REQUESTED_AT_ROW_PREFIX,
        SUBSTITUTE_HELPER_USER_ID_ROW_PREFIX,
        SUBSTITUTE_HELPER_NAME_ROW_PREFIX,
        SUBSTITUTE_HELPED_AT_ROW_PREFIX,
        SUBSTITUTE_UNASSIGNED_HELPER_ROW_PREFIX,
        SUBSTITUTE_SOURCE_PROJECT_ID_ROW_PREFIX,
        SUBSTITUTE_SOURCE_PROJECT_TITLE_ROW_PREFIX,
        SUBSTITUTE_SOURCE_PROJECT_MODE_ROW_PREFIX,
        SUBSTITUTE_SOURCE_MONTH_KEY_ROW_PREFIX,
        SUBSTITUTE_SOURCE_DAY_ROW_PREFIX,
        SUBSTITUTE_SOURCE_ENTRY_ID_ROW_PREFIX,
    }
    for row in rows[2:]:
        if not row:
            continue
        head = str(row[0]).strip()
        if head.isdigit():
            day_key = str(int(head))
            if day_key not in entries_per_day:
                continue
            entries_per_day[day_key] = [
                normalize_entry(
                    {
                        "value": cell,
                        "comment": "",
                        "employee_name": "",
                        "employee_number": "",
                        "site_branch_row_id": "",
                        "site_branch": "",
                    }
                )
                for cell in row[1:]
                if str(cell).strip()
            ]
            continue
        if head == COMMENT_ROW_PREFIX:
            comment_rows.append(row)
            continue
        if head == SECOND_OPTION_ROW_PREFIX:
            second_option_rows.append(row)
            continue
        if head == EMPLOYEE_NAME_ROW_PREFIX:
            employee_name_rows.append(row)
            continue
        if head == EMPLOYEE_NUMBER_ROW_PREFIX:
            employee_number_rows.append(row)
            continue
        if head == SITE_ROW_ID_ROW_PREFIX:
            site_row_id_rows.append(row)
            continue
        if head == SITE_ID_ROW_PREFIX:
            site_id_rows.append(row)
            continue
        if head == SITE_NAME_ROW_PREFIX:
            site_name_rows.append(row)
            continue
        if head == SITE_BRANCH_ROW_ID_ROW_PREFIX:
            site_branch_row_id_rows.append(row)
            continue
        if head == SITE_BRANCH_ROW_PREFIX:
            site_branch_rows.append(row)
            continue
        if head in substitute_row_prefixes:
            substitute_rows.append(row)
            continue
        if head == PROJECT_EMPLOYEE_NUMBER_ROW_PREFIX and len(row) >= 2:
            project_employee_number = str(row[1] or "").strip()

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

    for row in second_option_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["second_option"] = normalize_second_option(row[3])

    for row in employee_number_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["employee_number"] = str(row[3] or "").strip()

    for row in employee_name_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["employee_name"] = str(row[3] or "").strip()

    for row in site_row_id_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["site_row_id"] = _normalize_site_row_id(row[3])

    for row in site_id_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["site_id"] = str(row[3] or "").strip()

    for row in site_name_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["site_name"] = str(row[3] or "").strip()

    for row in site_branch_row_id_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["site_branch_row_id"] = _normalize_site_branch_row_id(row[3])

    for row in site_branch_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        entries_per_day[day_key][index]["site_branch"] = str(row[3] or "").strip()

    substitute_field_map = {
        SUBSTITUTE_REQUEST_TYPE_ROW_PREFIX: ("substitute_request_type", lambda value: value if value in {"scene", "person"} else ""),
        SUBSTITUTE_HELPER_EMPLOYEE_NAME_ROW_PREFIX: ("substitute_helper_employee_name", lambda value: _normalize_sync_text(value, limit=120)),
        SUBSTITUTE_HELPER_EMPLOYEE_NUMBER_ROW_PREFIX: ("substitute_helper_employee_number", lambda value: _normalize_sync_text(value, limit=40)),
        SUBSTITUTE_HELPER_SITE_ROW_ID_ROW_PREFIX: ("substitute_helper_site_row_id", _normalize_site_row_id),
        SUBSTITUTE_HELPER_SITE_ID_ROW_PREFIX: ("substitute_helper_site_id", lambda value: _normalize_sync_text(value, limit=40)),
        SUBSTITUTE_HELPER_SITE_NAME_ROW_PREFIX: ("substitute_helper_site_name", lambda value: _normalize_sync_text(value, limit=200)),
        SUBSTITUTE_RESOLVED_ROW_PREFIX: ("substitute_resolved", _normalize_bool),
        SUBSTITUTE_REQUESTER_USER_ID_ROW_PREFIX: ("substitute_requester_user_id", lambda value: _normalize_sync_text(value, limit=80)),
        SUBSTITUTE_REQUESTER_NAME_ROW_PREFIX: ("substitute_requester_name", lambda value: _normalize_sync_text(value, limit=120)),
        SUBSTITUTE_REQUESTED_AT_ROW_PREFIX: ("substitute_requested_at", lambda value: _normalize_sync_text(value, limit=40)),
        SUBSTITUTE_HELPER_USER_ID_ROW_PREFIX: ("substitute_helper_user_id", lambda value: _normalize_sync_text(value, limit=80)),
        SUBSTITUTE_HELPER_NAME_ROW_PREFIX: ("substitute_helper_name", lambda value: _normalize_sync_text(value, limit=120)),
        SUBSTITUTE_HELPED_AT_ROW_PREFIX: ("substitute_helped_at", lambda value: _normalize_sync_text(value, limit=40)),
        SUBSTITUTE_UNASSIGNED_HELPER_ROW_PREFIX: ("substitute_unassigned_helper", _normalize_bool),
        SUBSTITUTE_SOURCE_PROJECT_ID_ROW_PREFIX: ("substitute_source_project_id", lambda value: _normalize_sync_text(value, limit=80)),
        SUBSTITUTE_SOURCE_PROJECT_TITLE_ROW_PREFIX: ("substitute_source_project_title", lambda value: _normalize_sync_text(value, limit=120)),
        SUBSTITUTE_SOURCE_PROJECT_MODE_ROW_PREFIX: ("substitute_source_project_mode", lambda value: _normalize_sync_text(value, limit=40)),
        SUBSTITUTE_SOURCE_MONTH_KEY_ROW_PREFIX: ("substitute_source_month_key", lambda value: _normalize_sync_text(value, limit=20)),
        SUBSTITUTE_SOURCE_DAY_ROW_PREFIX: ("substitute_source_day", lambda value: _normalize_sync_text(value, limit=10)),
        SUBSTITUTE_SOURCE_ENTRY_ID_ROW_PREFIX: ("substitute_source_entry_id", lambda value: _normalize_sync_text(value, limit=80)),
    }
    for row in substitute_rows:
        if len(row) < 4:
            continue
        try:
            day_key = str(int(row[1]))
            index = int(row[2])
        except (TypeError, ValueError):
            continue
        if day_key not in entries_per_day or index < 0 or index >= len(entries_per_day[day_key]):
            continue
        field = substitute_field_map.get(str(row[0]).strip())
        if not field:
            continue
        key, normalizer = field
        raw_value = str(row[3] or "").strip()
        if key == "substitute_request_type":
            raw_value = raw_value.lower()
        entries_per_day[day_key][index][key] = normalizer(raw_value)

    return {
        "mode": mode,
        "year": year,
        "month": month,
        "title": title,
        "employee_number": project_employee_number,
        "capacity_enabled": required_capacity > 0,
        "required_capacity": required_capacity,
        "entries_per_day": entries_per_day,
    }


def entry_display_text(entry: Any, *, include_comment: bool = False, comment_limit: int | None = None) -> str:
    normalized = normalize_entry(entry)
    if not normalized:
        return ""
    option_key, name = parse_entry_value(normalized["value"])
    head = f"{name} {OPTION_MAPPINGS.get(option_key, option_key)}" if option_key else name
    second_option = normalize_second_option(normalized.get("second_option"))
    if second_option:
        head = f"{head}［{SECOND_OPTION_MAPPINGS.get(second_option, second_option)}］"
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


def entry_second_option(entry: Any) -> str:
    """entry の第二オプション（SUB/TRAIN）を返す。

    新形式の second_option フィールドを優先し、無ければ旧形式の値（`!SUB!名前`）
    からも導出する（保存前の生データでも判定できるようにする）。
    """
    if isinstance(entry, dict):
        direct = normalize_second_option(entry.get("second_option", entry.get("secondOption", "")))
        if direct:
            return direct
        option_key, _name = parse_entry_value(str(entry.get("value") or ""))
    else:
        option_key, _name = parse_entry_value(str(entry or ""))
    return normalize_second_option(option_key)
