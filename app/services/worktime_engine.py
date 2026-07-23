from __future__ import annotations

"""CloudShift から独立した、分単位の勤務時間計算エンジン。"""

from calendar import monthrange
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


ENGINE_VERSION = "1.0.0"
DAY_TYPES = ("weekday", "saturday", "holiday")
HOLIDAY_KINDS = ("", "scheduled", "legal")


@dataclass(frozen=True)
class TimeRange:
    start_minutes: int
    end_minutes: int

    def __post_init__(self) -> None:
        if not (0 <= self.start_minutes <= 1439):
            raise ValueError("始業時刻は00:00から23:59で指定してください")
        if not (1 <= self.end_minutes <= 1440):
            raise ValueError("終業時刻は00:01から24:00で指定してください")
        if self.start_minutes >= self.end_minutes:
            raise ValueError("始業時刻は終業時刻より前にしてください（日跨ぎ勤務は非対応です）")


@dataclass(frozen=True)
class WorkCode:
    key: str
    category: str
    times: Mapping[str, TimeRange | None]
    break_minutes: int | None = None
    leave_kind: str = ""
    requested: bool = False


@dataclass(frozen=True)
class DayInput:
    day: int
    day_type: str
    code_key: str = ""
    code_keys: tuple[str, ...] = ()
    holiday_kind: str = ""
    time_override: TimeRange | None = None
    bind_override_minutes: int | None = None
    has_external_assignment: bool = False


@dataclass(frozen=True)
class PersonInput:
    person_id: str
    label: str
    scheduled_bind_minutes: int | None
    days: tuple[DayInput, ...]
    prev_day_end_minutes: int | None = None


@dataclass(frozen=True)
class CheckSettings:
    kaizen_monthly_bind_enabled: bool = True
    kaizen_monthly_bind_warn_minutes: int = 16860
    kaizen_daily_bind_enabled: bool = True
    kaizen_daily_bind_warn_minutes: int = 780
    kaizen_daily_bind_max_minutes: int = 900
    kaizen_rest_period_enabled: bool = True
    kaizen_rest_period_min_minutes: int = 540
    overtime_monthly_enabled: bool = False
    overtime_monthly_warn_minutes: int = 2700
    anei_long_work_enabled: bool = False
    anei_long_work_warn_minutes: int = 4800
    consecutive_days_enabled: bool = False
    consecutive_days_warn_days: int = 7


@dataclass(frozen=True)
class WorktimeSettings:
    break_minutes: int = 60
    scheduled_bind_minutes: int = 570
    checks: CheckSettings = field(default_factory=CheckSettings)


@dataclass(frozen=True)
class WorktimeRequest:
    version: int
    year: int
    month: int
    codes: tuple[WorkCode, ...]
    settings: WorktimeSettings
    people: tuple[PersonInput, ...]


@dataclass(frozen=True)
class DayResult:
    day: int
    category: str
    code_key: str
    leave_kind: str
    requested: bool
    start_minutes: int | None
    end_minutes: int | None
    bind_minutes: int
    break_minutes: int
    work_minutes: int
    overtime_minutes: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PersonMonthTotals:
    calendar_days: int
    work_days: int
    bind_total_minutes: int
    break_total_minutes: int
    work_total_minutes: int
    payroll_overtime_minutes: int
    scheduled_holiday_work_minutes: int
    legal_holiday_work_minutes: int
    payroll_excess_total_minutes: int
    anei_base_minutes: int
    anei_excess_minutes: int
    leave_counts: Mapping[str, int]
    max_consecutive_work_days: int


@dataclass(frozen=True)
class WorktimeViolation:
    code: str
    severity: str
    person_id: str
    day: int | None
    message: str
    value_minutes: int
    threshold_minutes: int


@dataclass(frozen=True)
class PersonMonthResult:
    person_id: str
    label: str
    days: tuple[DayResult, ...]
    totals: PersonMonthTotals
    violations: tuple[WorktimeViolation, ...]


@dataclass(frozen=True)
class WorktimeResult:
    engine_version: str
    year: int
    month: int
    people: tuple[PersonMonthResult, ...]
    violations: tuple[WorktimeViolation, ...]


def minutes_to_hhmm(value: int | None) -> str | None:
    if value is None:
        return None
    value = max(0, int(value))
    return f"{value // 60}:{value % 60:02d}"


def parse_clock(value: Any, *, allow_24: bool = False) -> int:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"時刻 {text or '(空)'} はHH:MM形式で指定してください")
    hour, minute = int(parts[0]), int(parts[1])
    if minute > 59 or hour < 0 or hour > (24 if allow_24 else 23):
        raise ValueError(f"時刻 {text} が範囲外です")
    if hour == 24 and (not allow_24 or minute != 0):
        raise ValueError(f"時刻 {text} が範囲外です")
    return hour * 60 + minute


def time_range_from_json(value: Any) -> TimeRange | None:
    if value in (None, "", False):
        return None
    if not isinstance(value, Mapping):
        raise ValueError("時間帯は start/end を持つオブジェクトで指定してください")
    return TimeRange(
        parse_clock(value.get("start")),
        parse_clock(value.get("end"), allow_24=True),
    )


def _non_negative_int(value: Any, default: int, label: str) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}は整数で指定してください") from exc
    if result < 0:
        raise ValueError(f"{label}は0以上で指定してください")
    return result


def _optional_non_negative_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _non_negative_int(value, 0, label)


def _json_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError("真偽値の形式が不正です")


def _checks_from_json(raw: Any) -> CheckSettings:
    data = raw if isinstance(raw, Mapping) else {}
    defaults = CheckSettings()

    def item(name: str) -> Mapping[str, Any]:
        value = data.get(name)
        return value if isinstance(value, Mapping) else {}

    def enabled(name: str, default: bool) -> bool:
        value = item(name).get("enabled", default)
        return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}

    return CheckSettings(
        kaizen_monthly_bind_enabled=enabled("kaizen_monthly_bind", defaults.kaizen_monthly_bind_enabled),
        kaizen_monthly_bind_warn_minutes=_non_negative_int(item("kaizen_monthly_bind").get("warn_minutes"), defaults.kaizen_monthly_bind_warn_minutes, "月間拘束しきい値"),
        kaizen_daily_bind_enabled=enabled("kaizen_daily_bind", defaults.kaizen_daily_bind_enabled),
        kaizen_daily_bind_warn_minutes=_non_negative_int(item("kaizen_daily_bind").get("warn_minutes"), defaults.kaizen_daily_bind_warn_minutes, "日次拘束しきい値"),
        kaizen_daily_bind_max_minutes=_non_negative_int(item("kaizen_daily_bind").get("max_minutes"), defaults.kaizen_daily_bind_max_minutes, "日次拘束上限"),
        kaizen_rest_period_enabled=enabled("kaizen_rest_period", defaults.kaizen_rest_period_enabled),
        kaizen_rest_period_min_minutes=_non_negative_int(item("kaizen_rest_period").get("min_minutes"), defaults.kaizen_rest_period_min_minutes, "休息期間"),
        overtime_monthly_enabled=enabled("overtime_monthly", defaults.overtime_monthly_enabled),
        overtime_monthly_warn_minutes=_non_negative_int(item("overtime_monthly").get("warn_minutes"), defaults.overtime_monthly_warn_minutes, "残業しきい値"),
        anei_long_work_enabled=enabled("anei_long_work", defaults.anei_long_work_enabled),
        anei_long_work_warn_minutes=_non_negative_int(item("anei_long_work").get("warn_minutes"), defaults.anei_long_work_warn_minutes, "安衛しきい値"),
        consecutive_days_enabled=enabled("consecutive_days", defaults.consecutive_days_enabled),
        consecutive_days_warn_days=_non_negative_int(item("consecutive_days").get("warn_days"), defaults.consecutive_days_warn_days, "連続勤務日数"),
    )


def request_from_json(raw: Any) -> WorktimeRequest:
    if not isinstance(raw, Mapping):
        raise ValueError("JSONオブジェクトを指定してください")
    try:
        year, month = int(raw.get("year")), int(raw.get("month"))
    except (TypeError, ValueError) as exc:
        raise ValueError("年と月は整数で指定してください") from exc
    if int(raw.get("version", 1) or 1) != 1:
        raise ValueError("version は1のみ対応しています")
    if year < 1900 or year > 2100 or month < 1 or month > 12:
        raise ValueError("年または月が範囲外です")
    raw_codes = raw.get("codes") or []
    raw_people = raw.get("people") or []
    if not isinstance(raw_codes, list) or len(raw_codes) > 200:
        raise ValueError("コードは200件以内で指定してください")
    if not isinstance(raw_people, list) or len(raw_people) > 200:
        raise ValueError("人数は200人以内で指定してください")
    codes: list[WorkCode] = []
    seen: set[str] = set()
    for item in raw_codes:
        if not isinstance(item, Mapping):
            raise ValueError("コードの形式が不正です")
        key = str(item.get("key") or "").strip()
        folded = key.casefold()
        if not key or folded in seen:
            raise ValueError("コードkeyは空欄不可・大文字小文字を区別せず一意にしてください")
        seen.add(folded)
        category = str(item.get("category") or "work").lower()
        if category not in {"work", "leave"}:
            raise ValueError(f"コード {key} のcategoryが不正です")
        raw_times = item.get("times") if isinstance(item.get("times"), Mapping) else {}
        times = {day_type: time_range_from_json(raw_times.get(day_type)) for day_type in DAY_TYPES}
        codes.append(WorkCode(
            key=key,
            category=category,
            times=times,
            break_minutes=_optional_non_negative_int(item.get("break_minutes"), f"コード {key} の休憩"),
            leave_kind=str(item.get("leave_kind") or "").strip(),
            requested=_json_bool(item.get("requested")),
        ))
    raw_settings = raw.get("settings") if isinstance(raw.get("settings"), Mapping) else {}
    settings = WorktimeSettings(
        break_minutes=_non_negative_int(raw_settings.get("break_minutes"), 60, "休憩"),
        scheduled_bind_minutes=_non_negative_int(raw_settings.get("scheduled_bind_minutes"), 570, "所定拘束時間"),
        checks=_checks_from_json(raw_settings.get("checks")),
    )
    people: list[PersonInput] = []
    days_in_month = monthrange(year, month)[1]
    seen_people: set[str] = set()
    for item in raw_people:
        if not isinstance(item, Mapping):
            raise ValueError("メンバーの形式が不正です")
        person_id = str(item.get("person_id") or item.get("id") or "").strip()
        if not person_id:
            raise ValueError("person_idは必須です")
        if person_id in seen_people:
            raise ValueError("person_idが重複しています")
        seen_people.add(person_id)
        raw_days = item.get("days") or []
        if not isinstance(raw_days, list) or len(raw_days) > 31:
            raise ValueError("日別入力は31件以内で指定してください")
        days: list[DayInput] = []
        seen_days: set[int] = set()
        for day_item in raw_days:
            if not isinstance(day_item, Mapping):
                raise ValueError("日別入力の形式が不正です")
            try:
                day = int(day_item.get("day"))
            except (TypeError, ValueError) as exc:
                raise ValueError("日は整数で指定してください") from exc
            if day < 1 or day > days_in_month or day in seen_days:
                raise ValueError("日は月内で重複なく指定してください")
            seen_days.add(day)
            day_type = str(day_item.get("day_type") or "weekday")
            holiday_kind = str(day_item.get("holiday_kind") or "")
            if day_type not in DAY_TYPES or holiday_kind not in HOLIDAY_KINDS:
                raise ValueError(f"{day}日のダイヤ種別または休日区分が不正です")
            raw_assignments = day_item.get("assignments")
            code_keys: list[str] = []
            if raw_assignments not in (None, ""):
                if not isinstance(raw_assignments, list) or len(raw_assignments) > 12:
                    raise ValueError(f"{day}日の勤務は12件以内で指定してください")
                for assignment in raw_assignments:
                    row = assignment if isinstance(assignment, Mapping) else {"code_key": assignment}
                    key = str(row.get("code_key", row.get("code", "")) or "").strip()
                    if key and key.casefold() not in {item.casefold() for item in code_keys}:
                        code_keys.append(key)
            singular_code = str(day_item.get("code", day_item.get("code_key", "")) or "").strip()
            if not code_keys and singular_code:
                code_keys = [singular_code]
            days.append(DayInput(
                day=day,
                day_type=day_type,
                code_key=code_keys[0] if code_keys else singular_code,
                code_keys=tuple(code_keys),
                holiday_kind=holiday_kind,
                time_override=time_range_from_json(day_item.get("time_override")),
                bind_override_minutes=_optional_non_negative_int(day_item.get("bind_override_minutes"), f"{day}日の所定拘束時間"),
                has_external_assignment=_json_bool(day_item.get("has_external_assignment")),
            ))
        prev_end = item.get("prev_day_end_minutes")
        if prev_end in (None, "") and item.get("prev_day_end") not in (None, ""):
            prev_end = parse_clock(item.get("prev_day_end"), allow_24=True)
        normalized_prev_end = _optional_non_negative_int(prev_end, "前月末終業時刻")
        if normalized_prev_end is not None and normalized_prev_end > 1440:
            raise ValueError("前月末終業時刻は0〜1440分で指定してください")
        people.append(PersonInput(
            person_id=person_id,
            label=str(item.get("label") or person_id).strip(),
            scheduled_bind_minutes=_optional_non_negative_int(item.get("scheduled_bind_minutes"), "個人所定拘束時間"),
            days=tuple(sorted(days, key=lambda day: day.day)),
            prev_day_end_minutes=normalized_prev_end,
        ))
    return WorktimeRequest(1, year, month, tuple(codes), settings, tuple(people))


def _violation(code: str, severity: str, person: PersonInput, day: int | None, value: int, threshold: int, message: str) -> WorktimeViolation:
    return WorktimeViolation(code, severity, person.person_id, day, message, value, threshold)


def _day_result(day: DayInput, person: PersonInput, code_map: Mapping[str, WorkCode], settings: WorktimeSettings) -> DayResult:
    warnings: list[str] = []
    code_keys = list(day.code_keys) or ([day.code_key] if day.code_key else [])
    if not code_keys:
        return DayResult(day.day, "external" if day.has_external_assignment else "empty", "", "", False, None, None, 0, 0, 0, 0, ())
    resolved_codes: list[WorkCode] = []
    for key in code_keys:
        code = code_map.get(key.casefold())
        if code is None:
            warnings.append("CODE_UNDEFINED")
        else:
            resolved_codes.append(code)
    if not resolved_codes:
        return DayResult(day.day, "empty", " + ".join(code_keys), "", False, None, None, 0, 0, 0, 0, tuple(dict.fromkeys(warnings)))
    leave_codes = [code for code in resolved_codes if code.category == "leave"]
    work_codes = [code for code in resolved_codes if code.category == "work"]
    if leave_codes and work_codes:
        warnings.append("LEAVE_WITH_WORK")
    if leave_codes and not work_codes:
        if len(leave_codes) > 1:
            warnings.append("MULTIPLE_LEAVE_CODES")
        code = leave_codes[0]
        return DayResult(day.day, "leave", code.key, code.leave_kind, code.requested, None, None, 0, 0, 0, 0, tuple(dict.fromkeys(warnings)))

    selected_ranges: list[tuple[TimeRange, WorkCode]] = []
    for code in work_codes:
        selected = day.time_override if len(work_codes) == 1 else None
        if selected is None:
            selected = code.times.get(day.day_type)
            if selected is None and day.day_type != "weekday" and code.times.get("weekday") is not None:
                selected = code.times.get("weekday")
                warnings.append("TIME_SET_FALLBACK")
        if selected is None:
            warnings.append("TIME_UNDEFINED")
            continue
        selected_ranges.append((selected, code))
    category = "work" if not day.holiday_kind else f"{day.holiday_kind}_holiday_work"
    if not selected_ranges:
        return DayResult(day.day, category, " + ".join(code.key for code in work_codes), "", False, None, None, 0, 0, 0, 0, tuple(dict.fromkeys(warnings)))

    intervals = sorted((item.start_minutes, item.end_minutes) for item, _ in selected_ranges)
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start >= merged[-1][1]:
            merged.append([start, end])
        else:
            warnings.append("TIME_OVERLAP")
            merged[-1][1] = max(merged[-1][1], end)
    bind = sum(end - start for start, end in merged)
    explicit_breaks = [code.break_minutes for _, code in selected_ranges if code.break_minutes is not None]
    break_minutes = sum(explicit_breaks) if explicit_breaks else settings.break_minutes
    break_minutes = min(max(0, break_minutes), bind)
    work = bind - break_minutes
    scheduled = day.bind_override_minutes
    if scheduled is None:
        scheduled = person.scheduled_bind_minutes
    if scheduled is None:
        scheduled = settings.scheduled_bind_minutes
    overtime = max(0, bind - scheduled) if category == "work" else 0
    return DayResult(
        day.day, category, " + ".join(code.key for code in work_codes), "", False,
        min(start for start, _ in intervals), max(end for _, end in intervals),
        bind, break_minutes, work, overtime, tuple(dict.fromkeys(warnings)),
    )


def calculate(request: WorktimeRequest) -> WorktimeResult:
    days_in_month = monthrange(request.year, request.month)[1]
    code_map = {code.key.casefold(): code for code in request.codes}
    all_people: list[PersonMonthResult] = []
    all_violations: list[WorktimeViolation] = []
    anei_base = {28: 9600, 29: 9900, 30: 10260, 31: 10620}[days_in_month]
    checks = request.settings.checks
    for person in request.people:
        inputs = {day.day: day for day in person.days}
        results = tuple(
            _day_result(inputs.get(day, DayInput(day, "weekday")), person, code_map, request.settings)
            for day in range(1, days_in_month + 1)
        )
        violations: list[WorktimeViolation] = []
        work_categories = {"work", "scheduled_holiday_work", "legal_holiday_work"}
        consecutive = maximum = 0
        leave_counts: dict[str, int] = {}
        previous_work: DayResult | None = None
        previous_calendar_day: int | None = None
        for result in results:
            if result.category in work_categories and result.bind_minutes > 0:
                consecutive += 1
                maximum = max(maximum, consecutive)
                if checks.kaizen_daily_bind_enabled and result.bind_minutes > checks.kaizen_daily_bind_warn_minutes:
                    violations.append(_violation("KAIZEN_DAILY_BIND", "warning", person, result.day, result.bind_minutes, checks.kaizen_daily_bind_warn_minutes, f"{result.day}日の拘束時間 {minutes_to_hhmm(result.bind_minutes)} が目安 {minutes_to_hhmm(checks.kaizen_daily_bind_warn_minutes)} を超えています"))
                if checks.kaizen_daily_bind_enabled and result.bind_minutes > checks.kaizen_daily_bind_max_minutes:
                    violations.append(_violation("KAIZEN_DAILY_BIND_MAX", "violation", person, result.day, result.bind_minutes, checks.kaizen_daily_bind_max_minutes, f"{result.day}日の拘束時間 {minutes_to_hhmm(result.bind_minutes)} が上限 {minutes_to_hhmm(checks.kaizen_daily_bind_max_minutes)} を超えています"))
                if checks.kaizen_rest_period_enabled and result.start_minutes is not None:
                    previous_end = None
                    if result.day == 1 and person.prev_day_end_minutes is not None:
                        previous_end = person.prev_day_end_minutes
                    elif previous_work is not None and previous_calendar_day == result.day - 1:
                        previous_end = previous_work.end_minutes
                    if previous_end is not None:
                        rest = (1440 - previous_end) + result.start_minutes
                        if rest < checks.kaizen_rest_period_min_minutes:
                            violations.append(_violation(
                                "KAIZEN_REST_PERIOD",
                                "violation",
                                person,
                                result.day,
                                rest,
                                checks.kaizen_rest_period_min_minutes,
                                f"{result.day}日の休息期間 {minutes_to_hhmm(rest)} が最低 {minutes_to_hhmm(checks.kaizen_rest_period_min_minutes)} を下回っています",
                            ))
                previous_work = result
                previous_calendar_day = result.day
            else:
                consecutive = 0
                previous_work = None
                previous_calendar_day = None
                if result.category in {"leave", "empty"}:
                    key = "empty" if result.category == "empty" else (result.leave_kind or "other") + ("_requested" if result.requested else "")
                    leave_counts[key] = leave_counts.get(key, 0) + 1
            for warning in result.warnings:
                messages = {
                    "TIME_UNDEFINED": "勤務時間が未設定です",
                    "TIME_SET_FALLBACK": "ダイヤ別時間が未設定のため平日時間を使用しました",
                    "CODE_UNDEFINED": "未登録の勤務コードです",
                    "TIME_OVERLAP": "複数の勤務時間が重なっています（重複時間は1回だけ集計しました）",
                    "LEAVE_WITH_WORK": "勤務と休みが同じ日に入っています（勤務のみ計算しました）",
                    "MULTIPLE_LEAVE_CODES": "休みが複数入っています（先頭の休みを集計しました）",
                }
                violations.append(_violation(warning, "info", person, result.day, 0, 0, messages.get(warning, warning)))
        bind_total = sum(day.bind_minutes for day in results)
        break_total = sum(day.break_minutes for day in results)
        work_total = sum(day.work_minutes for day in results)
        payroll_ot = sum(day.overtime_minutes for day in results if day.category == "work")
        scheduled_work = sum(day.work_minutes for day in results if day.category == "scheduled_holiday_work")
        legal_work = sum(day.work_minutes for day in results if day.category == "legal_holiday_work")
        anei_excess = max(0, work_total - anei_base)
        if checks.kaizen_monthly_bind_enabled and bind_total > checks.kaizen_monthly_bind_warn_minutes:
            violations.append(_violation("KAIZEN_MONTHLY_BIND", "warning", person, None, bind_total, checks.kaizen_monthly_bind_warn_minutes, f"月間拘束時間 {minutes_to_hhmm(bind_total)} が目安 {minutes_to_hhmm(checks.kaizen_monthly_bind_warn_minutes)} を超えています"))
        if checks.overtime_monthly_enabled and payroll_ot > checks.overtime_monthly_warn_minutes:
            violations.append(_violation("OVERTIME_MONTHLY", "warning", person, None, payroll_ot, checks.overtime_monthly_warn_minutes, f"給与残業 {minutes_to_hhmm(payroll_ot)} が目安を超えています"))
        if checks.anei_long_work_enabled and anei_excess > checks.anei_long_work_warn_minutes:
            violations.append(_violation("ANEI_LONG_WORK", "warning", person, None, anei_excess, checks.anei_long_work_warn_minutes, f"長時間労働 {minutes_to_hhmm(anei_excess)} が目安を超えています"))
        if checks.consecutive_days_enabled and maximum > checks.consecutive_days_warn_days:
            violations.append(_violation("CONSECUTIVE_DAYS", "warning", person, None, maximum * 1440, checks.consecutive_days_warn_days * 1440, f"連続勤務 {maximum}日 が目安 {checks.consecutive_days_warn_days}日を超えています"))
        totals = PersonMonthTotals(days_in_month, sum(1 for day in results if day.category in work_categories and day.bind_minutes > 0), bind_total, break_total, work_total, payroll_ot, scheduled_work, legal_work, payroll_ot + scheduled_work + legal_work, anei_base, anei_excess, leave_counts, maximum)
        person_result = PersonMonthResult(person.person_id, person.label, results, totals, tuple(violations))
        all_people.append(person_result)
        all_violations.extend(violations)
    return WorktimeResult(ENGINE_VERSION, request.year, request.month, tuple(all_people), tuple(all_violations))


def result_to_dict(result: WorktimeResult) -> dict[str, Any]:
    payload = asdict(result)
    duration_keys = {
        "bind_minutes", "break_minutes", "work_minutes", "overtime_minutes",
        "bind_total_minutes", "break_total_minutes", "work_total_minutes",
        "payroll_overtime_minutes", "scheduled_holiday_work_minutes",
        "legal_holiday_work_minutes", "payroll_excess_total_minutes",
        "anei_base_minutes", "anei_excess_minutes", "value_minutes", "threshold_minutes",
    }

    def decorate(value: Any) -> Any:
        if isinstance(value, dict):
            result_dict = {key: decorate(item) for key, item in value.items()}
            for key in duration_keys:
                if key in value and isinstance(value[key], int):
                    result_dict[key.removesuffix("_minutes") + "_hhmm"] = minutes_to_hhmm(value[key])
            if "start_minutes" in value:
                result_dict["start"] = minutes_to_hhmm(value.get("start_minutes"))
            if "end_minutes" in value:
                result_dict["end"] = minutes_to_hhmm(value.get("end_minutes"))
            return result_dict
        if isinstance(value, (list, tuple)):
            return [decorate(item) for item in value]
        return value

    return decorate(payload)
