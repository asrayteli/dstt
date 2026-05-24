from pathlib import Path
import sys

import pytest
from flask import Flask
from flask_login import LoginManager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.navigation import NAV_ITEMS
from app.services.bus_pricing_service import BusPricingInputError, calculate
from app.tools.bus_pricing import bus_pricing_bp


_VEHICLE_OVERRIDE_KEYS = {
    "car_type", "pax_count", "use_floor_rates", "custom_dist_rate", "custom_time_rate",
}


def base_params(**overrides):
    params = {
        "block_id": "kanto",
        "car_type": "large",
        "running_distance_km": 300,
        "pax_running_distance_km": 260,
        "running_duration_min": 540,
        "steering_minutes": 420,
        "pax_count": 40,
        "is_night_run": True,
        "night_minutes": 120,
        "is_overnight": False,
        "operating_days": 1,
        "is_ferry_used": False,
        "ferry_duration_min": 0,
        "ferry_rest_ok": False,
        "is_alternate_driver": False,
        "has_sleeper_bed": False,
        "is_annual_contract": False,
        "annual_operating_days": 0,
        "utilization_rate_mode": "official",
        "custom_utilization_rate": 0,
        "is_school_bus": False,
        "is_special_vehicle": False,
        "special_vehicle_rate": 0,
        "use_floor_rates": True,
        "custom_dist_rate": 170,
        "custom_time_rate": 7190,
    }
    params.update(overrides)
    # Build default vehicle from params unless caller provided their own vehicle_items
    if "vehicle_items" not in overrides:
        params["vehicle_items"] = [
            {
                "car_type": params["car_type"],
                "quantity": 1,
                "pax_count": params["pax_count"],
                "use_floor_rates": params["use_floor_rates"],
                "custom_dist_rate": params["custom_dist_rate"],
                "custom_time_rate": params["custom_time_rate"],
            },
        ]
    return params


def audit_ids(result):
    return {entry.audit_id for entry in result.audit_logs}


def test_golden_kanto_large_case_matches_design_values():
    result = calculate(base_params())

    assert result.calculation_breakdown["base_distance_fare"] == 51000
    assert result.calculation_breakdown["base_time_fare"] == 79090
    assert result.calculation_breakdown["night_surcharge"] == 2876
    assert result.calculation_breakdown["raw_total_excluding_tax"] == 132966
    assert result.calculation_breakdown["consumption_tax"] == 13297
    assert result.calculation_breakdown["legal_minimum_including_tax"] == 146263
    assert result.calculation_breakdown["recommended_billable_including_tax"] == 146263
    assert result.calculation_breakdown["approved_rounded_billing_fare"] == 147000
    assert not result.has_errors


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(41.2, 50), (50.0, 50), (50.1, 60)],
)
def test_distance_rounds_up_to_next_10km(distance, expected):
    result = calculate(base_params(running_distance_km=distance, pax_running_distance_km=min(40, distance), is_night_run=False, night_minutes=0))

    assert result.calc_factors["final_calc_distance_km"] == expected


@pytest.mark.parametrize(
    ("minutes", "expected_hours"),
    [(569, 11), (570, 12)],
)
def test_gross_time_uses_half_up_rounding(minutes, expected_hours):
    result = calculate(base_params(running_duration_min=minutes, steering_minutes=400, is_night_run=False, night_minutes=0))

    assert result.calc_factors["final_calc_hours"] == expected_hours


def test_minimum_three_hour_drive_time_is_applied():
    result = calculate(base_params(running_duration_min=174, steering_minutes=120, is_night_run=False, night_minutes=0))

    assert result.calc_factors["raw_running_hours"] == 2.9
    assert result.calc_factors["drive_hours_unrounded"] == 3.0
    assert result.calc_factors["final_calc_hours"] == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"running_duration_min": 787, "steering_minutes": 420},
        {"pax_running_distance_km": 501, "running_distance_km": 560, "is_night_run": False, "night_minutes": 0},
        {"pax_running_distance_km": 401, "running_distance_km": 460, "is_night_run": True, "night_minutes": 60},
        {"steering_minutes": 547, "running_duration_min": 600},
    ],
)
def test_alternate_driver_is_not_auto_applied_for_risk_conditions(overrides):
    result = calculate(base_params(**overrides))

    assert result.calc_factors["auto_alternate_driver_applied"] is False
    assert result.calc_factors["alternate_driver_required"] is False
    assert result.calculation_breakdown["alternate_driver_fare"] == 0
    assert "A-PLAN" in audit_ids(result)


def test_alternate_driver_fare_is_applied_only_when_user_selects_it():
    result = calculate(base_params(is_alternate_driver=True))

    assert result.calc_factors["alternate_driver_required"] is True
    assert result.calculation_breakdown["alternate_driver_fare"] > 0


def test_two_man_capacity_and_vehicle_errors():
    assert not calculate(base_params(is_alternate_driver=True, pax_count=58)).has_errors
    over_capacity = calculate(base_params(is_alternate_driver=True, pax_count=59))
    assert "C-009" in audit_ids(over_capacity)

    small = calculate(base_params(car_type="small", is_alternate_driver=True, pax_count=20))
    assert "C-010" in audit_ids(small)


def test_ferry_exclusion_requires_rest_and_caps_at_eight_hours():
    no_rest = calculate(base_params(is_ferry_used=True, ferry_duration_min=540, ferry_rest_ok=False, is_night_run=False, night_minutes=0))
    capped = calculate(base_params(is_ferry_used=True, ferry_duration_min=540, ferry_rest_ok=True, is_night_run=False, night_minutes=0))
    short = calculate(base_params(is_ferry_used=True, ferry_duration_min=300, ferry_rest_ok=True, is_night_run=False, night_minutes=0))

    assert no_rest.calc_factors["ferry_excluded_hours"] == 0
    assert capped.calc_factors["ferry_excluded_hours"] == 8
    assert short.calc_factors["ferry_excluded_hours"] == 5


def test_annual_contract_general_and_school_days():
    general = calculate(base_params(is_annual_contract=True, is_school_bus=False, annual_operating_days=200))
    school = calculate(base_params(is_annual_contract=True, is_school_bus=True, annual_operating_days=200))

    assert general.annual_contract["calculated_operating_days"] == 246
    assert general.annual_contract["max_run_days_allowed"] == 344
    assert school.annual_contract["calculated_operating_days"] == 200
    assert school.annual_contract["max_run_days_allowed"] == 280


def test_annual_contract_uses_custom_rate_between_official_and_company_rates():
    blocks = {
        "kanto": {
            "name": "関東運輸局",
            "util_rate": 0.6758,
            "company_util_rate": 0.6200,
            "dist_rate": {"large": 170, "medium": 150, "small": 130, "commuter": 120},
            "time_rate": {"large": 7190, "medium": 6070, "small": 5320, "commuter": 4740},
            "alt_dist_rate": 40,
            "alt_time_rate": 2670,
        }
    }

    result = calculate(
        base_params(
            is_annual_contract=True,
            annual_operating_days=200,
            utilization_rate_mode="custom",
            custom_utilization_rate=0.6400,
        ),
        block_master=blocks,
    )

    assert result.annual_contract["selected_utilization_rate"] == 0.6400
    assert result.annual_contract["selectable_utilization_min"] == 0.6200
    assert result.annual_contract["selectable_utilization_max"] == 0.6758
    assert result.annual_contract["utilization_based_days"] == 233


def test_annual_contract_rejects_rate_outside_official_company_range():
    blocks = {
        "kanto": {
            "name": "関東運輸局",
            "util_rate": 0.6758,
            "company_util_rate": 0.6200,
            "dist_rate": {"large": 170, "medium": 150, "small": 130, "commuter": 120},
            "time_rate": {"large": 7190, "medium": 6070, "small": 5320, "commuter": 4740},
            "alt_dist_rate": 40,
            "alt_time_rate": 2670,
        }
    }

    with pytest.raises(BusPricingInputError) as exc:
        calculate(
            base_params(
                is_annual_contract=True,
                annual_operating_days=200,
                utilization_rate_mode="custom",
                custom_utilization_rate=0.7000,
            ),
            block_master=blocks,
        )

    assert exc.value.field == "custom_utilization_rate"


def test_custom_rates_can_calculate_but_flag_floor_error():
    result = calculate(base_params(use_floor_rates=False, custom_dist_rate=160, custom_time_rate=7000))

    assert result.calculation_breakdown["below_floor_error"] is True
    assert result.calculation_breakdown["shortfall_including_tax"] > 0


def test_multi_vehicle_quote_and_expenses_are_totaled():
    result = calculate(
        base_params(
            is_night_run=False,
            night_minutes=0,
            vehicle_items=[
                {"car_type": "large", "quantity": 2, "pax_count": 40, "custom_dist_rate": 170, "custom_time_rate": 7190},
                {"car_type": "medium", "quantity": 1, "pax_count": 25, "custom_dist_rate": 150, "custom_time_rate": 6070},
            ],
            expense_items=[
                {"name": "高速道路料金", "amount": 30000, "note": "概算"},
                {"name": "駐車料", "amount": 5000, "note": ""},
            ],
        )
    )

    assert result.quote_summary["vehicle_count"] == 3
    assert len(result.vehicle_items) == 2
    assert result.quote_summary["expense_total_including_tax"] == 35000
    assert result.quote_summary["quote_total_including_tax"] == result.quote_summary["vehicle_rounded_fare_including_tax"] + 35000


def _build_client():
    app = Flask(
        __name__,
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return None

    @app.context_processor
    def inject_navigation():
        return {
            "app_navigation_items": NAV_ITEMS,
            "current_user_id": "tester",
            "app_version": "test",
            "default_page_title": lambda: "DSTT - test",
        }

    app.register_blueprint(bus_pricing_bp)
    return app.test_client()


def test_bus_pricing_page_renders():
    client = _build_client()

    response = client.get("/tools/bus_pricing/")

    assert response.status_code == 200
    assert "貸切料金計算ツール".encode("utf-8") in response.data
    assert "計算する".encode("utf-8") in response.data
    assert "bus_pricing".encode("utf-8") in str(NAV_ITEMS).encode("utf-8")


def test_estimate_preview_route_renders_quote_shell():
    import json

    client = _build_client()
    vehicles = [
        {"label": "1号車", "car_type": "large", "quantity": 1, "pax_count": 40,
         "use_floor_rates": True, "custom_dist_rate": 170, "custom_time_rate": 7190,
         "is_night_run": False, "night_minutes": 0, "is_overnight": False,
         "operating_days": 1, "is_alternate_driver": False, "is_ferry_used": False,
         "ferry_duration_min": 0, "ferry_rest_ok": False, "is_special_vehicle": False,
         "special_vehicle_rate": 0, "steering_minutes": 0, "has_sleeper_bed": False,
         "is_annual_contract": False, "annual_operating_days": 0,
         "custom_utilization_rate": 0.6758, "utilization_rate_mode": "custom",
         "is_school_bus": False}
    ]
    expenses = [{"name": "高速道路料金", "amount": 10000, "note": "概算"}]

    response = client.post(
        "/tools/bus_pricing/estimate/preview",
        data={
            "block_id": "kanto",
            "running_distance_km": "300",
            "pax_running_distance_km": "260",
            "running_duration_min": "540",
            "vehicles_json": json.dumps(vehicles),
            "expenses_json": json.dumps(expenses),
        },
    )

    assert response.status_code == 200
    assert "御 見 積 書".encode("utf-8") in response.data
    assert "車両運賃・料金".encode("utf-8") in response.data
