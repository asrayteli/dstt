# 健診PLUS（health_check）設計書

健康診断の進捗を「予約 → 受診 → 再検査 → 二次検査完了」まで抜け漏れなく追跡する、
社員名簿PLUS連携の健康診断管理ツール。本書は実装着手前の合意用ドラフト。

- 表示名：**健診PLUS**
- 内部キー / Blueprint：`health_check`（`/tools/health_check`、API は `/tools/health_check/api/...`）
- 系統：PLUS家系（社員名簿PLUS／現場リストPLUSと同じくマスタ管理）
- アクセス区分：**sensitive**（健康情報＝要配慮個人情報。営業所スコープ＋個別付与）

---

## 1. 確定済みの方針

| 論点 | 決定 |
|---|---|
| ツール名 | 健診PLUS |
| レコード単位 | **対象者 × 健診年度で毎年1レコード**（年度履歴を保持） |
| 二次検査のToBell通知 | **二次検査受診推奨日が入力されたら自動起票**。推奨日の **朝9:00** に通知 |

---

## 2. 対象者と3モード（record_type）

健診は入社前の採用予定者を含む全員が対象。対象者を3層に分けて扱う。

| モード | record_type | 説明 | 名簿リンク |
|---|---|---|---|
| 名簿連携 | `linked` | 社員名簿PLUS（`Employee`）にいる現職社員 | あり（`employee_id`） |
| 入社前 | `pre_hire` | まだ名簿に載らない採用予定者の入社前健診 | なし。後で昇格可 |
| 内勤者 | `internal` | 名簿PLUSに載らない内勤・嘱託等 | なし |

- **入社前 → 名簿連携への昇格**：入社して名簿に載ったら、当該レコードに `employee_id` を結び付けて
  `pre_hire` → `linked` に昇格する導線を用意（健診履歴を引き継ぐ）。

---

## 3. データモデル

`app/models.py` の規約（`id`/`created_at`/`updated_at`、JST `jst_now()`）に準拠。
スキーマ追加は既存の `app/__init__.py: _ensure_access_control_schema()` の動的 ALTER 方式に乗せる
（Alembic 不使用）。

### 3.1 HealthCheckRecord（健診レコード本体）

`__tablename__ = 'health_check_records'`

| カラム | 型 | 出どころ（linked） | 説明 / 項目対応 |
|---|---|---|---|
| `id` | Integer PK | — | 主キー |
| `target_year` | Integer | 手入力(必須) | 対象健診年度（例 2026） |
| `record_type` | String(20) | — | `linked`/`pre_hire`/`internal` |
| `employee_id` | Integer FK→employees.id (null可) | — | 名簿リンク（linked時のみ） |
| `employee_number` | String(20) | 名簿同期 | ① 社員番号 |
| `employee_name` | String(100) | 名簿同期 | ② 社員名（手動モードは必須） |
| `employee_type` | String(50) | 名簿 `employee_type` 同期 | ③ 社員区分 |
| `assignment_site` | String(200) | 名簿 `site_name` 同期※ | ④ 専従先名（※要確認） |
| `manager_name` | String(100) | 名簿 `manager_name` 同期 | ⑤ 管理担当名 |
| `hire_date` | Date | 名簿 `hire_date` 同期 | ⑥ 入社日付（入社前は予定日） |
| `retirement_date` | String(50) | 名簿 `retirement_date` 同期 | ⑦ 退職日（名簿が文字列のため踏襲） |
| `reservation_date` | Date | 手入力 | ⑧ 予約日 |
| `exam_date` | Date | 手入力 | ⑨ 受診日 |
| `exam_date_passenger` | Date | 手入力 | ⑩ 受診日②（旅客／二種特殊健診） |
| `medical_institution` | String(200) | 手入力 | ⑪ 受診医療機関名 |
| `is_night_worker` | Boolean | 手入力 | ⑫ 深夜従事者（深夜業特殊健診対象） |
| `needs_recheck` | Boolean | 手入力 | ⑬ 再検査有無 |
| `recheck_items` | Text | 手入力 | ⑭ 再検査項目 |
| `secondary_recommended_date` | Date | 手入力 | ⑮ 二次検査受診推奨日 ← **ToBell発火日** |
| `secondary_exam_date` | Date | 手入力 | ⑯ 二次検診受信日（＝受診日） |
| `secondary_guide_sent_date` | Date | 手入力 | ⑰ 二次検査案内送付日 |
| `secondary_result` | Text | 手入力 | ⑱ 二次検査結果 |
| `remarks` | Text | 手入力 | ⑲ 備考 |
| `status` | String(20) | 自動算出（キャッシュ） | 受診ステータス（§5） |
| `tobell_task_id` | Integer (null可) | システム | 起票したToBellタスクID（§6で更新/クローズに使用） |
| `created_at` / `updated_at` | DateTime | — | 監査用 |

**一意制約**：`linked` は `UNIQUE(target_year, employee_id)`。
`pre_hire`/`internal` は `employee_id` が NULL のため `id` で識別（同名重複は警告表示で運用）。

### 3.2 HealthCheckAttachment（スキャン結果の添付）

`__tablename__ = 'health_check_attachments'`

| カラム | 型 | 説明 |
|---|---|---|
| `id` | Integer PK | |
| `record_id` | Integer FK→health_check_records.id | 紐付くレコード |
| `stored_path` | String(500) | 保存パス `uploads/health_check/<年度>/<uuid>.<ext>` |
| `original_name` | String(255) | アップロード時の元ファイル名（`secure_filename`） |
| `content_type` | String(100) | MIME |
| `uploaded_by` | String(80) | アップロード者 |
| `created_at` | DateTime | |

- 許可拡張子：`pdf` / `jpg` / `jpeg` / `png`、上限 10MB（名簿PLUSの上限に合わせる）。
- プリンタでスキャン → アップロードして該当年度のレコードに紐付ける運用。

### 3.3 編集履歴

誰がいつ何を変えたかを残すため、既存 `EditHistory` パターン（社員名簿PLUS）を踏襲して
健診レコードの変更履歴を記録する。

---

## 4. 社員名簿PLUS連携

- 参照は `from app.models import Employee` で直接クエリ（DSTT定石）。
- 営業所スコープ・論理削除（`is_deleted`）の扱いは社員名簿PLUSと統一。
- **同期方針**：`linked` レコードの ①〜⑦ は `Employee` からのスナップショット。
  一覧表示時・編集時に最新値へ同期する（改姓・区分変更・退職に追従）。
  退職者（`retirement_date` あり）は一覧でハイライトし、年度起票の既定対象から外す。
- **一括起票**：対象年度に対し、営業所配下の在籍社員を「健診対象」として一括起票するボタン。
- **前年度繰り越し**：前年度レコードを新年度へ一括複製（固定情報のみ引き継ぎ、受診系日付はクリア）。

### 4.1 専従先名（要確認）
`Employee` には `site_name`（現場名）/`company_name`（法人名）/`cost_name`（原価名称）があり、
「専従先」がどれに当たるか未確定。**既定は `site_name` を同期し、必要に応じ手動上書き可**として実装し、
実運用の定義が固まり次第マッピングを確定する。

---

## 5. 受診ステータスの自動判定

各日付・フラグから `status` を自動算出し、一覧で色分け表示する。

```
未予約        : 予約日なし
予約済        : 予約日あり・受診日なし
受診済        : 受診日あり・(再検査なし or 未判定)
再検査対象    : needs_recheck = true・二次系未完了
二次案内済    : secondary_guide_sent_date あり・secondary_exam_date なし
二次完了      : secondary_exam_date あり
```

ダッシュボード集計：年度別の受診率／未受診者数／再検査未完了者／案内未送付者。

---

## 6. ToBell連携（二次検査リマインダー）

既存フック機構 `app/services/to_bell_hooks.py: _create_task_for(...)` と
1分間隔スケジューラ `app/services/to_bell_push.py: send_due_task_pushes()` に乗せる。

### 6.1 自動起票トリガー
レコードの保存時、次の条件を満たしたら ToBell タスクを **自動で ensure（無ければ作成／あれば更新）**：

- `needs_recheck = true` かつ `secondary_recommended_date` が入力済み
- かつ `secondary_exam_date`（二次受診済）が未入力（未完了）

### 6.2 タスク内容
- `title` = `[健診] {employee_name} 二次検査 受診推奨`
- `due_at` = **`secondary_recommended_date` の 09:00**
  （push は時刻を明示しないと既定 23:59:59 で発火しないため、9時固定で起票）
- `source_tool="health_check"`、`source_ref_type="secondary_exam"`、
  `source_ref_id=str(record_id)` → これにより**重複起票を自動防止**
- `assigned_to` = 管理担当（`manager_name` 由来）または営業所担当ユーザー
- 起票したタスクIDを `tobell_task_id` に保持

### 6.3 更新・クローズ
- `secondary_recommended_date` 変更時 → 対応タスクの `due_at` を更新（9:00 を維持）
- `secondary_exam_date` 入力（二次完了）または `needs_recheck` 解除時 →
  対応タスクを完了/クローズ
- レコード削除時 → 対応タスクをクローズ

### 6.4 オプトイン
`app/services/to_bell_integrations.py: INTEGRATION_KEYS` に
`health_check.linkage` を追加し、ToBell利用者ごとにオプトイン可能にする。

---

## 7. 画面・API構成

- メイン：`/tools/health_check/`（テンプレート `templates/health_check.html`）
  - 一覧（年度・営業所・社員区分・モード・受診状況・再検査有無・深夜/旅客で絞り込み、列ソート、フリーワード検索）
  - レコード編集（モード別フォーム。linked は ①〜⑦ 読取専用＋同期、手動モードは手入力）
  - ダッシュボード（受診率・未受診・再検査未完了）
- API（`/tools/health_check/api/...`）
  - `GET  /records`        一覧（フィルタ・ページネーション）
  - `GET  /record/<id>`    詳細
  - `POST /record`         新規（モード指定）
  - `PUT  /record/<id>`    更新（保存時に名簿同期＋ToBell ensure を実行）
  - `DELETE /record/<id>`  削除
  - `POST /bulk_create`    名簿から年度一括起票
  - `POST /carryover`      前年度繰り越し
  - `POST /record/<id>/attachment`        スキャン結果アップロード
  - `GET  /record/<id>/attachment/<aid>`  ダウンロード
  - `GET  /export`         CSV/Excel 書き出し（`utf-8-sig`、`pandas`）
  - `POST /import`         CSV/Excel 取込

---

## 8. DSTTへの組み込み（編集対象ファイル）

| ファイル | 追加内容 |
|---|---|
| `app/tools/health_check.py` | Blueprint・各API実装 |
| `app/templates/health_check.html` | メイン画面 |
| `app/models.py` | `HealthCheckRecord` / `HealthCheckAttachment`（＋履歴） |
| `app/__init__.py` | blueprint 登録、`_BP_TO_TOOL_KEY` に `health_check`、必要ならスキーマ動的拡張 |
| `app/navigation.py` | NAV_ITEMS にエントリ（key/href/icon/label/description） |
| `app/access_control.py` | `TOOL_ACCESS_CATEGORIES` に `"health_check": "sensitive"` |
| `app/manuals.py` | マニュアル登録 |
| `app/services/to_bell_hooks.py` | `on_health_check_secondary_*`（ensure/close）フック追加 |
| `app/services/to_bell_integrations.py` | `INTEGRATION_KEYS` に `health_check.linkage` |
| `tests/test_health_check.py` | モデル・API・ToBell起票・ステータス判定のテスト |

---

## 9. 今後の確認事項

1. **専従先名**の定義（`site_name` / `company_name` / その他）。
2. **二次検査ToBellタスクの担当者**を、管理担当（`manager_name`）に自動割当でよいか、
   営業所担当者へ通知するか。
3. **添付**の上限・許可形式（既定：pdf/jpg/png・10MB）でよいか。
4. **一括起票**の既定対象（在籍者全員か、深夜/旅客などの条件付与か）。
5. アクセス権を社員名簿PLUSと同一スコープに揃えるか、健診独自スコープにするか。
