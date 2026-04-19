from pathlib import Path
import sys

from flask import Flask, render_template, render_template_string

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.navigation import NAV_ITEMS
from app.manuals import get_manual


TEMPLATE_ROOT = ROOT / "app" / "templates"


def _build_app():
    app = Flask(__name__, template_folder=str(TEMPLATE_ROOT))

    @app.context_processor
    def inject_navigation():
        return {"app_navigation_items": NAV_ITEMS}

    return app


def test_navigation_contains_color_extract_before_powerstamp():
    hrefs = [item["href"] for item in NAV_ITEMS]

    assert "/tools/color_extract" in hrefs
    assert "/tools/powerstamp" in hrefs
    assert hrefs.index("/tools/color_extract") < hrefs.index("/tools/powerstamp")


def test_base_template_renders_shared_sidebar_links():
    app = _build_app()

    with app.test_request_context("/tools/color_extract"):
        html = render_template_string(
            '{% extends "base.html" %}{% block content %}<p>content</p>{% endblock %}'
        )

    assert "/tools/color_extract" in html
    assert "/tools/powerstamp" in html


def test_dashboard_uses_shared_navigation_order():
    app = _build_app()

    with app.test_request_context("/"):
        html = render_template(
            "index.html",
            user_name="Tester",
            is_admin=False,
            announcements_to_show=[],
        )

    assert html.count("/tools/color_extract") == 4
    assert html.count("/tools/powerstamp") == 4
    assert html.find("/tools/color_extract") < html.find("/tools/powerstamp")


def test_dashboard_renders_manual_links():
    app = _build_app()

    with app.test_request_context("/"):
        html = render_template(
            "index.html",
            user_name="Tester",
            is_admin=False,
            announcements_to_show=[],
        )

    assert "/manual/datecalc" in html
    assert "/manual/siteplus" in html
    assert 'target="_blank"' in html


def test_all_navigation_items_have_manuals():
    missing = [item["key"] for item in NAV_ITEMS if get_manual(item["key"]) is None]
    assert missing == []


def test_manual_template_renders_optional_sections():
    app = _build_app()

    with app.test_request_context("/manual/pdf_power"):
        html = render_template("manual.html", manual=get_manual("pdf_power"))

    assert "機能ごとの見方" in html
    assert "コントロール" in html
