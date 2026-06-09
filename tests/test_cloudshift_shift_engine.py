"""CloudShift 自動シフト作成エンジン（コア）の単体テスト。

設計書 docs/cloudshift_shift_engine_design.md の「必須単体テスト」に対応する。
エンジン本体は Flask なしで検証できる前提のため、DB やルートには触れない。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services import cloudshift_shift_engine as e


# ---------------------------------------------------------------------------
# テスト用ビルダー
# ---------------------------------------------------------------------------

SITE = e.SiteRef(site_row_id="10", site_id="S1", site_name="A現場")
YEAR = 2026
MONTH = 7


def make_slot(day: int, shift_key: str = "", required: int = 1, slot_id: str | None = None,
              site_row_id: str = "10", branch: str = "") -> e.RequiredSlot:
    return e.RequiredSlot(
        slot_id=slot_id or f"s-{day}-{shift_key or 'none'}-{branch}",
        date=date(YEAR, MONTH, day),
        day=day,
        shift_key=shift_key,
        shift_label=shift_key,
        required_count=required,
        site_row_id=site_row_id,
        site_id="S1",
        site_name="A現場",
        site_branch_row_id=branch,
        site_branch=branch,
    )


def make_worker(number: str, *, name: str = "", active: bool = True,
                dedicated=(), experienced=("10",), trainee=(), options=(),
                preferred=(), blocked=(), max_assignments=None, min_assignments=None,
                prior_tail=0, weight=0) -> e.Worker:
    pref = None
    if preferred or blocked:
        pref = e.WorkerPreference(preferred_weekdays=tuple(preferred), blocked_weekdays=tuple(blocked))
    limit = None
    if max_assignments is not None or min_assignments is not None or prior_tail:
        limit = e.WorkerMonthlyLimit(min_assignments=min_assignments, max_assignments=max_assignments,
                                     prior_month_tail_consecutive=prior_tail)
    return e.Worker(
        worker_id=f"w-{number}",
        employee_number=number,
        name=name or f"社員{number}",
        active=active,
        dedicated_site_row_ids=tuple(dedicated),
        experienced_site_row_ids=tuple(experienced),
        trainee_site_row_ids=tuple(trainee),
        experienced_option_keys=tuple(options),
        preference=pref,
        monthly_limit=limit,
        worker_weight=weight,
    )


def make_request(slots, workers, *, existing=None, external=None, unavailable=None,
                 rules=None, preferences=None, weights=None, policies=None,
                 relax_sites=(), request_id="req") -> e.ShiftPlanningRequest:
    return e.ShiftPlanningRequest(
        request_id=request_id,
        version=e.REQUEST_VERSION,
        target_project_id="P1",
        target_site=SITE,
        year=YEAR,
        month=MONTH,
        base_revision=1,
        days=e.build_planning_days(YEAR, MONTH),
        required_slots=list(slots),
        workers=list(workers),
        existing_assignments=list(existing or []),
        external_assignments=list(external or []),
        unavailable_days=list(unavailable or []),
        rules=list(rules or []),
        preferences=preferences or e.default_planning_preferences(),
        scoring_weights=weights or e.ScoringWeights(),
        option_experience_policies=list(policies or []),
        external_occupancy_relax_sites=tuple(relax_sites),
    )


def assigned_numbers(result, day=None, shift_key=None):
    out = []
    for a in result.assignments:
        if day is not None and a.day != day:
            continue
        if shift_key is not None and a.shift_key != shift_key:
            continue
        out.append(a.employee_number)
    return out


# ---------------------------------------------------------------------------
# 基本・既定プロファイル・決定性
# ---------------------------------------------------------------------------


def test_default_profile_generates_reasonable_draft():
    """既定設定だけで、候補と需要があれば合理的な下書きが生成される。"""
    slots = [make_slot(d) for d in range(1, 6)]
    workers = [make_worker("E001"), make_worker("E002")]
    res = e.plan_shifts(make_request(slots, workers))
    assert res.status == "feasible"
    assert res.score.assigned_count == 5
    assert res.score.fill_rate == 1.0
    assert res.score.blocker_count == 0


def test_determinism_same_input_same_output():
    slots = [make_slot(d) for d in range(1, 11)]
    workers = [make_worker(f"E{i:03d}") for i in range(1, 6)]
    req = make_request(slots, workers)
    res1 = e.plan_shifts(req)
    res2 = e.plan_shifts(req)
    assert res1.request_hash == res2.request_hash
    assert [(a.slot_instance_id, a.employee_number) for a in res1.assignments] == \
           [(a.slot_instance_id, a.employee_number) for a in res2.assignments]


def test_zero_demand_is_feasible():
    workers = [make_worker("E001")]
    res = e.plan_shifts(make_request([], workers))
    assert res.status == "feasible"
    assert res.score.fill_rate == 1.0
    assert res.assignments == []


def test_zero_candidates_no_exception_partial():
    slots = [make_slot(1)]
    res = e.plan_shifts(make_request(slots, []))
    assert res.status == "partial"
    assert res.score.unfilled_count == 1
    assert res.unfilled_slots[0].reason_code == "no_candidates"


def test_all_hard_leave_excluded():
    slots = [make_slot(1)]
    workers = [make_worker("E001"), make_worker("E002")]
    unavailable = [
        e.UnavailableDay("E001", date(YEAR, MONTH, 1), "有休", "leave_mgr", "hard"),
        e.UnavailableDay("E002", date(YEAR, MONTH, 1), "有休", "leave_mgr", "hard"),
    ]
    res = e.plan_shifts(make_request(slots, workers, unavailable=unavailable))
    assert res.status == "partial"
    assert res.unfilled_slots[0].reason_code == "all_excluded_hard"
    assert res.assignments == []


def test_hard_leave_worker_not_assigned():
    slots = [make_slot(1)]
    workers = [make_worker("E001"), make_worker("E002")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 1), "有休", "leave_mgr", "hard")]
    res = e.plan_shifts(make_request(slots, workers, unavailable=unavailable))
    assert assigned_numbers(res) == ["E002"]


# ---------------------------------------------------------------------------
# 重複・他現場占有
# ---------------------------------------------------------------------------


def test_same_site_duplicate_not_created():
    """同一現場で重複する option（A と E）を同日同一人物に作らない。"""
    slots = [make_slot(1, "A", slot_id="a"), make_slot(1, "E", slot_id="e")]
    workers = [make_worker("E001", options=("A", "E"))]
    res = e.plan_shifts(make_request(slots, workers))
    # 1人しかいないので、A と E は重複 → どちらか1枠のみ
    nums = assigned_numbers(res, day=1)
    assert nums.count("E001") == 1
    assert res.score.unfilled_count == 1


def test_same_site_coexisting_options_allowed():
    """共存可能な option（A 早番 と L 遅番）は同日でも両方配置できる。"""
    slots = [make_slot(1, "A", slot_id="a"), make_slot(1, "L", slot_id="l")]
    workers = [make_worker("E001", options=("A", "L"))]
    res = e.plan_shifts(make_request(slots, workers))
    nums = assigned_numbers(res, day=1)
    assert nums.count("E001") == 2  # A と L は共存可


def test_external_occupancy_excludes_matching_option():
    """他現場で同日に確定（番号一致かつ option 重複）なら除外する。"""
    slots = [make_slot(1, "A")]
    workers = [make_worker("E001", options=("A",)), make_worker("E002", options=("A",))]
    external = [e.ExternalAssignment("E001", "社員E001", date(YEAR, MONTH, 1), 1, "A", "P2", "B現場", "scene")]
    res = e.plan_shifts(make_request(slots, workers, external=external))
    assert assigned_numbers(res) == ["E002"]


def test_external_occupancy_coexisting_option_not_excluded():
    """他現場占有でも共存可（A と L）なら除外しない。"""
    slots = [make_slot(1, "A")]
    workers = [make_worker("E001", options=("A",))]
    external = [e.ExternalAssignment("E001", "社員E001", date(YEAR, MONTH, 1), 1, "L", "P2", "B現場", "scene")]
    res = e.plan_shifts(make_request(slots, workers, external=external))
    assert assigned_numbers(res) == ["E001"]


def test_external_without_number_not_hard_excluded():
    """番号で突合できない他現場占有は Hard 除外しない。"""
    slots = [make_slot(1, "A")]
    workers = [make_worker("E001", options=("A",))]
    external = [e.ExternalAssignment("", "佐藤", date(YEAR, MONTH, 1), 1, "A", "P2", "B現場", "scene")]
    res = e.plan_shifts(make_request(slots, workers, external=external))
    assert assigned_numbers(res) == ["E001"]


# ---------------------------------------------------------------------------
# 最低基準・研修・オプション経験
# ---------------------------------------------------------------------------


def test_baseline_excludes_non_experienced():
    """専従でも経験者でもない候補は配置せず空欄にする。"""
    slots = [make_slot(1)]
    workers = [make_worker("E001", experienced=())]  # 経験なし
    res = e.plan_shifts(make_request(slots, workers))
    assert res.status == "partial"
    assert res.unfilled_slots[0].reason_code == "no_eligible"


def test_include_trainees_makes_eligible():
    slots = [make_slot(1)]
    workers = [make_worker("E001", experienced=(), trainee=("10",))]
    prefs = e.default_planning_preferences()
    prefs = e.replace(prefs, include_trainees=True)
    res = e.plan_shifts(make_request(slots, workers, preferences=prefs))
    assert assigned_numbers(res) == ["E001"]


def test_baseline_any_disables_min_baseline():
    slots = [make_slot(1)]
    workers = [make_worker("E001", experienced=())]
    prefs = e.replace(e.default_planning_preferences(), eligibility_baseline="any")
    res = e.plan_shifts(make_request(slots, workers, preferences=prefs))
    assert assigned_numbers(res) == ["E001"]


def test_option_aptitude_bonus_applied():
    """オプション一致の候補に加点する（一致しなくても配置は禁止しない）。"""
    slots = [make_slot(1, "M")]
    workers = [make_worker("E001", options=()), make_worker("E002", options=("M",))]
    res = e.plan_shifts(make_request(slots, workers))
    # E002 はオプション適性で加点され優先される
    assert assigned_numbers(res) == ["E002"]


def test_option_no_policy_allows_inexperienced():
    """ポリシーが無いオプションは未経験者でも配置できる。"""
    slots = [make_slot(1, "M")]
    workers = [make_worker("E001", options=())]
    res = e.plan_shifts(make_request(slots, workers))
    assert assigned_numbers(res) == ["E001"]


def test_option_experience_policy_excludes_inexperienced():
    """require_prior_experience のオプション枠は未経験者を除外し、経験者なしなら空欄。"""
    slots = [make_slot(1, "M")]
    workers = [make_worker("E001", options=())]
    policies = [e.OptionExperiencePolicy(option_key="M", require_prior_experience=True)]
    res = e.plan_shifts(make_request(slots, workers, policies=policies))
    assert res.status == "partial"
    assert res.unfilled_slots[0].reason_code == "option_experience_required"


def test_option_experience_policy_allows_experienced():
    slots = [make_slot(1, "M")]
    workers = [make_worker("E001", options=("M",))]
    policies = [e.OptionExperiencePolicy(option_key="M", require_prior_experience=True)]
    res = e.plan_shifts(make_request(slots, workers, policies=policies))
    assert assigned_numbers(res) == ["E001"]


def test_min_assignment_score_blocks_low_candidates():
    """最良候補のスコアが下限未満なら空欄にし、理由はスコア下限未満。"""
    slots = [make_slot(1)]
    # 経験のみ(120点)。専従なし。NG曜日で大きく減点して下限割れを作る。
    weekday = date(YEAR, MONTH, 1).weekday()
    workers = [make_worker("E001", dedicated=(), experienced=("10",), blocked=(weekday,))]
    prefs = e.replace(e.default_planning_preferences(), min_assignment_score=0)
    res = e.plan_shifts(make_request(slots, workers, preferences=prefs))
    assert res.status == "partial"
    assert res.unfilled_slots[0].reason_code == "below_min_score"


# ---------------------------------------------------------------------------
# 充足・優先・公平性
# ---------------------------------------------------------------------------


def test_meets_required_count():
    slots = [make_slot(1, required=3)]
    workers = [make_worker(f"E{i:03d}", options=()) for i in range(1, 5)]
    res = e.plan_shifts(make_request(slots, workers))
    assert res.score.assigned_count == 3
    assert res.score.unfilled_count == 0


def test_shortage_reports_partial():
    slots = [make_slot(1, required=3)]
    workers = [make_worker("E001")]
    res = e.plan_shifts(make_request(slots, workers))
    assert res.status == "partial"
    assert res.score.unfilled_count == 2
    assert res.unfilled_slots[0].shortage == 2


def test_dedicated_preferred():
    slots = [make_slot(1)]
    workers = [make_worker("E001", dedicated=(), experienced=("10",)),
               make_worker("E002", dedicated=("10",), experienced=("10",))]
    res = e.plan_shifts(make_request(slots, workers))
    assert assigned_numbers(res) == ["E002"]


def test_fairness_spreads_assignments():
    """公平性スコアが特定人物への集中を抑える。"""
    slots = [make_slot(d) for d in range(1, 5)]  # 4 枠（別日）
    workers = [make_worker("E001"), make_worker("E002")]
    res = e.plan_shifts(make_request(slots, workers))
    counts = {}
    for a in res.assignments:
        counts[a.employee_number] = counts.get(a.employee_number, 0) + 1
    assert counts.get("E001") == 2 and counts.get("E002") == 2


def test_preferred_weekday_bonus_and_blocked_penalty():
    """希望曜日は加点、NG曜日は強く減点だが他に回せなければ配置する。"""
    weekday = date(YEAR, MONTH, 1).weekday()
    slots = [make_slot(1)]
    workers = [make_worker("E001", preferred=(weekday,)), make_worker("E002", blocked=(weekday,))]
    res = e.plan_shifts(make_request(slots, workers))
    assert assigned_numbers(res) == ["E001"]  # 希望曜日の E001 が優先

    # NG 曜日でも唯一の候補なら配置する（Hard ではない）
    res2 = e.plan_shifts(make_request([make_slot(1)], [make_worker("E002", blocked=(weekday,))]))
    assert assigned_numbers(res2) == ["E002"]


# ---------------------------------------------------------------------------
# 既存固定・pinned・forbidden
# ---------------------------------------------------------------------------


def test_locked_existing_preserved():
    """既存固定シフトを動かさない。"""
    slots = [make_slot(1, "A", required=1)]
    workers = [make_worker("E001", options=("A",)), make_worker("E002", options=("A",))]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "A", "A", "E001", "社員E001",
                                     "manual", "entry1", "locked")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    nums = assigned_numbers(res, day=1)
    assert "E001" in nums
    locked = [a for a in res.assignments if a.source == "existing_locked"]
    assert locked and locked[0].employee_number == "E001"


def test_fixed_exceeds_demand_preserved_with_warning():
    """固定既存が需要を上回っても 100% 保持し、需要を引き上げて warning。"""
    slots = [make_slot(1, "A", required=1)]
    workers = [make_worker("E001", options=("A",)), make_worker("E002", options=("A",))]
    existing = [
        e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "A", "A", "E001", "社員E001", "manual", "en1", "locked"),
        e.ExistingAssignment("ex2", date(YEAR, MONTH, 1), 1, "A", "A", "E002", "社員E002", "manual", "en2", "locked"),
    ]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    nums = set(assigned_numbers(res, day=1))
    assert nums == {"E001", "E002"}
    assert any(w.code == "demand_raised_for_fixed" for w in res.warnings)
    assert res.score.blocker_count == 0


def test_locked_without_demand_preserved():
    """需要に無い固定既存も合成 slot で保持する。"""
    slots = []  # 需要ゼロ
    workers = [make_worker("E001", options=("A",))]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 2), 2, "A", "A", "E001", "社員E001",
                                     "manual", "entry1", "locked")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    assert "E001" in assigned_numbers(res, day=2)
    assert any(w.code == "fixed_without_demand" for w in res.warnings)


def test_seed_hard_leave_is_blocker():
    """固定既存シフトが hard 休暇と衝突したら重大違反（blocker）。"""
    slots = [make_slot(1, "A", required=1)]
    workers = [make_worker("E001", options=("A",))]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "A", "A", "E001", "社員E001",
                                     "manual", "entry1", "locked")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 1), "有休", "leave_mgr", "hard")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing, unavailable=unavailable))
    assert res.status == "failed"
    assert res.score.blocker_count >= 1
    assert any(v.code == "seed_hard_leave" for v in res.violations)


def test_pinned_seeded_and_forbidden_excluded():
    """pinned を seed として固定し、forbidden の枠に当人を入れない。"""
    slots = [make_slot(1, "A", required=1), make_slot(2, "A", required=1)]
    workers = [make_worker("E001", options=("A",)), make_worker("E002", options=("A",))]
    rules = [
        e.Rule("r1", "pinned", params={"employee_number": "E001", "day": 1, "shift_key": "A"}),
        e.Rule("r2", "forbidden", params={"employee_number": "E001", "day": 2, "shift_key": "A"}),
    ]
    res = e.plan_shifts(make_request(slots, workers, rules=rules))
    assert "E001" in assigned_numbers(res, day=1)
    assert "E001" not in assigned_numbers(res, day=2)


def test_pinned_forbidden_conflict_blocker():
    slots = [make_slot(1, "A", required=1)]
    workers = [make_worker("E001", options=("A",))]
    rules = [
        e.Rule("r1", "pinned", params={"employee_number": "E001", "day": 1, "shift_key": "A"}),
        e.Rule("r2", "forbidden", params={"employee_number": "E001", "day": 1, "shift_key": "A"}),
    ]
    res = e.plan_shifts(make_request(slots, workers, rules=rules))
    assert res.status == "failed"
    assert any(v.code == "pinned_forbidden_conflict" for v in res.violations)


def test_pair_avoid_not_same_slot_day():
    """pair_avoid の 2 名を同日同枠に並べない（第三者がいれば回避する）。"""
    slots = [make_slot(1, "A", slot_id="a1"), make_slot(1, "L", slot_id="l1")]
    workers = [make_worker("E001", options=("A", "L")),
               make_worker("E002", options=("A", "L")),
               make_worker("E003", options=("A", "L"))]
    rules = [e.Rule("r1", "pair_avoid", params={"employee_numbers": ["E001", "E002"]})]
    res = e.plan_shifts(make_request(slots, workers, rules=rules))
    day_nums = set(assigned_numbers(res, day=1))
    assert not ({"E001", "E002"} <= day_nums)


# ---------------------------------------------------------------------------
# 連勤・上限
# ---------------------------------------------------------------------------


def test_consecutive_hard_cap_avoided():
    """max_consecutive_days を Hard 設定にすると連勤上限超過を避ける。"""
    slots = [make_slot(d) for d in range(1, 5)]  # 1..4 連続日
    workers = [make_worker("E001"), make_worker("E002")]
    prefs = e.replace(e.default_planning_preferences(),
                      max_consecutive_days=2, max_consecutive_days_hard=True)
    res = e.plan_shifts(make_request(slots, workers, preferences=prefs))
    # どの worker も 3 連勤以上にならない
    by_worker = {}
    for a in res.assignments:
        by_worker.setdefault(a.employee_number, set()).add(a.date)
    for dates in by_worker.values():
        assert e._max_consecutive(dates, date(YEAR, MONTH, 1), 0) <= 2


def test_monthly_max_hard_limit():
    slots = [make_slot(d) for d in range(1, 4)]
    workers = [make_worker("E001", max_assignments=1), make_worker("E002", max_assignments=1),
               make_worker("E003", max_assignments=1)]
    res = e.plan_shifts(make_request(slots, workers))
    counts = {}
    for a in res.assignments:
        counts[a.employee_number] = counts.get(a.employee_number, 0) + 1
    assert all(c <= 1 for c in counts.values())


# ---------------------------------------------------------------------------
# 検証関数
# ---------------------------------------------------------------------------


def test_validate_request_rejects_bad_month():
    req = make_request([], [make_worker("E001")])
    req = e.replace(req, month=13)
    violations = e.validate_request(req)
    assert any(v.code == "invalid_month" and v.severity == "blocker" for v in violations)


def test_validate_request_rejects_duplicate_worker():
    req = make_request([], [make_worker("E001"), make_worker("E001")])
    violations = e.validate_request(req)
    assert any(v.code == "duplicate_worker" for v in violations)


def test_validate_request_rejects_slot_out_of_month():
    bad_slot = e.RequiredSlot(slot_id="x", date=date(YEAR, MONTH + 1, 1), day=1, shift_key="",
                              shift_label="", required_count=1, site_row_id="10")
    req = make_request([bad_slot], [make_worker("E001")])
    violations = e.validate_request(req)
    assert any(v.code == "slot_out_of_month" for v in violations)


def test_validate_result_detects_unknown_worker_and_slot():
    req = make_request([make_slot(1)], [make_worker("E001")])
    bad = e.Assignment("a1", "nope", "nope#1", date(YEAR, MONTH, 1), 1, "", "E999", "x", 0, "engine")
    view = e._ResultView([bad], [])
    violations = e.validate_result(req, view)
    codes = {v.code for v in violations}
    assert "unknown_worker" in codes
    assert "unknown_slot" in codes


def test_validate_result_detects_locked_change():
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "A", "A", "E001", "x",
                                     "manual", "en1", "locked")]
    req = make_request([make_slot(1, "A")], [make_worker("E001", options=("A",))], existing=existing)
    view = e._ResultView([], [])  # 固定が消えている
    violations = e.validate_result(req, view)
    assert any(v.code == "locked_changed" for v in violations)


# ---------------------------------------------------------------------------
# request_hash・ScoreSummary・status
# ---------------------------------------------------------------------------


def test_request_hash_stable_and_sensitive_to_options():
    slots = [make_slot(1, "M")]
    workers = [make_worker("E001", options=("M",))]
    req = make_request(slots, workers)
    h1 = e.compute_request_hash(req)
    h2 = e.compute_request_hash(req)
    assert h1 == h2
    # オプション値（未経験不可ポリシー）を加えると hash が変わる
    req2 = e.replace(req, option_experience_policies=[
        e.OptionExperiencePolicy(option_key="M", require_prior_experience=True)])
    assert e.compute_request_hash(req2) != h1


def test_score_summary_counts_consistent():
    slots = [make_slot(1, required=2), make_slot(2, required=1)]
    workers = [make_worker("E001"), make_worker("E002")]
    res = e.plan_shifts(make_request(slots, workers))
    s = res.score
    assert s.assigned_count + s.unfilled_count == s.required_count
    assert s.required_count == 3
    assert s.fill_rate == pytest.approx(s.assigned_count / s.required_count)


def test_greedy_never_returns_optimal():
    slots = [make_slot(1)]
    workers = [make_worker("E001")]
    res = e.plan_shifts(make_request(slots, workers))
    assert res.status != "optimal"
    assert res.solver_certified is False
    assert res.solver_backend == e.SOLVER_BACKEND


def test_no_option_keeps_core_behavior():
    """任意オプションが未設定（既定中立）のときコア挙動と一致する。"""
    slots = [make_slot(d) for d in range(1, 4)]
    workers = [make_worker("E001"), make_worker("E002")]
    base = e.plan_shifts(make_request(slots, workers))
    # advanced オプション相当を一切付けない別 request も同じ結果
    again = e.plan_shifts(make_request(slots, workers))
    assert [a.employee_number for a in base.assignments] == [a.employee_number for a in again.assignments]


def test_options_cannot_relax_core_hard_leave():
    """オプションでコアの Hard（hard 休暇）を緩められない。"""
    slots = [make_slot(1)]
    workers = [make_worker("E001")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 1), "有休", "leave_mgr", "hard")]
    # eligibility_baseline=any でも hard 休暇は外せない
    prefs = e.replace(e.default_planning_preferences(), eligibility_baseline="any")
    res = e.plan_shifts(make_request(slots, workers, unavailable=unavailable, preferences=prefs))
    assert res.assignments == []
    assert res.status == "partial"


# ---------------------------------------------------------------------------
# 回帰テスト（バグ修正の固定）
# ---------------------------------------------------------------------------


def test_multi_branch_locked_not_double_placed():
    """同一日・同一shiftで枝番違いの複数slot + ロック既存 → 同一人物を二重配置しない。"""
    slots = [make_slot(1, "A", slot_id="b1", branch="1"), make_slot(1, "A", slot_id="b2", branch="2")]
    workers = [make_worker("E001", options=("A",)), make_worker("E002", options=("A",))]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "1-A", "A", "E001", "社員E001",
                                     "manual", "en1", "locked", site_branch_row_id="1")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    assert res.score.blocker_count == 0
    day1 = assigned_numbers(res, day=1)
    assert day1.count("E001") == 1  # 枝番1にのみ配置（二重配置しない）


def test_fixed_assignment_exempt_from_baseline_recheck():
    """固定既存(locked)は最低基準を満たさなくても hard 違反にしない（100%維持）。"""
    slots = [make_slot(1, "A", required=1)]
    # E900 は経験なし（最低基準を満たさない）が locked 既存として配置済み
    workers = [make_worker("E900", experienced=(), options=())]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "1-A", "A", "E900", "社員E900",
                                     "manual", "en1", "locked")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    assert "E900" in assigned_numbers(res, day=1)
    assert res.score.hard_violation_count == 0  # 固定は baseline 再判定の対象外


# ---------------------------------------------------------------------------
# 対象日指定（fill_target_dates）
# ---------------------------------------------------------------------------


def _with_targets(req, *days):
    from dataclasses import replace
    return replace(req, fill_target_dates=tuple(date(YEAR, MONTH, d) for d in days))


def test_fill_target_dates_limits_new_assignments():
    """対象日指定があると、新規配置は対象日にだけ作られる。"""
    slots = [make_slot(d) for d in range(1, 11)]
    workers = [make_worker("E001"), make_worker("E002")]
    req = _with_targets(make_request(slots, workers), 1, 2, 3)
    res = e.plan_shifts(req)
    assert res.status == "feasible"
    assert sorted({a.day for a in res.assignments}) == [1, 2, 3]
    # 対象外日の需要は「未充足」として数えない（スコープ外）
    assert res.score.unfilled_count == 0
    assert res.score.required_count == 3


def test_fill_target_dates_keeps_existing_outside_scope():
    """対象外日の既存（movable 含む）は固定として 100% 保持される。"""
    slots = [make_slot(d) for d in range(1, 11)]
    workers = [make_worker("E001"), make_worker("E002")]
    existing = [
        e.ExistingAssignment("ex5", date(YEAR, MONTH, 5), 5, "5-", "", "E002", "社員E002",
                             "scene_sync", "en5", "movable"),
    ]
    req = _with_targets(make_request(slots, workers, existing=existing), 1, 2)
    res = e.plan_shifts(req)
    kept = [a for a in res.assignments if a.day == 5]
    assert kept and kept[0].employee_number == "E002"
    assert kept[0].source == "existing_locked"
    new_days = {a.day for a in res.assignments if a.source == "engine"}
    assert new_days <= {1, 2}


def test_fill_target_dates_full_month_constraints_still_apply():
    """対象日を絞っても、連勤などの計算は1か月全体の配置を見る。"""
    slots = [make_slot(d) for d in range(1, 8)]
    workers = [make_worker("E001"), make_worker("E002")]
    # E001 は 2〜6 日に固定で 5 連勤済み
    existing = [
        e.ExistingAssignment(f"ex{d}", date(YEAR, MONTH, d), d, f"{d}-", "", "E001", "社員E001",
                             "manual", f"en{d}", "locked")
        for d in range(2, 7)
    ]
    prefs = e.default_planning_preferences()
    from dataclasses import replace as dc_replace
    prefs = dc_replace(prefs, max_consecutive_days=5, max_consecutive_days_hard=True)
    req = _with_targets(
        make_request(slots, workers, existing=existing, preferences=prefs), 1, 7)
    res = e.plan_shifts(req)
    # 1日・7日に E001 を置くと 6 連勤になるため、E002 が選ばれる
    assert assigned_numbers(res, day=1) == ["E002"]
    assert assigned_numbers(res, day=7) == ["E002"]


def test_fill_target_dates_changes_request_hash():
    slots = [make_slot(d) for d in range(1, 6)]
    workers = [make_worker("E001")]
    req_all = make_request(slots, workers)
    req_subset = _with_targets(make_request(slots, workers), 1, 2)
    assert e.compute_request_hash(req_all) != e.compute_request_hash(req_subset)


def test_fill_target_out_of_month_warns():
    slots = [make_slot(1)]
    workers = [make_worker("E001")]
    from dataclasses import replace as dc_replace
    req = dc_replace(make_request(slots, workers),
                     fill_target_dates=(date(YEAR, MONTH, 1), date(YEAR + 1, 1, 5)))
    res = e.plan_shifts(req)
    assert any(v.code == "fill_target_out_of_month" for v in res.violations)
    assert res.score.blocker_count == 0


def test_out_of_scope_seed_conflict_is_warning_not_blocker():
    """対象外日の固定が hard 休暇と衝突しても、生成全体を止めない（warning 保持）。"""
    slots = [make_slot(1), make_slot(5)]
    workers = [make_worker("E001"), make_worker("E002")]
    existing = [e.ExistingAssignment("ex5", date(YEAR, MONTH, 5), 5, "5-", "", "E001", "社員E001",
                                     "manual", "en5", "locked")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 5), "有休", "leave_mgr", "hard")]
    req = _with_targets(
        make_request(slots, workers, existing=existing, unavailable=unavailable), 1)
    res = e.plan_shifts(req)
    assert res.status != "failed"
    assert res.score.blocker_count == 0
    # 固定はそのまま保持される
    assert "E001" in assigned_numbers(res, day=5)


# ---------------------------------------------------------------------------
# 有休系オプション entry（休みの記録）の扱い
# ---------------------------------------------------------------------------


def test_leave_entry_seed_coexists_with_hard_unavailable():
    """有休 entry（PAID 等）は本人の hard 休暇と同日でも衝突ではない。"""
    slots = [make_slot(1, required=1)]
    workers = [make_worker("E001"), make_worker("E002")]
    existing = [e.ExistingAssignment("ex1", date(YEAR, MONTH, 1), 1, "1-PAID", "PAID", "E001",
                                     "社員E001", "manual", "en1", "locked")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 1), "有休", "shift_entry", "hard")]
    res = e.plan_shifts(make_request(slots, workers, existing=existing, unavailable=unavailable))
    assert res.score.blocker_count == 0
    assert res.score.hard_violation_count == 0
    # 有休 entry は保持され、勤務枠には E002 が入る
    day1 = assigned_numbers(res, day=1)
    assert "E001" in day1 and "E002" in day1


def test_leave_entry_not_counted_as_work():
    """有休 entry は連勤・勤務数に算入されない。"""
    slots = []
    workers = [make_worker("E001")]
    existing = [
        e.ExistingAssignment(f"ex{d}", date(YEAR, MONTH, d), d, f"{d}-PAID", "PAID", "E001",
                             "社員E001", "manual", f"en{d}", "locked")
        for d in range(1, 11)
    ]
    res = e.plan_shifts(make_request(slots, workers, existing=existing))
    assert res.score.max_consecutive_days == 0


def test_hard_leave_blocks_new_assignment_same_day_as_leave_entry():
    """休みの日（hard 不可日）には新規の勤務シフトを入れない。"""
    slots = [make_slot(1, required=2)]
    workers = [make_worker("E001"), make_worker("E002"), make_worker("E003")]
    unavailable = [e.UnavailableDay("E001", date(YEAR, MONTH, 1), "休み予定", "shift_entry", "hard")]
    res = e.plan_shifts(make_request(slots, workers, unavailable=unavailable))
    assert "E001" not in assigned_numbers(res, day=1)


# ---------------------------------------------------------------------------
# 連勤判定の双方向化（橋渡し配置の検出）
# ---------------------------------------------------------------------------


def test_consecutive_run_counts_forward_bridge():
    """既存連勤の直前に置く配置も連勤として数える（前方を見落とさない）。"""
    worked = {date(YEAR, MONTH, d) for d in (10, 11, 12, 13, 14)}
    run = e._consecutive_run_if_placed(worked, date(YEAR, MONTH, 9), date(YEAR, MONTH, 1), 0, False)
    assert run == 6
    # 2 つの run の橋渡しも合算される
    worked2 = {date(YEAR, MONTH, d) for d in (7, 8, 10, 11)}
    run2 = e._consecutive_run_if_placed(worked2, date(YEAR, MONTH, 9), date(YEAR, MONTH, 1), 0, False)
    assert run2 == 5


def test_hard_max_consecutive_blocks_bridge_placement():
    """連勤上限（Hard）は橋渡し配置でも守られる。"""
    slots = [make_slot(9)]
    workers = [make_worker("E001"), make_worker("E002")]
    existing = [
        e.ExistingAssignment(f"ex{d}", date(YEAR, MONTH, d), d, f"{d}-", "", "E001", "社員E001",
                             "manual", f"en{d}", "locked")
        for d in (10, 11, 12, 13, 14)
    ]
    from dataclasses import replace as dc_replace
    prefs = dc_replace(e.default_planning_preferences(),
                       max_consecutive_days=5, max_consecutive_days_hard=True)
    res = e.plan_shifts(make_request(slots, workers, existing=existing, preferences=prefs))
    assert assigned_numbers(res, day=9) == ["E002"]


# ---------------------------------------------------------------------------
# 月次最低勤務数（Soft）
# ---------------------------------------------------------------------------


def test_min_monthly_assignments_prioritizes_under_target():
    """min_assignments を下回っている人を優先して引き上げる。"""
    slots = [make_slot(d) for d in (1, 3, 5)]
    workers = [make_worker("E001", min_assignments=3), make_worker("E002")]
    res = e.plan_shifts(make_request(slots, workers))
    counts = {}
    for a in res.assignments:
        counts[a.employee_number] = counts.get(a.employee_number, 0) + 1
    assert counts.get("E001", 0) == 3
