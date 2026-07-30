import json
import subprocess
from pathlib import Path

import pytest

from app.services.worktime_engine import calculate, request_from_json, result_to_dict


ROOT = Path(__file__).resolve().parents[1]


def _fixture_payload():
    shifts = {
        1: ("F", "08:30", "17:30", ""), 2: ("F", "08:30", "17:30", ""),
        3: ("B", "07:30", "18:05", ""), 4: ("F", "08:00", "16:00", ""),
        5: ("B", "07:30", "17:30", "legal"), 6: ("振休", None, None, ""),
        7: ("A", "07:05", "21:40", ""), 8: ("F", "08:30", "17:30", ""),
        9: ("B", "07:30", "18:05", ""), 10: ("B", "07:30", "18:05", ""),
        11: ("G", "08:00", "15:00", ""), 13: ("有休", None, None, ""),
        14: ("B", "07:30", "18:05", ""), 15: ("B", "07:30", "18:05", ""),
        16: ("A", "07:05", "21:40", ""), 17: ("有休", None, None, ""),
        19: ("G", "07:30", "17:30", ""), 20: ("有休", None, None, ""),
        21: ("B", "07:30", "19:00", ""), 22: ("B", "07:30", "18:05", ""),
        23: ("B", "07:30", "18:05", ""), 24: ("B", "07:30", "18:05", ""),
        26: ("C", "08:00", "18:20", ""), 27: ("D", "07:20", "20:00", ""),
        28: ("A", "07:05", "21:40", ""), 29: ("B", "07:30", "18:05", ""),
        30: ("B", "07:30", "18:05", ""), 31: ("A", "07:05", "21:40", ""),
    }
    days = []
    for day in range(1, 32):
        code, start, end, holiday_kind = shifts.get(day, ("", None, None, ""))
        item = {
            "day": day,
            "day_type": "holiday" if day in {5, 12, 19, 20, 26} else ("saturday" if day in {4, 11, 18, 25} else "weekday"),
            "code": code,
            "holiday_kind": holiday_kind,
        }
        if start:
            item["time_override"] = {"start": start, "end": end}
        days.append(item)
    codes = [
        {"key": key, "category": "work", "times": {"weekday": None, "saturday": None, "holiday": None}}
        for key in "ABCDFG"
    ] + [
        {"key": "振休", "category": "leave", "leave_kind": "substitute_rest", "times": {}},
        {"key": "有休", "category": "leave", "leave_kind": "paid", "times": {}},
    ]
    return {
        "version": 1,
        "year": 2026,
        "month": 7,
        "codes": codes,
        "settings": {"break_minutes": 60, "scheduled_bind_minutes": 570},
        "people": [{"person_id": "mem_1", "label": "職員A", "days": days}],
    }


def test_design_fixture_matches_every_month_total_and_warning():
    result = result_to_dict(calculate(request_from_json(_fixture_payload())))
    person = result["people"][0]
    totals = person["totals"]
    assert totals == {
        **totals,
        "calendar_days": 31,
        "work_days": 24,
        "bind_total_minutes": 15640,
        "break_total_minutes": 1440,
        "work_total_minutes": 14200,
        "payroll_overtime_minutes": 2260,
        "scheduled_holiday_work_minutes": 0,
        "legal_holiday_work_minutes": 540,
        "payroll_excess_total_minutes": 2800,
        "anei_base_minutes": 10620,
        "anei_excess_minutes": 3580,
        "leave_counts": {"substitute_rest": 1, "paid": 3, "empty": 3},
        "max_consecutive_work_days": 6,
    }
    assert [v["day"] for v in person["violations"] if v["code"] == "KAIZEN_DAILY_BIND"] == [7, 16, 28, 31]
    assert not [v for v in person["violations"] if v["code"] != "KAIZEN_DAILY_BIND"]


@pytest.mark.parametrize(
    ("day_type", "code", "override", "bind", "warning"),
    [
        ("weekday", "X", None, 540, None),
        ("saturday", "X", None, 360, None),
        ("holiday", "X", None, 540, "TIME_SET_FALLBACK"),
        ("weekday", "Y", None, 0, "TIME_UNDEFINED"),
        ("weekday", "Y", {"start": "10:00", "end": "16:00"}, 360, None),
        ("weekday", "Z", None, 0, "CODE_UNDEFINED"),
    ],
)
def test_time_resolution_cases(day_type, code, override, bind, warning):
    payload = {
        "year": 2026,
        "month": 7,
        "codes": [
            {"key": "X", "category": "work", "times": {"weekday": {"start": "09:00", "end": "18:00"}, "saturday": {"start": "09:00", "end": "15:00"}, "holiday": None}},
            {"key": "Y", "category": "work", "times": {"weekday": None, "saturday": None, "holiday": None}},
        ],
        "people": [{"person_id": "p", "days": [{"day": 1, "day_type": day_type, "code": code, "time_override": override}]}],
    }
    day = calculate(request_from_json(payload)).people[0].days[0]
    assert day.bind_minutes == bind
    assert (warning in day.warnings) if warning else not day.warnings


def test_javascript_mirror_matches_design_fixture_totals():
    payload = _fixture_payload()
    person = payload["people"][0]
    config = {
        "members": [{"id": "mem_1", "display_name": "職員A", "active": True}],
        "codes": payload["codes"],
        "settings": payload["settings"],
    }
    entries = {}
    for day in person["days"]:
        if not day["code"]:
            entries[str(day["day"])] = []
            continue
        entries[str(day["day"])] = [{
            "member_id": "mem_1",
            "value": day["code"],
            "holiday_kind": day["holiday_kind"],
            "time_override": day.get("time_override"),
        }]
    js_input = {"year": 2026, "month": 7, "config": config, "entries_per_day": entries, "holidays": ["2026-07-20"]}
    script = "const c=require('./app/static/cloudshift/js/cloudshift_large_calc.js'); process.stdout.write(JSON.stringify(c.calculate(JSON.parse(process.argv[1]))));"
    completed = subprocess.run(
        ["node", "-e", script, json.dumps(js_input, ensure_ascii=False)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    totals = result["people"][0]["totals"]
    assert (totals["bind_total_minutes"], totals["work_total_minutes"], totals["payroll_excess_total_minutes"]) == (15640, 14200, 2800)
    assert [v["day"] for v in result["violations"] if v["code"] == "KAIZEN_DAILY_BIND"] == [7, 16, 28, 31]


@pytest.mark.parametrize(
    ("holiday_kind", "override", "category", "bind", "work", "overtime"),
    [
        # 時刻上書きなしの外部勤務のみ: 従来どおり集計対象外（external）
        ("", None, "external", 0, 0, 0),
        # 時刻上書きあり: 勤務として拘束・労働・残業を集計する
        ("", {"start": "08:00", "end": "18:30"}, "work", 630, 570, 60),
        # 休日出勤マークとの組み合わせは休日勤務として集計（残業は付かない）
        ("legal", {"start": "08:00", "end": "18:30"}, "legal_holiday_work", 630, 570, 0),
    ],
)
def test_external_only_day_counts_only_with_time_override(holiday_kind, override, category, bind, work, overtime):
    payload = {
        "year": 2026,
        "month": 7,
        "codes": [],
        "settings": {"break_minutes": 60, "scheduled_bind_minutes": 570},
        "people": [{"person_id": "p", "days": [{
            "day": 1,
            "day_type": "weekday",
            "holiday_kind": holiday_kind,
            "has_external_assignment": True,
            "time_override": override,
        }]}],
    }
    day = calculate(request_from_json(payload)).people[0].days[0]
    assert (day.category, day.bind_minutes, day.work_minutes, day.overtime_minutes) == (category, bind, work, overtime)
    if override:
        assert (day.start_minutes, day.end_minutes) == (480, 1110)

    entry = {
        "member_id": "p",
        "assignments": [{"code_key": "他現場", "source_type": "sync"}],
        "holiday_kind": holiday_kind,
        "time_override": override,
    }
    config = {"members": [{"id": "p", "display_name": "職員"}], "codes": [], "settings": payload["settings"]}
    js_input = {"year": 2026, "month": 7, "config": config, "entries_per_day": {"1": [entry]}}
    script = "const c=require('./app/static/cloudshift/js/cloudshift_large_calc.js'); process.stdout.write(JSON.stringify(c.calculate(JSON.parse(process.argv[1]))));"
    completed = subprocess.run(["node", "-e", script, json.dumps(js_input, ensure_ascii=False)], cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=True)
    js_day = json.loads(completed.stdout)["people"][0]["days"][0]
    assert (js_day["category"], js_day["bind_minutes"], js_day["work_minutes"], js_day["overtime_minutes"]) == (category, bind, work, overtime)


def test_external_day_time_override_uses_member_and_day_scheduled_bind():
    payload = {
        "year": 2026,
        "month": 7,
        "codes": [],
        "settings": {"break_minutes": 60, "scheduled_bind_minutes": 570},
        "people": [{
            "person_id": "p",
            "scheduled_bind_minutes": 600,
            "days": [
                {"day": 1, "day_type": "weekday", "has_external_assignment": True,
                 "time_override": {"start": "08:00", "end": "19:00"}},
                {"day": 2, "day_type": "weekday", "has_external_assignment": True,
                 "time_override": {"start": "08:00", "end": "19:00"}, "bind_override_minutes": 630},
            ],
        }],
    }
    days = calculate(request_from_json(payload)).people[0].days
    # 1日: 個人所定拘束600分 -> 660-600=60分残業 / 2日: 日別上書き630分 -> 30分残業
    assert (days[0].overtime_minutes, days[1].overtime_minutes) == (60, 30)


def test_multiple_work_codes_use_union_time_and_warn_on_overlap():
    payload = {
        "year": 2026, "month": 7,
        "codes": [
            {"key": "午前", "category": "work", "times": {"weekday": {"start": "09:00", "end": "12:00"}}},
            {"key": "午後", "category": "work", "times": {"weekday": {"start": "11:00", "end": "18:00"}}},
        ],
        "settings": {"break_minutes": 60, "scheduled_bind_minutes": 570},
        "people": [{"person_id": "p", "days": [{
            "day": 1, "day_type": "weekday",
            "assignments": [{"code_key": "午前"}, {"code_key": "午後"}],
        }]}],
    }
    day = calculate(request_from_json(payload)).people[0].days[0]
    assert day.code_key == "午前 + 午後"
    assert day.bind_minutes == 540
    assert day.work_minutes == 480
    assert "TIME_OVERLAP" in day.warnings

    config = {"members": [{"id": "p", "display_name": "職員"}], "codes": payload["codes"], "settings": payload["settings"]}
    js_input = {"year": 2026, "month": 7, "config": config, "entries_per_day": {"1": [{"member_id": "p", "assignments": payload["people"][0]["days"][0]["assignments"]}]}}
    script = "const c=require('./app/static/cloudshift/js/cloudshift_large_calc.js'); process.stdout.write(JSON.stringify(c.calculate(JSON.parse(process.argv[1]))));"
    completed = subprocess.run(["node", "-e", script, json.dumps(js_input, ensure_ascii=False)], cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=True)
    js_day = json.loads(completed.stdout)["people"][0]["days"][0]
    assert (js_day["bind_minutes"], js_day["work_minutes"]) == (540, 480)
    assert "TIME_OVERLAP" in js_day["warnings"]
