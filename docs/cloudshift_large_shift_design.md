# CloudShift 大規模シフトモード 設計書

作成日: 2026-07-22
ステータス: 要件定義完了・実装前設計（実装は本書を正とする）

## 目的

CloudShift のシフト帳モードに、10〜30人規模の1枚のシフト表を「行=日付 × 列=メンバー」のグリッドで作成する **大規模シフトモード（mode=`large`）** を追加する。

現行運用（Excel 2ファイル）では、①シフト表（配車表）でシフトを作成 → ②勤務時間表へ該当者のシフトコードを手で転記 → ③残業時間・休日出勤時間を確認 → ④超過していればシフト表へ戻って作り直す、という往復が発生している。2ファイルは連携しておらず、常に両方を開いて作業するしかない。

本モードは、シフト入力と同時に拘束時間・労働時間・残業時間・休日出勤時間をリアルタイム計算し、この往復を1画面で完結させる。勤務時間計算ロジックは CloudShift から独立した **汎用の勤務時間計算エンジン + DSTT 内 API** として実装し、後で他ツールからも利用できるようにする。

## 要件定義の確定事項（発注者回答）

| # | 論点 | 決定 |
|---|---|---|
| 1 | 所定休日/法定休日の区分 | **セル単位（人×日）でマークする**（現行Excelの色分け踏襲）。ただしUIは現行Excelの再現ではなく CloudShift になじむものにする |
| 2 | コード体系 | **1コード + ダイヤ別時間**。コード「A」に平日/土曜/日祝の時間セットを持たせ、日付から自動で該当時間を引く。**「日曜ダイヤを平日に使う」等があるため、日単位のダイヤ種別上書きを設ける** |
| 3 | メンバー管理 | **社員名簿PLUSからの選択 + 自由入力の併用** |
| 4 | 警告指標 | **改善基準告示の拘束時間チェック**（月間拘束・1日拘束・休息期間）を既定ON。他指標（給与残業・安衛法長時間労働・連続勤務）は計算/表示は行い、警告は設定でON可能 |
| 5 | 休みコード | **休=シフト作成者が決めた休み（指定休）、㊡=本人の希望休**。所定/法定とは別軸。方式は変えてよいが区別は必須 |
| 6 | 所定拘束時間 | 基本 9:30 だが**変わることがある** → 既定値 + メンバー別 + セル別の上書きを設ける |
| 7 | 休憩 | **1時間固定でよい**（設定値として保持） |
| 8 | 第一/第二/共生 等 | この現場固有の行き先名。**汎用ソフトにするため「利用者が登録する勤務コード」として扱う**（特別扱いしない） |
| 9 | シフト表のY列の数字 | 対象外（無視してよい） |
| 10 | 日跨ぎ勤務 | **無い** → 同日内（始業<終業）前提で計算を簡素化 |
| 11 | 計算APIの利用者 | **DSTT内のみ**（外部公開しない。ログイン必須の内部API） |
| 12 | 共有機能 | 閲覧/編集リンク・PWA配布は**必要** |
| 13 | 出力 | Excel/PDF出力は**必要**。ただし**大規模モード専用レンダリング方式**とする |
| 14 | 行事メモ・セル補足 | **コメント機能で対応**（日メモ + セルコメント） |
| 15 | Excel取込 | **不要**（汎用ソフト方針のため既存ファイル移行機能は作らない） |
| 16 | 他モードとの同期 | **初期リリースでは対象外。ただし後で必ず追加する** → 同期可能なデータ形を最初から保つ |
| 追加 | 変更ハイライト | **初回に確定したシフト（基準版）から変更されたセルをハイライトする機能が必要** |

## 現行Excel運用の分析（読解結果）

### シフト表（配車表）

- 行=日付（1か月）、列=メンバー（約14名）のグリッド。セルにシフトコード（A, B, C, D1, D2, …, 班長, 共生AM/PM, 第一, 第二, 他 など）や休みコード（休, ㊡, 振休, 振㊡, 有㊡, 特休 など）を記入。
- セルの背景色で、その人・その日の「所定休日/法定休日」を区分。色付きセルに勤務コードが入っている日は休日出勤（振出等）を意味する。
- 「D1+日報」「振出+第一」のようにコードと補足メモの複合記入がある。日付ごとの行事メモ列もある。
- 数式は曜日表示のみで集計機能はない。

### 勤務時間表

- 「拘束時間」シート = シフトコードマスタ。コードごとに始業・終業。平日（A…）/土曜（土A…）/日曜・学休期（日A…）が**別コード**として定義されており、転記時に人が読み替えている。
- 個人別シート（人数分）にシフト表から手で転記。「出勤」「所定休出」「法定休出」の3区分の列に分けて入力し、コード→マスタ参照で始業終業を取得。
- 小計シートで全員の月次集計（拘束時間計・労働時間計・長時間労働計(労基/安衛)・給与残業計・給与所定休出計・給与法定休出計）。

### 検証済み計算ロジック（数式解析の結果）

| 項目 | ロジック |
|---|---|
| 始業・終業 | シフトコード → マスタ参照（曜日別は別コードで表現） |
| 休憩 | 実質ほぼ全日 1:00（規定上は拘束6h未満=0、休日出勤6〜8h=0:45 だが、対象データでは全日1:00で月次合計が一致することを確認済み） |
| 拘束時間 | 終業 − 始業（日次） |
| 労働時間 | 拘束時間 − 休憩 |
| 日次超過（残業） | max(0, 日次拘束時間 − 所定拘束時間9:30) ※労働時間ではなく拘束時間ベース |
| 給与残業計 = 長時間労働計(労基) | 通常出勤日の日次超過の月合計 |
| 所定休出計 / 法定休出計 | 休日区分マークのある日の勤務の**労働時間**をそれぞれ合計（残業計には入れない） |
| 長時間労働計(安衛) | 月の総労働時間（休出含む）− 月間基準時間（暦日数28日=160h / 29日=165h / 30日=171h / 31日=177h） |
| 超過計（給与） | 給与残業計 + 所定休出計 + 法定休出計 |
| その他 | 勤務日数（出勤+休出の日数）、歴日数、振休・有休等の日数管理 |

補足:

- 現行Excelは「所定休日に働く場合の時間」「法定休日に働く場合の時間」を別テーブルで持つが、値は実質同一（日祝ダイヤ相当）。本設計では **ダイヤ3種（平日/土曜/日祝）+ セル単位の時間上書き** に集約する。
- 「他」のようなコードは日によって時間がまちまち（08:00-16:00 の日と 08:30-17:30 の日がある）。**時間未設定コード + セル時間上書き** で表現する。
- 「振出」はExcel上、通常出勤扱い（G列）+ フラグ。 本設計では振出という勤務コードは作らず「休日マークを付けない通常勤務 + コメント」または勤務コードで表現する。振休は休みコードとして持つ。

## 用語

| 用語 | 意味 |
|---|---|
| ダイヤ種別（day_type） | その日にどの時間セットを使うか: `weekday`（平日）/ `saturday`（土曜）/ `holiday`（日祝） |
| 休日区分（holiday_kind） | 人×日のセルに付ける休日マーク: なし / `scheduled`（所定休日）/ `legal`（法定休日） |
| 勤務コード（work code） | 始業終業を持つコード（A, 班長, 第一 など）。休日区分マークのある日に入れば休日出勤 |
| 休みコード（leave code） | 勤務しないことを表すコード（休, ㊡, 振休, 有休 など）。時間は常に0 |
| 所定拘束時間 | 日次超過（残業）算定の基準となる1日の拘束時間（既定 9:30） |
| 基準版（baseline） | 変更ハイライトの比較元となる、確定時点の月データのスナップショット |

## 全体方針

1. `CloudShiftProject.mode` に `large` を追加し、既存の project / month / history / 共有トークン / PWA 基盤をそのまま使う。
2. 勤務時間計算は **`app/services/worktime_engine.py`（純粋Python、Flask/DB非依存）** に実装し、CloudShift はサービスとして直接呼ぶ。同じエンジンを **`/tools/worktime/api/calculate`（ログイン必須のDSTT内API）** としても公開する。
3. マスタ（メンバー・コード・設定）は project の extra_data（`project["large_config"]`）に保存する。既存モードのデータ構造には手を入れない。
4. セルデータは既存の `CloudShiftMonth.entries_per_day` に大規模モード専用のエントリ形式で保存する。正規化は既存 `normalize_entry` を通さず、**大規模モード専用の正規化関数**で行う（保存APIで mode 分岐）。
5. 計算はサーバを正とする。クライアントは同仕様のJSミラーで編集中の即時再計算を行い、保存/集計表示時にサーバ値で確定する。両実装は共通のフィクスチャ（本書末尾）でテストする。
6. 汎用性を優先する。「第一」「共生」等の固有名や、バス事業固有の値（281h等）はすべて**利用者が編集できるマスタ/設定値**とし、コードに固定で埋め込まない。
7. 将来の他モード同期（要件16）に備え、エントリに `employee_number` / `employee_name` を保持し、コード→時間がマスタから解決できる形を維持する。

## 非目標（初期リリース）

- 既存Excelファイルの取込・移行機能（要件15）
- 他モード（scene/person/master/substitute）との同期反映（要件16。Phase 3 で必ず実装）
- 自動シフト作成エンジン・アシスト・テンプレート・代務要請の大規模モード対応
- 深夜割増・22時以降の割増区分などの給与計算（残業=拘束超過のみ。将来拡張）
- 日跨ぎ勤務（要件10により不要）
- DSTT外部への計算API公開（要件11によりログイン必須の内部APIのみ）
- 有休の労働時間算入（現行Excel踏襲で算入しない。将来設定化の余地のみ残す）

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
      "display_name": "進士",           // 列ヘッダ表示名（必須）
      "employee_number": "1234",        // 社員名簿PLUS連携時のみ（自由入力メンバーは空）
      "employee_name": "進士 浩成",     // 連携時の正式名
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
      "requested": false,               // category=leave のみ: 本人希望由来か（㊡系=true）
      "color": "#e3f2fd",               // セル背景色
      "order": 10,
      "active": true,
      "note": ""
    }
  ],
  "settings": {
    "break_minutes": 60,                    // 休憩（既定1時間固定・要件7）
    "scheduled_bind_minutes": 570,          // 所定拘束時間 9:30（要件6の既定値）
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

- 休みコードの初期セット（プロジェクト作成時に自動投入。すべて編集・削除可能）:

| key | label | leave_kind | requested | 意味（要件5） |
|---|---|---|---|---|
| `休` | 休 | rest | false | シフト作成者が決めた休み（指定休） |
| `㊡` | ㊡ | rest | true | 本人の希望休 |
| `振休` | 振休 | substitute_rest | false | 振替休日 |
| `振㊡` | 振㊡ | substitute_rest | true | 希望による振替休日 |
| `有休` | 有休 | paid | false | 年次有給休暇 |
| `有㊡` | 有㊡ | paid | true | 希望日に充てた有給休暇 |
| `特休` | 特休 | special | false | 特別休暇 |

- 勤務コードの初期セットは投入しない（現場ごとに登録する。汎用方針・要件8）。
- バリデーション: members ≤ 60（UI推奨は30まで、31以上で注意表示）、codes ≤ 200、key は空・重複不可、times の start < end（日跨ぎ禁止・要件10）。
- メンバー・コードの削除は原則 `active=false` の無効化。エントリが参照している要素を物理削除しようとした場合は拒否し、無効化を促す。

### 月データ（セル）

`CloudShiftMonth.entries_per_day` を使う。日キー（"1".."31"）→ エントリ配列は既存と同じで、**エントリ形式のみ大規模モード専用**:

```jsonc
{
  "id": "ce_xxxxxxxx",
  "member_id": "mem_xxxxxxxx",          // large_config.members の id（必須）
  "value": "A",                          // コードkey。マスタ未登録の文字列も保存は許可（警告扱い）
  "holiday_kind": "",                    // "" | "scheduled"(所定休日) | "legal"(法定休日)
  "time_override": null,                 // {"start":"08:30","end":"17:30"} 例外日・時間可変コード用
  "bind_override_minutes": null,         // 所定拘束時間のセル上書き（要件6）
  "comment": "",                         // セルコメント（要件14。「日報」「振出」等の補足）
  "employee_number": "",                 // メンバーから複写（将来同期用・要件16）
  "employee_name": ""
}
```

ルール:

- **1メンバー×1日 = 最大1エントリ**（現行Excelの複合記入はコメントで表現）。保存時に重複 member_id を検出したら後勝ちで統合し警告。
- `value` 空文字 + holiday_kind もコメントも空のエントリは保存時に破棄（空セル）。
- `value` が空でも `holiday_kind` が付いていれば保存する（「休日マークだけの日」を許す。計算上は休み扱い）。
- 正規化は新設の `normalize_large_entry`（`app/tools/shiftersync_format.py` に追加）で行う。既存 `normalize_entry` の `!OPT!` 形式・代務系フィールドは適用しない。月保存API（PUT month / 公開編集PUT）で `project.mode == "large"` のとき分岐する。
- 下書き（draft_entries_per_day）・リビジョン（revision / revision_snapshots）・履歴（CloudShiftHistory）は既存機構をそのまま使う。

### 月メタデータと基準版（スキーマ追加）

`cloudshift_months` に JSON 列 **`meta_data`** を1本追加する（既存の `_ensure_cloudshift_runtime_schema` の ALTER 方式に倣う。models.py にも列を追加）:

```jsonc
{
  "day_types": {"6": "holiday", "21": "holiday"},   // ダイヤ種別の日単位上書き（要件2。無い日は自動判定）
  "day_notes": {"3": "奨学金説明会", "19": "オープンキャンパス"},  // 日メモ（行事・要件14）
  "baseline": {                                      // 基準版（変更ハイライト・追加要件）
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

- エンジンは同一入力→同一出力の決定的実装とし、時間は**分単位の整数**で扱う（浮動小数を使わない）。
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
    leave_counts: Mapping[str, int]      # leave_kind 別日数（requested 別も持つ: "rest", "rest_requested", ...）
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
2. **時間解決**（category=work）: 優先順位は `time_override` → `times[day_type]` → `times["weekday"]`（フォールバック時は警告 `TIME_SET_FALLBACK`）。どれも無ければ時間0 + 警告 `TIME_UNDEFINED`（「他」のような時間可変コードは times を全て null で登録し、セルの time_override で入力する運用）。
3. **拘束時間** `bind = end − start`。
4. **休憩** `break = code.break_minutes ?? settings.break_minutes`、ただし `bind == 0` なら 0、`break > bind` なら `bind` に丸める（労働時間を負にしない）。
5. **労働時間** `work = bind − break`。
6. **区分**: `holiday_kind == ""` → `work`（通常出勤）。`"scheduled"` → `scheduled_holiday_work`、`"legal"` → `legal_holiday_work`。休みコード＋holiday_kind 付きは `leave`（その日が休日でもある、のマークとして許容）。
7. **日次超過**（category=work のみ）: `scheduled_bind = bind_override ?? person.scheduled_bind_minutes ?? settings.scheduled_bind_minutes`、`overtime = max(0, bind − scheduled_bind)`。休出（scheduled/legal）は超過を計上しない（労働時間全体を休出計へ）。

### 月次集計仕様

- `bind_total` / `work_total` は通常出勤＋所定休出＋法定休出のすべてを含む（現行Excelの 拘束時間計・労働時間計 と同義）。
- `payroll_overtime`（給与残業計）= 通常出勤日の `overtime` 合計。**長時間労働計(労基) はこれと同値**（現行Excel踏襲）。
- `scheduled_holiday_work` / `legal_holiday_work` = 各休出日の `work` 合計。
- `anei_base_minutes`: 暦日数から自動決定。

| 暦日数 | 基準時間 |
|---|---|
| 28日 | 160:00 (9600分) |
| 29日 | 165:00 (9900分) |
| 30日 | 171:00 (10260分) |
| 31日 | 177:00 (10620分) |

- `anei_excess = max(0, work_total − anei_base)`（現行Excelは負値も表示するが、本設計では0未満は0とする）。
- `leave_counts` は leave_kind × requested の組み合わせで日数集計（指定休/希望休/振休/振㊡/有休/有㊡/特休…の管理・要件5）。
- `work_days` = category が work/休出 の日数。`calendar_days` = 月の暦日数。

### チェック（警告）仕様

`WorktimeViolation.code` の一覧。しきい値はすべて `settings.checks` で変更・有効無効化できる（要件4: 既定は改善基準3種のみON）。

| code | 既定 | 対象 | 内容 | severity |
|---|---|---|---|---|
| `KAIZEN_MONTHLY_BIND` | ON | 月次 | 月間拘束時間 > 281:00 | warning |
| `KAIZEN_DAILY_BIND` | ON | 日次 | 1日拘束 > 13:00 | warning |
| `KAIZEN_DAILY_BIND_MAX` | ON | 日次 | 1日拘束 > 15:00（上限超過） | violation |
| `KAIZEN_REST_PERIOD` | ON | 日次 | 休息期間 <  9:00。前日の終業〜当日の始業の間隔 `(1440 − prev_end) + start` で判定。月初日は `prev_day_end_minutes`（前月末日の終業）が与えられた場合のみ判定 | violation |
| `OVERTIME_MONTHLY` | OFF | 月次 | 給与残業計 > 45:00 | warning |
| `ANEI_LONG_WORK` | OFF | 月次 | 長時間労働(安衛) > 80:00 | warning |
| `CONSECUTIVE_DAYS` | OFF | 月次 | 連続勤務日数 > 7日 | warning |
| `TIME_UNDEFINED` / `TIME_SET_FALLBACK` / `CODE_UNDEFINED` | 常時 | 日次 | データ不備（時間未設定・ダイヤ未設定・未登録コード） | info |

- 改善基準の既定値（281h/13h/15h/9h）は2024年改正のバス運転者向け改善基準告示に基づく初期値。汎用ソフトとして**数値はすべて設定変更可能**とし、根拠の注記をUIに表示する。
- severity の扱い: `violation` / `warning` は保存自体は妨げない（Excel同様、作成途中の状態を許す）。UIで強調表示し続けることで是正を促す。

## API設計

### 汎用 勤務時間計算API（DSTT内・要件11）

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
    {"person_id": "p1", "label": "進士",
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
| `PUT /api/project/<id>/month/<y>/<m>` | 既存の月保存を拡張。mode=large のとき `entries_per_day` を `normalize_large_entry` で正規化し、`meta` （`day_types`/`day_notes`）を `meta_data` へ保存。revision/履歴は既存どおり |
| `GET /api/project/<id>/month-payload` 系（既存の project 取得に含まれる月データ） | mode=large では `meta_data`（day_types/day_notes/baseline有無・set_at）を月ペイロードに含める |
| `GET /api/project/<id>/month/<y>/<m>/worktime` | その月の計算結果（WorktimeResult 相当）。サーバが large_config + entries + meta から WorktimeRequest を組み立てて engine を呼ぶ。集計タブ・個人明細・保存後の確定表示に使う |
| `POST /api/project/<id>/month/<y>/<m>/baseline` | 基準版の設定（現在の確定 entries をスナップショット）/ `{"clear": true}` で解除。編集権限必須。履歴記録 |
| 公開系 `GET /api/public/<view|edit|pwa>/<token>` | 既存。mode=large の月ペイロード＋large_config（閲覧に必要な members/codes/settings）+ baseline を返す |
| 公開編集 `PUT /api/public/edit/<token>/month/<y>/<m>` | 既存拡張（mode分岐は月保存と共通実装） |
| 公開系 `GET /api/public/<type>/<token>/worktime/<y>/<m>` | 閲覧/編集/PWAリンクからの計算結果取得（集計・警告表示のため） |
| `GET /api/project/<id>/export/<format>` ほか公開export | mode=large のとき専用レンダラへ分岐（後述） |

- worktime 計算をセル編集のたびにサーバへ投げない。編集中はJSミラーで即時計算し、`/worktime` は表示確定・集計タブ・共有側で使う。
- 社員名簿PLUS連携（要件3）: メンバー追加UIは既存の社員検索API（pluslist）を利用して選択し、`employee_number`/`employee_name` を large_config に複写する。社員名簿側の変更は自動追従しない（表示名は display_name が正）。

## UI設計

### 作成フロー

- シフト帳一覧の新規作成モーダルのモード選択に「**大規模シフト帳**」を追加（説明: 10〜30人×1か月をグリッドで作成し、勤務時間・残業を自動計算）。
- 作成直後は「設定」画面（メンバー・コード登録）へ誘導する。メンバー0人・勤務コード0件のグリッドは編集できないため、空状態ガイドを出す。

### 設定画面（マスタ管理）

編集画面から開くモーダルまたは専用パネルで3タブ:

1. **メンバー**: 追加（社員名簿PLUS検索 / 自由入力の2ボタン・要件3）、表示名・並び順・所定拘束時間の個人上書き・無効化。ドラッグで列順変更。
2. **シフトコード**: 勤務コード（key・表示名・色・平日/土曜/日祝の時間セット・休憩上書き・「時間はセルで都度入力」= times全null）と休みコード（leave_kind・requested）の一覧編集。
3. **計算設定**: 休憩時間、所定拘束時間の既定、チェックのON/OFFとしきい値（要件4・7）。改善基準の既定値には注記を付ける。

### 編集画面（グリッド）

mode=large のとき、既存のカレンダーUIの代わりに専用グリッドを描画する（新規 `app/static/cloudshift/js/cloudshift_large.js` + CSS。`_cloudshift_script.html` から mode で分岐ロード）。

```
┌ ツールバー: 月移動 | 保存 | 基準版 ▾ | 表示(ハイライト/警告/コメント) | 設定 | 共有 | 出力 ┐
├──────┬────────────── メンバー列（固定ヘッダ・横スクロール）──────────────┤
│ 日付列 │ 進士 │ 山口 │ 橋本 │ …                                        │
│ 7/1(水) │  A   │  C   │ D1  │                                          │
│ 7/2(木) │ 班長 │  B   │ 有休 │                                          │
│ …      │      │      │      │                                          │
├──────┴─────────────────────────────────────────────┤
│ 集計フッタ（人ごと・固定）: 拘束計 / 労働計 / 残業計 / 休出計 / 警告バッジ         │
└─────────────────────────────────────────────────────┘
```

- **日付列（固定）**: 日付・曜日・ダイヤ種別バッジ（平/土/日祝。上書きされた日は強調）・日メモアイコン。日ヘッダのメニューから「この日のダイヤ種別を変更」（要件2の学休期運用）と「日メモ編集」（要件14）。
- **セル**: コード表示（マスタの色・休みコードは灰系）。休日区分マークは枠線/角マーカーで表現（所定=グレー、法定=青。色は固定でなくCSSトークン）。コメント有りは右上ドット。時間上書き有りは時計アイコン。
- **セル編集**: クリックでパレットポップオーバー（勤務コード群 / 休みコード群 / 休日区分トグル(なし/所定/法定) / 時間上書き / コメント / クリア）。**キーボード操作を第一級で対応**: 矢印移動、コードkey直接タイプ+補完確定、Delete=クリア、Ctrl+C/V でセル複製（同列/同行への連続貼り付け）。10〜30人分の大量入力を高速化する。
- **リアルタイム集計**: 編集のたびにJSミラーで再計算し、フッタと警告バッジを即時更新（「作って→別ファイルで確認→戻る」の往復を消す本機能の核）。
- **個人明細ドロワー**: メンバー列ヘッダをクリック→その人の日別計算表（日付/コード/始業終業/休憩/拘束/労働/超過/区分。現行の勤務時間表個人シート相当）+ 月次合計 + その人の警告一覧。
- **集計タブ**: 全員×指標のテーブル（現行の小計シート相当: 拘束時間計・労働時間計・長時間労働計(労基/安衛)・給与残業計・給与所定休出計・給与法定休出計・超過計・勤務日数・休み日数内訳）と警告一覧（クリックで該当セルへジャンプ）。値はサーバの `/worktime` を正とする。
- **下書き**: 既存の draft 機構をそのまま使う（下書き保存→公開）。
- モバイル/PWA閲覧: 全体グリッド（横スクロール）に加え、メンバー選択の**個人ビュー**（自分の1か月の縦リスト+個人集計）を用意する。

### 変更ハイライト（基準版・追加要件）

- ツールバー「基準版」メニュー: **「現在の内容を基準版として確定」**（初回公開時にも確定を促すトースト）/「基準版を解除」/「基準版時点を表示」。
- 基準版が存在する月は、基準と現在で内容が異なるセルを強調表示（枠色+マーカー）。比較対象: `value` / `holiday_kind` / `time_override`（コメントの差は含めない。設定で切替可）。
- セルホバー/タップで「基準: A → 現在: 休」のツールチップ。変更一覧パネル（人・日・変更前後）も集計タブに併設。
- 閲覧リンク・PWA でもハイライトを表示する（「初回シフトからここが変わった」をメンバーに伝える用途）。
- 基準版の再確定は編集権限のみ。確定・解除は履歴（CloudShiftHistory）に記録する。

### 共有（要件12）

- 既存の閲覧/編集トークン・アカウント共有・PWA配布をそのまま利用。公開ページ（cloudshift_public.html / pwa）に大規模グリッド表示（読み取り専用 or 編集）を追加する。
- PWA通知等の既存機構は変更しない。

## 出力（専用レンダリング・要件13）

既存の `/export/<format>` ルートを流用し、mode=large では**大規模モード専用レンダラ**（新規 `app/tools/cloudshift_large_export.py`）へ分岐する。既存モードの csv / calendar_png は大規模モードでは提供しない（400）。

| format | 内容 |
|---|---|
| `xlsx`（openpyxl） | シート1「シフト表」: 行=日付・列=メンバーのグリッド。休日区分を背景色、休みコードを文字色で表現。日メモ列付き。シート2「集計」: 小計シート相当の全員×指標。シート3「個人明細」: 人別の日次計算表（1人1ブロック縦並び） |
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
| 他モード同期（scene/person/master/substitute） | **初期は対象外だが必須の将来要件（要件16）**。エントリが `employee_number` を持ち、コード→時間がマスタ解決できるため、将来「大規模→個人シフト帳への配信同期」「個人→大規模の休暇反映」を既存 sync 機構の source として追加可能な形を維持する |
| 勤務時間計算API | 大規模モード以外の他ツールからも将来利用（DSTT内・要件11）。engine はCloudShift非依存を保つ |

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
  1. 他モード同期（大規模→個人シフト帳配信、休暇同期。要件16の「必ずつける」）
  2. 他ツールからの worktime API 利用の実例（給与系・ダッシュボード等）
  3. 警告プリセット（トラック/タクシー等の改善基準値セット）、深夜時間帯の区分集計

注: Phase 2 の基準版ハイライトとエクスポートは**確定要件**（追加要件・要件13）であり、省略不可。Phase 分割は実装順序の指針にすぎない。

## テスト観点

1. **エンジン単体（最重要）**: 下記フィクスチャの完全一致。丸め誤差禁止（分整数演算）。
2. 時間解決: ダイヤ別セット選択、`time_override` 優先、フォールバック警告、times全null（時間可変コード）+ override 運用。
3. 区分: holiday_kind による work / scheduled_holiday_work / legal_holiday_work / leave の分類。日曜でもマーク無しなら通常出勤（フィクスチャ d19 参照）。
4. チェック: 1日拘束13h/15h、休息期間（連日・月初の prev_day_end 有無）、月間拘束281h、OFF既定のチェックが無効であること、しきい値変更の反映。
5. 集計: 安衛基準の暦日数分岐（160/165/171/177）、leave_counts の kind×requested 集計、連続勤務日数。
6. API: `/tools/worktime/api/calculate` の契約（HH:MM⇔分、バリデーション、上限、未ログイン401）。
7. 大規模モードCRUD: create(mode=large)、large-config 検証（key重複・start<end・参照中コードの削除拒否）、月保存の正規化（1人1日1エントリ、空セル破棄）、meta_data 保存、既存モードへの無影響（meta_data空）。
8. 基準版: スナップショット設定/解除、差分判定（value/holiday_kind/time_override、コメント差は不検出）、権限、履歴記録。
9. 公開系: view/edit/pwa トークンでの月+config+worktime 取得、公開編集の保存。
10. エクスポート: xlsx/pdf の生成成功と主要値の埋め込み（集計シートの合計値がエンジン結果と一致）。
11. 性能: 30人×31日で `/worktime` 応答が実用範囲（目安 < 1秒）、保存ペイロード正常。

## 計算検証フィクスチャ（現行Excel実測値）

添付Excel「勤務時間表」の実在1名・2026年7月分から抽出した検証データ。**休憩1:00固定・所定拘束9:30 の本設計仕様で、現行Excelの月次合計と完全一致することを検証済み**（対象月は全勤務日で休憩1:00のため）。時間は "H:MM"。

前提: `settings.break_minutes=60`、`scheduled_bind_minutes=570`。各日は `time_override` で下表の始業終業を与える（コード時間解決のテストは別途小さなケースで行う）。

| 日 | 曜 | 入力 | holiday_kind | 始業-終業 | 拘束 | 休憩 | 労働 | 超過 |
|---|---|---|---|---|---|---|---|---|
| 1 | 水 | 他 | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 2 | 木 | 他 | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 3 | 金 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 4 | 土 | 他(振出) | | 08:00-16:00 | 8:00 | 1:00 | 7:00 | 0:00 |
| 5 | 日 | 班長 | legal | 07:30-17:30 | 10:00 | 1:00 | 9:00 | 0:00(法定休出9:00) |
| 6 | 月 | 振休 | | - | 0 | 0 | 0 | 0 |
| 7 | 火 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 8 | 水 | 他 | | 08:30-17:30 | 9:00 | 1:00 | 8:00 | 0:00 |
| 9 | 木 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 10 | 金 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 11 | 土 | 第一 | | 08:00-15:00 | 7:00 | 1:00 | 6:00 | 0:00 |
| 12 | 日 | （空） | | - | 0 | 0 | 0 | 0 |
| 13 | 月 | 有休 | | - | 0 | 0 | 0 | 0 |
| 14 | 火 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 15 | 水 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 16 | 木 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 17 | 金 | 有休 | | - | 0 | 0 | 0 | 0 |
| 18 | 土 | （空） | | - | 0 | 0 | 0 | 0 |
| 19 | 日 | 第一 | **（なし）** | 07:30-17:30 | 10:00 | 1:00 | 9:00 | **0:30**（マーク無し=通常出勤） |
| 20 | 月 | 有休 | | - | 0 | 0 | 0 | 0 |
| 21 | 火 | 班長 | | 07:30-19:00 | 11:30 | 1:00 | 10:30 | 2:00 |
| 22 | 水 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 23 | 木 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 24 | 金 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 25 | 土 | （空） | | - | 0 | 0 | 0 | 0 |
| 26 | 日 | 日A | | 08:00-18:20 | 10:20 | 1:00 | 9:20 | 0:50 |
| 27 | 月 | D | | 07:20-20:00 | 12:40 | 1:00 | 11:40 | 3:10 |
| 28 | 火 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |
| 29 | 水 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 30 | 木 | 班長 | | 07:30-18:05 | 10:35 | 1:00 | 9:35 | 1:05 |
| 31 | 金 | A | | 07:05-21:40 | 14:35 | 1:00 | 13:35 | 5:05 |

期待される月次集計（現行Excel「小計」実測値と一致）:

| 指標 | 期待値 |
|---|---|
| 歴日数 | 31 |
| 勤務日数 | 24（通常23 + 法定休出1） |
| 拘束時間計 | **260:40** |
| 休憩計 | 24:00 |
| 労働時間計 | **236:40** |
| 給与残業計 = 長時間労働計(労基) | **37:40** |
| 給与所定休出計 | **0:00** |
| 給与法定休出計 | **9:00** |
| 超過計 | **46:40** |
| 安衛基準（31日） | 177:00 |
| 長時間労働計(安衛) | **59:40**（236:40 − 177:00） |
| 休み内訳 | 振休1・有休3・空(休み扱い)3 |
| 警告（既定設定時） | `KAIZEN_DAILY_BIND`: d7/d16/d28/d31（14:35 > 13:00）ほか該当日 |

このフィクスチャは Python エンジンと JS ミラーの両方のテストに用いる（JSON化して `tests/` と静的アセットのテストで共有）。

## 未確定事項と前提（実装時に確認・調整可能）

1. 休みコード初期セットの意味付け（振㊡・有㊡等）は本書の表を初期値とするが、マスタは編集可能なので運用で調整できる。
2. 改善基準の既定しきい値（281h/13h/15h/9h）は貸切バス前提の初期値。他業態プロジェクトでは設定画面で変更する想定。
3. 有休・振休等の休みは労働時間に算入しない（現行Excel踏襲）。算入が必要になった場合は leave コードへの「みなし労働時間」属性追加で拡張する。
4. 休日区分マークの配色（所定=グレー系/法定=青系）は現行Excelの慣習に合わせた初期値。CSS変数で変更可能にする。
5. 安衛の長時間労働は 0 未満を 0 に丸める（現行Excelは負値表示）。表示要件が変われば表示層のみで対応。
6. グリッドの列固定・仮想スクロールは 30列×31行程度なら不要見込みだが、実装時に描画性能を確認して判断する。
