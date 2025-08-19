import os
import sys

def create_tool(tool_name):
    if not tool_name.islower():
        print("ツール名は小文字のアルファベットとアンダースコア（_）で構成してください。")
        sys.exit(1)

    tools_dir = "app/tools"
    templates_dir = "app/templates"
    index_file = os.path.join(templates_dir, "index.html")

    # 1. Pythonファイル
    tool_file = os.path.join(tools_dir, f"{tool_name}.py")
    with open(tool_file, "w", encoding="utf-8") as f:
        f.write(f"""from flask import Blueprint, render_template, request

{tool_name}_bp = Blueprint("{tool_name}", __name__, url_prefix="/tools/{tool_name}")

@{tool_name}_bp.route("/", methods=["GET", "POST"])
def {tool_name}():
    result = None
    if request.method == "POST":
        # ここにロジックを追加
        pass
    return render_template("{tool_name}.html", result=result)
""")
    print(f"{tool_name}.py を作成しました。")

    # 2. HTMLテンプレート
    template_file = os.path.join(templates_dir, f"{tool_name}.html")
    with open(template_file, "w", encoding="utf-8") as f:
        f.write(f"""{{% extends "base.html" %}}

{{% block content %}}
<div class="max-w-xl mx-auto bg-white p-6 rounded-2xl shadow mt-6">
  <h2 class="text-xl font-bold mb-4">🛠️ {tool_name} Tool</h2>
  <form method="POST">
    <!-- 入力フォーム -->
    <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">送信</button>
  </form>

  {{% if result %}}
  <div class="mt-4 text-green-700 font-semibold">
    ✅ 結果: {{ '{{ result }}' }}
  </div>
  {{% endif %}}
</div>
{{% endblock %}}
""")
    print(f"{tool_name}.html を作成しました。")

    # 3. __init__.py を更新
    init_file = "app/__init__.py"
    with open(init_file, "a", encoding="utf-8") as f:
        f.write(f"""
from .tools.{tool_name} import {tool_name}_bp
app.register_blueprint({tool_name}_bp)
""")
    print(f"__init__.py に {tool_name}_bp を追加しました。")

    # 4. index.html にカードを追記
    card_html = f"""
    <div class="bg-white p-6 rounded-2xl shadow hover:shadow-lg transition">
      <h2 class="text-lg font-semibold mb-2">🛠️ {tool_name}</h2>
      <p class="text-sm text-gray-600 mb-4">説明をここに追加</p>
      <a href="/tools/{tool_name}" class="text-blue-600 hover:underline">開く →</a>
    </div>
"""

    # index.html を読み込み
    with open(index_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # </div> の直前（gridの最後）に挿入
    insert_idx = next(
        (i for i, line in reversed(list(enumerate(lines))) if "</div>" in line),
        len(lines)
    )
    lines.insert(insert_idx, card_html)

    # 書き戻し
    with open(index_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print("index.html にツールカードを追加しました。")
    print(f"{tool_name} ツールの追加が完了しました。")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python generate_tool.py <tool_name>")
        sys.exit(1)

    tool_name = sys.argv[1]
    create_tool(tool_name)
