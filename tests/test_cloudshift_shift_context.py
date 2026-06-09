"""CloudShift context adapter と apply 層の統合テスト。

DB/Flask が無くても劣化動作するよう設計しているため、ここでは素の project dict で検証する
（Employee 不在時は在籍維持＋warning、専従/他現場/有休は空へ縮退）。
"""

from __future__ import annotations

from datetime import date

from app.services import cloudshift_shift_engine as e
from app.services import cloudshift_shift_context as ctx
from app.services import cloudshift_shift_apply as apply_mod


YEAR, MONTH = 2026, 7
MONTH_KEY = "2026-07"


def base_project(**months_extra):
    return {
        "id": "P1",
        "owner_user_id": "u1",
        "site_row_id": "10",
        "site_id": "S1",
        "site_name": "A現場",
        "assist": {
            "experienced_sites": [{"employee_number": "E001", "site_row_id": "10"}],
            "training_sites": [{"employee_number": "E003", "site_row_id": "10"}],
            "records": [{"employee_number": "E001", "shift_key": "A"}],
            "profiles": [
                {"employee_number": "E001", "preferred_weekdays": [0], "blocked_weekdays": []},
            ],
        },
        "months": {MONTH_KEY: {"revision": 3, "entries_per_day": {}, **months_extra}},
    }


def test_workers_built_from_assist():
    project = base_project()
    settings, _ = e.migrate_settings(project.get("shift_engine"))
    warnings: list = []
    workers = ctx.build_workers(project, settings, warnings)
    by_number = {w.employee_number: w for w in workers}
    assert "E001" in by_number and "E003" in by_number
    e1 = by_number["E001"]
    assert "10" in e1.experienced_site_row_ids
    assert "A" in e1.experienced_option_keys
    assert e1.active is True
    assert "10" in by_number["E003"].trainee_site_row_ids
    # Employee 不在のため warning が出る
    assert any(w.code == "assist_without_employee" for w in warnings)


def test_capacity_fallback_demand():
    project = base_project(capacity_enabled=True, required_capacity=2)
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    assert all(s.source == "required_capacity_fallback" for s in request.required_slots)
    assert request.required_slots[0].required_count == 2
    # 全日に展開
    assert len(request.required_slots) == 31


def test_demand_rules_take_priority():
    project = base_project(capacity_enabled=True, required_capacity=5)
    # 月曜(weekday=0)だけ A を 1 枠
    project["shift_engine"] = {
        "version": 1,
        "demand_rules": [
            {"rule_id": "r1", "enabled": True, "weekdays": [0], "include_holidays": False,
             "shift_key": "A", "required_count": 1}
        ],
    }
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    assert all(s.source == "settings" for s in request.required_slots)
    # 2026-07 の月曜は 6,13,20,27。ただし 7/20 は海の日(祝日)で include_holidays=False のため除外 → 3 日
    slot_dates = {s.date for s in request.required_slots}
    assert len(request.required_slots) == 3
    assert date(2026, 7, 20) not in slot_dates  # 祝日は除外される
    assert all(s.shift_key == "A" for s in request.required_slots)


def test_prev_month_estimate_demand():
    project = base_project()
    # 前月(2026-06)に確定実績を入れる: 各日 1 件
    prev_entries = {str(d): [{"id": f"p{d}", "value": "!A!佐藤", "employee_number": "E001"}] for d in range(1, 31)}
    project["months"]["2026-06"] = {"entries_per_day": prev_entries}
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    assert request.required_slots, "前月推定で需要が作られるべき"
    assert all(s.source == "prev_month_estimate" for s in request.required_slots)
    assert any(w.code == "prev_month_estimate" for w in warnings)


def test_existing_lock_policy_manual_vs_synced():
    project = base_project(entries_per_day={
        "1": [
            {"id": "m1", "value": "!A!佐藤", "employee_number": "E001"},          # 手入力
            {"id": "s1", "value": "!A!鈴木", "employee_number": "E002", "sync_source_type": "person_sync"},  # 同期
        ]
    })
    settings, _ = e.migrate_settings(project.get("shift_engine"))
    warnings: list = []
    existing = ctx.build_existing_assignments(
        project, project["months"][MONTH_KEY], YEAR, MONTH, "lock_manual", warnings
    )
    policies = {a.employee_number: a.lock_policy for a in existing}
    assert policies["E001"] == "manual_locked"
    assert policies["E002"] == "movable"


def test_unavailable_without_number_warns():
    settings, _ = e.migrate_settings(None)
    warnings: list = []
    # calendar_ids 無しなら空（leave_mgr を読まない）
    days = ctx.build_unavailable_days([], YEAR, MONTH, settings, "soft", warnings)
    assert days == []


def test_end_to_end_request_plan_apply():
    """adapter → engine → apply の通し。"""
    project = base_project(capacity_enabled=True, required_capacity=1,
                           entries_per_day={"1": [{"id": "lock1", "value": "!A!佐藤", "employee_number": "E001"}]})
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    result = e.plan_shifts(request)
    assert result.status in ("feasible", "partial")
    # 既存ロック(E001 1日)が保持される
    locked = [a for a in result.assignments if a.source == "existing_locked"]
    assert any(a.employee_number == "E001" and a.day == 1 for a in locked)

    draft = apply_mod.build_draft_entries(request, result)
    # 値は必ず非空
    for entries in draft.values():
        for entry in entries:
            assert entry["value"], "value は非空でなければならない"
            assert entry["employee_number"]
    # 1日目は元の entry_id を維持
    day1 = draft.get("1") or []
    assert any(en["id"] == "lock1" for en in day1)


def test_apply_encodes_option_value():
    project = base_project()
    project["shift_engine"] = {
        "version": 1,
        "demand_rules": [
            {"rule_id": "r1", "enabled": True, "weekdays": [0, 1, 2, 3, 4, 5, 6],
             "include_holidays": True, "shift_key": "A", "required_count": 1}
        ],
    }
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    result = e.plan_shifts(request)
    draft = apply_mod.build_draft_entries(request, result)
    # option 付きは !A!氏名 形式
    any_entry = next(en for entries in draft.values() for en in entries)
    assert any_entry["value"].startswith("!A!")


def test_request_hash_changes_with_overrides():
    project = base_project(capacity_enabled=True, required_capacity=1)
    r1, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    r2, _, _ = ctx.build_planning_request(project, YEAR, MONTH,
                                          plan_overrides={"eligibility_baseline": "any"})
    assert e.compute_request_hash(r1) != e.compute_request_hash(r2)
