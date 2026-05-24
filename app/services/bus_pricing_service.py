from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .bus_pricing_master import BLOCK_MASTER, CAR_MASTER


JST = timezone(timedelta(hours=9))


class BusPricingInputError(ValueError):
    def __init__(self, field_name: str, message: str):
        super().__init__(message)
        self.field = field_name
        self.message = message


@dataclass
class AuditEntry:
    audit_id: str
    level: str
    message: str


@dataclass
class CalcResult:
    applied_rates: dict
    calc_factors: dict
    calculation_breakdown: dict
    audit_logs: list[AuditEntry] = field(default_factory=list)
    annual_contract: dict = field(default_factory=dict)
    detail_formulas: dict = field(default_factory=dict)
    vehicle_items: list[dict] = field(default_factory=list)
    expense_items: list[dict] = field(default_factory=list)
    quote_summary: dict = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(a.level == "error" for a in self.audit_logs)

    @property
    def has_warnings(self) -> bool:
        return any(a.level == "warning" for a in self.audit_logs)


def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def yen_half_up(x: float) -> int:
    return round_half_up(x)


def validate_params(
    params: dict[str, Any],
    block_master: dict[str, dict] | None = None,
    car_master: dict[str, dict] | None = None,
) -> dict[str, Any]:
    block_master = block_master or BLOCK_MASTER
    car_master = car_master or CAR_MASTER
    clean = dict(params)
    _require_choice(clean, "block_id", block_master, "運輸局ブロックを選択してください。")
    _require_choice(clean, "car_type", car_master, "車種を選択してください。")

    clean["running_distance_km"] = _as_float(clean, "running_distance_km", "総走行距離を入力してください。")
    clean["pax_running_distance_km"] = _as_float(clean, "pax_running_distance_km", "実車距離を入力してください。")
    clean["running_duration_min"] = _as_int(clean, "running_duration_min", "総走行時間を入力してください。")
    clean["steering_minutes"] = _as_int(
        clean,
        "steering_minutes",
        "実ハンドル時間を入力してください。",
        default=0,
    )
    clean["pax_count"] = _as_int(clean, "pax_count", "乗車予定人数を入力してください。")
    clean["night_minutes"] = _as_int(clean, "night_minutes", "深夜早朝時間を入力してください。", default=0)
    clean["operating_days"] = _as_int(clean, "operating_days", "乗務日数を入力してください。", default=1)
    clean["ferry_duration_min"] = _as_int(clean, "ferry_duration_min", "フェリー乗船時間を入力してください。", default=0)
    clean["annual_operating_days"] = _as_int(clean, "annual_operating_days", "年間契約日数を入力してください。", default=0)
    clean["special_vehicle_rate"] = _as_float(clean, "special_vehicle_rate", "特殊車両割増率を入力してください。", default=0.0)
    clean["annual_operating_days"] = _as_int(clean, "annual_operating_days", "年間の運行予定日数を入力してください。", default=0)
    clean["custom_dist_rate"] = _as_float(clean, "custom_dist_rate", "キロ単価を入力してください。", default=0.0)
    clean["custom_time_rate"] = _as_float(clean, "custom_time_rate", "時間単価を入力してください。", default=0.0)
    clean["custom_utilization_rate"] = _as_float(clean, "custom_utilization_rate", "稼働率を入力してください。", default=0.0)

    for key in (
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
    ):
        clean[key] = bool(clean.get(key))
    clean["utilization_rate_mode"] = str(clean.get("utilization_rate_mode") or "official")

    _range(clean["running_distance_km"], "running_distance_km", 0, 2000, "総走行距離は 0km 超、2000km 以下で入力してください。")
    _range(clean["pax_running_distance_km"], "pax_running_distance_km", 0, clean["running_distance_km"], "実車距離は総走行距離以下で入力してください。", include_min=True)
    _range(clean["running_duration_min"], "running_duration_min", 0, 14400, "総走行時間は 1分以上、14400分以下で入力してください。")
    _range(clean["steering_minutes"], "steering_minutes", 0, clean["running_duration_min"], "実ハンドル時間は総走行時間以下で入力してください。", include_min=True)
    _range(clean["pax_count"], "pax_count", 1, 100, "乗車予定人数は 1〜100 名で入力してください。", include_min=True)
    _range(clean["night_minutes"], "night_minutes", 0, 14400, "深夜早朝時間は 0〜14400 分で入力してください。", include_min=True)
    _range(clean["operating_days"], "operating_days", 1, 30, "乗務日数は 1〜30 日で入力してください。", include_min=True)
    _range(clean["ferry_duration_min"], "ferry_duration_min", 0, 1440, "フェリー乗船時間は 0〜1440 分で入力してください。", include_min=True)
    _range(clean["special_vehicle_rate"], "special_vehicle_rate", 0, 0.5, "特殊車両割増率は 0〜0.5 の範囲で入力してください。", include_min=True)
    _range(clean["annual_operating_days"], "annual_operating_days", 0, 365, "年間の運行予定日数は 0〜365 日で入力してください。", include_min=True)

    if clean["is_overnight"] and clean["operating_days"] < 2:
        raise BusPricingInputError("operating_days", "宿泊運行の場合、乗務日数は 2 日以上にしてください。")
    if clean["is_ferry_used"] and clean["ferry_duration_min"] <= 0:
        raise BusPricingInputError("ferry_duration_min", "フェリー利用ありの場合、乗船時間を入力してください。")
    if clean["is_night_run"] and clean["night_minutes"] <= 0:
        raise BusPricingInputError("night_minutes", "深夜早朝運行ありの場合、対象時間を入力してください。")
    if clean["is_annual_contract"] and clean["annual_operating_days"] <= 0:
        raise BusPricingInputError("annual_operating_days", "年間契約を使う場合は、運行予定日数を入力してください。")
    if clean["is_annual_contract"] and clean["is_school_bus"]:
        _range(clean["annual_operating_days"], "annual_operating_days", 170, 365, "スクールバスの運行予定日数は 170〜365 日で入力してください。", include_min=True)
    if clean["utilization_rate_mode"] not in {"official", "company", "custom"}:
        raise BusPricingInputError("utilization_rate_mode", "稼働率の選択が不正です。")
    if clean["is_annual_contract"] and clean["utilization_rate_mode"] == "custom":
        block = block_master[clean["block_id"]]
        official_rate = float(block["util_rate"])
        company_rate = float(block.get("company_util_rate", official_rate))
        min_rate = min(official_rate, company_rate)
        max_rate = max(official_rate, company_rate)
        if min_rate == max_rate:
            _range(
                clean["custom_utilization_rate"],
                "custom_utilization_rate",
                min_rate,
                max_rate,
                f"稼働率は {min_rate * 100:.2f}% で入力してください。",
                include_min=True,
            )
        else:
            _range(
                clean["custom_utilization_rate"],
                "custom_utilization_rate",
                min_rate,
                max_rate,
                f"稼働率は運輸局稼働率 {official_rate * 100:.2f}% と会社稼働率 {company_rate * 100:.2f}% の間で入力してください。",
                include_min=True,
            )
    if clean["use_floor_rates"]:
        block = block_master[clean["block_id"]]
        clean["custom_dist_rate"] = block["dist_rate"][clean["car_type"]]
        clean["custom_time_rate"] = block["time_rate"][clean["car_type"]]
    else:
        _range(clean["custom_dist_rate"], "custom_dist_rate", 0, 100000, "キロ単価は 0 円/km 超で入力してください。")
        _range(clean["custom_time_rate"], "custom_time_rate", 0, 1000000, "時間単価は 0 円/時 超で入力してください。")

    clean["vehicle_items"] = _normalize_vehicle_items(clean, block_master, car_master)
    clean["expense_items"] = _normalize_expense_items(clean)

    return clean


def _normalize_vehicle_items(clean: dict[str, Any], block_master: dict[str, dict], car_master: dict[str, dict]) -> list[dict]:
    raw_items = clean.get("vehicle_items") or []
    if not isinstance(raw_items, list):
        raw_items = []
    block = block_master[clean["block_id"]]
    items: list[dict] = []

    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        quantity = _as_int(raw, "quantity", f"車両{index}の台数を入力してください。", default=0)
        if quantity <= 0:
            continue
        car_type = str(raw.get("car_type") or clean["car_type"])
        if car_type not in car_master:
            raise BusPricingInputError("vehicle_items", f"車両{index}の車種を選択してください。")
        pax_count = _as_int(raw, "pax_count", f"車両{index}の乗車人数を入力してください。", default=clean["pax_count"])
        _range(quantity, "vehicle_items", 0, 99, f"車両{index}の台数は 1〜99 台で入力してください。", include_min=False)
        _range(pax_count, "vehicle_items", 0, 100, f"車両{index}の乗車人数は 1〜100 名で入力してください。")
        if clean["use_floor_rates"]:
            dist_rate = block["dist_rate"][car_type]
            time_rate = block["time_rate"][car_type]
        else:
            dist_rate = _as_float(raw, "custom_dist_rate", f"車両{index}のキロ単価を入力してください。", default=clean["custom_dist_rate"])
            time_rate = _as_float(raw, "custom_time_rate", f"車両{index}の時間単価を入力してください。", default=clean["custom_time_rate"])
            _range(dist_rate, "vehicle_items", 0, 100000, f"車両{index}のキロ単価は 0 円/km 超で入力してください。")
            _range(time_rate, "vehicle_items", 0, 1000000, f"車両{index}の時間単価は 0 円/時 超で入力してください。")
        items.append(
            {
                "line_no": len(items) + 1,
                "label": str(raw.get("label") or "").strip(),
                "car_type": car_type,
                "quantity": quantity,
                "pax_count": pax_count,
                "custom_dist_rate": dist_rate,
                "custom_time_rate": time_rate,
            }
        )

    if not items:
        items.append(
            {
                "line_no": 1,
                "label": "",
                "car_type": clean["car_type"],
                "quantity": 1,
                "pax_count": clean["pax_count"],
                "custom_dist_rate": clean["custom_dist_rate"],
                "custom_time_rate": clean["custom_time_rate"],
            }
        )

    return items


def _normalize_expense_items(clean: dict[str, Any]) -> list[dict]:
    raw_items = clean.get("expense_items") or []
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        amount = _as_int(raw, "amount", f"実費{index}の金額を入力してください。", default=0)
        if amount <= 0 and not name:
            continue
        if amount <= 0 and name:
            continue
        if not name:
            raise BusPricingInputError("expense_items", f"実費{index}の項目名を入力してください。")
        _range(amount, "expense_items", 0, 100000000, f"実費{index}の金額は 0 円以上で入力してください。", include_min=True)
        items.append(
            {
                "line_no": len(items) + 1,
                "name": name,
                "amount_including_tax": amount,
                "note": str(raw.get("note") or "").strip(),
            }
        )
    return items


def calculate(
    params: dict[str, Any],
    block_master: dict[str, dict] | None = None,
    car_master: dict[str, dict] | None = None,
) -> CalcResult:
    block_master = block_master or BLOCK_MASTER
    car_master = car_master or CAR_MASTER
    params = validate_params(params, block_master, car_master)
    block = block_master[params["block_id"]]
    vehicle_lines = []
    for item in params["vehicle_items"]:
        line_params = dict(params)
        line_params.update(
            {
                "car_type": item["car_type"],
                "pax_count": item["pax_count"],
                "custom_dist_rate": item["custom_dist_rate"],
                "custom_time_rate": item["custom_time_rate"],
            }
        )
        vehicle_lines.append(
            _calculate_vehicle_line(
                params=line_params,
                block=block,
                car=car_master[item["car_type"]],
                quantity=item["quantity"],
                line_no=item["line_no"],
                line_label=item["label"],
            )
        )

    primary = vehicle_lines[0]
    quote_summary = _build_quote_summary(vehicle_lines, params["expense_items"])
    audits = []
    for line in vehicle_lines:
        prefix = f"車両{line['line_no']} {line['car_label']}"
        if line["quantity"] > 1:
            prefix += f" {line['quantity']}台"
        for audit in line["audit_logs"]:
            audits.append(AuditEntry(audit.audit_id, audit.level, f"{prefix}: {audit.message}"))

    calculation_breakdown = dict(primary["calculation_breakdown"])
    calculation_breakdown.update(
        {
            "vehicle_fare_including_tax": quote_summary["vehicle_fare_including_tax"],
            "vehicle_public_floor_including_tax": quote_summary["vehicle_public_floor_including_tax"],
            "expense_total_including_tax": quote_summary["expense_total_including_tax"],
            "quote_total_including_tax": quote_summary["quote_total_including_tax"],
            "quote_rounded_total_including_tax": quote_summary["quote_rounded_total_including_tax"],
            "below_floor_error": quote_summary["below_floor_error"],
            "shortfall_including_tax": quote_summary["shortfall_including_tax"],
        }
    )

    return CalcResult(
        applied_rates=primary["applied_rates"],
        calc_factors=primary["calc_factors"],
        calculation_breakdown=calculation_breakdown,
        audit_logs=audits,
        annual_contract=primary["annual_contract"],
        detail_formulas=primary["detail_formulas"],
        vehicle_items=vehicle_lines,
        expense_items=params["expense_items"],
        quote_summary=quote_summary,
    )


def _calculate_vehicle_line(
    *,
    params: dict[str, Any],
    block: dict,
    car: dict,
    quantity: int,
    line_no: int,
    line_label: str,
) -> dict[str, Any]:
    h_run = params["running_duration_min"] / 60.0
    h_base = max(3.0, h_run)

    excluded_ferry = 0.0
    if params["is_ferry_used"] and params.get("ferry_rest_ok"):
        excluded_ferry = min(8.0, params["ferry_duration_min"] / 60.0)
    h_drive = max(0.0, h_base - excluded_ferry)
    h_prep = params["operating_days"] * 2.0 if params["is_overnight"] else 2.0
    h_gross_pre = h_drive + h_prep

    h_total = round_half_up(h_gross_pre)
    h_drive_unit = round_half_up(h_drive)
    d_total = math.ceil(params["running_distance_km"] / 10.0) * 10
    alternate_plan_reasons = _alternate_driver_plan_reasons(params, h_gross_pre)
    is_two_man = params["is_alternate_driver"]

    public_r_dist = block["dist_rate"][params["car_type"]]
    public_r_time = block["time_rate"][params["car_type"]]
    r_dist = params["custom_dist_rate"]
    r_time = params["custom_time_rate"]
    f_base_dist = d_total * r_dist
    f_base_time = h_total * r_time
    f_base = f_base_dist + f_base_time

    h_night = 0
    f_night = 0
    if params["is_night_run"] and params["night_minutes"] > 0:
        h_night = round_half_up(params["night_minutes"] / 60.0)
        f_night = yen_half_up(h_night * r_time * 0.20)

    f_alt = 0
    if is_two_man and car["alt_allowed"]:
        f_alt = d_total * block["alt_dist_rate"] + yen_half_up(h_drive_unit * block["alt_time_rate"])
        if h_night:
            f_alt += yen_half_up(h_night * block["alt_time_rate"] * 0.20)

    f_special = 0
    if params["is_special_vehicle"]:
        f_special = yen_half_up(f_base * min(0.5, params["special_vehicle_rate"]))

    raw_ex_tax = f_base + f_night + f_alt + f_special
    tax = yen_half_up(raw_ex_tax * 0.10)
    legal_inc_tax = raw_ex_tax + tax
    billing = math.ceil(legal_inc_tax / 1000) * 1000

    public_base_dist = d_total * public_r_dist
    public_base_time = h_total * public_r_time
    public_base = public_base_dist + public_base_time
    public_night = yen_half_up(h_night * public_r_time * 0.20) if h_night else 0
    public_special = yen_half_up(public_base * min(0.5, params["special_vehicle_rate"])) if params["is_special_vehicle"] else 0
    public_raw_ex_tax = public_base + public_night + f_alt + public_special
    public_tax = yen_half_up(public_raw_ex_tax * 0.10)
    public_inc_tax = public_raw_ex_tax + public_tax
    below_floor = legal_inc_tax < public_inc_tax

    audits = run_compliance_audits(
        params=params,
        car=car,
        is_two_man=is_two_man,
        alternate_plan_reasons=alternate_plan_reasons,
        h_gross_pre=h_gross_pre,
        running_minutes=params["running_duration_min"],
        steering_minutes=params["steering_minutes"],
    )

    annual = _annual_contract(params, block, raw_ex_tax)
    if annual.get("is_applied"):
        per_vehicle_quote_inc = annual["annual_limit_including_tax"]
        public_per_vehicle_quote_inc = yen_half_up(public_raw_ex_tax * annual["calculated_operating_days"] * 1.10)
    else:
        per_vehicle_quote_inc = legal_inc_tax
        public_per_vehicle_quote_inc = public_inc_tax

    details = _build_detail_formulas(
        params=params,
        d_total=d_total,
        h_total=h_total,
        h_run=h_run,
        h_base=h_base,
        h_drive=h_drive,
        h_prep=h_prep,
        h_gross_pre=h_gross_pre,
        h_drive_unit=h_drive_unit,
        excluded_ferry=excluded_ferry,
        h_night=h_night,
        r_dist=r_dist,
        r_time=r_time,
        f_base_dist=f_base_dist,
        f_base_time=f_base_time,
        f_night=f_night,
        f_alt=f_alt,
        f_special=f_special,
        raw_ex_tax=raw_ex_tax,
        tax=tax,
        legal_inc_tax=legal_inc_tax,
        billing=billing,
        public_inc_tax=public_inc_tax,
        below_floor=below_floor,
        annual=annual,
        block=block,
    )

    calculation_breakdown = {
        "base_distance_fare": f_base_dist,
        "base_time_fare": f_base_time,
        "base_fare_subtotal": f_base,
        "night_surcharge": f_night,
        "alternate_driver_fare": f_alt,
        "special_vehicle_surcharge": f_special,
        "raw_total_excluding_tax": raw_ex_tax,
        "consumption_tax": tax,
        "legal_minimum_including_tax": legal_inc_tax,
        "recommended_billable_including_tax": legal_inc_tax,
        "approved_rounded_billing_fare": billing,
        "public_floor_excluding_tax": public_raw_ex_tax,
        "public_floor_including_tax": public_inc_tax,
        "below_floor_error": below_floor,
        "shortfall_including_tax": max(0, public_per_vehicle_quote_inc - per_vehicle_quote_inc),
    }

    return {
        "line_no": line_no,
        "label": line_label,
        "car_type": params["car_type"],
        "car_label": car["label"],
        "quantity": quantity,
        "pax_count": params["pax_count"],
        "dist_unit_yen_per_km": r_dist,
        "time_unit_yen_per_hour": r_time,
        "per_vehicle_quote_including_tax": per_vehicle_quote_inc,
        "line_quote_including_tax": per_vehicle_quote_inc * quantity,
        "per_vehicle_public_floor_including_tax": public_per_vehicle_quote_inc,
        "line_public_floor_including_tax": public_per_vehicle_quote_inc * quantity,
        "line_shortfall_including_tax": max(0, public_per_vehicle_quote_inc - per_vehicle_quote_inc) * quantity,
        "below_floor_error": per_vehicle_quote_inc < public_per_vehicle_quote_inc,
        "applied_rates": {
            "dist_unit_yen_per_km": r_dist,
            "time_unit_yen_per_hour": r_time,
            "public_dist_unit_yen_per_km": public_r_dist,
            "public_time_unit_yen_per_hour": public_r_time,
            "alt_dist_unit_yen_per_km": block["alt_dist_rate"],
            "alt_time_unit_yen_per_hour": block["alt_time_rate"],
            "block_name": block["name"],
            "car_label": car["label"],
            "floor_rates_applied": params["use_floor_rates"],
        },
        "calc_factors": {
            "raw_running_hours": h_run,
            "ferry_excluded_hours": excluded_ferry,
            "drive_hours_unrounded": h_drive,
            "prep_hours": h_prep,
            "gross_hours_pre_round": h_gross_pre,
            "final_calc_hours": h_total,
            "drive_calc_hours": h_drive_unit,
            "final_calc_distance_km": d_total,
            "night_calc_hours": h_night,
            "auto_alternate_driver_applied": False,
            "auto_alternate_driver_reasons": [],
            "alternate_driver_plan_reasons": alternate_plan_reasons,
            "alternate_driver_required": is_two_man,
        },
        "calculation_breakdown": calculation_breakdown,
        "audit_logs": audits,
        "annual_contract": annual,
        "detail_formulas": details,
    }


def _build_quote_summary(vehicle_lines: list[dict], expense_items: list[dict]) -> dict[str, Any]:
    vehicle_fare = sum(line["line_quote_including_tax"] for line in vehicle_lines)
    vehicle_public_floor = sum(line["line_public_floor_including_tax"] for line in vehicle_lines)
    expense_total = sum(item["amount_including_tax"] for item in expense_items)
    rounded_vehicle_fare = math.ceil(vehicle_fare / 1000) * 1000
    quote_total = rounded_vehicle_fare + expense_total
    return {
        "vehicle_count": sum(line["quantity"] for line in vehicle_lines),
        "vehicle_line_count": len(vehicle_lines),
        "vehicle_fare_including_tax": vehicle_fare,
        "vehicle_public_floor_including_tax": vehicle_public_floor,
        "vehicle_rounded_fare_including_tax": rounded_vehicle_fare,
        "expense_total_including_tax": expense_total,
        "quote_total_including_tax": quote_total,
        "quote_rounded_total_including_tax": quote_total,
        "below_floor_error": vehicle_fare < vehicle_public_floor,
        "shortfall_including_tax": max(0, vehicle_public_floor - vehicle_fare),
        "has_expenses": bool(expense_items),
        "annual_applied": any(line["annual_contract"].get("is_applied") for line in vehicle_lines),
    }


def run_compliance_audits(
    *,
    params: dict[str, Any],
    car: dict,
    is_two_man: bool,
    alternate_plan_reasons: list[str],
    h_gross_pre: float,
    running_minutes: int,
    steering_minutes: int,
) -> list[AuditEntry]:
    audits: list[AuditEntry] = []
    rest_minutes = running_minutes - steering_minutes

    if steering_minutes > 240 and rest_minutes < 30:
        audits.append(AuditEntry("C-001", "error", "連続運転4時間超の可能性があります。少なくとも30分以上の休憩計画を確認してください。"))

    if not is_two_man and h_gross_pre > 15.0:
        audits.append(AuditEntry("C-003", "error", "ワンマン運行の1日拘束時間上限15時間を超えています。交替運転者、別車両、行程分割などの運行計画を確認してください。"))
    elif not is_two_man and h_gross_pre > 13.0:
        audits.append(AuditEntry("C-004", "warning", "ワンマン運行の拘束時間が13時間を超えています。14時間超の回数管理も確認してください。"))

    if is_two_man and h_gross_pre > 20.0:
        audits.append(AuditEntry("C-005a", "error", "ツーマン運行でも拘束時間20時間を超える計画は扱えません。"))
    elif is_two_man and h_gross_pre > 19.0 and not params["has_sleeper_bed"]:
        audits.append(AuditEntry("C-005b", "error", "ツーマン拘束19時間超には車内ベッド等の休息設備が必要です。"))
    elif is_two_man and h_gross_pre > 19.0 and params["has_sleeper_bed"]:
        audits.append(AuditEntry("C-006", "warning", "ツーマン拘束19時間超です。車内ベッド等の休息設備と実運用を必ず確認してください。"))

    if is_two_man and not car["alt_allowed"]:
        audits.append(AuditEntry("C-010", "error", "小型車・コミューターでは交替運転者配置料金を加算できません。車種または行程を見直してください。"))

    if is_two_man and car["alt_allowed"]:
        max_pax = car["max_capacity"] - car["alt_seat_cost"]
        if params["pax_count"] > max_pax:
            audits.append(AuditEntry("C-009", "error", f"交替運転者を配置すると乗車可能人数は {max_pax} 名までです。"))

    if not is_two_man and alternate_plan_reasons:
        audits.append(AuditEntry("A-PLAN", "warning", "交替運転者・別車両・行程分割などの確認が必要な条件に該当しています。交替運転者料金は自動加算していません: " + "、".join(alternate_plan_reasons)))

    return audits


def serialize_result(
    result: CalcResult,
    params: dict[str, Any] | None = None,
    block_master: dict[str, dict] | None = None,
) -> dict[str, Any]:
    meta = {
        "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
    }
    if params:
        meta.update(
            {
                "block_id": params.get("block_id"),
                "block_name": (block_master or BLOCK_MASTER).get(params.get("block_id"), {}).get("name"),
                "car_type": params.get("car_type"),
            }
        )
    return {
        "status": "success",
        "meta": meta,
        "applied_rates": result.applied_rates,
        "calc_factors": result.calc_factors,
        "calculation_breakdown": result.calculation_breakdown,
        "compliance_audit": {
            "has_errors": result.has_errors,
            "has_warnings": result.has_warnings,
            "audit_logs": [entry.__dict__ for entry in result.audit_logs],
        },
        "annual_contract_special": _default_annual_contract(result.annual_contract),
        "detail_formulas": result.detail_formulas,
        "vehicle_items": result.vehicle_items,
        "expense_items": result.expense_items,
        "quote_summary": result.quote_summary,
    }


def _alternate_driver_plan_reasons(params: dict[str, Any], h_gross_pre: float) -> list[str]:
    reasons: list[str] = []
    if h_gross_pre > 15.0:
        reasons.append("拘束時間15時間超")
    if not params["is_night_run"] and params["pax_running_distance_km"] > 500:
        reasons.append("昼間実車距離500km超")
    if params["is_night_run"] and params["pax_running_distance_km"] > 400:
        reasons.append("夜間実車距離400km超")
    if params["steering_minutes"] / 60.0 > 9.0:
        reasons.append("実ハンドル時間9時間超")
    return reasons


def _annual_contract(params: dict[str, Any], block: dict, raw_ex_tax: int) -> dict[str, Any]:
    if not params["is_annual_contract"]:
        return {}
    selected_rate = _selected_utilization_rate(params, block)
    utilization_days = math.floor(365 * selected_rate)
    planned_days = params["annual_operating_days"]
    if params["is_school_bus"]:
        days = planned_days
        basis = "運行予定日数"
    else:
        days = max(planned_days, utilization_days)
        basis = "運行予定日数" if planned_days >= utilization_days else "稼働率換算日数"
    annual_ex = raw_ex_tax * days
    return {
        "is_applied": True,
        "daily_rate_used": raw_ex_tax,
        "standard_utilization_rate": block["util_rate"],
        "company_utilization_rate": block.get("company_util_rate", block["util_rate"]),
        "selected_utilization_rate": selected_rate,
        "selectable_utilization_min": min(block["util_rate"], block.get("company_util_rate", block["util_rate"])),
        "selectable_utilization_max": max(block["util_rate"], block.get("company_util_rate", block["util_rate"])),
        "utilization_rate_mode": params["utilization_rate_mode"],
        "planned_operating_days": planned_days,
        "utilization_based_days": utilization_days,
        "calculation_basis": basis,
        "calculated_operating_days": days,
        "annual_limit_subtotal": annual_ex,
        "annual_limit_including_tax": yen_half_up(annual_ex * 1.10),
        "max_run_days_allowed": math.floor(days * 1.4),
        "discount_efficiency_percentage": round((1 - 1 / 1.4) * 1000) / 10,
    }


def _default_annual_contract(annual: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "is_applied": False,
        "daily_rate_used": 0,
        "standard_utilization_rate": 0,
        "company_utilization_rate": 0,
        "selected_utilization_rate": 0,
        "selectable_utilization_min": 0,
        "selectable_utilization_max": 0,
        "calculated_operating_days": 0,
        "annual_limit_subtotal": 0,
        "annual_limit_including_tax": 0,
        "max_run_days_allowed": 0,
        "discount_efficiency_percentage": 0,
    }
    return {**defaults, **annual}


def _selected_utilization_rate(params: dict[str, Any], block: dict) -> float:
    mode = params.get("utilization_rate_mode", "official")
    if mode == "company":
        return float(block.get("company_util_rate", block["util_rate"]))
    if mode == "custom":
        return float(params["custom_utilization_rate"])
    return float(block["util_rate"])


def _build_detail_formulas(
    *,
    params: dict[str, Any],
    d_total: int,
    h_total: int,
    h_run: float,
    h_base: float,
    h_drive: float,
    h_prep: float,
    h_gross_pre: float,
    h_drive_unit: int,
    excluded_ferry: float,
    h_night: int,
    r_dist: float,
    r_time: float,
    f_base_dist: int,
    f_base_time: int,
    f_night: int,
    f_alt: int,
    f_special: int,
    raw_ex_tax: int,
    tax: int,
    legal_inc_tax: int,
    billing: int,
    public_inc_tax: int,
    below_floor: bool,
    annual: dict[str, Any],
    block: dict,
) -> dict[str, str]:
    details = {
        "distance": f"総走行距離 {params['running_distance_km']}km を 10km 単位で切り上げ = {d_total}km。{d_total}km × キロ単価 {int(r_dist)}円 = {f_base_dist:,}円。",
        "time": f"総走行時間 {params['running_duration_min']}分 = {h_run:.2f}時間。最低保証後 {h_base:.2f}時間、フェリー控除 {excluded_ferry:.2f}時間、点呼点検 {h_prep:.2f}時間を反映し、計算時間は {h_total}時間。{h_total}時間 × 時間単価 {int(r_time)}円 = {f_base_time:,}円。",
        "night": f"深夜早朝時間を 30分単位で丸めた時間 = {h_night}時間。{h_night}時間 × 時間単価 {int(r_time)}円 × 20% = {f_night:,}円。",
        "alternate": f"交替運転者料金は、距離 {d_total}km × 交替キロ単価 {block['alt_dist_rate']}円 と、走行時間 {h_drive_unit}時間 × 交替時間単価 {block['alt_time_rate']}円を合算します。深夜早朝がある場合は交替時間単価にも20%を加算します。合計 {f_alt:,}円。",
        "special": f"特殊車両割増は、キロ制運賃と時間制運賃の小計に割増率 {params['special_vehicle_rate'] * 100:.1f}% を掛けます。計算結果 {f_special:,}円。",
        "subtotal": f"税別合計 = キロ制運賃 {f_base_dist:,}円 + 時間制運賃 {f_base_time:,}円 + 深夜早朝割増 {f_night:,}円 + 交替運転者料金 {f_alt:,}円 + 特殊車両割増 {f_special:,}円 = {raw_ex_tax:,}円。",
        "tax": f"消費税 = 税別合計 {raw_ex_tax:,}円 × 10% を1円単位で四捨五入 = {tax:,}円。税込額 = {legal_inc_tax:,}円。",
        "billing": f"千円切上げ = 税込額 {legal_inc_tax:,}円 を 1,000円単位で切り上げ = {billing:,}円。",
        "floor": f"公示下限単価で同じ条件を計算した税込下限額は {public_inc_tax:,}円です。今回の税込額は {legal_inc_tax:,}円なので、{'下限額を下回っています。' if below_floor else '下限額以上です。'}",
    }
    if annual.get("is_applied"):
        details["annual"] = (
            f"年間契約は、運輸局稼働率 {annual['standard_utilization_rate'] * 100:.2f}% と会社稼働率 "
            f"{annual['company_utilization_rate'] * 100:.2f}% の間から、今回使う稼働率 "
            f"{annual['selected_utilization_rate'] * 100:.2f}% を使います。1日の税別額 {raw_ex_tax:,}円 × "
            f"採用日数 {annual['calculated_operating_days']}日で税別年間額を出し、消費税10%を加えます。採用日数は、運行予定日数 {annual['planned_operating_days']}日 と "
            f"稼働率換算日数 {annual['utilization_based_days']}日の比較から「{annual['calculation_basis']}」を使いました。"
        )
    return details


def _require_choice(params: dict[str, Any], key: str, choices: dict, message: str) -> None:
    if params.get(key) not in choices:
        raise BusPricingInputError(key, message)


def _as_float(params: dict[str, Any], key: str, message: str, default: float | None = None) -> float:
    value = params.get(key, default)
    if value in (None, ""):
        if default is not None:
            return float(default)
        raise BusPricingInputError(key, message)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BusPricingInputError(key, message) from exc


def _as_int(params: dict[str, Any], key: str, message: str, default: int | None = None) -> int:
    value = params.get(key, default)
    if value in (None, ""):
        if default is not None:
            return int(float(default))
        raise BusPricingInputError(key, message)
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise BusPricingInputError(key, message) from exc


def _range(
    value: float,
    field_name: str,
    min_value: float,
    max_value: float,
    message: str,
    *,
    include_min: bool = False,
    include_max: bool = True,
) -> None:
    min_ok = value >= min_value if include_min else value > min_value
    max_ok = value <= max_value if include_max else value < max_value
    if not (min_ok and max_ok):
        raise BusPricingInputError(field_name, message)
