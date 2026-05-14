from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import login_required

from app.services.datecalc_service import (
    DateCalcInputError,
    WEEKDAY_LABELS,
    calculate_addsub,
    calculate_date_count,
    calculate_diff,
    calculate_month_shift,
    parse_date_local,
    parse_datetime_local,
    parse_exact_dates,
    parse_int,
    parse_month_days,
    parse_optional_date_local,
    selected_weekdays,
)


datecalc_bp = Blueprint("datecalc", __name__, url_prefix="/tools/datecalc")


def build_default_form_state() -> dict:
    now = datetime.now().replace(second=0, microsecond=0)
    tomorrow = now + timedelta(days=1)
    return {
        "base_datetime": now.strftime("%Y-%m-%dT%H:%M"),
        "diff_start_datetime": now.strftime("%Y-%m-%dT%H:%M"),
        "diff_end_datetime": tomorrow.strftime("%Y-%m-%dT%H:%M"),
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
        "count_start_date": "",
        "count_end_date": "",
        "count_weekdays": [],
        "count_month_days": "",
        "count_exact_dates": "",
        "count_japan_holidays": True,
    }


def update_form_state_from_request(form, state: dict) -> None:
    text_fields = [
        "base_datetime",
        "diff_start_datetime",
        "diff_end_datetime",
        "operation",
        "output_format",
        "month_base_date",
        "count_start_date",
        "count_end_date",
        "count_month_days",
        "count_exact_dates",
    ]
    for key in text_fields:
        if key in form:
            state[key] = form.get(key) or ""

    for key in ["years", "months", "days", "hours", "minutes", "month_shift"]:
        if key in form:
            state[key] = form.get(key) or 0

    state["absolute_diff"] = form.get("absolute_diff") == "on"
    state["keep_month_end"] = form.get("keep_month_end") == "on"
    state["count_japan_holidays"] = form.get("count_japan_holidays") == "on"
    state["count_weekdays"] = sorted(selected_weekdays(form.getlist("count_weekdays[]")))


def build_result(mode: str, form_state: dict):
    if mode == "addsub":
        return calculate_addsub(
            base=parse_datetime_local(form_state["base_datetime"], "base_datetime"),
            operation=form_state["operation"],
            years=parse_int(form_state["years"], "years"),
            months=parse_int(form_state["months"], "months"),
            days=parse_int(form_state["days"], "days"),
            hours=parse_int(form_state["hours"], "hours"),
            minutes=parse_int(form_state["minutes"], "minutes"),
            output_format=form_state["output_format"],
            keep_month_end=form_state["keep_month_end"],
        )

    if mode == "diff":
        return calculate_diff(
            start_dt=parse_datetime_local(form_state["diff_start_datetime"], "diff_start_datetime"),
            end_dt=parse_datetime_local(form_state["diff_end_datetime"], "diff_end_datetime"),
            absolute=form_state["absolute_diff"],
        )

    if mode == "month_shift":
        return calculate_month_shift(
            base_date=parse_date_local(form_state["month_base_date"], "month_base_date"),
            month_shift=parse_int(form_state["month_shift"], "month_shift"),
            keep_month_end=form_state["keep_month_end"],
        )

    if mode == "count":
        today = date.today()
        return calculate_date_count(
            start_date=parse_optional_date_local(form_state["count_start_date"], today, "count_start_date"),
            end_date=parse_optional_date_local(form_state["count_end_date"], today, "count_end_date"),
            weekdays=set(form_state["count_weekdays"]),
            month_days=parse_month_days(form_state["count_month_days"], "count_month_days"),
            exact_dates=parse_exact_dates(form_state["count_exact_dates"], "count_exact_dates"),
            include_japan_holidays=form_state["count_japan_holidays"],
        )

    raise DateCalcInputError("mode", "未対応の計算モードです。")


@datecalc_bp.route("/", methods=["GET", "POST"])
@login_required
def datecalc():
    form_state = build_default_form_state()
    result = None
    errors: list[str] = []
    field_errors: dict[str, str] = {}
    active_mode = "addsub"

    if request.method == "POST":
        active_mode = request.form.get("mode", "addsub")
        update_form_state_from_request(request.form, form_state)

        try:
            result = build_result(active_mode, form_state)
        except DateCalcInputError as exc:
            field_errors[exc.field] = exc.message
        except Exception:
            errors.append("計算中にエラーが発生しました。入力内容を確認してください。")

    return render_template(
        "datecalc.html",
        active_mode=active_mode,
        form_state=form_state,
        result=result,
        errors=errors,
        field_errors=field_errors,
        weekday_labels=WEEKDAY_LABELS,
    )
