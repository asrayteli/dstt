from pathlib import Path
import sys

from flask import Flask
from flask_login import LoginManager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.navigation import NAV_ITEMS
from app.tools.datecalc import datecalc_bp


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

    app.register_blueprint(datecalc_bp)
    return app.test_client()


def test_datecalc_page_renders_four_modes():
    client = _build_client()

    response = client.get("/tools/datecalc/")

    assert response.status_code == 200
    assert b'data-datecalc-tab="addsub"' in response.data
    assert b'data-datecalc-tab="diff"' in response.data
    assert b'data-datecalc-tab="month_shift"' in response.data
    assert b'data-datecalc-tab="count"' in response.data
    assert b'data-datecalc-tab="business"' not in response.data
    assert b"/static/datecalc/datecalc.js" in response.data


def test_datecalc_diff_post_renders_datetime_delta_result():
    client = _build_client()

    response = client.post(
        "/tools/datecalc/",
        data={
            "mode": "diff",
            "diff_start_datetime": "2026-05-01T09:00",
            "diff_end_datetime": "2026-05-03T10:30",
            "absolute_diff": "on",
        },
    )

    assert response.status_code == 200
    assert "2日 1時間 30分".encode("utf-8") in response.data
    assert b"data-save-history" in response.data


def test_datecalc_count_post_renders_selected_day_count_result():
    client = _build_client()

    response = client.post(
        "/tools/datecalc/",
        data={
            "mode": "count",
            "count_start_date": "2026-05-01",
            "count_end_date": "2026-05-07",
            "count_weekdays[]": ["0"],
            "count_month_days": "5",
            "count_exact_dates": "2026-05-01",
            "count_japan_holidays": "on",
        },
    )

    assert response.status_code == 200
    assert "4日".encode("utf-8") in response.data
    assert "指定日カウント結果".encode("utf-8") in response.data


def test_datecalc_count_uses_today_when_one_side_is_blank():
    client = _build_client()

    response = client.post(
        "/tools/datecalc/",
        data={
            "mode": "count",
            "count_start_date": "",
            "count_end_date": "2026-05-07",
            "count_weekdays[]": ["5"],
        },
    )

    assert response.status_code == 200
    assert "指定日カウント結果".encode("utf-8") in response.data
