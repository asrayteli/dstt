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
