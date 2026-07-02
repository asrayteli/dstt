# DSTT Project

DSTT（DaishintoTools）は、Flask ベースの社内業務用ツール集です。ファイル処理、PDF/画像編集、社員・現場マスタ、シフト、車検証 OCR、貸切バス料金計算などをひとつのダッシュボードから利用できます。

## 主な機能

- ダッシュボードとカテゴリ別ナビゲーション
- ログイン、管理者、ツール別アクセス制御
- 社員名簿 PLUS、現場リスト PLUS、ShifterSync / CloudShift
- PowerPDF、PowerImager、Power Flow、PowerCSV
- FILE POST、PowerVote、車検証ツール、貸切料金計算ツール
- ツールごとのマニュアル表示

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python init_database.py
python run.py
```

既定では SQLite を使います。必要に応じて次の環境変数を設定してください。

- `DSTT_SECRET_KEY`: Flask の secret key
- `DSTT_DATABASE_URI`: SQLAlchemy の DB URI
- `DSTT_ALLOW_SELF_REGISTRATION`: 自己登録の許可
- `DSTT_SESSION_COOKIE_SECURE`: Secure Cookie の有効化
- `DSTT_DATA_ENCRYPTION_KEY`: 暗号化対象データ用キー
- `DSTT_GOOGLE_CLIENT_ID` / `DSTT_GOOGLE_CLIENT_SECRET`: ToBell の Google カレンダー連携用 OAuth クライアント情報
- `DSTT_TIMEZONE`: カレンダー連携で使うタイムゾーン（既定 `Asia/Tokyo`）
- `DSTT_MAX_CONTENT_LENGTH_MB`: リクエストボディ上限（MB）。既定は FILE POST の1リクエスト最大に合わせた `10304`（≒10GiB+余裕）。`0` で無制限
- `DSTT_ACTIVITY_LOG_RETENTION_DAYS`: ツール利用ログ（user_activity_logs）の保持日数。設定すると日次で期限切れ行を削除。未設定/`0` は無制限保持
- `DSTT_ENABLE_HSTS`: HTTPS応答への Strict-Transport-Security 付与（既定有効。`0` で無効）

### SQLite 運用メモ

本番既定の SQLite は WAL モードで動作します（`users.db` の隣に `-wal` / `-shm` ファイルが生成されます）。DB をファイルコピーでバックアップする場合は 3 ファイルをセットで取るか、`sqlite3 users.db ".backup backup.db"` を使ってください。

### DSTT 共通メール送信基盤（SMTP）

DSTT 全体で再利用できる共通メール基盤（`app/services/mail_service.py`）です。各ツールは `queue_mail()` でキューに積み、バックグラウンドスケジューラが SMTP で送信します。設定は環境変数（または `app.config`）で行います。**未設定でもアプリは壊れず、メールはキューに保留され、設定が入った時点で自動送信されます。**

| 環境変数 | 説明 | 既定 |
| --- | --- | --- |
| `DSTT_SMTP_HOST` | SMTP サーバーホスト名（未設定なら送信無効＝保留） | （なし） |
| `DSTT_SMTP_PORT` | ポート | security に応じ 465/587/25 |
| `DSTT_SMTP_USER` | 認証ユーザー（任意） | （なし） |
| `DSTT_SMTP_PASSWORD` | 認証パスワード（任意） | （なし） |
| `DSTT_SMTP_SECURITY` | `none` / `starttls` / `ssl` | `starttls` |
| `DSTT_MAIL_FROM` | 送信元アドレス | `DSTT_SMTP_USER` |
| `DSTT_MAIL_FROM_NAME` | 送信元表示名（任意） | （なし） |
| `DSTT_MAIL_TIMEOUT` | SMTP タイムアウト秒 | `20` |
| `DSTT_MAIL_SCHEDULER` | キュー送信スケジューラの有効/無効 | 有効 |
| `DSTT_MAIL_DISPATCH_INTERVAL_SECONDS` | 送信間隔秒（最小15） | `60` |

設定例（Gmail のアプリパスワードを使う場合）:

```powershell
$env:DSTT_SMTP_HOST = "smtp.gmail.com"
$env:DSTT_SMTP_PORT = "587"
$env:DSTT_SMTP_SECURITY = "starttls"
$env:DSTT_SMTP_USER = "your-account@gmail.com"
$env:DSTT_SMTP_PASSWORD = "xxxxxxxxxxxxxxxx"   # アプリパスワード
$env:DSTT_MAIL_FROM = "your-account@gmail.com"
$env:DSTT_MAIL_FROM_NAME = "DSTT 自動通知"
```

#### メール受信（IMAP）

送信（SMTP）の対として、IMAP でメールを受信して管理者ページの **Webメーラー** で閲覧・返信できます。受信は `app/services/mail_inbox.py` がバックグラウンドで定期ポーリングし、`inbound_mails` テーブルへ保存します（IMAP UID で冪等取り込み。サーバー側の既読は変更せず、既読状態はアプリ内で保持）。**未設定でもアプリは壊れず、受信が無効になるだけです。**

| 環境変数 | 説明 | 既定 |
| --- | --- | --- |
| `DSTT_IMAP_HOST` | IMAP ホスト名（未設定なら受信無効） | （なし） |
| `DSTT_IMAP_PORT` | ポート | security に応じ 993/143 |
| `DSTT_IMAP_USER` / `DSTT_IMAP_PASSWORD` | ログイン情報 | （なし） |
| `DSTT_IMAP_SECURITY` | `ssl` / `starttls` / `none` | `ssl` |
| `DSTT_IMAP_MAILBOX` | 取り込むメールボックス | `INBOX` |
| `DSTT_IMAP_POLL_INTERVAL_SECONDS` | ポーリング間隔秒（最小30） | `300` |
| `DSTT_IMAP_FETCH_LIMIT` | 1回の取り込み最大件数 | `50` |
| `DSTT_IMAP_RETENTION` | 受信トレイ保持件数（0で無制限） | `1000` |
| `DSTT_MAIL_INBOX_SCHEDULER` | 受信ポーリングの有効/無効 | 有効 |

設定が済めば、**管理者ページ →「Webメーラー」タブ**で次のことができます。

- **受信トレイ**: 新着の取り込み（「今すぐ受信」）、一覧・本文閲覧（HTML は sandbox iframe で安全に表示）、既読化、検索、返信、削除。
- **新規作成**: 任意の宛先（複数可）へ送信、または予約キュー投入。
- **送信キュー**: 送信状況の確認と、失敗・保留分の再送。

送信・受信とも未設定でもタブは開けます（送信分は保留として積まれます）。

### ToBell の Google カレンダー連携

ToBell のタスク期限を、利用者個人の Google カレンダー（メイン）へ片方向で送れます。利用するには Google Cloud で OAuth クライアント（種別: ウェブアプリケーション）を作成し、上記の環境変数を設定してください。リダイレクト URI には `https://<ホスト>/tools/to_bell/api/google/callback` を登録します。連携はユーザーごとにオプトイン（既定 OFF）で、ToBell の「設定 → DSTT連携」で有効化したうえで「Googleカレンダー」タブから各自のアカウントを接続します。

## テスト

```powershell
python -m pytest
```

## 開発メモ

ツール追加時は、通常次のファイルを更新します。

- `app/tools/<tool_key>.py`
- `app/templates/<tool_key>.html`
- `app/navigation.py`
- `app/manuals.py`
- `app/__init__.py`
- `tests/test_<tool_key>.py`

アップデート案は [docs/dstt_update_proposals.md](docs/dstt_update_proposals.md) にまとめています。

To Bell の設計図は [docs/tobell_design.md](docs/tobell_design.md) にまとめています。
