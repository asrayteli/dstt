import sys
from email.message import EmailMessage
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import InboundMail, db
from app.services import mail_inbox
from app.tools import user_management as um


@pytest.fixture()
def app(tmp_path):
    application = Flask(__name__, instance_path=str(tmp_path / "instance"))
    application.config["TESTING"] = True
    application.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'inbox.db').as_posix()}"
    application.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(application)
    with application.app_context():
        db.create_all()
        yield application


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
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'inbox_api.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(um.user_management_bp)
    monkeypatch.setattr(um, "is_admin_user", lambda *a, **k: True)
    with app.app_context():
        db.create_all()
    return app, app.test_client()


def _build_raw(subject="テスト件名", with_attachment=True):
    msg = EmailMessage()
    msg["From"] = "山田太郎 <taro@example.com>"
    msg["To"] = "dstt@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@example.com>"
    msg["Date"] = "Mon, 01 Jun 2026 09:00:00 +0900"
    msg.set_content("これはテキスト本文です。")
    msg.add_alternative("<p>これは<b>HTML</b>本文です。</p>", subtype="html")
    if with_attachment:
        msg.add_attachment(b"PDFDATA", maintype="application", subtype="pdf", filename="doc.pdf")
    return msg.as_bytes()


def test_parse_message_extracts_fields():
    record = mail_inbox._parse_message(_build_raw(), uid="42", mailbox="INBOX")
    assert record.from_address == "taro@example.com"
    assert record.from_name == "山田太郎"
    assert record.subject == "テスト件名"
    assert "テキスト本文" in record.body_text
    assert "HTML" in record.body_html
    assert record.has_attachments is True
    atts = record.attachment_list()
    assert atts[0]["name"] == "doc.pdf"
    assert atts[0]["content_type"] == "application/pdf"
    assert record.sent_at is not None
    assert record.imap_uid == "42"


def test_is_inbox_configured(app):
    with app.app_context():
        assert mail_inbox.is_inbox_configured() is False
        app.config["IMAP_HOST"] = "imap.example.com"
        app.config["IMAP_USER"] = "u"
        app.config["IMAP_PASSWORD"] = "p"
        assert mail_inbox.is_inbox_configured() is True


def test_prune_keeps_latest(app, monkeypatch):
    monkeypatch.setattr(mail_inbox, "_retention", lambda: 2)
    with app.app_context():
        from app.services.local_time import local_now
        from datetime import timedelta
        base = local_now()
        for i in range(5):
            db.session.add(InboundMail(
                mailbox="INBOX", imap_uid=str(i), subject=f"m{i}",
                received_at=base + timedelta(minutes=i),
            ))
        db.session.commit()
        pruned = mail_inbox._prune("INBOX")
        assert pruned == 3
        remaining = [r.subject for r in InboundMail.query.order_by(InboundMail.received_at).all()]
        assert remaining == ["m3", "m4"]


# --- 管理者 API ---

def _seed(app, **kwargs):
    with app.app_context():
        row = InboundMail(mailbox="INBOX", imap_uid=kwargs.pop("uid", "1"), **kwargs)
        db.session.add(row)
        db.session.commit()
        return row.id


def test_inbox_list_and_detail_marks_read(client):
    app, http = client
    mid = _seed(app, uid="7", subject="件名A", from_address="a@example.com",
                body_text="本文A", is_read=False)
    listing = http.get("/tools/user_management/api/mail/inbox").get_json()
    assert len(listing["messages"]) == 1
    assert "body_text" not in listing["messages"][0]  # 一覧に本文は含めない

    detail = http.get(f"/tools/user_management/api/mail/inbox/{mid}").get_json()
    assert detail["message"]["body_text"] == "本文A"
    with app.app_context():
        assert db.session.get(InboundMail, mid).is_read is True  # 開封で既読化


def test_inbox_unread_filter_and_delete(client):
    app, http = client
    _seed(app, uid="1", subject="未読", is_read=False)
    _seed(app, uid="2", subject="既読", is_read=True)
    unread = http.get("/tools/user_management/api/mail/inbox?unread=1").get_json()
    assert [m["subject"] for m in unread["messages"]] == ["未読"]

    mid = _seed(app, uid="3", subject="消す")
    assert http.delete(f"/tools/user_management/api/mail/inbox/{mid}").status_code == 200
    with app.app_context():
        assert db.session.get(InboundMail, mid) is None


def test_inbox_poll_requires_config(client):
    app, http = client
    res = http.post("/tools/user_management/api/mail/inbox/poll", json={})
    assert res.status_code == 400
    assert "IMAP" in res.get_json()["error"]


def test_inbox_status_reports_counts(client):
    app, http = client
    _seed(app, uid="1", is_read=False)
    _seed(app, uid="2", is_read=True)
    s = http.get("/tools/user_management/api/mail/inbox/status").get_json()
    assert s["configured"] is False
    assert s["unread"] == 1
    assert s["total"] == 2


def test_inbox_endpoints_require_admin(client, monkeypatch):
    app, http = client
    monkeypatch.setattr(um, "is_admin_user", lambda *a, **k: False)
    assert http.get("/tools/user_management/api/mail/inbox").status_code == 403
    assert http.get("/tools/user_management/api/mail/inbox/status").status_code == 403
    assert http.post("/tools/user_management/api/mail/inbox/poll", json={}).status_code == 403
