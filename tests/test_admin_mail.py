import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import MailMessage, db
from app.services import mail_service
from app.tools import user_management as um


@pytest.fixture()
def client(tmp_path, monkeypatch):
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'admin_mail.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(um.user_management_bp)
    # 管理者ゲートを通す（current_user を介さずに許可）。
    monkeypatch.setattr(um, "is_admin_user", lambda *a, **k: True)
    with app.app_context():
        db.create_all()
    return app, app.test_client()


def test_parse_recipients_splits_and_validates():
    valid, invalid = um._parse_recipients("a@example.com, b@example.com\nc@example.com a@example.com")
    assert valid == ["a@example.com", "b@example.com", "c@example.com"]  # 重複除去
    assert invalid == []
    valid, invalid = um._parse_recipients("good@example.com, broken-address")
    assert valid == ["good@example.com"]
    assert invalid == ["broken-address"]


def test_send_holds_when_unconfigured(client):
    app, http = client
    res = http.post("/tools/user_management/api/mail/send", json={
        "to": "taro@example.com, hanako@example.com",
        "subject": "周知",
        "body_text": "本文です",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["queued"] == 2
    assert data["sent"] == 0
    assert data["configured"] is False
    with app.app_context():
        rows = MailMessage.query.all()
        assert len(rows) == 2
        assert {r.to_address for r in rows} == {"taro@example.com", "hanako@example.com"}
        assert all(r.status == mail_service.STATUS_QUEUED for r in rows)
        assert all(r.category == "admin_manual" for r in rows)


def test_send_now_when_configured(client, monkeypatch):
    app, http = client
    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"
    monkeypatch.setattr(mail_service, "_send_smtp", lambda message, settings: None)
    res = http.post("/tools/user_management/api/mail/send", json={
        "to": "taro@example.com",
        "subject": "件名",
        "body_text": "本文",
        "send_now": True,
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["queued"] == 1 and data["sent"] == 1
    with app.app_context():
        assert MailMessage.query.first().status == mail_service.STATUS_SENT


def test_send_rejects_invalid_recipient(client):
    app, http = client
    res = http.post("/tools/user_management/api/mail/send", json={
        "to": "not-an-email",
        "subject": "件名",
        "body_text": "本文",
    })
    assert res.status_code == 400
    assert "形式" in res.get_json()["error"]


def test_send_requires_subject_and_body(client):
    app, http = client
    res = http.post("/tools/user_management/api/mail/send", json={"to": "a@example.com", "subject": ""})
    assert res.status_code == 400


def test_resend_requeues_message(client, monkeypatch):
    app, http = client
    with app.app_context():
        msg = mail_service.queue_mail("a@example.com", "件名", "本文")
        msg.status = mail_service.STATUS_FAILED
        msg.attempts = 5
        db.session.commit()
        msg_id = msg.id
    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"
    monkeypatch.setattr(mail_service, "_send_smtp", lambda message, settings: None)
    res = http.post(f"/tools/user_management/api/mail/messages/{msg_id}/resend", json={"send_now": True})
    assert res.status_code == 200
    with app.app_context():
        assert db.session.get(MailMessage, msg_id).status == mail_service.STATUS_SENT


def test_endpoints_require_admin(client, monkeypatch):
    app, http = client
    monkeypatch.setattr(um, "is_admin_user", lambda *a, **k: False)
    assert http.get("/tools/user_management/api/mail/status").status_code == 403
    assert http.post("/tools/user_management/api/mail/send", json={"to": "a@example.com"}).status_code == 403
    assert http.get("/tools/user_management/api/mail/messages").status_code == 403
