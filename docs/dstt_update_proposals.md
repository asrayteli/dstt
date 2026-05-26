# DSTT 調査メモとアップデート提案

作成日: 2026-05-26

## 現状サマリ

DSTT は Flask ベースの社内業務ツール集で、`NAV_ITEMS` と Flask Blueprint を中心にツールを追加していく構造です。現時点でダッシュボード掲載ツールは 22 件、テストは 311 件あります。

主な領域は次のとおりです。

- 業務マスタ連携: `pluslist`, `siteplus`, `shiftersync`, `cloudshift`, `subject_analysis_tool`, `bus_pricing`
- ファイル処理: `rename`, `compress`, `csvtool`, `pdf_power`, `share`
- 画像・作図: `color_extract`, `powerstamp`, `power_imager`, `power_flow`
- 管理・セキュリティ: `user_management`, `password_tool`, アクセス制御、ファイル暗号化
- 個別業務支援: `car_inspe`, `leave_mgr`, `monthly_generator`, `powervote`

最近の更新は CloudShift のシフト編集、同期、ツール管理カテゴリに寄っており、DSTT は「単発便利ツール」から「社員・現場・勤務・料金をつなぐ業務基盤」に進化しています。

## すぐ効くアップデート案

1. ダッシュボード検索とお気に入り
   - ツール数が増えたため、トップ画面に検索欄、最近使ったツール、お気に入りピン留めを追加する。
   - 低リスクで全ユーザーに効く改善です。

2. ツール状態の見える化
   - 管理画面に「ツール別の公開状態、アクセス種別、最終更新、マニュアル有無、テスト有無」を一覧表示する。
   - 新規ツール追加時の抜け漏れ確認にも使えます。

3. CloudShift の操作ログビュー
   - `cloudshift.py` は履歴・リビジョン・仮保存を持っているため、管理者向けに「誰が、いつ、何を変えたか」を検索できる画面を追加する。
   - シフト変更の説明責任と復旧判断がしやすくなります。

4. バックアップ・保守ページ
   - SQLite DB、`var/`、アップロード系データの容量、最終バックアップ日時を管理画面で確認する。
   - 手動バックアップ、古い一時ファイル削除、ストレージ警告を段階的に追加できます。

5. README と運用手順の拡充
   - 現在の README は最小限なので、セットアップ、環境変数、管理者作成、テスト実行、データ保管場所を追記する。
   - 引き継ぎ・復旧のコストを下げられます。

## 新規ツール候補

### Tool Health Dashboard

DSTT 自体の健康診断ツール。各 Blueprint の登録状況、ナビゲーション登録、マニュアル登録、アクセス制御、主要ディレクトリ容量、DB 接続、暗号化キー設定をチェックします。

実装しやすさ: 高  
効果: 高  
理由: 既存の `NAV_ITEMS`, `MANUALS`, `ToolSettings`, Flask route map を使えるため、少ない追加で管理価値が高いです。

### CSV Import Doctor

pluslist、siteplus、subject_analysis_tool、monthly_generator で扱う CSV/Excel の事前診断ツール。文字コード、必須列、空欄、重複、日付形式、契約コード形式をアップロード前に確認します。

実装しやすさ: 中  
効果: 高  
理由: CSV 取り込み系ツールが多く、失敗理由を共通化すると問い合わせ削減に効きます。

### 業務リンクマップ

社員、現場、契約コード、車両、シフト、料金計算のつながりを検索できる横断ビュー。たとえば現場名から siteplus、CloudShift、車検証、貸切料金関連をたどれるようにします。

実装しやすさ: 中  
効果: 中から高  
理由: DSTT がマスタ連携型に育っているため、各ツールの情報を横断する入口があると運用が楽になります。

### 公開フォーム管理

PowerVote と FILE POST の公開 URL、期限、回答・受領状況を一括管理するツール。公開中リンクの棚卸し、期限切れ確認、QR 再発行を扱います。

実装しやすさ: 中  
効果: 中  
理由: 外部・非ログイン利用の入口をまとめて監査できます。

## 推奨ロードマップ

1. まずは README 拡充と Tool Health Dashboard を作る。
2. 次にダッシュボード検索・お気に入りを入れて日常利用を改善する。
3. その後、CSV Import Doctor でデータ投入の失敗を減らす。
4. CloudShift 操作ログビューとバックアップ・保守ページで運用品質を上げる。
5. 最後に業務リンクマップで、DSTT 全体を横断検索できる入口に育てる。

## 実装候補の第一弾

第一弾としては `tool_health` を推奨します。

想定ファイル:

- `app/tools/tool_health.py`
- `app/templates/tool_health.html`
- `app/navigation.py`
- `app/manuals.py`
- `app/__init__.py`
- `tests/test_tool_health.py`

主なチェック:

- `NAV_ITEMS` のキー重複
- Blueprint 登録漏れ
- マニュアル登録漏れ
- `ToolSettings` 登録漏れ
- sensitive ツールのアクセス制御対象漏れ
- `var/`, `instance/`, `app/static/*/uploads` の容量
- 暗号化キー必須設定の状態

このツールは DSTT の今後の追加開発を支える土台になるため、最初に作る価値が高いです。
