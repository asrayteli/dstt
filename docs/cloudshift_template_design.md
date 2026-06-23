# CloudShift テンプレート機能 設計メモ

現場シフト（scene）/ 個人シフト（person）の「1 か月分」を再利用できるテンプレートとして
保存し、任意の対象月へ反映する機能。アシストモーダルの「テンプレート」タブから利用する。

## 入り口（UI 動線）

```
編集画面 → アクション → 「アシスト」ボタン → アシストモーダル → 「テンプレート」タブ
```

- 「テンプレート」タブは **シフト帳の所有者**（`PAGE.type === 'owner'`）かつ scene / person の
  ときだけ表示する（公開編集・閲覧では非表示）。
- 「新規テンプレート作成」「編集」は **別ウィンドウ**（`window.open`）の専用カレンダーで行う。

## 作成・編集ウィンドウ

- ルート: `GET /tools/shiftersync/cloudshift/project/<project_id>/template-editor?template_id=<任意>`
- テンプレート: `app/templates/cloudshift_template_editor.html`
- スクリプト: `app/static/cloudshift/js/cloudshift_template_editor.js`
- 既存の `ShifterSync.buildCalendar`（`ss_common.js`）をそのまま使い、代表月のカレンダーへ
  1 か月分を入力する。現場シフトでは現場の枝番号も `siteplus` の API から読み込んで選べる。
- 保存後は `window.opener.postMessage({ type: 'cloudshift-template-saved', ... })` で
  親ウィンドウへ通知し、アシストの一覧を自動更新する。

## 保存形式（DB）

`CloudShiftTemplate`（`app/models.py`、テーブル `cloudshift_templates`）

| 列 | 説明 |
| --- | --- |
| `id` | `tpl_` + 乱数 |
| `project_id` | 所有シフト帳（FK）。プロジェクト削除時に明示削除（PwaSubscription と同方式） |
| `owner_user_id` | 所有者 |
| `name` | テンプレート名 |
| `mode` | `scene` / `person`（シフト帳から継承） |
| `basis` | 既定の反映基準 `date` / `weekday`（反映時に上書き可） |
| `representative_year` / `representative_month` | 代表月（編集キャンバス兼、曜日基準の導出元） |
| `slots` | `{"1": [entry...], ...}` 日(1..31)キーの authoring エントリ（同期エントリは除外） |
| `options` | 反映の既定 `{apply_mode, holiday_mode, target_filter}` |

スキーマは `_ensure_cloudshift_runtime_schema()` 内の `db.create_all()` で自動作成される。

## 反映（カスタマイズ可能な項目）

`POST /api/project/<project_id>/templates/<template_id>/apply`
（`year`, `month`, `basis`, `apply_mode`, `holiday_mode`, `target_filter`）

- **基準（basis）**
  - `date`（日付基準）: 対象月の同じ日付へ `slots[日]` をそのまま入れる（存在しない日付はスキップ）。
  - `weekday`（曜日基準）: 代表月の「各曜日の最初の出現日」をその曜日のパターンとし、対象月の
    同じ曜日へ繰り返し入れる。
- **上書き方法（apply_mode）**: `overwrite`（置換）/ `append`（追加）/ `fill_empty`（空き日のみ）。
- **対象日（target_filter）**: `all` / `weekday`（平日のみ・祝日除く）/ `weekend`（土日祝）/
  `holiday` / `non_holiday`。
- **祝日の扱い（holiday_mode、曜日基準時）**: `as_weekday`（実曜日）/ `as_sunday`（日曜パターン）/
  `skip`（触らない）/ `clear`（空にする）。

反映は対象月の確定シフトに対して `_save_month_in_project` 経由で保存する。**他帳から同期された
エントリ（`sync_source_type`）は常に温存**し、手入力エントリのみを差し替える。反映後は当月で
あれば ViewPWA 購読者へ通知する。

## テスト

`tests/test_cloudshift_template.py`（作成 / 一覧 / 取得 / 更新 / 削除 / 反映の各基準・各オプション・
祝日処理・上書きクリア・アクセス制御・プロジェクト削除連動・エディタページ描画）。
