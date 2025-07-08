✅ DSTT機能追加マニュアル（Flask版）
🗂 構成ルール（DSTTプロジェクト共通）
区分	内容
言語	Python（Flask）+ HTML（Jinja2）
構造	各ツールは app/tools/ に独立したモジュールとして配置
ルーティング	Blueprint を使用し /tools/<ツール名> にマウント
テンプレート	共通ベース：base.html、各ツール専用：templates/<ツール名>.html
URL	各ツールは /tools/<名前> にアクセス可能
登録場所	app/__init__.py に Blueprint 登録必須

🛠 手順：DSTTにツール機能を追加する
以下の手順で進めれば、1つのツールを完全に組み込めます。

✅ Step 1. Pythonファイルを追加
app/tools/ に新しいPythonファイルを作成
例：exampletool.py

以下のような構成で記述：

python
コピーする
編集する
# app/tools/exampletool.py

from flask import Blueprint, render_template, request

example_bp = Blueprint("example", __name__, url_prefix="/tools/example")

@example_bp.route("/", methods=["GET", "POST"])
def example():
    result = None
    if request.method == "POST":
        data = request.form.get("example_input")
        # 処理ロジック
        result = data[::-1]  # 例：文字列を反転

    return render_template("exampletool.html", result=result)
✅ Step 2. HTMLテンプレートを作成
app/templates/ にテンプレートファイルを作成
例：exampletool.html

以下を記述（base.html を継承）：

html
コピーする
編集する
{% extends "base.html" %}

{% block content %}
<div class="max-w-xl mx-auto bg-white p-6 rounded-2xl shadow mt-6">
  <h2 class="text-xl font-bold mb-4">🛠️ Example Tool</h2>
  <form method="POST">
    <label class="block mb-2">文字列を入力してください：</label>
    <input type="text" name="example_input" class="w-full border rounded px-3 py-2 mb-4" required>
    <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">送信</button>
  </form>

  {% if result %}
  <div class="mt-4 text-green-700 font-semibold">
    ✅ 結果: {{ result }}
  </div>
  {% endif %}
</div>
{% endblock %}
✅ Step 3. __init__.py に登録
python
コピーする
編集する
# app/__init__.py

from .tools.exampletool import example_bp
app.register_blueprint(example_bp)
✅ Step 4. ホーム画面にリンクを追加（index.html）
html
コピーする
編集する
<div class="bg-white p-6 rounded-2xl shadow hover:shadow-lg transition">
  <h2 class="text-lg font-semibold mb-2">🛠️ Example Tool</h2>
  <p class="text-sm text-gray-600 mb-4">文字列を反転するテストツール</p>
  <a href="/tools/example" class="text-blue-600 hover:underline">開く →</a>
</div>
✅ Step 5. 起動・確認
bash
コピーする
編集する
# 開発サーバー起動
python run.py

# アクセス
http://localhost:5000/tools/example
🧼 命名規則（推奨）
種類	命名例
Pythonファイル	tools/datecalc.py（小文字+_）
Blueprint名	datecalc_bp（ツール名 + _bp）
HTMLテンプレート	datecalc.html（ツール名）
ルートURL	/tools/datecalc

📝 追加機能テンプレートを自動生成する？（今後）
希望があれば、以下のようなツール追加ジェネレータスクリプトも導入可能です：

bash
コピーする
編集する
python manage.py add_tool <toolname>
これにより：

tools/<toolname>.py

templates/<toolname>.html

__init__.py に自動追記

を自動生成します。

✅ 最終確認チェックリスト（完了前に確認）
✅	チェック内容
☐ Pythonファイルを tools/ に作成したか	
☐ Blueprint に正しい url_prefix を指定したか	
☐ HTMLテンプレートを templates/ に作成したか	
☐ __init__.py に app.register_blueprint() を追記したか	
☐ ホーム画面にカードを追加したか	
☐ サーバーを再起動したか（変更反映のため）	