# CloudShift テンプレート機能 設計メモ

現場シフト（scene）/ 個人シフト（person）のパターンを再利用できるテンプレートとして
保存し、任意の対象月へ反映する機能。アシストモーダルの「テンプレート」タブから利用する。

## 基準（basis）は 3 種類

| 基準 | 保存するもの | 反映のされ方 |
| --- | --- | --- |
| `date`（日付基準） | 代表月の 1 か月分（日付キー） | 対象月の同じ日付へそのまま（存在しない日付はスキップ） |
| `weekday`（曜日基準） | 月〜日 7 枠＋祝日 7 枠（計 14 枠） | 対象月の同じ曜日へ繰り返し |
| `week`（週基準） | 曜日基準と同じ 14 枠 | 反映時に指定した日付を含む「月曜始まりの 1 週間」だけへ |

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
- 既存の `ShifterSync.buildCalendar`（`ss_common.js`）をそのまま使う。現場シフトでは現場の
  枝番号も `siteplus` の API から読み込んで選べる。
- **日付基準**: 代表月のカレンダーへ 1 か月分を入力する（従来どおり）。
- **曜日基準 / 週基準**: 月の全日カレンダーではなく **14 枠のグリッド**で編集する。
  上段が通常の月〜日、下段が「その曜日が祝日だった場合」の月〜日。実装は
  `buildCalendar` の追加オプション（`dayCount` / `dayLabels` / `dayToneClasses`）で、
  月曜始まりの固定月（2026-06）の 1〜14 日を 2 行のグリッドとして描画している。
  基準を切り替えても、日付基準・曜日基準それぞれの入力バッファは保持される。
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
| `basis` | 既定の反映基準 `date` / `weekday` / `week`（反映時に上書き可） |
| `representative_year` / `representative_month` | 代表月（日付基準の編集キャンバス。旧形式の曜日基準では曜日導出元） |
| `slots` | 日付基準: `{"1": [entry...], ...}` 日(1..31)キー。曜日/週基準（新形式）: `{"w0".."w6": [...], "h0".."h6": [...]}` 曜日キー（w=通常、h=祝日、0=月..6=日）。authoring エントリのみ（同期エントリは除外） |
| `options` | 反映の既定 `{apply_mode, holiday_mode, target_filter}` |

- **旧形式との互換**: 旧実装の曜日基準テンプレート（代表月の日付キーのまま保存）は
  そのまま動く。反映時は従来どおり「各曜日の初出日」からパターンを導出し
  （`_template_weekday_pattern`）、祝日枠は空として扱う。API の詳細取得では旧形式でも
  `weekday_slots`（w/h キーの 14 枠ビュー）を導出して返すため、エディタで開いて保存した
  時点で新形式に置き換わる。
- 一覧・詳細のペイロードには `slot_format`（`"weekday"` = 新形式 / `"days"` = 日付キー）を
  含め、UI の表示分岐（「N枠 / M件」表示や代表月バッジの有無）に使う。

スキーマは `_ensure_cloudshift_runtime_schema()` 内の `db.create_all()` で自動作成される。

## 反映（カスタマイズ可能な項目）

`POST /api/project/<project_id>/templates/<template_id>/apply`
（`year`, `month`, `basis`, `apply_mode`, `holiday_mode`, `target_filter`, `target_day`）

- **基準（basis）**
  - `date`（日付基準）: 対象月の同じ日付へ `slots[日]` をそのまま入れる（存在しない日付はスキップ）。
  - `weekday`（曜日基準）: 曜日パターンを対象月の同じ曜日へ繰り返し入れる。
  - `week`（週基準）: `target_day`（対象月内の日、**必須**）を含む「月曜始まりの 1 週間」の
    うち対象月内に収まる日だけへ、曜日一致でパターンを入れる。週の他の日は一切触らない。
    `target_day` が無い・範囲外の場合は 400。
- **上書き方法（apply_mode）**: `overwrite`（置換）/ `append`（追加）/ `fill_empty`（空き日のみ）。
- **対象日（target_filter）**: `all` / `weekday`（平日のみ・祝日除く）/ `weekend`（土日祝）/
  `holiday` / `non_holiday`。
- **祝日の扱い（holiday_mode、曜日・週基準時）**:
  - `as_template`（既定）: テンプレートの祝日枠（`h*`）を使う。**祝日枠が空なら同じ曜日の
    通常枠（`w*`）へフォールバック**する。旧形式テンプレートでは祝日枠が無いため
    実質 `as_weekday` と同じ挙動になる。
  - `as_weekday`（実曜日・祝日枠を無視）/ `as_sunday`（通常の日曜枠）/
    `skip`（触らない）/ `clear`（空にする）。

反映は対象月の確定シフトに対して `_save_month_in_project` 経由で保存する。**他帳から同期された
エントリ（`sync_source_type`）は常に温存**し、手入力エントリのみを差し替える。反映後は当月で
あれば ViewPWA 購読者へ通知する。

## テスト

`tests/test_cloudshift_template.py`（作成 / 一覧 / 取得 / 更新 / 削除 / 反映の各基準・各オプション・
祝日処理・上書きクリア・アクセス制御・プロジェクト削除連動・エディタページ描画、
新形式 14 枠の作成・更新・`as_template` の祝日枠フォールバック、
週基準の単週反映・月またぎクリップ・`target_day` 必須、旧形式からの `weekday_slots` 導出）。
