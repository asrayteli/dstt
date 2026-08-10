from time import monotonic, sleep
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from .models import DsttLoginLog, User, UserLoginLog, db
from .security.password_policy import password_policy_error


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

_FAILED_LOGIN_ATTEMPTS: dict[tuple[str, str], list[float]] = {}


def _client_ip() -> str | None:
    # ProxyFix(x_for=1) が信頼できるプロキシの付与した X-Forwarded-For を
    # remote_addr へ反映済み。生ヘッダの左端はクライアントが自由に偽装できる
    # ため参照しない（監査ログ汚染・ログイン失敗ディレイ回避の防止）。
    return request.remote_addr


def _user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:255]


def _is_safe_next_url(target: str | None) -> bool:
    if not target:
        return False
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return False
    return target.startswith("/") and not target.startswith("//")


def _next_url() -> str:
    candidate = request.values.get("next") or session.get("login_next")
    if _is_safe_next_url(candidate):
        session["login_next"] = candidate
        return candidate
    session.pop("login_next", None)
    return ""


def _consume_next_url() -> str:
    target = _next_url()
    session.pop("login_next", None)
    return target or url_for("main.index")


def _failure_key(username: str) -> tuple[str, str]:
    return ((_client_ip() or ""), username.lower())


def _prune_stale_attempts(now: float, window_seconds: int) -> None:
    """観測窓を過ぎたキーを破棄し、辞書の無限成長を防ぐ。"""
    if len(_FAILED_LOGIN_ATTEMPTS) < 1000:
        return
    stale_keys = [
        key
        for key, stamps in _FAILED_LOGIN_ATTEMPTS.items()
        if not stamps or now - stamps[-1] > window_seconds
    ]
    for key in stale_keys:
        _FAILED_LOGIN_ATTEMPTS.pop(key, None)


def _register_failed_attempt(username: str) -> None:
    if not username:
        return

    window_seconds = int(current_app.config.get("LOGIN_FAILURE_WINDOW_SECONDS", 300))
    key = _failure_key(username)
    now = monotonic()
    _prune_stale_attempts(now, window_seconds)
    attempts = [
        stamp
        for stamp in _FAILED_LOGIN_ATTEMPTS.get(key, [])
        if now - stamp <= window_seconds
    ]
    attempts.append(now)
    _FAILED_LOGIN_ATTEMPTS[key] = attempts[-20:]

    threshold = int(current_app.config.get("LOGIN_FAILURE_DELAY_THRESHOLD", 3))
    delay = float(current_app.config.get("LOGIN_FAILURE_DELAY_SECONDS", 0.6))
    if len(attempts) >= threshold and delay > 0 and not current_app.config.get("TESTING"):
        sleep(min(delay * (len(attempts) - threshold + 1), 2.0))


def _clear_failed_attempts(username: str) -> None:
    if username:
        _FAILED_LOGIN_ATTEMPTS.pop(_failure_key(username), None)


def _record_successful_login(user: User) -> None:
    ip_address = _client_ip()
    user_agent = _user_agent()
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


def _record_failed_login(username: str) -> None:
    db.session.add(
        UserLoginLog(
            username=username[:80],
            success=False,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
    )
    db.session.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_consume_next_url())

    next_url = _next_url()

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "1"

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            login_user(user, remember=remember)
            _clear_failed_attempts(username)
            try:
                _record_successful_login(user)
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to record DSTT login log")
            return redirect(_consume_next_url())

        if username:
            _register_failed_attempt(username)
            try:
                _record_failed_login(username)
            except Exception:
                db.session.rollback()

        flash("ユーザー名またはパスワードが正しくありません", "error")
        next_url = _next_url()

    return render_template("login.html", next_url=next_url)


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
        # ログイン時と同じ正規化（strip）を行い、前後空白付きで登録されて
        # ログインできなくなる事故を防ぐ。
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if not username or not password or password != confirm:
            flash("入力内容に誤りがあります", "error")
        elif User.query.filter_by(username=username).first():
            flash("このユーザー名は既に使われています", "error")
        elif error := password_policy_error(password, username=username):
            flash(error, "error")
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
