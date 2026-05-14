from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

try:
    from app.tools.japan_holidays import JAPAN_HOLIDAYS
except ImportError:  # pragma: no cover - direct module loading fallback
    JAPAN_HOLIDAYS = []


WEEKDAY_LABELS = ["日", "月", "火", "水", "木", "金", "土"]


class DateCalcInputError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class DateCalcResult:
    title: str
    summary: str
    details: list[str]
    copy_text: str
    mode: str
    input_summary: str


def parse_datetime_local(value: str, field: str = "datetime") -> datetime:
    text = (value or "").strip()
    if not text:
        raise DateCalcInputError(field, "日時を入力してください。")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise DateCalcInputError(field, "日時の形式が正しくありません。") from exc


def parse_date_local(value: str, field: str = "date") -> date:
    text = (value or "").strip()
    if not text:
        raise DateCalcInputError(field, "日付を入力してください。")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise DateCalcInputError(field, "日付の形式が正しくありません。") from exc


def parse_optional_date_local(value: str, fallback: date, field: str) -> date:
    if not (value or "").strip():
        return fallback
    return parse_date_local(value, field)


def parse_int(value: object, field: str, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        number = default
    else:
        try:
            number = int(text)
        except ValueError as exc:
            raise DateCalcInputError(field, "整数で入力してください。") from exc

    if minimum is not None and number < minimum:
        raise DateCalcInputError(field, f"{minimum}以上で入力してください。")
    if maximum is not None and number > maximum:
        raise DateCalcInputError(field, f"{maximum}以下で入力してください。")
    return number


def _split_tokens(raw: str) -> list[str]:
    normalized = (
        (raw or "")
        .replace("\n", ",")
        .replace("\r", ",")
        .replace("\t", ",")
        .replace(" ", ",")
        .replace("、", ",")
        .replace("，", ",")
    )
    return [token.strip() for token in normalized.split(",") if token.strip()]


def parse_month_days(raw: str, field: str = "count_month_days") -> set[int]:
    values: set[int] = set()
    invalid: list[str] = []
    for token in _split_tokens(raw):
        try:
            value = int(token)
        except ValueError:
            invalid.append(token)
            continue
        if 1 <= value <= 31:
            values.add(value)
        else:
            invalid.append(token)

    if invalid:
        raise DateCalcInputError(field, f"日付は1から31で入力してください: {', '.join(invalid[:3])}")
    return values


def parse_exact_dates(raw: str, field: str = "count_exact_dates") -> set[date]:
    values: set[date] = set()
    invalid: list[str] = []
    for token in _split_tokens(raw):
        try:
            values.add(parse_date_local(token, field))
        except DateCalcInputError:
            invalid.append(token)

    if invalid:
        raise DateCalcInputError(field, f"個別日付の形式が正しくありません: {', '.join(invalid[:3])}")
    return values


def selected_weekdays(values: list[str] | tuple[str, ...] | None) -> set[int]:
    return {int(value) for value in values or [] if str(value).isdigit() and 0 <= int(value) <= 6}


def format_datetime(dt: datetime, output_format: str) -> str:
    if output_format == "slash":
        return dt.strftime("%Y/%m/%d %H:%M")
    if output_format == "iso":
        return dt.strftime("%Y-%m-%dT%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def format_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def add_years_months(base: datetime, years: int, months: int, keep_month_end: bool) -> datetime:
    total_month = base.month - 1 + months + years * 12
    new_year = base.year + total_month // 12
    new_month = total_month % 12 + 1

    last_day_target = calendar.monthrange(new_year, new_month)[1]
    is_base_month_end = base.day == calendar.monthrange(base.year, base.month)[1]
    day = last_day_target if keep_month_end and is_base_month_end else min(base.day, last_day_target)

    return base.replace(year=new_year, month=new_month, day=day)


def to_sunday_start_weekday(value: date | datetime) -> int:
    return (value.weekday() + 1) % 7


def weekday_label(value: date | datetime) -> str:
    return WEEKDAY_LABELS[to_sunday_start_weekday(value)]


def each_date(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def normalized_range(start_date: date, end_date: date) -> tuple[date, date, bool]:
    if start_date <= end_date:
        return start_date, end_date, False
    return end_date, start_date, True


def calculate_datetime_delta(start_dt: datetime, end_dt: datetime, absolute: bool) -> dict[str, int | str]:
    raw_seconds = int((end_dt - start_dt).total_seconds())
    seconds = abs(raw_seconds) if absolute else raw_seconds
    sign = -1 if seconds < 0 else 1
    abs_seconds = abs(seconds)
    total_minutes = abs_seconds // 60
    days = total_minutes // (24 * 60)
    hours = (total_minutes % (24 * 60)) // 60
    minutes = total_minutes % 60
    prefix = "-" if sign < 0 else ""

    return {
        "raw_seconds": raw_seconds,
        "total_days": round(seconds / 86400, 4),
        "total_hours": round(seconds / 3600, 2),
        "total_minutes": sign * total_minutes,
        "parts": f"{prefix}{days}日 {hours}時間 {minutes}分",
    }


def calculate_addsub(
    base: datetime,
    operation: str,
    years: int,
    months: int,
    days: int,
    hours: int,
    minutes: int,
    output_format: str,
    keep_month_end: bool,
) -> DateCalcResult:
    sign = 1 if operation == "add" else -1
    shifted = add_years_months(base, sign * years, sign * months, keep_month_end)
    result_dt = shifted + timedelta(days=sign * days, hours=sign * hours, minutes=sign * minutes)
    display = format_datetime(result_dt, output_format)
    operator = "加算" if sign > 0 else "減算"
    input_summary = f"{format_datetime(base, 'hyphen')} に {years}年 {months}か月 {days}日 {hours}時間 {minutes}分を{operator}"

    return DateCalcResult(
        title="加算・減算結果",
        summary=display,
        details=[
            f"曜日: {weekday_label(result_dt)}",
            f"ISO: {result_dt.strftime('%Y-%m-%dT%H:%M')}",
            f"月末維持: {'有効' if keep_month_end else '無効'}",
        ],
        copy_text=display,
        mode="addsub",
        input_summary=input_summary,
    )


def calculate_diff(start_dt: datetime, end_dt: datetime, absolute: bool) -> DateCalcResult:
    delta = calculate_datetime_delta(start_dt, end_dt, absolute)
    raw_seconds = int(delta["raw_seconds"])
    if raw_seconds > 0:
        relation = "終了日時が後"
    elif raw_seconds < 0:
        relation = "終了日時が前"
    else:
        relation = "同時刻"

    summary = str(delta["parts"])
    return DateCalcResult(
        title="差分結果",
        summary=summary,
        details=[
            f"総日数: {delta['total_days']}日",
            f"総時間: {delta['total_hours']}時間",
            f"総分: {delta['total_minutes']}分",
            f"判定: {relation}",
        ],
        copy_text=summary,
        mode="diff",
        input_summary=f"{format_datetime(start_dt, 'hyphen')} から {format_datetime(end_dt, 'hyphen')}",
    )


def calculate_month_shift(base_date: date, month_shift: int, keep_month_end: bool) -> DateCalcResult:
    base_dt = datetime.combine(base_date, time(0, 0))
    shifted = add_years_months(base_dt, 0, month_shift, keep_month_end)
    summary = format_date(shifted.date())

    return DateCalcResult(
        title="月送り結果",
        summary=summary,
        details=[
            f"曜日: {weekday_label(shifted)}",
            f"月末維持: {'有効' if keep_month_end else '無効'}",
            f"基準日: {format_date(base_date)}",
        ],
        copy_text=summary,
        mode="month_shift",
        input_summary=f"{format_date(base_date)} から {month_shift}か月",
    )


def calculate_date_count(
    start_date: date,
    end_date: date,
    weekdays: set[int],
    month_days: set[int],
    exact_dates: set[date],
    include_japan_holidays: bool,
) -> DateCalcResult:
    start, end, swapped = normalized_range(start_date, end_date)
    range_days = list(each_date(start, end))
    holiday_dates = {
        parse_date_local(value)
        for value in JAPAN_HOLIDAYS
        if start <= parse_date_local(value) <= end
    } if include_japan_holidays else set()

    weekday_hits = {day for day in range_days if to_sunday_start_weekday(day) in weekdays}
    month_day_hits = {day for day in range_days if day.day in month_days}
    exact_hits = {day for day in exact_dates if start <= day <= end}
    holiday_hits = {day for day in range_days if day in holiday_dates}
    all_hits = weekday_hits | month_day_hits | exact_hits | holiday_hits

    preview = ", ".join(format_date(day) for day in sorted(all_hits)[:20])
    if len(all_hits) > 20:
        preview += f" ほか{len(all_hits) - 20}日"

    selected_targets: list[str] = []
    if weekdays:
        selected_targets.append("曜日")
    if month_days:
        selected_targets.append("毎月の日付")
    if exact_dates:
        selected_targets.append("個別日付")
    if include_japan_holidays:
        selected_targets.append("祝日")

    details = [
        f"期間: {format_date(start)} から {format_date(end)}",
        f"期間日数: {len(range_days)}日",
        f"曜日一致: {len(weekday_hits)}日",
        f"毎月の日付一致: {len(month_day_hits)}日",
        f"個別日付一致: {len(exact_hits)}日",
        f"祝日一致: {len(holiday_hits)}日",
        f"重複除外後: {len(all_hits)}日",
    ]
    if preview:
        details.append(f"一致日: {preview}")
    if swapped:
        details.append("開始日と終了日を入れ替えて計算しました。")

    summary = f"{len(all_hits)}日"
    return DateCalcResult(
        title="指定日カウント結果",
        summary=summary,
        details=details,
        copy_text=summary,
        mode="count",
        input_summary=f"{format_date(start)} から {format_date(end)} / 対象: {', '.join(selected_targets) if selected_targets else '未指定'}",
    )
