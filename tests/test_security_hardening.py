from __future__ import annotations

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


def test_legacy_username_is_always_admin(app_ctx):
    from app.access_control import is_admin_user
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username="3243012", password_hash="hash", name="Legacy", is_admin=False)
        db.session.add(user)
        db.session.commit()

        assert is_admin_user(user) is True


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
