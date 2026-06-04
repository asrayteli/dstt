# 健診PLUS（health_check）設計書

健康診断の進捗を「予約 → 受診 → 再検査 → 二次検査完了」まで抜け漏れなく追跡する、
社員名簿PLUS連携の健康診断管理ツール。本書は実装着手前の合意ドキュメント（決定反映済み）。

- 表示名：**健診PLUS**
- 内部キー / Blueprint：`health_check`（`/tools/health_check`、API は `/tools/health_check/api/...`）
- 系統：PLUS家系（社員名簿PLUS／現場リストPLUSと同じくマスタ管理）
- アクセス区分：**sensitive**（健康情報＝要配慮個人情報）。さらに**健診PLUS独自の狭い権限**で運用

---

## 1. 確定済みの方針（決定事項）

| 論点 | 決定 |
|---|---|
| ツール名 | 健診PLUS（key `health_check`） |
| レコード単位 | **対象者 × 健診年度で毎年1レコード**（年度履歴を保持） |
| 健診年度 | **年度＝4月開始**（4月〜翌3月）。集計・繰り越しの基準 |
| 専従先名 | 社員名簿PLUSの **`company_name`（法人名）** を同期（手動上書き可） |
| 受診日② | **深夜従事者の年2回目受診日**（深夜業は年2回健診）。ラベルは「受診日②（深夜従事者）」 |
| 二次検査ToBell | 推奨日入力で**自動起票**。**推奨日当日9:00** に通知。**全体既定リードタイム＋レコード個別上書き**の追加通知も可 |
| 深夜2回目ToBell | **受診日②（年2回目）も**未受診ならToBellリマインド対象 |
| ToBell担当者 | `manager_name`をDSTTユーザー表示名に**自動紐付け（表記ゆれ吸収）**、手動上書き可、**未紐付け一覧**を提供 |
| ToBell有効化 | **各自オプトイン必須**（既定OFF。設定→DSTT連携で有効化） |
| 名簿一括起票 | 対象年度に**在籍者全員を一括起票** |
| 閲覧範囲 | **健診PLUS独自の狭い権限**。管理者＝**DSTT管理者＋個別付与** |
| 添付 | **pdf/jpg/png・1ファイル10MB**まで |

---

## 2. 対象者と3モード（record_type）

| モード | record_type | 説明 | 名簿リンク |
|---|---|---|---|
| 名簿連携 | `linked` | 社員名簿PLUS（`Employee`）にいる現職社員 | あり（`employee_id`） |
| 入社前 | `pre_hire` | まだ名簿に載らない採用予定者の入社前健診 | なし。後で昇格可 |
| 内勤者 | `internal` | 名簿PLUSに載らない内勤・嘱託等 | なし |

- **入社前 → 名簿連携への昇格**：入社して名簿に載ったら `employee_id` を結び付けて
  `pre_hire` → `linked` に昇格（健診履歴を引き継ぐ）。
- 手動モード（pre_hire/internal）の社員番号は空欄可。

---

## 3. データモデル

`app/models.py` の規約（`id`/`created_at`/`updated_at`、JST `jst_now()`）に準拠。
スキーマ追加は既存の `app/__init__.py: _ensure_access_control_schema()` の動的 ALTER 方式に乗せる。

### 3.1 HealthCheckRecord（健診レコード本体）

`__tablename__ = 'health_check_records'`

| カラム | 型 | 出どころ（linked） | 説明 / 項目対応 |
|---|---|---|---|
| `id` | Integer PK | — | 主キー |
| `target_year` | Integer | 手入力(必須) | 対象健診年度（年度=4月開始。例 2026＝2026/4〜2027/3） |
| `record_type` | String(20) | — | `linked`/`pre_hire`/`internal` |
| `employee_id` | Integer FK→employees.id (null可) | — | 名簿リンク（linked時のみ） |
| `employee_number` | String(20) | 名簿同期 | ① 社員番号 |
| `employee_name` | String(100) | 名簿同期 | ② 社員名（手動モード必須） |
| `employee_type` | String(50) | 名簿 `employee_type` 同期 | ③ 社員区分 |
| `assignment_site` | String(200) | 名簿 **`company_name`** 同期 | ④ 専従先名（手動上書き可） |
| `manager_name` | String(100) | 名簿 `manager_name` 同期 | ⑤ 管理担当名 |
| `manager_user` | String(80) (null可) | 自動/手動 | ⑤の担当をDSTTユーザー(username)に解決（ToBell宛先） |
| `hire_date` | Date | 名簿 `hire_date` 同期 | ⑥ 入社日付（入社前は予定日） |
| `retirement_date` | String(50) | 名簿 `retirement_date` 同期 | ⑦ 退職日（名簿が文字列のため踏襲） |
| `reservation_date` | Date | 手入力 | ⑧ 予約日 |
| `exam_date` | Date | 手入力 | ⑨ 受診日 |
| `exam_date_2` | Date | 手入力 | ⑩ 受診日②（深夜従事者の年2回目） |
| `exam_date_2_target` | Date (null可) | 手入力/既定 | ⑩のリマインド基準日（未入力時 受診日＋6か月を既定） |
| `medical_institution` | String(200) | 手入力 | ⑪ 受診医療機関名 |
| `is_night_worker` | Boolean | 手入力 | ⑫ 深夜従事者（深夜業特殊健診対象） |
| `needs_recheck` | Boolean | 手入力 | ⑬ 再検査有無 |
| `recheck_items` | Text | 手入力 | ⑭ 再検査項目 |
| `secondary_recommended_date` | Date | 手入力 | ⑮ 二次検査受診推奨日 ← **ToBell発火日** |
| `secondary_exam_date` | Date | 手入力 | ⑯ 二次検診受信日（＝受診日） |
| `secondary_guide_sent_date` | Date | 手入力 | ⑰ 二次検査案内送付日 |
| `secondary_result` | Text | 手入力 | ⑱ 二次検査結果 |
| `remarks` | Text | 手入力 | ⑲ 備考 |
| `reminder_lead_days` | Integer (null可) | 手入力 | 追加事前通知のリードタイム（全体既定を個別上書き。§6.2） |
| `status` | String(20) | 自動算出（キャッシュ） | 受診ステータス（§5） |
| `secondary_task_id` | Integer (null可) | システム | 二次検査ToBellタスクID（更新/クローズ用） |
| `night2_task_id` | Integer (null可) | システム | 深夜2回目ToBellタスクID |
| `created_at` / `updated_at` | DateTime | — | 監査用 |

**一意制約**：`linked` は `UNIQUE(target_year, employee_id)`。
`pre_hire`/`internal` は `id` で識別（同名重複は警告表示）。

### 3.2 HealthCheckAttachment（スキャン結果の添付）

`__tablename__ = 'health_check_attachments'`

| カラム | 型 | 説明 |
|---|---|---|
| `id` | Integer PK | |
| `record_id` | Integer FK→health_check_records.id | 紐付くレコード |
| `stored_path` | String(500) | 保存パス `uploads/health_check/<年度>/<uuid>.<ext>` |
| `original_name` | String(255) | 元ファイル名（`secure_filename`） |
| `content_type` | String(100) | MIME |
| `uploaded_by` | String(80) | アップロード者 |
| `created_at` | DateTime | |

- 許可拡張子：`pdf`/`jpg`/`jpeg`/`png`、上限 **10MB**。
- プリンタでスキャン → アップロードして該当年度のレコードに紐付ける運用。

### 3.3 編集履歴

既存 `EditHistory` パターン（社員名簿PLUS）を踏襲し、健診レコードの変更履歴を記録。

### 3.4 アクセス権（健診PLUS独自）

健診PLUS専用の権限管理を持つ（leave_mgr/pluslist 同様）。
- 管理者：**DSTT管理者（`is_admin`）＋ 健診PLUSで個別付与された人**。
- 一般利用者：個別付与された人のみ閲覧・編集可（既定は deny）。
- 営業所スコープは付与時に併せて指定可能（広い名簿スコープには自動連動しない＝独自の狭い権限）。

---

## 4. 社員名簿PLUS連携

- 参照は `from app.models import Employee` で直接クエリ（DSTT定石）。
- **同期方針**：`linked` の ①〜⑦ は `Employee` のスナップショット。一覧表示時・編集時に最新へ同期
  （改姓・区分変更・退職に追従）。退職者（`retirement_date` あり）は一覧でハイライトし、
  年度一括起票の既定対象から外す。
- **専従先名**：`Employee.company_name`（法人名）を同期し、必要に応じ手動上書き。
- **一括起票**：対象年度に対し、閲覧可能な営業所配下の**在籍者全員**を「健診対象」として一括起票。
- **前年度繰り越し**：前年度レコードを新年度へ一括複製（固定情報のみ引き継ぎ、受診系日付はクリア）。

### 4.1 担当者（manager_name）→ DSTTユーザーの自動紐付け
- `manager_name` を DSTTユーザーの表示名（`User.name`）と突き合わせて `manager_user`(username) を解決。
- マッチングは**正規化して比較**：前後空白除去・連続空白圧縮・全角/半角空白統一・大文字小文字無視。
- 一意に当たれば自動紐付け。曖昧/不一致は未解決のままにし、**手動で DSTTユーザーを選択して上書き**可能。
- **未紐付け一覧**：`manager_user` 未解決のレコードを一覧で抽出し、まとめて担当者を割当できる画面を提供。

---

## 5. 受診ステータスの自動判定

各日付・フラグから `status` を自動算出し、一覧で色分け表示。

```
未予約        : 予約日なし
予約済        : 予約日あり・受診日なし
受診済        : 受診日あり・(再検査なし or 未判定)
再検査対象    : needs_recheck = true・二次系未完了
二次案内済    : secondary_guide_sent_date あり・secondary_exam_date なし
二次完了      : secondary_exam_date あり
```

- 深夜従事者は「年2回目（受診日②）」の受診状況も別途トラッキング。
- ダッシュボード：年度別の受診率／未受診者数／再検査未完了者／案内未送付者／深夜2回目未受診者。

---

## 6. ToBell連携（リマインダー）

既存フック `app/services/to_bell_hooks.py: _create_task_for(...)` と
1分間隔スケジューラ `app/services/to_bell_push.py: send_due_task_pushes()` に乗せる。

### 6.1 対象と自動起票トリガー
レコード保存時／日次sweep時に、次の3系統のタスクを **ensure（無ければ作成／あれば更新）**：

1. **健康診断予約日リマインド**：`reservation_date` 入力済み かつ `exam_date` 未入力 → `source_ref_type="reservation"`
2. **深夜2回目（受診日②）リマインド**：`is_night_worker=true` かつ 基準日 `exam_date_2_target`
   （未入力時は `exam_date`＋6か月を既定）が確定し、`exam_date_2` 未入力（未受診）→ `night2_task_id`
3. **二次検査リマインド**：`needs_recheck=true` かつ `secondary_recommended_date` 入力済み
   かつ `secondary_exam_date` 未入力（未完了）→ `secondary_task_id`

### 6.2 通知タイミング
- **対象日の前日にタスク化**（`_hc_should_materialize`：対象日 − 1日 以降に作成）。
  まだ前日に達していない将来分のタスクは作らない（日次sweepで前日になったら起票）。
- **対象日当日 09:00 にアラート（push）**（`due_at` = 対象日 09:00。pushは時刻明示しないと
  既定23:59:59で発火しないため 09:00 を明示）。
- 旧仕様の「全体既定リードタイム＋`reminder_lead_days` による事前通知」は廃止（本リマインドでは未使用）。

### 6.3 タスク内容
- タイトル・本文（通知文）は3系統とも **「{employee_name}さんの{ジャンル}になりました。」** で統一。
  - 予約：「{employee_name}さんの健康診断予約日になりました。」
  - 深夜2回目：「{employee_name}さんの受診日②になりました。」
  - 二次検査：「{employee_name}さんの二次検査受診推奨日になりました。」
- `source_tool="health_check"`、`source_ref_type` = `"reservation"` / `"night_second"` / `"secondary_exam"`、
  `source_ref_id=str(record_id)` → **重複起票を自動防止**
- `assigned_to` = `manager_user`（自動/手動で解決済みの担当者）

### 6.4 オプトイン（各自必須）
- `app/services/to_bell_integrations.py: INTEGRATION_KEYS` に `health_check.linkage` を追加。
- 通知（ToBellタスク/push）は**担当者がオプトインしている場合のみ**起票・配信。
- 担当者が未オプトイン or 未紐付けのレコードは、健診PLUS側の**「未通知/未紐付け」一覧**に表示し、
  管理者が手動フォローできるようにする（取りこぼし防止）。

### 6.5 更新・クローズ
- 対象日（予約日／深夜2回目基準日／二次検査推奨日）変更時 → 対応タスクの `due_at` を更新（前日に達していれば9:00維持、未到達なら一旦クローズ）。
- 受診完了（`exam_date` / `exam_date_2` / `secondary_exam_date` 入力）または条件解除時 → 対応タスクを完了/クローズ。
- レコード削除時 → 対応タスクをクローズ。

---

## 7. 画面・API構成

- メイン：`/tools/health_check/`（テンプレート `templates/health_check.html`）
  - 一覧（年度・営業所・社員区分・モード・受診状況・再検査有無・深夜/担当者紐付け状況で絞り込み、列ソート、フリーワード検索）
  - レコード編集（モード別フォーム。linked は ①〜⑦ 読取専用＋同期、手動モードは手入力）
  - 担当者未紐付け一覧 / 一括割当
  - ダッシュボード（受診率・未受診・再検査未完了・深夜2回目未受診）
- API（`/tools/health_check/api/...`）
  - `GET  /records`        一覧（フィルタ・ページネーション）
  - `GET  /record/<id>`    詳細
  - `POST /record`         新規（モード指定）
  - `PUT  /record/<id>`    更新（保存時に名簿同期＋担当者解決＋ToBell ensure）
  - `DELETE /record/<id>`  削除
  - `POST /bulk_create`    名簿から年度一括起票（在籍者全員）
  - `POST /carryover`      前年度繰り越し
  - `POST /resolve_managers` 担当者の一括自動紐付け
  - `PUT  /record/<id>/manager` 担当者の手動上書き
  - `POST /record/<id>/attachment`        スキャン結果アップロード
  - `GET  /record/<id>/attachment/<aid>`  ダウンロード
  - `GET  /export`         CSV/Excel 書き出し（`utf-8-sig`、`pandas`）
  - `POST /import`         CSV/Excel 取込
  - `GET/POST /settings`   既定リードタイム等のツール設定、権限付与

---

## 8. DSTTへの組み込み（編集対象ファイル）

| ファイル | 追加内容 |
|---|---|
| `app/tools/health_check.py` | Blueprint・各API・権限・名簿同期・担当者解決・ToBell ensure |
| `app/templates/health_check.html` | メイン画面（一覧/編集/未紐付け/ダッシュボード） |
| `app/models.py` | `HealthCheckRecord` / `HealthCheckAttachment`（＋履歴） |
| `app/__init__.py` | blueprint 登録、`_BP_TO_TOOL_KEY` に `health_check`、スキーマ動的拡張 |
| `app/navigation.py` | NAV_ITEMS にエントリ（key/href/icon/label/description） |
| `app/access_control.py` | `TOOL_ACCESS_CATEGORIES` に `"health_check": "sensitive"` |
| `app/manuals.py` | マニュアル登録 |
| `app/services/to_bell_hooks.py` | 二次検査/深夜2回目の ensure・close フック追加 |
| `app/services/to_bell_integrations.py` | `INTEGRATION_KEYS` に `health_check.linkage` |
| `tests/test_health_check.py` | モデル・API・担当者解決・ToBell起票・ステータス判定のテスト |

---

## 9. 補足・既定値（実装時の細部）

- 健診年度＝4月開始。一覧の既定表示は現在の年度。
- **手動モード（入社前・内勤者）のレコード追加の入力支援**：
  - **社員区分**：`営業社員（R契約）` / `営業社員（P契約）` / `営業社員（PT契約）` から選択（`EMPLOYEE_TYPE_OPTIONS`）。
  - **生年月日**：手入力可（年齢・NASVA判定で使用）。名簿連携は名簿から同期。
  - **管理担当名**：対象営業所のメンバーのうち、DSTT管理者ページで担当（`AccessDepartment`）が
    **エリアマネージャー**（`AREA_MANAGER_DEPARTMENT`）に設定されているユーザーから選択
    （`GET /api/area_managers?office=`）。選択すると ToBell 担当者（`manager_user`）も自動で連動。
  - **専従先名**：**現場リストPLUS（`Site`）からの検索入力**（`GET /api/sites?search=`）＋手動入力に対応。
- 深夜2回目の基準日 `exam_date_2_target` は未入力時 `exam_date` ＋6か月を既定（運用で調整可）。
- 添付：pdf/jpg/png・10MB。保存先 `uploads/health_check/<年度>/`。
- ToBell通知は担当者オプトイン時のみ。未紐付け/未オプトインは健診PLUS側一覧でフォロー。
