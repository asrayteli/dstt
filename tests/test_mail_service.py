import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import MailMessage, db
from app.services import mail_service


@pytest.fixture()
def app(tmp_path):
    application = Flask(__name__, instance_path=str(tmp_path / "instance"))
    application.config["TESTING"] = True
    application.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'mail.db').as_posix()}"
    application.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(application)
    with application.app_context():
        db.create_all()
        yield application


def test_queue_mail_dedupe_returns_existing_row(app):
    with app.app_context():
        first = mail_service.queue_mail("a@example.com", "件名", "本文", dedupe_key="k1")
        second = mail_service.queue_mail("a@example.com", "件名2", "本文2", dedupe_key="k1")
        assert first is not None and second is not None
        assert first.id == second.id
        assert MailMessage.query.count() == 1


def test_dispatch_holds_when_smtp_unconfigured(app):
    """SMTP 未設定時は失わず queued のまま保留する（"安全な保留"）。"""
    with app.app_context():
        mail_service.queue_mail("a@example.com", "件名", "本文")
        summary = mail_service.dispatch_pending()
        assert summary["sent"] == 0
        assert summary["held"] == 1
        row = MailMessage.query.first()
        # skipped で失わず、設定後に送信できるよう queued のまま据え置く。
        assert row.status == mail_service.STATUS_QUEUED
        assert row.attempts == 0


def test_held_mail_sends_after_smtp_configured(app, monkeypatch):
    """未設定時に保留したメールは、設定が入れば次の tick で送信される。"""
    with app.app_context():
        mail_service.queue_mail("a@example.com", "件名", "本文")
        assert mail_service.dispatch_pending()["held"] == 1
        # ここで SMTP 設定が後追いで投入されたとみなす。
        app.config["MAIL_HOST"] = "smtp.example.com"
        app.config["MAIL_FROM"] = "noreply@example.com"
        monkeypatch.setattr(mail_service, "_send_smtp", lambda message, settings: None)
        summary = mail_service.dispatch_pending()
        assert summary["sent"] == 1
        assert MailMessage.query.first().status == mail_service.STATUS_SENT


def test_requeue_message_resets_and_sends(app, monkeypatch):
    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"
    with app.app_context():
        msg = mail_service.queue_mail("a@example.com", "件名", "本文")
        msg.status = mail_service.STATUS_FAILED
        msg.attempts = 5
        msg.last_error = "boom"
        db.session.commit()
        monkeypatch.setattr(mail_service, "_send_smtp", lambda message, settings: None)
        mail_service.requeue_message(msg, send_now=True)
        row = MailMessage.query.first()
        assert row.status == mail_service.STATUS_SENT
        assert row.attempts == 1
        assert row.last_error is None


def test_dispatch_sends_when_configured(app, monkeypatch):
    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"
    sent = {}

    def fake_send(message, settings):
        sent["to"] = message.to_address
        sent["from"] = settings["from_address"]

    monkeypatch.setattr(mail_service, "_send_smtp", fake_send)
    with app.app_context():
        assert mail_service.is_mail_configured() is True
        mail_service.queue_mail("driver@example.com", "期限のお知らせ", "本文")
        summary = mail_service.dispatch_pending()
        assert summary["sent"] == 1
        row = MailMessage.query.first()
        assert row.status == mail_service.STATUS_SENT
        assert row.sent_at is not None
        assert sent == {"to": "driver@example.com", "from": "noreply@example.com"}


def test_failed_send_retries_then_marks_failed(app, monkeypatch):
    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"

    def boom(message, settings):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mail_service, "_send_smtp", boom)
    with app.app_context():
        mail_service.queue_mail("driver@example.com", "件名", "本文", max_attempts=2)
        mail_service.dispatch_pending()
        row = MailMessage.query.first()
        assert row.status == mail_service.STATUS_QUEUED  # 1回目失敗 → 再試行待ち
        assert row.attempts == 1
        mail_service.dispatch_pending()
        row = MailMessage.query.first()
        assert row.status == mail_service.STATUS_FAILED  # 2回目で上限到達
        assert row.attempts == 2
        assert "connection refused" in (row.last_error or "")


def test_queue_mail_skips_empty_recipient(app):
    with app.app_context():
        assert mail_service.queue_mail("", "件名", "本文") is None
        assert MailMessage.query.count() == 0
