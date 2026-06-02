"""ViewURL / EditURL / ViewPWA URL の未ログインアクセスを検証する統合テスト。

CloudShift の共有URLは DSTT アカウントを持たない人向けの機能であり、
リンクを知っていることが認可になる。create_app() の before_request で
未ログインをログイン画面へリダイレクトしてしまう不具合が再発しないよう、
ここでは実 app を立てて確認する。
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "cloudshift.db"
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


def _create_owner(app_ctx, username: str = "owner"):
    from app.models import User, db
    from app.access_control import grant_tool_access

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name="Owner")
        db.session.add(user)
        db.session.commit()
        grant_tool_access(user.id, ["shiftersync"], "system")


def _login(client, username: str = "owner"):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _token_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _create_project_urls(app_ctx) -> dict[str, str]:
    _create_owner(app_ctx)
    client = app_ctx.test_client()
    _login(client)
    resp = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "共有テスト", "mode": "scene", "year": "2026", "month": "5"},
    )
    assert resp.status_code == 200
    return resp.get_json()["project"]["project"]["urls"]


def test_public_view_url_accessible_without_login(app_ctx):
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["view_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/view/{token}")
    assert resp.status_code == 200
    # ログイン画面へリダイレクトされていないこと
    assert resp.location is None or "/auth/login" not in (resp.location or "")


def test_public_edit_url_accessible_without_login(app_ctx):
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["edit_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/edit/{token}")
    assert resp.status_code == 200
    assert resp.location is None or "/auth/login" not in (resp.location or "")


def test_public_pwa_url_accessible_without_login(app_ctx):
    """PR205で対応した本題: ViewPWA URLが未ログインで開けること。"""
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["pwa_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/pwa/{token}")
    assert resp.status_code == 200
    assert resp.location is None or "/auth/login" not in (resp.location or "")


def test_public_pwa_manifest_accessible_without_login(app_ctx):
    """ViewPWA インストール時に取得する manifest.webmanifest も未ログイン可。"""
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["pwa_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/pwa/{token}/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type", "").startswith("application/manifest+json")


def test_public_pwa_push_subscribe_endpoint_accessible_without_login(app_ctx):
    """通知購読API（/api/pwa/<token>/push/*）も未ログイン可。

    Web Push を送るブラウザ自身は DSTT のセッションを持たないため、
    リンク（=PWAトークン）の保持を認可とする。
    """
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["pwa_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/api/pwa/{token}/push/public-key")
    # push ライブラリが入っていない環境では 503 を返すが、いずれも
    # ログインリダイレクト(302→/auth/login) では無いことを担保する。
    assert resp.status_code in (200, 503)


def test_public_api_view_accessible_without_login(app_ctx):
    """既存の /api/public/<token_type>/<token> 経由のJSON取得も未ログインで開く。"""
    urls = _create_project_urls(app_ctx)
    token = _token_from_url(urls["view_url"])
    anon = app_ctx.test_client()
    resp = anon.get(f"/tools/shiftersync/cloudshift/api/public/view/{token}")
    assert resp.status_code == 200
