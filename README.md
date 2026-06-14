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

設定が済めば、**管理者ページ →「メール送信」タブ**から任意の宛先へメールを送信でき、送信キューの状態確認・失敗分の再送も行えます。送信状況の確認だけなら未設定でも開けます（保留として積まれます）。

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
