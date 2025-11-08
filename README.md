# DSTT Project

Flaskベースの業務用ツール集

## 📋 目次

- [概要](#概要)
- [主な機能](#主な機能)
- [システム要件](#システム要件)
- [セットアップ手順](#セットアップ手順)
- [使い方](#使い方)
- [本番環境での起動](#本番環境での起動)
- [トラブルシューティング](#トラブルシューティング)
- [新しいツールの追加](#新しいツールの追加)

## 概要

DSTTは、日常業務で必要となる様々なツールを一つのWebアプリケーションにまとめた業務支援システムです。Flaskフレームワークをベースに構築されており、直感的なWebインターフェースで様々な作業を効率化できます。

## 主な機能

DSTTには以下のツールが含まれています：

| ツール名 | 説明 | URL |
|---------|------|-----|
| 📅 日付計算 | 日付の計算や期間の算出を行います | `/tools/datecalc` |
| 🧮 計算機 | 基本的な計算機能を提供します | `/tools/calc` |
| 📝 ファイル名変更 | ファイル名の一括変更を行います | `/tools/rename` |
| 🗜️ 圧縮ツール | ファイルの圧縮・解凍を行います | `/tools/compress` |
| 📊 CSVツール | CSVファイルの編集や変換を行います | `/tools/csvtool` |
| 🔐 パスワード生成 | 安全なパスワードを生成します | `/tools/password_tool` |
| 📆 勤務日管理 | 勤務日の管理や計算を行います | `/tools/workday` |
| 📄 PDF処理 | PDFファイルの結合・分割などを行います | `/tools/pdf_power` |
| 📤 ファイル共有 | ファイルの共有機能を提供します | `/tools/share` |
| 🚗 車両検査 | 車両検査情報の管理を行います | `/tools/car_inspe` |
| 👥 シフト同期 | シフト情報の同期や管理を行います | `/tools/shiftersync` |
| 🏖️ 休暇管理 | 休暇の申請や管理を行います | `/tools/leave_mgr` |
| 👤 ユーザー管理 | ユーザーアカウントの管理を行います | `/tools/user_management` |
| 📊 月次レポート | 月次レポートの生成を行います | `/tools/monthly_generator` |

## システム要件

- Python 3.8以上
- pip（Pythonパッケージマネージャー）
- 対応OS：Windows、macOS、Linux

## セットアップ手順

### 1. プロジェクトのクローン

```bash
git clone <リポジトリURL>
cd dstt
```

### 2. 仮想環境の作成（推奨）

Windowsの場合：
```bash
python -m venv dstt
dstt\Scripts\activate
```

macOS/Linuxの場合：
```bash
python3 -m venv dstt
source dstt/bin/activate
```

### 3. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 4. データベースの初期化

```bash
python init_database.py
```

このコマンドを実行すると、ユーザー認証用のデータベースが作成されます。

### 5. 初回ユーザーの作成

```bash
python create_user.py
```

以下の情報を入力してください：
- **ユーザーID**: ログイン時に使用するID
- **日本語名**: 表示名（例：田中太郎）
- **パスワード**: ログイン用パスワード（確認のため2回入力）

## 使い方

### 開発環境での起動

```bash
python run.py
```

サーバーが起動したら、ブラウザで以下のURLにアクセスします：

```
http://localhost:5000
```

### ログイン

1. ブラウザでアプリケーションのURLにアクセス
2. ログイン画面が表示されます
3. `create_user.py`で作成したユーザーIDとパスワードを入力
4. ログインボタンをクリック

### ツールの使用

1. ログイン後、ホーム画面に各ツールのカードが表示されます
2. 使用したいツールのカードをクリック
3. 各ツールの画面で必要な操作を行います

### ログアウト

画面右上のログアウトボタンをクリックすると、ログアウトできます。

## 本番環境での起動

本番環境では、Gunicornを使用した起動を推奨します。

### Gunicornでの起動

```bash
gunicorn -c gunicorn.conf.py run:app
```

gunicorn.conf.pyには以下のような設定が含まれています：
- ワーカー数
- バインドアドレスとポート
- ログ設定

### リバースプロキシの設定（推奨）

本番環境では、NginxやApacheなどのリバースプロキシの背後でGunicornを動作させることを推奨します。

Nginxの設定例：
```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## トラブルシューティング

### ポート5000が既に使用されている

別のポートを使用する場合は、`run.py`の最終行を編集してください：

```python
app.run(host="0.0.0.0", port=8000, debug=True)  # ポート番号を変更
```

### データベースエラーが発生する

データベースをリセットする場合：

```bash
python init_database.py
python create_user.py
```

**注意**: この操作は既存のデータをすべて削除します。

### モジュールが見つからないエラー

依存パッケージを再インストールしてください：

```bash
pip install -r requirements.txt
```

### ログインできない

1. ユーザーが正しく作成されているか確認：
   ```bash
   python create_user.py
   ```
2. データベースファイル（instance/users.db）が存在するか確認
3. パスワードが正しいか確認（大文字・小文字を区別します）

## 新しいツールの追加

DSTTには新しいツールを簡単に追加できます。詳細な手順は`readme.txt`を参照してください。

### 基本的な手順

1. **Pythonファイルを作成**: `app/tools/`に新しいツールのPythonファイルを作成
2. **HTMLテンプレートを作成**: `app/templates/`にHTMLファイルを作成
3. **Blueprintを登録**: `app/__init__.py`に新しいツールを登録
4. **ホーム画面にリンクを追加**: トップページにツールのカードを追加

詳細な実装方法やサンプルコードは`readme.txt`に記載されています。

## セキュリティに関する注意事項

- 本番環境では`SECRET_KEY`を必ず変更してください（`app/__init__.py`）
- HTTPSを使用することを強く推奨します
- データベースファイルは適切なアクセス権限で保護してください
- 定期的にパスワードを変更してください

## ライセンス

このプロジェクトは内部業務用ツールです。

## サポート

問題が発生した場合や質問がある場合は、プロジェクト管理者に連絡してください。