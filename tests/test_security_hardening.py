from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_optional_deps():
    if "openpyxl" in sys.modules:
        return

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

    db_path = tmp_path / "security.db"
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


def test_create_app_generates_local_secret_key(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DSTT_SECRET_KEY", raising=False)
    monkeypatch.delenv("DSTT_DATABASE_URI", raising=False)

    app = create_app({"TESTING": True})

    assert app.config["SECRET_KEY"]
    assert (Path(app.instance_path) / "secret_key").exists()


def test_legacy_username_is_not_implicitly_admin(app_ctx):
    from app.access_control import is_admin_user
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username="3243012", password_hash="hash", name="Legacy", is_admin=False)
        db.session.add(user)
        db.session.commit()

        assert is_admin_user(user) is False


def test_legacy_admin_requires_explicit_migration_opt_in(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.setenv("DSTT_ENABLE_LEGACY_ADMIN", "1")
    from app import create_app
    from app.access_control import is_admin_user
    from app.models import User, db

    app = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'legacy-admin.db'}",
        "SECRET_KEY": "test-secret",
        "TESTING": True,
    })
    with app.app_context():
        user = User(username="3243012", password_hash="hash", is_admin=False)
        db.session.add(user)
        db.session.commit()
        assert is_admin_user(user) is True


def test_legacy_admin_flag_migration_is_disabled_without_opt_in(app_ctx):
    from app.access_control import ensure_legacy_admin_flag
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username="3243012", password_hash="hash", name="Legacy", is_admin=False)
        db.session.add(user)
        db.session.commit()

        ensure_legacy_admin_flag()
        db.session.refresh(user)
        assert user.is_admin is False

        app_ctx.config["ENABLE_LEGACY_ADMIN"] = True
        ensure_legacy_admin_flag()
        db.session.refresh(user)
        assert user.is_admin is True


def test_register_route_disabled_by_default(app_ctx):
    client = app_ctx.test_client()

    response = client.get("/auth/register")

    assert response.status_code == 404


def test_register_route_can_be_reenabled(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app
    from app.models import db

    db_path = tmp_path / "registration.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "SECRET_KEY": "test-secret",
            "TESTING": True,
            "ALLOW_SELF_REGISTRATION": True,
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

    client = app.test_client()
    response = client.get("/auth/register")

    assert response.status_code == 200


def test_non_testing_defaults_harden_session_cookies(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app

    db_path = tmp_path / "cookie.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "SECRET_KEY": "test-secret",
        }
    )

    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
    assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_DURATION"].days == 14
    assert app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 12 * 60 * 60


def test_password_policy_only_requires_four_characters():
    from app.security.password_policy import password_policy_error

    assert password_policy_error("abc")
    assert password_policy_error("abcd") is None
    assert password_policy_error("1234") is None
    assert password_policy_error("user", username="user") is None
    assert password_policy_error("x" * 1000) is None


def test_sqlite_engine_has_wal_and_busy_timeout(tmp_path, monkeypatch):
    """ファイルベース SQLite では WAL と busy_timeout が全接続に適用される。"""
    _stub_optional_deps()
    from app import create_app
    from app.models import db
    from sqlalchemy import text

    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'pragmas.db'}",
            "SECRET_KEY": "test-secret",
            "TESTING": True,
        }
    )

    with app.app_context():
        with db.engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 30000


def test_sqlite_database_and_sidecars_are_owner_only(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app
    from app.models import db

    db_path = tmp_path / "private.db"
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test-secret",
        "TESTING": True,
    })
    with app.app_context():
        db.session.execute(db.text("SELECT 1"))
        db.session.commit()

    if os.name == "nt":
        # Windows の os.chmod は POSIX の所有者専用ACLを表現しない。実データは
        # サービスアカウント専用ディレクトリのNTFS ACLで保護する。
        assert db_path.exists()
        return

    assert db_path.stat().st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            assert sidecar.stat().st_mode & 0o777 == 0o600


def test_sqlite_database_cannot_be_placed_under_static(tmp_path):
    _stub_optional_deps()
    from app import create_app

    static_db = Path(__file__).resolve().parents[1] / "app" / "static" / "unsafe.db"
    try:
        with pytest.raises(RuntimeError, match="static"):
            create_app({
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{static_db}",
                "SECRET_KEY": "test-secret",
                "TESTING": True,
            })
        assert not static_db.exists()
    finally:
        for candidate in (static_db, Path(f"{static_db}-wal"), Path(f"{static_db}-shm")):
            candidate.unlink(missing_ok=True)


def test_hsts_header_only_on_https_requests(app_ctx):
    """HTTPS（X-Forwarded-Proto 含む）の応答にのみ HSTS を付与する。"""
    client = app_ctx.test_client()

    plain = client.get("/auth/login")
    assert "Strict-Transport-Security" not in plain.headers

    secure = client.get("/auth/login", base_url="https://localhost")
    assert secure.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_forwarded_headers_are_not_trusted_by_default(app_ctx):
    response = app_ctx.test_client().get(
        "/auth/login",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.example",
        },
    )
    assert "Strict-Transport-Security" not in response.headers


def test_forwarded_proto_can_be_trusted_explicitly(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.setenv("DSTT_PROXY_X_PROTO", "1")
    from app import create_app

    app = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'proxy.db'}",
        "SECRET_KEY": "test-secret",
        "TESTING": True,
    })
    response = app.test_client().get(
        "/auth/login",
        headers={"X-Forwarded-Proto": "https"},
    )

    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_authenticated_responses_are_not_cached(app_ctx):
    from app.models import User, db
    from werkzeug.security import generate_password_hash

    with app_ctx.app_context():
        db.session.add(User(username="cache-user", password_hash=generate_password_hash("password")))
        db.session.commit()
    client = app_ctx.test_client()
    client.post("/auth/login", data={"username": "cache-user", "password": "password"})
    response = client.get("/")
    assert response.headers["Cache-Control"] == "no-store, private"
    assert "geolocation=()" in response.headers["Permissions-Policy"]


def test_authenticated_static_assets_keep_normal_cache_behavior(app_ctx):
    from app.models import User, db
    from werkzeug.security import generate_password_hash

    with app_ctx.app_context():
        db.session.add(User(username="static-user", password_hash=generate_password_hash("password")))
        db.session.commit()
    client = app_ctx.test_client()
    client.post("/auth/login", data={"username": "static-user", "password": "password"})

    response = client.get("/static/site.webmanifest")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get("Cache-Control", "")


def test_compress_upload_keeps_its_documented_one_gib_limit(app_ctx):
    from flask import request
    from app.tools.compress import (
        COMPRESS_REQUEST_LIMIT,
        _allow_documented_compress_upload_size,
    )

    assert app_ctx.config["MAX_CONTENT_LENGTH"] == 256 * 1024 * 1024
    with app_ctx.test_request_context("/tools/compress/", method="POST"):
        _allow_documented_compress_upload_size()
        assert request.max_content_length == COMPRESS_REQUEST_LIMIT
        assert request.max_content_length > 1024 * 1024 * 1024


def test_max_content_length_default(app_ctx):
    """リクエストボディ上限が既定で設定される（無制限にしない）。"""
    expected = 256 * 1024 * 1024
    assert app_ctx.config.get("MAX_CONTENT_LENGTH") == expected
