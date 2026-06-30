"""CloudShift 自動シフト作成エンジン（Phase 1: GreedyRepairSolver）。

設計書 `docs/cloudshift_shift_engine_design.md` に基づく独立エンジン本体。

方針:
- 本モジュールは Flask / SQLAlchemy / CloudShift の DB アクセス関数に依存しない。
  唯一の外部依存は重複判定の正となる純粋関数 `is_duplicate_by_rules`
  （`app/tools/shiftersync_check.py`）と祝日定数 `JAPAN_HOLIDAYS`
  （`app/tools/japan_holidays.py`）であり、いずれも DB/Flask 非依存。
- 入力は `ShiftPlanningRequest` 一つに正規化され、同じ入力・設定・revision なら
  同じ出力（`assignments` と `request_hash`）になる決定的実装。
- Hard Constraint と Soft Constraint を明確に分け、生成結果は必ず検証を通す。

エンジンは「説明可能で検証可能な下書き生成器」であり、完璧な自動決定者ではない。
守れない条件は `unfilled_slots` と `violations` として返す。
"""

from __future__ import annotations

import hashlib
import json
import time
from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Literal

try:  # パッケージ／単体どちらでも読めるように両対応（既存コードと同じ流儀）
    from ..tools.japan_holidays import JAPAN_HOLIDAYS
    from ..tools.shiftersync_check import LEAVE_OPTION_KEYS, is_duplicate_by_rules
except ImportError:  # pragma: no cover - 単体実行時のフォールバック
    from app.tools.japan_holidays import JAPAN_HOLIDAYS  # type: ignore
    from app.tools.shiftersync_check import (  # type: ignore
        LEAVE_OPTION_KEYS,
        is_duplicate_by_rules,
    )


SOLVER_BACKEND = "greedy_repair_v1"
SETTINGS_VERSION = 1
REQUEST_VERSION = 1

# 祝日集合（"YYYY-MM-DD" 文字列。日付判定用に保持）
_HOLIDAY_SET = set(JAPAN_HOLIDAYS)

# 連勤の Soft 抑制で「快適」とみなす既定しきい値（max_consecutive_days 未指定時に使用）。
# これを超える連勤に consecutive_day_penalty を科す。Hard 上限は別途 max_consecutive_days。
CONSECUTIVE_SOFT_DEFAULT = 5

# 局所修復で未充足 1 件に科す擬似コスト（充足を最優先させるための支配項）。
UNFILLED_COST = 100_000

# 候補パネル（枠ごとの候補比較）に載せる候補数の上限。
PANEL_MAX_CANDIDATES = 5

# 月次最低勤務数（min_assignments）未達の重み倍率。
# ユーザーが明示した最低保証は、一般的な公平性ペナルティ（2 乗）より優先させる。
UNDER_MIN_MULTIPLIER = 3


# ---------------------------------------------------------------------------
# 設定モデル（project["shift_engine"] に保存する設定）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemandRule:
    """現場ごとの必要枠を定義する需要ルール。"""

    rule_id: str
    enabled: bool
    weekdays: tuple[int, ...]
    include_holidays: bool
    exclude_dates: tuple[date, ...] = ()
    include_dates: tuple[date, ...] = ()
    shift_key: str = ""
    shift_label: str = ""
    required_count: int = 1
    site_branch_row_id: str = ""
    site_branch: str = ""
    # 予約フィールド。資格マスタが無いため Phase 1 では Hard 化しない。
    required_qualification_codes: tuple[str, ...] = ()
    # Soft（過去実績ベースの適性）として扱う。Hard にしない。
    required_vehicle_options: tuple[str, ...] = ()
    priority: int = 100


@dataclass(frozen=True)
class LeavePolicy:
    """有休共有ツールの leave_type を不可強度へ変換する設定。"""

    leave_type: str
    strength: Literal["hard", "soft", "info"]


@dataclass(frozen=True)
class PlanningPreferences:
    """生成方針。必ず既定値を持ち、未指定は default_planning_preferences() で埋める。"""

    existing_policy: Literal["lock_all", "lock_manual", "replace_all"]
    allow_partial: bool
    eligibility_baseline: Literal["dedicated_or_experienced", "any"]
    min_assignment_score: int | None
    unconfirmed_leave_strength: Literal["hard", "soft", "info"]
    max_consecutive_days: int | None
    min_monthly_assignments: int | None
    max_monthly_assignments: int | None
    include_trainees: bool
    prefer_dedicated: bool
    prefer_experienced: bool
    prefer_fairness: bool
    minimize_changes: bool
    suppress_weekend_imbalance: bool
    # --- 以下は設計の「Soft/Hard 選択」を実装するための既定付き拡張 ---
    # 月次上限超過は既定で Hard（設計の Hard Constraint）。設定で warning へ下げられる。
    monthly_limit_hard: bool = True
    # 最大連勤数を Hard 上限として扱うか（既定 False = Soft 抑制のみ）。
    max_consecutive_days_hard: bool = False


@dataclass(frozen=True)
class WorkerLimit:
    """現場全体／個人別の月次上限設定（adapter が Worker.monthly_limit へ解決する）。"""

    employee_number: str
    min_assignments: int | None = None
    max_assignments: int | None = None


@dataclass(frozen=True)
class ScoringWeights:
    """スコア重み。プリセット → ScoringWeights に変換して使う。"""

    dedicated_bonus: int = 500
    experience_bonus: int = 120
    # 過去の出勤実績 1 回あたりの追加加点（experience_bonus への上乗せ）。
    # 実績が多い人ほどその現場の優先度を上げる。
    experience_count_bonus: int = 12
    # 実績加点の上限（ベテランが他要素を支配しすぎないよう頭打ちにする）。
    experience_count_bonus_cap: int = 180
    # 研修（TRAIN オプション）を受けた現場。「教わったが一人ではやっていない」
    # 状態のため、実績（代務 SUB を含む）より一段低い習熟度として加点する
    trained_site_bonus: int = 60
    option_aptitude_bonus: int = 80
    preferred_weekday_bonus: int = 40
    blocked_weekday_penalty: int = 250
    soft_unavailable_penalty: int = 300
    consecutive_day_penalty: int = 80
    monthly_imbalance_penalty: int = 60
    weekend_imbalance_penalty: int = 50
    change_penalty: int = 100
    trainee_penalty: int = 30
    worker_weight_unit: int = 100  # Rule(worker_weight) の 1 単位あたりの加減点
    pair_together_bonus: int = 60
    pair_avoid_penalty: int = 200
    fixed_weekday_bonus: int = 90


@dataclass(frozen=True)
class OptionExperiencePolicy:
    """特定オプションを「未経験不可」にする現場ごとの任意設定。"""

    option_key: str
    require_prior_experience: bool
    site_row_id: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class Rule:
    """人物単位の好み・固定/禁止・手動ルールを 1 つの型で表す。"""

    rule_id: str
    kind: Literal[
        "pinned", "forbidden", "pair_together", "pair_avoid",
        "worker_weight", "fixed_weekday", "manual",
    ]
    enabled: bool = True
    priority: int = 100
    effective_from: date | None = None
    effective_to: date | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OptionToggle:
    """汎用・将来拡張用の任意オプション（既定は無効＝コア挙動）。"""

    key: str
    enabled: bool = False
    scope: Literal["site", "branch", "option_key", "worker", "weekday"] = "site"
    target: str = ""
    value: Any = None
    note: str = ""


@dataclass(frozen=True)
class ShiftEngineSettings:
    """project["shift_engine"] に保存するエンジン設定一式。"""

    version: int
    demand_rules: list[DemandRule] = field(default_factory=list)
    leave_policies: list[LeavePolicy] = field(default_factory=list)
    default_preferences: PlanningPreferences | None = None
    worker_limits: list[WorkerLimit] = field(default_factory=list)
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)
    option_experience_policies: list[OptionExperiencePolicy] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    advanced_options: list[OptionToggle] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 入力モデル
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteRef:
    site_row_id: str
    site_id: str
    site_name: str


@dataclass(frozen=True)
class PlanningDay:
    date: date
    day: int
    weekday: int  # 月=0 … 日=6（date.weekday() と同規約）
    is_holiday: bool
    is_weekend: bool
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequiredSlot:
    slot_id: str
    date: date
    day: int
    shift_key: str
    shift_label: str
    required_count: int
    site_row_id: str = ""
    site_id: str = ""
    site_name: str = ""
    site_branch_row_id: str = ""
    site_branch: str = ""
    required_qualification_codes: tuple[str, ...] = ()
    required_vehicle_options: tuple[str, ...] = ()
    time_band: str = ""
    priority: int = 100
    source: Literal[
        "settings", "required_capacity_fallback", "prev_month_estimate",
        "existing_entry", "target_override",
    ] = "settings"


@dataclass(frozen=True)
class WorkerPreference:
    preferred_weekdays: tuple[int, ...] = ()
    blocked_weekdays: tuple[int, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class WorkerMonthlyLimit:
    min_assignments: int | None = None
    max_assignments: int | None = None
    prior_month_tail_consecutive: int = 0


@dataclass(frozen=True)
class Worker:
    worker_id: str
    employee_number: str
    name: str
    active: bool
    employee_type: str = ""
    office_name: str = ""
    office_code: str = ""
    dedicated_site_ids: tuple[str, ...] = ()
    dedicated_site_row_ids: tuple[str, ...] = ()
    # 実績のある現場。代務（SUB）で一人で勤務した現場も「実績」としてここに入る
    experienced_site_row_ids: tuple[str, ...] = ()
    # 研修（TRAIN）を受けた現場（研修済み）。「教わったが一人ではやっていない」
    # ため実績より一段低い習熟度で適格になる
    trained_site_row_ids: tuple[str, ...] = ()
    trainee_site_row_ids: tuple[str, ...] = ()
    experienced_option_keys: tuple[str, ...] = ()
    # 対象現場での過去の出勤実績数（確定シフトの回数）。実績が多い人ほど
    # その現場の優先度を上げるために使う（experience_count_bonus で加点）。
    site_experience_count: int = 0
    qualification_codes: tuple[str, ...] = ()  # 予約
    vehicle_options: tuple[str, ...] = ()  # 予約（legacy）
    capable_shift_keys: tuple[str, ...] = ()  # 予約（legacy）
    preference: WorkerPreference | None = None
    monthly_limit: WorkerMonthlyLimit | None = None
    worker_weight: int = 0  # Rule(worker_weight) を adapter が解決した個別加減点単位


@dataclass(frozen=True)
class ExistingAssignment:
    assignment_id: str
    date: date
    day: int
    slot_key: str
    shift_key: str
    employee_number: str
    employee_name: str
    source_type: Literal[
        "manual", "scene_sync", "person_sync", "master_sync", "substitute_sync", "engine"
    ]
    entry_id: str
    lock_policy: Literal["locked", "manual_locked", "movable", "replaceable"]
    site_row_id: str = ""
    site_id: str = ""
    site_name: str = ""
    site_branch_row_id: str = ""
    site_branch: str = ""


@dataclass(frozen=True)
class ExternalAssignment:
    employee_number: str
    employee_name: str
    date: date
    day: int
    shift_key: str
    project_id: str
    project_title: str
    source_mode: Literal["scene", "person", "substitute", "master"]
    confirmed: bool = True


@dataclass(frozen=True)
class UnavailableDay:
    employee_number: str
    date: date
    reason: str
    # shift_entry: 対象/関連シフト帳の有休系オプション entry 由来の不可日
    source: Literal["leave_mgr", "assist_profile", "manual_rule", "person_shift", "shift_entry"]
    strength: Literal["hard", "soft", "info"]
    confirmed: bool = True


@dataclass(frozen=True)
class ShiftPlanningRequest:
    request_id: str
    version: int
    target_project_id: str
    target_site: SiteRef
    year: int
    month: int
    base_revision: int
    days: list[PlanningDay]
    required_slots: list[RequiredSlot]
    workers: list[Worker]
    existing_assignments: list[ExistingAssignment]
    external_assignments: list[ExternalAssignment]
    unavailable_days: list[UnavailableDay]
    rules: list[Rule]
    preferences: PlanningPreferences
    scoring_weights: ScoringWeights
    option_experience_policies: list[OptionExperiencePolicy]
    # external_occupancy を warning へ緩める現場（site_row_id の集合）。
    external_occupancy_relax_sites: tuple[str, ...] = ()
    # 自動作成で「新規配置を作ってよい日」の集合。空なら全日が対象。
    # 対象外の日も固定既存・休暇・連勤などの計算には全て含まれる（1か月全体で評価）。
    fill_target_dates: tuple[date, ...] = ()


# ---------------------------------------------------------------------------
# 出力モデル
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    slot_id: str
    slot_instance_id: str
    date: date
    day: int
    shift_key: str
    employee_number: str
    employee_name: str
    score: int
    source: Literal["engine", "existing_locked", "pinned"]
    site_row_id: str = ""
    site_id: str = ""
    site_name: str = ""
    site_branch_row_id: str = ""
    site_branch: str = ""
    shift_label: str = ""


@dataclass(frozen=True)
class UnfilledSlot:
    slot_id: str
    date: date
    day: int
    shift_key: str
    shortage: int
    reason_code: str
    reason: str


@dataclass(frozen=True)
class Violation:
    code: str
    severity: Literal["blocker", "hard", "warning", "info"]
    message: str
    date: date | None = None
    employee_number: str = ""
    slot_id: str = ""


@dataclass(frozen=True)
class PlanningWarning:
    code: str
    message: str
    date: date | None = None
    employee_number: str = ""
    slot_id: str = ""


@dataclass(frozen=True)
class ScoreFactor:
    category: Literal[
        "dedicated", "experience", "aptitude", "preference",
        "fairness", "holiday", "change", "training", "penalty",
    ]
    label: str
    points: int
    detail: str = ""


@dataclass(frozen=True)
class Explanation:
    assignment_id: str
    slot_id: str
    employee_number: str
    summary: str
    factors: list[ScoreFactor]


@dataclass(frozen=True)
class ScoreSummary:
    total_score: int
    fill_rate: float
    assigned_count: int
    required_count: int
    unfilled_count: int
    blocker_count: int
    hard_violation_count: int
    warning_count: int
    fairness_index: float
    weekend_balance_index: float
    max_consecutive_days: int
    changed_existing_count: int


@dataclass(frozen=True)
class CandidateScore:
    """候補パネルの 1 候補（最終状態での counterfactual スコア）。"""

    employee_number: str
    name: str
    score: int
    selected: bool
    factors: list[ScoreFactor] = field(default_factory=list)


@dataclass(frozen=True)
class SlotCandidatePanel:
    """「なぜこの人が選ばれたか・他に誰が候補だったか」を枠単位で表す。

    候補スコアは最終結果の状態（当該枠の配置だけ外した counterfactual）で
    再計算するため、配置時点ではなく確定結果に対する説明になる。
    """

    slot_instance_id: str
    slot_id: str
    date: date
    day: int
    shift_key: str
    shift_label: str
    selected_employee_number: str  # 空 = 未充足
    candidates: list[CandidateScore] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)  # 除外理由コード -> 人数
    eligible_count: int = 0


@dataclass(frozen=True)
class ShiftPlanningResult:
    request_id: str
    status: Literal["feasible", "partial", "failed", "optimal"]
    solver_backend: str
    solver_certified: bool
    base_revision: int
    assignments: list[Assignment]
    unfilled_slots: list[UnfilledSlot]
    violations: list[Violation]
    warnings: list[PlanningWarning]
    score: ScoreSummary
    explanations: list[Explanation]
    request_hash: str = ""
    candidate_panels: list[SlotCandidatePanel] = field(default_factory=list)


@dataclass(frozen=True)
class SolverLimits:
    """サーバー側の安全弁（PlanningPreferences には含めない運用値）。"""

    max_solver_seconds: float = 15.0
    max_local_search_iterations: int = 200
    max_candidates_per_slot: int = 0  # 0 = 無制限
    max_plan_preview_entries: int = 5000


# 未配置理由コード → 人間向け文言
REASON_MESSAGES: dict[str, str] = {
    "no_candidates": "候補者がいない",
    "all_excluded_hard": "候補者はいるが全員 Hard 制約で除外された",
    "no_eligible": "専従・経験者の適格候補がいない（やったことがある人がいない）",
    "option_experience_required": "オプション未経験不可で適格者がいない",
    "below_min_score": "最良候補のスコアが下限未満",
    "no_capacity": "固定配置や上限により割当余地がない",
    "partial_allowed": "Soft 制約を優先し未充足を許可した",
}


# ---------------------------------------------------------------------------
# 既定プロファイル
# ---------------------------------------------------------------------------


def default_planning_preferences() -> PlanningPreferences:
    """コア既定プロファイルと一致する既定方針を返す。"""
    return PlanningPreferences(
        existing_policy="lock_manual",
        allow_partial=True,
        eligibility_baseline="dedicated_or_experienced",
        min_assignment_score=None,
        # 有休共有ツールの休みは確認有無に関わらず既定で配置対象から外す
        # （休みが入っている日に自動作成がシフトを入れない、を最優先する）。
        unconfirmed_leave_strength="hard",
        max_consecutive_days=None,
        min_monthly_assignments=None,
        max_monthly_assignments=None,
        include_trainees=False,
        prefer_dedicated=True,
        prefer_experienced=True,
        prefer_fairness=True,
        minimize_changes=True,
        suppress_weekend_imbalance=True,
        monthly_limit_hard=True,
        max_consecutive_days_hard=False,
    )


def default_leave_policies() -> list[LeavePolicy]:
    """有休種別ごとの既定不可強度。

    有休共有ツールに登録された休みは、種別を問わず既定で hard（その日は
    自動作成の配置対象から外す）。緩めたい種別だけ設定で soft/info に変える。
    """
    return [
        LeavePolicy("有休", "hard"),
        LeavePolicy("代休", "hard"),
        LeavePolicy("公休", "hard"),
        LeavePolicy("慶弔休暇", "hard"),
        LeavePolicy("介護休暇", "hard"),
        LeavePolicy("リフレッシュ休暇", "hard"),
        LeavePolicy("その他", "hard"),
    ]


def build_default_settings() -> ShiftEngineSettings:
    """無設定の現場でも合理的に動くコア既定設定を構成する。"""
    return ShiftEngineSettings(
        version=SETTINGS_VERSION,
        demand_rules=[],
        leave_policies=default_leave_policies(),
        default_preferences=default_planning_preferences(),
        worker_limits=[],
        scoring_weights=ScoringWeights(),
        option_experience_policies=[],
        rules=[],
        advanced_options=[],
    )


def build_planning_days(
    year: int, month: int, labels: dict[int, tuple[str, ...]] | None = None
) -> list[PlanningDay]:
    """対象月の PlanningDay を monthrange 準拠で生成する（祝日は JAPAN_HOLIDAYS 参照）。"""
    labels = labels or {}
    days_in_month = monthrange(year, month)[1]
    result: list[PlanningDay] = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        weekday = d.weekday()
        is_holiday = d.isoformat() in _HOLIDAY_SET
        is_weekend = weekday >= 5
        result.append(
            PlanningDay(
                date=d,
                day=day,
                weekday=weekday,
                is_holiday=is_holiday,
                is_weekend=is_weekend,
                labels=tuple(labels.get(day, ())),
            )
        )
    return result


def _opt(shift_key: str | None) -> str | None:
    """空文字を None に正規化（is_duplicate_by_rules はオプション無しを None で扱う）。"""
    text = str(shift_key or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# request 検証
# ---------------------------------------------------------------------------


def validate_request(request: ShiftPlanningRequest) -> list[Violation]:
    """生成前の入力検証。重大な不整合は blocker として返す。"""
    violations: list[Violation] = []

    if not (1 <= request.month <= 12):
        violations.append(Violation("invalid_month", "blocker", f"月が不正です: {request.month}"))
        return violations
    if request.year < 1900 or request.year > 2100:
        violations.append(Violation("invalid_year", "blocker", f"年が不正です: {request.year}"))
        return violations

    days_in_month = monthrange(request.year, request.month)[1]
    if len(request.days) != days_in_month:
        violations.append(
            Violation(
                "days_mismatch",
                "blocker",
                f"days 件数 {len(request.days)} が対象月の日数 {days_in_month} と一致しません",
            )
        )

    if request.base_revision is None or int(request.base_revision) < 0:
        violations.append(
            Violation("invalid_base_revision", "blocker", "base_revision が不正です")
        )

    # employee_number の重複禁止
    seen_numbers: set[str] = set()
    for worker in request.workers:
        number = str(worker.employee_number or "").strip()
        if not number:
            continue
        if number in seen_numbers:
            violations.append(
                Violation(
                    "duplicate_worker",
                    "blocker",
                    f"employee_number が重複しています: {number}",
                    employee_number=number,
                )
            )
        seen_numbers.add(number)

    # required_count >= 0 / slot 日付が対象月内
    for slot in request.required_slots:
        if slot.required_count < 0:
            violations.append(
                Violation(
                    "negative_required_count",
                    "blocker",
                    f"required_count が負です: {slot.slot_id}",
                    slot_id=slot.slot_id,
                )
            )
        if slot.date.year != request.year or slot.date.month != request.month:
            violations.append(
                Violation(
                    "slot_out_of_month",
                    "blocker",
                    f"slot の日付が対象月外です: {slot.slot_id} ({slot.date.isoformat()})",
                    slot_id=slot.slot_id,
                    date=slot.date,
                )
            )

    worker_numbers = {str(w.employee_number or "").strip() for w in request.workers}

    # existing_assignments の employee が worker に存在
    for existing in request.existing_assignments:
        number = str(existing.employee_number or "").strip()
        if number and number not in worker_numbers:
            violations.append(
                Violation(
                    "existing_unknown_worker",
                    "warning",
                    f"既存配置の社員番号が候補者に存在しません: {number}",
                    employee_number=number,
                    date=existing.date,
                )
            )

    # unavailable_days / external_assignments の日付が対象月内
    for unavailable in request.unavailable_days:
        if unavailable.date.year != request.year or unavailable.date.month != request.month:
            violations.append(
                Violation(
                    "unavailable_out_of_month",
                    "warning",
                    f"不可日が対象月外です: {unavailable.employee_number} {unavailable.date.isoformat()}",
                    employee_number=unavailable.employee_number,
                    date=unavailable.date,
                )
            )
    for external in request.external_assignments:
        if external.date.year != request.year or external.date.month != request.month:
            violations.append(
                Violation(
                    "external_out_of_month",
                    "warning",
                    f"他現場占有が対象月外です: {external.employee_number} {external.date.isoformat()}",
                    employee_number=external.employee_number,
                    date=external.date,
                )
            )

    # 対象日指定が対象月内に収まっているか（月外は無視されるため warning）
    for target in request.fill_target_dates:
        if target.year != request.year or target.month != request.month:
            violations.append(
                Violation(
                    "fill_target_out_of_month",
                    "warning",
                    f"自動作成の対象日が対象月外です（無視されます）: {target.isoformat()}",
                    date=target,
                )
            )

    return violations


# ---------------------------------------------------------------------------
# 内部: 割当単位と固定 seed
# ---------------------------------------------------------------------------


@dataclass
class _Instance:
    """RequiredSlot を割当単位（slot_id#n）へ展開した内部表現。"""

    instance_id: str
    slot: RequiredSlot
    fixed_worker: str | None = None  # locked/manual_locked/pinned で確保された社員番号
    fixed_source: Literal["existing_locked", "pinned"] | None = None
    fixed_assignment_id: str = ""
    original_worker: str | None = None  # movable/replaceable の元担当（変更最小用）


@dataclass
class _SeedConflict:
    severity: Literal["blocker", "hard", "warning"]
    code: str
    message: str
    employee_number: str = ""
    date: date | None = None
    slot_id: str = ""


# ---------------------------------------------------------------------------
# 内部: 生成コンテキスト（precompute 済みの参照）
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self, request: ShiftPlanningRequest, limits: SolverLimits):
        self.request = request
        self.limits = limits
        self.prefs = request.preferences
        self.weights = request.scoring_weights
        self.first_day = date(request.year, request.month, 1)
        self.month = request.month

        self.day_by_date: dict[date, PlanningDay] = {d.date: d for d in request.days}
        self.existing_by_id: dict[str, ExistingAssignment] = {
            a.assignment_id: a for a in request.existing_assignments
        }
        self.worker_by_number: dict[str, Worker] = {
            str(w.employee_number or "").strip(): w
            for w in request.workers
            if str(w.employee_number or "").strip()
        }

        # 休暇: hard / soft(+info) を引けるようにする
        self.hard_unavailable: set[tuple[str, date]] = set()
        self.soft_unavailable: dict[tuple[str, date], str] = {}
        for u in request.unavailable_days:
            number = str(u.employee_number or "").strip()
            if not number:
                continue
            if u.strength == "hard":
                self.hard_unavailable.add((number, u.date))
            elif u.strength == "soft":
                self.soft_unavailable[(number, u.date)] = u.reason

        # 他現場占有: 番号一致できるものだけを Hard 判定に使う
        self.external_by_emp_date: dict[tuple[str, date], list[str | None]] = {}
        for ext in request.external_assignments:
            number = str(ext.employee_number or "").strip()
            if not number:
                continue
            self.external_by_emp_date.setdefault((number, ext.date), []).append(
                _opt(ext.shift_key)
            )

        self.relax_sites = set(request.external_occupancy_relax_sites)

        # 新規配置の対象日（空＝全日対象）。対象外日は固定の保持と制約計算のみ行う。
        valid_dates = set(self.day_by_date.keys())
        self.fill_target_dates: set[date] = {
            d for d in request.fill_target_dates if d in valid_dates
        }
        self.fill_all_dates = not request.fill_target_dates

        # 月次最低勤務数（個人別 > 全体方針）。最低保証までの配置は「偏り」として罰しない。
        self.min_target_by_number: dict[str, int] = {}
        for w in request.workers:
            limit = w.monthly_limit
            min_target = (
                limit.min_assignments
                if limit and limit.min_assignments is not None
                else request.preferences.min_monthly_assignments
            )
            number = str(w.employee_number or "").strip()
            if min_target and number:
                self.min_target_by_number[number] = min_target

        # ルール展開
        self.forbidden: set[tuple[str, date, str]] = set()
        self.pair_together: list[tuple[str, str]] = []
        self.pair_avoid: list[tuple[str, str]] = []
        self.fixed_weekday: dict[str, set[int]] = {}
        for rule in request.rules:
            if not rule.enabled:
                continue
            if not self._rule_active(rule):
                continue
            self._index_rule(rule)

        # オプション未経験不可ポリシー（site 別）
        self.option_policies: list[OptionExperiencePolicy] = [
            p for p in request.option_experience_policies if p.enabled and p.require_prior_experience
        ]

    def is_fill_target(self, d: date) -> bool:
        """この日に新規配置を作ってよいか（対象日指定が無ければ常に True）。"""
        return self.fill_all_dates or d in self.fill_target_dates

    def _rule_active(self, rule: Rule) -> bool:
        """有効期間内かを判定（月内のどこかで有効なら True）。"""
        if rule.effective_from and rule.effective_from > date(self.request.year, self.request.month, monthrange(self.request.year, self.request.month)[1]):
            return False
        if rule.effective_to and rule.effective_to < self.first_day:
            return False
        return True

    def _index_rule(self, rule: Rule) -> None:
        params = rule.params or {}
        if rule.kind == "forbidden":
            number = str(params.get("employee_number") or "").strip()
            shift_key = str(params.get("shift_key") or "").strip()
            for d in self._rule_dates(rule, params):
                if number:
                    self.forbidden.add((number, d, shift_key))
        elif rule.kind == "pair_together":
            pair = self._rule_pair(params)
            if pair:
                self.pair_together.append(pair)
        elif rule.kind == "pair_avoid":
            pair = self._rule_pair(params)
            if pair:
                self.pair_avoid.append(pair)
        elif rule.kind == "fixed_weekday":
            number = str(params.get("employee_number") or "").strip()
            weekday = params.get("weekday")
            if number and weekday is not None:
                try:
                    self.fixed_weekday.setdefault(number, set()).add(int(weekday))
                except (TypeError, ValueError):
                    pass

    @staticmethod
    def _rule_pair(params: dict[str, Any]) -> tuple[str, str] | None:
        numbers = params.get("employee_numbers")
        if isinstance(numbers, (list, tuple)) and len(numbers) >= 2:
            a = str(numbers[0] or "").strip()
            b = str(numbers[1] or "").strip()
        else:
            a = str(params.get("a") or "").strip()
            b = str(params.get("b") or "").strip()
        if a and b and a != b:
            return (a, b)
        return None

    def _rule_dates(self, rule: Rule, params: dict[str, Any]) -> list[date]:
        """forbidden/pinned が効く日付の集合を返す。"""
        result: list[date] = []
        raw_date = params.get("date")
        if raw_date:
            d = _parse_iso_date(raw_date)
            if d and d in self.day_by_date:
                result.append(d)
        raw_day = params.get("day")
        if raw_day is not None and not raw_date:
            try:
                d = date(self.request.year, self.request.month, int(raw_day))
                if d in self.day_by_date:
                    result.append(d)
            except (TypeError, ValueError):
                pass
        return result


# ---------------------------------------------------------------------------
# 内部: 可変プラン状態
# ---------------------------------------------------------------------------


class _PlanState:
    def __init__(self) -> None:
        # instance_id -> employee_number
        self.placed: dict[str, str] = {}
        # employee_number -> {date -> [option|None, ...]}
        self.day_options: dict[str, dict[date, list[str | None]]] = {}
        # employee_number -> set(date)
        self.worked_dates: dict[str, set[date]] = {}
        # employee_number -> count
        self.month_count: dict[str, int] = {}
        self.weekend_count: dict[str, int] = {}

    def add(self, instance_id: str, number: str, d: date, option: str | None, is_weekend: bool) -> None:
        self.placed[instance_id] = number
        self.day_options.setdefault(number, {}).setdefault(d, []).append(option)
        if option in LEAVE_OPTION_KEYS:
            # 有休系オプションは「休みの記録」。勤務日数・連勤・土日祝の計算に入れない。
            return
        self.worked_dates.setdefault(number, set()).add(d)
        self.month_count[number] = self.month_count.get(number, 0) + 1
        if is_weekend:
            self.weekend_count[number] = self.weekend_count.get(number, 0) + 1

    def remove(self, instance_id: str, d: date, option: str | None, is_weekend: bool) -> None:
        number = self.placed.pop(instance_id, None)
        if number is None:
            return
        options = self.day_options.get(number, {}).get(d)
        if options and option in options:
            options.remove(option)
            if not options:
                self.day_options[number].pop(d, None)
        if option in LEAVE_OPTION_KEYS:
            return
        # その日にもう配置が無ければ worked_dates から外す
        if not self.day_options.get(number, {}).get(d):
            self.worked_dates.get(number, set()).discard(d)
        self.month_count[number] = max(0, self.month_count.get(number, 0) - 1)
        if is_weekend:
            self.weekend_count[number] = max(0, self.weekend_count.get(number, 0) - 1)


# ---------------------------------------------------------------------------
# 内部: Hard / 適格性 判定
# ---------------------------------------------------------------------------


def _site_match(values: tuple[str, ...], site_row_id: str) -> bool:
    if not site_row_id:
        return bool(values)
    return site_row_id in values


def _baseline_ok(worker: Worker, slot: RequiredSlot, prefs: PlanningPreferences) -> bool:
    """配置の最低基準（専従/実績/研修済み、要研修者は include_trainees 時のみ）。"""
    if prefs.eligibility_baseline == "any":
        return True
    site = slot.site_row_id
    if _site_match(worker.dedicated_site_row_ids, site):
        return True
    if _site_match(worker.experienced_site_row_ids, site):
        return True
    # 研修済み（教わった現場）も「現場を知っている人」として適格
    if _site_match(worker.trained_site_row_ids, site):
        return True
    if prefs.include_trainees and _site_match(worker.trainee_site_row_ids, site):
        return True
    return False


def _option_experience_ok(
    worker: Worker, slot: RequiredSlot, policies: list[OptionExperiencePolicy]
) -> bool:
    """未経験不可ポリシーに該当する枠で、当人がオプション経験者かを判定。"""
    option = str(slot.shift_key or "").strip()
    if not option:
        return True
    for policy in policies:
        if policy.option_key != option:
            continue
        if policy.site_row_id and policy.site_row_id != slot.site_row_id:
            continue
        # 該当ポリシーあり: 経験必須
        return option in worker.experienced_option_keys
    return True


def _consecutive_run_if_placed(
    worked_dates: set[date],
    d: date,
    first_day: date,
    prior_tail: int,
    cross_month: bool,
) -> int:
    """worker を日付 d に置いたときに d を含む連勤 run の長さ。

    前方（d より後の既配置）も数える。固定既存や先に埋めた枠が月内に散らばるため、
    後ろ向きだけ数えると「既存の連勤の直前に置いて run を橋渡しする」配置を
    見落とし、連勤上限（Hard）も連勤ペナルティ（Soft）もすり抜けてしまう。
    """
    run = 1
    cur = d
    while True:
        prev = cur - timedelta(days=1)
        if prev < first_day:
            break
        if prev in worked_dates:
            run += 1
            cur = prev
        else:
            break
    nxt = d + timedelta(days=1)
    while nxt in worked_dates:
        run += 1
        nxt += timedelta(days=1)
    if cross_month and cur == first_day and prior_tail > 0:
        run += prior_tail
    return run


def _hard_ok(
    ctx: _Context, state: _PlanState, worker: Worker, slot: RequiredSlot, day: PlanningDay
) -> tuple[bool, str]:
    """動的 Hard 制約（同日重複・他現場占有・上限・連勤上限・forbidden）を判定。"""
    number = worker.employee_number
    option = _opt(slot.shift_key)

    # forbidden
    if (number, day.date, str(slot.shift_key or "").strip()) in ctx.forbidden:
        return False, "forbidden"

    # 同一現場の同日重複
    for existing_opt in state.day_options.get(number, {}).get(day.date, ()):  # type: ignore[arg-type]
        if is_duplicate_by_rules(option, existing_opt, same_site=True):
            return False, "dup_same_site"

    # 他現場占有（番号一致のみ Hard）
    if slot.site_row_id not in ctx.relax_sites:
        for ext_opt in ctx.external_by_emp_date.get((number, day.date), ()):  # type: ignore[arg-type]
            if is_duplicate_by_rules(option, ext_opt, same_site=False):
                return False, "external_dup"

    # 月次上限（Hard 設定時）
    limit = worker.monthly_limit
    max_assignments = limit.max_assignments if limit else None
    if max_assignments is None:
        max_assignments = ctx.prefs.max_monthly_assignments
    if ctx.prefs.monthly_limit_hard and max_assignments is not None:
        if state.month_count.get(number, 0) >= max_assignments:
            return False, "monthly_max"

    # 連勤上限（Hard 設定時のみ）
    if ctx.prefs.max_consecutive_days_hard and ctx.prefs.max_consecutive_days:
        prior_tail = limit.prior_month_tail_consecutive if limit else 0
        run = _consecutive_run_if_placed(
            state.worked_dates.get(number, set()),
            day.date,
            ctx.first_day,
            prior_tail,
            cross_month=True,
        )
        if run > ctx.prefs.max_consecutive_days:
            return False, "consecutive_max"

    return True, ""


def _static_exclusion_reason(
    ctx: _Context, worker: Worker, slot: RequiredSlot, day: PlanningDay
) -> str | None:
    """状態に依存しない除外理由コード（None = 適格）。

    在籍・番号・hard 休暇・最低基準・未経験不可を区別して返し、
    候補パネルの除外内訳（「なぜこの人は候補に出ないか」）に使う。
    """
    if not worker.active:
        return "inactive"
    if not worker.employee_number:
        return "no_number"
    if (worker.employee_number, day.date) in ctx.hard_unavailable:
        return "hard_leave"
    if not _baseline_ok(worker, slot, ctx.prefs):
        return "baseline"
    if not _option_experience_ok(worker, slot, ctx.option_policies):
        return "option_experience"
    return None


def _static_eligible(ctx: _Context, worker: Worker, slot: RequiredSlot, day: PlanningDay) -> bool:
    """状態に依存しない適格性（在籍・番号・hard 休暇・最低基準・未経験不可）。"""
    return _static_exclusion_reason(ctx, worker, slot, day) is None


# 候補パネルの除外理由コード → 人間向けラベル
EXCLUSION_LABELS: dict[str, str] = {
    "inactive": "在籍対象外",
    "no_number": "社員番号なし",
    "hard_leave": "休暇・休み",
    "baseline": "専従・経験なし",
    "option_experience": "オプション未経験",
    "forbidden": "配置禁止ルール",
    "dup_same_site": "同日に配置済み",
    "external_dup": "他現場で勤務",
    "monthly_max": "月次上限到達",
    "consecutive_max": "連勤上限",
    "below_min_score": "スコア下限未満",
}


# ---------------------------------------------------------------------------
# 内部: スコアリング
# ---------------------------------------------------------------------------


def _score_candidate(
    ctx: _Context, state: _PlanState, instance: _Instance, worker: Worker, day: PlanningDay
) -> tuple[int, list[ScoreFactor]]:
    """Soft Constraint の重みで候補者をスコアリングし、説明用 factor も返す。"""
    weights = ctx.weights
    prefs = ctx.prefs
    slot = instance.slot
    number = worker.employee_number
    factors: list[ScoreFactor] = []
    score = 0

    # 専従者優先
    if prefs.prefer_dedicated and _site_match(worker.dedicated_site_row_ids, slot.site_row_id):
        score += weights.dedicated_bonus
        factors.append(ScoreFactor("dedicated", "専従者", weights.dedicated_bonus, "対象現場の専従者"))
    # 現場習熟度（実績（代務含む）> 研修済み。重複加点せず上位のみ）
    if prefs.prefer_experienced:
        if _site_match(worker.experienced_site_row_ids, slot.site_row_id):
            score += weights.experience_bonus
            factors.append(ScoreFactor("experience", "経験者", weights.experience_bonus, "対象現場の実績あり（代務含む）"))
            # 実績の厚み: 過去の出勤回数が多い人ほど上乗せ（上限あり）。
            if worker.site_experience_count > 0 and weights.experience_count_bonus > 0:
                count_bonus = min(
                    weights.experience_count_bonus_cap,
                    worker.site_experience_count * weights.experience_count_bonus,
                )
                score += count_bonus
                factors.append(ScoreFactor(
                    "experience", "実績の厚み", count_bonus,
                    f"過去 {worker.site_experience_count} 回の実績",
                ))
        elif _site_match(worker.trained_site_row_ids, slot.site_row_id):
            score += weights.trained_site_bonus
            factors.append(ScoreFactor("experience", "研修済み", weights.trained_site_bonus, "研修を受けた現場（一人での実績はまだ）"))
    # オプション適性（過去実績）
    option = str(slot.shift_key or "").strip()
    if option and option in worker.experienced_option_keys:
        score += weights.option_aptitude_bonus
        factors.append(ScoreFactor("aptitude", "オプション適性", weights.option_aptitude_bonus, f"{option} の実績あり"))
    # 要研修者の減点（現場をまだ知らない人。実績・研修済みの人は対象外）
    if _site_match(worker.trainee_site_row_ids, slot.site_row_id) and not (
        _site_match(worker.experienced_site_row_ids, slot.site_row_id)
        or _site_match(worker.dedicated_site_row_ids, slot.site_row_id)
        or _site_match(worker.trained_site_row_ids, slot.site_row_id)
    ):
        score -= weights.trainee_penalty
        factors.append(ScoreFactor("training", "研修者", -weights.trainee_penalty, "経験前の研修対象"))

    # 希望/NG 曜日
    pref = worker.preference
    if pref:
        if day.weekday in pref.preferred_weekdays:
            score += weights.preferred_weekday_bonus
            factors.append(ScoreFactor("preference", "希望曜日", weights.preferred_weekday_bonus))
        if day.weekday in pref.blocked_weekdays:
            score -= weights.blocked_weekday_penalty
            factors.append(ScoreFactor("preference", "NG曜日", -weights.blocked_weekday_penalty))

    # fixed_weekday（固定担当曜日）
    if day.weekday in ctx.fixed_weekday.get(number, set()):
        score += weights.fixed_weekday_bonus
        factors.append(ScoreFactor("preference", "固定担当曜日", weights.fixed_weekday_bonus))

    # soft 休暇・未確認休暇の回避
    if (number, day.date) in ctx.soft_unavailable:
        score -= weights.soft_unavailable_penalty
        factors.append(ScoreFactor("penalty", "soft休暇", -weights.soft_unavailable_penalty))

    # 連勤抑制（Soft）
    limit = worker.monthly_limit
    prior_tail = limit.prior_month_tail_consecutive if limit else 0
    run = _consecutive_run_if_placed(
        state.worked_dates.get(number, set()), day.date, ctx.first_day, prior_tail, cross_month=True
    )
    threshold = prefs.max_consecutive_days if prefs.max_consecutive_days else CONSECUTIVE_SOFT_DEFAULT
    if run > threshold:
        penalty = weights.consecutive_day_penalty * (run - threshold)
        score -= penalty
        factors.append(ScoreFactor("penalty", "連勤", -penalty, f"連勤 {run} 日"))

    # 月次偏り抑制（最低保証を超えた分が多いほど減点）
    min_target = ctx.min_target_by_number.get(number, 0)
    if prefs.prefer_fairness:
        current = state.month_count.get(number, 0)
        over = current - min_target
        if over > 0:
            penalty = weights.monthly_imbalance_penalty * over
            score -= penalty
            factors.append(ScoreFactor("fairness", "勤務数偏り", -penalty, f"今月 {current} 件"))

    # 月次最低勤務数（下回っている人を優先して引き上げる）
    if min_target:
        current = state.month_count.get(number, 0)
        if current < min_target:
            bonus = weights.monthly_imbalance_penalty * UNDER_MIN_MULTIPLIER * (min_target - current)
            score += bonus
            factors.append(ScoreFactor("fairness", "最低勤務数未達", bonus, f"今月 {current}/{min_target} 件"))

    # 土日祝偏り抑制
    if prefs.suppress_weekend_imbalance and (day.is_weekend or day.is_holiday):
        current_weekend = state.weekend_count.get(number, 0)
        if current_weekend > 0:
            penalty = weights.weekend_imbalance_penalty * current_weekend
            score -= penalty
            factors.append(ScoreFactor("holiday", "土日祝偏り", -penalty, f"土日祝 {current_weekend} 件"))

    # 変更最小化（元担当を優先）
    if prefs.minimize_changes and instance.original_worker:
        if instance.original_worker == number:
            score += weights.change_penalty
            factors.append(ScoreFactor("change", "現状維持", weights.change_penalty, "既存からの変更なし"))

    # 人物単位の重み
    if worker.worker_weight:
        delta = worker.worker_weight * weights.worker_weight_unit
        score += delta
        factors.append(ScoreFactor("preference", "個別調整", delta))

    # ペア（同日同枠の相手有無で加減点）
    score += _pair_adjustment(ctx, state, instance, number, day, factors)

    return score, factors


def _pair_adjustment(
    ctx: _Context,
    state: _PlanState,
    instance: _Instance,
    number: str,
    day: PlanningDay,
    factors: list[ScoreFactor],
) -> int:
    """pair_together / pair_avoid の同日加減点。"""
    delta = 0
    same_day_numbers = {
        emp for emp, days in state.day_options.items() if day.date in days
    }
    for a, b in ctx.pair_avoid:
        partner = None
        if number == a:
            partner = b
        elif number == b:
            partner = a
        if partner and partner in same_day_numbers:
            delta -= ctx.weights.pair_avoid_penalty
            factors.append(ScoreFactor("penalty", "回避ペア", -ctx.weights.pair_avoid_penalty))
    for a, b in ctx.pair_together:
        partner = None
        if number == a:
            partner = b
        elif number == b:
            partner = a
        if partner and partner in same_day_numbers:
            delta += ctx.weights.pair_together_bonus
            factors.append(ScoreFactor("preference", "推奨ペア", ctx.weights.pair_together_bonus))
    return delta


# ---------------------------------------------------------------------------
# GreedyRepairSolver
# ---------------------------------------------------------------------------


class GreedyRepairSolver:
    """依存追加なしの貪欲＋局所修復ソルバー（Phase 1）。"""

    backend = SOLVER_BACKEND

    def __init__(self, limits: SolverLimits | None = None) -> None:
        self.limits = limits or SolverLimits()

    def solve(self, request: ShiftPlanningRequest) -> ShiftPlanningResult:
        start = time.monotonic()
        violations: list[Violation] = []
        warnings: list[PlanningWarning] = []

        # 1) request 検証
        request_violations = validate_request(request)
        violations.extend(request_violations)
        if any(v.severity == "blocker" for v in request_violations):
            return self._failed_result(request, violations, warnings)

        ctx = _Context(request, self.limits)

        # 2) 割当単位へ展開 ＋ 固定 seed
        instances, seed_conflicts, seed_warnings = self._build_instances(ctx)
        warnings.extend(seed_warnings)

        state = _PlanState()
        self._apply_seeds(ctx, state, instances, seed_conflicts, violations)

        seed_blocked = any(c.severity == "blocker" for c in seed_conflicts)

        # 対象日に新規枠が無い場合の診断警告（「対象日なのに配置されない」の最多原因）
        warnings.extend(self._target_day_warnings(ctx, instances))

        # 3) 貪欲割当
        explanations_map: dict[str, list[ScoreFactor]] = {}
        unfilled_reason: dict[str, tuple[str, str]] = {}
        if not seed_blocked:
            self._greedy_assign(ctx, state, instances, explanations_map, unfilled_reason, start)
            # 4) 局所修復（再配置・救済移動・入替）
            self._local_repair(ctx, state, instances, explanations_map, start)

        # 5) 候補パネル（最終状態での counterfactual 比較）と説明の再構築
        panels = self._build_candidate_panels(ctx, state, instances, explanations_map)
        panel_by_instance = {p.slot_instance_id: p for p in panels}

        # 6) 出力組み立て
        assignments = self._build_assignments(ctx, state, instances, explanations_map)
        unfilled = self._build_unfilled(ctx, state, instances, unfilled_reason, panel_by_instance)
        explanations = self._build_explanations(assignments, explanations_map)

        # 7) result 検証（plan 後に必ず通す）
        violations.extend(validate_result(request, _ResultView(assignments, unfilled)))

        score = self._score_summary(ctx, state, instances, assignments, unfilled, violations, warnings)

        if seed_blocked:
            status: Literal["feasible", "partial", "failed"] = "failed"
        elif sum(u.shortage for u in unfilled) > 0:
            status = "partial"
        else:
            status = "feasible"

        request_hash = compute_request_hash(request)
        return ShiftPlanningResult(
            request_id=request.request_id,
            status=status,
            solver_backend=self.backend,
            solver_certified=False,
            base_revision=request.base_revision,
            assignments=assignments,
            unfilled_slots=unfilled,
            violations=violations,
            warnings=warnings,
            score=score,
            explanations=explanations,
            request_hash=request_hash,
            candidate_panels=panels,
        )

    def _target_day_warnings(
        self, ctx: _Context, instances: list[_Instance]
    ) -> list[PlanningWarning]:
        """対象日指定があるとき、新規に埋められる枠が無い対象日を警告する。"""
        if ctx.fill_all_dates:
            return []
        open_dates: set[date] = set()
        any_dates: set[date] = set()
        for instance in instances:
            any_dates.add(instance.slot.date)
            if instance.fixed_worker is None:
                open_dates.add(instance.slot.date)
        warnings: list[PlanningWarning] = []
        for d in sorted(ctx.fill_target_dates):
            if d in open_dates:
                continue
            if d in any_dates:
                message = (
                    f"対象日 {d.isoformat()} は固定済みの配置のみで、新規に埋める枠がありません"
                    "（需要が固定分で充足済み）"
                )
            else:
                message = (
                    f"対象日 {d.isoformat()} は必要人数が 0 のため配置されません"
                    "（需要設定・必要人数・前月実績を確認してください）"
                )
            warnings.append(PlanningWarning("target_day_no_demand", message, date=d))
        return warnings

    # -- seed / instances ---------------------------------------------------

    def _build_instances(
        self, ctx: _Context
    ) -> tuple[list[_Instance], list[_SeedConflict], list[PlanningWarning]]:
        request = ctx.request
        warnings: list[PlanningWarning] = []

        # slot を決定的に並べる
        slots = sorted(
            request.required_slots,
            key=lambda s: (s.date, s.shift_key, s.site_branch_row_id, s.slot_id),
        )

        # 固定（locked/manual_locked）と pinned を (date, shift_key, 枝番) 単位に集約。
        # 枝番を含めることで、同一日・同一 shift で枝番違いの複数 slot に
        # 同じ固定配置を二重投入してしまう不具合を防ぐ。
        fixed_by_key: dict[tuple[date, str, str], list[tuple[str, str, str]]] = {}
        # value: (employee_number, source, assignment_id)
        for existing in request.existing_assignments:
            lock_policy = existing.lock_policy
            if lock_policy in ("movable", "replaceable") and not ctx.is_fill_target(existing.date):
                # 対象外の日の既存は自動作成で動かしてはいけないため固定として扱う
                lock_policy = "locked"
            if lock_policy in ("locked", "manual_locked"):
                key = (
                    existing.date,
                    str(existing.shift_key or "").strip(),
                    str(existing.site_branch_row_id or "").strip(),
                )
                fixed_by_key.setdefault(key, []).append(
                    (existing.employee_number, "existing_locked", existing.assignment_id)
                )
        for rule in request.rules:
            if rule.kind != "pinned" or not rule.enabled:
                continue
            params = rule.params or {}
            number = str(params.get("employee_number") or "").strip()
            shift_key = str(params.get("shift_key") or "").strip()
            branch = str(params.get("site_branch_row_id") or "").strip()
            d = _parse_iso_date(params.get("date"))
            if not d and params.get("day") is not None:
                try:
                    d = date(request.year, request.month, int(params.get("day")))
                except (TypeError, ValueError):
                    d = None
            if number and d:
                fixed_by_key.setdefault((d, shift_key, branch), []).append(
                    (number, "pinned", rule.rule_id)
                )

        # movable/replaceable の元担当（変更最小用。対象外日は上で固定済みのため除く）
        movable_by_key: dict[tuple[date, str, str], list[str]] = {}
        for existing in request.existing_assignments:
            if existing.lock_policy in ("movable", "replaceable") and ctx.is_fill_target(existing.date):
                key = (
                    existing.date,
                    str(existing.shift_key or "").strip(),
                    str(existing.site_branch_row_id or "").strip(),
                )
                movable_by_key.setdefault(key, []).append(existing.employee_number)

        instances: list[_Instance] = []
        conflicts: list[_SeedConflict] = []

        for slot in slots:
            key = (slot.date, str(slot.shift_key or "").strip(), str(slot.site_branch_row_id or "").strip())
            # 固定/movable は「消費」する。これにより同一フルキーの slot が複数あっても
            # 同じ固定配置を二度割り当てない（最初の slot が確保する）。
            fixed_here = list(fixed_by_key.get(key, ()))
            if key in fixed_by_key:
                fixed_by_key[key] = []
            movable_here = list(movable_by_key.get(key, ()))
            if key in movable_by_key:
                movable_by_key[key] = []
            required = slot.required_count
            in_scope = ctx.is_fill_target(slot.date)
            if not in_scope:
                # 対象外の日は新規枠を作らない。固定既存の保持に必要な分だけ展開する
                # （連勤・公平性などの制約計算には固定分が全月分そのまま入る）。
                required = len(fixed_here)
            elif len(fixed_here) > required:
                warnings.append(
                    PlanningWarning(
                        "demand_raised_for_fixed",
                        f"固定数 {len(fixed_here)} が需要 {required} を上回るため需要を引き上げます",
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                required = len(fixed_here)

            for index in range(1, required + 1):
                instance = _Instance(instance_id=f"{slot.slot_id}#{index}", slot=slot)
                pos = index - 1
                if pos < len(fixed_here):
                    number, source, assignment_id = fixed_here[pos]
                    instance.fixed_worker = number
                    instance.fixed_source = source  # type: ignore[assignment]
                    instance.fixed_assignment_id = assignment_id
                elif pos - len(fixed_here) < len(movable_here):
                    instance.original_worker = movable_here[pos - len(fixed_here)]
                instances.append(instance)

        # どの slot にも一致しなかった固定既存は 100% 保持のため合成 slot を作る
        for key, fixed_here in sorted(fixed_by_key.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
            if not fixed_here:
                continue
            d, shift_key, branch = key
            day = ctx.day_by_date.get(d)
            if day is None:
                continue
            if shift_key not in LEAVE_OPTION_KEYS:
                # 有休系 entry は需要に無いのが当然なので警告しない（保持はする）
                warnings.append(
                    PlanningWarning(
                        "fixed_without_demand",
                        f"需要に無い固定配置を保持します（{d.isoformat()} {shift_key or '指定なし'}）",
                        date=d,
                    )
                )
            synthetic_slot = RequiredSlot(
                slot_id=f"existing-{d.isoformat()}-{shift_key or 'none'}-{branch or '0'}",
                date=d,
                day=day.day,
                shift_key=shift_key,
                shift_label=shift_key,
                required_count=len(fixed_here),
                site_row_id=request.target_site.site_row_id,
                site_id=request.target_site.site_id,
                site_name=request.target_site.site_name,
                site_branch_row_id=branch,
                source="existing_entry",
            )
            for index, (number, source, assignment_id) in enumerate(fixed_here, start=1):
                instance = _Instance(
                    instance_id=f"{synthetic_slot.slot_id}#{index}",
                    slot=synthetic_slot,
                    fixed_worker=number,
                    fixed_source=source,  # type: ignore[arg-type]
                    fixed_assignment_id=assignment_id,
                )
                instances.append(instance)

        return instances, conflicts, warnings

    def _apply_seeds(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        conflicts: list[_SeedConflict],
        violations: list[Violation],
    ) -> None:
        """固定 seed を投入し、validate_seed 相当の衝突検出を行う。

        対象外日（fill_target_dates 外）の固定は自動作成が動かせないため、
        衝突しても blocker にせず warning として保持する（生成全体を止めない）。
        """

        def record(severity, code, message, number, slot):
            conflicts.append(
                _SeedConflict(severity, code, message,
                              employee_number=number, date=slot.date, slot_id=slot.slot_id)
            )
            violations.append(
                Violation(code, severity, message,
                          employee_number=number, date=slot.date, slot_id=slot.slot_id)
            )

        for instance in instances:
            if instance.fixed_worker is None:
                continue
            number = instance.fixed_worker
            slot = instance.slot
            day = ctx.day_by_date.get(slot.date)
            if day is None:
                continue
            option = _opt(slot.shift_key)
            option_key = str(slot.shift_key or "").strip()
            # 有休系オプションの entry は「休みの記録」であり勤務ではない。
            # 本人の hard 休暇と同日でも衝突ではない（むしろ整合している）。
            is_leave_entry = option_key in LEAVE_OPTION_KEYS
            # 対象外日の固定は動かせないため、衝突は warning で保持する。
            severity = "blocker" if ctx.is_fill_target(slot.date) else "warning"

            blocked = False

            # forbidden と pinned の矛盾（ユーザー設定同士の矛盾は常に blocker）
            if instance.fixed_source == "pinned" and (
                number, slot.date, option_key
            ) in ctx.forbidden:
                record("blocker", "pinned_forbidden_conflict",
                       f"pinned と forbidden が同一枠で矛盾します: {number}", number, slot)
                continue

            # hard 休暇との衝突（休み記録 entry 自体は対象外）
            if not is_leave_entry and (number, slot.date) in ctx.hard_unavailable:
                record(severity, "seed_hard_leave",
                       f"固定配置が hard 休暇と衝突します: {number} {slot.date.isoformat()}",
                       number, slot)
                blocked = severity == "blocker"

            # 固定同士の同日重複（same_site=True）
            if not blocked:
                for existing_opt in state.day_options.get(number, {}).get(slot.date, ()):  # type: ignore[arg-type]
                    if is_duplicate_by_rules(option, existing_opt, same_site=True):
                        record(severity, "seed_duplicate",
                               f"固定配置同士が同日重複します: {number} {slot.date.isoformat()}",
                               number, slot)
                        blocked = severity == "blocker"
                        break

            # 他現場占有との衝突（same_site=False, 番号一致のみ）
            if not blocked and slot.site_row_id not in ctx.relax_sites:
                for ext_opt in ctx.external_by_emp_date.get((number, slot.date), ()):  # type: ignore[arg-type]
                    if is_duplicate_by_rules(option, ext_opt, same_site=False):
                        record(severity, "seed_external_conflict",
                               f"固定配置が他現場占有と衝突します: {number} {slot.date.isoformat()}",
                               number, slot)
                        blocked = severity == "blocker"
                        break

            if blocked:
                continue

            # 確保（warning 付きの対象外日固定もそのまま保持する）
            state.add(instance.instance_id, number, slot.date, option, day.is_weekend or day.is_holiday)

    # -- greedy -------------------------------------------------------------

    def _greedy_assign(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
        unfilled_reason: dict[str, tuple[str, str]],
        start: float,
    ) -> None:
        open_instances = [i for i in instances if i.fixed_worker is None]

        # 各 instance の静的適格候補数で並べる（制約が強い枠を先に）
        def static_count(instance: _Instance) -> int:
            slot = instance.slot
            day = ctx.day_by_date.get(slot.date)
            if day is None:
                return 0
            return sum(1 for w in ctx.request.workers if _static_eligible(ctx, w, slot, day))

        order = sorted(
            open_instances,
            key=lambda i: (
                static_count(i),
                i.slot.date,
                i.slot.shift_key,
                i.instance_id,
            ),
        )

        for instance in order:
            if self._timed_out(start):
                unfilled_reason[instance.instance_id] = ("partial_allowed", REASON_MESSAGES["partial_allowed"])
                continue
            self._fill_instance(ctx, state, instance, explanations_map, unfilled_reason)

    def _fill_instance(
        self,
        ctx: _Context,
        state: _PlanState,
        instance: _Instance,
        explanations_map: dict[str, list[ScoreFactor]],
        unfilled_reason: dict[str, tuple[str, str]],
    ) -> bool:
        slot = instance.slot
        day = ctx.day_by_date.get(slot.date)
        if day is None:
            unfilled_reason[instance.instance_id] = ("no_capacity", REASON_MESSAGES["no_capacity"])
            return False

        # 静的適格者を抽出
        static_pool = [w for w in ctx.request.workers if _static_eligible(ctx, w, slot, day)]
        if not static_pool:
            unfilled_reason[instance.instance_id] = self._diagnose_unfilled(ctx, slot, day)
            return False

        # 動的 Hard を通る候補
        candidates: list[tuple[int, Worker, list[ScoreFactor]]] = []
        for worker in static_pool:
            ok, _reason = _hard_ok(ctx, state, worker, slot, day)
            if not ok:
                continue
            score, factors = _score_candidate(ctx, state, instance, worker, day)
            candidates.append((score, worker, factors))

        if not candidates:
            unfilled_reason[instance.instance_id] = ("no_capacity", REASON_MESSAGES["no_capacity"])
            return False

        if ctx.limits.max_candidates_per_slot > 0:
            candidates = self._cap_candidates(candidates, ctx.limits.max_candidates_per_slot)

        # tie-breaker: スコア降順 → 今月割当昇順 → 連勤昇順 → employee_number 昇順
        def sort_key(item: tuple[int, Worker, list[ScoreFactor]]):
            score, worker, _ = item
            number = worker.employee_number
            limit = worker.monthly_limit
            prior_tail = limit.prior_month_tail_consecutive if limit else 0
            run = _consecutive_run_if_placed(
                state.worked_dates.get(number, set()), day.date, ctx.first_day, prior_tail, True
            )
            return (-score, state.month_count.get(number, 0), run, number)

        candidates.sort(key=sort_key)
        best_score, best_worker, best_factors = candidates[0]

        # スコア下限
        min_score = ctx.prefs.min_assignment_score
        if min_score is not None and best_score < min_score:
            unfilled_reason[instance.instance_id] = ("below_min_score", REASON_MESSAGES["below_min_score"])
            return False

        state.add(
            instance.instance_id,
            best_worker.employee_number,
            day.date,
            _opt(slot.shift_key),
            day.is_weekend or day.is_holiday,
        )
        explanations_map[instance.instance_id] = best_factors
        return True

    @staticmethod
    def _cap_candidates(
        candidates: list[tuple[int, Worker, list[ScoreFactor]]], cap: int
    ) -> list[tuple[int, Worker, list[ScoreFactor]]]:
        ranked = sorted(candidates, key=lambda item: (-item[0], item[1].employee_number))
        return ranked[:cap]

    def _diagnose_unfilled(self, ctx: _Context, slot: RequiredSlot, day: PlanningDay) -> tuple[str, str]:
        """静的に候補ゼロの理由を funnel で診断する。"""
        pool = [w for w in ctx.request.workers if w.active and w.employee_number]
        if not pool:
            return ("no_candidates", REASON_MESSAGES["no_candidates"])
        after_hard = [
            w for w in pool
            if (w.employee_number, day.date) not in ctx.hard_unavailable
            and (w.employee_number, day.date, str(slot.shift_key or "").strip()) not in ctx.forbidden
            and not self._static_external_block(ctx, w, slot, day)
        ]
        if not after_hard:
            return ("all_excluded_hard", REASON_MESSAGES["all_excluded_hard"])
        after_baseline = [w for w in after_hard if _baseline_ok(w, slot, ctx.prefs)]
        if not after_baseline:
            return ("no_eligible", REASON_MESSAGES["no_eligible"])
        after_option = [w for w in after_baseline if _option_experience_ok(w, slot, ctx.option_policies)]
        if not after_option:
            return ("option_experience_required", REASON_MESSAGES["option_experience_required"])
        return ("no_capacity", REASON_MESSAGES["no_capacity"])

    @staticmethod
    def _static_external_block(ctx: _Context, worker: Worker, slot: RequiredSlot, day: PlanningDay) -> bool:
        if slot.site_row_id in ctx.relax_sites:
            return False
        option = _opt(slot.shift_key)
        for ext_opt in ctx.external_by_emp_date.get((worker.employee_number, day.date), ()):  # type: ignore[arg-type]
            if is_duplicate_by_rules(option, ext_opt, same_site=False):
                return True
        return False

    # -- local repair -------------------------------------------------------

    def _local_repair(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
        start: float,
    ) -> None:
        """決定的な走査で総ペナルティが厳密に減る変更のみ採用する局所修復。

        3 種類の近傍を順に試す:
        1. reassign: 1 枠の担当を別候補へ置き換える（空き枠の充足を含む）
        2. relocate: 未充足枠へ他枠の担当を移し、空いた枠を別候補で埋め直す
           （貪欲が「後の枠でしか使えない人」を先に消費した場合の救済）
        3. swap: 2 枠の担当を入れ替える（公平性・連勤・土日祝の偏りの解消）
        """
        for _iteration in range(self.limits.max_local_search_iterations):
            if self._timed_out(start):
                return
            improved = self._reassign_pass(ctx, state, instances, explanations_map, start)
            improved = self._relocate_pass(ctx, state, instances, explanations_map, start) or improved
            improved = self._swap_pass(ctx, state, instances, explanations_map, start) or improved
            if not improved:
                return

    def _worker_soft_cost(self, ctx: _Context, state: _PlanState, number: str) -> float:
        """1 人分の Soft コスト（連勤・偏り・最低不足・土日祝）。

        局所修復の差分評価用。配置変更で影響を受けるのは当事者の worker だけ
        なので、全体コストを再計算せずこの値の差分で判定する。
        """
        weights = ctx.weights
        cost = 0.0
        dates = state.worked_dates.get(number)
        if dates:
            threshold = (
                ctx.prefs.max_consecutive_days
                if ctx.prefs.max_consecutive_days
                else CONSECUTIVE_SOFT_DEFAULT
            )
            worker = ctx.worker_by_number.get(number)
            prior_tail = 0
            if worker and worker.monthly_limit:
                prior_tail = worker.monthly_limit.prior_month_tail_consecutive
            cost += weights.consecutive_day_penalty * _consecutive_excess(
                dates, ctx.first_day, threshold, prior_tail
            )
        count = state.month_count.get(number, 0)
        min_target = ctx.min_target_by_number.get(number, 0)
        if ctx.prefs.prefer_fairness:
            over = count - min_target
            if over > 0:
                cost += weights.monthly_imbalance_penalty * over * over
        if min_target:
            shortfall = max(0, min_target - count)
            cost += weights.monthly_imbalance_penalty * UNDER_MIN_MULTIPLIER * shortfall
        if ctx.prefs.suppress_weekend_imbalance:
            weekend = state.weekend_count.get(number, 0)
            if weekend:
                cost += weights.weekend_imbalance_penalty * weekend * weekend
        return cost

    @staticmethod
    def _change_penalty(ctx: _Context, instance: _Instance, number: str | None) -> int:
        """instance を number（None=空欄）にしたときの変更最小化ペナルティ。"""
        if not ctx.prefs.minimize_changes or not instance.original_worker:
            return 0
        return 0 if number == instance.original_worker else ctx.weights.change_penalty

    def _reassign_pass(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
        start: float,
    ) -> bool:
        scan = sorted(instances, key=lambda i: (i.slot.date, i.instance_id))
        improved = False
        min_score = ctx.prefs.min_assignment_score

        for instance in scan:
            if instance.fixed_worker is not None:
                continue
            # 1 パスの途中でも時間予算を超えたら打ち切る（大規模入力の安全弁）。
            if self._timed_out(start):
                return improved
            slot = instance.slot
            day = ctx.day_by_date.get(slot.date)
            if day is None:
                continue

            current_worker = state.placed.get(instance.instance_id)
            option = _opt(slot.shift_key)
            is_we = day.is_weekend or day.is_holiday

            # この枠を空けた状態を基準に、候補ごとの差分コストで評価する
            # （影響を受けるのは候補本人の Soft コストと未充足数・変更数のみ）。
            if current_worker is not None:
                state.remove(instance.instance_id, day.date, option, is_we)
            empty_change = self._change_penalty(ctx, instance, None)

            def delta_for(number: str) -> float:
                before = self._worker_soft_cost(ctx, state, number)
                state.add(instance.instance_id, number, day.date, option, is_we)
                after = self._worker_soft_cost(ctx, state, number)
                state.remove(instance.instance_id, day.date, option, is_we)
                return (
                    (after - before)
                    - UNFILLED_COST
                    + self._change_penalty(ctx, instance, number)
                    - empty_change
                )

            reference_delta = 0.0
            if current_worker is not None:
                reference_delta = delta_for(current_worker)

            # 同コスト時の tie-break を一意にするため employee_number 昇順で走査する。
            static_pool = sorted(
                (w for w in ctx.request.workers if _static_eligible(ctx, w, slot, day)),
                key=lambda w: w.employee_number,
            )
            best_choice: tuple[float, str, list[ScoreFactor]] | None = None

            for worker in static_pool:
                number = worker.employee_number
                if number == current_worker:
                    continue
                ok, _r = _hard_ok(ctx, state, worker, slot, day)
                if not ok:
                    continue
                score, factors = _score_candidate(ctx, state, instance, worker, day)
                if min_score is not None and score < min_score:
                    continue
                delta = delta_for(number)
                if delta < reference_delta - 1e-9:
                    if best_choice is None or delta < best_choice[0]:
                        best_choice = (delta, number, factors)

            if best_choice is not None:
                _delta, number, factors = best_choice
                state.add(instance.instance_id, number, day.date, option, is_we)
                explanations_map[instance.instance_id] = factors
                improved = True
            elif current_worker is not None:
                state.add(instance.instance_id, current_worker, day.date, option, is_we)

        return improved

    def _relocate_pass(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
        start: float,
    ) -> bool:
        """未充足枠の救済: 他枠の担当者を未充足枠へ移し、空いた枠を埋め直す。

        貪欲法は「その枠でしか使えない人」を先の枠で消費してしまうことがある
        （例: スコア下限や NG 曜日で他の候補が使えない枠）。担当の付け替え＋
        埋め直しの 2 手で総コストが厳密に減る場合のみ採用する。
        """
        scan = sorted(instances, key=lambda i: (i.slot.date, i.instance_id))
        improved = False

        for unfilled in scan:
            if self._timed_out(start):
                return improved
            if unfilled.fixed_worker is not None or state.placed.get(unfilled.instance_id):
                continue
            slot_u = unfilled.slot
            day_u = ctx.day_by_date.get(slot_u.date)
            if day_u is None or not ctx.is_fill_target(slot_u.date):
                continue
            opt_u = _opt(slot_u.shift_key)
            we_u = day_u.is_weekend or day_u.is_holiday
            min_score = ctx.prefs.min_assignment_score
            applied = False

            for donor in scan:
                if applied:
                    break
                if donor.fixed_worker is not None or donor.instance_id == unfilled.instance_id:
                    continue
                donor_number = state.placed.get(donor.instance_id)
                if not donor_number:
                    continue
                worker = ctx.worker_by_number.get(donor_number)
                if worker is None or not _static_eligible(ctx, worker, slot_u, day_u):
                    continue
                slot_d = donor.slot
                day_d = ctx.day_by_date.get(slot_d.date)
                if day_d is None:
                    continue
                opt_d = _opt(slot_d.shift_key)
                we_d = day_d.is_weekend or day_d.is_holiday

                # 差分コスト評価: 影響を受けるのは donor 本人・埋め直し候補・未充足数・変更数のみ
                donor_cost_before = self._worker_soft_cost(ctx, state, donor_number)

                # donor を外し、未充足枠へ移せるか検証
                state.remove(donor.instance_id, slot_d.date, opt_d, we_d)
                ok, _r = _hard_ok(ctx, state, worker, slot_u, day_u)
                score_u, factors_u = (0, [])
                if ok:
                    score_u, factors_u = _score_candidate(ctx, state, unfilled, worker, day_u)
                    if min_score is not None and score_u < min_score:
                        ok = False
                if not ok:
                    state.add(donor.instance_id, donor_number, slot_d.date, opt_d, we_d)
                    continue
                state.add(unfilled.instance_id, donor_number, slot_u.date, opt_u, we_u)

                # 空いた donor 枠を最良候補で埋め直す（埋まらなくても可。コストで判定）
                refill: tuple[int, Worker, list[ScoreFactor]] | None = None
                for cand in sorted(
                    (w for w in ctx.request.workers if _static_eligible(ctx, w, slot_d, day_d)),
                    key=lambda w: w.employee_number,
                ):
                    ok2, _r2 = _hard_ok(ctx, state, cand, slot_d, day_d)
                    if not ok2:
                        continue
                    score2, factors2 = _score_candidate(ctx, state, donor, cand, day_d)
                    if min_score is not None and score2 < min_score:
                        continue
                    if refill is None or score2 > refill[0]:
                        refill = (score2, cand, factors2)
                refill_cost_before = (
                    self._worker_soft_cost(ctx, state, refill[1].employee_number)
                    if refill is not None else 0.0
                )
                if refill is not None:
                    state.add(donor.instance_id, refill[1].employee_number, slot_d.date, opt_d, we_d)

                donor_cost_after = self._worker_soft_cost(ctx, state, donor_number)
                refill_cost_after = (
                    self._worker_soft_cost(ctx, state, refill[1].employee_number)
                    if refill is not None else 0.0
                )
                delta = (
                    (donor_cost_after - donor_cost_before)
                    + (refill_cost_after - refill_cost_before)
                    + UNFILLED_COST * ((0 if refill is not None else 1) - 1)
                    + self._change_penalty(ctx, unfilled, donor_number)
                    - self._change_penalty(ctx, unfilled, None)
                    + self._change_penalty(
                        ctx, donor, refill[1].employee_number if refill is not None else None
                    )
                    - self._change_penalty(ctx, donor, donor_number)
                )
                if delta < -1e-9:
                    explanations_map[unfilled.instance_id] = factors_u
                    if refill is not None:
                        explanations_map[donor.instance_id] = refill[2]
                    improved = True
                    applied = True
                else:
                    # 巻き戻し
                    if refill is not None:
                        state.remove(donor.instance_id, slot_d.date, opt_d, we_d)
                    state.remove(unfilled.instance_id, slot_u.date, opt_u, we_u)
                    state.add(donor.instance_id, donor_number, slot_d.date, opt_d, we_d)

        return improved

    def _swap_pass(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
        start: float,
    ) -> bool:
        """2 枠の担当入替（2-opt）。reassign 単体では直せない偏りを解消する。"""
        placed_instances = [
            i for i in sorted(instances, key=lambda i: (i.slot.date, i.instance_id))
            if i.fixed_worker is None and state.placed.get(i.instance_id)
        ]
        improved = False
        min_score = ctx.prefs.min_assignment_score

        for a_index, inst_a in enumerate(placed_instances):
            if self._timed_out(start):
                return improved
            for inst_b in placed_instances[a_index + 1:]:
                number_a = state.placed.get(inst_a.instance_id)
                number_b = state.placed.get(inst_b.instance_id)
                if not number_a or not number_b or number_a == number_b:
                    continue
                worker_a = ctx.worker_by_number.get(number_a)
                worker_b = ctx.worker_by_number.get(number_b)
                if worker_a is None or worker_b is None:
                    continue
                slot_a, slot_b = inst_a.slot, inst_b.slot
                day_a = ctx.day_by_date.get(slot_a.date)
                day_b = ctx.day_by_date.get(slot_b.date)
                if day_a is None or day_b is None:
                    continue
                if not _static_eligible(ctx, worker_b, slot_a, day_a):
                    continue
                if not _static_eligible(ctx, worker_a, slot_b, day_b):
                    continue
                opt_a, opt_b = _opt(slot_a.shift_key), _opt(slot_b.shift_key)
                we_a = day_a.is_weekend or day_a.is_holiday
                we_b = day_b.is_weekend or day_b.is_holiday

                # 差分コスト評価: 影響は当事者 2 名の Soft コストと変更数のみ
                cost_before = (
                    self._worker_soft_cost(ctx, state, number_a)
                    + self._worker_soft_cost(ctx, state, number_b)
                )
                state.remove(inst_a.instance_id, slot_a.date, opt_a, we_a)
                state.remove(inst_b.instance_id, slot_b.date, opt_b, we_b)
                accepted = False
                ok_b, _r = _hard_ok(ctx, state, worker_b, slot_a, day_a)
                if ok_b:
                    score_a, factors_a = _score_candidate(ctx, state, inst_a, worker_b, day_a)
                    state.add(inst_a.instance_id, number_b, slot_a.date, opt_a, we_a)
                    ok_a, _r2 = _hard_ok(ctx, state, worker_a, slot_b, day_b)
                    if ok_a:
                        score_b, factors_b = _score_candidate(ctx, state, inst_b, worker_a, day_b)
                        if (min_score is None or (score_a >= min_score and score_b >= min_score)):
                            state.add(inst_b.instance_id, number_a, slot_b.date, opt_b, we_b)
                            cost_after = (
                                self._worker_soft_cost(ctx, state, number_a)
                                + self._worker_soft_cost(ctx, state, number_b)
                            )
                            delta = (
                                cost_after - cost_before
                                + self._change_penalty(ctx, inst_a, number_b)
                                - self._change_penalty(ctx, inst_a, number_a)
                                + self._change_penalty(ctx, inst_b, number_a)
                                - self._change_penalty(ctx, inst_b, number_b)
                            )
                            if delta < -1e-9:
                                explanations_map[inst_a.instance_id] = factors_a
                                explanations_map[inst_b.instance_id] = factors_b
                                improved = True
                                accepted = True
                            else:
                                state.remove(inst_b.instance_id, slot_b.date, opt_b, we_b)
                    if not accepted:
                        state.remove(inst_a.instance_id, slot_a.date, opt_a, we_a)
                if not accepted:
                    state.add(inst_a.instance_id, number_a, slot_a.date, opt_a, we_a)
                    state.add(inst_b.instance_id, number_b, slot_b.date, opt_b, we_b)

        return improved

    def _global_cost(self, ctx: _Context, state: _PlanState, instances: list[_Instance]) -> float:
        """総コストの参照実装（充足を支配項、その他は Soft ペナルティの近似）。

        局所修復は性能のため差分評価（`_worker_soft_cost` と `_change_penalty`）で
        判定するが、その差分が常にこの全体コストの差と一致するよう、定義を
        ここに一本化している（テストの整合検証用にも使う）。
        """
        cost = UNFILLED_COST * (len(instances) - len(state.placed))
        numbers = (
            set(state.worked_dates)
            | set(state.month_count)
            | set(state.weekend_count)
            | set(ctx.min_target_by_number)
        )
        for number in sorted(numbers):
            cost += self._worker_soft_cost(ctx, state, number)
        for instance in instances:
            cost += self._change_penalty(ctx, instance, state.placed.get(instance.instance_id))
        return cost

    # -- candidate panels (説明・候補比較) -----------------------------------

    def _build_candidate_panels(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
    ) -> list[SlotCandidatePanel]:
        """最終結果に対する候補比較パネルを枠単位で構築する。

        各枠について「当該枠の配置だけ外した状態」で全候補を再評価し、
        選ばれた人・他の候補のスコア・除外された人の理由内訳を返す。
        explanations_map も最終状態のスコア要因で更新する（配置時点の値より正確）。
        """
        panels: list[SlotCandidatePanel] = []
        ordered = sorted(instances, key=lambda i: (i.slot.date, i.slot.shift_key, i.instance_id))
        for instance in ordered:
            if instance.fixed_worker is not None:
                continue  # 固定枠は強制配置のため候補比較の対象外
            slot = instance.slot
            day = ctx.day_by_date.get(slot.date)
            if day is None:
                continue
            option = _opt(slot.shift_key)
            is_we = day.is_weekend or day.is_holiday
            placed = state.placed.get(instance.instance_id)
            if placed is not None:
                state.remove(instance.instance_id, slot.date, option, is_we)

            scored: list[tuple[int, Worker, list[ScoreFactor]]] = []
            excluded: dict[str, int] = {}
            for worker in ctx.request.workers:
                reason = _static_exclusion_reason(ctx, worker, slot, day)
                if reason is None:
                    ok, dynamic_reason = _hard_ok(ctx, state, worker, slot, day)
                    if not ok:
                        reason = dynamic_reason
                if reason:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                score, factors = _score_candidate(ctx, state, instance, worker, day)
                scored.append((score, worker, factors))

            # スコア下限未満は除外内訳へ（選ばれた本人は表示のため残す）
            min_score = ctx.prefs.min_assignment_score
            if min_score is not None:
                below = [
                    item for item in scored
                    if item[0] < min_score and item[1].employee_number != placed
                ]
                if below:
                    excluded["below_min_score"] = excluded.get("below_min_score", 0) + len(below)
                    scored = [
                        item for item in scored
                        if item[0] >= min_score or item[1].employee_number == placed
                    ]

            scored.sort(key=lambda item: (
                -item[0], state.month_count.get(item[1].employee_number, 0), item[1].employee_number
            ))
            eligible_count = len(scored)

            top = scored[:PANEL_MAX_CANDIDATES]
            if placed is not None and all(w.employee_number != placed for _s, w, _f in top):
                for item in scored:
                    if item[1].employee_number == placed:
                        top = top + [item]
                        break

            candidates: list[CandidateScore] = []
            placed_factors: list[ScoreFactor] | None = None
            for score, worker, factors in top:
                selected = worker.employee_number == placed
                if selected:
                    placed_factors = factors
                top_factors = sorted(factors, key=lambda f: -abs(f.points))[:3]
                candidates.append(
                    CandidateScore(worker.employee_number, worker.name, score, selected, top_factors)
                )

            if placed is not None:
                state.add(instance.instance_id, placed, slot.date, option, is_we)
                if placed_factors is not None:
                    explanations_map[instance.instance_id] = placed_factors

            panels.append(
                SlotCandidatePanel(
                    slot_instance_id=instance.instance_id,
                    slot_id=slot.slot_id,
                    date=slot.date,
                    day=slot.day,
                    shift_key=slot.shift_key,
                    shift_label=slot.shift_label,
                    selected_employee_number=placed or "",
                    candidates=candidates,
                    excluded_counts=dict(sorted(excluded.items())),
                    eligible_count=eligible_count,
                )
            )
        return panels

    # -- output -------------------------------------------------------------

    def _build_assignments(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        explanations_map: dict[str, list[ScoreFactor]],
    ) -> list[Assignment]:
        assignments: list[Assignment] = []
        for instance in instances:
            number = state.placed.get(instance.instance_id)
            if not number:
                continue
            worker = ctx.worker_by_number.get(number)
            name = worker.name if worker else ""
            slot = instance.slot
            if instance.fixed_worker == number and instance.fixed_source:
                source = instance.fixed_source
                score = 0
                if instance.fixed_source == "existing_locked":
                    # 元 entry の表記（氏名）をそのまま保持する
                    original = ctx.existing_by_id.get(instance.fixed_assignment_id)
                    if original and original.employee_name:
                        name = original.employee_name
            else:
                source = "engine"
                factors = explanations_map.get(instance.instance_id, [])
                score = sum(f.points for f in factors)
            assignments.append(
                Assignment(
                    assignment_id=f"engine-{ctx.request.request_id}-{instance.instance_id}",
                    slot_id=slot.slot_id,
                    slot_instance_id=instance.instance_id,
                    date=slot.date,
                    day=slot.day,
                    shift_key=slot.shift_key,
                    employee_number=number,
                    employee_name=name,
                    score=score,
                    source=source,  # type: ignore[arg-type]
                    site_row_id=slot.site_row_id,
                    site_id=slot.site_id,
                    site_name=slot.site_name,
                    site_branch_row_id=slot.site_branch_row_id,
                    site_branch=slot.site_branch,
                    shift_label=slot.shift_label,
                )
            )
        assignments.sort(key=lambda a: (a.date, a.shift_key, a.slot_instance_id, a.employee_number))
        return assignments

    def _build_unfilled(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        unfilled_reason: dict[str, tuple[str, str]],
        panel_by_instance: dict[str, SlotCandidatePanel] | None = None,
    ) -> list[UnfilledSlot]:
        # slot_id ごとに不足数を集計
        shortage_by_slot: dict[str, int] = {}
        reason_by_slot: dict[str, tuple[str, str]] = {}
        breakdown_by_slot: dict[str, str] = {}
        slot_meta: dict[str, RequiredSlot] = {}
        for instance in instances:
            slot_meta[instance.slot.slot_id] = instance.slot
            if state.placed.get(instance.instance_id):
                continue
            slot_id = instance.slot.slot_id
            shortage_by_slot[slot_id] = shortage_by_slot.get(slot_id, 0) + 1
            reason = unfilled_reason.get(instance.instance_id)
            if reason and slot_id not in reason_by_slot:
                reason_by_slot[slot_id] = reason
            # 候補パネルがあれば除外内訳（休暇◯名・他現場◯名…）を理由に添える
            if panel_by_instance and slot_id not in breakdown_by_slot:
                panel = panel_by_instance.get(instance.instance_id)
                if panel and panel.excluded_counts:
                    parts = [
                        f"{EXCLUSION_LABELS.get(code, code)}{count}名"
                        for code, count in panel.excluded_counts.items()
                    ]
                    breakdown_by_slot[slot_id] = "・".join(parts)

        unfilled: list[UnfilledSlot] = []
        for slot_id, shortage in shortage_by_slot.items():
            slot = slot_meta[slot_id]
            reason_code, reason = reason_by_slot.get(
                slot_id, ("partial_allowed", REASON_MESSAGES["partial_allowed"])
            )
            breakdown = breakdown_by_slot.get(slot_id)
            if breakdown:
                reason = f"{reason}（内訳: {breakdown}）"
            unfilled.append(
                UnfilledSlot(
                    slot_id=slot_id,
                    date=slot.date,
                    day=slot.day,
                    shift_key=slot.shift_key,
                    shortage=shortage,
                    reason_code=reason_code,
                    reason=reason,
                )
            )
        unfilled.sort(key=lambda u: (u.date, u.shift_key, u.slot_id))
        return unfilled

    def _build_explanations(
        self,
        assignments: list[Assignment],
        explanations_map: dict[str, list[ScoreFactor]],
    ) -> list[Explanation]:
        explanations: list[Explanation] = []
        for assignment in assignments:
            factors = explanations_map.get(assignment.slot_instance_id)
            if assignment.source != "engine":
                summary = "固定配置として保持" if assignment.source == "existing_locked" else "pinned 指定で固定"
                explanations.append(
                    Explanation(
                        assignment_id=assignment.assignment_id,
                        slot_id=assignment.slot_id,
                        employee_number=assignment.employee_number,
                        summary=summary,
                        factors=[],
                    )
                )
                continue
            top = sorted(factors or [], key=lambda f: -abs(f.points))[:3]
            positives = [f.label for f in top if f.points > 0]
            summary = "・".join(positives) if positives else "適格候補から選定"
            explanations.append(
                Explanation(
                    assignment_id=assignment.assignment_id,
                    slot_id=assignment.slot_id,
                    employee_number=assignment.employee_number,
                    summary=summary,
                    factors=top,
                )
            )
        return explanations

    def _score_summary(
        self,
        ctx: _Context,
        state: _PlanState,
        instances: list[_Instance],
        assignments: list[Assignment],
        unfilled: list[UnfilledSlot],
        violations: list[Violation],
        warnings: list[PlanningWarning],
    ) -> ScoreSummary:
        required_count = len(instances)
        assigned_count = len(assignments)
        unfilled_count = sum(u.shortage for u in unfilled)
        fill_rate = 1.0 if required_count == 0 else assigned_count / required_count

        # 公平性: 割当のある worker の総割当数の母標準偏差
        counts = [c for c in state.month_count.values() if c > 0]
        fairness_index = _balance_index(counts)
        weekend_counts = [c for c in state.weekend_count.values() if c > 0]
        weekend_balance_index = _balance_index(weekend_counts)

        # 最大連勤
        max_consecutive = 0
        for number, dates in state.worked_dates.items():
            worker = ctx.worker_by_number.get(number)
            prior_tail = 0
            if worker and worker.monthly_limit:
                prior_tail = worker.monthly_limit.prior_month_tail_consecutive
            max_consecutive = max(max_consecutive, _max_consecutive(dates, ctx.first_day, prior_tail))

        # 既存からの変更数
        changed_existing = 0
        for instance in instances:
            if instance.original_worker:
                if state.placed.get(instance.instance_id) != instance.original_worker:
                    changed_existing += 1

        total_score = sum(a.score for a in assignments)
        return ScoreSummary(
            total_score=total_score,
            fill_rate=round(fill_rate, 6),
            assigned_count=assigned_count,
            required_count=required_count,
            unfilled_count=unfilled_count,
            blocker_count=sum(1 for v in violations if v.severity == "blocker"),
            hard_violation_count=sum(1 for v in violations if v.severity == "hard"),
            warning_count=sum(1 for v in violations if v.severity == "warning") + len(warnings),
            fairness_index=fairness_index,
            weekend_balance_index=weekend_balance_index,
            max_consecutive_days=max_consecutive,
            changed_existing_count=changed_existing,
        )

    def _failed_result(
        self,
        request: ShiftPlanningRequest,
        violations: list[Violation],
        warnings: list[PlanningWarning],
    ) -> ShiftPlanningResult:
        score = ScoreSummary(
            total_score=0,
            fill_rate=0.0,
            assigned_count=0,
            required_count=sum(s.required_count for s in request.required_slots),
            unfilled_count=sum(s.required_count for s in request.required_slots),
            blocker_count=sum(1 for v in violations if v.severity == "blocker"),
            hard_violation_count=sum(1 for v in violations if v.severity == "hard"),
            warning_count=sum(1 for v in violations if v.severity == "warning") + len(warnings),
            fairness_index=1.0,
            weekend_balance_index=1.0,
            max_consecutive_days=0,
            changed_existing_count=0,
        )
        return ShiftPlanningResult(
            request_id=request.request_id,
            status="failed",
            solver_backend=self.backend,
            solver_certified=False,
            base_revision=request.base_revision,
            assignments=[],
            unfilled_slots=[],
            violations=violations,
            warnings=warnings,
            score=score,
            explanations=[],
            request_hash=compute_request_hash(request),
        )

    def _timed_out(self, start: float) -> bool:
        return (time.monotonic() - start) > self.limits.max_solver_seconds


def _consecutive_excess(dates: set[date], first_day: date, threshold: int, prior_tail: int) -> int:
    """連勤の超過分（Σ max(0, runlen - threshold)）を返す。"""
    if not dates:
        return 0
    excess = 0
    for run_len in _run_lengths(dates, first_day, prior_tail):
        if run_len > threshold:
            excess += run_len - threshold
    return excess


def _max_consecutive(dates: set[date], first_day: date, prior_tail: int) -> int:
    runs = _run_lengths(dates, first_day, prior_tail)
    return max(runs) if runs else 0


def _run_lengths(dates: set[date], first_day: date, prior_tail: int) -> list[int]:
    """連勤の各 run の長さ一覧（月内のみ。day1 を含む run には prior_tail を加える）。"""
    if not dates:
        return []
    ordered = sorted(dates)
    runs: list[int] = []
    run = 1
    start_date = ordered[0]
    for prev, cur in zip(ordered, ordered[1:]):
        if (cur - prev).days == 1:
            run += 1
        else:
            runs.append(run + (prior_tail if start_date == first_day else 0))
            run = 1
            start_date = cur
    runs.append(run + (prior_tail if start_date == first_day else 0))
    return runs


def _balance_index(counts: list[int]) -> float:
    """0.0〜1.0 の偏り指標（1.0 が最良）。母標準偏差/平均で正規化。"""
    if len(counts) <= 1:
        return 1.0
    mean = sum(counts) / len(counts)
    if mean == 0:
        return 1.0
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    pstdev = variance ** 0.5
    return round(1 - min(1.0, pstdev / max(1.0, mean)), 6)


# ---------------------------------------------------------------------------
# result 検証
# ---------------------------------------------------------------------------


class _ResultView:
    """validate_result が plan 後と apply-draft 前で共通に使う軽量ビュー。"""

    def __init__(self, assignments: list[Assignment], unfilled_slots: list[UnfilledSlot]) -> None:
        self.assignments = assignments
        self.unfilled_slots = unfilled_slots


def validate_result(request: ShiftPlanningRequest, result: Any) -> list[Violation]:
    """生成後の不変条件検証（plan 後・apply-draft 前の両方で実行）。"""
    violations: list[Violation] = []
    assignments: list[Assignment] = list(getattr(result, "assignments", []))
    unfilled_slots: list[UnfilledSlot] = list(getattr(result, "unfilled_slots", []))

    worker_numbers = {str(w.employee_number or "").strip() for w in request.workers}
    slot_ids = {s.slot_id for s in request.required_slots}
    # 合成 slot（existing_entry）も許容する
    synthetic_slot_ids = {a.slot_id for a in assignments if a.slot_id.startswith("existing-")}
    valid_slot_ids = slot_ids | synthetic_slot_ids

    day_by_date = {d.date: d for d in request.days}
    hard_unavailable = {
        (str(u.employee_number or "").strip(), u.date)
        for u in request.unavailable_days
        if u.strength == "hard"
    }
    external_by_emp_date: dict[tuple[str, date], list[str | None]] = {}
    for ext in request.external_assignments:
        number = str(ext.employee_number or "").strip()
        if number:
            external_by_emp_date.setdefault((number, ext.date), []).append(_opt(ext.shift_key))
    relax_sites = set(request.external_occupancy_relax_sites)

    # forbidden 集合
    forbidden: set[tuple[str, date, str]] = set()
    for rule in request.rules:
        if rule.kind == "forbidden" and rule.enabled:
            params = rule.params or {}
            number = str(params.get("employee_number") or "").strip()
            shift_key = str(params.get("shift_key") or "").strip()
            d = _parse_iso_date(params.get("date"))
            if not d and params.get("day") is not None:
                try:
                    d = date(request.year, request.month, int(params.get("day")))
                except (TypeError, ValueError):
                    d = None
            if number and d:
                forbidden.add((number, d, shift_key))

    # 月次上限
    max_by_number: dict[str, int] = {}
    for worker in request.workers:
        if worker.monthly_limit and worker.monthly_limit.max_assignments is not None:
            max_by_number[worker.employee_number] = worker.monthly_limit.max_assignments

    # オプション未経験不可
    option_policies = [
        p for p in request.option_experience_policies if p.enabled and p.require_prior_experience
    ]
    worker_by_number = {w.employee_number: w for w in request.workers}

    seen_instances: set[str] = set()
    same_day_options: dict[tuple[str, date], list[str | None]] = {}
    month_count: dict[str, int] = {}

    fill_targets = set(request.fill_target_dates)
    fill_all = not request.fill_target_dates

    for assignment in assignments:
        number = str(assignment.employee_number or "").strip()
        if not number:
            violations.append(
                Violation("empty_employee_number", "hard", "社員番号が空の配置があります", slot_id=assignment.slot_id)
            )
            continue
        if number not in worker_numbers:
            violations.append(
                Violation(
                    "unknown_worker",
                    "hard",
                    f"存在しない候補者を参照しています: {number}",
                    employee_number=number,
                    slot_id=assignment.slot_id,
                )
            )
        if assignment.slot_id not in valid_slot_ids:
            violations.append(
                Violation(
                    "unknown_slot",
                    "hard",
                    f"存在しない slot を参照しています: {assignment.slot_id}",
                    slot_id=assignment.slot_id,
                )
            )
        # 重複割当（同一 slot_instance に 2 配置）
        if assignment.slot_instance_id in seen_instances:
            violations.append(
                Violation(
                    "duplicate_instance",
                    "hard",
                    f"同一割当単位が重複しています: {assignment.slot_instance_id}",
                    slot_id=assignment.slot_id,
                )
            )
        seen_instances.add(assignment.slot_instance_id)

        # 対象外日の固定既存はエンジンが動かせないため、衝突は warning に下げる。
        in_scope = fill_all or assignment.date in fill_targets
        hard_severity = "hard" if (in_scope or assignment.source == "engine") else "warning"
        # 有休系オプションの entry は休みの記録であり、本人の休暇との同日は整合
        is_leave_assignment = str(assignment.shift_key or "").strip() in LEAVE_OPTION_KEYS

        # hard 休暇
        if not is_leave_assignment and (number, assignment.date) in hard_unavailable:
            violations.append(
                Violation(
                    "hard_leave_assigned",
                    hard_severity,
                    f"hard 休暇に配置しています: {number} {assignment.date.isoformat()}",
                    employee_number=number,
                    date=assignment.date,
                    slot_id=assignment.slot_id,
                )
            )
        # forbidden
        if (number, assignment.date, str(assignment.shift_key or "").strip()) in forbidden:
            violations.append(
                Violation(
                    "forbidden_assigned",
                    "hard",
                    f"forbidden の枠に配置しています: {number}",
                    employee_number=number,
                    date=assignment.date,
                    slot_id=assignment.slot_id,
                )
            )

        option = _opt(assignment.shift_key)
        # 同一現場の同日重複
        for existing_opt in same_day_options.get((number, assignment.date), ()):
            if is_duplicate_by_rules(option, existing_opt, same_site=True):
                violations.append(
                    Violation(
                        "same_site_duplicate",
                        hard_severity,
                        f"同一現場で同日重複しています: {number} {assignment.date.isoformat()}",
                        employee_number=number,
                        date=assignment.date,
                        slot_id=assignment.slot_id,
                    )
                )
                break
        # 他現場占有
        if assignment.site_row_id not in relax_sites:
            for ext_opt in external_by_emp_date.get((number, assignment.date), ()):
                if is_duplicate_by_rules(option, ext_opt, same_site=False):
                    violations.append(
                        Violation(
                            "external_duplicate",
                            hard_severity,
                            f"他現場占有と同日重複しています: {number} {assignment.date.isoformat()}",
                            employee_number=number,
                            date=assignment.date,
                            slot_id=assignment.slot_id,
                        )
                    )
                    break
        same_day_options.setdefault((number, assignment.date), []).append(option)
        if not is_leave_assignment:
            month_count[number] = month_count.get(number, 0) + 1

        # 最低基準・未経験不可（保存前再判定）
        # 最低基準・未経験不可はエンジンが選定した配置にのみ再判定する。
        # 固定既存(existing_locked)・pinned は強制配置であり 100% 維持するため対象外。
        slot = _slot_for_assignment(request, assignment)
        if slot is not None and assignment.source == "engine":
            worker = worker_by_number.get(number)
            if worker is not None:
                if not _baseline_ok(worker, slot, request.preferences) and request.preferences.eligibility_baseline != "any":
                    violations.append(
                        Violation(
                            "baseline_violation",
                            "hard",
                            f"最低基準を満たさない配置です: {number}",
                            employee_number=number,
                            date=assignment.date,
                            slot_id=assignment.slot_id,
                        )
                    )
                if not _option_experience_ok(worker, slot, option_policies):
                    violations.append(
                        Violation(
                            "option_experience_violation",
                            "hard",
                            f"未経験不可オプションに未経験者を配置しています: {number}",
                            employee_number=number,
                            date=assignment.date,
                            slot_id=assignment.slot_id,
                        )
                    )

    # 月次上限超過
    for number, count in month_count.items():
        limit = max_by_number.get(number)
        if limit is not None and count > limit and request.preferences.monthly_limit_hard:
            violations.append(
                Violation(
                    "monthly_limit_exceeded",
                    "hard",
                    f"月次上限を超過しています: {number} ({count}/{limit})",
                    employee_number=number,
                )
            )

    # 固定既存・pinned が 100% 維持されているか
    placed_pairs = {
        (str(a.employee_number or "").strip(), a.date, str(a.shift_key or "").strip())
        for a in assignments
    }
    for existing in request.existing_assignments:
        if existing.lock_policy in ("locked", "manual_locked"):
            key = (
                str(existing.employee_number or "").strip(),
                existing.date,
                str(existing.shift_key or "").strip(),
            )
            if key not in placed_pairs:
                violations.append(
                    Violation(
                        "locked_changed",
                        "hard",
                        f"固定既存配置が維持されていません: {existing.employee_number} {existing.date.isoformat()}",
                        employee_number=existing.employee_number,
                        date=existing.date,
                    )
                )
    for rule in request.rules:
        if rule.kind == "pinned" and rule.enabled:
            params = rule.params or {}
            number = str(params.get("employee_number") or "").strip()
            shift_key = str(params.get("shift_key") or "").strip()
            d = _parse_iso_date(params.get("date"))
            if not d and params.get("day") is not None:
                try:
                    d = date(request.year, request.month, int(params.get("day")))
                except (TypeError, ValueError):
                    d = None
            if number and d and (number, d, shift_key) not in placed_pairs:
                violations.append(
                    Violation(
                        "pinned_missing",
                        "hard",
                        f"pinned 指定が配置に含まれていません: {number} {d.isoformat()}",
                        employee_number=number,
                        date=d,
                    )
                )

    return violations


def _slot_for_assignment(request: ShiftPlanningRequest, assignment: Assignment) -> RequiredSlot | None:
    for slot in request.required_slots:
        if slot.slot_id == assignment.slot_id:
            return slot
    return None


# ---------------------------------------------------------------------------
# request_hash
# ---------------------------------------------------------------------------


def _parse_iso_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _jsonify(value: Any) -> Any:
    """request_hash 用に決定的な JSON 可能オブジェクトへ変換する。"""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _jsonify(getattr(value, name))
            for name in sorted(value.__dataclass_fields__.keys())
        }
    return value


def compute_request_hash(request: ShiftPlanningRequest) -> str:
    """出力に影響する全入力から決定的な hash を作る（UI 表示用データは含めない）。"""
    workers_payload = []
    for worker in sorted(request.workers, key=lambda w: w.employee_number):
        workers_payload.append(
            {
                "employee_number": worker.employee_number,
                "active": worker.active,
                "dedicated_site_row_ids": sorted(worker.dedicated_site_row_ids),
                "experienced_site_row_ids": sorted(worker.experienced_site_row_ids),
                "trained_site_row_ids": sorted(worker.trained_site_row_ids),
                "trainee_site_row_ids": sorted(worker.trainee_site_row_ids),
                "experienced_option_keys": sorted(worker.experienced_option_keys),
                "site_experience_count": worker.site_experience_count,
                "preference": _jsonify(worker.preference),
                "monthly_limit": _jsonify(worker.monthly_limit),
                "worker_weight": worker.worker_weight,
            }
        )

    payload = {
        "target_project_id": request.target_project_id,
        "year": request.year,
        "month": request.month,
        "base_revision": request.base_revision,
        "required_slots": [
            _jsonify(s) for s in sorted(request.required_slots, key=lambda s: s.slot_id)
        ],
        "workers": workers_payload,
        "existing_assignments": [
            _jsonify(e) for e in sorted(request.existing_assignments, key=lambda e: e.assignment_id)
        ],
        "external_assignments": [
            _jsonify(e)
            for e in sorted(
                request.external_assignments,
                key=lambda e: (e.employee_number, e.date, e.shift_key, e.project_id),
            )
        ],
        "unavailable_days": [
            _jsonify(u)
            for u in sorted(
                request.unavailable_days, key=lambda u: (u.employee_number, u.date, u.reason)
            )
        ],
        "rules": [_jsonify(r) for r in sorted(request.rules, key=lambda r: r.rule_id)],
        "preferences": _jsonify(request.preferences),
        "scoring_weights": _jsonify(request.scoring_weights),
        "option_experience_policies": [
            _jsonify(p)
            for p in sorted(
                request.option_experience_policies, key=lambda p: (p.option_key, p.site_row_id)
            )
        ],
        "external_occupancy_relax_sites": sorted(request.external_occupancy_relax_sites),
        "fill_target_dates": sorted(d.isoformat() for d in request.fill_target_dates),
        "settings_version": request.version,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 公開エントリポイント
# ---------------------------------------------------------------------------


def plan_shifts(
    request: ShiftPlanningRequest, limits: SolverLimits | None = None
) -> ShiftPlanningResult:
    """エンジンのメインエントリ。request だけを見て決定的に生成する。"""
    return GreedyRepairSolver(limits).solve(request)


# ---------------------------------------------------------------------------
# 設定の (デ)シリアライズと migration
# ---------------------------------------------------------------------------

_PREFERENCE_FIELDS = set(PlanningPreferences.__dataclass_fields__.keys())
_KNOWN_SETTINGS_KEYS = {
    "version", "demand_rules", "leave_policies", "default_preferences",
    "worker_limits", "scoring_weights", "option_experience_policies",
    "rules", "advanced_options",
}


def _as_int_tuple(value: Any) -> tuple[int, ...]:
    out: list[int] = []
    for item in value or ():
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (value or ()) if str(item).strip())


def _as_date_tuple(value: Any) -> tuple[date, ...]:
    out: list[date] = []
    for item in value or ():
        d = _parse_iso_date(item)
        if d:
            out.append(d)
    return tuple(out)


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or(value: Any, default: int) -> int:
    """数値化できない保存値（破損・手編集）でも例外を出さず default に落とす。"""
    parsed = _opt_int(value)
    return default if parsed is None else parsed


def preferences_from_dict(raw: Any) -> PlanningPreferences:
    """既定方針の上に dict の指定値を重ねた PlanningPreferences を返す。"""
    base = default_planning_preferences()
    if not isinstance(raw, dict):
        return base
    updates: dict[str, Any] = {}
    for key in _PREFERENCE_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if key in {"min_assignment_score", "max_consecutive_days",
                   "min_monthly_assignments", "max_monthly_assignments"}:
            updates[key] = _opt_int(value)
        elif key in {"allow_partial", "include_trainees", "prefer_dedicated",
                     "prefer_experienced", "prefer_fairness", "minimize_changes",
                     "suppress_weekend_imbalance", "monthly_limit_hard",
                     "max_consecutive_days_hard"}:
            updates[key] = bool(value)
        else:
            updates[key] = value
    return replace(base, **updates)


def preferences_to_dict(prefs: PlanningPreferences) -> dict[str, Any]:
    return {key: getattr(prefs, key) for key in _PREFERENCE_FIELDS}


def scoring_weights_from_dict(raw: Any) -> ScoringWeights:
    base = ScoringWeights()
    if not isinstance(raw, dict):
        return base
    updates: dict[str, Any] = {}
    for key in ScoringWeights.__dataclass_fields__:
        if key in raw:
            try:
                updates[key] = int(raw[key])
            except (TypeError, ValueError):
                continue
    return replace(base, **updates)


def _demand_rule_from_dict(raw: dict[str, Any]) -> DemandRule:
    return DemandRule(
        rule_id=str(raw.get("rule_id") or ""),
        enabled=bool(raw.get("enabled", True)),
        weekdays=_as_int_tuple(raw.get("weekdays")),
        include_holidays=bool(raw.get("include_holidays", False)),
        exclude_dates=_as_date_tuple(raw.get("exclude_dates")),
        include_dates=_as_date_tuple(raw.get("include_dates")),
        shift_key=str(raw.get("shift_key") or ""),
        shift_label=str(raw.get("shift_label") or ""),
        required_count=_int_or(raw.get("required_count", 1) or 0, 0),
        site_branch_row_id=str(raw.get("site_branch_row_id") or ""),
        site_branch=str(raw.get("site_branch") or ""),
        required_qualification_codes=_as_str_tuple(raw.get("required_qualification_codes")),
        required_vehicle_options=_as_str_tuple(raw.get("required_vehicle_options")),
        priority=_int_or(raw.get("priority", 100) or 100, 100),
    )


def _rule_from_dict(raw: dict[str, Any]) -> Rule:
    return Rule(
        rule_id=str(raw.get("rule_id") or ""),
        kind=str(raw.get("kind") or "manual"),  # type: ignore[arg-type]
        enabled=bool(raw.get("enabled", True)),
        priority=_int_or(raw.get("priority", 100) or 100, 100),
        effective_from=_parse_iso_date(raw.get("effective_from")),
        effective_to=_parse_iso_date(raw.get("effective_to")),
        params=dict(raw.get("params") or {}),
    )


def settings_from_dict(raw: Any) -> tuple[ShiftEngineSettings, list[PlanningWarning]]:
    """project["shift_engine"] の dict を ShiftEngineSettings へ復元する。"""
    warnings: list[PlanningWarning] = []
    if not isinstance(raw, dict) or not raw:
        return build_default_settings(), warnings

    unknown = sorted(set(raw.keys()) - _KNOWN_SETTINGS_KEYS)
    if unknown:
        warnings.append(
            PlanningWarning(
                "raw_settings",
                f"未知の設定キーがあります（無視されます）: {', '.join(unknown)}",
            )
        )

    demand_rules = [
        _demand_rule_from_dict(item) for item in (raw.get("demand_rules") or []) if isinstance(item, dict)
    ]
    leave_policies = [
        LeavePolicy(str(item.get("leave_type") or ""), str(item.get("strength") or "soft"))  # type: ignore[arg-type]
        for item in (raw.get("leave_policies") or []) if isinstance(item, dict)
    ] or default_leave_policies()
    worker_limits = [
        WorkerLimit(
            employee_number=str(item.get("employee_number") or ""),
            min_assignments=_opt_int(item.get("min_assignments")),
            max_assignments=_opt_int(item.get("max_assignments")),
        )
        for item in (raw.get("worker_limits") or []) if isinstance(item, dict)
    ]
    policies = [
        OptionExperiencePolicy(
            option_key=str(item.get("option_key") or ""),
            require_prior_experience=bool(item.get("require_prior_experience", False)),
            site_row_id=str(item.get("site_row_id") or ""),
            enabled=bool(item.get("enabled", True)),
        )
        for item in (raw.get("option_experience_policies") or []) if isinstance(item, dict)
    ]
    rules = [_rule_from_dict(item) for item in (raw.get("rules") or []) if isinstance(item, dict)]
    advanced = [
        OptionToggle(
            key=str(item.get("key") or ""),
            enabled=bool(item.get("enabled", False)),
            scope=str(item.get("scope") or "site"),  # type: ignore[arg-type]
            target=str(item.get("target") or ""),
            value=item.get("value"),
            note=str(item.get("note") or ""),
        )
        for item in (raw.get("advanced_options") or []) if isinstance(item, dict)
    ]

    settings = ShiftEngineSettings(
        version=int(raw.get("version", SETTINGS_VERSION) or SETTINGS_VERSION),
        demand_rules=demand_rules,
        leave_policies=leave_policies,
        default_preferences=preferences_from_dict(raw.get("default_preferences")),
        worker_limits=worker_limits,
        scoring_weights=scoring_weights_from_dict(raw.get("scoring_weights")),
        option_experience_policies=policies,
        rules=rules,
        advanced_options=advanced,
    )
    return settings, warnings


def settings_to_dict(settings: ShiftEngineSettings) -> dict[str, Any]:
    """ShiftEngineSettings を保存・表示用の dict へ変換する。"""
    return {
        "version": settings.version,
        "demand_rules": [
            {
                "rule_id": r.rule_id,
                "enabled": r.enabled,
                "weekdays": list(r.weekdays),
                "include_holidays": r.include_holidays,
                "exclude_dates": [d.isoformat() for d in r.exclude_dates],
                "include_dates": [d.isoformat() for d in r.include_dates],
                "shift_key": r.shift_key,
                "shift_label": r.shift_label,
                "required_count": r.required_count,
                "site_branch_row_id": r.site_branch_row_id,
                "site_branch": r.site_branch,
                "required_qualification_codes": list(r.required_qualification_codes),
                "required_vehicle_options": list(r.required_vehicle_options),
                "priority": r.priority,
            }
            for r in settings.demand_rules
        ],
        "leave_policies": [{"leave_type": p.leave_type, "strength": p.strength} for p in settings.leave_policies],
        "default_preferences": preferences_to_dict(
            settings.default_preferences or default_planning_preferences()
        ),
        "worker_limits": [
            {
                "employee_number": w.employee_number,
                "min_assignments": w.min_assignments,
                "max_assignments": w.max_assignments,
            }
            for w in settings.worker_limits
        ],
        "scoring_weights": {k: getattr(settings.scoring_weights, k) for k in ScoringWeights.__dataclass_fields__},
        "option_experience_policies": [
            {
                "option_key": p.option_key,
                "require_prior_experience": p.require_prior_experience,
                "site_row_id": p.site_row_id,
                "enabled": p.enabled,
            }
            for p in settings.option_experience_policies
        ],
        "rules": [
            {
                "rule_id": r.rule_id,
                "kind": r.kind,
                "enabled": r.enabled,
                "priority": r.priority,
                "effective_from": r.effective_from.isoformat() if r.effective_from else None,
                "effective_to": r.effective_to.isoformat() if r.effective_to else None,
                "params": r.params,
            }
            for r in settings.rules
        ],
        "advanced_options": [
            {
                "key": o.key,
                "enabled": o.enabled,
                "scope": o.scope,
                "target": o.target,
                "value": o.value,
                "note": o.note,
            }
            for o in settings.advanced_options
        ],
    }


def migrate_settings(raw: Any) -> tuple[ShiftEngineSettings, list[PlanningWarning]]:
    """旧設定を読み込み、必須値を既定で補完する（自動保存はしない）。"""
    return settings_from_dict(raw)


# ---------------------------------------------------------------------------
# 出力の dict 化（API レスポンス用。UI 表示データを含む）
# ---------------------------------------------------------------------------


def result_to_dict(result: ShiftPlanningResult) -> dict[str, Any]:
    """ShiftPlanningResult を JSON 応答用 dict へ変換する。"""
    return {
        "request_id": result.request_id,
        "status": result.status,
        "solver_backend": result.solver_backend,
        "solver_certified": result.solver_certified,
        "base_revision": result.base_revision,
        "request_hash": result.request_hash,
        "assignments": [
            {
                "assignment_id": a.assignment_id,
                "slot_id": a.slot_id,
                "slot_instance_id": a.slot_instance_id,
                "date": a.date.isoformat(),
                "day": a.day,
                "shift_key": a.shift_key,
                "shift_label": a.shift_label,
                "employee_number": a.employee_number,
                "employee_name": a.employee_name,
                "score": a.score,
                "source": a.source,
                "site_row_id": a.site_row_id,
                "site_branch": a.site_branch,
            }
            for a in result.assignments
        ],
        "unfilled_slots": [
            {
                "slot_id": u.slot_id,
                "date": u.date.isoformat(),
                "day": u.day,
                "shift_key": u.shift_key,
                "shortage": u.shortage,
                "reason_code": u.reason_code,
                "reason": u.reason,
            }
            for u in result.unfilled_slots
        ],
        "violations": [
            {
                "code": v.code,
                "severity": v.severity,
                "message": v.message,
                "date": v.date.isoformat() if v.date else None,
                "employee_number": v.employee_number,
                "slot_id": v.slot_id,
            }
            for v in result.violations
        ],
        "warnings": [
            {
                "code": w.code,
                "message": w.message,
                "date": w.date.isoformat() if w.date else None,
                "employee_number": w.employee_number,
                "slot_id": w.slot_id,
            }
            for w in result.warnings
        ],
        "score": {
            "total_score": result.score.total_score,
            "fill_rate": result.score.fill_rate,
            "assigned_count": result.score.assigned_count,
            "required_count": result.score.required_count,
            "unfilled_count": result.score.unfilled_count,
            "blocker_count": result.score.blocker_count,
            "hard_violation_count": result.score.hard_violation_count,
            "warning_count": result.score.warning_count,
            "fairness_index": result.score.fairness_index,
            "weekend_balance_index": result.score.weekend_balance_index,
            "max_consecutive_days": result.score.max_consecutive_days,
            "changed_existing_count": result.score.changed_existing_count,
        },
        "explanations": [
            {
                "assignment_id": ex.assignment_id,
                "slot_id": ex.slot_id,
                "employee_number": ex.employee_number,
                "summary": ex.summary,
                "factors": [
                    {"category": f.category, "label": f.label, "points": f.points, "detail": f.detail}
                    for f in ex.factors
                ],
            }
            for ex in result.explanations
        ],
        "candidate_panels": [
            {
                "slot_instance_id": p.slot_instance_id,
                "slot_id": p.slot_id,
                "date": p.date.isoformat(),
                "day": p.day,
                "shift_key": p.shift_key,
                "shift_label": p.shift_label,
                "selected_employee_number": p.selected_employee_number,
                "eligible_count": p.eligible_count,
                "candidates": [
                    {
                        "employee_number": c.employee_number,
                        "name": c.name,
                        "score": c.score,
                        "selected": c.selected,
                        "factors": [
                            {"label": f.label, "points": f.points, "detail": f.detail}
                            for f in c.factors
                        ],
                    }
                    for c in p.candidates
                ],
                "excluded": [
                    {"code": code, "label": EXCLUSION_LABELS.get(code, code), "count": count}
                    for code, count in p.excluded_counts.items()
                ],
            }
            for p in result.candidate_panels
        ],
    }


def build_day_summaries(
    request: ShiftPlanningRequest, result: ShiftPlanningResult
) -> list[dict[str, Any]]:
    """UI の日別診断用サマリ（需要・配置・未充足・対象内外）を日ごとに返す。

    「対象日なのに配置されない」原因（需要 0 / 候補全滅 / 固定で充足済み）を
    そのまま画面に出せる形にする。
    """
    targets = set(request.fill_target_dates)
    fill_all = not request.fill_target_dates

    demand_by_date: dict[date, int] = {}
    for slot in request.required_slots:
        demand_by_date[slot.date] = demand_by_date.get(slot.date, 0) + max(0, slot.required_count)

    engine_by_date: dict[date, int] = {}
    fixed_by_date: dict[date, int] = {}
    leave_by_date: dict[date, int] = {}
    for assignment in result.assignments:
        if str(assignment.shift_key or "").strip() in LEAVE_OPTION_KEYS:
            leave_by_date[assignment.date] = leave_by_date.get(assignment.date, 0) + 1
        elif assignment.source == "engine":
            engine_by_date[assignment.date] = engine_by_date.get(assignment.date, 0) + 1
        else:
            fixed_by_date[assignment.date] = fixed_by_date.get(assignment.date, 0) + 1

    unfilled_by_date: dict[date, int] = {}
    reasons_by_date: dict[date, list[str]] = {}
    for u in result.unfilled_slots:
        unfilled_by_date[u.date] = unfilled_by_date.get(u.date, 0) + u.shortage
        reasons = reasons_by_date.setdefault(u.date, [])
        if u.reason and u.reason not in reasons:
            reasons.append(u.reason)

    summaries: list[dict[str, Any]] = []
    for day in request.days:
        in_scope = fill_all or day.date in targets
        demand = demand_by_date.get(day.date, 0)
        summaries.append(
            {
                "day": day.day,
                "date": day.date.isoformat(),
                "weekday": day.weekday,
                "is_holiday": day.is_holiday,
                "is_weekend": day.is_weekend,
                "in_scope": in_scope,
                "demand": demand,
                "engine_count": engine_by_date.get(day.date, 0),
                "fixed_count": fixed_by_date.get(day.date, 0),
                "leave_count": leave_by_date.get(day.date, 0),
                "unfilled": unfilled_by_date.get(day.date, 0),
                "unfilled_reasons": reasons_by_date.get(day.date, []),
                # 対象日なのに需要が無い＝「配置されない」の最多原因
                "no_demand": bool(in_scope and demand == 0),
            }
        )
    return summaries
