# 貸切バス運賃・料金自動計算＆法令監査ツール（DSTT 適合版）基本・詳細設計書

本書は道路運送法および国土交通省「貸切バスの公示運賃・料金」基準、ならびに厚生労働省「自動車運転者の労働時間等の改善のための基準（改善基準告示）」に基づく自動算定・コンプライアンス監査ツールを、**DSTT（DaishintoTools）** の既存ツール群と同じ規約で実装するための仕様書である。

原設計書（外部ベンダー作成）の構造を踏襲しつつ、以下の二点を行う:

1. DSTT の標準パターン（Flask Blueprint + HTML フォーム + `app/services/` の業務ロジック分離）に合わせて構成変更
2. 原設計書のロジックの矛盾・誤りを実装可能な内容に修正

---

## 1. ツール概要

| 項目 | 内容 |
|---|---|
| ツールキー | `bus_pricing` |
| 表示名 | 貸切バス運賃計算 |
| URL プレフィックス | `/tools/bus_pricing` |
| アクセス制御 | `@login_required`（機微情報は扱わないため `TOOL_ACCESS_CATEGORIES` には登録しない＝一般カテゴリ） |
| NAV アイコン候補 | 🚌 |
| 主な依存 | 標準ライブラリ + Flask のみ（追加パッケージなし） |

### 1.1 目的

公示運賃の**下限額**を自動算定し、改善基準告示に基づく**勤務適正性**を同時に検証する「コンプライアンス監査型計算ツール」を DSTT 内に追加する。下限割れの見積りや、行政処分対象となる勤務過重スケジュールはバリデーションで拒否する。

### 1.2 スコープ

- 計算と監査に限定。配車・予約・運行指示書・請求書出力などはスコープ外。
- 入力 1 件 = 運行 1 件分の下限額算定。年間契約は「最も高くなる 1 日」の算定額を年間積算する。

---

## 2. DSTT 上の配置と動作環境

### 2.1 ファイル配置

```
app/
  tools/
    bus_pricing.py            # Blueprint（フォーム入出力／薄いエンドポイント）
  services/
    bus_pricing_service.py    # 業務ロジック（純関数群、テスト容易）
    bus_pricing_master.py     # 静的マスタ（運輸局別単価／実働率／車種定員）
  templates/
    bus_pricing.html          # 入力フォーム + 結果表示
    manual_bus_pricing.html   # マニュアル（任意。manuals.py に登録）
tests/
  test_bus_pricing_service.py # サービス層の単体テスト
```

### 2.2 統合作業（既存ファイルに対する追加）

| ファイル | 追加内容 |
|---|---|
| `app/navigation.py` | `NAV_ITEMS` に `bus_pricing` 項目を追加 |
| `app/__init__.py` | `from .tools.bus_pricing import bus_pricing_bp` / `app.register_blueprint(bus_pricing_bp)` |
| `app/manuals.py` | `MANUALS["bus_pricing"]` を追加（任意） |
| `app/templates/index.html` | NAV から自動カード生成されるため通常は変更不要 |

### 2.3 動作環境

- Python 3.10+ / Flask 3.1（既存 `requirements.txt` で要件充足）
- 永続化なし。マスタは Python モジュール定数で保持しプロセス起動時に常駐。

---

## 3. 静的マスタデータ定義

`app/services/bus_pricing_master.py` に以下を定数として保持する。

### 3.1 運輸局ブロック別単価マスタ

各エントリは「公示下限の税別単価」「平均実働率」「交替運転者用の併用単価（車種共通）」を保持する。**`alt_dist_rate` / `alt_time_rate` はブロックごとに 1 組のみ**（車種別ではない点に注意。原設計書の表組では値が連結して見えていたが意味は単一値）。

```python
BLOCK_MASTER: dict[str, dict] = {
    "hokkaido": {
        "name": "北海道運輸局",
        "util_rate": 0.7143,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 6080, "medium": 5130, "small": 4500, "commuter": 4010},
        "alt_dist_rate": 10,
        "alt_time_rate": 2410,
    },
    "tohoku":            {"name": "東北運輸局",        "util_rate": 0.5810,
        "dist_rate": {"large": 180, "medium": 160, "small": 140, "commuter": 120},
        "time_rate": {"large": 7130, "medium": 6020, "small": 5270, "commuter": 4700},
        "alt_dist_rate": 20, "alt_time_rate": 2400},
    "kanto":             {"name": "関東運輸局",        "util_rate": 0.6758,
        "dist_rate": {"large": 170, "medium": 150, "small": 130, "commuter": 120},
        "time_rate": {"large": 7190, "medium": 6070, "small": 5320, "commuter": 4740},
        "alt_dist_rate": 40, "alt_time_rate": 2670},
    "hokuriku_shinetsu": {"name": "北陸信越運輸局",    "util_rate": 0.5833,
        "dist_rate": {"large": 160, "medium": 140, "small": 120, "commuter": 110},
        "time_rate": {"large": 7030, "medium": 5930, "small": 5190, "commuter": 4630},
        "alt_dist_rate": 20, "alt_time_rate": 2470},
    "chubu":             {"name": "中部運輸局",        "util_rate": 0.6645,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 7430, "medium": 6270, "small": 5490, "commuter": 4900},
        "alt_dist_rate": 30, "alt_time_rate": 2610},
    "kinki":             {"name": "近畿運輸局",        "util_rate": 0.5996,
        "dist_rate": {"large": 170, "medium": 140, "small": 120, "commuter": 110},
        "time_rate": {"large": 8040, "medium": 6790, "small": 5950, "commuter": 5300},
        "alt_dist_rate": 30, "alt_time_rate": 2480},
    "chugoku":           {"name": "中国運輸局",        "util_rate": 0.5943,
        "dist_rate": {"large": 200, "medium": 170, "small": 150, "commuter": 130},
        "time_rate": {"large": 6890, "medium": 5820, "small": 5090, "commuter": 4540},
        "alt_dist_rate": 30, "alt_time_rate": 2460},
    "shikoku":           {"name": "四国運輸局",        "util_rate": 0.5404,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 6940, "medium": 5860, "small": 5130, "commuter": 4570},
        "alt_dist_rate": 30, "alt_time_rate": 2420},
    "kyushu":            {"name": "九州運輸局",        "util_rate": 0.6285,
        "dist_rate": {"large": 150, "medium": 130, "small": 120, "commuter": 100},
        "time_rate": {"large": 6920, "medium": 5840, "small": 5110, "commuter": 4560},
        "alt_dist_rate": 10, "alt_time_rate": 2430},
    "okinawa":           {"name": "沖縄総合事務局",    "util_rate": 0.6278,
        "dist_rate": {"large": 210, "medium": 180, "small": 160, "commuter": 140},
        "time_rate": {"large": 5710, "medium": 4820, "small": 4220, "commuter": 3760},
        "alt_dist_rate": 30, "alt_time_rate": 2660},
}
```

### 3.2 車種・定員マスタ

```python
CAR_MASTER: dict[str, dict] = {
    "large":    {"label": "大型車",       "max_capacity": 60, "alt_seat_cost": 2, "alt_allowed": True},
    "medium":   {"label": "中型車",       "max_capacity": 27, "alt_seat_cost": 2, "alt_allowed": True},
    "small":    {"label": "小型車",       "max_capacity": 24, "alt_seat_cost": 0, "alt_allowed": False},
    "commuter": {"label": "コミューター", "max_capacity": 14, "alt_seat_cost": 0, "alt_allowed": False},
}
```

- `alt_allowed=False` の車種に対して `is_alternate_driver=True` または自動判定で交替必須となった場合は C-010 エラーで停止する。
- `alt_allowed=True` の場合、ツーマン運行時の実乗車可能定員は `max_capacity - alt_seat_cost`。

---

## 4. 入力仕様

### 4.1 入力パラメータ

DSTT 内では HTML フォームから受け取り、サービス層に dict として渡す。型・必須・範囲は同一。

| 物理名 | 型 | 必須 | 制約 | 説明 |
|---|---|---|---|---|
| `block_id` | str | ◯ | enum | `BLOCK_MASTER` のキー |
| `car_type` | str | ◯ | enum | `CAR_MASTER` のキー |
| `running_distance_km` | float | ◯ | `0 < D ≤ 2000` | 回送含む総走行距離 |
| `pax_running_distance_km` | float | ◯ | `0 ≤ Dp ≤ running_distance_km` | **実車距離**（回送を除く）。ワンマン判定で使用 |
| `running_duration_min` | int | ◯ | `0 < T ≤ 14400` | 出庫〜帰庫の総走行時間（分） |
| `steering_minutes` | int | ◯ | `0 ≤ Ts ≤ running_duration_min` | 実ハンドル時間（点呼・休憩を除く運転時間、分） |
| `pax_count` | int | ◯ | `1 ≤ P ≤ 100` | 乗車予定人数 |
| `is_night_run` | bool | ◯ | — | 22:00〜翌05:00 の運行があるか |
| `night_minutes` | int | △ | `0 ≤ N ≤ 14400` | 同時間帯の総拘束時間（`is_night_run` が真なら必須） |
| `is_overnight` | bool | ◯ | — | 宿泊運行か |
| `operating_days` | int | △ | `1 ≤ d ≤ 30` | **乗務日数**（運転士が運行・移動を行う日数。完全待機の中日は含めない）。`is_overnight=True` の場合必須 |
| `is_ferry_used` | bool | ◯ | — | フェリー航送あり |
| `ferry_duration_min` | int | △ | `0 ≤ Tf ≤ 1440` | 乗船時間（`is_ferry_used=True` で必須） |
| `ferry_rest_ok` | bool | △ | — | 乗船中に 8 時間以上の休息施設が確保できる計画か（控除可否） |
| `is_alternate_driver` | bool | ◯ | — | 交替運転者の明示配置 |
| `has_sleeper_bed` | bool | ◯ | — | 車内に運転者用仮眠ベッド（ASV 仕様等）があるか。ツーマン拘束 19h–20h 領域の判定で使用 |
| `is_annual_contract` | bool | ◯ | — | 年間契約特例 |
| `annual_operating_days` | int | △ | スクール時 `170 ≤ d ≤ 365` / それ以外は無視 | 年間契約日数。`is_annual_contract=True` かつ `is_school_bus=True` の場合のみ必須 |
| `is_school_bus` | bool | △ | — | スクールバス特例 |
| `agent_commission_rate` | float | ◯ | `0 ≤ R < 100` | 仲介手数料率（%）。100 は除外（除算不能のため） |
| `is_special_vehicle` | bool | ◯ | — | サロン等特殊仕様 |
| `special_vehicle_rate` | float | △ | `0 ≤ R ≤ 0.5` | 特殊仕様割増率 |

> **原設計書からの変更点**: `pax_running_distance_km` と `steering_minutes` を追加（ワンマン判定の精度確保）／`overnight_days` を `operating_days` に改名（宿泊日数と乗務日数の意味衝突解消）／`ferry_rest_ok` を追加（フェリー控除の前提条件チェック）／`has_sleeper_bed` を追加（C-005/C-006 で参照するため）。

### 4.2 相関バリデーション

| ID | ルール | 失敗時 |
|---|---|---|
| V-01 | `is_overnight=True` のとき `operating_days ≥ 2` | フォーム再表示＋エラー |
| V-02 | `is_ferry_used=True` のとき `ferry_duration_min > 0` | 同上 |
| V-03 | `is_night_run=True` のとき `night_minutes > 0` | 同上 |
| V-04 | `is_annual_contract=True` かつ `is_school_bus=True` のとき `annual_operating_days` が `170 ≤ d ≤ 365` を満たす。一般年間契約（`is_school_bus=False`）では `annual_operating_days` の入力値は使用せず、固定値 365 を採用する | 同上 |
| V-05 | `steering_minutes ≤ running_duration_min` | 同上 |
| V-06 | `pax_running_distance_km ≤ running_distance_km` | 同上 |
| V-07 | `agent_commission_rate < 100`（手数料 100% は不可） | 同上 |

エラー時は HTTP 200 でフォームを再描画し、画面上にエラーメッセージを表示する（DSTT の他ツール踏襲）。JSON API（§7.2）の場合のみ 400 で返す。

---

## 5. 計算アルゴリズム

実装は `bus_pricing_service.py` の純関数群とし、Blueprint からは `calculate(params: dict) -> CalcResult` を呼ぶ。

### 5.1 走行時間の端数処理

```
H_run         = running_duration_min / 60                # 入力時間
H_base        = max(3.0, H_run)                          # 3時間最低保証
H_drive       = H_base - excluded_ferry_hours            # §5.3 のフェリー控除を走行時間側に適用
H_drive       = max(0.0, H_drive)                        # 安全側：負値にしない
H_prep        = operating_days * 2.0  if is_overnight    # 各乗務日に出発前+帰着後の合計2時間
              else 2.0                                   # 単日運行は出庫1h+帰庫1h
H_gross_pre   = H_drive + H_prep                         # 拘束時間（監査・自動ツーマン判定で使用）
H_total       = round_half_up(H_gross_pre)               # 30分単位（0.5基準）四捨五入。基本運賃用
H_drive_unit  = round_half_up(H_drive)                   # 交替運転者料金で使う「走行時間のみ」の丸め値
```

| 変数 | 用途 |
|---|---|
| `H_drive` | フェリー控除済の純粋な走行時間。生値（小数あり）。`H_gross_pre` 算出と監査で使用 |
| `H_gross_pre` | 点検 2h を加えた**拘束時間**。改善基準告示の判定（15h/19h 等）に使う基準値 |
| `H_total` | 拘束時間を 30 分丸めしたもの。**時間制基本運賃 R_time の乗算対象** |
| `H_drive_unit` | 走行時間のみを 30 分丸めしたもの。**交替時間料金 R_alt_t の乗算対象**（点検 2h を含めない仕様のため） |

> **修正点**: 原設計書では `prep_hours = overnight_days * 2` としていたが、`overnight_days`（宿泊数）と「乗務日数」が混同されていた。1 泊 2 日（宿泊数 1、乗務日数 2）では `2×2=4h` が正しいため、入力名を `operating_days`（乗務日数）に統一して `operating_days × 2` とする。完全待機の中日は `operating_days` に含めない。

四捨五入は **0.5 基準の half-up**（通常の算数四捨五入）。Python 標準 `round()` は banker's rounding なので使用しない:

```python
from decimal import Decimal, ROUND_HALF_UP
def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

### 5.2 走行距離の端数処理

総走行距離を 10km 単位で**一括**切上げ（区間ごとに重ねない）。

```
D_total = ceil(running_distance_km / 10) * 10
```

### 5.3 フェリー乗船時間の控除

控除可能な条件: `is_ferry_used=True` **かつ** `ferry_rest_ok=True`（船内で 8 時間以上の連続休息施設が確保できる計画）。

```
if is_ferry_used and ferry_rest_ok:
    excluded_ferry_hours = min(8.0, ferry_duration_min / 60)
else:
    excluded_ferry_hours = 0
```

控除値は §5.1 の `H_gross` から差し引く。**乗船時間が 8 時間を超えても、控除上限は 8 時間**（残超過分は拘束として残る）。原設計書では「8 時間を超えた分を除外」と逆向きの記述があったが、公示の趣旨は「最大 8 時間まで控除可」であり、本設計はそれに従う。

### 5.4 ワンマン運行可否の判定

以下のいずれかに該当する場合、`auto_alternate_driver = True` として交替運転者料金を内部適用する。

| ID | 条件 | 根拠 |
|---|---|---|
| A-01 | `H_gross_pre > 15.0`（拘束時間 15h 超） | 改善基準告示 ワンマン拘束上限。13h は警告（C-004）に留め、自動切替は 15h 超で発動 |
| A-02 | `is_night_run=False` かつ `pax_running_distance_km > 500` | 昼間ワンマン実車距離 500km |
| A-03 | `is_night_run=True` かつ `pax_running_distance_km > 400` | 夜間ワンマン実車距離 400km |
| A-04 | `steering_minutes / 60 > 9.0` | 1 日の実運転時間 9h 上限 |

> **修正点**: 原設計書では自動切替閾値が `H_gross > 13.0` だったが、これは C-003/C-004 の整合性を欠く（13h は「警告」だが自動切替してしまうと警告自体が出ない）。15h 超で自動切替、13h〜15h は警告のみとする。距離判定も**実車距離**（`pax_running_distance_km`）を使用するよう修正。

連続運転時間（4h ルール）は入力分割なしでは厳密判定不能なため、C-001 の監査は「`steering_minutes` が 240 を超え、かつ休憩時間（= `running_duration_min - steering_minutes`）が 30 分未満」という近似で警告する（厳密化は将来拡張）。

### 5.5 基本運賃（時間距離併用制）

```
R_dist = BLOCK_MASTER[block_id]["dist_rate"][car_type]
R_time = BLOCK_MASTER[block_id]["time_rate"][car_type]
F_base = H_total * R_time + D_total * R_dist
```

### 5.6 深夜割増（22:00–05:00）

```
H_night = round_half_up(night_minutes / 60)
F_night = H_night * R_time * 0.20
```

### 5.7 交替運転者配置料金

`is_alternate_driver=True` または `auto_alternate_driver=True` のとき:

```
R_alt_d  = BLOCK_MASTER[block_id]["alt_dist_rate"]
R_alt_t  = BLOCK_MASTER[block_id]["alt_time_rate"]
F_alter        = D_total * R_alt_d + H_drive_unit * R_alt_t   # 点検2hは加算しない（走行時間のみ）
F_alter_night  = H_night * R_alt_t * 0.20                     # 深夜帯がある場合のみ
F_alter_total  = F_alter + F_alter_night
```

- **走行時間も 30 分丸め後の `H_drive_unit` を使用**する（原設計書では未丸めの `H_base` を使っており、本則の 30 分単位丸めと不整合だったため統一）。
- フェリー控除も `H_drive_unit` 経由で反映される（基本運賃側と同じ控除を交替運転者側にも適用、二重控除はしない）。
- `alt_allowed=False`（small / commuter）の車種でツーマンが必要になった場合は C-010 エラーで停止し、料金加算は行わない。

### 5.8 特殊車両割増

```
F_special = F_base * min(0.5, special_vehicle_rate)
```

### 5.9 年間契約（期間契約特例）

```
C_daily = F_base + F_night + F_alter_total + F_special   # その日の税別下限額
E_rate  = BLOCK_MASTER[block_id]["util_rate"]

if is_school_bus:
    D_calc = annual_operating_days                       # スクールは契約運行日数を直接使用
else:
    D_calc = floor(365 * E_rate)                         # 一般は365日に実働率を掛けて推定

F_annual_limit_total = C_daily * D_calc
Max_Run_Days         = floor(D_calc * 1.4)
```

> **修正点**: 原設計書は school bus 時も `floor(annual_operating_days × util_rate)` としていたが、これは二重補正で誤り。スクールバスは契約運行日数自体が確定値（年 200 日など）なので、実働率を再度掛けるべきではない。一般契約のみ「365 日 × 実働率」で想定稼働日を推定する。

実運行日数が `Max_Run_Days` を超過した場合、超過日 1 日ごとに通常日割計算で別途精算する旨を出力（実際の超過判定は別の運行データ照合が必要なので、本ツールでは上限値の提示にとどめる）。

### 5.10 仲介手数料による下限額のアップリフト

本ツールは「公示**下限**額」そのものを算定する。下限額に等しい契約金額で運行する場合、仲介手数料を差し引くと必ず下限割れする（道路運送法第 10 条「不当な割り戻し」の構造的問題）。したがって、手数料率が 0% より大きい場合は、提示すべき**最低契約金額**を以下のように引き上げる:

```
F_total_ex_tax  = F_base + F_night + F_alter_total + F_special
F_tax_amount    = round_half_up(F_total_ex_tax * 0.10)            # 消費税
F_legal_inc_tax = F_total_ex_tax + F_tax_amount                   # 公示税込下限額

# 手数料アップリフト：差引後にちょうど F_legal_inc_tax 以上が残る最小総額
if agent_commission_rate > 0:
    F_recommended = F_legal_inc_tax / (1 - agent_commission_rate / 100)
else:
    F_recommended = F_legal_inc_tax
```

`F_legal_inc_tax` は法定下限、`F_recommended` は「手数料控除後にも下限を下回らないために事業者が提示すべき最低総額」を意味する。両者は別フィールドとしてレスポンスに返す。

> **修正の背景**: 原設計書は「Net_Amount < F_legal_min なら C-011 違反」というロジックだったが、本ツールが算定するのは下限額そのものなので、手数料 > 0 のとき C-011 が**必ず**発火し、運用上意味をなさない。下限額自体を必要分だけ持ち上げる方式に改めた。C-011 は「事業者が外部入力した実提示額がアップリフト後の下限を下回る場合」に限り発火する将来拡張とし、現バージョンでは未実装とする（§6 のテーブルも更新）。

### 5.11 千円単位の請求丸め

商慣習として請求書は 1,000 円単位で発行されるため、丸め後の請求額が下限を割らない最小の 1,000 円倍数を求める。「切り捨てが下限割れを起こすか」のガード分岐は、下限額そのものを返す本ツールでは常に「下限割れ側」になるため意味をなさない。実装は**切り上げ一択**で十分:

```
F_billing = ceil(F_recommended / 1000) * 1000
```

これにより、`F_billing ≥ F_recommended ≥ F_legal_inc_tax` が常に保証される。

> **修正の背景**: 原設計書 §5.11 の条件分岐は `F_legal_min_inc_tax = F_total_inc_tax` 前提では恒に else 側に倒れる（floor は必ず inc_tax を下回る、例外は inc_tax がちょうど 1,000 の倍数の場合のみ）。実害は出ないが「常に発火しない if」は実装上の混乱を招くため削除。事業者が**任意上乗せ後の提示額**を入力できるオプション項目を将来追加した場合に、再度ガード分岐が意味を持つ。

---

## 6. コンプライアンス監査チェック

| ID | 名称 | トリガ | レベル |
|---|---|---|---|
| C-001 | 連続運転時間 | `steering_minutes > 240` かつ `running_duration_min - steering_minutes < 30` | error |
| C-002 | 高速道路連続 | 入力に高速比率がないため**実装は将来拡張**（入力追加時に有効化） | — |
| C-003 | ワンマン拘束上限 | `is_alternate_driver=False` かつ `auto_alternate_driver=False` かつ `H_gross_pre > 15.0` | error |
| C-004 | ワンマン拘束警告 | `is_alternate_driver=False` かつ `13.0 < H_gross_pre ≤ 15.0` | warning |
| C-005a | ツーマン拘束絶対上限 | ツーマン時 `H_gross_pre > 20.0` | error |
| C-005b | ツーマン拘束上限（ベッドなし） | ツーマン時 `H_gross_pre > 19.0` かつ `has_sleeper_bed=False` | error |
| C-006 | 車内ベッド要件警告 | ツーマン時 `19.0 < H_gross_pre ≤ 20.0` かつ `has_sleeper_bed=True` | warning |
| C-007 | 勤務間休息下限 | （入力に前日・翌日情報なし）将来拡張 | — |
| C-008 | 勤務間休息推奨 | 同上 | — |
| C-009 | ツーマン時定員不足 | ツーマン適用かつ `pax_count > max_capacity - alt_seat_cost`（large/medium のみ） | error |
| C-010 | ツーマン不可車種 | `car_type ∈ {small, commuter}` かつ ツーマン必要 | error |
| C-011 | 手数料割戻し違反 | 外部入力された実提示額 `external_quote_inc_tax` が `F_recommended` を下回る場合（**将来拡張**：現バージョンでは入力項目を持たないため発火しない。手数料による下限額の引き上げは §5.10 のアップリフトで吸収する） | — |

> **修正点**: C-002／C-007／C-008 は本ツールの入力モデルだけでは厳密判定できない（路線種別や前後日の勤務情報が必要）。将来拡張として残し、現バージョンでは未実装である旨を画面上の注記に明示する。

監査結果は `errors`/`warnings` のリストで返し、画面上では赤/黄のバナーで表示する。`errors` が 1 件でもあれば「請求可能金額」の欄は表示せず、**是正してください**メッセージのみ表示する（ガードレール）。

---

## 7. インターフェース仕様

### 7.1 画面（HTML フォーム）

`/tools/bus_pricing` GET でフォームを表示、POST で同画面に計算結果を埋め込んで再描画する。DSTT の他ツール（datecalc / calc 等）と同じ構造。

画面構成:

1. **入力カード**: ブロック・車種・距離・時間・人数・各種フラグ
2. **計算内訳カード**: 基本運賃／深夜割増／交替運転者／特殊車両／税抜合計／消費税／税込合計／請求丸め後
3. **コンプライアンス監査カード**: エラー（赤）・警告（黄）の一覧
4. **年間契約特例カード**: 適用時のみ、想定稼働日数・年間下限総額・許容運行日数・割引効率

### 7.2 内部 JSON API（任意）

外部システム連携用に **同 Blueprint 配下** に JSON エンドポイントを 1 本だけ用意する（独立サーバーは立てない）:

| メソッド | URL | 用途 |
|---|---|---|
| POST | `/tools/bus_pricing/api/calculate` | JSON で同一ロジックを呼ぶ |

`@login_required` を維持し、`Content-Type: application/json` のみ受け付ける。CSRF 対策として、書き込み系ではないが Origin/Referer 同一オリジン検査を `app/__init__.py` の `_SAME_ORIGIN_PATH_PREFIXES` に追加することを推奨。

### 7.3 レスポンス JSON スキーマ

下の例は §7.2 の入力（kanto / large / 300km / 540min / 夜間 120min / 手数料 10%）に対する**実ロジック通りの**算定結果である。各数値は §5.1–§5.11 のアルゴリズムを手計算で検証済み。

```json
{
  "status": "success",
  "meta": {
    "timestamp": "2026-05-21T17:48:00+09:00",
    "block_id": "kanto",
    "block_name": "関東運輸局",
    "car_type": "large"
  },
  "applied_rates": {
    "dist_unit_yen_per_km": 170,
    "time_unit_yen_per_hour": 7190,
    "alt_dist_unit_yen_per_km": 40,
    "alt_time_unit_yen_per_hour": 2670
  },
  "calc_factors": {
    "raw_running_hours": 9.0,
    "ferry_excluded_hours": 0.0,
    "drive_hours_unrounded": 9.0,
    "prep_hours": 2.0,
    "gross_hours_pre_round": 11.0,
    "final_calc_hours": 11,
    "drive_calc_hours": 9,
    "final_calc_distance_km": 300,
    "night_calc_hours": 2,
    "auto_alternate_driver_applied": false,
    "alternate_driver_required": false
  },
  "calculation_breakdown": {
    "base_distance_fare": 51000,
    "base_time_fare": 79090,
    "base_fare_subtotal": 130090,
    "night_surcharge": 2876,
    "alternate_driver_fare": 0,
    "special_vehicle_surcharge": 0,
    "raw_total_excluding_tax": 132966,
    "consumption_tax": 13297,
    "legal_minimum_including_tax": 146263,
    "recommended_billable_including_tax": 162515,
    "approved_rounded_billing_fare": 163000
  },
  "compliance_audit": {
    "has_errors": false,
    "has_warnings": false,
    "audit_logs": []
  },
  "annual_contract_special": {
    "is_applied": false,
    "daily_rate_used": 0,
    "standard_utilization_rate": 0,
    "calculated_operating_days": 0,
    "annual_limit_subtotal": 0,
    "annual_limit_including_tax": 0,
    "max_run_days_allowed": 0,
    "discount_efficiency_percentage": 0
  }
}
```

数値検算:

- `base_distance_fare`: 300 × 170 = **51,000**
- `base_time_fare`: 11 × 7,190 = **79,090**
- `night_surcharge`: 2 × 7,190 × 0.20 = **2,876**
- `raw_total_excluding_tax`: 51,000 + 79,090 + 2,876 = **132,966**
- `consumption_tax`: round_half_up(132,966 × 0.10) = round_half_up(13,296.6) = **13,297**
- `legal_minimum_including_tax`: 132,966 + 13,297 = **146,263**（公示税込下限）
- `recommended_billable_including_tax`: 146,263 / (1 − 0.10) = 162,514.44… を1円単位 ceil → **162,515**（仲介手数料 10% 控除後も下限を割らないために必要な最低総額）
- `approved_rounded_billing_fare`: ceil(162,515 / 1000) × 1000 = **163,000**

> **修正点**: 原設計書のキー `annual_contract_特例` は ASCII 化（`annual_contract_special`）／`audit_logs` の値欠落を修正／`approved_rounded_billing_fare` を原例の 146,000 から、§5.10–§5.11 のロジックに沿った **163,000** に修正（原例は手数料アップリフトを無視し、かつ千円切り捨てで下限を割っており、二重に誤りだった）／`legal_minimum_including_tax` と `recommended_billable_including_tax` を追加し、下限額と推奨提示額を分離して可視化。

### 7.4 エラーレスポンス

入力バリデーションエラー時:

- HTML: 同フォームをエラーメッセージ付きで再描画（HTTP 200）
- JSON API: HTTP 400、`{"status":"error","errors":[{"field":"...","message":"..."}]}`

サーバ内部例外時はログを残し HTTP 500 を返す（DSTT のエラーハンドラに従う）。

---

## 8. 実装スケルトン（Python）

### 8.1 `app/services/bus_pricing_service.py`（抜粋）

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .bus_pricing_master import BLOCK_MASTER, CAR_MASTER


class BusPricingInputError(ValueError):
    def __init__(self, field_name: str, message: str):
        super().__init__(message)
        self.field = field_name
        self.message = message


def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def yen_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class AuditEntry:
    audit_id: str
    level: str   # "error" | "warning"
    message: str


@dataclass
class CalcResult:
    applied_rates: dict
    calc_factors: dict
    calculation_breakdown: dict
    audit_logs: list[AuditEntry] = field(default_factory=list)
    annual_contract: dict = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(a.level == "error" for a in self.audit_logs)

    @property
    def has_warnings(self) -> bool:
        return any(a.level == "warning" for a in self.audit_logs)


def calculate(params: dict[str, Any]) -> CalcResult:
    block = BLOCK_MASTER[params["block_id"]]
    car = CAR_MASTER[params["car_type"]]

    # --- 5.1 / 5.3 時間処理 + フェリー控除 -------------------------------
    h_run = params["running_duration_min"] / 60.0
    h_base = max(3.0, h_run)

    excluded_ferry = 0.0
    if params["is_ferry_used"] and params.get("ferry_rest_ok"):
        excluded_ferry = min(8.0, params["ferry_duration_min"] / 60.0)
    h_drive = max(0.0, h_base - excluded_ferry)

    if params["is_overnight"]:
        h_prep = params["operating_days"] * 2.0
    else:
        h_prep = 2.0
    h_gross_pre = h_drive + h_prep

    h_total = round_half_up(h_gross_pre)        # 拘束時間 → 30分丸め（時間制基本運賃用）
    h_drive_unit = round_half_up(h_drive)       # 走行時間のみ → 30分丸め（交替時間料金用）

    # --- 5.2 距離処理 -----------------------------------------------------
    d_total = math.ceil(params["running_distance_km"] / 10.0) * 10

    # --- 5.4 ワンマン可否 -------------------------------------------------
    auto_alt = False
    if h_gross_pre > 15.0:
        auto_alt = True
    if not params["is_night_run"] and params["pax_running_distance_km"] > 500:
        auto_alt = True
    if params["is_night_run"] and params["pax_running_distance_km"] > 400:
        auto_alt = True
    if params["steering_minutes"] / 60.0 > 9.0:
        auto_alt = True
    is_two_man = params["is_alternate_driver"] or auto_alt

    # --- 5.5 基本運賃 -----------------------------------------------------
    r_dist = block["dist_rate"][params["car_type"]]
    r_time = block["time_rate"][params["car_type"]]
    f_base_dist = d_total * r_dist
    f_base_time = h_total * r_time
    f_base = f_base_dist + f_base_time

    # --- 5.6 深夜割増 -----------------------------------------------------
    f_night = 0
    h_night = 0
    if params["is_night_run"] and params.get("night_minutes", 0) > 0:
        h_night = round_half_up(params["night_minutes"] / 60.0)
        f_night = yen_half_up(h_night * r_time * 0.20)

    # --- 5.7 交替運転者料金 -----------------------------------------------
    f_alt = 0
    if is_two_man and car["alt_allowed"]:
        f_alt = d_total * block["alt_dist_rate"] + yen_half_up(h_drive_unit * block["alt_time_rate"])
        if h_night > 0:
            f_alt += yen_half_up(h_night * block["alt_time_rate"] * 0.20)
    # alt_allowed=False の場合は C-010 監査が error を吐く（料金は加算しない）

    # --- 5.8 特殊車両 -----------------------------------------------------
    f_special = 0
    if params["is_special_vehicle"]:
        rate = min(0.5, params.get("special_vehicle_rate", 0.0))
        f_special = yen_half_up(f_base * rate)

    # --- 税計算 -----------------------------------------------------------
    raw_ex_tax = f_base + f_night + f_alt + f_special
    tax = yen_half_up(raw_ex_tax * 0.10)
    legal_inc_tax = raw_ex_tax + tax

    # --- 5.10 仲介手数料アップリフト -------------------------------------
    commission_rate = params["agent_commission_rate"]
    if commission_rate > 0:
        recommended_inc_tax = math.ceil(legal_inc_tax / (1 - commission_rate / 100))
    else:
        recommended_inc_tax = legal_inc_tax

    # --- 5.11 千円単位丸め（切り上げ一択） -------------------------------
    billing = math.ceil(recommended_inc_tax / 1000) * 1000

    # --- 監査 -------------------------------------------------------------
    audits = run_compliance_audits(
        params=params,
        car=car,
        is_two_man=is_two_man,
        auto_alt=auto_alt,
        h_gross_pre=h_gross_pre,
        steering_minutes=params["steering_minutes"],
        running_minutes=params["running_duration_min"],
    )

    # --- 5.9 年間契約 -----------------------------------------------------
    annual = {}
    if params["is_annual_contract"]:
        c_daily = raw_ex_tax
        e_rate = block["util_rate"]
        if params.get("is_school_bus"):
            d_calc = params["annual_operating_days"]
        else:
            d_calc = math.floor(365 * e_rate)
        annual_ex = c_daily * d_calc
        annual_inc = yen_half_up(annual_ex * 1.10)
        # 1.4倍運行ルールを最大限活用した場合の実効値引き率
        # = 1 − (D_calc / Max_Run_Days) = 1 − 1/1.4 ≈ 28.6%（規程定数）
        discount_pct = round((1 - 1 / 1.4) * 1000) / 10
        annual = {
            "is_applied": True,
            "daily_rate_used": c_daily,
            "standard_utilization_rate": e_rate,
            "calculated_operating_days": d_calc,
            "annual_limit_subtotal": annual_ex,
            "annual_limit_including_tax": annual_inc,
            "max_run_days_allowed": math.floor(d_calc * 1.4),
            "discount_efficiency_percentage": discount_pct,
        }

    return CalcResult(
        applied_rates={
            "dist_unit_yen_per_km": r_dist,
            "time_unit_yen_per_hour": r_time,
            "alt_dist_unit_yen_per_km": block["alt_dist_rate"],
            "alt_time_unit_yen_per_hour": block["alt_time_rate"],
        },
        calc_factors={
            "raw_running_hours": h_run,
            "ferry_excluded_hours": excluded_ferry,
            "drive_hours_unrounded": h_drive,
            "prep_hours": h_prep,
            "gross_hours_pre_round": h_gross_pre,
            "final_calc_hours": h_total,
            "drive_calc_hours": h_drive_unit,
            "final_calc_distance_km": d_total,
            "night_calc_hours": h_night,
            "auto_alternate_driver_applied": auto_alt,
            "alternate_driver_required": is_two_man,
        },
        calculation_breakdown={
            "base_distance_fare": f_base_dist,
            "base_time_fare": f_base_time,
            "base_fare_subtotal": f_base,
            "night_surcharge": f_night,
            "alternate_driver_fare": f_alt,
            "special_vehicle_surcharge": f_special,
            "raw_total_excluding_tax": raw_ex_tax,
            "consumption_tax": tax,
            "legal_minimum_including_tax": legal_inc_tax,
            "recommended_billable_including_tax": recommended_inc_tax,
            "approved_rounded_billing_fare": billing,
        },
        audit_logs=audits,
        annual_contract=annual,
    )
```

`run_compliance_audits()` で §6 のチェックを順次評価し `AuditEntry` を append する。

### 8.2 `app/tools/bus_pricing.py`（抜粋）

```python
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from app.services.bus_pricing_service import (
    BusPricingInputError,
    calculate,
)

bus_pricing_bp = Blueprint("bus_pricing", __name__, url_prefix="/tools/bus_pricing")


@bus_pricing_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form_state = build_default_form_state()
    result = None
    errors: list[dict] = []
    if request.method == "POST":
        update_form_state_from_request(request.form, form_state)
        try:
            params = coerce_form_to_params(form_state)
            result = calculate(params)
        except BusPricingInputError as exc:
            errors.append({"field": exc.field, "message": exc.message})
    return render_template(
        "bus_pricing.html",
        form_state=form_state,
        result=result,
        errors=errors,
    )


@bus_pricing_bp.route("/api/calculate", methods=["POST"])
@login_required
def api_calculate():
    payload = request.get_json(silent=True) or {}
    try:
        params = validate_api_params(payload)
        result = calculate(params)
    except BusPricingInputError as exc:
        return jsonify({"status": "error", "errors": [{"field": exc.field, "message": exc.message}]}), 400
    return jsonify(serialize_result(result))
```

### 8.3 テスト（`tests/test_bus_pricing_service.py`）

最低限カバーするケース:

- 関東・大型・基本ケース（§7.2 の入力 → §7.3 のゴールデン値完全一致）
- 3 時間最低保証の境界（`H_run`=2.9h → `H_base`=3.0h）
- 距離 10km 切上げ境界（41.2km → 50km、50.0km → 50km、50.1km → 60km）
- 拘束時間の 30 分単位丸め（11.49h → 11、11.50h → 12）
- ワンマン自動切替 4 系統:
  - 拘束 15.1h（`H_gross_pre > 15`）
  - 昼間实车 501km
  - 夜間实车 401km
  - 実運転 9.1h
- 各車種のツーマン時定員チェック（C-009）:
  - large: 乗客 58 名 → OK、59 名 → error（60 − 2 = 58 上限）
  - medium: 乗客 25 名 → OK、26 名 → error（27 − 2 = 25 上限）
- 小型／コミューターのツーマン要求 → C-010 error
- 拘束時間境界:
  - ワンマン 12.9h → 警告なし、13.1h → C-004 警告、15.1h → C-003 error
  - ツーマン 18.9h → 警告なし、19.1h かつ `has_sleeper_bed=False` → C-005b error、19.1h かつ `True` → C-006 警告、20.1h → C-005a error
- 連続運転（C-001）: `steering_minutes`=300 かつ休憩時間 = 240−300 < 0 はバリデーション V-05 で弾く。`steering_minutes`=240 で休憩 < 30 分 → C-001 警告
- 年間契約:
  - 一般（kanto, util_rate=0.6758）→ `D_calc`=floor(365×0.6758)=246、`Max_Run_Days`=floor(246×1.4)=344
  - スクール（`annual_operating_days`=200）→ `D_calc`=200、`Max_Run_Days`=floor(200×1.4)=280
- 仲介手数料アップリフト:
  - 0% → `recommended_inc_tax == legal_inc_tax`
  - 10% → `recommended_inc_tax = ceil(legal_inc_tax / 0.9)`
  - 50% → `recommended_inc_tax = ceil(legal_inc_tax / 0.5)`
- 千円丸め: `recommended_inc_tax`=162,515 → `billing`=163,000
- フェリー: `is_ferry_used=False` → 控除 0；`True` かつ `ferry_rest_ok=False` → 控除 0；`True`/`True` かつ 9h → 控除 8h（上限）；`True`/`True` かつ 5h → 控除 5h

---

## 9. 既存機能との関係 / 注意事項

- `app/__init__.py` への Blueprint 登録のみで利用可能（DB マイグレーション不要）。
- 監査結果が `error` を含む場合は「請求可能金額」を画面に表示しない（DSTT のガードレール思想を踏襲）。
- 公示単価および実働率は法令改正で変動するため、`BLOCK_MASTER` は**版数コメント**（例: `# 適用: 令和7年11月1日改定`）を必ず付ける。改定時は単体テストのゴールデン値も併せて更新する。
- 本ツールは「下限額計算」を行うが、**実際の請求金額**は事業者の判断で上乗せ可能（下限以上であればよい）。画面上には「公示下限額」「推奨提示額（任意上乗せ後）」を分けて表示する余地を残す。

---

## 10. 原設計書からの主な変更点まとめ

| # | 箇所 | 変更内容 |
|---|---|---|
| 1 | 全体構成 | 単独 Flask API → DSTT 標準の Blueprint + HTML フォーム + サービス層分離 |
| 2 | エンドポイント | `/api/v1/calculate` → `/tools/bus_pricing`（任意で `/tools/bus_pricing/api/calculate`） |
| 3 | §3.1 マスタ | `alt_dist_rate` / `alt_time_rate` を車種共通の単一値として明示。表の連結誤読を回避 |
| 4 | §4 入力 | `overnight_days`（宿泊数）を `operating_days`（乗務日数）に改名／`pax_running_distance_km` `steering_minutes` `ferry_rest_ok` を追加 |
| 5 | §5.1 | 点検時間 `operating_days × 2` に修正（宿泊数ではなく乗務日数基準）／`round()` のバンカーズ丸めを避けて `Decimal` の half-up を採用 |
| 6 | §5.3 | フェリー控除を「控除上限 8h」として正しい方向で再定義／前提条件 `ferry_rest_ok` を導入 |
| 7 | §5.4 | 自動ツーマン切替閾値を 13h → 15h に修正（13h は警告のみで C-004 と整合）／距離判定を**実車距離**に変更 |
| 8 | §5.9 | スクールバスの `D_calc` 二重補正を修正（`annual_operating_days` をそのまま使用） |
| 9 | §6 | C-002/C-007/C-008 は現入力モデルでは厳密判定不能なため将来拡張と明記／C-009 は `alt_allowed=True` の車種限定 |
| 10 | §7 JSON | キー名 ASCII 化（`annual_contract_特例` → `annual_contract_special`）／`audit_logs` 値欠落の修正 |
| 11 | §8 擬似コード | `false` → `False`、`audit_logs =` → `audit_logs = []`、純関数 + dataclass による DSTT 風の構造に書き換え |
| 12 | C-011 比較対象 | 「同条件下の税込下限額」を明確化（= 算定結果そのもの） |
| 13 | §5.10 仲介手数料 | 「Net < 下限 → C-011 違反」方式は手数料 > 0 で必発火し運用不能のため廃止。代わりに `F_recommended = F_legal / (1 − rate/100)` のアップリフト計算に変更。C-011 は外部入力された実提示額の照合用として将来拡張に降格 |
| 14 | §5.11 千円丸め | `F_legal_min = F_total_inc_tax` 前提では分岐が常に else に倒れるため切り上げ一択に簡素化 |
| 15 | §5.1 / §5.7 時間処理 | フェリー控除を `H_drive`（走行時間）側に適用するよう順序を整理。交替時間料金で使う時間も `H_drive_unit`（30 分丸め）に統一し、原設計書の「未丸め生値」を排除 |
| 16 | C-005 / C-006 | 「ベッド有無」を参照するのに入力フィールドが存在しなかった矛盾を解消。`has_sleeper_bed` を入力スキーマに追加。C-005 を 20h 絶対上限 / 19h ベッドなしに分割（C-005a / C-005b） |
| 17 | §7.3 数値例 | 原例の `approved_rounded_billing_fare: 146000` および `audit_logs: []` はロジック上不可能だったため、検算ベースで 163,000 と `[]`（手数料は C-011 ではなくアップリフトに吸収）に修正 |
| 18 | §4.2 V-04 | 非スクール年間契約は固定 365 を使う点を明文化。`annual_operating_days` の必須化はスクールのみに限定 |
| 19 | §8.1 軽微 | `BusPricingInputError(field, ...)` の引数名が `dataclasses.field` と衝突するため `field_name` にリネーム |
| 20 | テスト | 境界値・カバレッジを補強し、§7.3 の検算値をゴールデン値として固定 |
