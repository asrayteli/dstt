"""現場の「勤怠時間」「出勤時間」の正規化と表示整形。

現場リストPLUS（siteplus）と ShifterSync / CloudShift の双方から使う共通ロジック。
DB へは常に ``HH:MM``（ゼロ埋め）で保存し、表示時だけ時の先頭ゼロを落として
「9:40」「10:00」の形にする。

- 勤怠時間: ``[{"start": "HH:MM", "end": "HH:MM"}, ...]``
  中抜け休憩に対応するため複数区間を保持できる。
- 出勤時間: ``"HH:MM"`` の単発値。
"""

from __future__ import annotations

import json
import re
from typing import Any


# 中抜けが何度もある現場を想定しても十分な上限。無制限にすると表示・CSVが壊れるため区切る。
MAX_ATTENDANCE_RANGES = 6

ATTENDANCE_LABEL = "勤怠"
REPORT_LABEL = "出勤"
RANGE_SEPARATOR = "~"
# 「勤怠：…、出勤：…」と読点で連結するため、区間同士は読点以外で区切る。
MULTI_RANGE_SEPARATOR = " / "
SEGMENT_SEPARATOR = "、"

_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
# 全角コロン・各種チルダ/ハイフンを ASCII に寄せて、手入力の揺れを吸収する。
_SEPARATOR_TRANSLATION = str.maketrans("：〜～－ー―‐−", ":~~-----")
_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_COMPACT_TIME_PATTERN = re.compile(r"^(\d{1,2})(\d{2})$")
_JP_TIME_PATTERN = re.compile(r"^(\d{1,2})時(?:(\d{1,2})分?)?$")


def _ascii_digits(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    return text.translate(_DIGIT_TRANSLATION).translate(_SEPARATOR_TRANSLATION).strip()


def normalize_time_text(value: Any) -> str:
    """時刻を ``HH:MM`` へ正規化する。解釈できない値は空文字を返す。"""
    text = _ascii_digits(value)
    if not text:
        return ""
    text = text.replace(" ", "")

    hour_text = ""
    minute_text = ""
    matched = _TIME_PATTERN.match(text)
    if matched:
        hour_text, minute_text = matched.group(1), matched.group(2)
    else:
        matched = _JP_TIME_PATTERN.match(text)
        if matched:
            hour_text, minute_text = matched.group(1), matched.group(2) or "0"
        else:
            matched = _COMPACT_TIME_PATTERN.match(text)
            if matched:
                hour_text, minute_text = matched.group(1), matched.group(2)
            elif text.isdigit() and len(text) <= 2:
                hour_text, minute_text = text, "0"

    if not hour_text:
        return ""
    hour = int(hour_text)
    minute = int(minute_text)
    # 24:00 は「その日の終わり」を表す慣用表記として通す。
    if hour == 24 and minute == 0:
        return "24:00"
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def parse_time_text(value: Any, label: str) -> str:
    """``normalize_time_text`` の検証版。入力があって解釈できない場合は例外。"""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return ""
    normalized = normalize_time_text(raw)
    if not normalized:
        raise ValueError(f"{label}は 9:40 のような時刻形式で入力してください")
    return normalized


def _coerce_range_source(item: Any) -> tuple[Any, Any]:
    if isinstance(item, dict):
        return (
            item.get("start", item.get("from", item.get("begin", ""))),
            item.get("end", item.get("to", item.get("finish", ""))),
        )
    if isinstance(item, (list, tuple)):
        start = item[0] if len(item) > 0 else ""
        end = item[1] if len(item) > 1 else ""
        return start, end
    text = _ascii_digits(item)
    if not text:
        return "", ""
    for separator in ("~", "-"):
        if separator in text:
            start, _, end = text.partition(separator)
            return start, end
    return text, ""


def _iter_range_sources(value: Any):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                decoded = None
            if decoded is not None:
                return _iter_range_sources(decoded)
        # "10:00~13:00, 14:00~19:00" のような1行入力にも対応する。
        return [chunk for chunk in re.split(r"[,\n、/]", text) if chunk.strip()]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_attendance_times(value: Any) -> list[dict[str, str]]:
    """勤怠時間を ``[{"start", "end"}, ...]`` へ正規化する（不正な区間は落とす）。"""
    ranges: list[dict[str, str]] = []
    for item in _iter_range_sources(value):
        raw_start, raw_end = _coerce_range_source(item)
        start = normalize_time_text(raw_start)
        end = normalize_time_text(raw_end)
        if not start and not end:
            continue
        ranges.append({"start": start, "end": end})
        if len(ranges) >= MAX_ATTENDANCE_RANGES:
            break
    return ranges


def parse_attendance_times(value: Any) -> list[dict[str, str]]:
    """``normalize_attendance_times`` の検証版。入力の取りこぼしを例外で知らせる。"""
    sources = list(_iter_range_sources(value))
    if len(sources) > MAX_ATTENDANCE_RANGES:
        raise ValueError(f"勤怠時間は{MAX_ATTENDANCE_RANGES}区間まで登録できます")

    ranges: list[dict[str, str]] = []
    for item in sources:
        raw_start, raw_end = _coerce_range_source(item)
        start = parse_time_text(raw_start, "勤怠時間の開始")
        end = parse_time_text(raw_end, "勤怠時間の終了")
        if not start and not end:
            continue
        if not start or not end:
            raise ValueError("勤怠時間は開始と終了の両方を入力してください")
        ranges.append({"start": start, "end": end})
    return ranges


def _display_time(value: str) -> str:
    """``09:40`` → ``9:40``。時の先頭ゼロだけを落とす。"""
    text = str(value or "").strip()
    matched = _TIME_PATTERN.match(text)
    if not matched:
        return text
    return f"{int(matched.group(1))}:{matched.group(2)}"


def format_attendance_times(value: Any) -> str:
    """``10:00~13:00 / 14:00~19:00`` の形に整形する（値なしは空文字）。"""
    parts = []
    for item in normalize_attendance_times(value):
        start = _display_time(item.get("start", ""))
        end = _display_time(item.get("end", ""))
        if start and end:
            parts.append(f"{start}{RANGE_SEPARATOR}{end}")
        elif start or end:
            parts.append(start or end)
    return MULTI_RANGE_SEPARATOR.join(parts)


def format_report_time(value: Any) -> str:
    """``9:40`` の形に整形する（値なしは空文字）。"""
    return _display_time(normalize_time_text(value))


def format_shift_time_label(
    attendance_times: Any,
    report_time: Any,
    *,
    show_attendance: bool = True,
    show_report: bool = True,
) -> str:
    """カレンダー等に出す「勤怠：10:00~19:00、出勤：9:40」の1行を組み立てる。"""
    segments: list[str] = []
    if show_attendance:
        attendance_text = format_attendance_times(attendance_times)
        if attendance_text:
            segments.append(f"{ATTENDANCE_LABEL}：{attendance_text}")
    if show_report:
        report_text = format_report_time(report_time)
        if report_text:
            segments.append(f"{REPORT_LABEL}：{report_text}")
    return SEGMENT_SEPARATOR.join(segments)


def attendance_times_equal(left: Any, right: Any) -> bool:
    return normalize_attendance_times(left) == normalize_attendance_times(right)


def _shift_time_values(source: Any) -> tuple[list[dict[str, str]], str]:
    if source is None:
        return [], ""
    if isinstance(source, dict):
        attendance = source.get("attendance_times")
        report = source.get("report_time")
    else:
        attendance = getattr(source, "attendance_times", None)
        report = getattr(source, "report_time", None)
    return normalize_attendance_times(attendance), normalize_time_text(report)


def resolve_shift_times(site: Any, branch: Any = None) -> dict[str, Any]:
    """親現場を既定値、枝番号を上書きとして勤怠/出勤を解決する。

    勤怠時間と出勤時間はそれぞれ独立に上書き判定する（枝番号側で片方だけ
    設定してある場合、もう片方は親現場の値を引き継ぐ）。
    """
    attendance, report = _shift_time_values(site)
    branch_attendance, branch_report = _shift_time_values(branch)
    if branch_attendance:
        attendance = branch_attendance
    if branch_report:
        report = branch_report
    return {"attendance_times": attendance, "report_time": report}
