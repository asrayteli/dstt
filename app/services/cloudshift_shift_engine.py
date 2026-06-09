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
    from ..tools.shiftersync_check import is_duplicate_by_rules
except ImportError:  # pragma: no cover - 単体実行時のフォールバック
    from app.tools.japan_holidays import JAPAN_HOLIDAYS  # type: ignore
    from app.tools.shiftersync_check import is_duplicate_by_rules  # type: ignore


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
        "settings", "required_capacity_fallback", "prev_month_estimate", "existing_entry"
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
    dedicated_site_ids: tuple[str, ...] = ()
    dedicated_site_row_ids: tuple[str, ...] = ()
    experienced_site_row_ids: tuple[str, ...] = ()
    trainee_site_row_ids: tuple[str, ...] = ()
    experienced_option_keys: tuple[str, ...] = ()
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
    source: Literal["leave_mgr", "assist_profile", "manual_rule", "person_shift"]
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
        unconfirmed_leave_strength="soft",
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
    """有休種別ごとの既定不可強度（確定休暇は hard、その他は soft）。"""
    return [
        LeavePolicy("有休", "hard"),
        LeavePolicy("代休", "hard"),
        LeavePolicy("公休", "hard"),
        LeavePolicy("慶弔休暇", "hard"),
        LeavePolicy("介護休暇", "hard"),
        LeavePolicy("リフレッシュ休暇", "hard"),
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
    """配置の最低基準（専従/経験、研修は include_trainees 時のみ）。"""
    if prefs.eligibility_baseline == "any":
        return True
    site = slot.site_row_id
    if _site_match(worker.dedicated_site_row_ids, site):
        return True
    if _site_match(worker.experienced_site_row_ids, site):
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
    """worker を日付 d に置いたときの、d を末尾とする連勤数。"""
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


def _static_eligible(ctx: _Context, worker: Worker, slot: RequiredSlot, day: PlanningDay) -> bool:
    """状態に依存しない適格性（在籍・番号・hard 休暇・最低基準・未経験不可・静的 forbidden）。"""
    if not worker.active:
        return False
    if not worker.employee_number:
        return False
    if (worker.employee_number, day.date) in ctx.hard_unavailable:
        return False
    if not _baseline_ok(worker, slot, ctx.prefs):
        return False
    if not _option_experience_ok(worker, slot, ctx.option_policies):
        return False
    return True


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
    # 経験者優先
    if prefs.prefer_experienced and _site_match(worker.experienced_site_row_ids, slot.site_row_id):
        score += weights.experience_bonus
        factors.append(ScoreFactor("experience", "経験者", weights.experience_bonus, "対象現場の経験あり"))
    # オプション適性（過去実績）
    option = str(slot.shift_key or "").strip()
    if option and option in worker.experienced_option_keys:
        score += weights.option_aptitude_bonus
        factors.append(ScoreFactor("aptitude", "オプション適性", weights.option_aptitude_bonus, f"{option} の実績あり"))
    # 研修者の減点（やや抑制）
    if _site_match(worker.trainee_site_row_ids, slot.site_row_id) and not _site_match(
        worker.experienced_site_row_ids, slot.site_row_id
    ) and not _site_match(worker.dedicated_site_row_ids, slot.site_row_id):
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

    # 月次偏り抑制（既配置が多いほど減点）
    if prefs.prefer_fairness:
        current = state.month_count.get(number, 0)
        if current > 0:
            penalty = weights.monthly_imbalance_penalty * current
            score -= penalty
            factors.append(ScoreFactor("fairness", "勤務数偏り", -penalty, f"今月 {current} 件"))

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

        # 3) 貪欲割当
        explanations_map: dict[str, list[ScoreFactor]] = {}
        unfilled_reason: dict[str, tuple[str, str]] = {}
        if not seed_blocked:
            self._greedy_assign(ctx, state, instances, explanations_map, unfilled_reason, start)
            # 4) 局所修復
            self._local_repair(ctx, state, instances, explanations_map, start)

        # 5) 出力組み立て
        assignments = self._build_assignments(ctx, state, instances, explanations_map)
        unfilled = self._build_unfilled(ctx, state, instances, unfilled_reason)
        explanations = self._build_explanations(assignments, explanations_map)

        # 6) result 検証（plan 後に必ず通す）
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
        )

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

        # 固定（locked/manual_locked）と pinned を (date, shift_key) 単位に集約
        fixed_by_key: dict[tuple[date, str], list[tuple[str, str, str]]] = {}
        # value: (employee_number, source, assignment_id)
        for existing in request.existing_assignments:
            if existing.lock_policy in ("locked", "manual_locked"):
                key = (existing.date, str(existing.shift_key or "").strip())
                fixed_by_key.setdefault(key, []).append(
                    (existing.employee_number, "existing_locked", existing.assignment_id)
                )
        for rule in request.rules:
            if rule.kind != "pinned" or not rule.enabled:
                continue
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
                fixed_by_key.setdefault((d, shift_key), []).append(
                    (number, "pinned", rule.rule_id)
                )

        # movable/replaceable の元担当（変更最小用）
        movable_by_key: dict[tuple[date, str], list[str]] = {}
        for existing in request.existing_assignments:
            if existing.lock_policy in ("movable", "replaceable"):
                key = (existing.date, str(existing.shift_key or "").strip())
                movable_by_key.setdefault(key, []).append(existing.employee_number)

        instances: list[_Instance] = []
        conflicts: list[_SeedConflict] = []
        consumed_fixed_keys: set[tuple[date, str]] = set()

        for slot in slots:
            key = (slot.date, str(slot.shift_key or "").strip())
            fixed_here = fixed_by_key.get(key, [])
            movable_here = list(movable_by_key.get(key, []))
            required = slot.required_count
            if len(fixed_here) > required:
                warnings.append(
                    PlanningWarning(
                        "demand_raised_for_fixed",
                        f"固定数 {len(fixed_here)} が需要 {required} を上回るため需要を引き上げます",
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                required = len(fixed_here)
            consumed_fixed_keys.add(key)

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

        # どの slot にも一致しない固定既存は 100% 保持のため合成 slot を作る
        for key, fixed_here in sorted(fixed_by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if key in consumed_fixed_keys:
                continue
            d, shift_key = key
            day = ctx.day_by_date.get(d)
            if day is None:
                continue
            warnings.append(
                PlanningWarning(
                    "fixed_without_demand",
                    f"需要に無い固定配置を保持します（{d.isoformat()} {shift_key or '指定なし'}）",
                    date=d,
                )
            )
            synthetic_slot = RequiredSlot(
                slot_id=f"existing-{d.isoformat()}-{shift_key or 'none'}",
                date=d,
                day=day.day,
                shift_key=shift_key,
                shift_label=shift_key,
                required_count=len(fixed_here),
                site_row_id=request.target_site.site_row_id,
                site_id=request.target_site.site_id,
                site_name=request.target_site.site_name,
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
        """固定 seed を投入し、validate_seed 相当の衝突検出を行う。"""
        # pinned と forbidden の同枠矛盾
        for instance in instances:
            if instance.fixed_worker is None:
                continue
            number = instance.fixed_worker
            slot = instance.slot
            day = ctx.day_by_date.get(slot.date)
            if day is None:
                continue
            option = _opt(slot.shift_key)

            # forbidden と pinned の矛盾
            if instance.fixed_source == "pinned" and (
                number,
                slot.date,
                str(slot.shift_key or "").strip(),
            ) in ctx.forbidden:
                conflicts.append(
                    _SeedConflict(
                        "blocker",
                        "pinned_forbidden_conflict",
                        f"pinned と forbidden が同一枠で矛盾します: {number}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                violations.append(
                    Violation(
                        "pinned_forbidden_conflict",
                        "blocker",
                        f"pinned と forbidden が同一枠で矛盾します: {number}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                continue

            # hard 休暇との衝突
            if (number, slot.date) in ctx.hard_unavailable:
                conflicts.append(
                    _SeedConflict(
                        "blocker",
                        "seed_hard_leave",
                        f"固定配置が hard 休暇と衝突します: {number} {slot.date.isoformat()}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                violations.append(
                    Violation(
                        "seed_hard_leave",
                        "blocker",
                        f"固定配置が hard 休暇と衝突します: {number} {slot.date.isoformat()}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                continue

            # 固定同士の同日重複（same_site=True）
            duplicate = False
            for existing_opt in state.day_options.get(number, {}).get(slot.date, ()):  # type: ignore[arg-type]
                if is_duplicate_by_rules(option, existing_opt, same_site=True):
                    duplicate = True
                    break
            if duplicate:
                conflicts.append(
                    _SeedConflict(
                        "blocker",
                        "seed_duplicate",
                        f"固定配置同士が同日重複します: {number} {slot.date.isoformat()}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                violations.append(
                    Violation(
                        "seed_duplicate",
                        "blocker",
                        f"固定配置同士が同日重複します: {number} {slot.date.isoformat()}",
                        employee_number=number,
                        date=slot.date,
                        slot_id=slot.slot_id,
                    )
                )
                continue

            # 他現場占有との衝突（same_site=False, 番号一致のみ）
            if slot.site_row_id not in ctx.relax_sites:
                for ext_opt in ctx.external_by_emp_date.get((number, slot.date), ()):  # type: ignore[arg-type]
                    if is_duplicate_by_rules(option, ext_opt, same_site=False):
                        conflicts.append(
                            _SeedConflict(
                                "blocker",
                                "seed_external_conflict",
                                f"固定配置が他現場占有と衝突します: {number} {slot.date.isoformat()}",
                                employee_number=number,
                                date=slot.date,
                                slot_id=slot.slot_id,
                            )
                        )
                        violations.append(
                            Violation(
                                "seed_external_conflict",
                                "blocker",
                                f"固定配置が他現場占有と衝突します: {number} {slot.date.isoformat()}",
                                employee_number=number,
                                date=slot.date,
                                slot_id=slot.slot_id,
                            )
                        )
                        duplicate = True
                        break
            if duplicate:
                continue

            # 衝突なし → 確保
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
        """決定的な走査で総ペナルティが厳密に減る入替・追加のみ採用する。"""
        scan = sorted(instances, key=lambda i: (i.slot.date, i.instance_id))
        for _iteration in range(self.limits.max_local_search_iterations):
            if self._timed_out(start):
                return
            improved = False
            current_cost = self._global_cost(ctx, state, instances)

            for instance in scan:
                if instance.fixed_worker is not None:
                    continue
                slot = instance.slot
                day = ctx.day_by_date.get(slot.date)
                if day is None:
                    continue

                current_worker = state.placed.get(instance.instance_id)
                option = _opt(slot.shift_key)
                is_we = day.is_weekend or day.is_holiday

                # 空き枠を埋める / 担当を入れ替える
                static_pool = [w for w in ctx.request.workers if _static_eligible(ctx, w, slot, day)]
                best_choice: tuple[float, str | None, list[ScoreFactor]] | None = None

                for worker in static_pool:
                    number = worker.employee_number
                    if number == current_worker:
                        continue
                    # 一旦現担当を外して評価
                    if current_worker is not None:
                        state.remove(instance.instance_id, day.date, option, is_we)
                    ok, _r = _hard_ok(ctx, state, worker, slot, day)
                    if not ok:
                        if current_worker is not None:
                            state.add(instance.instance_id, current_worker, day.date, option, is_we)
                        continue
                    score, factors = _score_candidate(ctx, state, instance, worker, day)
                    min_score = ctx.prefs.min_assignment_score
                    state.add(instance.instance_id, number, day.date, option, is_we)
                    candidate_cost = self._global_cost(ctx, state, instances)
                    state.remove(instance.instance_id, day.date, option, is_we)
                    if current_worker is not None:
                        state.add(instance.instance_id, current_worker, day.date, option, is_we)
                    if min_score is not None and score < min_score:
                        continue
                    if candidate_cost < current_cost - 1e-9:
                        if best_choice is None or candidate_cost < best_choice[0]:
                            best_choice = (candidate_cost, number, factors)

                if best_choice is not None:
                    _cost, number, factors = best_choice
                    if number is not None:
                        if current_worker is not None:
                            state.remove(instance.instance_id, day.date, option, is_we)
                        state.add(instance.instance_id, number, day.date, option, is_we)
                        explanations_map[instance.instance_id] = factors
                        improved = True
                        current_cost = self._global_cost(ctx, state, instances)

            if not improved:
                return

    def _global_cost(self, ctx: _Context, state: _PlanState, instances: list[_Instance]) -> float:
        """局所探索用の総コスト（充足を支配項、その他は Soft ペナルティの近似）。"""
        filled = len(state.placed)
        total_instances = len(instances)
        cost = UNFILLED_COST * (total_instances - filled)

        weights = ctx.weights
        # 連勤コスト
        threshold = ctx.prefs.max_consecutive_days if ctx.prefs.max_consecutive_days else CONSECUTIVE_SOFT_DEFAULT
        for number, dates in state.worked_dates.items():
            worker = ctx.worker_by_number.get(number)
            prior_tail = 0
            if worker and worker.monthly_limit:
                prior_tail = worker.monthly_limit.prior_month_tail_consecutive
            cost += weights.consecutive_day_penalty * _consecutive_excess(
                dates, ctx.first_day, threshold, prior_tail
            )
        # 月次偏り（Σ count^2）
        if ctx.prefs.prefer_fairness:
            for count in state.month_count.values():
                cost += weights.monthly_imbalance_penalty * count * count
        # 土日祝偏り（Σ weekend^2）
        if ctx.prefs.suppress_weekend_imbalance:
            for count in state.weekend_count.values():
                cost += weights.weekend_imbalance_penalty * count * count
        # 変更コスト
        if ctx.prefs.minimize_changes:
            for instance in instances:
                if instance.original_worker:
                    placed = state.placed.get(instance.instance_id)
                    if placed != instance.original_worker:
                        cost += weights.change_penalty
        return cost

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
    ) -> list[UnfilledSlot]:
        # slot_id ごとに不足数を集計
        shortage_by_slot: dict[str, int] = {}
        reason_by_slot: dict[str, tuple[str, str]] = {}
        slot_meta: dict[str, RequiredSlot] = {}
        for instance in instances:
            slot_meta[instance.slot.slot_id] = instance.slot
            if state.placed.get(instance.instance_id):
                continue
            shortage_by_slot[instance.slot.slot_id] = shortage_by_slot.get(instance.slot.slot_id, 0) + 1
            reason = unfilled_reason.get(instance.instance_id)
            if reason and instance.slot.slot_id not in reason_by_slot:
                reason_by_slot[instance.slot.slot_id] = reason

        unfilled: list[UnfilledSlot] = []
        for slot_id, shortage in shortage_by_slot.items():
            slot = slot_meta[slot_id]
            reason_code, reason = reason_by_slot.get(
                slot_id, ("partial_allowed", REASON_MESSAGES["partial_allowed"])
            )
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

        # hard 休暇
        if (number, assignment.date) in hard_unavailable:
            violations.append(
                Violation(
                    "hard_leave_assigned",
                    "hard",
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
                        "hard",
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
                            "hard",
                            f"他現場占有と同日重複しています: {number} {assignment.date.isoformat()}",
                            employee_number=number,
                            date=assignment.date,
                            slot_id=assignment.slot_id,
                        )
                    )
                    break
        same_day_options.setdefault((number, assignment.date), []).append(option)
        month_count[number] = month_count.get(number, 0) + 1

        # 最低基準・未経験不可（保存前再判定）
        slot = _slot_for_assignment(request, assignment)
        if slot is not None:
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
                "trainee_site_row_ids": sorted(worker.trainee_site_row_ids),
                "experienced_option_keys": sorted(worker.experienced_option_keys),
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
