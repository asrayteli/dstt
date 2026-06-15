from pathlib import Path
import sys

from flask import Flask
from flask_login import LoginManager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.navigation import NAV_ITEMS
from app.tools.bus_pricing import bus_pricing_bp


def _build_client():
    app = Flask(
        __name__,
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return None

    @app.context_processor
    def inject_navigation():
        return {
            "app_navigation_items": NAV_ITEMS,
            "current_user_id": "tester",
            "app_version": "test",
            "default_page_title": lambda: "DSTT - test",
        }

    app.register_blueprint(bus_pricing_bp)
    return app.test_client()


def test_bus_pricing_page_notes_unimplemented_compliance_checks():
    """The screen must disclose that C-002/C-007/C-008 are not auto-evaluated.

    Required by docs/bus_pricing_design.md §6: these checks are left as future
    extensions and the page must make that explicit so users do not assume the
    tool verified them.
    """
    client = _build_client()

    response = client.get("/tools/bus_pricing/")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "自動判定の対象外" in body
    assert "C-002" in body
    assert "C-007" in body
    assert "C-008" in body
