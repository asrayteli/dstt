# CloudShift 自動シフト作成エンジン設計書

作成日: 2026-06-08  
修正日: 2026-06-08（既存コード調査による整合訂正、重複判定・他現場占有の Hard 化、最低基準＝専従/経験、オプション体系、コア既定プロファイルとアルゴリズム強化を反映）  
修正日: 2026-06-09（追補: 対象日指定・休み除外の強化・UI 導線変更・アルゴリズム修正。本文と矛盾する箇所は文末の「2026-06-09 追補」を正とする）

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

## 実装前提の訂正（既存コード調査反映）

本設計を既存コード（`app/models.py`、`app/tools/cloudshift.py`、`app/tools/leave_mgr.py`、`app/tools/shiftersync_format.py`）と突き合わせた結果、データ源の有無に基づいて次を訂正する。これらは Phase 1 の実装可能性に直結するため、以降の各節の記述より優先する。

1. **資格（qualification）はデータ源が存在しない。** 現行コードに資格マスタや社員資格情報は一切ない（`Employee` モデルにも資格項目はない）。よって `required_qualification_codes` / `qualification_codes` は「将来用の予約フィールド」にとどめ、Phase 1 では Hard Constraint にも seed/result 検証にも使わない。空でない資格要求が来た場合は `warning` として返し、配置可否には影響させない。
2. **車両・時間オプションは Hard ではなく Soft。** 既存の `VEHICLE_OPTION_KEYS`（`M/C/O/W/V/N1..N5`）と `SHIFT_TIME_OPTION_KEYS`（`A/P/E/L/TEMP`）はシフト種別ラベルであり、「担当できるか」を表す許可データではない。現行実装ではこれらは過去実績に基づく**適性（aptitude）スコア**として扱われている（`_assist_option_aptitude_*`）。したがって `required_vehicle_options` を満たさない配置を禁止する根拠データはない。これらは Soft Constraint（適性ボーナス／不一致ペナルティ）として扱い、Hard Constraint からは外す。なお `RequiredSlot.required_count` を「オプション種別ごとに何枠必要か」で定義することは需要側の話であり、これは引き続き有効。
3. **`Worker.active` は単一フラグから取れない。** `Employee` に `active` 真偽値はなく、`is_deleted`（Bool）と `retirement_date`（String 型で `"？退職？"` のような非正規値を含む）から導出する。退職判定は文字列を正規化したうえで保守的に行い、確実に退職と判断できる場合のみ `active=False` とする。判定不能な値は `active` を維持しつつ `warning` を出す。
4. **`base_revision` 競合検出は新規実装。** `CloudShiftMonth.revision` は保存時にインクリメントされ `revision_snapshots` も保持されるが、`base_revision` 比較による mismatch 検出は現行 API に存在しない（既存の下書き保存はサーバー内 `_project_lock` で直列化しているだけ）。`apply-draft` での revision mismatch 検出はこの設計で新規に実装する。
5. **fallback は `capacity_enabled` を尊重する。** `required_capacity` は `CloudShiftMonth.capacity_enabled`（Bool）で有効化される。`required_capacity` からの自動展開 fallback は `capacity_enabled=True` のときだけ行い、無効時は需要ゼロとして扱う。
6. **他現場との同日二重配置は Hard で防ぐ。** エンジンは対象シフト帳だけでなく、関連シフト帳（同 owner の他 scene project）で同じ日に確定している配置も考慮する。既存の `_assist_scene_conflict_entries` が示すとおり、このデータは到達可能。context adapter が確定 entries から `external_assignments` を収集してエンジンへ渡し、エンジンは占有 option と配置 option を `is_duplicate_by_rules(same_site=False)` で判定して、重複する候補者を Hard 除外する（例: 佐藤さんが A 現場の 1/1 に入っていれば B 現場の 1/1 では別の人を探す）。エンジン本体の DB 非依存は維持する。

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
  - `CloudShiftMonth.capacity_enabled`（Bool）で有効・無効が切り替わる。
  - エンジンでは後方互換用の初期値にとどめ、fallback 展開は `capacity_enabled=True` のときだけ行う。
- `CloudShiftMonth.revision`
  - `plan` から `apply-draft` までの競合検出に使う。
  - 保存時に増加し、`CloudShiftMonth.revision_snapshots` に履歴が残る。
  - ただし `base_revision` を受け取って mismatch を検出する仕組みは現行 API に無いため、`apply-draft` で新規実装する。

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
5. 需要は `RequiredSlot`、人は `Worker`、休暇は `UnavailableDay`、既存配置は `ExistingAssignment`、他現場占有は `ExternalAssignment` に正規化する。
6. Hard Constraint と Soft Constraint を明確に分ける。
7. 生成結果は必ずエンジン自身の検証を通す。
8. 同じ入力なら同じ出力になるよう、Phase 1 は deterministic に実装する。
9. 既存 `assist` JSON は将来の移行材料として扱い、エンジンの唯一の真実にはしない。
10. エンジン設定は `project["shift_engine"]` に保存する。
11. サーバー上で再現できない下書きは保存しない。
12. 生成理由は人間が読める短文と、機械検証できる構造化データの両方で持つ。
13. 失敗時も「なぜ作れなかったか」を成果物として返す。
14. コアは堅実な基本アルゴリズムに徹し、人間の判断が要る高レベルな調整はすべて現場ごとの任意オプションとして外出しする（「設定オプションの体系」を参照）。コア単体でも成立し、オプションで完成度を上げる二層構成にする。

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

`fill_rate` は `assigned_count / required_count` とする。`required_count` が 0 の場合は 0 除算を避け、`fill_rate=1.0`（需要なし＝完全充足）とする。

件数の定義（取り違え防止）: `required_count` は全 slot の `required_count` の総和（割当単位の総数）。`assigned_count` は確定した割当数。`unfilled_count` は不足ポジション数（Σ`UnfilledSlot.shortage`）であり、`assigned_count + unfilled_count == required_count` を満たす。これは `len(unfilled_slots)`（不足のある slot 数）とは異なる。

`fairness_index` と `weekend_balance_index` は 0.0〜1.0 の正規化値とし、1.0 に近いほど良い。初期実装は次で定義する（決定的・0 除算回避）。

- `fairness_index = 1 - min(1, pstdev(割当のある worker の総割当数) / max(1, mean(同)))`。母集団は「割当が 1 件以上の worker」、`pstdev` は母標準偏差。割当のある worker が 1 人以下なら 1.0。
- `weekend_balance_index = 1 - min(1, pstdev(割当のある worker の土日祝割当数) / max(1, mean(同)))`。土日祝の割当が無ければ 1.0。
- `max_consecutive_days` は全 worker の最大連勤数。割当ゼロなら 0。

これらは指標であり保存可否には影響しない。算出は割当確定後に 1 回だけ行う。

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
    option_experience_policies: list[OptionExperiencePolicy]
    rules: list[Rule]                      # 人物単位の好み・固定/禁止・手動ルール（kind で分岐）
    advanced_options: list[OptionToggle]   # 現場ごとの任意・拡張オプション（フィルタ等。既定は空＝コア挙動）
```

各オプションの保存先:

- 型が確立したもの（需要・休暇・重み・既定方針・上限・オプション経験）は専用フィールドに保存する。
- 人物単位の好みと固定/禁止（`pinned`/`forbidden`/`pair_together`/`pair_avoid`/`worker_weight`/`fixed_weekday`/`manual`）は `rules` に保存し、`enabled` と有効期間で制御する。
- 候補フィルタ（`office_filter`/`employee_type_filter`/`candidate_allowlist`/`candidate_blocklist`）やその他の単純トグルは `advanced_options`（`OptionToggle`）に保存する。
- context adapter は、有効期間内の `rules` と `advanced_options` を解決して `ShiftPlanningRequest`（`rules` と、フィルタ適用済みの `workers`）に反映する。

最小構成では `demand_rules` が未設定でも動くようにする。この場合は `capacity_enabled=True` のときに限り、`CloudShiftMonth.required_capacity` を全営業日に対する同一人数として `RequiredSlot` へ展開する。`capacity_enabled=False` の場合は需要ゼロとして扱い、勝手に配置を増やさない。この fallback は暫定扱いであり、UI では「月全体の必要人数から自動展開」と明示する。

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
    required_qualification_codes: tuple[str, ...] = ()  # 予約フィールド。資格マスタが無いため Phase 1 では Hard 化しない
    required_vehicle_options: tuple[str, ...] = ()       # Soft（過去実績ベースの適性）として扱う。Hard にしない
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
    eligibility_baseline: Literal["dedicated_or_experienced", "any"]  # 配置の最低基準
    min_assignment_score: int | None       # 追加のスコア下限（任意）。これ未満の候補も配置しない
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

`PlanningPreferences` は必ず既定値を持つ。`default_planning_preferences()` は「コア既定プロファイル」と一致する次の値を返し、未指定フィールドはこれで埋める。

```python
def default_planning_preferences() -> PlanningPreferences:
    return PlanningPreferences(
        existing_policy="lock_manual",
        allow_partial=True,
        eligibility_baseline="dedicated_or_experienced",
        min_assignment_score=None,
        unconfirmed_leave_strength="soft",
        max_consecutive_days=None,        # Hard 上限は既定で無し（Soft 抑制のみ）
        min_monthly_assignments=None,
        max_monthly_assignments=None,
        include_trainees=False,
        prefer_dedicated=True,
        prefer_experienced=True,
        prefer_fairness=True,
        minimize_changes=True,
        suppress_weekend_imbalance=True,
    )
```

一時的な UI 入力は `PlanningPreferences` に入れる。永続設定と UI 入力が衝突した場合は、`plan` payload に含まれる明示値を優先し、`Explanation` に「一時設定で上書き」と出す。優先順位は **plan の明示値 > project の `default_preferences` > `default_planning_preferences()`**。

### スコア重み

```python
@dataclass(frozen=True)
class ScoringWeights:
    dedicated_bonus: int = 500
    experience_bonus: int = 120
    option_aptitude_bonus: int = 80  # 車両/時間帯オプションの過去実績適性。資格・許可の代替ではない
    preferred_weekday_bonus: int = 40
    blocked_weekday_penalty: int = 250   # NG 曜日。強めだが Hard ではない（他に回せないなら配置を許す）
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

### オプション経験ポリシー

オプション（車両・時間帯など）の扱いは 2 段構えにする。

1. **オプション一致は加点（既定）。** 候補者がそのオプションを過去に担当した実績があれば、`option_aptitude_bonus` で加点する（`Worker.experienced_option_keys` または既存の `_assist_option_aptitude_*` を使う）。一致しなくても配置は禁止しない。あくまで優先度。
2. **特定オプションだけ「未経験不可」にできる（任意）。** 現場ごとに「このオプションは経験者しか入れない」を設定したい場合に使う。`OptionExperiencePolicy` で対象オプションを `require_prior_experience=True` にすると、そのオプションの枠は当該オプション経験者だけを適格とし、未経験者は Hard で除外する。

```python
@dataclass(frozen=True)
class OptionExperiencePolicy:
    option_key: str                  # 例: "M"(車両), "A"(時間帯)。OPTION_LABELS のキー
    require_prior_experience: bool   # True なら、このオプションは経験者のみ配置可（Hard）
    site_row_id: str = ""            # 空なら全現場、指定で特定現場のみ
    enabled: bool = True
```

- この設定の **UI は CloudShift のアシストのオプション欄から行う**。ただし保存先は `assist` JSON ではなく `project["shift_engine"].option_experience_policies` とする（「assist にエンジン設定を追記しない」原則を守る）。
- ポリシーが無いオプションは従来どおり「一致で加点、不一致でも配置可」。
- `require_prior_experience=True` のオプション枠で経験者が一人もいなければ、その枠は空欄（`unfilled_slot`）にし、未配置理由を「オプション未経験不可で適格者なし」とする。
- オプション経験は `Worker.experienced_option_keys`（assist records の `shift_key` 実績から作る）で判定する。

### 現実性フィルタ（最低基準とスコア下限）

「やったことがある人」を最低基準にする。現実性のない候補を無理に配置するより、対象者なしを明示する。割り当てられる適格候補がいなければ、その枠は空欄（`unfilled_slot`）のままにする。これはエンジンの正常動作。

#### 最低基準（適格性）

`eligibility_baseline="dedicated_or_experienced"`（既定）のとき、次のいずれかを満たす候補だけを配置対象にする。満たさない候補は Hard を通っていても割り当てない。

- 対象現場の**専従者**である（`Worker.dedicated_site_row_ids` に対象 `site_row_id` を含む）。
- 対象現場の**経験者**である（`Worker.experienced_site_row_ids` に対象 `site_row_id` を含む。= 過去に実際に勤務した実績がある）。
- `include_trainees=True` のときに限り、対象現場の**研修対象者**（`Worker.trainee_site_row_ids` に含む）も適格に加える。研修は「やったことがある」には当たらないため、明示的に許可した場合のみ。

`eligibility_baseline="any"` にすると最低基準を外し、Hard を通った全候補を対象にする（従来の挙動）。

最低基準は scoring の `prefer_dedicated` / `prefer_experienced`（優先度=点数）とは別物。優先度は「専従・経験者を上位に並べる」ための加点であり、最低基準は「専従でも経験者でもない人は配置しない」という適格性の足切り。

#### 追加のスコア下限（任意）

`min_assignment_score` を指定すると、最低基準を満たした候補の中でも総合スコアがこれ未満なら配置しない。`None` のときは無効（最低基準だけで判定）。重みに依存する補助設定であり、まずは最低基準（専従/経験）で運用する。

#### 共通の原則

- 足切りで見送った枠は `unfilled_slots` に入れ、未配置理由を「専従・経験者の適格候補がいない」または「スコア下限未満」とする。`status` は `partial` になりうる。
- `allow_partial=False` でも、最低基準・スコア下限による空欄は許可する。無理な配置を強制するより安全。
- 同点・同条件時の決定性は既存 tie-breaker を維持する。足切りは割当の可否判定にのみ使う。

### 設定バージョン

`ShiftEngineSettings.version` は必須とする。設定 schema を変える場合は、古い設定を読み込んだ時点で migration する。

設定 migration の原則:

- 不明なキーは捨てずに `raw_settings` として context API の警告に出す。
- 必須値が欠ける場合は既定値を補完する。
- 補完した設定は自動保存しない。ユーザーが設定画面で保存した時だけ永続化する。

## 設定オプションの体系（現場ごとの任意設定）

### 思想

シフトの作り方は現場の数だけある。そこでエンジンを次の 2 層に分ける。

- **コア（内蔵アルゴリズム）**: どの現場でも成り立つ堅実な基本ロジック。Hard 制約の遵守、需要充足、決定性、検証、下書き保存。オプションを一切設定しなくても、コアだけで安全な下書きが作れる。
- **オプション（現場ごとの任意設定）**: 「人が判断した方がよい」高レベルな部分を、現場ごとに ON/OFF・調整できる任意設定として外出しする。設定するほど現場の癖に寄せられるが、未設定でもコアが成立する。

原則: **少しでも人間の判断が要るところはコアに固定値で埋め込まず、必ずオプションにする。** コアは「正しさ・安全・再現性」を担い、オプションは「現場ごとの好み・運用ルール」を担う。

ただしオプション＝全部 OFF ではない。設定は次の 2 クラスに分ける。

- **コア既定 ON オプション**: ほとんどの現場で妥当な、エンジンが最初から有効にしておく既定値。これにより**何も設定しなくても合理的なシフトが作れる**。ユーザーは必要なら上書きする。
- **純オプション（既定 OFF/中立）**: 現場特有の要望で、明示的に有効化したときだけ効く設定。未設定ならコア挙動を一切変えない。

### コア既定プロファイル（無設定で合理的なシフトを作る）

設定が一切無い現場でも、次の既定値で動く。`build_default_settings()` 相当が常にこのプロファイルを構成し、ユーザー設定はこの上に重ねる。

| 設定 | 既定値（ON） | 狙い |
|---|---|---|
| `eligibility_baseline` | `dedicated_or_experienced` | 「やったことがある人」だけを配置し現実性を担保 |
| `include_trainees` | `False` | 研修者は明示時のみ |
| `leave_policies` | 有休/代休/公休/慶弔/介護/リフレッシュ=`hard`、その他=`soft` | 確定休暇は確実に避ける |
| `unconfirmed_leave_strength` | `soft` | 未確認休暇は避けるが絶対化しない |
| `existing_policy` | `lock_manual`（手入力だけ固定） | 人手で入れた予定は壊さない |
| `minimize_changes` | 弱く有効 | 既存・前月から無駄に動かさない |
| `weekend_balance` | 抑制する | 土日祝の偏りを抑える |
| `fairness`（`prefer_fairness`） | 有効 | 勤務数の偏り・集中を抑える |
| 連勤抑制 | 有効（Soft、`consecutive_day_penalty`） | 連勤が伸びるほど減点。Hard 上限（`max_consecutive_days`）は既定なし |
| `scoring_weights` | バランス型（既定の `ScoringWeights`） | 専従>経験>適性>希望 の標準配分 |
| `allow_partial` | `True` | 無理に埋めず未充足を明示 |
| `external_occupancy` | Hard 除外 | 他現場と同日二重配置を防ぐ |
| `same_site_duplicate` | 禁止（`is_duplicate_by_rules`） | 同一現場の重複を防ぐ |
| 需要 fallback | demand_rules → capacity_fallback →（無ければ）前月パターン推定 | 需要未設定でも妥当な枠を用意（推定は warning 付き） |
| `min_assignment_score` | `None` | 既定では基準（専従/経験）だけで判定 |

これら以外（`office_filter`、`candidate_allowlist`、`pinned_assignments`、`option_experience_policies`、`pair_*` など）は純オプションで既定 OFF。

### オプション・フレームワーク

オプションは個別の場当たり設定にせず、共通の枠組みで管理する。

- すべてのオプションは型付き・検証付きで、`project["shift_engine"]` に保存する（`assist` JSON には保存しない）。
- 各オプションは「コア既定 ON」か「純オプション（既定 OFF/中立）」かを明示する。コア既定 ON は上記プロファイルの値、純オプションは未設定でコア挙動を変えない。
- オプションは**合成可能**にする。互いに矛盾しうる設定は、後述の優先順位で解決し、矛盾は `warning` として返す。
- 各オプションは「分類（Hard 足切り / Soft 加点 / 需要 / 出力 / 運用）」「スコープ（現場全体 / 枝番 / オプションキー / 個人 / 曜日）」「既定値」「データ源」を明示する。
- データ源が現行コードに無いオプションは**予約**として定義だけ置き、Phase 1 では `warning` を返して挙動には反映しない（例: 資格）。
- 設定 schema は `ShiftEngineSettings.version` で版管理し、未知キーは捨てずに `raw_settings` 警告に出す。

共通の値の持ち方（任意オプションの土台）:

```python
@dataclass(frozen=True)
class OptionToggle:
    key: str
    enabled: bool = False          # 既定は無効（コア挙動）
    scope: Literal["site", "branch", "option_key", "worker", "weekday"] = "site"
    target: str = ""               # scope の対象（枝番/オプションキー/employee_number/曜日 など）
    value: Any = None              # 数値・閾値・選択肢など
    note: str = ""
```

型が定まっているオプション（需要・休暇・重みなど）は専用 dataclass を使い、汎用・将来拡張は `advanced_options: list[OptionToggle]` に逃がす。`ShiftEngineSettings` に `advanced_options` を追加し、未知の高度設定でも安全に往復・検証できるようにする。

### オプション・カタログ

現場ごとに設定できる任意オプションを分類して並べる。**既定列が「中立」なものは、設定しなければコア挙動のまま。** 既存実装済みの概念は「実装済み」、データ源が無いものは「予約」と記す。

#### 需要・枠

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `demand_rules` | 曜日別・枝番別・オプション別・日付指定の必要人数（実装済み概念） | fallback のみ | 需要 |
| `capacity_fallback` | `required_capacity` から全営業日へ自動展開するか | `capacity_enabled` 準拠 | 需要 |
| `prev_month_estimate` | demand も capacity も無い時、前月確定の曜日別平均で需要を推定（warning 付き） | コア既定 ON | 需要 |
| `day_overrides` | 特定日の必要人数を増減（`include_dates`/`exclude_dates` 相当） | なし | 需要 |
| `holiday_policy` | 祝日を営業/休業/別需要のどれで扱うか（`JAPAN_HOLIDAYS` 参照） | 営業日扱い | 需要 |
| `special_workday_labels` | 特別稼働日・休業日を `PlanningDay.labels` で指定 | なし | 需要 |
| `per_option_demand` | 車両・時間帯など option ごとの必要数 | なし | 需要 |

#### 適格性（足切り）

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `eligibility_baseline` | 専従/経験者のみ か 全員可（実装済み） | 専従/経験者のみ | Hard 足切り |
| `include_trainees` | 研修対象者も適格に含める（実装済み） | 含めない | Hard 足切り |
| `option_experience_policies` | 特定オプションを未経験不可にする（実装済み） | なし（加点のみ） | Hard 足切り |
| `min_assignment_score` | 追加のスコア下限（実装済み） | なし | Hard 足切り |
| `office_filter` | 特定所属（`Employee.office_code`）のみ候補にする | なし | Hard 足切り |
| `employee_type_filter` | 特定の社員区分（`Employee.employee_type`）に限定 | なし | Hard 足切り |
| `candidate_allowlist` | 明示した社員番号だけを候補にする | なし | Hard 足切り |
| `candidate_blocklist` | 明示した社員番号を候補から外す | なし | Hard 足切り |
| `required_qualification` | 必須資格（**予約**。資格データが無い） | 無効 | 予約 |

#### 休暇

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `leave_policies` | 有休種別ごとの不可強度（実装済み） | 全 `hard`/その他 `soft` | Hard/Soft |
| `unconfirmed_leave_strength` | 未確認休暇の扱い（実装済み） | `soft` | Hard/Soft |
| `leave_calendars` | 参照する有休共有カレンダーの選択 | ユーザー選択 | 入力 |
| `deputy_awareness` | 代務（`deputies`）がある休暇の優先度調整 | 無効 | Soft |

#### 連続性・公平性

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `max_consecutive_days` | 最大連勤数（実装済み） | なし | Soft/Hard 選択 |
| `min_rest_days` | 連勤後に確保する休息日数 | なし | Soft |
| `monthly_min_max` | 月内の最小/最大勤務数（実装済み） | なし | Soft/Hard 選択 |
| `weekly_max` | 週あたり上限 | なし | Soft |
| `consecutive_cross_month` | 連勤を前月末から数えるか（前月末を入力に含める） | 含めない（月内のみ） | Soft |
| `fairness_scope` | 公平性を「この現場だけ」か「関連現場合算」で見るか | この現場だけ | Soft |
| `weekend_balance` | 土日祝の偏り抑制（実装済み） | 抑制する | Soft |
| `fixed_weekday_workers` | 特定曜日を固定担当にする（曜日×人） | なし | Soft/Hard 選択 |

#### 重複・他現場

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `same_site_duplicate` | 同一現場内重複（`is_duplicate_by_rules(same_site=True)`、実装済み） | 禁止（**コア固定・無効化不可**） | Hard |
| `external_occupancy` | 他現場の同日占有を Hard 除外（実装済み） | Hard 除外（コア既定。`external_occupancy_relax` でのみ緩和可） | Hard |
| `external_occupancy_relax` | 他現場占有を現場単位で `warning` に緩める（唯一の緩和手段） | 無効（Hard のまま） | 選択 |

#### 人物単位の好み

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `worker_weight` | 特定人物を優先/抑制（個別加点・減点） | なし | Soft |
| `pair_together` | 一緒に組ませたいペア | なし | Soft |
| `pair_avoid` | 同日同枠に並べたくないペア | なし | Soft |
| `pinned_assignments` | 特定日・特定枠に人を固定配置 | なし | Hard seed |
| `forbidden_assignments` | 特定日・特定枠に人を入れない | なし | Hard |

#### 既存シフト・スコア・出力

| オプション | 内容 | 既定 | 分類 |
|---|---|---|---|
| `existing_policy` | すべて固定/手入力だけ固定/再配置候補（実装済み） | 手入力だけ固定 | 既存 |
| `minimize_changes` | 既存・前月からの変更最小化（実装済み） | 弱く有効 | Soft |
| `scoring_weights` / プリセット | 重み調整（実装済み） | バランス型 | Soft |
| `allow_partial` | 未充足を許可するか（実装済み） | 許可 | 出力 |
| `override_policy` | Hard 違反の override を誰に許すか | 管理者のみ | 運用 |

### 優先順位と安全

オプションが増えても破綻しないよう、評価順を固定する。

1. **不可侵のコア Hard**（`hard` 休暇・同一現場の重複・固定既存シフトの不変更・無効従業員）。オプションで外せない安全装置。
2. **既定 Hard だが明示設定で緩められるもの**（他現場占有。`external_occupancy_relax` を明示設定したときだけ `warning` へ降格できる）。
3. **オプションの Hard**: `pinned` は強制配置として seed に投入（最優先で確保）。`forbidden` は当該枠から除外。適格性（最低基準）・未経験不可・allow/block list・office/type フィルタは候補の足切り。
4. **需要の確定**（demand と fallback、day_overrides、holiday_policy）。
5. **Soft スコアリング**（重み・人物単位の好み・公平性・連続性・オプション一致加点）。
6. **下限・しきい値での足切り**（`min_assignment_score`）。
7. **決定的 tie-breaker** と局所修復。

安全原則:

- どのオプションも**不可侵のコア Hard（上記 1）を緩めない**。緩められるのは「オプション由来の Hard を付けない/外す」方向と、他現場占有（上記 2）を明示設定で warning へ落とす場合だけ。
- 相反するオプション（例: allowlist と blocklist に同一人物）は、より安全側（除外）を採り `warning` を出す。
- オプションを足切りに使った結果の空欄は、コアと同じく `unfilled_slots` に理由付きで返す。無理に埋めない。
- すべてのオプションは出力に影響するため、`request_hash` に effective なオプション値を含め、設定変更後の古い plan を `apply-draft` で拒否する。

### 設定 UI の方針

- **基本パネル**: 多くの現場で使う代表オプション（需要・既存の扱い・方針プリセット・最低基準・未充足許可）だけを出す。
- **詳細設定**: それ以外の豊富なオプションは折りたたみの詳細設定に置き、既定中立のまま隠す。設定したものだけ要約表示する。
- **オプション横断の整合表示**: 設定が空欄を増やす要因（厳しい最低基準・未経験不可・各種フィルタ）は、自動作成パネルで「空欄が増えうる設定」として一覧表示する。
- 一部のオプション（特定オプション未経験不可など）は、関連する既存画面（アシストのオプション欄）から設定し、保存先は `shift_engine` 設定に統一する。

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
    external_assignments: list[ExternalAssignment]
    unavailable_days: list[UnavailableDay]
    rules: list[Rule]
    preferences: PlanningPreferences
    scoring_weights: ScoringWeights
    option_experience_policies: list[OptionExperiencePolicy]
```

エンジンは純粋関数として、この request だけを見て生成する。スコアリングに必要な `scoring_weights`、オプション未経験不可の判定に必要な `option_experience_policies` も request に含める（候補フィルタ `office_filter`/`candidate_*` 等は adapter が `workers` に適用済みのため request には持たせない）。

重要なのは、CloudShift の `entries_per_day` をそのまま最適化しないこと。いったん「日」「必要枠」「人」「不可日」「既存配置」「他シフト帳占有」「制約」へ分解する。

`external_assignments` は、**対象シフト帳以外の関連シフト帳で同じ日に確定している配置**を表す。これにより「他現場で既に勤務している候補者を、対象現場の同じ日に二重配置しない」を実現する（例: 佐藤さんが A 現場の 1/1 に確定で入っていれば、B 現場の 1/1 では候補から除外して別の人を探す）。エンジン本体は DB を読まないため、この占有情報は context adapter が収集して入力として渡す。

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

`weekday` は Python の `date.weekday()` と同じ規約（月=0 … 日=6）で持つ。これは既存の `ASSIST_WEEKDAY_LABELS`（`["月","火",…,"日"]`）と一致するため、`DemandRule.weekdays` や assist の希望/NG 曜日もこの規約に統一する。`is_holiday` は `app/tools/japan_holidays.py` の `JAPAN_HOLIDAYS` を参照して判定する。祝日、特別稼働日、休業日を後から扱えるよう、`labels` を持たせる。

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
    required_qualification_codes: tuple[str, ...] = ()  # 予約フィールド。資格データが無いため Phase 1 では warning のみ
    required_vehicle_options: tuple[str, ...] = ()       # Soft（適性）として扱う。Hard にしない
    time_band: str = ""
    priority: int = 100
    source: Literal["settings", "required_capacity_fallback", "prev_month_estimate", "existing_entry"] = "settings"
```

`required_count` が 2 以上の場合、エンジン内部では割当単位として `slot_id#1`, `slot_id#2` のように展開してよい。UI と結果では元の `slot_id` に戻して表示する。

需要の作り方（上から順に評価し、最初に確定したソースを使う）:

1. `project["shift_engine"].demand_rules` があればそれを使う（最優先、`source="settings"`）。
2. demand_rules が無く `capacity_enabled=True` なら、`required_capacity` を全営業日へ同一人数で展開する（`source="required_capacity_fallback"`）。
3. 1・2 のいずれも無い場合のみ、**前月の確定シフトの曜日別の平均人数**を推定需要として使う（コア既定 ON のフォールバック、`source="prev_month_estimate"`）。平均は曜日ごとに `round half up` で整数化して決定的にする。推定である旨を必ず `warning` で返す。前月の確定データが無ければ需要ゼロ。
4. 固定既存（`locked`/`manual_locked`）と `pinned` は 100% 保持する。各 slot の最終需要は `max(1〜3 で決めた需要, その slot の固定数)` とし、固定が需要を上回る場合は需要を引き上げて固定を保持し `warning` を出す（`blocker` にはしない）。固定分は seed として先に確保し、残り `required_count` を新規割当の対象にする。
5. 固定を織り込んだ後に需要がゼロの日は、エンジンが新規配置を増やさない（固定はそのまま残す）。

この順序により、何も設定していない現場でも「前月の人員規模」を出発点に妥当な下書きが作れる。前月パターンを使いたくない場合は `capacity_fallback` を明示無効化するか demand_rules を設定する。

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
    experienced_site_row_ids: tuple[str, ...] = ()  # 実績のある現場（経験）。最低基準と経験点に使う
    trainee_site_row_ids: tuple[str, ...] = ()       # 研修対象の現場。include_trainees 時のみ適格に含める
    experienced_option_keys: tuple[str, ...] = ()    # 【正】過去に担当した option。一致で加点、未経験不可ポリシー時の適格判定に使う
    qualification_codes: tuple[str, ...] = ()  # 予約フィールド。現行コードに資格データが無い
    vehicle_options: tuple[str, ...] = ()       # 予約（legacy）。option 実績は experienced_option_keys に一本化。エンジンは参照しない
    capable_shift_keys: tuple[str, ...] = ()    # 予約（legacy）。同上。担当可能性の証明にはしない
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
| 経験、研修 | assist records / experienced_sites / training_sites | 最低基準（経験＝`experienced_site_row_ids`、研修＝`trainee_site_row_ids`）とスコア材料 |
| 既存勤務数 | CloudShift entries / draft entries | 公平性と変更最小化 |
| 他現場の同日確定勤務 | 同 owner の他 scene project の確定 entries | 他現場占有（`external_assignments`） |
| 休暇 | leave_mgr | 不可日 |

正規化ルール:

- `employee_number` は前後空白を除去し、空なら割当対象外にする。
- 同じ `employee_number` が複数ソースに出た場合は 1人に統合する。
- 在籍状態は `Employee.is_deleted`（Bool）と `Employee.retirement_date`（String 型で `"？退職？"` 等の非正規値を含む）から導出する。削除済み、または正規化後に退職と確実に判断できる人は `active=False` とし、割当対象から外す。判定不能な `retirement_date` は `active` を維持しつつ `warning` を返す。
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

### 他シフト帳占有モデル

対象シフト帳の外（関連する別 project）で、同じ日に候補者が確定勤務している占有を表す。これを使って他現場との同日二重配置を Hard で防ぐ。

```python
@dataclass(frozen=True)
class ExternalAssignment:
    employee_number: str
    employee_name: str
    date: date
    day: int
    shift_key: str          # option key。素の名前のみなら ""
    project_id: str
    project_title: str
    source_mode: Literal["scene", "person", "substitute", "master"]
    confirmed: bool = True
```

収集と突合のルール:

- context adapter が、既存の `_assist_scene_conflict_entries` と同じ方針で収集する。すなわち**同じ `owner_user_id`** の**他 scene project**の、**対象年月の確定 `entries_per_day`**（下書きは含めない）から、`parse_entry_value` で option と氏名を取り出して作る。`person`／`substitute` モードは段階的に対象へ加える。
- 候補者との突合は `employee_number` 一致を主とする。`employee_number` が無い占有は名前一致のみとなるため Hard 化せず `warning` として扱う（番号なし配置を自動でハード排除しない原則と揃える）。
- 占有 option と配置しようとする option の重複判定は `is_duplicate_by_rules(candidate_option, external_option, same_site=False)` で行う。重複する場合だけ配置禁止にする。素の名前同士（option が無い）や同時間帯は重複扱いとなるため、例の「佐藤さんが A にいるので B では使えない」は満たされる。一方、`is_duplicate_by_rules` が共存可と判定する option 組み合わせ（例: 早番 `A` と遅番 `L`）は二重配置を許す。
- `external_assignments` は出力結果（draft entry）には書き込まない。あくまで候補除外のための入力。

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

### 補助データ構造

本文で参照する残りの型を定義する。すべて `frozen=True` の dataclass。

```python
@dataclass(frozen=True)
class SiteRef:
    site_row_id: str
    site_id: str
    site_name: str

@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    slot_id: str            # 表示用は元 slot_id（#1 等の内部展開は戻す）
    slot_instance_id: str   # 内部の割当単位（slot_id#n）
    date: date
    day: int
    shift_key: str
    employee_number: str
    employee_name: str
    score: int
    source: Literal["engine", "existing_locked", "pinned"]

@dataclass(frozen=True)
class UnfilledSlot:
    slot_id: str
    date: date
    day: int
    shift_key: str
    shortage: int           # 不足数（required_count - assigned）
    reason_code: str        # 未配置理由の優先順位コード
    reason: str

@dataclass(frozen=True)
class PlanningWarning:
    code: str
    message: str
    date: date | None = None
    employee_number: str = ""
    slot_id: str = ""

@dataclass(frozen=True)
class WorkerPreference:
    preferred_weekdays: tuple[int, ...] = ()   # 月=0..日=6
    blocked_weekdays: tuple[int, ...] = ()
    note: str = ""

@dataclass(frozen=True)
class WorkerMonthlyLimit:
    min_assignments: int | None = None
    max_assignments: int | None = None
    prior_month_tail_consecutive: int = 0      # 前月末からの連勤数（consecutive_cross_month 用、既定 0）

@dataclass(frozen=True)
class WorkerLimit:
    employee_number: str
    min_assignments: int | None = None
    max_assignments: int | None = None

@dataclass(frozen=True)
class LeavePolicy:
    leave_type: str
    strength: Literal["hard", "soft", "info"]

@dataclass(frozen=True)
class Rule:
    rule_id: str
    kind: Literal["pinned", "forbidden", "pair_together", "pair_avoid", "worker_weight", "fixed_weekday", "manual"]
    enabled: bool = True
    priority: int = 100
    effective_from: date | None = None
    effective_to: date | None = None
    params: dict[str, Any] = field(default_factory=dict)   # kind ごとのパラメータ
```

`Rule` は人物単位の好み（`pair_*`、`worker_weight`、`pinned`/`forbidden`、`fixed_weekday`）と手動ルールを 1 つの型で表す。`kind` と `params` で内容を分け、`enabled`・`priority`・有効期間で制御する。`WorkerLimit`（settings 側の現場全体・個人別の上限設定）は context adapter が解決して `Worker.monthly_limit`（`WorkerMonthlyLimit`）に落とし込む。

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
- `external_assignments` の日付が対象月内に収まる。
- `base_revision` が正の整数。

ここで失敗する場合は `status="failed"` とし、配置処理に入らない。

### seed 検証

固定配置（既存ロック `locked`/`manual_locked` と `pinned_assignments`）を seed として投入した直後に `validate_seed(seed)` を実行する。

- 固定配置同士が `is_duplicate_by_rules`（同一現場は `same_site=True`）基準で重複しない。
- 固定配置が `hard` 休暇と衝突しない。
- 固定配置が他現場占有（`external_assignments`、`same_site=False`）と衝突しない。
- 固定配置が枝番条件に違反しない。資格・車両・時間帯は許可データが無いため Phase 1 では Hard 検証しない。
- 固定数が設定需要を上回る場合は、需要を固定数まで引き上げて固定を保持し `warning` とする（`blocker` にはしない。固定既存は 100% 維持する）。
- `pinned` が `forbidden` と同一枠で矛盾する場合は `blocker`。

seed が `blocker` を持つ場合、エンジンは再配置で解消しない。固定を外すよう UI で促す。

### result 検証

`validate_result(request, result)` は `plan` 後と `apply-draft` 前の両方で実行する。

- assignment が存在しない worker を参照しない。
- assignment が存在しない slot を参照しない。
- `hard` 休暇に配置しない。
- `is_duplicate_by_rules` 基準で同日重複が無い（同一現場は `same_site=True`）。
- 番号で突合できる `external_assignments` と `is_duplicate_by_rules(same_site=False)` で重複する配置を作っていない。
- 固定配置（既存ロックと `pinned`）を削除・変更していない。`pinned` は必ず配置に含まれている。
- `forbidden_assignments` の枠に当人を配置していない。
- その他の Hard にも違反していない: 無効従業員・無効現場/枝番への配置、月次上限超過、最低基準（`eligibility_baseline`）違反、`OptionExperiencePolicy` の未経験不可枠への未経験者配置。これらも生成時のフィルタ任せにせず、保存前に再判定する。
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
    category: Literal["dedicated", "experience", "aptitude", "preference", "fairness", "holiday", "change", "training", "penalty"]
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
3. 候補者はいるが、専従・経験者の最低基準を満たす適格者がいない（「やったことがある人」がいない）。
4. 候補者はいるが、オプションが「未経験不可」設定で、その経験者がいない。
5. 候補者はいるが、最良候補のスコアが下限（`min_assignment_score`）未満で見送った。
6. 固定配置や上限により割当余地がない。
7. Soft Constraint を優先した結果、未充足を許可した。

## 制約分類

### Hard Constraints

守れない場合は原則配置しない。

- `hard` 休暇、勤務不可日の配置禁止
- 同一現場内の同日重複の禁止。一律の「同日禁止」ではなく、`is_duplicate_by_rules(option1, option2, same_site=True)`（`app/tools/shiftersync_check.py`）で判定する。オプションの組み合わせ次第で同日共存が許容される（例: 時間帯 `A` と `L` は共存可、`A` と `E` は重複。`V` 車両は全車両と重複。`N1..N5` は同番号のみ重複。有休系オプションは重複しない）ため、この関数を重複判定の唯一の正とする。
- 他現場（関連シフト帳）との同日二重配置の禁止。`external_assignments` の占有 option と配置 option を `is_duplicate_by_rules(candidate_option, external_option, same_site=False)` で判定し、重複する候補者をその日その枠から除外する（例: 佐藤さんが A 現場の 1/1 に確定している → B 現場の 1/1 では除外して別の人を探す）。ただし `employee_number` で突合できない占有は名前一致のみのため Hard 化せず `warning` にする。
- 無効な従業員の配置禁止
- 無効な現場、枝番への配置禁止
- 固定既存シフトの変更禁止
- `employee_number` がない候補者の自動配置禁止
- 月次上限を超える配置禁止。ただし設定で `warning` に下げられる
- 「未経験不可」に設定されたオプション枠への、当該オプション未経験者の配置禁止（`OptionExperiencePolicy` が有効な場合のみ。既定は無効＝加点扱い）

資格は許可データが現行コードに無いため Hard Constraint にしない（`warning` 扱い）。車両・時間帯オプションは既定では下記 Soft の適性（加点）として扱い、Hard にはしない。ただし現場ごとに `OptionExperiencePolicy` で「未経験不可」に設定したオプションだけは、上記のとおり経験者限定の Hard になる。

他現場占有を Hard にしても、エンジン本体の DB 非依存は保たれる。占有データは context adapter が収集して `external_assignments` として渡し、エンジンはその入力だけを見る。

### Soft Constraints

破ってもよいが減点する。

- 専従者優先
- 過去実績者優先
- 車両/時間帯オプションの過去実績適性（オプション一致で加点。`Worker.experienced_option_keys` / `_assist_option_aptitude_*` 相当）
- 研修者の適度な混入
- 希望曜日（`preferred_weekdays`）の加点
- NG 曜日（`blocked_weekdays`）の回避（`blocked_weekday_penalty` で強めに減点。ただし Hard ではない＝他に回せないなら配置を許し、空欄を増やしすぎない）
- `soft` 休暇、未確認休暇の回避
- 勤務日数の公平性
- 連勤抑制
- 土日祝の偏り抑制
- 同一人物への集中抑制
- 番号で突合できない他現場占有（名前一致のみ）の回避。Hard にできないため減点と `warning` で扱う
- 前月、既存シフトからの変更最小化
- 代務者より通常候補を優先
- 人物単位の好み（`Rule`: `worker_weight` の加点/減点、`pair_together`/`pair_avoid`、`fixed_weekday`）
- 手動ルールの優先順位

### 設定化すべき制約

現場ごとに違いが出るものは固定値にしない。代表例を挙げる（全体像は「設定オプションの体系」のカタログを正とする）。

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
- 配置の最低基準（`eligibility_baseline`：専従/経験者のみ か 全員可）
- 追加のスコア下限（`min_assignment_score`、任意）
- オプション別の「未経験不可」設定（`OptionExperiencePolicy`。既定は加点のみ）

## 生成アルゴリズム

最高品質を目指すなら、最終的には OR-Tools CP-SAT のような制約最適化ソルバーが向いている。ただし現行 `requirements.txt` には OR-Tools がないため、実装段階では次の二段構えにする。

### Phase 1: GreedyRepairSolver

依存追加なしで作る。最適性は保証しないが、現行アシストより実用的な月次下書きを生成する。

手順:

1. 入力正規化
2. `RequiredSlot` を割当単位（`slot_id#n`）へ展開
3. 固定既存配置（`locked`/`manual_locked`/`pinned`）を seed として投入
4. seed の Hard Constraint 違反を検証（衝突は再配置せず `blocker`）
5. 各 slot の候補者を **Hard Constraint** でフィルタする（候補フィルタ `office_filter`/`employee_type_filter`/`candidate_*` は adapter が `workers` に適用済みなので、ここでは扱わない）。次をこの順で適用:
   - `hard` 休暇・勤務不可
   - 同一現場の重複（`is_duplicate_by_rules(same_site=True)`）
   - 他現場占有（`is_duplicate_by_rules(same_site=False)`。`external_occupancy_relax` 時は除外せず warning）
   - 無効従業員・`employee_number` 無し・`forbidden_assignments`
   - 月次上限到達者、`max_consecutive_days` を Hard 設定にしている場合は連勤上限到達者
6. 最低基準（`eligibility_baseline`）で適格者に絞る。専従でも経験者でもない候補（研修者は `include_trainees` 時のみ可）を除外。枠のオプションが `OptionExperiencePolicy` で「未経験不可」なら当該オプション未経験者も除外
7. slot を処理順に並べる。**並び順は決定的**にする: ①候補者数 昇順（最も制約が強い枠を先に）②`date` 昇順 ③`shift_key` ④`slot_instance_id`
8. 各 slot で候補者を Soft Constraint の重みでスコアリング（slot 単位でキャッシュ）
9. tie-breaker で最良候補を選ぶ。最良候補のスコアが `min_assignment_score` 未満、または適格者がいなければ割り当てず空欄にし、理由コードを残す
10. 局所修復（連勤・勤務数偏り・土日祝偏り・未充足）を行う。修復は**決定的な走査順**（`date` → `slot_instance_id`）で、各反復は「総ペナルティが厳密に減る入替のみ」を採用する。改善が無くなるか `max_local_search_iterations` で停止
11. 入替・追加配置は、入替後も両当事者が全 Hard・最低基準・`min_assignment_score` を満たす場合のみ許可
12. 未充足枠、違反、警告、理由、`ScoreSummary` を出力

tie-breaker（候補選択の決定的順序）:

1. スコア降順
2. 今月割当数 昇順（公平性）
3. 連勤数 昇順
4. `employee_number` 昇順（最終の絶対的決定子）

`employee_number` 昇順が最後に必ず効くため、全段階で順序が一意に定まり、同じ入力・設定なら必ず同じ出力になる。スコア計算・ソートに集合や辞書の反復順へ依存する箇所を作らない（リストとタプルで明示ソートする）。

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

これらは server 側の安全弁設定（`SolverLimits`）として持ち、`plan` 実行時にエンジンへ渡す。`PlanningPreferences` には含めない（UI から通常変更しない運用値のため）。

大量データ時の劣化方針:

1. Hard Constraint フィルタは必ず実行する。
2. 候補者数が多すぎる場合は、専従者、経験者、希望曜日一致者を優先して候補を絞る。
3. 局所探索を省略してもよい。
4. 省略した最適化は `warnings` に残す。

## エッジケースと不変条件

コアを強固にするため、次のエッジケースを明示的に定義し、テストで固定する。どの入力でも例外で落ちず、必ず `ShiftPlanningResult` を返す。

入力エッジケース:

- **候補者ゼロ / 全員不適格**: `assignments` 空、全 slot を `unfilled_slots`、`status="partial"`（需要があるとき）。例外にしない。
- **需要ゼロ**: `assignments` 空、`status="feasible"`、`fill_rate=1.0`。
- **全員 `hard` 休暇**: 全 slot 未充足、理由「候補者はいるが全員 Hard で除外」。
- **seed が Hard 違反**（固定既存同士の重複・固定が `hard` 休暇）: 該当を `blocker`、`status="failed"` または `partial`。エンジンは固定を動かさない。
- **`employee_number` 重複**: `validate_request` で `failed`。
- **同一人物が allowlist と blocklist の両方**: 除外を採用し `warning`。
- **`required_count=0` の枠**: 割当不要。fallback より優先（明示ゼロ）。
- **月初の連勤**: `consecutive_cross_month` が無効なら前月末を数えない（既定）。有効時は `WorkerMonthlyLimit.prior_month_tail_consecutive` を起点にする。
- **短い月 / うるう年**: `days` は `monthrange` 準拠で生成し、`required_slots` の日付は対象月内のみ。
- **前月データ無しで需要フォールバック**: 推定できず需要ゼロ。`warning` で「需要未設定」を示す。

不変条件（result 検証で必ず満たす）:

- 出力の各 assignment は、ある `required_slots` の slot_instance にちょうど 1 対 1 で対応する（重複割当なし）。
- `assigned_count + Σshortage == Σrequired_count`。
- 固定既存（`locked`/`manual_locked`/`pinned`）は入力どおり 100% 残る。
- 同一 worker・同一日で `is_duplicate_by_rules` が重複と判定する配置を作らない。
- 全 `employee_number` は非空。`value` は非空（空 value は正規化で消えるため）。
- 同じ入力・設定・revision なら、`assignments` の順序・内容・`request_hash` が完全一致する。

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

- `id`: `engine-<request_id>-<slot_instance_id>` のように安定生成する。`normalize_entry` は既存 `id` を保持するため、この id は正規化後も残る。
- `value`: 既存の `parse_entry_value` / 表示形式と互換にする。オプション（shift_key）付き配置は `ENTRY_VALUE_PATTERN`（`^!([^!]+)!(.+)$`、すなわち `!オプション!氏名`）に従ってエンコードする。`value` は必ず非空にする。`normalize_entry` は空 `value` の entry を破棄するため、空にすると配置が黙って消えて件数が崩れる。
- `employee_name`: `Worker.name`
- `employee_number`: `Worker.employee_number`
- 現場リンク（`site_row_id`/`site_id`/`site_name`/`site_branch_row_id`/`site_branch`）: `Assignment` は現場項目を持たないため、割当先の `RequiredSlot` から取得して埋める。これにより entry の現場リンク・枝番が欠落しない。
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

既存 `assist` JSON は廃止前提ではなく、移行材料として使う。context adapter が次のとおり**定義済みのエンジン型・Worker フィールドへ直接集約**する（中間の独自型は作らない）。

- `profiles` -> `WorkerPreference`（希望/NG 曜日）
- `records` / `experienced_sites` -> `Worker.experienced_site_row_ids`（経験現場）と `Worker.experienced_option_keys`（経験 option）
- `training_sites` -> `Worker.trainee_site_row_ids`（研修現場）
- `rules` -> `Rule`（手動ルール）

ただし、エンジン内部では assist JSON を直接参照し続けない。アダプタ層で上記へ変換してから渡す。

移行時の注意:

- NG 曜日は `UnavailableDay(strength="hard" or "soft")` ではなく、原則 `WorkerPreference.blocked_weekdays` として扱い、強めの Soft 減点（`blocked_weekday_penalty`）で評価する（Hard 除外にはしない。希望表明であり確定不可ではないため）。
- 希望曜日は `WorkerPreference.preferred_weekdays` として Soft の加点にする。
- 実績は経験点として使うが、資格や担当可能性の証明にはしない。実績の現場（`site_row_id`）は `Worker.experienced_site_row_ids`、実績のオプション（`shift_key`）は `Worker.experienced_option_keys` に集約する。
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
- demand の決定ソース（`settings` / `required_capacity_fallback` / `prev_month_estimate`）と推定なら警告
- 適格候補者数（最低基準を満たす人数）と総候補者数
- 他現場占有の件数
- 空欄が増えうる設定（厳しい最低基準・未経験不可・各種フィルタ）の一覧
- 設定不足の警告（社員番号なし候補、カレンダー未接続、demand 未整備 など）

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
- `workers` の出力に影響する全属性（`employee_number`、`active`、`dedicated_site_row_ids`、`experienced_site_row_ids`、`trainee_site_row_ids`、`experienced_option_keys`、`preference`、`monthly_limit`）
- `existing_assignments`
- `external_assignments`
- `unavailable_days`
- `rules`
- `preferences`
- 出力に影響する設定（effective なオプション値）: `settings.version`、`scoring_weights`、`option_experience_policies`、`advanced_options`、その他カタログ上の有効オプション値

`external_assignments` は他現場の確定状況で出力が変わるため hash に含める。これにより、他現場のシフトが更新された後に古い plan を `apply-draft` で保存しようとした場合に `request_hash` 不一致で拒否できる。同様に、出力に影響するオプション設定（最低基準・オプション経験ポリシー・スコア重み・各種フィルタ・人物単位の好み等）はすべて hash に含め、設定変更後の古い plan を拒否する。JSON 化は key sort し、日付は ISO 形式に統一する。UI 表示用の `explanations`、`warnings`、entry preview は hash に含めない。

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
- 配置の最低基準（専従・経験者のみ / 全員可。既定は専従・経験者のみ）
- 追加のスコア下限（任意。低すぎる候補は入れず空欄にする）
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

「特定オプションを未経験不可にする」設定は、自動作成パネルではなく **CloudShift アシストのオプション設定欄**から行う（オプションごとに「経験者のみ」を切り替える）。保存先は `assist` JSON ではなく `project["shift_engine"].option_experience_policies`。自動作成パネル側では、現在「未経験不可」になっているオプションを読み取り専用で表示し、空欄が増える要因として示す。

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
- 「未充足ゼロ」を絶対視しない。無理な配置より、未充足を明示する方が安全。最低基準（専従・経験者）により「やったことがある人がいない枠」は空欄で返す。経験者が乏しい現場では空欄が増えるため、研修者の投入（`include_trainees`）や最低基準の緩和（`eligibility_baseline="any"`）を現場ごとに判断する。
- override は便利だが、濫用するとエンジンの信頼性が落ちる。履歴で追跡する。
- 連勤数は対象月内の配置から数える。月初の連勤評価は前月末を含めないため、月境界の連勤は Phase 1 では近似となる点に注意する（必要なら前月末日の既存配置を追加入力する）。
- 生成結果はあくまで下書きであり、公開前確認を省略しない。

## テスト方針

エンジン本体は Flask なしで単体テストする。

必須単体テスト:

- **既定設定（コア既定プロファイル）だけで、候補と需要があれば合理的な下書きが生成される。**
- 設定が一切無く前月確定シフトがある場合、前月パターン推定で需要が作られ `prev_month_estimate` の `warning` が付く。
- 前月データも無い場合は需要ゼロで `feasible`、例外を出さない。
- 同じ入力・設定・revision で 2 回実行すると `assignments` と `request_hash` が完全一致する（決定性）。
- 候補者ゼロ・需要ゼロ・全員 hard 休暇でも例外を出さず `ShiftPlanningResult` を返す。
- `required_capacity` fallback から `RequiredSlot` を作れる。
- 曜日別、枝番別、オプション別 demand を展開できる。
- `hard` 休暇者を配置しない。
- 未確認休暇を設定どおり `soft` または `hard` に変換する。
- `is_duplicate_by_rules` 基準で同日重複を作らない（重複する組み合わせは禁止し、共存可の組み合わせは許容する）。
- 他現場で同日に確定勤務している候補者（`external_assignments` で番号一致かつ option が重複）を、その日その枠から除外する。
- 他現場占有でも option が共存可（例: A と L）なら除外しない。
- 番号で突合できない他現場占有は Hard 除外せず `warning` にする。
- 専従でも経験者でもない候補を配置せず、適格者がいない枠は空欄にする（最低基準）。
- `include_trainees=True` のときは研修対象者も適格に含める。
- `eligibility_baseline="any"` のときは最低基準を外し全候補を対象にする。
- オプション一致の候補に `option_aptitude_bonus` で加点する（一致しなくても配置は禁止しない）。
- `OptionExperiencePolicy(require_prior_experience=True)` のオプション枠では当該オプション未経験者を除外し、経験者がいなければ空欄にする。
- ポリシーが無いオプションは未経験者でも配置できる（加点なしで配置可）。
- 最良候補のスコアが `min_assignment_score` 未満の枠は空欄にし、未配置理由を「スコア下限未満」とする。
- 必要人数を満たす。
- 人員不足時に `unfilled_slots` を出し、`status="partial"` にする。
- 専従者を優先する。
- 既存固定シフトを動かさない。
- 固定既存が設定需要を上回る場合でも 100% 保持し、需要を引き上げて `warning` にする（`blocker` にしない）。
- NG 曜日（`blocked_weekdays`）の候補を強く減点するが、他に回せないときは配置する（Hard ではない）。希望曜日一致は加点する。
- 固定既存シフトが休暇と衝突した場合に重大違反を返す。
- 最大連勤を超えた場合に回避または警告する。
- 公平性スコアが偏りを抑える。
- 同じ入力で同じ結果を返す。
- GreedyRepairSolver が `optimal` を返さない。
- `validate_request` が不正な月、重複 worker、不正 slot を拒否する。
- `validate_result` が存在しない worker、存在しない slot、固定配置変更を検出する。
- `request_hash` が key sort と ISO 日付で安定し、オプション値を変えると変化する。
- `ScoreSummary` の件数が assignment と unfilled slots から再計算できる。
- 任意オプションが未設定（既定中立）のときコア挙動と一致する。
- `candidate_allowlist`/`candidate_blocklist`/`office_filter`/`employee_type_filter` が候補集合を正しく絞る。
- 同一人物が allowlist と blocklist に入った場合は除外側を採り `warning` を出す。
- `pinned_assignments` を seed として固定し、`forbidden_assignments` の枠に当人を入れない。
- `pair_avoid` の 2 名を同日同枠に並べない。`pair_together` は可能なら同日に揃える。
- オプションでコアの Hard（休暇・重複・他現場占有・固定既存）を緩められない。

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

- `project["shift_engine"]` の設定 schema（`ShiftEngineSettings` と各サブ dataclass）を作る。
- 入力/出力/補助の dataclass（`PlanningDay`, `RequiredSlot`, `Worker`, `ExistingAssignment`, `ExternalAssignment`, `UnavailableDay`, `Rule`, `Assignment`, `UnfilledSlot`, `Violation`, `PlanningWarning`, `ScoreSummary`, `Explanation`, `SiteRef`, `WorkerPreference`, `WorkerMonthlyLimit`, `WorkerLimit`, `LeavePolicy`, `OptionExperiencePolicy`, `OptionToggle`）を作る。
- `build_default_settings()` / `default_planning_preferences()` でコア既定プロファイルを構成する。
- エンジン入力/出力の JSON 化を定義する。
- `request_hash` と schema migration を作る。

### Step 2: Context Adapter

- CloudShift month から既存配置（`lock_policy` 付き）を作る。
- 需要を作る: demand_rules → `capacity_fallback` → 前月パターン推定（`prev_month_estimate`、warning 付き）の順で `RequiredSlot` を構成する。
- Employee、SitePlus、assist から `Worker` と補助情報（経験現場・経験 option・専従・研修・希望/NG 曜日・上限）を作る。
- 同 owner の他 scene project の確定 entries から `external_assignments`（他現場の同日占有）を作る。`_assist_scene_conflict_entries` の収集ロジックを再利用する。
- 有休共有ツールから `UnavailableDay` を作る。
- 設定の `rules`（有効期間内）と `advanced_options`（フィルタ等）を解決し、`workers` のフィルタ適用と `rules` 反映を行う。
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

目指す姿は二層構成。**コアの基本アルゴリズムは完成度を高く保ち（安全・充足・決定性・検証）、その上に現場ごとの判断を反映する豊富な任意オプションを載せる。** シフトの作り方は人の数だけあるため、高レベルな好みはコアに埋め込まず、すべてオプションとして開放する。設定しなければコアで成立し、設定するほど現場の運用に寄っていく。これにより「エンジン自体の完成度が高く、かつオプションも豊富」を両立する。

## 2026-06-09 追補（実装済みの仕様変更）

本追補は実装に合わせた正式な仕様変更であり、本文の記述と矛盾する場合は本追補を正とする。

### 1. UI 導線の変更（自動作成の入口）

- 「自動作成」ボタンをカレンダー下のアクション群から撤去した。
- 自動作成は、アクションの「アシスト」ボタンを押して開くアシストモーダルの右上から起動する。
- 表示条件は従来どおり: 現場シフト帳（scene）の所有者のみ、対象月があるとき。公開編集 URL では使えない。

### 2. 自動作成の対象日指定（fill_target_dates）

- `ShiftPlanningRequest.fill_target_dates: tuple[date, ...] = ()` を追加した。空なら従来どおり全日が対象。
- plan / apply-draft API の payload に `target_days`（日番号または ISO 日付の配列）を追加した。
  context adapter（`_coerce_fill_target_dates`）が対象月内の date へ正規化する。
- UI はモーダル内のカレンダー形式グリッドで日付を個別 ON/OFF でき、プリセット
  （全日・平日のみ・土日祝のみ・全解除）と曜日チップ（月〜日の一括切替）を備える。
  これにより「平日だけ」「火曜日から土曜日」「特定日だけ」を指定できる。
- **計算は常に 1 か月全体で行う。** 対象日を絞っても、固定既存・休暇・他現場占有・連勤・
  公平性などの判定には月内の全配置が入る（連勤などの法律ルールは月全体に響くため）。
  対象日指定の意味は「新規配置を作ってよい日」の限定だけ。
  - 対象外日の需要は割当単位に展開しない（未充足にも数えない。required_count はスコープ内のみ）。
  - 対象外日の既存 entry は lock_policy に関わらず固定（locked 扱い）として 100% 保持する。
  - 対象外日の固定が hard 休暇等と衝突しても blocker にせず warning として保持する
    （自動作成では直せない既存データのため、生成全体を止めない）。
  - `fill_target_dates` は `request_hash` に含める（対象日を変えた古い plan を apply-draft で拒否）。

### 3. 休みの除外の強化（有休共有ツール・シフト帳の休み）

「休みが入っている日には自動作成がシフトを入れない」を既定で保証する。

- **有休共有ツール**:
  - 既定の `leave_policies` に「その他: hard」を追加し、ポリシー未定義の種別の fallback も
    soft → hard に変更した（全種別が既定 hard）。
  - `unconfirmed_leave_strength` の既定を soft → hard に変更した（未確認の休みも配置しない。
    UI から従来どおり soft / info へ緩められる）。
  - 自動作成パネルの有休カレンダーは既定で全て取り込み対象（チェック済み）にした。
- **シフト帳に入っている休み（有休系オプション entry）**: `is_duplicate_by_rules` は有休系
  オプションを重複扱いしないため、既存配置として持つだけでは同日への新規配置を防げなかった。
  次のとおり hard の不可日（`UnavailableDay(source="shift_entry", strength="hard")`）へ変換する。
  - 対象シフト帳自身の有休系 entry（`leave_days_from_existing`）。当該 entry は existing_policy に
    関わらず locked で 100% 保持する。
  - 同 owner の他 scene シフト帳の有休系 entry（`build_external_assignments` が外部占有と分離して返す）。
  - 同 owner の個人シフト帳（person）の有休系 entry（`build_person_project_leave_days`。
    個人帳は 1 人 1 帳のため project.employee_number の休みとして扱う）。
- **エンジン側の整合**:
  - 有休系オプションの固定 seed は本人の hard 休暇と同日でも衝突（blocker）にしない
    （休みの記録と不可日はむしろ整合している）。`validate_result` も同様に扱う。
  - 有休系オプションの配置は勤務として数えない（連勤・月次勤務数・土日祝カウントに入れない。
    連勤はむしろ休みで分断される）。
  - 固定既存（existing_locked）の氏名は元 entry の表記を保持する（worker 名で上書きしない）。

### 4. アルゴリズムの調査結果と修正

調査の結果、以下を修正・改善した。

1. **連勤判定の双方向化（バグ修正）**: `_consecutive_run_if_placed` が「置く日より前」しか
   数えていなかった。貪欲法は日付順ではなく制約の強い枠から埋めるため、既存連勤の直前に
   置く「橋渡し」配置が連勤上限（Hard）・連勤ペナルティ（Soft）をすり抜けていた。
   前方（後続日）も含めて run 全体を数えるよう修正した。
2. **月次最低勤務数（min_assignments / min_monthly_assignments）の実装**: 設定は存在したが
   どこからも参照されていなかった。Soft 制約として実装した。
   - 最低未達の候補に `monthly_imbalance_penalty × 3 ×不足数` を加点。
   - 公平性（偏り）の算定は「最低保証を超えた分」だけを偏りとして数える
     （明示された最低保証までの配置を『偏り』として罰しない）。局所修復の総コストも同基準。
3. **対象日指定との統合**: 上記 2. の対象外日 ── 固定の保持・warning 化・hash 反映。

調査して妥当と判断し維持した点: Hard/Soft の分離、決定的 tie-breaker
（スコア→割当数→連勤→社員番号）、候補数の少ない枠から埋める順序、未充足を許す設計
（無理な配置より空欄＋理由）、局所修復が「厳密にコストが減る入替のみ」を採用する点、
seed 検証 → 貪欲 → 修復 → result 検証のパイプライン。

## 2026-06-10 追補（説明可能性・診断・探索強化）

「対象日（例: 木曜・金曜）にしたのに配置されない」報告への対応と、
選定理由の可視化、探索アルゴリズムの強化を実装した。

### 1. 「対象日が配置されない」の診断（根本原因の可視化）

調査の結果、対象日が埋まらない典型原因は「候補者がいない」ではなく
**その日の需要（必要人数）が 0** であること。特に需要未設定の現場では
前月実績の曜日別平均（`prev_month_estimate`）が需要になるため、
前月に木金の実績が無い現場では木金の需要が 0 になり、対象日にしても
新規枠が 1 つも作られない。これを次で可視化・解決可能にした。

- **`target_day_no_demand` 警告**: 対象日に新規枠が無い場合、
  「必要人数が 0」か「固定で充足済み」かを区別して警告する。
- **日別サマリ（`build_day_summaries` / plan レスポンスの `day_summary`）**:
  日ごとの 需要・自動配置・固定・休み・未充足・対象内外・需要0 フラグを返し、
  UI の日別結果に表示する。需要 0 の対象日は警告行として明示し、対処方法を案内する。
- **対象日の必要人数の上書き（`target_required_count`）**: 自動作成パネルから
  対象日の「オプション指定なしの必要人数」を直接指定できる。指定時は
  需要設定・前月推定より優先する（`RequiredSlot.source="target_override"`）。
  需要 0 で配置されない日をその場で解決できる。
- **前月推定の精度修正**: 前月実績の人数集計から有休系 entry を除外した
  （休みを勤務人数として数えていた）。

### 2. 説明可能性（なぜこの人か・他の候補は誰か）

- **候補パネル（`ShiftPlanningResult.candidate_panels`）**: エンジン管理の各枠について、
  最終結果の状態で全候補を再評価（当該枠の配置だけ外した counterfactual）し、
  以下を返す。
  - 選ばれた人（selected）とスコア・スコア要因（上位3件）
  - 他の上位候補（最大 5 名）とスコア・要因
  - 除外された人の理由内訳（休暇・他現場勤務・同日重複・専従経験なし・
    月次上限・連勤上限・スコア下限未満 など。`EXCLUSION_LABELS`）
  - 配置可能な候補数（eligible_count）
- **説明の最終状態化**: `explanations` のスコア要因を配置時点ではなく
  最終結果の状態で再計算する（修復後の実態と一致させる）。
- **未充足理由の内訳**: `UnfilledSlot.reason` に除外内訳（例:「休暇2名・他現場1名」）を添える。
- **UI（日別結果ビュー）**: 試算結果に日別の展開行を追加。日ごとの配置チップ
  （自動/固定/休み）、選定理由、候補比較（★=選定・スコア・要因）、除外内訳、
  未充足理由を表示する。静的除外は `_static_exclusion_reason` で理由コード化した。

### 3. 探索アルゴリズムの強化（局所修復の 3 近傍化と高速化）

- 局所修復を 3 種類の近傍に拡張した。いずれも決定的走査で、
  総コストが厳密に減る変更のみ採用する。
  1. **reassign**（従来）: 1 枠の担当の置換・空き枠の充足
  2. **relocate**（新規）: 未充足枠へ他枠の担当を移し、空いた枠を埋め直す。
     貪欲が「その枠でしか使えない人」を先に消費した場合の救済
     （スコア下限・NG 曜日・月次上限が絡むケースで充足率が上がる）
  3. **swap**（新規・2-opt）: 2 枠の担当入替。reassign 単体では直せない
     公平性・連勤・土日祝の偏りを解消する
- **差分コスト評価**: 修復の採否判定を全体コスト再計算から
  「影響を受ける担当者の Soft コスト差分（`_worker_soft_cost`）＋変更数差分
  ＋未充足数差分」へ置き換えた。`_global_cost` は同じ定義の参照実装として残す。
  ベンチマーク（31日・100名・186割当単位）で 5.5 秒 → 1.6 秒。
  品質指標（充足・公平性・決定性）は同一。

## 2026-06-11 追補（代務・研修オプションと経験の自動登録）

目的: ユーザーがシフトの entry に「代務」「研修」オプションを付けるだけで経験が
サーバーへ自動登録され、アシスト・自動作成までの道のりを簡略化する
（手動のアシスト登録 UI は従来どおり維持する）。

### 1. 役割オプション（ShifterSync 共通）

- `SHIFT_OPTION_MAPPINGS` に `SUB`（代務）と `TRAIN`（研修）を追加した
  （`ROLE_OPTION_MAPPINGS` として分類）。ShifterSync / CloudShift の
  オプション選択 UI に「役割」セクションとして表示される。
- 重複判定（`is_duplicate_by_rules`）では代務・研修は**終日の勤務拘束**として扱い、
  休暇系以外のあらゆるオプション（時間帯・車両・素の名前・TEMP・SUB/TRAIN 同士）と
  同日重複になる。休暇系とは従来どおり重複しない。

### 2. 習熟度の定義（ユーザー要件）

- **代務（SUB）= 一人で実際に勤務した状態 = 実績と同格。**
- **研修（TRAIN）= 教わったが一人ではやっていない状態 = 実績より一段低い。**

エンジン側は `Worker.trained_site_row_ids`（研修済み現場）を追加し、
代務由来の現場は `experienced_site_row_ids`（実績）へ統合する。

- 最低基準（eligibility_baseline）: 専従／実績（代務含む）／研修済み が適格。
  要研修（trainee）は従来どおり include_trainees 時のみ。
- スコア: `experience_bonus`（120, 実績・代務） > `trained_site_bonus`（60, 研修済み）。
  重複加点せず上位のみ。`trained_site_row_ids` は `request_hash` に含める。

### 3. 経験の自動登録（アシスト実績への変換）

- `_sync_role_option_experience_for_month`: scene シフト帳の確定 entry から
  SUB/TRAIN を検出し、アシスト実績（records）へ自動登録する。
  - 集約単位は (社員番号, オプション, 月) で 1 実績（点数とデータ量の暴走防止）。
    date はその月の最新該当日、`source_occurrences` に回数を持つ。
  - `source_type="role_option_experience"` の自動実績のみ追加・更新・削除し、
    手動実績や person 連携（person_experience）の実績には一切触れない。
  - entry が消えたら対象月の自動実績だけ解除する。
  - フックは `_save_month_in_project`（月保存・下書き公開が通る）と
    プロジェクト作成直後（CSV 取り込み月対応）。保存は既存の `_save_project`
    1 回に相乗りし、DB 書き込み回数を増やさない。失敗しても保存自体は止めない。
- 自動登録された実績はアシスト UI でバッジ表示（「自動登録（シフトの代務オプション）」等）。
- 加えて context adapter は entry の SUB/TRAIN から経験を**直接導出**する
  （`_role_option_sites_from_entries`）。自動登録前の試算や同期由来の entry でも
  自動作成に反映される（二重化による取りこぼし防止）。

### 4. アシスト検索の点数再配分（役割実績）

役割実績は「現場習熟」を表すため、曜日・オプション一致に依存させず
固定点 + 鮮度（曜日一致と同じ段階）で評価する。

| 種別 | 点数 | 備考 |
|---|---|---|
| 通常実績の完全一致（normal） | 100 + 鮮度(〜30) = 〜130 | 既存どおり |
| **代務実績（SUB）** | **100 + 鮮度(〜15) = 〜115** | 一人で勤務した実績（曜日非依存） |
| 通常実績の曜日一致 | 50 + 鮮度(〜15) = 〜65 | 既存どおり |
| **研修実績（TRAIN）** | **45 + 鮮度(〜15) = 〜60** | 教わった段階（曜日非依存） |
| 通常実績の曜日不一致 | 25 + 鮮度(〜10) = 〜35 | 既存どおり |

365 日より古い役割実績は加点しない。`score_reference` に
`substitute_record` / `training_record` として公開する。

## 2026-06-11 追補その2（代務・研修を「第二オプション」へ再設計）

前項の「役割オプション」を見直し、代務・研修を entry の値とは独立した
**第二オプション**（`second_option`）として扱うようにした。

### 1. データモデル

- entry に `second_option` フィールド（`''` / `SUB` / `TRAIN`）を追加。
  `SHIFT_OPTION_MAPPINGS` から `SUB`/`TRAIN` を外し、`SECOND_OPTION_MAPPINGS`
  に分離（`ROLE_OPTION_MAPPINGS` は同義エイリアスとして互換維持）。
- CSV は `#second_option` 行で保持する。旧形式 `!SUB!名前` / `!TRAIN!名前` は
  `normalize_entry` が読み込み時に `second_option` へ移行し、値は素の名前にする
  （`_split_second_option`、冪等）。`entry_second_option()` は生データでも判定可。

### 2. 重複チェックへの非干渉（要件3）

- 第二オプションは entry の値に入らないため、`is_duplicate_by_rules` /
  `compare_shift_payloads` には影響しない。`is_duplicate_by_rules` から
  代務・研修の終日拘束ルールは撤去した（午前＋代務などの併用が可能）。
- 第二オプションが使われるのは「アシストの経験済み現場・研修要現場・自動作成
  エンジン・ユーザー表示」だけに限定する。

### 3. アシストへの反映（要件4/5）

`_sync_role_option_experience_for_month` を拡張：

- 代務（SUB）→ 対象ユーザーの**実績**（record）＋ アシストの
  **経験済み現場**（`experienced_sites` 自動エントリ）。
- 研修（TRAIN）→ 対象ユーザーの**研修済み現場**（TRAIN record 由来）＋
  **経験済み現場**、かつ同ユーザー×当該現場の**研修要現場**
  （`training_sites`）を一覧から削除する。
- 自動分は `source_type="role_option_experience"` + `source_month_key` で識別し、
  追加・更新・解除する（研修要現場の削除のみ要件どおり手動分も対象）。
- context adapter（`_role_option_sites_from_entries`）も `second_option` を読み、
  代務・研修いずれも experienced に含める。研修済み・経験済みになった現場は
  `build_workers` で研修要（trainee）から除外する。

### 4. UI

- ShifterSync 共通エディタ（`ss_common.js`）のエントリモーダルに
  「第二オプション」セレクト（なし/代務/研修）を追加。カレンダーのチップに
  代務・研修バッジを表示。役割オプションは主オプションのセクションから撤去。
- 個人シフト帳のアシストに **「詳細」タブ**を追加し、経験済み現場・研修要現場を
  まとめて確認できるようにした（要件6）。
