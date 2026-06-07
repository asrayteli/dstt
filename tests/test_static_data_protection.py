"""static_folder 配下の実行時データ（個人情報・権限設定）が /static/ 経由で
無認証配信されないことを検証する。正規の静的アセット(css/js)は配信され続ける。"""

import os

import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
    })
    return app, app.test_client()


def _write(path, content="{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_sensitive_static_data_is_blocked(client):
    app, c = client
    static = app.static_folder

    # 実在しても 404 になること（配信されない）を保証するため実ファイルを置く。
    created = [
        os.path.join(static, "leave_mgr", "calendars", "cal_2025-01.json"),
        os.path.join(static, "leave_mgr", "permissions.json"),
        os.path.join(static, "health_check", "permissions.json"),
        os.path.join(static, "health_check", "settings.json"),
        os.path.join(static, "health_check", "uploads", "2025", "secret.pdf"),
        os.path.join(static, "pluslist", "uploads", "in.csv"),
        os.path.join(static, "subject_analysis_tool", "uploads", "in.csv"),
        os.path.join(static, "monthly_generator", "uploads", "in.csv"),
    ]
    for p in created:
        _write(p, "sensitive")

    blocked_urls = [
        "/static/leave_mgr/calendars/cal_2025-01.json",
        "/static/leave_mgr/permissions.json",
        "/static/health_check/permissions.json",
        "/static/health_check/settings.json",
        "/static/health_check/uploads/2025/secret.pdf",
        "/static/pluslist/uploads/in.csv",
        "/static/subject_analysis_tool/uploads/in.csv",
        "/static/monthly_generator/uploads/in.csv",
    ]
    try:
        for url in blocked_urls:
            assert c.get(url).status_code == 404, url
    finally:
        for p in created:
            if os.path.exists(p):
                os.remove(p)


def test_legitimate_static_assets_still_served(client):
    app, c = client
    # リポジトリに存在する正規アセットは引き続き 200 で配信される。
    for url in (
        "/static/leave_mgr/js/leave_mgr.js",
        "/static/subject_analysis_tool/js/main.js",
        "/static/css/style.css",
    ):
        assert c.get(url).status_code == 200, url
