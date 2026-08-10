from __future__ import annotations

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    monkeypatch.chdir(tmp_path)
    application = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'classification.db'}",
            "SECRET_KEY": "test-secret",
            "TESTING": True,
        }
    )
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application


def test_about_page_is_public_and_describes_business_purpose(app):
    client = app.test_client()

    response = client.get("/about")

    assert response.status_code == 200
    assert "BusinessApplication" in response.get_data(as_text=True)
    assert "社内向けWebアプリケーション" in response.get_data(as_text=True)


def test_robots_exposes_only_public_classification_pages(app):
    response = app.test_client().get("/robots.txt")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == (
        "User-agent: *\n"
        "Allow: /about\n"
        "Allow: /auth/login\n"
        "Disallow: /\n"
    )
