# CloudShift 自動シフト作成エンジン設計書

作成日: 2026-06-08  
修正日: 2026-06-08

## 目的

CloudShift の現行アシスト機能を、単一日・単一枠の候補者検索から、1か月分のシフト下書きを生成する独立エンジンへ発展させる。

この設計の目的は、勤務不可日、休暇、有休共有ツール、既存シフト、現場条件、専従者、過去実績、公平性を入力として扱い、ユーザーが確認できる品質の下書きを生成すること。最終判断はユーザーが行い、エンジンは正式シフトへ直接反映しない。

初期対象は `scene` モードの現場シフト帳とする。`person`、`master`、`substitute` は参照、固定、重複検出の材料として扱い、月次自動生成の直接対象にはしない。

## 設計修正の要点

前回設計から、実装上あいまいだった点を次のように修正する。

1. `required_capacity` をそのまま最適化の需要にしない。必ず `RequiredSlot` に展開してからエンジンへ渡す。
2. 既存シフトは単なる entry ではなく、`ExistingAssignment` と `lock_policy` に変換して扱う。
3. Phase 1 の Pure Python 版では `optimal` を返さない。最適性を証明できる Solver Backend のみ `optimal` を返せる。
4. 重大な Hard Constraint 違反がある生成結果は、既定では `apply-draft` で拒否する。保存を許す場合も明示的な override と UI 表示を必須にする。
5. `assist` JSON は移行材料に限定し、エンジン設定は `shift_engine` 設定として CloudShift project の extra data 側に持つ。
6. 生成結果は既存 entry 正規化で保持されるフィールドだけへ変換する。任意メタデータを entry に混ぜない。
7. `plan` と `apply-draft` の間で月データが変わった場合は revision mismatch として拒否する。
8. request、result、entry preview、audit summary を分け、UI 表示用データと保存データを混同しない。
9. 生成品質を「充足率」「重大違反ゼロ」「公平性」「説明可能性」で測る。
10. Phase 1 から監査ログ、性能予算、ロールアウト条件を持たせる。

## 現行構造と前提

CloudShift は `CloudShiftProject` と `CloudShiftMonth` を中心に構成されている。

- `CloudShiftProject.mode`
  - `scene`: 現場シフト帳
  - `person`: 個人シフト帳
  - `master`: マスター型
  - `substitute`: 要代務シフト帳
- `CloudShiftProject.assist`
  - 現行アシスト情報を JSON で保持する。
  - `profiles`, `records`, `rules`, `experienced_sites`, `training_sites` などを持つ。
- `CloudShiftProject.extra_data`
  - DB 上は `extra_data` として保存され、project dict では通常キーとして展開される。
  - 新エンジン設定は `project["shift_engine"]` に保存する。
- `CloudShiftMonth.entries_per_day`
  - 正式シフト。
- `CloudShiftMonth.draft_entries_per_day`
  - 下書きシフト。
  - 既存 API で保存、クリア、公開が可能。
- `CloudShiftMonth.required_capacity`
  - 現在は月全体の必要人数として扱われる。
  - エンジンでは後方互換用の初期値にとどめる。
- `CloudShiftMonth.revision`
  - `plan` から `apply-draft` までの競合検出に使う。

現行の自動入力に使いやすい接点は `draft_entries_per_day`。生成結果は正式反映せず、まず下書きへ入れ、ユーザーが確認して公開する。

## 現行アシスト機能の評価

現在のアシストは、単一日・単一枠に対する候補者スコアリングとして動いている。

評価材料:

- SitePlus の専従者マスタ
- 手動登録ルール
- 過去実績
- 候補者プロファイル
- 希望曜日
- NG 曜日
- 時間帯/車両オプションの過去実績
- 他現場との同日重複

弱点:

- 1日単位の候補検索であり、1か月全体の最適化ではない。
- 月末までの連勤、偏り、総勤務数、公平性を見ない。
- 空欄を埋めるワークフローと、エンジンとしての入出力境界がない。
- `assist` JSON は UI と履歴に近く、最適化エンジンの正規データとしては不十分。
- 既存ルールは UI、履歴、同期処理と密結合している。

結論として、既存アシストの内部定義をそのまま拡張しない。CloudShift の保存形式とマスタ情報をアダプタで読み取り、新しいエンジン内部モデルに変換する。

## 基本方針

1. エンジン本体は独立 Python モジュールとして作る。
2. エンジン本体は Flask、SQLAlchemy、CloudShift の既存関数に依存しない。
3. CloudShift、有休共有ツール、Employee、SitePlus からの読み込みはアダプタ層で行う。
4. 生成結果は `draft_entries_per_day` へ保存する。
5. 需要は `RequiredSlot`、人は `Worker`、休暇は `UnavailableDay`、既存配置は `ExistingAssignment` に正規化する。
6. Hard Constraint と Soft Constraint を明確に分ける。
7. 生成結果は必ずエンジン自身の検証を通す。
8. 同じ入力なら同じ出力になるよう、Phase 1 は deterministic に実装する。
9. 既存 `assist` JSON は将来の移行材料として扱い、エンジンの唯一の真実にはしない。
10. エンジン設定は `project["shift_engine"]` に保存する。
11. サーバー上で再現できない下書きは保存しない。
12. 生成理由は人間が読める短文と、機械検証できる構造化データの両方で持つ。
13. 失敗時も「なぜ作れなかったか」を成果物として返す。

## 非目標

- Phase 1 で数学的な最適性を保証しない。
- エンジン本体から DB を読まない。
- エンジン本体から CloudShift entry を保存しない。
- 重大な休暇衝突や重複を黙って保存しない。
- 未登録の資格、車両、担当可能性を名前だけで推測しない。
- `assist` JSON に新しいエンジン設定を追記しない。
- UI だけで検証を完結しない。
- plan 結果を長期保存しない。正式な保存対象は CloudShift 下書きと履歴だけにする。

## 品質目標

初期リリースでは、次を満たすことを成功条件にする。

- 生成結果に `blocker` がない。
- override なしの保存では `hard` がない。
- 既存固定シフトが 100% 維持される。
- 有休共有ツールの `hard` 休暇者が 100% 除外される。
- entry 変換後に `employee_number`、現場リンク、枝番が欠落しない。
- 同じ入力、同じ設定、同じ revision で同じ結果になる。
- 1か月、31日、候補者 100名、需要 200枠程度で、plan API が通常 5秒以内に返る。
- 失敗時も `unfilled_slots` と `violations` が具体的に返る。

品質指標は `ScoreSummary` にまとめる。

```python
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
```

`fill_rate` は `assigned_count / required_count` とする。`fairness_index` と `weekend_balance_index` は初期実装では 0.0 から 1.0 の正規化値とし、1.0 に近いほど良い状態とする。

## 推奨ファイル構成

第一段階:

- `app/services/cloudshift_shift_engine.py`
  - 独立したシフト作成エンジン本体。
  - dataclass、制約評価、候補生成、スコアリング、修復、検証を含む。
- `app/services/cloudshift_shift_context.py`
  - CloudShift、Employee、SitePlus、有休共有ツールから `ShiftPlanningRequest` を作る。
- `app/services/cloudshift_shift_apply.py`
  - `ShiftPlanningResult` を CloudShift entry 形式へ変換し、既存の下書き保存処理へ渡す。

第二段階:

- `app/services/cloudshift_shift_solver.py`
  - `GreedyRepairSolver`, `LocalSearchSolver`, `CpSatSolver` などの backend 分離。
- `app/services/cloudshift_shift_explain.py`
  - UI 表示用の理由、警告、差分要約を整形する。

## エンジン設定

`project["shift_engine"]` に次のような設定を保存する。既存 `assist` JSON には保存しない。

```python
@dataclass(frozen=True)
class ShiftEngineSettings:
    version: int
    demand_rules: list[DemandRule]
    leave_policies: list[LeavePolicy]
    default_preferences: PlanningPreferences
    worker_limits: list[WorkerLimit]
    scoring_weights: ScoringWeights
```

最小構成では `demand_rules` が未設定でも動くようにする。この場合は `CloudShiftMonth.required_capacity` を全営業日に対する同一人数として `RequiredSlot` へ展開する。ただし、この fallback は暫定扱いであり、UI では「月全体の必要人数から自動展開」と明示する。

### 需要設定

`DemandRule` は、現場ごとの必要枠を定義する。

```python
@dataclass(frozen=True)
class DemandRule:
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
    required_qualification_codes: tuple[str, ...] = ()
    required_vehicle_options: tuple[str, ...] = ()
    priority: int = 100
```

ルール解決の優先順位:

1. `include_dates` は曜日条件に関係なく適用する。
2. `exclude_dates` は常に除外する。
3. 祝日を平日扱いするか休日扱いするかは `include_holidays` と `PlanningDay.labels` で決める。
4. 同じ日、同じ shift、同じ枝番に複数ルールが当たった場合は加算する。

`required_count` は 0 以上にする。0 は明示的に不要枠を表すため、fallback より優先する。

### 方針設定

```python
@dataclass(frozen=True)
class PlanningPreferences:
    existing_policy: Literal["lock_all", "lock_manual", "replace_all"]
    allow_partial: bool
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
```

一時的な UI 入力は `PlanningPreferences` に入れる。永続設定と UI 入力が衝突した場合は、`plan` payload に含まれる明示値を優先し、`Explanation` に「一時設定で上書き」と出す。

### スコア重み

```python
@dataclass(frozen=True)
class ScoringWeights:
    dedicated_bonus: int = 500
    experience_bonus: int = 120
    preferred_weekday_bonus: int = 40
    soft_unavailable_penalty: int = 300
    consecutive_day_penalty: int = 80
    monthly_imbalance_penalty: int = 60
    weekend_imbalance_penalty: int = 50
    change_penalty: int = 100
    trainee_penalty: int = 30
```

重みは設定可能にするが、初期 UI では細かく出さない。まずはプリセットで運用する。

- 専従者優先
- 公平性優先
- 経験者優先
- 変更最小
- バランス型

内部ではプリセットを `ScoringWeights` に変換する。

### 設定バージョン

`ShiftEngineSettings.version` は必須とする。設定 schema を変える場合は、古い設定を読み込んだ時点で migration する。

設定 migration の原則:

- 不明なキーは捨てずに `raw_settings` として context API の警告に出す。
- 必須値が欠ける場合は既定値を補完する。
- 補完した設定は自動保存しない。ユーザーが設定画面で保存した時だけ永続化する。

## エンジン入出力

### 入力モデル

`cloudshift_shift_engine.py` 内で、少なくとも次の dataclass を定義する。

```python
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
    unavailable_days: list[UnavailableDay]
    rules: list[Rule]
    preferences: PlanningPreferences
```

重要なのは、CloudShift の `entries_per_day` をそのまま最適化しないこと。いったん「日」「必要枠」「人」「不可日」「既存配置」「制約」へ分解する。

### 日モデル

```python
@dataclass(frozen=True)
class PlanningDay:
    date: date
    day: int
    weekday: int
    is_holiday: bool
    is_weekend: bool
    labels: tuple[str, ...] = ()
```

祝日、特別稼働日、休業日を後から扱えるよう、`labels` を持たせる。

### 需要モデル

```python
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
    source: Literal["settings", "required_capacity_fallback", "existing_entry"] = "settings"
```

`required_count` が 2 以上の場合、エンジン内部では割当単位として `slot_id#1`, `slot_id#2` のように展開してよい。UI と結果では元の `slot_id` に戻して表示する。

需要の作り方:

1. `project["shift_engine"].demand_rules` がある場合は、それを最優先にする。
2. 既存シフトを固定する場合は、既存配置分を `ExistingAssignment` として先に確保する。
3. `demand_rules` がない場合のみ、`required_capacity` から日別の同一需要を作る。
4. 需要がゼロの日は、エンジンが勝手に配置を増やさない。

### 人員モデル

```python
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
    qualification_codes: tuple[str, ...] = ()
    vehicle_options: tuple[str, ...] = ()
    capable_shift_keys: tuple[str, ...] = ()
    preference: WorkerPreference | None = None
    monthly_limit: WorkerMonthlyLimit | None = None
```

`employee_number` がある人はそれを主キーとする。番号がない候補者は Phase 1 では割当対象から外すか、`manual_candidate` として明示的に扱う。重複判定を名前だけに頼ると誤判定が起きるため、番号なし候補は警告対象にする。

### 人員ソースと正規化

`Worker` の情報源は複数あるため、責務を分けて合成する。

| 情報 | 主なソース | 扱い |
|---|---|---|
| 氏名、社員番号、在籍状態 | Employee | 主キーと有効性の基準 |
| 現場、枝番、専従者 | SitePlus / CloudShift project | 専従者優先と現場リンク |
| 希望曜日、NG 曜日 | assist profiles | Soft Constraint / preference |
| 経験、研修 | assist records / experienced_sites / training_sites | スコア材料 |
| 既存勤務数 | CloudShift entries / draft entries | 公平性と変更最小化 |
| 休暇 | leave_mgr | 不可日 |

正規化ルール:

- `employee_number` は前後空白を除去し、空なら割当対象外にする。
- 同じ `employee_number` が複数ソースに出た場合は 1人に統合する。
- Employee 側で退職済み、削除済み、無効扱いの人は `active=False` とし、割当対象から外す。
- 名前だけ一致する候補は同一人物とみなさない。
- `assist` 側に存在するが Employee に存在しない候補は `warning` として返す。

### 既存配置モデル

```python
@dataclass(frozen=True)
class ExistingAssignment:
    assignment_id: str
    date: date
    day: int
    slot_key: str
    shift_key: str
    employee_number: str
    employee_name: str
    source_type: Literal["manual", "scene_sync", "person_sync", "master_sync", "substitute_sync", "engine"]
    entry_id: str
    lock_policy: Literal["locked", "manual_locked", "movable", "replaceable"]
```

UI の「既存シフトの扱い」は次のように変換する。

- すべて固定: 全既存配置を `locked`
- 手入力だけ固定: 手入力を `manual_locked`、同期由来と生成由来を `movable`
- すべて再配置候補: 既存配置を `replaceable`

`locked` と `manual_locked` は、需要を満たす seed として先に投入する。固定配置が Hard Constraint と衝突する場合、エンジンは勝手に動かさず、`blocker` として返す。

### 休暇、不可日モデル

```python
@dataclass(frozen=True)
class UnavailableDay:
    employee_number: str
    date: date
    reason: str
    source: Literal["leave_mgr", "assist_profile", "manual_rule", "person_shift"]
    strength: Literal["hard", "soft", "info"]
    confirmed: bool = True
```

有休共有ツールの `leave_type` は `LeavePolicy` で `strength` へ変換する。

初期設定:

- 有休: `hard`
- 代休: `hard`
- 公休: `hard`
- 慶弔休暇: `hard`
- 介護休暇: `hard`
- リフレッシュ休暇: `hard`
- その他: `soft`

`confirmed_by` が空の休暇は設定で切り替える。

- 厳格: `hard`
- 通常: `soft`
- 参考: `info`

初期値は通常の `soft` とする。未確認休暇を完全無視しない。

### 出力モデル

```python
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
```

`optimal` は `solver_certified=True` の場合だけ使用できる。Phase 1 の `GreedyRepairSolver` は `feasible`、`partial`、`failed` のみ返す。

### 違反モデル

```python
@dataclass(frozen=True)
class Violation:
    code: str
    severity: Literal["blocker", "hard", "warning", "info"]
    message: str
    date: date | None = None
    employee_number: str = ""
    slot_id: str = ""
```

保存判定:

- `blocker`: `apply-draft` 不可
- `hard`: 既定では `apply-draft` 不可。明示 override 時のみ下書き保存可
- `warning`: 保存可、UI 表示必須
- `info`: 保存可

`unfilled_slots` は違反ではなく、`partial` の主因として扱う。ただし UI では目立つように表示する。

## 検証パイプライン

エンジンは生成前、生成中、生成後の 3段階で検証する。

### request 検証

`validate_request(request)` で確認する。

- 対象年月が妥当。
- `days` が対象月の日数と一致する。
- `required_slots` の日付が対象月内に収まる。
- `required_count` が 0 以上。
- `workers.employee_number` が重複しない。
- `existing_assignments` の employee が worker に存在する。
- `unavailable_days` の日付が対象月内に収まる。
- `base_revision` が正の整数。

ここで失敗する場合は `status="failed"` とし、配置処理に入らない。

### seed 検証

固定既存配置を投入した直後に `validate_seed(seed)` を実行する。

- 固定配置同士の同日重複がない。
- 固定配置が `hard` 休暇と衝突しない。
- 固定配置が必須資格、枝番、時間帯条件に違反しない。
- 固定配置だけで需要を超過している場合は、超過を `warning` ではなく `blocker` にする。

seed が `blocker` を持つ場合、エンジンは再配置で解消しない。固定を外すよう UI で促す。

### result 検証

`validate_result(request, result)` は `plan` 後と `apply-draft` 前の両方で実行する。

- assignment が存在しない worker を参照しない。
- assignment が存在しない slot を参照しない。
- `hard` 休暇に配置しない。
- 同日同時間帯の重複がない。
- 固定既存配置を削除、変更していない。
- `required_slots` と `assignments` から `unfilled_slots` が正しく計算されている。
- `ScoreSummary` の件数が実データと一致している。

検証関数は副作用を持たない。CloudShift entry への変換や保存は行わない。

## スコアと説明

スコアは配置のための判断材料であり、保存可否の根拠ではない。保存可否は `Violation.severity` で決める。

`Explanation` は人間向けの説明と、UI 集計向けの分類を持つ。

```python
@dataclass(frozen=True)
class Explanation:
    assignment_id: str
    slot_id: str
    employee_number: str
    summary: str
    factors: list[ScoreFactor]

@dataclass(frozen=True)
class ScoreFactor:
    category: Literal["dedicated", "experience", "preference", "fairness", "holiday", "change", "training", "penalty"]
    label: str
    points: int
    detail: str = ""
```

説明の原則:

- 1配置あたり UI に出す理由は上位 3件まで。
- 減点理由も隠さない。
- 「なぜこの人か」と同時に「なぜ未配置か」を返す。
- Hard Constraint で落ちた候補者は通常表示しないが、デバッグ表示では件数を返せるようにする。

未配置理由の優先順位:

1. 候補者がいない。
2. 候補者はいるが全員 Hard Constraint で除外された。
3. 固定配置や上限により割当余地がない。
4. Soft Constraint を優先した結果、未充足を許可した。

## 制約分類

### Hard Constraints

守れない場合は原則配置しない。

- `hard` 休暇、勤務不可日の配置禁止
- 同日同時間帯の重複禁止
- 同じ現場内での重複禁止
- 無効な従業員の配置禁止
- 無効な現場、枝番、車両枠への配置禁止
- 固定既存シフトの変更禁止
- 必須資格、車両、時間帯条件を満たさない人の配置禁止
- `employee_number` がない候補者の自動配置禁止
- 月次上限を超える配置禁止。ただし設定で `warning` に下げられる

### Soft Constraints

破ってもよいが減点する。

- 専従者優先
- 過去実績者優先
- 研修者の適度な混入
- 希望曜日
- `soft` 休暇、未確認休暇の回避
- 勤務日数の公平性
- 連勤抑制
- 土日祝の偏り抑制
- 同一人物への集中抑制
- 前月、既存シフトからの変更最小化
- 代務者より通常候補を優先
- 手動ルールの優先順位

### 設定化すべき制約

現場ごとに違いが出るものは固定値にしない。

- 1日必要人数
- 曜日別必要人数
- オプション別必要人数
- 枝番別必要人数
- 最大連勤数
- 月内の最大/最小勤務数
- 土日祝の扱い
- 有休種別ごとの不可強度
- 未確認休暇の扱い
- 既存シフトを固定するか、上書き候補にするか
- 専従者をどれだけ強く優先するか
- 公平性と経験者優先の重み

## 生成アルゴリズム

最高品質を目指すなら、最終的には OR-Tools CP-SAT のような制約最適化ソルバーが向いている。ただし現行 `requirements.txt` には OR-Tools がないため、実装段階では次の二段構えにする。

### Phase 1: GreedyRepairSolver

依存追加なしで作る。最適性は保証しないが、現行アシストより実用的な月次下書きを生成する。

手順:

1. 入力正規化
2. `RequiredSlot` を割当単位へ展開
3. 固定既存配置を seed として投入
4. seed の Hard Constraint 違反を検証
5. 各 slot の候補者を Hard Constraint でフィルタ
6. 候補者数が少ない slot から順に並べる
7. Soft Constraint の重みで候補者をスコアリング
8. 決定的な tie-breaker で割当する
9. 連勤、勤務数偏り、土日祝偏りを見て局所修復
10. 交換で改善できる場合だけ入替
11. 未充足枠、違反、警告、理由を出力

tie-breaker:

1. スコア降順
2. 今月割当数昇順
3. 連勤数昇順
4. `employee_number` 昇順

この順序により、同じ入力なら同じ出力になる。

### Phase 2: Solver Backend

エンジンのインターフェースを保ったまま、backend を差し替える。

- `GreedyRepairSolver`
- `LocalSearchSolver`
- `CpSatSolver`

CP-SAT を導入する場合:

- `x[slot_instance, worker]` を 0/1 変数にする。
- Hard Constraint は制約式にする。
- Soft Constraint は目的関数のペナルティにする。
- `status="optimal"` は solver が最適性を証明した場合だけ返す。
- 依存追加は optional backend として扱い、導入できない環境では GreedyRepairSolver へ fallback する。

## 性能と安全弁

Phase 1 の性能目標:

- 31日、候補者 100名、需要 200枠で plan 5秒以内。
- 需要 500枠、候補者 300名を超える場合は context API で警告する。
- plan API は 15秒を超えたら `failed` として中断できるようにする。
- 局所探索は改善が止まったら終了し、最大反復数を持つ。
- 候補者スコアは slot ごとにキャッシュする。

安全弁:

- `max_solver_seconds`
- `max_local_search_iterations`
- `max_candidates_per_slot`
- `max_plan_preview_entries`

これらは `PlanningPreferences` または server default として持つ。UI から通常変更できる必要はない。

大量データ時の劣化方針:

1. Hard Constraint フィルタは必ず実行する。
2. 候補者数が多すぎる場合は、専従者、経験者、希望曜日一致者を優先して候補を絞る。
3. 局所探索を省略してもよい。
4. 省略した最適化は `warnings` に残す。

## CloudShift への反映設計

生成結果は `draft_entries_per_day` に保存する。

推奨フロー:

1. ユーザーが現場シフト帳で「自動作成」を開く。
2. サーバーが context API で対象月、需要設定、カレンダー候補、既存配置、候補者概要を返す。
3. ユーザーが方針と固定ルールを選ぶ。
4. `plan` API が `ShiftPlanningRequest` を構築する。
5. `cloudshift_shift_engine.py` が候補シフトを生成する。
6. UI で下書き、未充足、警告、理由、既存シフトとの差分を表示する。
7. ユーザーが確認する。
8. `apply-draft` API が現在の `revision` と `base_revision` を比較する。
9. revision が一致し、重大違反が許容範囲なら CloudShift entry 形式へ変換する。
10. `draft_entries_per_day` に保存する。
11. ユーザーが必要に応じて修正して公開する。

正式保存ではなく下書き保存にすることで、エンジンのミスを運用で吸収できる。ただし、Hard Constraint 違反の下書きを無警告で保存してはいけない。

### entry 変換ルール

CloudShift entry は `normalize_entries_for_month` で保持されるフィールドだけを使う。

主な利用フィールド:

- `id`
- `value`
- `comment`
- `employee_name`
- `employee_number`
- `site_row_id`
- `site_id`
- `site_name`
- `site_branch_row_id`
- `site_branch`
- `sync_source_type`
- `sync_source_project_id`
- `sync_source_project_title`
- `sync_source_month_key`
- `sync_source_day`
- `sync_source_entry_id`

生成配置は次の方針で変換する。

- `id`: `engine-<request_id>-<slot_instance_id>` のように安定生成する。
- `value`: 既存の `parse_entry_value` / 表示形式と互換にする。
- `employee_name`: `Worker.name`
- `employee_number`: `Worker.employee_number`
- `comment`: 短い生成理由を入れる。詳細理由は `plan` レスポンス側で表示し、entry に大量保存しない。
- `sync_source_type`: 既存同期種別と衝突しない値を導入できるまでは空欄にする。生成由来判定は entry id prefix と下書き保存履歴で扱う。

任意の `engine_meta` のような未知フィールドは entry へ入れない。正規化で失われるため、理由やスコアは `plan` レスポンスと履歴に保持する。

## 有休共有ツール連携

新規アダプタで次を行う。

1. 対象ユーザーがアクセスできるカレンダーを取得する。
2. 対象月の `calendar_id_YYYYMM.json` を読む。
3. `employee_number` をキーに休暇を正規化する。
4. `leave_type` ごとの扱いを `LeavePolicy` に従って決める。
5. `UnavailableDay` としてエンジンへ渡す。

休暇レコードの主な項目:

- `date`
- `name`
- `employee_number`
- `leave_type`
- `deputies`
- `remarks`
- `confirmed_by`
- `sync_source`

注意点:

- `employee_number` がない休暇は自動照合しない。名前一致での Hard Constraint 化は避け、警告として出す。
- `confirmed_by` が空の場合は設定に従う。
- CloudShift から有休共有ツールへ同期された休暇も、逆向き取り込み時は同じ `UnavailableDay` として扱う。

## 既存アシストからの移行

既存 `assist` JSON は廃止前提ではなく、移行材料として使う。

- `profiles` -> `WorkerPreference`
- `records` -> `ExperienceRecord`
- `rules` -> `Rule`
- `experienced_sites` -> `ExperienceRecord`
- `training_sites` -> `TrainingRequirement`

ただし、エンジン内部では別モデルに変換する。既存 JSON を直接参照し続けない。

移行時の注意:

- NG 曜日は `UnavailableDay(strength="hard" or "soft")` ではなく、原則 `WorkerPreference.blocked_weekdays` として扱う。
- 希望曜日は Soft Constraint にする。
- 実績は経験点として使うが、資格や担当可能性の証明にはしない。
- 手動ルールは優先度と有効期間を持つ `Rule` に変換する。

## API 案

```text
GET  /tools/shiftersync/cloudshift/api/project/<project_id>/shift-engine/context
POST /tools/shiftersync/cloudshift/api/project/<project_id>/shift-engine/plan
POST /tools/shiftersync/cloudshift/api/project/<project_id>/shift-engine/apply-draft
```

### context

返すもの:

- 対象 project と month の概要
- `base_revision`
- `shift_engine` 設定
- 有休共有カレンダー候補
- 既存配置の件数と固定候補
- demand fallback の有無
- 候補者数
- 設定不足の警告

### plan

保存せず計算結果だけ返す。

入力:

- 対象年月
- 対象カレンダー
- 既存シフトの扱い
- 方針
- 最大連勤数などの一時設定
- `base_revision`

返すもの:

- `ShiftPlanningResult`
- 下書きプレビュー用 entries
- `request_hash`
- 未充足、警告、理由、差分

### apply-draft

ユーザー確認後に下書きへ反映する。

必須検証:

- ログインユーザーが project を編集できる。
- `project.mode == "scene"`。
- 現在の month revision が `base_revision` と一致する。
- `request_hash` が plan 時の入力と一致する。
- `blocker` がない。
- `hard` がある場合は明示 override がある。
- CloudShift entry へ変換後、再度 `_normalize_entries` 相当の正規化を通しても件数が崩れない。

`apply-draft` はクライアントから返ってきた assignments をそのまま信用しない。受け取った payload を `ShiftPlanningResult` 相当に再構築し、エンジンの `validate_result` を再実行してから entry へ変換する。可能であれば `request_hash` と同じ入力でサーバー側再計算を行い、差分がある場合は保存前に拒否する。

### request_hash

`request_hash` は次の値から作る。

- `target_project_id`
- `year`
- `month`
- `base_revision`
- `required_slots`
- `workers` の employee_number と active 状態
- `existing_assignments`
- `unavailable_days`
- `rules`
- `preferences`
- `settings.version`

JSON 化は key sort し、日付は ISO 形式に統一する。UI 表示用の `explanations`、`warnings`、entry preview は hash に含めない。

### plan セッション

Phase 1 では plan 結果を DB に長期保存しない。ただし `apply-draft` の検証を安定させるため、短命の plan cache を使ってよい。

保存する場合:

- key: `request_hash`
- value: `ShiftPlanningRequest`、`ShiftPlanningResult`、作成者、作成時刻
- TTL: 15分から60分
- 保存先: server memory または `var/` の一時ファイル

cache がない場合でも、同じ入力から再計算できることを前提にする。

### 監査ログ

`apply-draft` 成功時は、既存 CloudShift history に次を残す。

- action: `shift_engine_draft_applied`
- month_key
- actor
- request_hash
- solver_backend
- status
- assigned_count
- unfilled_count
- blocker_count
- hard_violation_count
- warning_count
- override の有無
- changed_existing_count

`plan` のたびに履歴を残す必要はない。保存していない試算まで履歴化するとノイズが増えるため、成功した `apply-draft` と拒否された重大操作だけを残す。

拒否時に履歴を残す条件:

- revision mismatch
- `blocker` による拒否
- override なしの `hard` による拒否
- 権限不足

### 権限

context、plan、apply-draft はすべてログイン必須とする。

- context: project を閲覧できるユーザー。
- plan: project を編集できるユーザー。
- apply-draft: project を編集できるユーザー。

公開編集 URL からの自動作成は初期リリースでは無効にする。公開編集側に解放する場合は、対象 project、対象月、override 可否を明示的に制限する。

## UI 案

CloudShift の現場シフト帳に「自動作成」パネルを追加する。

主な項目:

- 対象月
- 有休共有カレンダー
- 需要設定
  - 月全体の必要人数から展開
  - 曜日別
  - 枝番別
  - オプション別
- 既存シフトの扱い
  - すべて固定
  - 手入力だけ固定
  - すべて再配置候補
- 方針
  - 専従者優先
  - 公平性優先
  - 経験者優先
  - 研修者も入れる
  - 変更最小
- 最大連勤数
- 月内最大/最小勤務数
- 土日祝の偏り抑制
- 未確認休暇の扱い
- 未充足を許可するか

生成後に表示するもの:

- 下書きシフト
- 未配置枠
- 有休/NG 衝突
- 同日重複
- 固定シフト衝突
- 連勤警告
- 勤務日数の偏り
- 既存シフトとの差分
- 配置理由

UI 表示では、`blocker` と `hard` を通常の警告と同じ見た目にしない。保存不可または override 必須であることを明示する。

## 段階導入

### Pilot 0: 設定診断

自動生成は行わず、context API で不足情報を表示する。

- demand rules があるか。
- 候補者が何人いるか。
- 社員番号なし候補がないか。
- 有休カレンダーにアクセスできるか。
- 既存シフトに重複や休暇衝突がないか。

この段階で「自動作成の準備状況」を見せる。

### Pilot 1: 保存しない試算

`plan` のみ有効化する。`apply-draft` は無効。

成功基準:

- `blocker` が出た場合に理由が理解できる。
- 未充足枠の理由が現場担当者に伝わる。
- 既存シフトとの差分が妥当。
- plan が通常 5秒以内に返る。

### Pilot 2: 下書き保存

`apply-draft` を有効化する。ただし override は管理者または限定ユーザーのみ。

成功基準:

- 下書き保存後も正式シフトへ影響しない。
- 公開前の閲覧 URL に下書きが出ない。
- 保存履歴から誰がいつ自動作成したか追える。
- 手入力修正と公開フローが自然に続く。

### Pilot 3: 品質改善

公平性、連勤、土日祝偏り、研修者混入を調整する。

成功基準:

- 手修正量が減る。
- 特定社員への集中が減る。
- 専従者と経験者の配置が現場感覚と合う。
- 未充足が減るか、理由の説明精度が上がる。

## 運用上の注意

- demand rules が未整備の現場では、生成品質より設定診断を優先する。
- 有休共有ツールの社員番号が欠けていると品質が大きく落ちる。
- 「未充足ゼロ」を絶対視しない。無理な配置より、未充足を明示する方が安全。
- override は便利だが、濫用するとエンジンの信頼性が落ちる。履歴で追跡する。
- 生成結果はあくまで下書きであり、公開前確認を省略しない。

## テスト方針

エンジン本体は Flask なしで単体テストする。

必須単体テスト:

- `required_capacity` fallback から `RequiredSlot` を作れる。
- 曜日別、枝番別、オプション別 demand を展開できる。
- `hard` 休暇者を配置しない。
- 未確認休暇を設定どおり `soft` または `hard` に変換する。
- 同日重複を作らない。
- 必要人数を満たす。
- 人員不足時に `unfilled_slots` を出し、`status="partial"` にする。
- 専従者を優先する。
- 既存固定シフトを動かさない。
- 固定既存シフトが休暇と衝突した場合に重大違反を返す。
- 最大連勤を超えた場合に回避または警告する。
- 公平性スコアが偏りを抑える。
- 同じ入力で同じ結果を返す。
- GreedyRepairSolver が `optimal` を返さない。
- `validate_request` が不正な月、重複 worker、不正 slot を拒否する。
- `validate_result` が存在しない worker、存在しない slot、固定配置変更を検出する。
- `request_hash` が key sort と ISO 日付で安定する。
- `ScoreSummary` の件数が assignment と unfilled slots から再計算できる。

必須統合テスト:

- 有休共有ツールの JSON から `UnavailableDay` を作れる。
- CloudShift の `draft_entries_per_day` に保存できる。
- `apply-draft` が revision mismatch を拒否する。
- `blocker` つき結果を保存しない。
- `hard` つき結果は override なしで保存しない。
- 生成結果を CloudShift entry に変換できる。
- entry 変換後に正規化しても `employee_number` が残る。
- 下書き保存後に正式シフトへ影響しない。
- 公開前は閲覧 URL に下書きが出ない。
- 公開後に正式シフトへ反映される。
- 公開編集 URL では初期リリースの自動作成 API が使えない。
- `shift_engine_draft_applied` 履歴が必要な要約を残す。
- plan cache が切れても同じ入力から再計算できる。
- 性能上限を超えた時に `warnings` または `failed` で返る。

## 実装ロードマップ

### Step 1: 設定とモデル

- `project["shift_engine"]` の設定 schema を作る。
- `RequiredSlot`, `Worker`, `ExistingAssignment`, `UnavailableDay`, `Violation` の dataclass を作る。
- エンジン入力/出力の JSON 化を定義する。
- `request_hash` と schema migration を作る。

### Step 2: Context Adapter

- CloudShift month から既存配置を作る。
- `required_capacity` fallback と demand rules から `RequiredSlot` を作る。
- Employee、SitePlus、assist から `Worker` と補助情報を作る。
- 有休共有ツールから `UnavailableDay` を作る。
- 設定診断 API と警告を作る。

### Step 3: 検証基盤

- `validate_request` を作る。
- `validate_seed` を作る。
- `validate_result` を作る。
- `ScoreSummary` と `Explanation` を作る。

### Step 4: 最小生成

- 休暇不可を反映する。
- 必要人数を満たす。
- 重複禁止を守る。
- 固定既存シフトを seed として扱う。
- 未充足を出す。

### Step 5: plan API

- 保存しない試算を返す。
- 下書き preview entries を返す。
- 未充足、警告、理由、差分を返す。

### Step 6: 下書き反映

- `ShiftPlanningResult` を CloudShift entry へ変換する。
- `apply-draft` で revision と重大違反を検証する。
- 既存下書き保存処理へ接続する。
- 履歴に `shift_engine_draft_applied` を残す。

### Step 7: UI

- 自動作成パネルを追加する。
- 需要設定、固定設定、方針設定を表示する。
- 生成結果の差分、理由、警告、未充足を表示する。

### Step 8: 品質向上

- 専従者、経験、希望曜日、NG 曜日を反映する。
- 連勤、公平性、土日祝偏りをスコア化する。
- 入替修復を入れる。

### Step 9: Solver 強化

- Pure Python 版で運用しながら制約を固める。
- 必要に応じて OR-Tools CP-SAT backend を optional として追加する。

## 最重要ポイント

この機能の核は、検証可能なシフト作成エンジンである。

CloudShift の強みは、既に現場、社員、個人シフト、有休共有、下書き保存が存在すること。ここに独立エンジンを接続すれば、ユーザーが判断できる高品質な下書きを作れる。

ただし、月次シフト作成で本当に重要なのは「それらしい配置」ではなく、「どの条件を守り、どの条件を妥協し、どこが未解決なのか」を明確に返すこと。エンジンは完璧な自動決定者ではなく、説明可能で検証可能な下書き生成器として設計する。
