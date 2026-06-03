from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from .models import DsttLoginLog, User, UserLoginLog, db


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _record_successful_login(user: User) -> None:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    ip_address = forwarded_for.split(",", 1)[0].strip() if forwarded_for else request.remote_addr
    user_agent = (request.headers.get("User-Agent") or "")[:255]
    db.session.add(
        UserLoginLog(
            username=user.username,
            success=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
    db.session.add(
        DsttLoginLog(
            user_id=user.id,
            username=user.username,
            name=user.name,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
    db.session.commit()


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
            try:
                _record_successful_login(user)
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to record DSTT login log")
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
