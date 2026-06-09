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


# ---------------------------------------------------------------------------
# 回帰テスト（バグ修正の固定）
# ---------------------------------------------------------------------------


def test_locked_existing_worker_added_to_candidate_pool():
    """assist に居ないロック既存の担当者も候補プールに補完され、hard 誤検出しない。"""
    project = base_project(capacity_enabled=True, required_capacity=1,
                           entries_per_day={"1": [{"id": "x", "value": "!A!外部太郎", "employee_number": "E999"}]})
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    by_number = {w.employee_number: w for w in request.workers}
    assert "E999" in by_number
    assert by_number["E999"].name == "外部太郎"
    assert "10" in by_number["E999"].experienced_site_row_ids
    res = e.plan_shifts(request)
    assert res.score.hard_violation_count == 0
    assert "E999" in [a.employee_number for a in res.assignments if a.day == 1]


def test_pinned_worker_added_to_candidate_pool():
    project = base_project(capacity_enabled=True, required_capacity=1)
    project["shift_engine"] = {
        "version": 1,
        "rules": [{"rule_id": "p1", "kind": "pinned", "enabled": True,
                   "params": {"employee_number": "E777", "day": 1, "shift_key": ""}}],
    }
    request, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    assert "E777" in {w.employee_number for w in request.workers}
    res = e.plan_shifts(request)
    assert "E777" in [a.employee_number for a in res.assignments if a.day == 1]
    assert res.score.blocker_count == 0


def test_build_request_deterministic_with_existing():
    project = base_project(capacity_enabled=True, required_capacity=2,
                           entries_per_day={"1": [{"id": "x", "value": "!A!外部太郎", "employee_number": "E999"}]})
    r1, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    r2, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    assert e.compute_request_hash(r1) == e.compute_request_hash(r2)
    res1 = e.plan_shifts(r1)
    res2 = e.plan_shifts(r2)
    assert [(a.slot_instance_id, a.employee_number) for a in res1.assignments] == \
           [(a.slot_instance_id, a.employee_number) for a in res2.assignments]


def test_coerce_site_row_id_normalizes_invalid():
    assert ctx._coerce_site_row_id("10") == "10"
    assert ctx._coerce_site_row_id("0") == ""
    assert ctx._coerce_site_row_id("") == ""
    assert ctx._coerce_site_row_id("abc") == ""
    assert ctx._coerce_site_row_id(None) == ""


def test_candidate_blocklist_excludes():
    project = base_project()
    project["shift_engine"] = {"version": 1, "advanced_options": [
        {"key": "candidate_blocklist", "enabled": True, "value": ["E001"]}]}
    request, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    assert "E001" not in {w.employee_number for w in request.workers}


def test_candidate_allowlist_restricts():
    project = base_project()
    project["shift_engine"] = {"version": 1, "advanced_options": [
        {"key": "candidate_allowlist", "enabled": True, "value": ["E003"]}]}
    request, _, _ = ctx.build_planning_request(project, YEAR, MONTH)
    assert {w.employee_number for w in request.workers} == {"E003"}


def test_allow_block_conflict_excludes_and_warns():
    project = base_project()
    project["shift_engine"] = {"version": 1, "advanced_options": [
        {"key": "candidate_allowlist", "enabled": True, "value": ["E001"]},
        {"key": "candidate_blocklist", "enabled": True, "value": ["E001"]}]}
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    assert "E001" not in {w.employee_number for w in request.workers}  # 除外側を採用
    assert any(w.code == "allow_block_conflict" for w in warnings)


# ---------------------------------------------------------------------------
# 休み（有休系オプション entry・有休共有ツール）の扱い
# ---------------------------------------------------------------------------


def test_leave_entry_locked_and_blocks_same_day_assignment():
    """対象シフト帳の有休 entry は固定保持され、その日は本人へ新規配置しない。"""
    project = base_project(capacity_enabled=True, required_capacity=1,
                           entries_per_day={
                               "1": [{"id": "lv1", "value": "!PAID!佐藤", "employee_number": "E001",
                                      "sync_source_type": "person_sync"}],
                           })
    request, settings, warnings = ctx.build_planning_request(project, YEAR, MONTH)
    # 有休 entry は同期由来でも locked
    leave = [a for a in request.existing_assignments if a.shift_key == "PAID"]
    assert leave and leave[0].lock_policy == "locked"
    # hard の不可日になっている
    assert any(u.employee_number == "E001" and u.date == date(YEAR, MONTH, 1)
               and u.strength == "hard" and u.source == "shift_entry"
               for u in request.unavailable_days)

    result = e.plan_shifts(request)
    assert result.score.blocker_count == 0
    day1 = [a for a in result.assignments if a.day == 1]
    # 有休 entry は保持されるが、E001 への勤務シフトは入らない
    assert any(a.shift_key == "PAID" and a.employee_number == "E001" for a in day1)
    assert not any(a.shift_key != "PAID" and a.employee_number == "E001" for a in day1)
    # 下書きにも有休 entry が残る
    draft = apply_mod.build_draft_entries(request, result)
    assert any(en["value"] == "!PAID!佐藤" for en in draft.get("1", []))


def test_external_leave_entry_becomes_hard_unavailable(monkeypatch):
    """他の現場シフト帳に入っている休みは hard の不可日になる。"""
    from app.tools import cloudshift as cs

    def fake_conflicts(project, target_date):
        if target_date == date(YEAR, MONTH, 2):
            return [{"project_id": "P2", "project_title": "B現場", "shift_key": "PAID",
                     "shift_label": "有休", "entry_name": "佐藤", "employee_number": "E001"}]
        return []

    monkeypatch.setattr(cs, "_assist_scene_conflict_entries", fake_conflicts)
    warnings: list = []
    external, leave_days = ctx.build_external_assignments(base_project(), YEAR, MONTH, warnings)
    assert external == []
    assert len(leave_days) == 1
    u = leave_days[0]
    assert (u.employee_number, u.date, u.strength, u.source) == \
        ("E001", date(YEAR, MONTH, 2), "hard", "shift_entry")


def test_person_project_leave_days(monkeypatch):
    """同 owner の個人シフト帳の休みは hard の不可日になる。"""
    from app.tools import cloudshift as cs

    person_project = {
        "id": "PP1",
        "mode": "person",
        "owner_user_id": "u1",
        "employee_number": "E001",
        "title": "佐藤の個人シフト",
        "months": {MONTH_KEY: {"entries_per_day": {
            "3": [{"id": "x", "value": "!COMP!代休"}],
            "4": [{"id": "y", "value": "!A!A現場"}],
        }}},
    }
    monkeypatch.setattr(cs, "_iter_stored_projects", lambda: [person_project])
    leave_days = ctx.build_person_project_leave_days(base_project(), YEAR, MONTH)
    assert len(leave_days) == 1
    assert leave_days[0].employee_number == "E001"
    assert leave_days[0].date == date(YEAR, MONTH, 3)
    assert leave_days[0].strength == "hard"


def test_unknown_leave_type_defaults_to_hard(monkeypatch):
    """ポリシー未定義の休暇種別も既定で hard（休みの日に配置しない）。"""
    from app.tools import leave_mgr

    monkeypatch.setattr(leave_mgr, "load_calendar_data", lambda cid, ym: {
        "leaves": [
            {"date": f"{YEAR}-{MONTH:02d}-05", "employee_number": "E001",
             "leave_type": "特別休暇", "confirmed_by": "admin"},
            {"date": f"{YEAR}-{MONTH:02d}-06", "employee_number": "E001",
             "leave_type": "有休", "confirmed_by": ""},  # 未確認
        ]
    })
    settings, _ = e.migrate_settings(None)
    warnings: list = []
    days = ctx.build_unavailable_days(["cal1"], YEAR, MONTH, settings,
                                      e.default_planning_preferences().unconfirmed_leave_strength,
                                      warnings)
    strengths = {(u.date.day): u.strength for u in days}
    assert strengths[5] == "hard"  # 未知の種別
    assert strengths[6] == "hard"  # 未確認も既定 hard


# ---------------------------------------------------------------------------
# 対象日指定（fill_target_days）
# ---------------------------------------------------------------------------


def test_coerce_fill_target_dates():
    dates = ctx._coerce_fill_target_dates([1, "2", f"{YEAR}-{MONTH:02d}-03", "bad", 99,
                                           f"{YEAR}-12-01"], YEAR, MONTH)
    assert dates == (date(YEAR, MONTH, 1), date(YEAR, MONTH, 2), date(YEAR, MONTH, 3))
    assert ctx._coerce_fill_target_dates(None, YEAR, MONTH) == ()


def test_fill_target_days_passed_to_request():
    project = base_project(capacity_enabled=True, required_capacity=1)
    request, _, _ = ctx.build_planning_request(project, YEAR, MONTH, fill_target_days=[1, 2])
    assert request.fill_target_dates == (date(YEAR, MONTH, 1), date(YEAR, MONTH, 2))
    result = e.plan_shifts(request)
    assert sorted({a.day for a in result.assignments}) == [1, 2]
