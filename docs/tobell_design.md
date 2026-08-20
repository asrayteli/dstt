# To Bell 設計図

作成日: 2026-05-26

## 1. コンセプト

**To Bell - 仕事の司令塔**

To Bell は、DSTT 全体の「やること」を集約し、個人 Todo、チームタスク、確認依頼、期限管理、他ツールからの要対応通知をひとつの画面で操縦するタスク管理ツールです。

設計の中心は次の一点です。

> シンプルに使えば、すぐ書けてすぐ終わる Todo。  
> 深く使えば、担当・進捗・確認・添付・履歴・他ツール連携まで扱える仕事の司令塔。

## 2. 基本方針

### 2.1 3秒で使える

最初の入力はタスク名だけで成立させます。

- タスク名
- 期限
- 担当者
- メモ

これ以外の項目は詳細パネルに隠し、必要な人だけ開きます。

### 2.2 詳細化はあとから

タスクは作ったあとで育てられる設計にします。

- サブタスク
- 進捗率
- 優先度
- タグ
- プロジェクト
- 担当者
- 確認者
- 添付
- コメント
- 関連ツール
- 履歴
- 繰り返し
- 依存関係

### 2.3 テンプレートはユーザー作成のみ

サーバー側で業務テンプレートは用意しません。

理由:

- AI や開発側が作ったテンプレートは、本来の業務外の作業を含める可能性がある
- 拠点・部署・担当者ごとに実際の手順が違う
- 運用に合わないテンプレートがあると、かえって現場の負担になる

To Bell では、ユーザーが実際に使うタスクからテンプレート化します。

- このタスクをテンプレート化
- このプロジェクトをテンプレート化
- 空のテンプレートを作成
- 自分だけで使う
- 所属内で共有する
- 管理者が共有テンプレートを非表示にする

AI によるテンプレート生成は初期実装では行いません。将来入れる場合も、明示的な下書き補助に留め、保存・共有はユーザー承認を必須にします。

### 2.4 Gmail / Google Calendar で代用できる機能は後回し

対象ユーザーが普段使うアプリは Gmail と Google Calendar 程度であるため、次は優先度を下げます。

- メール本文管理
- メールスレッド型の会話機能
- 純粋な予定表管理
- 会議招待の作成
- 時間単位のスケジュール調整

To Bell が優先するのは、Google Calendar では管理しにくい「作業」「確認」「進捗」「担当」「他ツール由来の要対応」です。

## 3. ツール名と配置

表示名:

- To Bell

サブタイトル:

- 仕事の司令塔

内部キー:

- `to_bell`

URL:

- `/tools/to_bell`

ナビ説明:

- タスク、確認依頼、期限、他ツール通知をまとめて操縦する仕事の司令塔

アクセス制御:

- 初期は `public`
- チーム共有、管理レポート、他ツール連携を入れる段階で `sensitive` も検討

## 4. CloudShift 通知から参考にする点

CloudShift の休暇種別変更申請は、次の考え方を持っています。

- 未表示申請数を `unviewed_leave_change_request_count` として持つ
- 保留中申請数を `pending_leave_change_request_count` として持つ
- 画面上では `!` バッジを出す
- 申請一覧を開いたら `mark-viewed` API で未表示を既読化する
- 保留中と未表示を分けて扱う

To Bell ではこの考え方を全体通知に拡張します。

- 未読: ユーザーがまだ見ていない通知
- 要対応: まだ処理が終わっていないタスク・申請・期限切れ
- 既読: 見たが、要対応としては残る可能性がある
- 解決済み: 完了、却下、期限解除などで処理対象から外れる

## 5. ダッシュボードカードの通知マーク

To Bell 実装と同時に、DSTT ダッシュボードのツールカードへ通知マークを出せる共通設計を入れます。

### 5.1 表示ルール

各ツールカードに、ユーザー別の通知状態を表示します。

- 未読あり: 小さな赤いバッジ
- 要対応あり: 件数つきバッジ
- 期限注意: 黄色または橙のバッジ
- 通知なし: 何も表示しない

例:

- `!`
- `3`
- `期限 2`
- `確認待ち 1`

### 5.2 最初に対応するカード

最初は次の 2 つから始めます。

- To Bell: 自分の未読通知・期限切れ・今日の要対応
- ShifterSync / CloudShift: 休暇種別変更申請の未表示・保留

### 5.3 通知集約 API

ダッシュボード用に軽量な API を用意します。

`GET /api/tool-notifications`

返却例:

```json
{
  "to_bell": {
    "unread_count": 4,
    "action_count": 7,
    "label": "要対応 7",
    "severity": "danger",
    "href": "/tools/to_bell?filter=attention"
  },
  "shiftersync": {
    "unread_count": 1,
    "action_count": 3,
    "label": "申請 3",
    "severity": "warning",
    "href": "/tools/shiftersync/cloudshift"
  }
}
```

### 5.4 ツール別通知プロバイダ

各ツールが通知数を返せるように、サーバー側にプロバイダ関数を置きます。

候補:

- `app/tool_notifications.py`
- `get_tool_notification_summary(user) -> dict`
- `register_tool_notification_provider(tool_key, provider)`

最初はシンプルに固定関数でよいです。プラグイン化は後回しにします。

## 6. 主要画面

### 6.1 今日

最初に開く画面です。

表示するもの:

- 今日が期限
- 期限切れ
- 自分に割り当て
- 確認依頼
- コメント未読
- ピン留め

操作:

- 追加
- 完了
- 期限変更
- 担当変更
- コメント
- 詳細を開く

### 6.2 受信箱

まだ整理していないタスクを置く場所です。

用途:

- 思いついたことをすぐ入れる
- 他ツールから自動作成されたタスクを確認する
- あとで期限・担当・タグを付ける

### 6.3 カンバン

状態別に管理します。

列:

- 未着手
- 進行中
- 保留
- 確認待ち
- 差戻し
- 完了

PC ではドラッグ移動。スマホではタップで状態変更を優先します。

### 6.4 タスク詳細

PC では右ペイン、スマホでは下から開く詳細シートにします。

内容:

- タイトル
- 状態
- 期限
- 優先度
- 担当者
- 確認者
- プロジェクト
- タグ
- 進捗率
- サブタスク
- コメント
- 添付
- 関連リンク
- 履歴

### 6.5 プロジェクト

複数タスクのまとまりです。

例:

- 月次作業
- シフト確定
- 車検更新対応
- 請求確認
- 現場追加対応

ただし初期テンプレートは作りません。ユーザーが作った実プロジェクトをテンプレート化できます。

### 6.6 カレンダー

Google Calendar の代替ではなく、タスク期限を見るための画面です。

表示:

- 日
- 週
- 月
- 期限切れ

機能:

- 期限変更
- 当日タスク確認
- 繰り返しタスク確認

予定表そのものの管理は Google Calendar に任せ、To Bell は「作業期限」に集中します。

### 6.7 レポート

管理者またはチーム向けです。

- 期限切れ件数
- 完了率
- 担当者別の未完了件数
- 確認待ち滞留
- プロジェクト別進捗
- 自動作成タスクの処理状況

初期実装では後回しです。

## 7. タスクモデル

### 7.1 ステータス

- `todo`: 未着手
- `doing`: 進行中
- `blocked`: 保留
- `review`: 確認待ち
- `returned`: 差戻し
- `done`: 完了
- `archived`: アーカイブ

### 7.2 優先度

- 低
- 通常
- 高
- 緊急

自動スコアも将来追加できます。

例:

- 期限切れ +50
- 今日期限 +30
- 緊急 +30
- 確認待ち +20
- コメント未読 +10

### 7.3 進捗率

初期はサブタスク完了率から自動計算します。

- サブタスクなし: 手動入力可
- サブタスクあり: 完了数 / 全件数
- 手動固定も可能

### 7.4 タスク種別

- 通常タスク
- 確認依頼
- 承認依頼
- 期限通知
- 他ツール連携タスク
- 繰り返し生成タスク

## 8. データベース設計

### 8.1 `to_bell_tasks`

中心テーブルです。

主なカラム:

- `id`
- `title`
- `description`
- `status`
- `priority`
- `due_at`
- `start_at`
- `completed_at`
- `created_by`
- `assigned_to`
- `reviewer_id`
- `project_id`
- `visibility_scope`
- `visibility_branch_id`
- `visibility_office_id`
- `visibility_department_id`
- `progress_mode`
- `manual_progress`
- `source_tool`
- `source_ref_type`
- `source_ref_id`
- `created_at`
- `updated_at`

### 8.2 `to_bell_projects`

- `id`
- `name`
- `description`
- `status`
- `owner_id`
- `visibility_scope`
- `created_at`
- `updated_at`

### 8.3 `to_bell_subtasks`

- `id`
- `task_id`
- `title`
- `is_done`
- `sort_order`
- `created_at`
- `updated_at`

### 8.4 `to_bell_comments`

- `id`
- `task_id`
- `body`
- `created_by`
- `created_at`
- `updated_at`

### 8.5 `to_bell_tags`

- `id`
- `name`
- `color`
- `created_by`

### 8.6 `to_bell_task_tags`

- `task_id`
- `tag_id`

### 8.7 `to_bell_attachments`

- `id`
- `task_id`
- `file_name`
- `stored_path`
- `mime_type`
- `file_size`
- `uploaded_by`
- `created_at`

大容量ファイルは FILE POST 連携に逃がせるようにします。

### 8.8 `to_bell_links`

DSTT 連携の中核です。

- `id`
- `task_id`
- `link_tool`
- `link_type`
- `target_id`
- `target_label`
- `target_url`
- `created_at`

例:

- `link_tool = siteplus`
- `link_type = site`
- `target_id = 123`
- `target_label = 契約コード 00000001 / ○○現場`

### 8.9 `to_bell_notifications`

アプリ内通知です。

- `id`
- `user_id`
- `task_id`
- `source_tool`
- `event_type`
- `title`
- `body`
- `href`
- `severity`
- `is_read`
- `read_at`
- `is_resolved`
- `resolved_at`
- `created_at`

### 8.10 `to_bell_activity_logs`

- `id`
- `task_id`
- `actor_id`
- `action`
- `changes`
- `created_at`

### 8.11 `to_bell_templates`

ユーザー作成テンプレートのみ保存します。

- `id`
- `name`
- `description`
- `owner_id`
- `scope`
- `payload`
- `is_hidden`
- `created_at`
- `updated_at`

`payload` には、タスク・サブタスク・タグ・相対期限などを JSON で保持します。

### 8.12 `to_bell_push_subscriptions`

Web Push 用です。

- `id`
- `user_id`
- `endpoint`
- `p256dh`
- `auth`
- `user_agent`
- `device_label`
- `is_active`
- `created_at`
- `updated_at`

## 9. 権限設計

タスクの公開範囲:

- 自分だけ
- 担当者と確認者
- 指定ユーザー
- 営業所
- 支店
- 管理者

DSTT 既存の所属情報を使います。

- `branch_id`
- `office_id`
- `department_id`
- 追加営業所アクセス
- 管理者判定

初期は安全側に寄せます。

- 個人タスクは本人のみ
- 担当者に割り当てたら担当者も閲覧可
- コメントや添付はタスク閲覧権限に従う
- 他ツール連携タスクは元ツールの閲覧権限を尊重する

## 10. 通知設計

### 10.1 通知レイヤー

3 段階で実装します。

1. アプリ内通知
2. ダッシュボードカードの通知マーク
3. PC / iPhone への Web Push 通知

### 10.2 アプリ内通知

通知対象:

- 自分に割り当て
- 期限前
- 期限切れ
- コメント追加
- 確認依頼
- 差戻し
- 完了
- 自動タスク作成
- 他ツールからの通知

### 10.3 既読と解決

CloudShift と同じく、「見た」と「終わった」を分けます。

- `is_read`: ユーザーが通知を見た
- `is_resolved`: 対応が終わった

例:

- 期限切れ通知を開く: 既読
- タスクを完了する: 解決

### 10.4 Web Push

最終的に PC と iPhone の両方へ通知を出します。

必要なもの:

- HTTPS
- Web App Manifest
- Service Worker
- Push API
- Notifications API
- VAPID key
- ユーザー別 subscription 保存
- 通知許可ボタン
- 通知設定画面

iPhone は iOS / iPadOS 16.4 以降で、ホーム画面に追加した Web アプリとして使う前提にします。通常の Safari タブだけでは期待通りに使えない場合があります。

### 10.5 通知設定

ユーザーごとに設定できます。

- 通知を使う
- PC 通知
- スマホ通知
- 期限前通知
- 期限切れ通知
- コメント通知
- 確認依頼通知
- 他ツール通知
- 静かな時間帯

## 11. 他ツール連携

### 11.1 CloudShift

最初に連携する候補です。

連携内容:

- 休暇種別変更申請を To Bell 通知へ流す
- 未表示申請を ShifterSync カードに表示
- 必要なら To Bell に確認タスクを自動作成
- 仮保存あり、未反映、確認待ちを通知化

### 11.2 siteplus

- 現場にタスクを紐付け
- 契約コード単位でタスク検索
- 現場登録・枝番号登録・専従者変更の確認タスク

### 11.3 pluslist

- 社員を担当者・関係者として紐付け
- 社員情報更新の確認タスク
- 所属情報を権限判定に利用

### 11.4 car_inspe

- 車検期限タスク
- 点検期限タスク
- 車検証 OCR 後の確認タスク
- PDF 添付連携

### 11.5 FILE POST

- 大容量添付の共有リンク化
- 受領期限のタスク化
- 公開期限切れ通知

### 11.6 PowerVote

- 回答期限の通知
- 集計確認タスク
- 未回答確認タスク

### 11.7 bus_pricing

- 見積確認タスク
- 料金監査エラーの確認依頼
- 承認待ちタスク

## 12. Google 連携案

Gmail と Google Calendar で代用できる部分は後回しにしますが、連携できると便利な機能はあります。

### 12.1 Google Calendar

候補:

- To Bell タスクの期限を Google Calendar にイベントとして送る
- 期限前リマインダーを Google Calendar 側にも持たせる
- 完了時にイベントを削除または完了印を付ける

注意:

- To Bell は予定表ではなくタスク管理なので、全タスクを Calendar に流すとカレンダーが汚れます
- ユーザーが選んだ重要タスクだけ連携するのがよいです

Google Calendar API ではイベント作成時にリマインダー設定を指定できます。

参考:

- https://developers.google.com/calendar/api/concepts/reminders
- https://developers.google.com/workspace/calendar/api/v3/reference/events/insert

### 12.2 Gmail

候補:

- タスクの共有・確認依頼を Gmail API でメール送信
- タスクコメントをメール通知
- Gmail のメールから To Bell タスクを作成

優先度:

- メール送信は比較的有用
- Gmail 受信箱の取り込みは複雑なので後回し

Gmail API にはメール送信用の `users.messages.send` があります。

参考:

- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
- https://developers.google.com/workspace/gmail/api/auth/scopes

### 12.3 OAuth

Google 連携を使う場合は、Google OAuth 2.0 の Web Server Flow を使います。

方針:

- Google 連携は任意
- ユーザーごとに接続
- 必要な scope だけ要求
- refresh token は暗号化保存
- 管理者が Google 連携を無効化できる

参考:

- https://developers.google.com/identity/protocols/oauth2/web-server

## 13. PC / スマホ UI

### 13.1 PC

3 ペイン構成です。

- 左: フィルタ、プロジェクト、タグ
- 中央: タスク一覧 / カンバン / カレンダー
- 右: タスク詳細

キーボード操作:

- `N`: 新規タスク
- `/`: 検索
- `E`: 編集
- `Ctrl + Enter`: 保存
- `Space`: 完了切り替え

### 13.2 スマホ

下部ナビを使います。

- 今日
- 追加
- 受信箱
- 検索
- 通知

スマホでは、次の操作を最優先にします。

- 見る
- 追加する
- 完了する
- コメントする
- 期限を変える

詳細機能は下から開くシートにまとめます。

## 14. API 設計

### 14.1 タスク

- `GET /tools/to_bell/api/tasks`
- `POST /tools/to_bell/api/tasks`
- `GET /tools/to_bell/api/tasks/<id>`
- `PUT /tools/to_bell/api/tasks/<id>`
- `DELETE /tools/to_bell/api/tasks/<id>`
- `POST /tools/to_bell/api/tasks/<id>/complete`
- `POST /tools/to_bell/api/tasks/<id>/reopen`

### 14.2 サブタスク

- `POST /tools/to_bell/api/tasks/<id>/subtasks`
- `PUT /tools/to_bell/api/subtasks/<id>`
- `DELETE /tools/to_bell/api/subtasks/<id>`

### 14.3 コメント

- `GET /tools/to_bell/api/tasks/<id>/comments`
- `POST /tools/to_bell/api/tasks/<id>/comments`

### 14.4 通知

- `GET /tools/to_bell/api/notifications`
- `POST /tools/to_bell/api/notifications/<id>/read`
- `POST /tools/to_bell/api/notifications/read-all`
- `POST /tools/to_bell/api/notifications/<id>/resolve`

### 14.5 Push

- `GET /tools/to_bell/api/push/public-key`
- `POST /tools/to_bell/api/push/subscribe`
- `POST /tools/to_bell/api/push/unsubscribe`
- `POST /tools/to_bell/api/push/test`

### 14.6 テンプレート

- `GET /tools/to_bell/api/templates`
- `POST /tools/to_bell/api/templates`
- `PUT /tools/to_bell/api/templates/<id>`
- `DELETE /tools/to_bell/api/templates/<id>`
- `POST /tools/to_bell/api/templates/<id>/instantiate`

## 15. 段階的実装

### Phase 1: To Bell の核

目的:

シンプル Todo として毎日使える状態にする。

機能:

- タスク作成・編集・完了
- 今日ビュー
- 受信箱
- 期限切れ
- 担当者
- 優先度
- ステータス
- サブタスク
- コメント
- タグ
- タスク詳細
- PC / スマホ対応
- アプリ内通知
- ダッシュボードの To Bell カード通知マーク

### Phase 2: 仕事の管理ツール化

目的:

チームや部署単位の仕事を扱えるようにする。

機能:

- プロジェクト
- 確認依頼
- 差戻し
- 変更履歴
- 添付
- 保存済みフィルタ
- ユーザー作成テンプレート
- カンバン
- カレンダー

### Phase 3: DSTT 司令塔化

目的:

他ツールの「やること」を To Bell に集約する。

機能:

- CloudShift 休暇種別変更申請の通知統合
- ShifterSync カード通知マーク
- siteplus 紐付け
- pluslist 担当者参照
- car_inspe 期限タスク
- FILE POST 期限通知
- PowerVote 回答期限通知
- bus_pricing 確認依頼

### Phase 4: PC / iPhone 通知

目的:

ブラウザを閉じていても重要通知に気づけるようにする。

機能:

- PWA 化
- Service Worker
- Web Push
- iPhone ホーム画面アプリ対応
- 通知設定
- テスト通知
- 端末別購読管理

### Phase 5: Google 連携

目的:

普段使いの Gmail / Google Calendar と必要最小限につなぐ。

機能:

- Google OAuth 接続
- 重要タスクだけ Google Calendar に追加
- To Bell から確認依頼メール送信
- Gmail から手動でタスク化する導線

## 16. 実装ファイル案

### サーバー

- `app/tools/to_bell.py`
- `app/services/to_bell_service.py`
- `app/services/to_bell_notifications.py`
- `app/services/to_bell_push.py`
- `app/tool_notifications.py`
- `app/templates/to_bell.html`
- `app/static/to_bell/to_bell.js`
- `app/static/to_bell/to_bell.css`
- `app/static/to_bell/service-worker.js`

### 既存ファイル更新

- `app/navigation.py`
- `app/__init__.py`
- `app/manuals.py`
- `app/templates/index.html`
- `app/static/css/style.css`

### テスト

- `tests/test_to_bell.py`
- `tests/test_to_bell_notifications.py`
- `tests/test_tool_notifications.py`
- `tests/test_to_bell_permissions.py`

## 17. 最初に作るべき MVP

最初の完成形は、あえて次に絞ります。

- タスクをすぐ作れる
- 今日やることが見える
- 期限切れが見える
- 自分の担当が見える
- サブタスクで進捗が見える
- コメントできる
- スマホで完了できる
- To Bell カードに未読・要対応マークが出る
- CloudShift の休暇種別変更申請を参考に、通知の既読/要対応を分ける

これで「Todo アプリ」として成立し、次の段階で「仕事の司令塔」へ広げられます。

## 18. 判断メモ

優先するもの:

- すぐ入力できること
- 今日やることが迷わず見えること
- 通知がカードにも出ること
- PC とスマホの両方で破綻しないこと
- 他ツール連携をあとから足せること
- テンプレートはユーザーが作ること

後回しにするもの:

- AI テンプレート生成
- メールクライアント的機能
- Google Calendar の完全同期
- 高度な分析レポート
- 全ツール連携の同時実装

To Bell は、小さく始めるほど強くなります。最初は軽い Todo と通知カードから入り、徐々に DSTT 全体の仕事を束ねる司令塔へ育てるのが最も安全です。

## 19. 使いやすさ改善（2026-06 追加）

現場フィードバックを受けた改善。既存の連携（CloudShift / 健診PLUS / Googleカレンダー）を壊さないことを前提に、次を追加した。

### 19.1 プロジェクトへのタスク追加

- プロジェクトを選択中（`project_id` で絞り込み中）にタスクを追加すると、そのプロジェクトに自動で紐づける。
- クイック追加・新規タスクポップアップの両方で有効。

### 19.2 連携タスク専用フィルタと自動削除

- CloudShift / 健診PLUS / Googleカレンダー由来の自動生成タスク（`source_tool` が設定されたタスク）は、通常フィルタ・カンバン・カレンダー・サマリー件数から除外する。
- 専用フィルタ「🔗 連携」を新設し、自動生成タスクはそこにのみ表示する。
- 自動生成タスクは追加（`created_at`）から既定 30 日（`INTEGRATION_TASK_RETENTION_DAYS`）を過ぎると、日次クリーンアップ（`cleanup_expired_records`）で削除する。
- 削除は ToBell の DB 行のみを消す。Googleカレンダーの予定には触れない（取り込みタスクは `gcal_event_id` を持たないため、カレンダー API を呼ばない）。

### 19.3 新規タスクのポップアップ追加

- ヒーローの「新規」ボタンは、クイック追加にフォーカスするだけでなく、状態・優先度・担当・プロジェクト・タグ・ピン留め・通知日時まで設定できるポップアップ（`#tb-newtask-modal`）を開く。

### 19.4 プロジェクトの「日付」

- プロジェクトに任意の日付 `due_at` を追加。未指定なら従来どおりサイドバーのみに表示。
- 日付を指定したプロジェクトは、タスク一覧とカレンダーに「1つのタスク」として表示し、クリックでそのプロジェクトのタスク一覧へ切り替える。

### 19.5 カレンダーの操作

- 日付の数字だけでなく「セル全体」を操作対象にした。
- 追加・操作は左クリックではなく、右クリック（タッチ端末は長押し／タップ）で操作メニューを開く。メニューからタスク追加・プロジェクト追加、タスク／プロジェクトの削除・完了などができる。

### 19.6 タスク名による一括削除

- 設定（⚙）→「一括削除」タブから、タスク名（完全一致／部分一致）でまとめて削除できる。
- 削除前に件数とサンプルを確認できる。対象は実行ユーザーが操作可能（作成・担当・確認者）なタスクのみ。

## 20. 仕様変更と不具合修正（2026-08 追加）

全体のバグチェックを受けた修正と、2件の仕様変更。既存の連携（CloudShift / 健診PLUS /
Googleカレンダー）と共有リンクの使い勝手を壊さないことを前提にしている。

### 20.1 仕様変更: プロジェクトを開いた状態での追加

- プロジェクトで絞り込み中にタスクを追加すると、そのプロジェクトのタスクになる。
- 対象は クイック追加 / 新規タスクポップアップ / テンプレートからの作成 / カレンダーの
  セル右クリック追加 のすべて。
- 画面上でも分かるようにする（クイック追加の入力欄プレースホルダ、ポップアップの注記）。
  ポップアップの「プロジェクト」欄で変更もできる。

### 20.2 仕様変更: 期日未指定タスクは「追加した日の終日」

- 期日を指定せずに追加したタスクは、`due_at = 追加日 23:59:59`（終日）として保存する。
  期日なしタスクはカレンダー・期限ビューに一切現れず、Googleカレンダーへも送れないため。
- 終日タスクは「時刻未指定」として扱うので、プッシュ／前面通知は飛ばない
  （`has_explicit_notification_time()`）。
- 自動で入れた日付には `due_auto` の印を付け、「期限切れ」の判定から外す
  （利用者が決めた締切ではないため）。利用者が期日を変えた時点で `due_auto` は落ち、
  以後は通常どおり期限切れの対象になる。画面でも「2026-08-20 終日（自動）」と表示し、
  赤バッジも出さない。
- 「受信箱」の定義を「完了・アーカイブ以外のすべてのタスク」に変更した。
  すべてのタスクに日付が入るようになり、「期日なし」を条件にすると常に空になるため。
- 詳細パネルに「終日」チェックを追加した。`datetime-local` は秒を持てないため、
  終日タスクを開いてそのまま保存すると 23:59:59 → 23:59:00 になり「時刻指定あり＝通知する」
  タスクに化けていた。終日フラグを一緒に送ることでこれを防ぐ。
- DB: `to_bell_tasks.due_auto`（BOOLEAN NOT NULL DEFAULT 0）を追加。既存行は
  「利用者が入れた期日」として扱う。起動時の ALTER（`app/__init__.py`）で追加する。

### 20.3 権限の抜け道をふさぐ

- ToBell の連携トグルは利用者が自分で押せるため、それだけを条件にすると sensitive ツールの
  データへ到達できてしまう。`is_enabled()` に連携先ツールのアクセス権判定を一本化した。
  - `pluslist.linkage` → `pluslist`、`siteplus.linkage` → `siteplus`
  - `cloudshift.*` → `shiftersync`（オプトインした全ユーザーへブロードキャストされるため）
  - 健診PLUS は宛先がレコード側で決まる（自己選択できない）ため対象外。
- 共有リンク（ログイン不要）は、ToBell の外へ届く操作・取り返しのつかない操作・他人へ
  影響する操作をブロックする（`_SHARE_BLOCKED_ENDPOINTS` を blueprint の before_request で判定）。
  画面側でも該当のボタン（設定⚙・削除・紐付け・共有リンク）を出さない
  （`data-tb-share` で判定）。押せるのに 403 になる導線を残さないため。
- プロジェクトの並び替えは利用者個人の設定（`ToBellUserSettings.preferences.project_order`）に
  保存する。共有カラム `sort_order` を書き換えると、一人の並べ替えが同じ営業所の全員の
  画面を変えてしまうため。`sort_order` は作成者が決める既定の並び（個人設定が無い人に適用）
  として残す。
- `source_tool` などの連携メタ情報は、リクエスト経由の作成では受け付けない。

### 20.4 Googleカレンダー取り込みの修正

- 取り込んだ予定が「🔗 連携」フィルタにしか出ず、カレンダー表示にも出ていなかった。
  カレンダー表示には並べる（リスト・カンバンは従来どおり除外）。
- 取り込む範囲（モード）を変えても `syncToken` が据え置きで、既存の予定が取り込まれなかった。
  モード変更時にトークンをリセットし、次回フル取得に戻す。
- 初回取得の未来窓を 90日 → 365日 に広げた（差分同期は未変更の予定を返さないため、
  窓の外の予定は永久に取り込まれない）。
- 末尾「TB」マーカーはモードによらず常に取り除く（モード変更で既存タスク名が
  「請求」→「請求 TB」に書き換わっていた）。ただし直前が英数字なら語の一部とみなして残す
  （"定例MTB" が "定例M" に化けないようにするため）。
- 連携タスクの30日自動削除は「期日も過ぎたもの」に限定した。先の予定を消すと、
  差分同期では復活しない。
- 「今すぐ取り込み」の結果に対象外件数を出し、新規・更新があれば「🔗 連携」フィルタへ切り替える。

### 20.5 その他の修正

- 一括削除の `%` / `_` を LIKE ワイルドカードとして扱わない（1文字で全件削除できた）。
- タスクの完全削除・一括削除・日次クリーンアップで、添付ファイルの実体も消す。
- アーカイブ時に Google カレンダーの予定を消せたときだけ、タスク側の参照も外す。
  消せなかった（接続断・未接続）ときは参照を残す。消せていないのに参照を捨てると、
  ToBell から手が届かない予定がカレンダーに取り残される。
  イベント更新（PATCH）の 404 を成功扱いにしない。
- 通知パネルからリンク先を開けるようにし、個別の「対応済み」ボタンを追加。
  タスクに紐づかない通知（プロジェクト通知）は既読で解決済みにする。
- カレンダー専用プロジェクトのタスクを要対応バッジからも除外（バッジと一覧の不一致）。
- 「期限切れ」を現在時刻基準に変更（終日タスクは当日中は期限切れにしない）。
- アーカイブフィルタを追加（アーカイブしたタスクを見て戻せる導線が無かった）。
- 詳細パネルに確認者（reviewer）を追加。テンプレートの公開範囲を画面から選べるようにした。
- Service Worker のキャッシュを ToBell の資産だけに限定し、network-first に変更
  （DSTT 全体の静的ファイルが更新されなくなっていた）。
- スマホのブラウザ（PWA以外）でもカンバンの状態移動ができるようにした。
- 通知（プッシュ／アプリ内）のリンクは、対象が現在のフィルタに無くても単体取得して開く。
- FILEPOST へ退避した添付を、詳細の「添付」欄にも表示する。
- 一覧APIの N+1 を解消（サブタスク・コメント・添付・タグを一括ロード）。
- 連携の一斉通知で宛先のアクセス権をまとめて解決する（`usernames_with_tool_access`）。
  1人ずつ判定すると 40人で 122 クエリ → 5 クエリ。
- 共有トークンの `last_used_at` 更新を1時間に1回へ間引いた。

### 20.6 未対応（既知の制約）

- ブラウザに無効な `remember_token` クッキーが残っていると、Flask-Login の読み込み順の
  都合で共有リンクが1回目だけ失敗する（クッキーが破棄されるため、再読み込みで通る）。
  private API を使わずに直す手段が無く、失敗しても「ログイン画面に戻る」だけのため据え置き。

