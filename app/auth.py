from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from .models import User, UserLoginLog, db


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            login_user(user)
            db.session.add(
                UserLoginLog(
                    username=user.username,
                    success=True,
                    ip_address=request.remote_addr,
                    user_agent=request.user_agent.string,
                )
            )
            db.session.commit()
            return redirect(url_for("main.index"))

        if username:
            try:
                db.session.add(
                    UserLoginLog(
                        username=username,
                        success=False,
                        ip_address=request.remote_addr,
                        user_agent=request.user_agent.string,
                    )
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

        flash("ユーザー名またはパスワードが正しくありません", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if not current_app.config.get("ALLOW_SELF_REGISTRATION", False):
        abort(404)

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if not username or not password or password != confirm:
            flash("入力内容に誤りがあります", "error")
        elif User.query.filter_by(username=username).first():
            flash("このユーザー名は既に使われています", "error")
        else:
            new_user = User(
                username=username,
                password_hash=generate_password_hash(password),
            )
            db.session.add(new_user)
            db.session.commit()
            flash("登録が完了しました。ログインしてください。", "success")
            return redirect(url_for("auth.login"))

    return render_template("register.html")
