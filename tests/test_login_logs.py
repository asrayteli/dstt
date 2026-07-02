from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_optional_deps():
    if "openpyxl" not in sys.modules:
        openpyxl = types.ModuleType("openpyxl")
        openpyxl.Workbook = object
        openpyxl.load_workbook = lambda *_args, **_kwargs: object()

        styles = types.ModuleType("openpyxl.styles")
        styles.Font = object
        styles.PatternFill = object
        styles.Border = object
        styles.Side = object
        styles.Alignment = object

        sys.modules["openpyxl"] = openpyxl
        sys.modules["openpyxl.styles"] = styles

    if "qrcode" not in sys.modules:
        qrcode = types.ModuleType("qrcode")
        qrcode.make = lambda *_args, **_kwargs: types.SimpleNamespace(save=lambda *_a, **_k: None)
        sys.modules["qrcode"] = qrcode

    if "pytesseract" not in sys.modules:
        pytesseract = types.ModuleType("pytesseract")
        pytesseract.image_to_string = lambda *_args, **_kwargs: ""
        sys.modules["pytesseract"] = pytesseract


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app
    from app.models import db

    db_path = tmp_path / "login_logs.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "SECRET_KEY": "test-secret",
            "TESTING": True,
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _create_user(username: str, *, password: str = "secret", is_admin: bool = False, name: str | None = None):
    from app.models import User, db

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        name=name or username.title(),
        is_admin=is_admin,
    )
    db.session.add(user)
    db.session.commit()
    return user


def test_successful_login_records_dstt_login_log(app_ctx):
    from app.models import DsttLoginLog

    with app_ctx.app_context():
        _create_user("alice", name="Alice")

    before_jst = datetime.utcnow() + timedelta(hours=9)
    client = app_ctx.test_client()
    response = client.post(
        "/auth/login",
        data={"username": "alice", "password": "secret"},
        headers={"X-Forwarded-For": "spoofed-by-client, 203.0.113.10", "User-Agent": "pytest-browser"},
    )
    after_jst = datetime.utcnow() + timedelta(hours=9)

    assert response.status_code == 302
    with app_ctx.app_context():
        logs = DsttLoginLog.query.all()
        assert len(logs) == 1
        assert logs[0].username == "alice"
        assert logs[0].name == "Alice"
        assert logs[0].ip_address == "203.0.113.10"
        assert logs[0].user_agent == "pytest-browser"
        assert before_jst <= logs[0].logged_in_at <= after_jst


def test_failed_login_shows_message_and_records_forwarded_ip(app_ctx):
    from app.models import UserLoginLog

    with app_ctx.app_context():
        _create_user("alice", name="Alice")

    client = app_ctx.test_client()
    response = client.post(
        "/auth/login",
        data={"username": "alice", "password": "wrong"},
        headers={"X-Forwarded-For": "spoofed-by-client, 198.51.100.7", "User-Agent": "pytest-browser"},
    )

    assert response.status_code == 200
    assert "ユーザー名またはパスワードが正しくありません".encode("utf-8") in response.data
    with app_ctx.app_context():
        log = UserLoginLog.query.filter_by(username="alice", success=False).one()
        assert log.ip_address == "198.51.100.7"
        assert log.user_agent == "pytest-browser"


def test_login_redirects_to_safe_next_url(app_ctx):
    with app_ctx.app_context():
        _create_user("alice", name="Alice")

    client = app_ctx.test_client()
    response = client.post(
        "/auth/login?next=/tools/calc",
        data={"username": "alice", "password": "secret"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/tools/calc"


def test_login_rejects_external_next_url(app_ctx):
    with app_ctx.app_context():
        _create_user("alice", name="Alice")

    client = app_ctx.test_client()
    response = client.post(
        "/auth/login?next=https://evil.example.com/",
        data={"username": "alice", "password": "secret"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_login_can_issue_remember_cookie(app_ctx):
    with app_ctx.app_context():
        _create_user("alice", name="Alice")

    client = app_ctx.test_client()
    response = client.post(
        "/auth/login",
        data={"username": "alice", "password": "secret", "remember": "1"},
    )

    assert response.status_code == 302
    assert any(cookie.startswith("remember_token=") for cookie in response.headers.getlist("Set-Cookie"))


def test_login_required_redirect_does_not_show_information_message(app_ctx):
    client = app_ctx.test_client()

    response = client.get("/", follow_redirects=True)

    assert response.status_code == 200
    assert "ログインしてください。".encode("utf-8") not in response.data
    assert b"Please log in to access this page." not in response.data


def test_login_page_uses_single_visible_logo_image(app_ctx):
    client = app_ctx.test_client()

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert response.data.count(b"<img") == 1
    assert b"Please log in to access this page." not in response.data


def test_admin_login_logs_api_returns_recent_first(app_ctx):
    from app.models import DsttLoginLog, db

    with app_ctx.app_context():
        _create_user("admin", password="adminpass", is_admin=True, name="Admin")
        db.session.add_all(
            [
                DsttLoginLog(
                    username="alice",
                    name="Alice",
                    logged_in_at=datetime.utcnow() - timedelta(hours=1),
                ),
                DsttLoginLog(
                    username="bob",
                    name="Bob",
                    logged_in_at=datetime.utcnow(),
                ),
            ]
        )
        db.session.commit()

    client = app_ctx.test_client()
    client.post("/auth/login", data={"username": "admin", "password": "adminpass"})
    response = client.get("/tools/user_management/api/login-logs?limit=2")

    assert response.status_code == 200
    data = response.get_json()
    assert [row["username"] for row in data["logs"]] == ["admin", "bob"]
