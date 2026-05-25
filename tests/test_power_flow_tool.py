from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "power_flow.db"
    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _create_user(app_ctx, username="alice", name="Alice"):
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name=name)
        db.session.add(user)
        db.session.commit()
        return user.username


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def test_power_flow_page_loads_launcher_and_editor(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    response = client.get("/tools/power_flow/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "power-flow-launcher" in html
    assert "PowerFlow" in html
    assert "pf-launch-new" in html
    assert "pf-launch-saved-list" in html
    assert "pf-launch-import" in html

    editor_response = client.get("/tools/power_flow/editor")
    assert editor_response.status_code == 200
    editor_html = editor_response.get_data(as_text=True)
    assert "power-flow-app" in editor_html
    assert "SVG" in editor_html
    assert "PDF" in editor_html
    assert "power_flow/power_flow.js" in editor_html


def test_power_flow_is_public_navigation_tool():
    from app import create_app
    from app.access_control import tool_category
    from app.manuals import get_manual
    from app.navigation import NAV_ITEMS

    item = next(nav for nav in NAV_ITEMS if nav["key"] == "power_flow")

    assert item["href"] == "/tools/power_flow"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        assert tool_category("power_flow") == "public"
    assert get_manual("power_flow")["tool_label"] == "Power Flow"
