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
        entry_second_option,
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
# 上表は時系列順(A午前→P午後→E早番→L遅番)でキーを書くが、照合側は
# tuple(sorted(...)) でアルファベット順に正規化して引く。両順序が食い違うキー
# （例 ("P","L") → sorted=("L","P")）は辞書ミスでフォールバックに落ち、本来の
# 衝突判定(True)を取りこぼす。照合用にキーをソート正規化した表を持つ。
_TIME_CONFLICT_LOOKUP = {
    tuple(sorted(pair)): value for pair, value in TIME_CONFLICT_RULES.items()
}
VEHICLE_OPTIONS = {"M", "C", "O", "W", "V"}
LEAVE_OPTION_KEYS = set(LEAVE_OPTION_MAPPINGS.keys())
# 代務・研修は「第二オプション」。entry の値ではなく別フィールドで保持し、
# アシスト／自動作成／表示にのみ用いる。重複チェックには影響させないため、
# ここでは ROLE_OPTION_KEYS を判定に使わない（互換のため定義のみ残す）。
ROLE_OPTION_KEYS = set(ROLE_OPTION_MAPPINGS.keys())
SECOND_OPTION_KEYS = ROLE_OPTION_KEYS


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
        return _TIME_CONFLICT_LOOKUP.get(key, option1 == option2)

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
                second_option = entry_second_option(entry)
                normalized_entries.append(
                    {
                        "original": entry["value"],
                        "display": entry_display_text(entry),
                        "comparison": entry_name_for_comparison(entry),
                        "option": option_key,
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
    a_leave = bool(a.get("is_leave"))
    b_leave = bool(b.get("is_leave"))
    if a_leave and b_leave:
        return None
    if a_leave != b_leave:
        # 休みと勤務が同日に別々の帳へ入っている（配置ミス/申請漏れの可能性）。
        return "leave_work"
    # ここから双方とも勤務。
    a_range, b_range = a.get("time_range"), b.get("time_range")
    if a_range and b_range:
        return "time_overlap" if _ranges_overlap(a_range, b_range) else None
    # 少なくとも一方が実時間帯を持たない（現場/個人のオプション表記など）。
    # 既存のオプション別ルールで判定する。大規模のローカル勤務は option=None のため
    # is_duplicate_by_rules 上は「他の勤務と衝突しうる」扱いになり、取りこぼしを避ける。
    if is_duplicate_by_rules(a.get("option"), b.get("option")):
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
    for record in records:
        if not record.get("person_key"):
            continue
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
