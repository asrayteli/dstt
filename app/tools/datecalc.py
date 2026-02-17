from flask import Blueprint, render_template, request
from datetime import datetime, timedelta, time
import calendar
from flask_login import login_required


datecalc_bp = Blueprint("datecalc", __name__, url_prefix="/tools/datecalc")

WEEKDAY_LABELS = ["日", "月", "火", "水", "木", "金", "土"]


def parse_datetime_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def parse_date_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def format_datetime(dt: datetime, output_format: str) -> str:
    if output_format == "slash":
        return dt.strftime("%Y/%m/%d %H:%M")
    if output_format == "iso":
        return dt.strftime("%Y-%m-%dT%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def add_years_months(base: datetime, years: int, months: int, keep_month_end: bool) -> datetime:
    total_month = base.month - 1 + months + years * 12
    new_year = base.year + total_month // 12
    new_month = total_month % 12 + 1

    last_day_target = calendar.monthrange(new_year, new_month)[1]
    is_base_month_end = base.day == calendar.monthrange(base.year, base.month)[1]

    if keep_month_end and is_base_month_end:
        day = last_day_target
    else:
        day = min(base.day, last_day_target)

    return base.replace(year=new_year, month=new_month, day=day)


def to_sunday_start_weekday(dt: datetime) -> int:
    return (dt.weekday() + 1) % 7


def build_default_form_state():
    now = datetime.now().replace(second=0, microsecond=0)
    return {
        "base_datetime": now.strftime("%Y-%m-%dT%H:%M"),
        "date1": now.strftime("%Y-%m-%d"),
        "date2": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
        "operation": "add",
        "years": 0,
        "months": 0,
        "days": 0,
        "hours": 0,
        "minutes": 0,
        "output_format": "hyphen",
        "absolute_diff": True,
        "month_base_date": now.strftime("%Y-%m-%d"),
        "month_shift": 1,
        "keep_month_end": True,
    }


def update_form_state_from_request(form, state):
    state["base_datetime"] = form.get("base_datetime") or state["base_datetime"]
    state["date1"] = form.get("date1") or state["date1"]
    state["date2"] = form.get("date2") or state["date2"]
    state["operation"] = form.get("operation") or state["operation"]
    state["output_format"] = form.get("output_format") or state["output_format"]
    state["month_base_date"] = form.get("month_base_date") or state["month_base_date"]

    for key in ["years", "months", "days", "hours", "minutes", "month_shift"]:
        try:
            state[key] = int(form.get(key) or 0)
        except ValueError:
            state[key] = 0

    state["absolute_diff"] = form.get("absolute_diff") == "on"
    state["keep_month_end"] = form.get("keep_month_end") == "on"


def handle_addsub(form_state):
    base = parse_datetime_local(form_state["base_datetime"])
    sign = 1 if form_state["operation"] == "add" else -1

    shifted = add_years_months(
        base,
        years=sign * form_state["years"],
        months=sign * form_state["months"],
        keep_month_end=True,
    )

    delta = timedelta(
        days=sign * form_state["days"],
        hours=sign * form_state["hours"],
        minutes=sign * form_state["minutes"],
    )

    result_dt = shifted + delta
    display = format_datetime(result_dt, form_state["output_format"])

    return {
        "title": "加算・減算結果",
        "summary": display,
        "details": [
            f"曜日: {WEEKDAY_LABELS[to_sunday_start_weekday(result_dt)]}",
            f"UNIX: {int(result_dt.timestamp())}",
        ],
    }


def handle_date_diff(form_state):
    d1 = parse_date_local(form_state["date1"]).date()
    d2 = parse_date_local(form_state["date2"]).date()

    raw_days = (d2 - d1).days
    days = abs(raw_days) if form_state["absolute_diff"] else raw_days

    if raw_days > 0:
        relation = "日時2が後"
    elif raw_days < 0:
        relation = "日時2が前"
    else:
        relation = "同日"

    return {
        "title": "日付差分結果",
        "summary": f"{days}日",
        "details": [
            f"{d1.strftime('%Y-%m-%d')} から {d2.strftime('%Y-%m-%d')}",
            f"判定: {relation}",
        ],
    }


def handle_month_shift(form_state):
    base = parse_date_local(form_state["month_base_date"])
    base_dt = datetime.combine(base.date(), time(0, 0))

    shifted = add_years_months(
        base_dt,
        years=0,
        months=form_state["month_shift"],
        keep_month_end=form_state["keep_month_end"],
    )

    summary = shifted.strftime("%Y-%m-%d")
    return {
        "title": "月単位シフト結果",
        "summary": summary,
        "details": [
            f"曜日: {WEEKDAY_LABELS[to_sunday_start_weekday(shifted)]}",
            f"月末固定: {'有効' if form_state['keep_month_end'] else '無効'}",
        ],
    }


@datecalc_bp.route("/", methods=["GET", "POST"])
@login_required
def datecalc():
    form_state = build_default_form_state()
    result = None
    errors = []

    if request.method == "POST":
        mode = request.form.get("mode", "addsub")
        update_form_state_from_request(request.form, form_state)

        try:
            if mode == "addsub":
                result = handle_addsub(form_state)
            elif mode == "diff":
                result = handle_date_diff(form_state)
            elif mode == "month_shift":
                result = handle_month_shift(form_state)
            else:
                errors.append("未対応の計算モードです。")
        except Exception as exc:
            errors.append(f"入力エラー: {exc}")

    return render_template(
        "datecalc.html",
        form_state=form_state,
        result=result,
        errors=errors,
    )
