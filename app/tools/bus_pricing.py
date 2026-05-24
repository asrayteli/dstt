from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Any

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.access_control import is_admin_user, require_admin
from app.services.bus_pricing_config import (
    load_effective_block_master,
    reset_rate_config,
    save_rate_config,
)
from app.services.bus_pricing_master import BLOCK_MASTER, CAR_MASTER
from app.services.bus_pricing_service import (
    BusPricingInputError,
    calculate,
    serialize_result,
    validate_params,
)


bus_pricing_bp = Blueprint("bus_pricing", __name__, url_prefix="/tools/bus_pricing")


DEFAULT_FORM_STATE: dict[str, Any] = {
    "block_id": "kanto",
    "car_type": "large",
    "running_distance_km": "300",
    "pax_running_distance_km": "260",
    "running_duration_min": "540",
    "steering_minutes": "",
    "pax_count": "40",
    "is_night_run": False,
    "night_minutes": "0",
    "is_overnight": False,
    "operating_days": "1",
    "is_ferry_used": False,
    "ferry_duration_min": "0",
    "ferry_rest_ok": False,
    "is_alternate_driver": False,
    "has_sleeper_bed": False,
    "is_annual_contract": False,
    "is_school_bus": False,
    "annual_operating_days": "",
    "utilization_rate_mode": "custom",
    "custom_utilization_rate": "0.6758",
    "is_special_vehicle": False,
    "special_vehicle_rate": "0",
    "use_floor_rates": True,
    "custom_dist_rate": "170",
    "custom_time_rate": "7190",
    "vehicle_items": [
        {"label": "", "car_type": "large", "quantity": "1", "pax_count": "40", "custom_dist_rate": "170", "custom_time_rate": "7190"},
        {"label": "", "car_type": "medium", "quantity": "0", "pax_count": "", "custom_dist_rate": "150", "custom_time_rate": "6070"},
        {"label": "", "car_type": "small", "quantity": "0", "pax_count": "", "custom_dist_rate": "130", "custom_time_rate": "5320"},
        {"label": "", "car_type": "commuter", "quantity": "0", "pax_count": "", "custom_dist_rate": "120", "custom_time_rate": "4740"},
    ],
    "expense_items": [
        {"name": "高速道路料金", "amount": "0", "note": ""},
        {"name": "駐車料", "amount": "0", "note": ""},
        {"name": "乗務員宿泊料", "amount": "0", "note": ""},
        {"name": "ガイド料", "amount": "0", "note": ""},
        {"name": "その他実費", "amount": "0", "note": ""},
    ],
}


BOOL_FIELDS = {
    "is_night_run",
    "is_overnight",
    "is_ferry_used",
    "ferry_rest_ok",
    "is_alternate_driver",
    "has_sleeper_bed",
    "is_annual_contract",
    "is_school_bus",
    "is_special_vehicle",
    "use_floor_rates",
}


@bus_pricing_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    blocks = load_effective_block_master()
    form_state = build_default_form_state()
    result = None
    errors: list[dict[str, str]] = []

    if request.method == "POST":
        update_form_state_from_request(request.form, form_state)
        try:
            params = coerce_form_to_params(form_state)
            result = calculate(params, blocks, CAR_MASTER)
        except BusPricingInputError as exc:
            errors.append({"field": exc.field, "message": exc.message})

    field_errors = {err["field"]: err["message"] for err in errors}
    return render_template(
        "bus_pricing.html",
        blocks=blocks,
        cars=CAR_MASTER,
        form_state=form_state,
        result=result,
        errors=errors,
        field_errors=field_errors,
        is_admin=is_admin_user(),
    )


@bus_pricing_bp.route("/estimate/preview", methods=["GET", "POST"])
@login_required
def estimate_preview():
    if request.method == "GET":
        return redirect(url_for("bus_pricing.index"))
    blocks = load_effective_block_master()
    form_state = build_default_form_state()
    update_form_state_from_request(request.form, form_state)
    errors: list[dict[str, str]] = []
    result = None
    try:
        params = coerce_form_to_params(form_state)
        result = calculate(params, blocks, CAR_MASTER)
    except BusPricingInputError as exc:
        errors.append({"field": exc.field, "message": exc.message})

    if errors:
        field_errors = {err["field"]: err["message"] for err in errors}
        return render_template(
            "bus_pricing.html",
            blocks=blocks,
            cars=CAR_MASTER,
            form_state=form_state,
            result=result,
            errors=errors,
            field_errors=field_errors,
            is_admin=is_admin_user(),
        )

    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y年%m月%d日")
    return render_template(
        "bus_pricing_estimate.html",
        result=result,
        form_state=form_state,
        today=today,
    )


@bus_pricing_bp.route("/estimate/pdf", methods=["POST"])
@login_required
def estimate_pdf():
    return jsonify(
        {
            "status": "not_implemented",
            "message": "PDF生成は次工程の実装用エンドポイントです。見積書プレビューの計算結果をここへ接続してください。",
        }
    ), 501


@bus_pricing_bp.route("/api/calculate", methods=["POST"])
@login_required
def api_calculate():
    blocks = load_effective_block_master()
    if not request.is_json:
        return jsonify({"status": "error", "errors": [{"field": "request", "message": "Content-Type は application/json を指定してください。"}]}), 400
    payload = request.get_json(silent=True) or {}
    try:
        params = validate_params(payload, blocks, CAR_MASTER)
        result = calculate(params, blocks, CAR_MASTER)
    except BusPricingInputError as exc:
        return jsonify({"status": "error", "errors": [{"field": exc.field, "message": exc.message}]}), 400
    return jsonify(serialize_result(result, params, blocks))


@bus_pricing_bp.route("/reference", methods=["GET"])
@login_required
def reference():
    return render_template(
        "bus_pricing_reference.html",
        blocks=load_effective_block_master(),
        cars=CAR_MASTER,
        is_admin=is_admin_user(),
    )


@bus_pricing_bp.route("/admin", methods=["GET", "POST"])
@login_required
@require_admin
def admin():
    saved = False
    if request.method == "POST" and request.form.get("action") == "reset":
        reset_rate_config()
        return redirect(url_for("bus_pricing.admin", reset="1"))

    blocks = load_effective_block_master()
    if request.method == "POST":
        blocks = _blocks_from_admin_form(request.form, blocks)
        save_rate_config(blocks)
        saved = True
    return render_template(
        "bus_pricing_admin.html",
        blocks=blocks,
        cars=CAR_MASTER,
        saved=saved,
        reset=request.args.get("reset") == "1",
    )


def build_default_form_state() -> dict[str, Any]:
    return deepcopy(DEFAULT_FORM_STATE)


def update_form_state_from_request(form, form_state: dict[str, Any]) -> None:
    for key in form_state:
        if key in {"vehicle_items", "expense_items"}:
            continue
        if key in BOOL_FIELDS:
            form_state[key] = key in form
        else:
            form_state[key] = form.get(key, form_state[key])
    form_state["vehicle_items"] = _vehicle_items_from_form(form)
    form_state["expense_items"] = _expense_items_from_form(form)


def coerce_form_to_params(form_state: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = dict(form_state)
    for key in BOOL_FIELDS:
        params[key] = bool(params.get(key))
    active_vehicle = next((item for item in params.get("vehicle_items", []) if str(item.get("quantity") or "0") not in {"", "0"}), None)
    if active_vehicle:
        params["car_type"] = active_vehicle.get("car_type", params["car_type"])
        params["pax_count"] = active_vehicle.get("pax_count") or params["pax_count"]
        params["custom_dist_rate"] = active_vehicle.get("custom_dist_rate") or params["custom_dist_rate"]
        params["custom_time_rate"] = active_vehicle.get("custom_time_rate") or params["custom_time_rate"]
    return validate_params(params, load_effective_block_master(), CAR_MASTER)


def _vehicle_items_from_form(form) -> list[dict[str, str]]:
    cars = form.getlist("vehicle_car_type")
    quantities = form.getlist("vehicle_quantity")
    pax_counts = form.getlist("vehicle_pax_count")
    dist_rates = form.getlist("vehicle_custom_dist_rate")
    time_rates = form.getlist("vehicle_custom_time_rate")
    labels = form.getlist("vehicle_label")
    rows = max(len(cars), len(quantities), len(pax_counts), len(dist_rates), len(time_rates), len(labels), 1)
    items = []
    for index in range(rows):
        car_type = cars[index] if index < len(cars) else "large"
        items.append(
            {
                "label": labels[index] if index < len(labels) else "",
                "car_type": car_type,
                "quantity": quantities[index] if index < len(quantities) else "0",
                "pax_count": pax_counts[index] if index < len(pax_counts) else "",
                "custom_dist_rate": dist_rates[index] if index < len(dist_rates) else "",
                "custom_time_rate": time_rates[index] if index < len(time_rates) else "",
            }
        )
    return items


def _expense_items_from_form(form) -> list[dict[str, str]]:
    names = form.getlist("expense_name")
    amounts = form.getlist("expense_amount")
    notes = form.getlist("expense_note")
    rows = max(len(names), len(amounts), len(notes), 1)
    items = []
    for index in range(rows):
        items.append(
            {
                "name": names[index] if index < len(names) else "",
                "amount": amounts[index] if index < len(amounts) else "0",
                "note": notes[index] if index < len(notes) else "",
            }
        )
    return items


def _blocks_from_admin_form(form, current_blocks: dict[str, dict]) -> dict[str, dict]:
    blocks = {key: dict(value) for key, value in current_blocks.items()}
    for block_id, block in blocks.items():
        block["name"] = form.get(f"{block_id}__name", block["name"]).strip() or block["name"]
        block["util_rate"] = _form_float(form, f"{block_id}__util_rate", block["util_rate"])
        block["company_util_rate"] = _form_float(form, f"{block_id}__company_util_rate", block.get("company_util_rate", block["util_rate"]))
        block["alt_dist_rate"] = _form_int(form, f"{block_id}__alt_dist_rate", block["alt_dist_rate"])
        block["alt_time_rate"] = _form_int(form, f"{block_id}__alt_time_rate", block["alt_time_rate"])
        block["dist_rate"] = dict(block["dist_rate"])
        block["time_rate"] = dict(block["time_rate"])
        for car_type in CAR_MASTER:
            block["dist_rate"][car_type] = _form_int(form, f"{block_id}__dist__{car_type}", block["dist_rate"][car_type])
            block["time_rate"][car_type] = _form_int(form, f"{block_id}__time__{car_type}", block["time_rate"][car_type])
    return blocks


def _form_int(form, key: str, default: int) -> int:
    try:
        return int(float(form.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


def _form_float(form, key: str, default: float) -> float:
    try:
        return float(form.get(key, default))
    except (TypeError, ValueError):
        return float(default)
