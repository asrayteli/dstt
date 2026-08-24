from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from typing import Any

try:
    from .shiftersync_format import (
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        ROLE_OPTION_MAPPINGS,
        SECOND_OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        entry_options,
        entry_second_option,
    )
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        ROLE_OPTION_MAPPINGS,
        SECOND_OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        entry_options,
        entry_second_option,
    )


TEMPORARY_OPTION = "TEMP"
TIME_CONFLICT_RULES = {
    ("A", "P"): False,
    ("A", "E"): True,
    ("A", "L"): False,
    ("P", "E"): False,
    ("P", "L"): True,
    ("E", "L"): False,
}
# 既存の対応表は回帰テスト用に残す。実装は午前系(A/E)・午後系(P/L)の
# グループ判定で同じ結果を表現する。
LEAVE_OPTION_KEYS = set(LEAVE_OPTION_MAPPINGS.keys())
# 代務・研修は「第二オプション」。entry の値ではなく別フィールドで保持し、
# アシスト／自動作成／表示にのみ用いる。重複チェックには影響させないため、
# ここでは ROLE_OPTION_KEYS を判定に使わない（互換のため定義のみ残す）。
ROLE_OPTION_KEYS = set(ROLE_OPTION_MAPPINGS.keys())
SECOND_OPTION_KEYS = ROLE_OPTION_KEYS

# 元帳側が重複チェック対象になる同期種別。元帳が選択済みなら鏡像を除外し、
# 元帳が対象外なら鏡像を実配置の代理として1件だけ残す。
LEDGER_BACKED_SYNC_TYPES = {"scene_shift", "person_shift", "large_shift"}
MIRROR_ONLY_SYNC_TYPES = {"master_shift", "substitute_shift"}
NON_PLACEMENT_SYNC_TYPES = {"substitute_request"}


TIME_OPTION_GROUPS = ({"A", "E"}, {"P", "L"})


def _axes(value: Any) -> dict[str, str | None]:
    if isinstance(value, dict) and isinstance(value.get("options"), dict):
        return _axes(value["options"])
    if isinstance(value, dict) and any(
        key in value for key in ("time", "vehicle", "car", "leave", "second")
    ):
        return {
            "time": str(value.get("time") or "").strip().upper() or None,
            "vehicle": str(value.get("vehicle") or "").strip().upper() or None,
            "car": str(value.get("car") or "").strip().upper() or None,
            "leave": str(value.get("leave") or "").strip().upper() or None,
            "second": str(value.get("second") or "").strip().upper() or None,
        }
    if isinstance(value, dict) and "option" in value:
        option = str(value.get("option") or "").strip().upper()
        result = entry_options(f"!{option}!x" if option else "x")
        if value.get("is_leave") and not result["leave"]:
            result["leave"] = option or "OTHER"
        return result
    return entry_options(value)


def time_options_conflict(
    time1: str | None, time2: str | None, *, same_site: bool = False
) -> bool:
    """Compare time symbols; a missing time is an all-day assignment."""
    left = str(time1 or "").strip().upper() or None
    right = str(time2 or "").strip().upper() or None
    if left is None or right is None:
        return True
    if left == TEMPORARY_OPTION or right == TEMPORARY_OPTION:
        return same_site and left == right == TEMPORARY_OPTION
    return any(left in group and right in group for group in TIME_OPTION_GROUPS)


def person_conflicts(a: dict[str, Any], b: dict[str, Any], *, same_site: bool = False) -> bool:
    """Person duplication: leave is excluded and only the time axis matters."""
    left, right = _axes(a), _axes(b)
    if left["leave"] or right["leave"]:
        return False
    return time_options_conflict(left["time"], right["time"], same_site=same_site)


def leave_work_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return whether exactly one placement is leave and the other is work."""
    left, right = _axes(a), _axes(b)
    return bool(left["leave"]) != bool(right["leave"])


def vehicle_conflicts(a: dict[str, Any], b: dict[str, Any], *, same_site: bool) -> bool:
    """Same-site car duplication, gated by overlapping time symbols."""
    if not same_site:
        return False
    left, right = _axes(a), _axes(b)
    if left["leave"] or right["leave"] or not left["car"] or left["car"] != right["car"]:
        return False
    return time_options_conflict(left["time"], right["time"], same_site=True)


def _different_people(a: dict[str, Any], b: dict[str, Any]) -> bool:
    left_number = str(a.get("employee_number") or "").strip()
    right_number = str(b.get("employee_number") or "").strip()
    if left_number and right_number:
        return left_number != right_number
    left_name = str(a.get("comparison") or "").strip()
    right_name = str(b.get("comparison") or "").strip()
    return bool(left_name and right_name and left_name != right_name)


def _same_person(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Prefer employee numbers, falling back to names when either side lacks one."""
    left_number = str(a.get("employee_number") or "").strip().casefold()
    right_number = str(b.get("employee_number") or "").strip().casefold()
    if left_number and right_number:
        return left_number == right_number
    left_name = str(a.get("comparison") or "").strip().casefold()
    right_name = str(b.get("comparison") or "").strip().casefold()
    return bool(left_name and right_name and left_name == right_name)


def _entry_origin(
    entry: dict[str, Any],
    *,
    day: int,
    project_id: str,
    selected_project_ids: set[str],
) -> tuple[str | None, bool]:
    """Return (canonical origin key, should_skip) for conflict normalization."""
    sync_type = str(entry.get("sync_source_type") or "").strip()
    if sync_type in NON_PLACEMENT_SYNC_TYPES:
        return None, True
    if sync_type:
        source_project_id = str(entry.get("sync_source_project_id") or "").strip()
        source_entry_id = str(entry.get("sync_source_entry_id") or entry.get("id") or "").strip()
        if sync_type in LEDGER_BACKED_SYNC_TYPES:
            # Old/incomplete mirrors cannot be tied back to one canonical source,
            # so retaining them risks counting both the source and its mirror.
            if not source_project_id or source_project_id in selected_project_ids:
                return None, True
        if sync_type not in LEDGER_BACKED_SYNC_TYPES | MIRROR_ONLY_SYNC_TYPES:
            return None, True
        source_month = str(entry.get("sync_source_month_key") or "").strip()
        source_day = str(entry.get("sync_source_day") or day).strip()
        origin = (
            f"{source_project_id}:{source_month}:{source_day}:{source_entry_id}"
            if source_project_id and source_entry_id
            else None
        )
        return origin, False
    entry_id = str(entry.get("id") or "").strip()
    return (f"{project_id}:{day}:{entry_id}" if project_id and entry_id else None), False


def compare_shift_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        raise ValueError("比較するシフトデータを選択してください")
    # 大規模シフトは1帳が人数分の擬似現場列へ展開されるため、上限に余裕を持たせる
    # （最大60人規模＋他現場でも収まるよう120件まで許容）。
    if len(payloads) > 120:
        raise ValueError("比較するシフトデータは120件までにしてください")

    mode = None
    year = None
    month = None
    targets: list[str] = []
    capacities: list[int | None] = []
    sources: list[dict[str, Any]] = []
    shift_data = defaultdict(lambda: [[] for _ in range(len(payloads))])
    selected_project_ids = {
        str(payload.get("project_id") or "").strip()
        for payload in payloads
        if str(payload.get("project_id") or "").strip()
    }
    seen_origins: set[str] = set()

    for payload_index, payload in enumerate(payloads):
        source_label = str(payload.get("label") or payload.get("title") or f"source_{payload_index + 1}").strip()
        source_mode = str(payload.get("mode") or "").strip().lower()
        source_year = int(payload.get("year"))
        source_month = int(payload.get("month"))
        source_entries = payload.get("entries_per_day") or {}
        source_capacity = payload.get("required_capacity") or None
        source_employee_number = str(payload.get("employee_number") or "").strip()
        source_project_id = str(payload.get("project_id") or "").strip()

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
                origin, should_skip = _entry_origin(
                    entry,
                    day=day,
                    project_id=source_project_id,
                    selected_project_ids=selected_project_ids,
                )
                if should_skip or (origin and origin in seen_origins):
                    continue
                if origin:
                    seen_origins.add(origin)
                option_key, name, comment = entry_option_and_name(entry)
                options = entry_options(entry)
                if str(entry.get("sync_source_type") or "").strip() == "large_shift":
                    # Large work codes are arbitrary strings.  A code named "A",
                    # for example, must not be inferred as the morning symbol.
                    options = {**options, "time": None, "vehicle": None, "car": None}
                second_option = entry_second_option(entry)
                comparison = (
                    source_label.casefold()
                    if source_mode == "person"
                    else entry_name_for_comparison(entry)
                )
                employee_number = source_employee_number or str(entry.get("employee_number") or "").strip()
                normalized_entries.append(
                    {
                        "original": entry["value"],
                        "display": entry_display_text(entry),
                        "comparison": comparison,
                        "employee_number": employee_number,
                        "person_key": (
                            f"number:{employee_number.casefold()}"
                            if employee_number
                            else (f"name:{comparison}" if comparison else "")
                        ),
                        "option": option_key,
                        "options": options,
                        "second_option": second_option,
                        "second_option_label": SECOND_OPTION_MAPPINGS.get(second_option, ""),
                        "name": name,
                        "comment": comment,
                    }
                )
            shift_data[day][payload_index].extend(normalized_entries)

    if mode is None or year is None or month is None:
        raise ValueError("比較するシフトデータがありません")

    conflicts = []
    same_site_conflicts = []
    leave_work_conflicts = []
    vehicle_duplicate_conflicts = []

    for day, per_source_entries in shift_data.items():
        for source_index, entries in enumerate(per_source_entries):
            for left_index, left in enumerate(entries):
                for right in entries[left_index + 1 :]:
                    if not _same_person(left, right):
                        continue
                    if person_conflicts(left["options"], right["options"], same_site=True):
                        same_site_conflicts.append(
                            {"date": day, "entry": left["original"], "file_index": source_index}
                        )
                        same_site_conflicts.append(
                            {"date": day, "entry": right["original"], "file_index": source_index}
                        )
                    elif leave_work_conflict(left["options"], right["options"]):
                        leave_work_conflicts.extend(
                            [
                                {"date": day, "entry": left["original"], "file_index": source_index},
                                {"date": day, "entry": right["original"], "file_index": source_index},
                            ]
                        )

            if mode == "scene":
                for left_index, left in enumerate(entries):
                    for right in entries[left_index + 1 :]:
                        if _different_people(left, right) and vehicle_conflicts(
                            left["options"], right["options"], same_site=True
                        ):
                            vehicle_duplicate_conflicts.extend(
                                [
                                    {"date": day, "entry": left["original"], "file_index": source_index},
                                    {"date": day, "entry": right["original"], "file_index": source_index},
                                ]
                            )

        flattened = [
            {"file_index": source_index, "entry": entry}
            for source_index, entries in enumerate(per_source_entries)
            for entry in entries
        ]
        for left_index, left in enumerate(flattened):
            for right in flattened[left_index + 1 :]:
                if left["file_index"] == right["file_index"]:
                    continue
                if not _same_person(left["entry"], right["entry"]):
                    continue
                if person_conflicts(left["entry"]["options"], right["entry"]["options"]):
                    conflicts.append({"date": day, "entry": left["entry"]["original"]})
                    conflicts.append({"date": day, "entry": right["entry"]["original"]})
                elif leave_work_conflict(left["entry"]["options"], right["entry"]["options"]):
                    leave_work_conflicts.extend(
                        [
                            {"date": day, "entry": left["entry"]["original"]},
                            {"date": day, "entry": right["entry"]["original"]},
                        ]
                    )

    conflicts = list({f'{item["date"]}-{item["entry"]}': item for item in conflicts}.values())
    same_site_conflicts = list(
        {
            f'{item["date"]}-{item["entry"]}-{item["file_index"]}': item
            for item in same_site_conflicts
        }.values()
    )
    leave_work_conflicts = list(
        {
            f'{item["date"]}-{item["entry"]}-{item.get("file_index", "cross")}': item
            for item in leave_work_conflicts
        }.values()
    )
    vehicle_duplicate_conflicts = list(
        {
            f'{item["date"]}-{item["entry"]}-{item["file_index"]}': item
            for item in vehicle_duplicate_conflicts
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
        "leave_work_conflicts": leave_work_conflicts,
        "vehicle_conflicts": vehicle_duplicate_conflicts,
        "option_mappings": OPTION_MAPPINGS,
        "second_option_mappings": SECOND_OPTION_MAPPINGS,
        "total_files": len(payloads),
    }


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _placement_conflict_kind(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    """2つの実配置（別シフト帳・同一人物・同一日）が衝突するかを判定する。

    戻り値: "time_overlap"（実時間帯が重なる）/ "double_booking"（オプション別
    ルールで二重配置）/ "leave_work"（休みと勤務が同日=注意）/ None（衝突なし）。
    """
    if leave_work_conflict(a, b):
        # 休みと勤務が同日に別々の帳へ入っている（配置ミス/申請漏れの可能性）。
        return "leave_work"
    # ここから双方とも勤務。
    a_range, b_range = a.get("time_range"), b.get("time_range")
    if _axes(a)["leave"] and _axes(b)["leave"]:
        return None
    if a_range and b_range and a.get("book_mode") == b.get("book_mode") == "large":
        return "time_overlap" if _ranges_overlap(a_range, b_range) else None
    # 少なくとも一方が実時間帯を持たない（現場/個人のオプション表記など）。
    # 既存のオプション別ルールで判定する。大規模のローカル勤務は option=None のため
    # 時間帯なしは終日扱いとして、他の勤務との取りこぼしを避ける。
    if person_conflicts(a, b):
        return "double_booking"
    return None


def cross_mode_conflicts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """人物×日付を軸に、複数シフト帳（大規模/現場/個人）を横断して二重配置を検出する。

    ``records`` は各配置を正規化した dict のリスト。呼び出し側が同期鏡像
    （他モードからのコピー）と大規模の非ローカル割当を除外して渡す前提で、ここでは
    「実配置のみ」を突き合わせる。必須キー:
      - book_id / book_label / book_mode
      - person_key（社員番号優先・無ければ ``name:正規化氏名``）/ person_label
      - day(int) / option(str|None) / is_leave(bool) / time_range(tuple[int,int]|None)
      - display(str)

    同一シフト帳内のペアは各モードの保存・計算側で扱うため対象外とする。
    """
    by_person_day: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    seen_origins: set[str] = set()
    for record in records:
        if not record.get("person_key"):
            continue
        origin_key = str(record.get("origin_key") or "").strip()
        if origin_key and origin_key in seen_origins:
            continue
        if origin_key:
            seen_origins.add(origin_key)
        by_person_day[(str(record["person_key"]), int(record["day"]))].append(record)

    results: list[dict[str, Any]] = []
    for (person_key, day), items in by_person_day.items():
        for left_index in range(len(items)):
            for right_index in range(left_index + 1, len(items)):
                left, right = items[left_index], items[right_index]
                if str(left.get("book_id")) == str(right.get("book_id")):
                    continue
                kind = _placement_conflict_kind(left, right)
                if not kind:
                    continue
                results.append({
                    "day": day,
                    "person_key": person_key,
                    "person_label": left.get("person_label") or right.get("person_label") or person_key,
                    "kind": kind,
                    "left": {
                        "book_id": left.get("book_id"),
                        "book_label": left.get("book_label"),
                        "book_mode": left.get("book_mode"),
                        "display": left.get("display"),
                    },
                    "right": {
                        "book_id": right.get("book_id"),
                        "book_label": right.get("book_label"),
                        "book_mode": right.get("book_mode"),
                        "display": right.get("display"),
                    },
                })
    results.sort(key=lambda item: (item["day"], str(item["person_label"])))
    return results
