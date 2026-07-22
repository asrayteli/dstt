# CloudShift 大規模シフトモード 設計書

作成日: 2026-07-22
改訂日: 2026-07-22（外部資料に依存しない自己完結版へ全面改訂）
ステータス: 要件定義完了・実装前設計

**本書は単体で実装可能な自己完結の設計書である。** 実装に必要な仕様・計算規則・初期値・検証用の正解データはすべて本書に含まれており、外部ファイル（参考にしたスプレッドシート等）を参照する必要はない。

## 目的

CloudShift のシフト帳モードに、1つのシフト帳で多人数（目安 10〜30人）を扱う **大規模シフトモード（mode=`large`）** を追加する。

多人数のシフト運用では、次のような分断がよく起きる。

1. シフト表（誰がどの勤務に入るか）を作る。
2. 別の台帳へ人ごとの勤務を転記し、拘束時間・労働時間・残業時間・休日出勤時間を集計する。
3. 上限を超えていればシフト表へ戻って作り直す。

シフト表と勤務時間集計が連携していないため、転記・確認・手戻りの往復が発生する。本モードは「行=日付 × 列=メンバー」のグリッド入力と同時に勤務時間・残業・警告をリアルタイム計算し、この往復を1画面で完結させる。

勤務時間計算ロジックは CloudShift から独立した **汎用の勤務時間計算エンジン + DSTT 内 API** として実装し、後で他ツールからも利用できるようにする。

## 位置づけと汎用性の原則

本モードは特定の会社・業種・現場のための専用機能ではなく、**CloudShift の汎用追加機能**である。実装全体を通して次の原則を守る。

1. **固有名詞をコードに埋め込まない。** 勤務コード・休みコード・行き先名などはすべて利用者がプロジェクトごとに登録するマスタで表現する。初期投入するのは業種中立な休みコードのみ。
2. **法規・業種依存のしきい値は設定値にする。** 初期値として自動車運転者向け改善基準告示ベースの値を持つが、すべて設定画面から変更・無効化できる。将来は業種別プリセットの追加で対応する。
3. **UIラベルは業種中立にする。**（「配車」「便」などの語を固定ラベルに使わない）
4. **既存モード（scene/person/master/substitute）の動作を一切変えない。** 追加はモード分岐・新規カラム・新規ファイルで行う。

## 確定要件

要件定義（発注者との質疑）で確定した内容。以降の設計はこの表を正とする。

| ID | 論点 | 決定 |
|---|---|---|
| R-01 | UI | 従来運用の再現ではなく、CloudShift になじむ専用グリッドUIにする |
| R-02 | 休日区分 | 所定休日/法定休日は**セル単位（人×日）でマーク**する。シフトコードや曜日からの自動決定にしない |
| R-03 | コード体系 | **1コード + ダイヤ別時間**。コードに平日/土曜/日祝の時間セットを持たせ、日付から自動で該当時間を引く。「日曜ダイヤを平日に使う」等のため**日単位のダイヤ種別上書き**を設ける |
| R-04 | メンバー管理 | **社員名簿PLUSからの選択 + 自由入力の併用** |
| R-05 | 警告指標 | **改善基準告示の拘束時間チェック**（月間拘束・1日拘束・休息期間）を既定ON。他指標（給与残業・安衛法長時間労働・連続勤務）は計算/表示は常に行い、警告は設定でON可能 |
| R-06 | 休みの区別 | 「シフト作成者が決めた休み（指定休）」と「本人の希望休」を区別して管理する。表現方式は問わないが区別は必須 |
| R-07 | 所定拘束時間 | 既定 9:30 だが人・日によって変わることがある → 既定値 + メンバー別 + セル別の上書き |
| R-08 | 休憩 | 1時間固定でよい（設定値として保持し変更可能） |
| R-09 | 行き先・業務名 | 現場固有の名称（行き先・便名等）は**利用者が登録する勤務コード**として扱い、特別扱いしない（汎用性の原則） |
| R-10 | 日跨ぎ勤務 | 対象外（始業 < 終業 の同日内勤務のみ） |
| R-11 | 計算APIの利用範囲 | **DSTT内のみ**（ログイン必須の内部API。外部公開しない） |
| R-12 | 共有機能 | 閲覧/編集リンク・PWA配布は必要 |
| R-13 | 出力 | xlsx/PDF出力は必要。**大規模モード専用レンダリング方式**とする |
| R-14 | メモ | 日単位のメモ（行事等）とセル単位のコメント（補足事項）で対応 |
| R-15 | 既存データ取込 | 外部ファイル（スプレッドシート等）の取込・移行機能は作らない |
| R-16 | 他モード同期 | 初期リリースでは対象外。**ただし後で必ず追加する** → 同期可能なデータ形を最初から保つ |
| R-17 | 変更ハイライト | 初回に確定したシフト（基準版）から変更されたセルをハイライト表示する |

## 用語

| 用語 | 意味 |
|---|---|
| ダイヤ種別（day_type） | その日にどの時間セットを使うか: `weekday`（平日）/ `saturday`（土曜）/ `holiday`（日祝） |
| 休日区分（holiday_kind） | 人×日のセルに付ける休日マーク: なし / `scheduled`（所定休日）/ `legal`（法定休日） |
| 勤務コード（work code） | 始業終業を持つコード。休日区分マークのある日に入れば休日出勤として計算される |
| 休みコード（leave code） | 勤務しないことを表すコード。時間は常に0。休み種別（leave_kind）と希望フラグ（requested）を持つ |
| 拘束時間 | 終業 − 始業（休憩を含む拘束の長さ）。日次計算の基礎 |
| 労働時間 | 拘束時間 − 休憩 |
| 所定拘束時間 | 日次超過（残業）算定の基準となる1日の拘束時間（既定 9:30） |
| 日次超過 | max(0, 日次拘束時間 − 所定拘束時間)。**労働時間ではなく拘束時間ベース**で算定する |
| 基準版（baseline） | 変更ハイライトの比較元となる、確定時点の月データのスナップショット |

## 全体方針

1. `CloudShiftProject.mode` に `large` を追加し、既存の project / month / history / 共有トークン / PWA 基盤をそのまま使う。
2. 勤務時間計算は **`app/services/worktime_engine.py`（純粋Python、Flask/DB非依存）** に実装し、CloudShift はサービスとして直接呼ぶ。同じエンジンを **`/tools/worktime/api/calculate`（ログイン必須のDSTT内API）** としても公開する（R-11）。
3. マスタ（メンバー・コード・設定）は project の extra_data（`project["large_config"]`）に保存する。既存モードのデータ構造には手を入れない。
4. セルデータは既存の `CloudShiftMonth.entries_per_day` に大規模モード専用のエントリ形式で保存する。正規化は既存 `normalize_entry` を通さず、**大規模モード専用の正規化関数**で行う（保存APIで mode 分岐）。
5. 計算はサーバを正とする。クライアントは同仕様のJSミラーで編集中の即時再計算を行い、保存/集計表示時にサーバ値で確定する。両実装は共通のフィクスチャ（本書「計算検証フィクスチャ」）でテストする。
6. 時間は**分単位の整数**で扱う（浮動小数を使わない）。同一入力→同一出力の決定的実装とする。
7. 将来の他モード同期（R-16）に備え、エントリに `employee_number` / `employee_name` を保持し、コード→時間がマスタから解決できる形を維持する。

## 非目標（初期リリース）

- 外部ファイル取込・移行機能（R-15）
- 他モード（scene/person/master/substitute）との同期反映（R-16。Phase 3 で必ず実装）
- 自動シフト作成エンジン・アシスト・テンプレート・代務要請の大規模モード対応
- 深夜割増・22時以降の割増区分などの給与計算（残業=拘束超過のみ。将来拡張）
- 日跨ぎ勤務（R-10）
- DSTT外部への計算API公開（R-11）
- 有休等の休みを労働時間へ算入する扱い（本仕様では算入しない。将来設定化の余地のみ残す）

## データモデル

### プロジェクト

- `CloudShiftProject.mode = "large"` を追加する。
  - `_sanitize_mode` の許容集合に `large` を追加。
  - `mode_label` 系のマップに `large: "大規模"` を追加。
  - 現場リンク（site_row_id等）は使わない（NULLのまま）。`employee_number` も使わない。
  - assist / shift_engine / templates / substitute 関連UI・APIは mode=large では無効（403 か非表示）。
- 月データの `capacity_enabled` / `required_capacity` は大規模モードでは未使用。

### `project["large_config"]`（extra_data に保存）

```jsonc
{
  "version": 1,
  "members": [
    {
      "id": "mem_xxxxxxxx",            // 安定ID（列の主キー。改名しても履歴が壊れない）
      "display_name": "職員A",          // 列ヘッダ表示名（必須）
      "employee_number": "1234",        // 社員名簿PLUS連携時のみ（自由入力メンバーは空）
      "employee_name": "（正式氏名）",   // 連携時に社員名簿から複写
      "scheduled_bind_minutes": null,   // 所定拘束時間の個人上書き（nullなら settings 値）
      "order": 10,                      // 列順
      "active": true,                   // 退職・離任は false（列非表示、データは保持）
      "note": ""
    }
  ],
  "codes": [
    {
      "key": "A",                       // セルに保存される値（プロジェクト内一意、大文字小文字区別なしで一意）
      "label": "A",                     // 表示名
      "category": "work",               // "work" | "leave"
      "times": {                        // category=work のみ。各ダイヤの時間セット（null=未設定）
        "weekday":  {"start": "07:05", "end": "21:40"},
        "saturday": {"start": "07:30", "end": "19:20"},
        "holiday":  {"start": "08:00", "end": "18:20"}
      },
      "break_minutes": null,            // 休憩の個別上書き（nullなら settings.break_minutes）
      "leave_kind": "",                 // category=leave のみ: "rest"|"substitute_rest"|"paid"|"special"|"other"
      "requested": false,               // category=leave のみ: 本人希望由来か
      "color": "#e3f2fd",               // セル背景色
      "order": 10,
      "active": true,
      "note": ""
    }
  ],
  "settings": {
    "break_minutes": 60,                    // 休憩（既定1時間・R-08）
    "scheduled_bind_minutes": 570,          // 所定拘束時間 9:30（R-07の既定値）
    "checks": {
      "kaizen_monthly_bind": {"enabled": true,  "warn_minutes": 16860},                 // 月間拘束 281h
      "kaizen_daily_bind":   {"enabled": true,  "warn_minutes": 780, "max_minutes": 900}, // 1日拘束 13h/15h
      "kaizen_rest_period":  {"enabled": true,  "min_minutes": 540},                    // 休息期間 9h
      "overtime_monthly":    {"enabled": false, "warn_minutes": 2700},                  // 給与残業 45h
      "anei_long_work":      {"enabled": false, "warn_minutes": 4800},                  // 安衛 80h
      "consecutive_days":    {"enabled": false, "warn_days": 7}                         // 連続勤務
    }
  }
}
```

- **休みコードの初期セット**（プロジェクト作成時に自動投入。すべて編集・削除可能。R-06 の「指定休/希望休の区別」を `requested` フラグで表現する）:

| key | label | leave_kind | requested | 意味 |
|---|---|---|---|---|
| `休` | 休 | rest | false | シフト作成者が決めた休み（指定休） |
| `㊡` | ㊡ | rest | true | 本人の希望休 |
| `振休` | 振休 | substitute_rest | false | 振替休日 |
| `振㊡` | 振㊡ | substitute_rest | true | 希望による振替休日 |
| `有休` | 有休 | paid | false | 年次有給休暇 |
| `有㊡` | 有㊡ | paid | true | 希望日に充てた有給休暇 |
| `特休` | 特休 | special | false | 特別休暇 |

- 勤務コードの初期セットは投入しない（現場ごとに登録する。R-09）。
- **時間可変の勤務コード**を登録できる: `times` の3セットすべて null のコードは「時間はセルで都度入力する」運用を表す（雑務・応援など日によって時間が違う業務向け）。セルの `time_override` が無い日は時間0+警告 `TIME_UNDEFINED` になる。
- バリデーション: members ≤ 60（UI推奨は30まで、31以上で注意表示）、codes ≤ 200、key は空・重複不可、times の start < end（日跨ぎ禁止・R-10）。
- メンバー・コードの削除は原則 `active=false` の無効化。エントリが参照している要素の物理削除は拒否し、無効化を促す。

### 月データ（セル）

`CloudShiftMonth.entries_per_day` を使う。日キー（"1".."31"）→ エントリ配列は既存と同じで、**エントリ形式のみ大規模モード専用**:

```jsonc
{
  "id": "ce_xxxxxxxx",
  "member_id": "mem_xxxxxxxx",          // large_config.members の id（必須）
  "value": "A",                          // コードkey。マスタ未登録の文字列も保存は許可（警告扱い）
  "holiday_kind": "",                    // "" | "scheduled"(所定休日) | "legal"(法定休日)
  "time_override": null,                 // {"start":"08:30","end":"17:30"} 例外日・時間可変コード用
  "bind_override_minutes": null,         // 所定拘束時間のセル上書き（R-07）
  "comment": "",                         // セルコメント（R-14。補足事項）
  "employee_number": "",                 // メンバーから複写（将来同期用・R-16）
  "employee_name": ""
}
```

ルール:

- **1メンバー×1日 = 最大1エントリ**（補足事項はコメントで表現）。保存時に重複 member_id を検出したら後勝ちで統合し警告。
- `value` 空文字 + holiday_kind もコメントも空のエントリは保存時に破棄（空セル）。
- `value` が空でも `holiday_kind` が付いていれば保存する（「休日マークだけの日」を許す。計算上は休み扱い）。
- 「振替出勤」のような扱いは専用コードを設けない。**休日マークを付けない通常勤務 + コメント**（または任意の勤務コード）で表現し、振替の休み側は休みコード（振休等）で表現する。
- 正規化は新設の `normalize_large_entry`（`app/tools/shiftersync_format.py` に追加）で行う。既存 `normalize_entry` の `!OPT!` 形式・代務系フィールドは適用しない。月保存API（PUT month / 公開編集PUT）で `project.mode == "large"` のとき分岐する。
- 下書き（draft_entries_per_day）・リビジョン（revision / revision_snapshots）・履歴（CloudShiftHistory）は既存機構をそのまま使う。

### 月メタデータと基準版（スキーマ追加）

`cloudshift_months` に JSON 列 **`meta_data`** を1本追加する（既存の `_ensure_cloudshift_runtime_schema` の ALTER 方式に倣う。models.py にも列を追加）:

```jsonc
{
  "day_types": {"6": "holiday", "21": "holiday"},   // ダイヤ種別の日単位上書き（R-03。無い日は自動判定）
  "day_notes": {"3": "全体行事", "19": "施設点検"},   // 日メモ（R-14）
  "baseline": {                                      // 基準版（R-17）
    "entries_per_day": { "1": [ /* セルエントリのスナップショット */ ] },
    "set_at": "2026-06-25T18:00:00+09:00",
    "set_by": "user_label",
    "revision": 5                                    // 基準化時点の月 revision
  }
}
```

- `day_types` の自動判定（上書きが無い日）: 月〜金=`weekday`、土=`saturday`、日および `JAPAN_HOLIDAYS`（`app/tools/japan_holidays.py`）の祝日=`holiday`。
- `meta_data` は大規模モード以外では常に `{}`。既存モードの動作に影響を与えない。
- 基準版は月単位。`entries_per_day` と同型のスナップショットを保持する（30人×31日でも数百KB程度でJSON列に収まる。revision_snapshots の剪定と独立させるため専用フィールドとする）。

## 勤務時間計算エンジン（worktime engine）

### 配置とレイヤ

```
app/services/worktime_engine.py   … 純粋計算エンジン（dataclass・Flask/DB非依存）
app/tools/worktime.py             … 汎用API blueprint（/tools/worktime、login_required）
app/tools/cloudshift.py           … 大規模モードの月計算で engine をサービスとして直接呼ぶ
app/static/cloudshift/js/cloudshift_large_calc.js … 同仕様のJSミラー（編集中の即時計算用）
```

- CloudShift → engine は Python 呼び出し（HTTP を経由しない）。他ツールは当面 HTTP API（または直接 import）で利用する。

### 入出力モデル（dataclass スケッチ）

```python
@dataclass(frozen=True)
class TimeRange:
    start_minutes: int   # 0..1439（例 07:05 → 425）
    end_minutes: int     # start < end（日跨ぎ禁止）

@dataclass(frozen=True)
class WorkCode:
    key: str
    category: Literal["work", "leave"]
    times: Mapping[str, TimeRange | None]   # "weekday"/"saturday"/"holiday" → 時間 or None
    break_minutes: int | None = None        # None = settings 値
    leave_kind: str = ""                    # leave のみ
    requested: bool = False                 # leave のみ

@dataclass(frozen=True)
class DayInput:
    day: int                                 # 1..31
    day_type: Literal["weekday", "saturday", "holiday"]  # ダイヤ種別（上書き解決済み）
    code_key: str = ""                        # 空=空セル
    holiday_kind: Literal["", "scheduled", "legal"] = ""
    time_override: TimeRange | None = None
    bind_override_minutes: int | None = None

@dataclass(frozen=True)
class PersonInput:
    person_id: str
    label: str
    scheduled_bind_minutes: int | None       # None = settings 値
    days: tuple[DayInput, ...]
    prev_day_end_minutes: int | None = None  # 前月末日の終業（休息期間の月跨ぎ判定用。不明なら None）

@dataclass(frozen=True)
class CheckSettings:
    kaizen_monthly_bind_enabled: bool = True
    kaizen_monthly_bind_warn_minutes: int = 16860
    kaizen_daily_bind_enabled: bool = True
    kaizen_daily_bind_warn_minutes: int = 780
    kaizen_daily_bind_max_minutes: int = 900
    kaizen_rest_period_enabled: bool = True
    kaizen_rest_period_min_minutes: int = 540
    overtime_monthly_enabled: bool = False
    overtime_monthly_warn_minutes: int = 2700
    anei_long_work_enabled: bool = False
    anei_long_work_warn_minutes: int = 4800
    consecutive_days_enabled: bool = False
    consecutive_days_warn_days: int = 7

@dataclass(frozen=True)
class WorktimeSettings:
    break_minutes: int = 60
    scheduled_bind_minutes: int = 570
    checks: CheckSettings = CheckSettings()

@dataclass(frozen=True)
class WorktimeRequest:
    version: int          # 1
    year: int
    month: int
    codes: tuple[WorkCode, ...]
    settings: WorktimeSettings
    people: tuple[PersonInput, ...]
```

出力:

```python
@dataclass(frozen=True)
class DayResult:
    day: int
    category: Literal["work", "scheduled_holiday_work", "legal_holiday_work", "leave", "empty"]
    code_key: str
    leave_kind: str            # category=leave のとき
    requested: bool
    start_minutes: int | None
    end_minutes: int | None
    bind_minutes: int          # 拘束
    break_minutes: int         # 休憩
    work_minutes: int          # 労働
    overtime_minutes: int      # 日次超過（category=work のみ計上）
    warnings: tuple[str, ...]  # "TIME_UNDEFINED" 等のコード

@dataclass(frozen=True)
class PersonMonthTotals:
    calendar_days: int
    work_days: int                       # work + 休出 の日数
    bind_total_minutes: int              # 拘束時間計（休出含む）
    break_total_minutes: int
    work_total_minutes: int              # 労働時間計（休出含む）
    payroll_overtime_minutes: int        # 給与残業計 = Σ通常出勤日の日次超過（=長時間労働計(労基)）
    scheduled_holiday_work_minutes: int  # 給与所定休出計（労働時間）
    legal_holiday_work_minutes: int      # 給与法定休出計（労働時間）
    payroll_excess_total_minutes: int    # 超過計 = 上3行の合計
    anei_base_minutes: int               # 月間基準時間（下表）
    anei_excess_minutes: int             # 長時間労働計(安衛) = max(0, 労働時間計 − 基準)
    leave_counts: Mapping[str, int]      # leave_kind × requested 別の日数
    max_consecutive_work_days: int

@dataclass(frozen=True)
class WorktimeViolation:
    code: str                            # 下記チェック一覧のコード
    severity: Literal["violation", "warning", "info"]
    person_id: str
    day: int | None                      # 月次チェックは None
    message: str                         # 人が読める短文（値としきい値を含める）
    value_minutes: int
    threshold_minutes: int

@dataclass(frozen=True)
class PersonMonthResult:
    person_id: str
    label: str
    days: tuple[DayResult, ...]
    totals: PersonMonthTotals
    violations: tuple[WorktimeViolation, ...]

@dataclass(frozen=True)
class WorktimeResult:
    engine_version: str
    year: int
    month: int
    people: tuple[PersonMonthResult, ...]
    violations: tuple[WorktimeViolation, ...]   # 全人分を平坦化した一覧（UI警告パネル用）
```

### 日次計算仕様

セル（DayInput）ごとに次の順で確定する。

1. **コード解決**: `code_key` 空 → `empty`。マスタに無いキー → `empty` 扱い + 警告 `CODE_UNDEFINED`（保存は許すが時間0）。`category="leave"` → `leave`（時間すべて0、leave_kind/requested を結果へ）。
2. **時間解決**（category=work）: 優先順位は `time_override` → `times[day_type]` → `times["weekday"]`（フォールバック時は警告 `TIME_SET_FALLBACK`）。どれも無ければ時間0 + 警告 `TIME_UNDEFINED`。
3. **拘束時間** `bind = end − start`。
4. **休憩** `break = code.break_minutes ?? settings.break_minutes`、ただし `bind == 0` なら 0、`break > bind` なら `bind` に丸める（労働時間を負にしない）。
5. **労働時間** `work = bind − break`。
6. **区分**: `holiday_kind == ""` → `work`（通常出勤）。`"scheduled"` → `scheduled_holiday_work`、`"legal"` → `legal_holiday_work`。休みコード＋holiday_kind 付きは `leave`（その日が休日でもある、のマークとして許容）。**曜日は区分に影響しない**（日曜でもマーク無しなら通常出勤。休日出勤か否かは holiday_kind のみで決まる。R-02）。
7. **日次超過**（category=work のみ）: `scheduled_bind = bind_override ?? person.scheduled_bind_minutes ?? settings.scheduled_bind_minutes`、`overtime = max(0, bind − scheduled_bind)`。休出（scheduled/legal）は超過を計上しない（労働時間全体を休出計へ）。

### 月次集計仕様

- `bind_total` / `work_total` は通常出勤＋所定休出＋法定休出のすべてを含む。
- `payroll_overtime`（給与残業計）= 通常出勤日の `overtime` 合計。**長時間労働計(労基) はこれと同値として扱う**。
- `scheduled_holiday_work` / `legal_holiday_work` = 各休出日の `work`（労働時間）合計。
- `payroll_excess_total`（超過計）= 給与残業計 + 所定休出計 + 法定休出計。
- `anei_base_minutes`（安衛法の長時間労働算定に使う月間基準時間）: 暦日数から自動決定。

| 暦日数 | 基準時間 |
|---|---|
| 28日 | 160:00 (9600分) |
| 29日 | 165:00 (9900分) |
| 30日 | 171:00 (10260分) |
| 31日 | 177:00 (10620分) |

- `anei_excess = max(0, work_total − anei_base)`（0未満は0とする）。
- `leave_counts` は leave_kind × requested の組み合わせで日数集計（指定休/希望休/振休/有休等の管理・R-06）。キー例: `"rest"`, `"rest_requested"`, `"paid"`, `"paid_requested"`, `"substitute_rest"`, `"special"`, `"other"`, `"empty"`（空セル=休み扱いの日数）。
- `work_days` = category が work/休出 の日数。`calendar_days` = 月の暦日数。
- `max_consecutive_work_days` = work/休出 が連続した最大日数（月内。月初は `prev_day_end_minutes` の有無に関係なく月内のみで数える）。

### チェック（警告）仕様

`WorktimeViolation.code` の一覧。しきい値はすべて `settings.checks` で変更・有効無効化できる（R-05: 既定は改善基準3種のみON）。

| code | 既定 | 対象 | 内容 | severity |
|---|---|---|---|---|
| `KAIZEN_MONTHLY_BIND` | ON | 月次 | 月間拘束時間 > 281:00 | warning |
| `KAIZEN_DAILY_BIND` | ON | 日次 | 1日拘束 > 13:00（休出日にも適用） | warning |
| `KAIZEN_DAILY_BIND_MAX` | ON | 日次 | 1日拘束 > 15:00（上限超過） | violation |
| `KAIZEN_REST_PERIOD` | ON | 日次 | 休息期間 < 9:00。**直前の勤務日**の終業〜当日の始業の間隔 `(1440 − prev_end) + start` で判定（間に休みを挟む場合は判定しない）。月初日は `prev_day_end_minutes` が与えられた場合のみ判定 | violation |
| `OVERTIME_MONTHLY` | OFF | 月次 | 給与残業計 > 45:00 | warning |
| `ANEI_LONG_WORK` | OFF | 月次 | 長時間労働(安衛) > 80:00 | warning |
| `CONSECUTIVE_DAYS` | OFF | 月次 | 連続勤務日数 > 7日 | warning |
| `TIME_UNDEFINED` / `TIME_SET_FALLBACK` / `CODE_UNDEFINED` | 常時 | 日次 | データ不備（時間未設定・ダイヤ時間未登録・未登録コード） | info |

- 改善基準の既定値（281h/13h/15h/9h）は自動車運転者向け改善基準告示（2024年改正・バス）に基づく**初期値**。汎用機能として数値はすべて設定変更可能とし、UIに根拠の注記を表示する。将来、業種別プリセット（トラック/タクシー等）を追加できる構造にする（R-05・汎用性の原則2）。
- severity の扱い: `violation` / `warning` とも保存は妨げない（作成途中の状態を許す）。UIで強調表示し続けることで是正を促す。

## API設計

### 汎用 勤務時間計算API（DSTT内・R-11）

新規 blueprint `worktime_bp`（`app/tools/worktime.py`、url_prefix `/tools/worktime`）。`@login_required` のみ（外部公開・トークン認証はしない）。ナビゲーションには載せない（画面を持たないAPI専用ツール。将来UIを付ける場合に追加）。

```
POST /tools/worktime/api/calculate
```

リクエスト（WorktimeRequest のJSON表現。時刻は "HH:MM" 文字列で受け、サーバで分に変換）:

```jsonc
{
  "version": 1,
  "year": 2026, "month": 7,
  "codes": [
    {"key": "A", "category": "work",
     "times": {"weekday": {"start": "07:05", "end": "21:40"}, "saturday": null, "holiday": null},
     "break_minutes": null}
  ],
  "settings": {"break_minutes": 60, "scheduled_bind_minutes": 570, "checks": { /* 省略可・既定値 */ }},
  "people": [
    {"person_id": "p1", "label": "職員A",
     "scheduled_bind_minutes": null,
     "prev_day_end": null,               // "21:40" 形式（前月末日の終業）
     "days": [
       {"day": 1, "day_type": "weekday", "code": "A", "holiday_kind": "",
        "time_override": null, "bind_override_minutes": null}
     ]}
  ]
}
```

レスポンス: `{"success": true, "result": WorktimeResult のJSON}`。時刻・時間は分（整数）と "HH:MM" 表記の両方を返す（表示側の変換を不要にする）。

- バリデーションエラーは 400 + `{"success": false, "error": "..."}`（既存 CloudShiftError と同じ流儀）。
- ステートレス（DB読み書きなし）。人数×日数の上限は 200人×31日（それ以上は400）。
- `engine_version` をレスポンスに含め、計算仕様の変更を追跡可能にする。

### 大規模モード用エンドポイント（cloudshift_bp 内）

| メソッド/パス | 内容 |
|---|---|
| `POST /api/create` | 既存。`mode: "large"` を受理（title のみ必須。現場リンク・社員番号は不可）。作成時に休みコード初期セットを `large_config` に投入 |
| `GET/PUT /api/project/<id>/large-config` | マスタ（members/codes/settings）の取得・全置換保存。PUT はバリデーション + 履歴記録（action=`large_config_update`）。編集権限（owner/editor）必須 |
| `PUT /api/project/<id>/month/<y>/<m>` | 既存の月保存を拡張。mode=large のとき `entries_per_day` を `normalize_large_entry` で正規化し、`meta`（`day_types`/`day_notes`）を `meta_data` へ保存。revision/履歴は既存どおり |
| project 取得系（既存） | mode=large では月ペイロードに `meta_data`（day_types/day_notes/baseline有無・set_at）を含める |
| `GET /api/project/<id>/month/<y>/<m>/worktime` | その月の計算結果（WorktimeResult 相当）。サーバが large_config + entries + meta から WorktimeRequest を組み立てて engine を呼ぶ。集計タブ・個人明細・保存後の確定表示に使う |
| `POST /api/project/<id>/month/<y>/<m>/baseline` | 基準版の設定（現在の確定 entries をスナップショット）/ `{"clear": true}` で解除。編集権限必須。履歴記録 |
| 公開系 `GET /api/public/<view|edit|pwa>/<token>` | 既存。mode=large の月ペイロード＋large_config（閲覧に必要な members/codes/settings）+ baseline を返す |
| 公開編集 `PUT /api/public/edit/<token>/month/<y>/<m>` | 既存拡張（mode分岐は月保存と共通実装） |
| 公開系 `GET /api/public/<type>/<token>/worktime/<y>/<m>` | 閲覧/編集/PWAリンクからの計算結果取得（集計・警告表示のため） |
| `GET /api/project/<id>/export/<format>` ほか公開export | mode=large のとき専用レンダラへ分岐（後述） |

- worktime 計算をセル編集のたびにサーバへ投げない。編集中はJSミラーで即時計算し、`/worktime` は表示確定・集計タブ・共有側で使う。
- 社員名簿PLUS連携（R-04）: メンバー追加UIは既存の社員検索APIを利用して選択し、`employee_number`/`employee_name` を large_config に複写する。社員名簿側の変更は自動追従しない（表示名は display_name が正）。

## UI設計

### 作成フロー

- シフト帳一覧の新規作成モーダルのモード選択に「**大規模シフト帳**」を追加（説明: 多人数×1か月をグリッドで作成し、勤務時間・残業を自動計算）。
- 作成直後は「設定」画面（メンバー・コード登録）へ誘導する。メンバー0人・勤務コード0件のグリッドは編集できないため、空状態ガイドを出す。

### 設定画面（マスタ管理）

編集画面から開くモーダルまたは専用パネルで3タブ:

1. **メンバー**: 追加（社員名簿PLUS検索 / 自由入力の2ボタン・R-04）、表示名・並び順・所定拘束時間の個人上書き・無効化。ドラッグで列順変更。
2. **シフトコード**: 勤務コード（key・表示名・色・平日/土曜/日祝の時間セット・休憩上書き・「時間はセルで都度入力」= times全null）と休みコード（leave_kind・requested）の一覧編集。
3. **計算設定**: 休憩時間、所定拘束時間の既定、チェックのON/OFFとしきい値（R-05・R-07・R-08）。改善基準の既定値には注記を付ける。

### 編集画面（グリッド）

mode=large のとき、既存のカレンダーUIの代わりに専用グリッドを描画する（新規 `app/static/cloudshift/js/cloudshift_large.js` + CSS。`_cloudshift_script.html` から mode で分岐ロード）。

```
┌ ツールバー: 月移動 | 保存 | 基準版 ▾ | 表示(ハイライト/警告/コメント) | 設定 | 共有 | 出力 ┐
├──────┬────────────── メンバー列（固定ヘッダ・横スクロール）──────────────┤
│ 日付列 │ 職員A │ 職員B │ 職員C │ …                                      │
│ 7/1(水) │  A   │  C   │  D1  │                                        │
│ 7/2(木) │  B   │  A   │ 有休 │                                        │
│ …      │      │      │      │                                        │
├──────┴─────────────────────────────────────────────┤
│ 集計フッタ（人ごと・固定）: 拘束計 / 労働計 / 残業計 / 休出計 / 警告バッジ         │
└─────────────────────────────────────────────────────┘
```

- **日付列（固定）**: 日付・曜日・ダイヤ種別バッジ（平/土/日祝。上書きされた日は強調）・日メモアイコン。日ヘッダのメニューから「この日のダイヤ種別を変更」（R-03の長期休暇期運用）と「日メモ編集」（R-14）。
- **セル**: コード表示（マスタの色・休みコードは灰系）。休日区分マークは枠線/角マーカーで表現（所定と法定は別色。色はCSS変数で定義し固定しない）。コメント有りは右上ドット。時間上書き有りは時計アイコン。
- **セル編集**: クリックでパレットポップオーバー（勤務コード群 / 休みコード群 / 休日区分トグル(なし/所定/法定) / 時間上書き / コメント / クリア）。**キーボード操作を第一級で対応**: 矢印移動、コードkey直接タイプ+補完確定、Delete=クリア、Ctrl+C/V でセル複製（同列/同行への連続貼り付け）。多人数分の大量入力を高速化する。
- **リアルタイム集計**: 編集のたびにJSミラーで再計算し、フッタと警告バッジを即時更新（転記→別台帳で確認→戻る、の往復解消が本機能の核）。
- **個人明細ドロワー**: メンバー列ヘッダをクリック→その人の日別計算表（日付/コード/始業終業/休憩/拘束/労働/超過/区分）+ 月次合計 + その人の警告一覧。
- **集計タブ**: 全員×指標のテーブル（拘束時間計・労働時間計・長時間労働計(労基/安衛)・給与残業計・給与所定休出計・給与法定休出計・超過計・勤務日数・休み日数内訳）と警告一覧（クリックで該当セルへジャンプ）。値はサーバの `/worktime` を正とする。
- **下書き**: 既存の draft 機構をそのまま使う（下書き保存→公開）。
- モバイル/PWA閲覧: 全体グリッド（横スクロール）に加え、メンバー選択の**個人ビュー**（自分の1か月の縦リスト+個人集計）を用意する。

### 変更ハイライト（基準版・R-17）

- ツールバー「基準版」メニュー: **「現在の内容を基準版として確定」**（初回公開時にも確定を促すトースト）/「基準版を解除」/「基準版時点を表示」。
- 基準版が存在する月は、基準と現在で内容が異なるセルを強調表示（枠色+マーカー）。比較対象: `value` / `holiday_kind` / `time_override`（コメントの差は含めない。設定で切替可）。
- セルホバー/タップで「基準: A → 現在: 休」のツールチップ。変更一覧パネル（人・日・変更前後）も集計タブに併設。
- 閲覧リンク・PWA でもハイライトを表示する（「初回シフトからここが変わった」をメンバーに伝える用途）。
- 基準版の再確定は編集権限のみ。確定・解除は履歴（CloudShiftHistory）に記録する。

### 共有（R-12）

- 既存の閲覧/編集トークン・アカウント共有・PWA配布をそのまま利用。公開ページ（cloudshift_public.html / pwa）に大規模グリッド表示（読み取り専用 or 編集）を追加する。
- PWA通知等の既存機構は変更しない。

## 出力（専用レンダリング・R-13）

既存の `/export/<format>` ルートを流用し、mode=large では**大規模モード専用レンダラ**（新規 `app/tools/cloudshift_large_export.py`）へ分岐する。既存モードの csv / calendar_png は大規模モードでは提供しない（400）。

| format | 内容 |
|---|---|
| `xlsx`（openpyxl） | シート1「シフト表」: 行=日付・列=メンバーのグリッド。休日区分を背景色、休みコードを文字色で表現。日メモ列付き。シート2「集計」: 全員×指標の月次集計。シート3「個人明細」: 人別の日次計算表（1人1ブロック縦並び） |
| `pdf`（reportlab） | A4/A3横のシフト表グリッド + 集計表。日本語フォントは既存PDF系ツールの登録方式に従う |

- 変更ハイライトの印字（基準版と異なるセルへの記号付与）はオプション（クエリ `highlight=1`）。
- 公開トークン経由の export（既存の public export ルート）も同じレンダラを使う。

## 既存機能との関係

| 機能 | 大規模モードでの扱い |
|---|---|
| 履歴・リビジョン・下書き | 既存機構をそのまま利用 |
| 共有トークン・アカウント共有・PWA | 既存機構をそのまま利用（表示のみ専用UI） |
| アシスト / 自動作成エンジン / テンプレート | 対象外（UI非表示・API 400）。将来検討 |
| 代務要請 / 有休同期 / 空き確認・スポット | 対象外（初期） |
| 他モード同期（scene/person/master/substitute） | **初期は対象外だが必須の将来要件（R-16）**。エントリが `employee_number` を持ち、コード→時間がマスタ解決できるため、将来「大規模→個人シフト帳への配信同期」「個人→大規模の休暇反映」を既存 sync 機構の source として追加可能な形を維持する |
| 勤務時間計算API | 大規模モード以外の他ツールからも将来利用（DSTT内・R-11）。engine はCloudShift非依存を保つ |

## 段階実装計画

- **Phase 1（MVP: シフト作成と計算の1画面完結）**
  1. `worktime_engine.py` + 単体テスト（本書フィクスチャ）
  2. mode=large の追加（create / sanitize / mode_label / 既存機能のガード）
  3. `large_config` CRUD（設定画面3タブ、社員名簿PLUS選択）
  4. 月保存の large 分岐（normalize_large_entry / meta_data / スキーマ追加）
  5. グリッド編集UI（キーボード操作・パレット・日メモ・ダイヤ上書き）+ JSミラー計算 + 集計フッタ
  6. 個人明細・集計タブ・警告パネル（`/worktime` API）
  7. `/tools/worktime/api/calculate`（汎用API）
  8. 閲覧/編集リンク・PWAでの大規模表示（読み取り+公開編集）
- **Phase 2（運用強化）**
  1. 基準版・変更ハイライト（エディタ/共有/一覧パネル）
  2. 専用エクスポート（xlsx / pdf、ハイライト印字オプション）
  3. PWA個人ビュー・モバイル最適化
  4. コピー&ペースト強化（行/列一括、前月コピー）
- **Phase 3（連携・将来）**
  1. 他モード同期（大規模→個人シフト帳配信、休暇同期。R-16の「必ずつける」）
  2. 他ツールからの worktime API 利用の実例（給与系・ダッシュボード等）
  3. 警告プリセット（業種別の改善基準値セット等）、深夜時間帯の区分集計

注: Phase 2 の基準版ハイライト（R-17）とエクスポート（R-13）は**確定要件**であり、省略不可。Phase 分割は実装順序の指針にすぎない。

## テスト観点

1. **エンジン単体（最重要）**: 下記フィクスチャの完全一致。丸め誤差禁止（分整数演算）。
2. 時間解決: ダイヤ別セット選択、`time_override` 優先、フォールバック警告、times全null（時間可変コード）+ override 運用。
3. 区分: holiday_kind による work / scheduled_holiday_work / legal_holiday_work / leave の分類。**日曜でもマーク無しなら通常出勤**（フィクスチャ 19日目参照）。
4. チェック: 1日拘束13h/15h、休息期間（連日・月初の prev_day_end 有無・間に休みを挟む場合はスキップ）、月間拘束281h、OFF既定のチェックが無効であること、しきい値変更の反映。
5. 集計: 安衛基準の暦日数分岐（160/165/171/177）、leave_counts の kind×requested 集計、連続勤務日数。
6. API: `/tools/worktime/api/calculate` の契約（HH:MM⇔分、バリデーション、上限、未ログイン401）。
7. 大規模モードCRUD: create(mode=large)、large-config 検証（key重複・start<end・参照中コードの削除拒否）、月保存の正規化（1人1日1エントリ、空セル破棄）、meta_data 保存、既存モードへの無影響（meta_data空・既存エントリ正規化の不変）。
8. 基準版: スナップショット設定/解除、差分判定（value/holiday_kind/time_override、コメント差は不検出）、権限、履歴記録。
9. 公開系: view/edit/pwa トークンでの月+config+worktime 取得、公開編集の保存。
10. エクスポート: xlsx/pdf の生成成功と主要値の埋め込み（集計シートの合計値がエンジン結果と一致）。
11. 性能: 30人×31日で `/worktime` 応答が実用範囲（目安 < 1秒）、保存ペイロード正常。

## 計算検証フィクスチャ（正解データ）

エンジン実装の正しさを検証するための1名×1か月の完全なテストケース。実運用1か月分（匿名化済み）から抽出し、月次合計値を突き合わせて検証済みの**正解データ**である。Python エンジンと JS ミラーの両方のテストに使う（JSON化して `tests/` と静的アセットのテストで共有する）。

### 入力条件

- 対象月: **2026年7月**（31日、7/1は水曜）
- 設定: `break_minutes=60`、`scheduled_bind_minutes=570`（9:30）、チェックは既定値
- コード: 下表の始業終業はすべてセルの `time_override` として与える（コード時間解決のテストはこのフィクスチャとは別の小さなケースで行う）。使用コード:
  - 勤務コード: `A` `B` `C` `D` `F` `G`（F と G は時間可変コードを想定）
  - 休みコード: `振休`（substitute_rest）、`有休`（paid）
- `prev_day_end_minutes` は None（月初の休息期間判定なし）

### 日別入力と期待値

| 日 | 曜 | 入力コード | holiday_kind | 始業-終業(override) | 期待: 拘束 | 休憩 | 労働 | 超過 |
|---|---|---|---|---|---|---|---|---|
| 1 | 水 | F | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 2 | 木 | F | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 3 | 金 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 4 | 土 | F | | 08:00-16:00 | 8:00 | 1:00 | 7:00 | 0:00 |
| 5 | 日 | B | **legal** | 07:30-17:30 | 10:00 | 1:00 | 9:00 | 0:00（法定休出 9:00） |
| 6 | 月 | 振休 | | - | 0 | 0 | 0 | 0 |
| 7 | 火 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 8 | 水 | F | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 9 | 木 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 10 | 金 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 11 | 土 | G | | 08:00-15:00 | 7:00 | 1:00 | 6:00 | 0:00 |
| 12 | 日 | （空） | | - | 0 | 0 | 0 | 0 |
| 13 | 月 | 有休 | | - | 0 | 0 | 0 | 0 |
| 14 | 火 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 15 | 水 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 16 | 木 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 17 | 金 | 有休 | | - | 0 | 0 | 0 | 0 |
| 18 | 土 | （空） | | - | 0 | 0 | 0 | 0 |
| 19 | 日 | G | **（なし）** | 07:30-17:30 | 10:00 | 1:00 | 9:00 | **0:30** ※日曜だがマーク無し=通常出勤 |
| 20 | 月 | 有休 | | - | 0 | 0 | 0 | 0 |
| 21 | 火 | B | | 07:30-19:00 | 11:30 | 1:00 | 10:30 | 2:00 |
| 22 | 水 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 23 | 木 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 24 | 金 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 25 | 土 | （空） | | - | 0 | 0 | 0 | 0 |
| 26 | 日 | C | | 08:00-18:20 | 10:20 | 1:00 | 9:20 | 0:50 |
| 27 | 月 | D | | 07:20-20:00 | 12:40 | 1:00 | 11:40 | 3:10 |
| 28 | 火 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 29 | 水 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 30 | 木 | B | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 31 | 金 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |

### 期待される月次集計

| 指標 | 期待値 |
|---|---|
| 歴日数 | 31 |
| 勤務日数 | 24（通常出勤23 + 法定休出1） |
| 拘束時間計 | **260:40**（15640分） |
| 休憩計 | 24:00（1440分） |
| 労働時間計 | **236:40**（14200分） |
| 給与残業計 = 長時間労働計(労基) | **37:40**（2260分） |
| 給与所定休出計 | **0:00** |
| 給与法定休出計 | **9:00**（540分） |
| 超過計 | **46:40**（2800分） |
| 安衛基準（31日） | 177:00（10620分） |
| 長時間労働計(安衛) | **59:40**（3580分）= 236:40 − 177:00 |
| 休み内訳 | substitute_rest=1、paid=3、empty（空セル）=3 |
| 連続勤務日数（最大） | 5（7日〜11日） |

### 期待される警告（既定設定時）

- `KAIZEN_DAILY_BIND`（1日拘束 > 13:00）: **7日・16日・28日・31日**の4件（いずれも 14:35）。15:00 以下のため `KAIZEN_DAILY_BIND_MAX` は0件。
- `KAIZEN_REST_PERIOD`: **0件**（最短は 28日21:40終業 → 29日07:30始業 の 9:50 で、9:00 以上）。
- `KAIZEN_MONTHLY_BIND`: 0件（260:40 ≤ 281:00）。
- OFF既定のチェック（`OVERTIME_MONTHLY` 等）: 0件であること（37:40 > 45:00 ではない点にも注意。仮に `overtime_monthly` をONにしても発火しない）。

### 補助フィクスチャ（コード時間解決）

上記とは別に、次の小ケースでダイヤ別時間解決を検証する。

- コード `X`: weekday 09:00-18:00 / saturday 09:00-15:00 / holiday null
  - day_type=weekday → 拘束 9:00
  - day_type=saturday → 拘束 6:00
  - day_type=holiday → weekdayへフォールバック（拘束 9:00 + 警告 `TIME_SET_FALLBACK`）
- コード `Y`: times 全null（時間可変コード）
  - override 無し → 拘束 0 + 警告 `TIME_UNDEFINED`
  - override 10:00-16:00 → 拘束 6:00、休憩 1:00、労働 5:00
- 未登録コード `Z` → category=empty + 警告 `CODE_UNDEFINED`
- 休憩丸め: 拘束 0:40（override 09:00-09:40）→ 休憩 0:40 に丸め、労働 0:00

## 初期値・前提（実装時に調整可能）

1. 休みコード初期セットの意味付け（本書の表）は初期値であり、マスタは編集可能なので運用で調整できる。
2. 改善基準の既定しきい値（281h/13h/15h/9h）は自動車運転者（バス）前提の初期値。他業態プロジェクトでは設定画面で変更する想定。
3. 有休・振休等の休みは労働時間に算入しない。算入が必要になった場合は leave コードへの「みなし労働時間」属性追加で拡張する。
4. 休日区分マークの配色（所定/法定）はCSS変数で定義し、テーマ変更可能にする。
5. 長時間労働計(安衛)は 0 未満を 0 に丸める。表示要件が変われば表示層のみで対応。
6. グリッドの列固定・仮想スクロールは 30列×31行程度なら不要見込みだが、実装時に描画性能を確認して判断する。
