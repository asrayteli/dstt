from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from .models import db, User

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# ログインページ
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('main.index'))
        else:
            flash('ユーザー名またはパスワードが正しくありません', 'error')

    return render_template('login.html')

# ログアウト処理
@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

# ユーザー登録（初期登録用・削除可能）
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm = request.form.get('confirm')

        if not username or not password or password != confirm:
            flash('入力に誤りがあります', 'error')
        elif User.query.filter_by(username=username).first():
            flash('このユーザー名は既に使われています', 'error')
        else:
            new_user = User(
                username=username,
                password_hash=generate_password_hash(password)
            )
            db.session.add(new_user)
            db.session.commit()
            flash('登録が完了しました。ログインしてください。', 'success')
            return redirect(url_for('auth.login'))

    return render_template('register.html')
