from flask import Blueprint, render_template
from flask_login import login_required, current_user

# トップページ用のBlueprint
main = Blueprint("main", __name__)

@main.route("/")
@login_required
def index():
    # ログインユーザーの名前を取得（nameフィールドが空の場合は「ゲスト」を表示）
    user_name = current_user.name if current_user.name and current_user.name != 'unknown' else 'ゲスト'
    is_admin = current_user.username == "3243012"
    
    return render_template("index.html", user_name=user_name, is_admin=is_admin)