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
    page = response.get_data(as_text=True)
    assert "BusinessApplication" in page
    assert "PDF編集" in page
    assert "CSV編集" in page
    assert "ページの並べ替え、結合、分割" in page
    assert "表のセル・行・列の編集" in page
    assert "ファイル処理" not in page
    assert "document processing" not in page
    assert "通信を中継" not in page


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
