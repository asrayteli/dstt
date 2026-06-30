"""CloudShift／Employee／SitePlus／有休共有ツールから ShiftPlanningRequest を構築するアダプタ。

設計書「人員ソースと正規化」「需要の作り方」「既存アシストからの移行」に対応する。
エンジン本体は DB を読まないため、DB アクセスはすべてこの層に閉じ込める。
データ源が欠けても例外で落とさず、warning として返して可能な範囲で動かす。
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date
from typing import Any

from .cloudshift_shift_engine import (
    ExistingAssignment,
    ExternalAssignment,
    PlanningPreferences,
    PlanningWarning,
    RequiredSlot,
    Rule,
    ShiftEngineSettings,
    ShiftPlanningRequest,
    SiteRef,
    UnavailableDay,
    Worker,
    WorkerMonthlyLimit,
    WorkerPreference,
    REQUEST_VERSION,
    build_planning_days,
    default_planning_preferences,
    migrate_settings,
)

try:
    from ..tools.shiftersync_check import LEAVE_OPTION_KEYS, ROLE_OPTION_KEYS
except ImportError:  # pragma: no cover
    from app.tools.shiftersync_check import (  # type: ignore
        LEAVE_OPTION_KEYS,
        ROLE_OPTION_KEYS,
    )

# 役割オプション → 経験レベル（SUB=代務は一人で勤務した「実績」、TRAIN=研修済み）
_ROLE_OPTION_SUBSTITUTE = "SUB"
_ROLE_OPTION_TRAINING = "TRAIN"

# 未確認休暇の既定（confirmed_by が空のとき）。設定で上書き可。
# 休みが入っている日に自動作成がシフトを入れないことを最優先し hard とする。
_UNCONFIRMED_DEFAULT = "hard"

# 対象日の必要人数（UI 指定）の上限。1 日 1 現場に現実的に必要な人数を大きく
# 超える値を許すと、日数×人数ぶんの割当インスタンスを生成してメモリ・計算資源を
# 枯渇させられるため、必要人数入力の上限（99）に合わせてクランプする。
_MAX_TARGET_REQUIRED_COUNT = 99


# ---------------------------------------------------------------------------
# 小さなユーティリティ
# ---------------------------------------------------------------------------


def _str(value: Any) -> str:
    return str(value or "").strip()


def _coerce_site_row_id(value: Any) -> str:
    # site_row_id は数値文字列。0 以下・非数値は無効として "" に正規化する
    # （app/tools/shiftersync_format._normalize_site_row_id と同じ規約）。
    text = _str(value)
    if text.isdigit() and int(text) > 0:
        return text
    return ""


def _round_half_up(value: float) -> int:
    return int(value + 0.5)


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


# ---------------------------------------------------------------------------
# 在籍状態の導出（Employee.is_deleted / retirement_date）
# ---------------------------------------------------------------------------

_CLEAR_RETIRED_TOKENS = {"退職", "退職済", "退社"}


def _derive_active(emp: Any, warnings: list[PlanningWarning], number: str) -> bool:
    """Employee から在籍状態を保守的に導出する。確実に退職のときだけ False。"""
    if emp is None:
        return True
    if getattr(emp, "is_deleted", False):
        return False
    retirement = _str(getattr(emp, "retirement_date", ""))
    if not retirement:
        return True
    # 解釈できた日付は確定的に判定する（過去・当日なら退職、未来日なら在籍）。
    # 未来日は「判定不能」ではないため警告を出さない。
    iso = retirement.replace("/", "-")
    parts = iso.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        try:
            ret_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
            return ret_date > date.today()
        except ValueError:
            pass
    # 「？退職？」等の非正規値は判定不能 → 在籍維持＋warning
    if "?" in retirement or "？" in retirement or retirement not in _CLEAR_RETIRED_TOKENS:
        if retirement not in _CLEAR_RETIRED_TOKENS:
            warnings.append(
                PlanningWarning(
                    "uncertain_retirement",
                    f"退職日付が判定不能のため在籍として扱います: {number} ({retirement})",
                    employee_number=number,
                )
            )
            return True
    return False


# ---------------------------------------------------------------------------
# Worker 構築
# ---------------------------------------------------------------------------


def _role_option_sites_from_entries(
    project: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    """全月の確定 entry の第二オプション（代務/研修）から経験を直接導出する。

    SUB（代務）= 一人で勤務した実績、TRAIN（研修）= 研修済み。いずれも要件どおり
    アシストの「経験済み現場」へも反映するため experienced へ含める。
    戻り値は (経験 {社員番号: {site_row_id}}, 研修済み {同}, 氏名 {社員番号: 氏名})。
    """
    experienced: dict[str, set[str]] = {}
    trained: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    if not site_row_id:
        return experienced, trained, names
    try:
        from app.tools.shiftersync_format import entry_second_option, parse_entry_value
    except Exception:  # pragma: no cover
        return experienced, trained, names

    for month_data in (project.get("months") or {}).values():
        if not isinstance(month_data, dict):
            continue
        for day_entries in (month_data.get("entries_per_day") or {}).values():
            if not isinstance(day_entries, list):
                continue
            for entry in day_entries:
                if not isinstance(entry, dict):
                    continue
                key = entry_second_option(entry)
                if key not in ROLE_OPTION_KEYS:
                    continue
                number = _str(entry.get("employee_number"))
                if not number:
                    continue
                # 代務（SUB）= 一人で勤務した実績 → 経験。
                # 研修（TRAIN）= 教わったが一人ではやっていない → 研修済みのみ。
                # 実績(120点)と研修済み(60点)の習熟度差を保つため、TRAIN は経験へ
                # 入れない（アシスト records 経路の振り分けと統一）。
                if key == _ROLE_OPTION_TRAINING:
                    trained.setdefault(number, set()).add(site_row_id)
                else:
                    experienced.setdefault(number, set()).add(site_row_id)
                _option, name = parse_entry_value(entry.get("value") or "")
                if _str(name):
                    names.setdefault(number, _str(name))
    return experienced, trained, names


def _site_experience_counts(
    project: dict[str, Any],
) -> tuple[dict[str, int], dict[str, str]]:
    """対象シフト帳の全月の確定シフトから社員ごとの出勤実績数と氏名を集める。

    実績が多い人ほどその現場の優先度を上げる（エンジンの experience_count_bonus）
    ために使う。次は実績に数えない:
    - 有休系オプション（休み）
    - 研修（TRAIN）= 教わったが一人での実績ではない（研修済みは別途扱う）
    戻り値は (実績数 {社員番号: 回数}, 氏名 {社員番号: 氏名})。
    """
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    try:
        from app.tools.shiftersync_format import entry_second_option, parse_entry_value
    except Exception:  # pragma: no cover
        return counts, names
    for month_data in (project.get("months") or {}).values():
        if not isinstance(month_data, dict):
            continue
        for day_entries in (month_data.get("entries_per_day") or {}).values():
            if not isinstance(day_entries, list):
                continue
            for entry in day_entries:
                if not isinstance(entry, dict):
                    continue
                number = _str(entry.get("employee_number"))
                if not number:
                    continue
                option, name = parse_entry_value(entry.get("value") or "")
                opt = _str(option)
                if opt in LEAVE_OPTION_KEYS:
                    continue
                # 研修(TRAIN)は一人での実績ではないため実績数に数えない
                role = _str(entry_second_option(entry)) or opt
                if role == _ROLE_OPTION_TRAINING:
                    continue
                counts[number] = counts.get(number, 0) + 1
                if _str(name):
                    names.setdefault(number, _str(name))
    return counts, names


def _load_employees(numbers: set[str]) -> dict[str, Any]:
    """Employee を employee_number で引けるようにする（DB 不在なら空）。"""
    if not numbers:
        return {}
    try:
        from app.models import Employee  # 遅延 import（重い blueprint を避ける）

        rows = Employee.query.filter(Employee.employee_number.in_(list(numbers))).all()
        return {_str(r.employee_number): r for r in rows}
    except Exception:  # pragma: no cover - app context / DB 不在
        return {}


def build_workers(
    project: dict[str, Any],
    settings: ShiftEngineSettings,
    warnings: list[PlanningWarning],
) -> list[Worker]:
    """assist・専従マスタ・Employee から Worker を合成する。"""
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    assist = project.get("assist") if isinstance(project.get("assist"), dict) else {}

    dedicated_numbers: set[str] = set()
    names: dict[str, str] = {}
    experienced_sites: dict[str, set[str]] = {}
    trained_sites: dict[str, set[str]] = {}  # 研修済み（TRAIN オプション由来）
    trainee_sites: dict[str, set[str]] = {}
    option_keys: dict[str, set[str]] = {}
    preferred: dict[str, tuple[int, ...]] = {}
    blocked: dict[str, tuple[int, ...]] = {}

    # 専従者（SiteContractMaster 由来）
    try:
        from app.tools.cloudshift import _project_registered_dedicated_candidates

        for item in _project_registered_dedicated_candidates(project) or []:
            number = _str(item.get("employee_number"))
            if not number:
                continue
            dedicated_numbers.add(number)
            if item.get("name"):
                names.setdefault(number, _str(item.get("name")))
    except Exception:  # pragma: no cover
        pass

    # 経験現場
    for item in (assist.get("experienced_sites") or []):
        if not isinstance(item, dict):
            continue
        number = _str(item.get("employee_number"))
        srid = _coerce_site_row_id(item.get("site_row_id"))
        if number and srid:
            experienced_sites.setdefault(number, set()).add(srid)
        if number and item.get("shift_key"):
            option_keys.setdefault(number, set()).add(_str(item.get("shift_key")))

    # 研修現場
    for item in (assist.get("training_sites") or []):
        if not isinstance(item, dict):
            continue
        number = _str(item.get("employee_number"))
        srid = _coerce_site_row_id(item.get("site_row_id"))
        if number and srid:
            trainee_sites.setdefault(number, set()).add(srid)

    # 実績（この現場の経験 + 経験オプション）。
    # 研修（TRAIN）実績は「教わったが一人ではやっていない」ため研修済みへ、
    # 代務（SUB）実績は一人で勤務した実績として経験へ振り分ける。
    for item in (assist.get("records") or []):
        if not isinstance(item, dict):
            continue
        number = _str(item.get("employee_number"))
        if not number:
            continue
        shift_key = _str(item.get("shift_key")).upper()
        if site_row_id:
            if shift_key == _ROLE_OPTION_TRAINING:
                trained_sites.setdefault(number, set()).add(site_row_id)
            else:
                experienced_sites.setdefault(number, set()).add(site_row_id)
        if shift_key and shift_key not in ROLE_OPTION_KEYS:
            option_keys.setdefault(number, set()).add(shift_key)

    # シフト entry の 代務/研修 オプションからも直接導出する。
    # アシストへの自動登録前（保存直後の試算など）でも経験として扱えるようにする。
    entry_experienced, entry_trained, entry_names = _role_option_sites_from_entries(project)
    for number, sites in entry_experienced.items():
        experienced_sites.setdefault(number, set()).update(sites)
    for number, sites in entry_trained.items():
        trained_sites.setdefault(number, set()).update(sites)
    for number, name in entry_names.items():
        names.setdefault(number, name)

    # 研修要現場（trainee）から、経験済み・研修済みになった現場を除く（要件5）。
    # 保存前の試算でも研修要が残らないようにする。
    for number, sites in list(trainee_sites.items()):
        done = experienced_sites.get(number, set()) | trained_sites.get(number, set())
        remaining = sites - done
        if remaining:
            trainee_sites[number] = remaining
        else:
            trainee_sites.pop(number, None)

    # プロファイル（希望/NG 曜日・氏名）
    for item in (assist.get("profiles") or []):
        if not isinstance(item, dict):
            continue
        number = _str(item.get("employee_number"))
        if not number:
            continue
        if item.get("name"):
            names.setdefault(number, _str(item.get("name")))
        preferred[number] = tuple(int(x) for x in (item.get("preferred_weekdays") or []) if str(x).isdigit())
        blocked[number] = tuple(int(x) for x in (item.get("blocked_weekdays") or []) if str(x).isdigit())

    # 対象現場での過去の出勤実績数（実績の厚みによる優先度付けに使う）
    experience_counts, history_names = _site_experience_counts(project)
    for number, nm in history_names.items():
        names.setdefault(number, nm)
    # 過去に実際に勤務した人は、アシスト未登録でも「経験者の候補」として扱う
    # （実績がある人を確実に候補へ含め、回数で優先度を上げる）。
    if site_row_id:
        for number in experience_counts:
            experienced_sites.setdefault(number, set()).add(site_row_id)

    candidate_numbers = (
        set(dedicated_numbers)
        | set(experienced_sites)
        | set(trained_sites)
        | set(trainee_sites)
        | set(option_keys)
        | set(preferred)
        | set(blocked)
        | set(names)
        | set(experience_counts)
    )
    candidate_numbers.discard("")

    employees = _load_employees(candidate_numbers)

    # 上限設定（個人別 + 全体）
    limit_by_number: dict[str, tuple[int | None, int | None]] = {}
    global_min: int | None = None
    global_max: int | None = None
    for wl in settings.worker_limits:
        if wl.employee_number in ("", "*", "ALL"):
            global_min, global_max = wl.min_assignments, wl.max_assignments
        else:
            limit_by_number[wl.employee_number] = (wl.min_assignments, wl.max_assignments)

    # 個別重み（Rule: worker_weight）
    weight_by_number: dict[str, int] = {}
    for rule in settings.rules:
        if rule.kind == "worker_weight" and rule.enabled:
            number = _str(rule.params.get("employee_number"))
            try:
                weight_by_number[number] = int(rule.params.get("weight", 0) or 0)
            except (TypeError, ValueError):
                pass

    workers: list[Worker] = []
    for number in sorted(candidate_numbers):
        emp = employees.get(number)
        if emp is None:
            warnings.append(
                PlanningWarning(
                    "assist_without_employee",
                    f"候補者が社員マスタに存在しません（在籍未確認のまま扱います）: {number}",
                    employee_number=number,
                )
            )
        active = _derive_active(emp, warnings, number)
        name = _str(getattr(emp, "employee_name", "")) or names.get(number, "") or number

        pref = None
        if preferred.get(number) or blocked.get(number):
            pref = WorkerPreference(
                preferred_weekdays=preferred.get(number, ()),
                blocked_weekdays=blocked.get(number, ()),
            )

        min_a, max_a = limit_by_number.get(number, (global_min, global_max))
        limit = None
        if min_a is not None or max_a is not None:
            limit = WorkerMonthlyLimit(min_assignments=min_a, max_assignments=max_a)

        workers.append(
            Worker(
                worker_id=f"emp-{number}",
                employee_number=number,
                name=name,
                active=active,
                employee_type=_str(getattr(emp, "employee_type", "")),
                office_name=_str(getattr(emp, "office_name", "")),
                office_code=_str(getattr(emp, "office_code", "")),
                dedicated_site_row_ids=(site_row_id,) if number in dedicated_numbers and site_row_id else (),
                experienced_site_row_ids=tuple(sorted(experienced_sites.get(number, set()))),
                trained_site_row_ids=tuple(sorted(trained_sites.get(number, set()))),
                trainee_site_row_ids=tuple(sorted(trainee_sites.get(number, set()))),
                experienced_option_keys=tuple(sorted(option_keys.get(number, set()))),
                site_experience_count=experience_counts.get(number, 0),
                preference=pref,
                monthly_limit=limit,
                worker_weight=weight_by_number.get(number, 0),
            )
        )

    return _apply_candidate_filters(workers, settings, warnings)


def _apply_candidate_filters(
    workers: list[Worker],
    settings: ShiftEngineSettings,
    warnings: list[PlanningWarning],
) -> list[Worker]:
    """advanced_options の候補フィルタ（office/type/allow/block）を適用する。"""
    office_filter: set[str] = set()
    type_filter: set[str] = set()
    allowlist: set[str] = set()
    blocklist: set[str] = set()
    for opt in settings.advanced_options:
        if not opt.enabled:
            continue
        values = opt.value if isinstance(opt.value, (list, tuple)) else ([opt.value] if opt.value else [])
        values = {_str(v) for v in values if _str(v)}
        if opt.key == "office_filter":
            office_filter |= values
        elif opt.key == "employee_type_filter":
            type_filter |= values
        elif opt.key == "candidate_allowlist":
            allowlist |= values
        elif opt.key == "candidate_blocklist":
            blocklist |= values

    conflict = allowlist & blocklist
    if conflict:
        warnings.append(
            PlanningWarning(
                "allow_block_conflict",
                f"allowlist と blocklist に重複があり、除外側を採用します: {', '.join(sorted(conflict))}",
            )
        )

    filtered: list[Worker] = []
    for worker in workers:
        number = worker.employee_number
        if number in blocklist:
            continue
        if allowlist and number not in allowlist:
            continue
        # office_filter は所属コード（Employee.office_code）で判定する
        if office_filter and worker.office_code not in office_filter:
            continue
        if type_filter and worker.employee_type not in type_filter:
            continue
        filtered.append(worker)
    return filtered


# ---------------------------------------------------------------------------
# 既存配置・他現場占有
# ---------------------------------------------------------------------------


def _lock_policy_for(existing_policy: str, sync_source_type: str) -> str:
    if existing_policy == "lock_all":
        return "locked"
    if existing_policy == "replace_all":
        return "replaceable"
    # lock_manual: 手入力は固定、同期由来は再配置可
    return "manual_locked" if not sync_source_type else "movable"


def build_existing_assignments(
    project: dict[str, Any],
    month_data: dict[str, Any],
    year: int,
    month: int,
    existing_policy: str,
    warnings: list[PlanningWarning],
) -> list[ExistingAssignment]:
    """対象月の確定 entries_per_day を ExistingAssignment + lock_policy へ変換する。"""
    from app.tools.shiftersync_format import normalize_entries_for_month, parse_entry_value

    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    entries = normalize_entries_for_month(month_data.get("entries_per_day"), year, month)
    result: list[ExistingAssignment] = []
    for day_key, day_entries in entries.items():
        try:
            day = int(day_key)
        except (TypeError, ValueError):
            continue
        d = date(year, month, day)
        for entry in day_entries:
            number = _str(entry.get("employee_number"))
            option, name = parse_entry_value(entry.get("value") or "")
            shift_key = _str(option)
            if not number:
                # 番号なしは自動配置の対象にできない。固定保持できないため warning。
                warnings.append(
                    PlanningWarning(
                        "existing_without_number",
                        f"社員番号のない既存配置はエンジン管理外です: {d.isoformat()} {name}",
                        date=d,
                    )
                )
                continue
            sync_type = _str(entry.get("sync_source_type"))
            # 有休系オプションの entry は「休みの予定」であり、自動作成が動かしては
            # いけないため existing_policy に関わらず固定で保持する。
            lock_policy = (
                "locked" if shift_key in LEAVE_OPTION_KEYS
                else _lock_policy_for(existing_policy, sync_type)
            )
            result.append(
                ExistingAssignment(
                    assignment_id=f"existing-{_str(entry.get('id')) or day_key}-{number}",
                    date=d,
                    day=day,
                    slot_key=f"{day}-{shift_key}",
                    shift_key=shift_key,
                    employee_number=number,
                    employee_name=name or number,
                    source_type="manual" if not sync_type else "scene_sync",  # type: ignore[arg-type]
                    entry_id=_str(entry.get("id")),
                    lock_policy=lock_policy,  # type: ignore[arg-type]
                    site_row_id=_str(entry.get("site_row_id")) or site_row_id,
                    site_id=_str(entry.get("site_id")),
                    site_name=_str(entry.get("site_name")),
                    site_branch_row_id=_str(entry.get("site_branch_row_id")),
                    site_branch=_str(entry.get("site_branch")),
                )
            )
    return result


# 勤務占有として扱うモード。
# - scene/person: entry の employee がそのまま勤務者。
# - substitute(要代務): 解決済み（回答者が代務者を割り当てた）entry の代務者だけを
#   勤務者として扱う。代務者が割り当たればその人の仕事になるため占有に含める。
#   未割当・未解決の依頼は勤務確定でないため除外する（_substitute_assignment が判定）。
# master(テンプレート)は実勤務ではないため一切対象外。
_OCCUPANCY_DIRECT_MODES = {"scene", "person"}  # entry の employee がそのまま勤務者
_OCCUPANCY_MODES = {"scene", "person", "substitute"}


def build_external_assignments(
    project: dict[str, Any], year: int, month: int, warnings: list[PlanningWarning]
) -> tuple[list[ExternalAssignment], list[UnavailableDay]]:
    """会社全体の全シフト帳から、候補者の同日勤務占有と休みを集約する。

    オーナーを問わず（他管理者の帳も含め）、対象月の確定シフトを走査する。
    - 勤務占有（ExternalAssignment）: 同じ人が同じ日にどこかで勤務していれば、対象
      現場の同日配置を Hard で防ぐための入力。
      - scene/person 帳: entry の勤務者。
      - substitute(要代務)帳: 回答者が代務者を割り当てた（解決済み）entry の代務者
        （その人の仕事になるため占有に数える）。
    - 休み（UnavailableDay, hard）: scene/person 帳で休みが入っていればその日は配置しない。
    - master（テンプレート）は実勤務でないため対象外。
    - 対象シフト帳自身（同 id）と、対象帳から同期された mirror entry
      （sync_source_project_id == 対象 id）は除外し、自帳の予定で自分を締め出さない。
    戻り値は (external_assignments, leave_unavailable_days)。
    """
    try:
        from app.tools.cloudshift import _iter_stored_projects, _substitute_assignment
        from app.tools.shiftersync_format import parse_entry_value
    except Exception:  # pragma: no cover
        return [], []

    # プロジェクト一覧は月内で不変なので一度だけ全件取得して使い回す。
    try:
        stored = list(_iter_stored_projects())
    except Exception:  # pragma: no cover
        return [], []

    target_id = _str(project.get("id"))
    month_key = _month_key(year, month)
    days_in_month = monthrange(year, month)[1]

    external: list[ExternalAssignment] = []
    leave_days: list[UnavailableDay] = []
    seen_work: set[tuple[str, date, str, str]] = set()
    seen_leave: set[tuple[str, date]] = set()
    seen_warn: set[tuple[str, str]] = set()

    def warn(code: str, name: str, d: date) -> None:
        key = (code, name)
        if key in seen_warn:
            return
        seen_warn.add(key)
        message = (
            f"社員番号のない他シフト帳の休みは自動照合しません: {name}"
            if code == "leave_without_number"
            else f"番号で突合できない他シフト帳の勤務は警告扱いにします: {name}"
        )
        warnings.append(PlanningWarning(code, message, date=d))

    def add_work(number, name, d, shift_key, project_id, project_title, source_mode):
        key = (number, d, shift_key, project_id)
        if key in seen_work:
            return
        seen_work.add(key)
        external.append(
            ExternalAssignment(
                employee_number=number,
                employee_name=name,
                date=d,
                day=d.day,
                shift_key=shift_key,
                project_id=project_id,
                project_title=project_title,
                source_mode=source_mode,
                confirmed=True,
            )
        )

    for other in stored:
        if not isinstance(other, dict):
            continue
        if _str(other.get("id")) == target_id:
            continue
        mode = _str(other.get("mode"))
        if mode not in _OCCUPANCY_MODES:
            continue  # master 等は対象外
        month_data = (other.get("months") or {}).get(month_key)
        if not isinstance(month_data, dict):
            continue
        entries_per_day = month_data.get("entries_per_day") or {}
        if not isinstance(entries_per_day, dict):
            continue
        project_id = _str(other.get("id"))
        project_title = _str(other.get("title")) or "他シフト帳"
        book_number = _str(other.get("employee_number"))  # person 帳の本人
        is_direct = mode in _OCCUPANCY_DIRECT_MODES
        for day_key, day_entries in entries_per_day.items():
            if not isinstance(day_entries, list):
                continue
            try:
                d = date(year, month, int(day_key))
            except (TypeError, ValueError):
                continue
            if not (1 <= d.day <= days_in_month):
                continue
            for entry in day_entries:
                if not isinstance(entry, dict):
                    continue
                # 対象帳から同期された mirror は自帳の予定なので除外する
                if target_id and _str(entry.get("sync_source_project_id")) == target_id:
                    continue

                if not is_direct:
                    # 要代務帳: 回答者が代務者を割り当てた（解決済み）entry の代務者を勤務扱い
                    assignment = _substitute_assignment(entry)
                    if not assignment or assignment.get("unassigned_helper"):
                        continue
                    helper = _str(assignment.get("employee_number"))
                    if not helper:
                        continue
                    add_work(
                        helper, _str(assignment.get("employee_name")), d,
                        _str(assignment.get("option_key")), project_id, project_title, "substitute",
                    )
                    continue

                option, name = parse_entry_value(entry.get("value") or "")
                shift_key = _str(option)
                number = _str(entry.get("employee_number")) or book_number
                if shift_key in LEAVE_OPTION_KEYS:
                    if not number:
                        warn("leave_without_number", name, d)
                        continue
                    key = (number, d)
                    if key in seen_leave:
                        continue
                    seen_leave.add(key)
                    leave_days.append(
                        UnavailableDay(
                            employee_number=number,
                            date=d,
                            reason=f"{project_title}に休み予定",
                            source="shift_entry",
                            strength="hard",
                            confirmed=True,
                        )
                    )
                    continue
                if not number:
                    warn("external_without_number", name, d)
                    continue
                add_work(number, name, d, shift_key, project_id, project_title, mode)

    external.sort(key=lambda e: (e.employee_number, e.date, e.shift_key, e.project_id))
    leave_days.sort(key=lambda u: (u.employee_number, u.date))
    return external, leave_days


def leave_days_from_existing(existing: list[ExistingAssignment]) -> list[UnavailableDay]:
    """対象シフト帳の有休系オプション entry を hard の不可日へ変換する。

    休みの予定が入っている日に、自動作成が同じ人へ勤務シフトを足さないための入力。
    （有休系オプションは is_duplicate_by_rules では重複扱いにならないため、
    既存配置として持つだけでは同日への新規配置を防げない。）
    """
    result: list[UnavailableDay] = []
    seen: set[tuple[str, date]] = set()
    for assignment in existing:
        if assignment.shift_key not in LEAVE_OPTION_KEYS:
            continue
        number = _str(assignment.employee_number)
        if not number:
            continue
        key = (number, assignment.date)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            UnavailableDay(
                employee_number=number,
                date=assignment.date,
                reason="このシフト帳に休み予定",
                source="shift_entry",
                strength="hard",
                confirmed=True,
            )
        )
    return result


# ---------------------------------------------------------------------------
# 不可日（有休共有ツール）
# ---------------------------------------------------------------------------


def build_unavailable_days(
    calendar_ids: list[str],
    year: int,
    month: int,
    settings: ShiftEngineSettings,
    unconfirmed_strength: str,
    warnings: list[PlanningWarning],
) -> list[UnavailableDay]:
    """有休共有カレンダーから UnavailableDay を作る。"""
    if not calendar_ids:
        return []
    try:
        from app.tools import leave_mgr
    except Exception:  # pragma: no cover
        return []

    strength_by_type = {p.leave_type: p.strength for p in settings.leave_policies}
    ym = f"{year:04d}{month:02d}"
    result: list[UnavailableDay] = []
    for calendar_id in calendar_ids:
        try:
            data = leave_mgr.load_calendar_data(calendar_id, ym)
        except Exception:  # pragma: no cover
            continue
        for leave in (data.get("leaves") or []):
            if not isinstance(leave, dict):
                continue
            d = _parse_date(leave.get("date"))
            if d is None or d.year != year or d.month != month:
                continue
            number = _str(leave.get("employee_number"))
            leave_type = _str(leave.get("leave_type"))
            if not number:
                warnings.append(
                    PlanningWarning(
                        "leave_without_number",
                        f"社員番号のない休暇は自動照合しません: {d.isoformat()} {_str(leave.get('name'))}",
                        date=d,
                    )
                )
                continue
            confirmed = bool(_str(leave.get("confirmed_by")))
            if confirmed:
                # ポリシー未定義の種別も既定は hard（休みの日に配置しない）
                strength = strength_by_type.get(leave_type, "hard")
            else:
                strength = unconfirmed_strength
            result.append(
                UnavailableDay(
                    employee_number=number,
                    date=d,
                    reason=leave_type or "休暇",
                    source="leave_mgr",
                    strength=strength,  # type: ignore[arg-type]
                    confirmed=confirmed,
                )
            )
    return result


def _parse_date(value: Any) -> date | None:
    text = _str(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 需要（demand_rules → capacity_fallback → prev_month_estimate）
# ---------------------------------------------------------------------------


def build_required_slots(
    project: dict[str, Any],
    month_data: dict[str, Any],
    settings: ShiftEngineSettings,
    days,
    year: int,
    month: int,
    warnings: list[PlanningWarning],
) -> tuple[list[RequiredSlot], str]:
    """需要を上から評価し、最初に確定したソースで RequiredSlot を作る。戻り値は (slots, source)。"""
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    site_id = _str(project.get("site_id"))
    site_name = _str(project.get("site_name"))

    enabled_rules = [r for r in settings.demand_rules if r.enabled]
    if enabled_rules:
        return _slots_from_demand_rules(enabled_rules, days, site_row_id, site_id, site_name), "settings"

    capacity_enabled = bool(month_data.get("capacity_enabled"))
    required_capacity = int(month_data.get("required_capacity") or 0)
    if capacity_enabled and required_capacity > 0:
        slots = [
            RequiredSlot(
                slot_id=f"cap-{d.date.isoformat()}",
                date=d.date,
                day=d.day,
                shift_key="",
                shift_label="",
                required_count=required_capacity,
                site_row_id=site_row_id,
                site_id=site_id,
                site_name=site_name,
                source="required_capacity_fallback",
            )
            for d in days
        ]
        return slots, "required_capacity_fallback"

    # 前月パターン推定
    slots = _slots_from_prev_month(project, days, year, month, site_row_id, site_id, site_name)
    if slots:
        warnings.append(
            PlanningWarning(
                "prev_month_estimate",
                "需要が未設定のため前月の確定シフトから曜日別平均で推定しました（要確認）",
            )
        )
        return slots, "prev_month_estimate"

    warnings.append(
        PlanningWarning("demand_unset", "需要が未設定で、前月の確定データもないため需要ゼロです")
    )
    return [], "none"


def _slots_from_demand_rules(rules, days, site_row_id, site_id, site_name) -> list[RequiredSlot]:
    # (date, shift_key, branch) ごとに required_count を加算
    agg: dict[tuple[date, str, str], dict[str, Any]] = {}
    for d in days:
        for rule in rules:
            in_include = d.date in rule.include_dates
            if d.date in rule.exclude_dates:
                continue
            weekday_match = d.weekday in rule.weekdays
            holiday_ok = (not d.is_holiday) or rule.include_holidays
            applies = in_include or (weekday_match and holiday_ok)
            if not applies:
                continue
            key = (d.date, rule.shift_key, rule.site_branch_row_id)
            bucket = agg.setdefault(
                key,
                {
                    "count": 0,
                    "shift_label": rule.shift_label,
                    "site_branch": rule.site_branch,
                    "priority": rule.priority,
                    "day": d.day,
                    "vehicle": rule.required_vehicle_options,
                    "qual": rule.required_qualification_codes,
                },
            )
            bucket["count"] += max(0, rule.required_count)

    slots: list[RequiredSlot] = []
    for (d, shift_key, branch), bucket in agg.items():
        if bucket["count"] <= 0:
            continue
        slots.append(
            RequiredSlot(
                slot_id=f"rule-{d.isoformat()}-{shift_key or 'none'}-{branch or '0'}",
                date=d,
                day=bucket["day"],
                shift_key=shift_key,
                shift_label=bucket["shift_label"] or shift_key,
                required_count=bucket["count"],
                site_row_id=site_row_id,
                site_id=site_id,
                site_name=site_name,
                site_branch_row_id=branch,
                site_branch=bucket["site_branch"],
                required_qualification_codes=bucket["qual"],
                required_vehicle_options=bucket["vehicle"],
                priority=bucket["priority"],
                source="settings",
            )
        )
    return slots


def _slots_from_prev_month(project, days, year, month, site_row_id, site_id, site_name) -> list[RequiredSlot]:
    from app.tools.shiftersync_format import normalize_entries_for_month, parse_entry_value

    prev_year, prev_month = _prev_month(year, month)
    prev_data = (project.get("months") or {}).get(_month_key(prev_year, prev_month))
    if not isinstance(prev_data, dict):
        return []
    prev_entries = normalize_entries_for_month(prev_data.get("entries_per_day"), prev_year, prev_month)

    # 前月の曜日別平均人数（有休系 entry は勤務ではないため数えない）
    by_weekday_counts: dict[int, list[int]] = {}
    prev_days = monthrange(prev_year, prev_month)[1]
    for day in range(1, prev_days + 1):
        d = date(prev_year, prev_month, day)
        count = 0
        for entry in (prev_entries.get(str(day)) or []):
            option, _name = parse_entry_value(entry.get("value") or "")
            if _str(option) not in LEAVE_OPTION_KEYS:
                count += 1
        by_weekday_counts.setdefault(d.weekday(), []).append(count)

    avg_by_weekday: dict[int, int] = {}
    for weekday, counts in by_weekday_counts.items():
        if counts:
            avg_by_weekday[weekday] = _round_half_up(sum(counts) / len(counts))

    slots: list[RequiredSlot] = []
    for d in days:
        required = avg_by_weekday.get(d.weekday, 0)
        if required <= 0:
            continue
        slots.append(
            RequiredSlot(
                slot_id=f"prev-{d.date.isoformat()}",
                date=d.date,
                day=d.day,
                shift_key="",
                shift_label="",
                required_count=required,
                site_row_id=site_row_id,
                site_id=site_id,
                site_name=site_name,
                source="prev_month_estimate",
            )
        )
    return slots


# ---------------------------------------------------------------------------
# 方針の解決（plan 明示値 > project 既定 > エンジン既定）
# ---------------------------------------------------------------------------


def resolve_preferences(
    settings: ShiftEngineSettings, overrides: dict[str, Any] | None
) -> PlanningPreferences:
    base = settings.default_preferences or default_planning_preferences()
    if not overrides:
        return base
    fields = set(PlanningPreferences.__dataclass_fields__.keys())
    updates: dict[str, Any] = {}
    int_fields = {"min_assignment_score", "max_consecutive_days",
                  "min_monthly_assignments", "max_monthly_assignments"}
    bool_fields = {"allow_partial", "include_trainees", "prefer_dedicated",
                   "prefer_experienced", "prefer_fairness", "minimize_changes",
                   "suppress_weekend_imbalance", "monthly_limit_hard", "max_consecutive_days_hard"}
    for key, value in overrides.items():
        if key not in fields:
            continue
        if key in int_fields:
            if value in (None, ""):
                updates[key] = None
            else:
                try:
                    updates[key] = int(value)
                except (TypeError, ValueError):
                    continue
        elif key in bool_fields:
            updates[key] = bool(value)
        else:
            updates[key] = value
    return replace(base, **updates)


def _ensure_fixed_workers_present(
    workers: list[Worker],
    existing: list[ExistingAssignment],
    settings: ShiftEngineSettings,
    site_row_id: str,
) -> None:
    """固定（locked/manual_locked）・pinned の担当者を候補プールに補完する。"""
    present = {w.employee_number for w in workers}
    name_by_number: dict[str, str] = {}
    needed: list[str] = []

    for assignment in existing:
        if assignment.lock_policy in ("locked", "manual_locked"):
            number = _str(assignment.employee_number)
            if number and number not in present and number not in name_by_number:
                name_by_number[number] = _str(assignment.employee_name) or number
                needed.append(number)
    for rule in settings.rules:
        if rule.kind == "pinned" and rule.enabled:
            number = _str(rule.params.get("employee_number"))
            if number and number not in present and number not in name_by_number:
                name_by_number[number] = number
                needed.append(number)

    for number in needed:
        workers.append(
            Worker(
                worker_id=f"fixed-{number}",
                employee_number=number,
                name=name_by_number.get(number, number),
                active=True,
                # 固定として実配置されている＝この現場の経験者とみなす（最低基準を満たす）
                experienced_site_row_ids=(site_row_id,) if site_row_id else (),
            )
        )


def _relax_sites(settings: ShiftEngineSettings) -> tuple[str, ...]:
    sites: list[str] = []
    for opt in settings.advanced_options:
        if opt.enabled and opt.key == "external_occupancy_relax":
            if opt.target:
                sites.append(_str(opt.target))
    return tuple(sites)


# ---------------------------------------------------------------------------
# メインエントリ
# ---------------------------------------------------------------------------


def _coerce_fill_target_dates(
    fill_target_days: Any, year: int, month: int
) -> tuple[date, ...]:
    """UI 指定の対象日（日番号 or ISO 日付の混在可）を対象月内の date へ正規化する。"""
    if not fill_target_days:
        return ()
    days_in_month = monthrange(year, month)[1]
    result: set[date] = set()
    for item in fill_target_days:
        d: date | None = None
        text = _str(item)
        if text.isdigit():
            day = int(text)
            if 1 <= day <= days_in_month:
                d = date(year, month, day)
        else:
            d = _parse_date(item)
            if d and (d.year != year or d.month != month):
                d = None
        if d is not None:
            result.add(d)
    return tuple(sorted(result))


def _apply_target_required_override(
    required_slots: list[RequiredSlot],
    target_dates: tuple[date, ...],
    target_required_count: Any,
    days,
    project: dict[str, Any],
    warnings: list[PlanningWarning],
) -> list[RequiredSlot]:
    """対象日の「オプション指定なしの必要人数」を UI 指定値で上書きする。

    需要設定が無い・前月推定が 0 の日でも、ユーザーが対象日と人数を明示すれば
    その日を埋められるようにする（「対象日なのに需要 0 で配置されない」の救済）。
    オプション別・枝番別の需要 slot はそのまま残す。
    """
    try:
        count = int(target_required_count)
    except (TypeError, ValueError):
        return required_slots
    if count <= 0:
        return required_slots
    # 過大な値による割当インスタンスの大量生成（メモリ・計算資源の枯渇）を防ぐ。
    count = min(count, _MAX_TARGET_REQUIRED_COUNT)

    # 対象日指定が無ければ全日へ適用
    dates = set(target_dates) if target_dates else {d.date for d in days}
    day_by_date = {d.date: d for d in days}
    site_row_id = _coerce_site_row_id(project.get("site_row_id"))

    kept = [
        s for s in required_slots
        if not (s.date in dates and not s.shift_key and not s.site_branch_row_id)
    ]
    for d in sorted(dates):
        day = day_by_date.get(d)
        if day is None:
            continue
        kept.append(
            RequiredSlot(
                slot_id=f"target-{d.isoformat()}",
                date=d,
                day=day.day,
                shift_key="",
                shift_label="",
                required_count=count,
                site_row_id=site_row_id,
                site_id=_str(project.get("site_id")),
                site_name=_str(project.get("site_name")),
                source="target_override",
            )
        )
    warnings.append(
        PlanningWarning(
            "target_required_override",
            f"対象日の必要人数を {count} 名で指定したため、既存の需要設定より優先します",
        )
    )
    return kept


def build_planning_request(
    project: dict[str, Any],
    year: int,
    month: int,
    *,
    plan_overrides: dict[str, Any] | None = None,
    calendar_ids: list[str] | None = None,
    fill_target_days: list[Any] | None = None,
    target_required_count: Any = None,
    request_id: str = "",
) -> tuple[ShiftPlanningRequest, ShiftEngineSettings, list[PlanningWarning]]:
    """CloudShift project から ShiftPlanningRequest を構築する。"""
    warnings: list[PlanningWarning] = []
    settings, setting_warnings = migrate_settings(project.get("shift_engine"))
    warnings.extend(setting_warnings)

    month_key = _month_key(year, month)
    month_data = (project.get("months") or {}).get(month_key) or {}
    base_revision = int(month_data.get("revision") or 1)

    preferences = resolve_preferences(settings, plan_overrides)
    days = build_planning_days(year, month)

    workers = build_workers(project, settings, warnings)
    existing = build_existing_assignments(
        project, month_data, year, month, preferences.existing_policy, warnings
    )
    external, external_leave_days = build_external_assignments(project, year, month, warnings)
    unavailable = build_unavailable_days(
        calendar_ids or [], year, month, settings, preferences.unconfirmed_leave_strength, warnings
    )
    # 対象シフト帳自身に入っている休み予定も hard の不可日として扱う
    # （他帳・他現場・他オーナーの休みと勤務占有は build_external_assignments で集約済み）
    unavailable.extend(leave_days_from_existing(existing))
    unavailable.extend(external_leave_days)
    required_slots, _demand_source = build_required_slots(
        project, month_data, settings, days, year, month, warnings
    )
    fill_target_dates = _coerce_fill_target_dates(fill_target_days, year, month)
    if target_required_count is not None:
        required_slots = _apply_target_required_override(
            required_slots, fill_target_dates, target_required_count, days, project, warnings
        )

    site_row_id = _coerce_site_row_id(project.get("site_row_id"))
    # 固定既存(locked/manual_locked)・pinned の担当者は実在の配置者なので、
    # 候補プールに居なければ補完する（経験者扱い）。これを怠ると engine の
    # validate_result が unknown_worker(hard) を誤検出し、保存できなくなる。
    _ensure_fixed_workers_present(workers, existing, settings, site_row_id)

    site = SiteRef(
        site_row_id=site_row_id,
        site_id=_str(project.get("site_id")),
        site_name=_str(project.get("site_name")),
    )

    request = ShiftPlanningRequest(
        request_id=request_id or f"plan-{project.get('id')}-{month_key}",
        version=REQUEST_VERSION,
        target_project_id=_str(project.get("id")),
        target_site=site,
        year=year,
        month=month,
        base_revision=base_revision,
        days=days,
        required_slots=required_slots,
        workers=workers,
        existing_assignments=existing,
        external_assignments=external,
        unavailable_days=unavailable,
        rules=list(settings.rules),
        preferences=preferences,
        scoring_weights=settings.scoring_weights,
        option_experience_policies=list(settings.option_experience_policies),
        external_occupancy_relax_sites=_relax_sites(settings),
        fill_target_dates=fill_target_dates,
    )
    return request, settings, warnings


def build_demand_source(
    project: dict[str, Any], year: int, month: int
) -> str:
    """context 診断用に、需要の決定ソースだけを返す。"""
    settings, _ = migrate_settings(project.get("shift_engine"))
    month_data = (project.get("months") or {}).get(_month_key(year, month)) or {}
    days = build_planning_days(year, month)
    _slots, source = build_required_slots(project, month_data, settings, days, year, month, [])
    return source
