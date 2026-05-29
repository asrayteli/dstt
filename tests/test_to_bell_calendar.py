from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "to_bell_cal.db"
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


def _create_user(app_ctx, username: str):
    from app.models import User, db

    with app_ctx.app_context():
        db.session.add(User(username=username, password_hash="hash", name=username))
        db.session.commit()


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _enable_integration(app_ctx, username: str):
    from app.services.to_bell_integrations import update_integrations

    with app_ctx.app_context():
        update_integrations(username, {"integrations": {"google.calendar": True}})


def _connect_account(app_ctx, username: str, *, expired: bool = False):
    from app.models import ToBellGoogleAccount, db

    with app_ctx.app_context():
        expiry = datetime.utcnow() + (timedelta(minutes=-5) if expired else timedelta(hours=1))
        db.session.add(
            ToBellGoogleAccount(
                username=username,
                google_email="alice@example.com",
                refresh_token="refresh-xyz",
                access_token="access-abc",
                token_expiry=expiry,
                scope="calendar.events",
            )
        )
        db.session.commit()


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeCalendar:
    """Calendar REST API のフェイク。requests.request を差し替えて使う。"""

    def __init__(self):
        self.calls = []
        self._counter = 0

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        if method == "POST":
            self._counter += 1
            return FakeResponse(200, {"id": f"evt-{self._counter}"})
        if method == "PATCH":
            return FakeResponse(200, {"id": "evt-1"})
        if method == "DELETE":
            return FakeResponse(204)
        return FakeResponse(200, {})


def _make_task(app_ctx, username: str, *, due_at):
    from app.services.to_bell_service import create_task

    with app_ctx.app_context():
        task = create_task(username, {"title": "請求確認", "due_at": due_at})
        return task.id


def test_status_reflects_configuration(app_ctx, monkeypatch):
    from app.services import google_oauth

    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    monkeypatch.setattr(google_oauth, "is_configured", lambda: False)
    status = client.get("/tools/to_bell/api/google/status").get_json()
    assert status["configured"] is False
    assert status["connected"] is False
    # 既定リマインダーが返る
    assert status["default_reminders"] == [{"method": "popup", "minutes": 1440}]


def test_enable_requires_integration_enabled(app_ctx):
    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 400
    assert "無効" in resp.get_json()["error"]


def test_enable_requires_connection(app_ctx):
    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 400
    assert "未接続" in resp.get_json()["error"]


def test_enable_requires_due_at(app_ctx):
    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="")

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 400
    assert "期限" in resp.get_json()["error"]


def test_enable_creates_all_day_event(app_ctx, monkeypatch):
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")  # 日付のみ

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["gcal_synced"] is True
    assert body["task"]["gcal_synced"] is True

    post_call = [c for c in fake.calls if c["method"] == "POST"][0]
    assert post_call["url"].endswith("/calendars/primary/events")
    assert "date" in post_call["json"]["start"]  # 終日イベント
    assert "dateTime" not in post_call["json"]["start"]
    assert post_call["json"]["reminders"]["overrides"] == [{"method": "popup", "minutes": 1440}]


def test_enable_creates_timed_event(app_ctx, monkeypatch):
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01T15:00")

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 200
    post_call = [c for c in fake.calls if c["method"] == "POST"][0]
    assert "dateTime" in post_call["json"]["start"]
    assert post_call["json"]["start"]["timeZone"] == "Asia/Tokyo"


def test_complete_marks_event_done(app_ctx, monkeypatch):
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")
    client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})

    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/complete")
    assert resp.status_code == 200

    patch_calls = [c for c in fake.calls if c["method"] == "PATCH"]
    assert patch_calls, "完了時に PATCH が呼ばれること"
    last = patch_calls[-1]
    assert last["json"]["summary"].startswith("✓ ")
    assert last["json"]["colorId"] == to_bell_calendar.COMPLETED_COLOR_ID


def test_disable_deletes_event(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")
    client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})

    resp = client.delete(f"/tools/to_bell/api/tasks/{task_id}/calendar")
    assert resp.status_code == 200
    assert resp.get_json()["gcal_synced"] is False
    assert any(c["method"] == "DELETE" for c in fake.calls)

    with app_ctx.app_context():
        task = ToBellTask.query.get(task_id)
        assert task.gcal_event_id is None
        assert task.gcal_synced_by is None


def test_per_task_reminder_override(app_ctx, monkeypatch):
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")

    resp = client.post(
        f"/tools/to_bell/api/tasks/{task_id}/calendar",
        json={"reminders": [{"method": "email", "minutes": 60}]},
    )
    assert resp.status_code == 200
    post_call = [c for c in fake.calls if c["method"] == "POST"][0]
    assert post_call["json"]["reminders"]["overrides"] == [{"method": "email", "minutes": 60}]


def test_reminder_override_can_be_reset_to_default(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")

    client.post(
        f"/tools/to_bell/api/tasks/{task_id}/calendar",
        json={"reminders": [{"method": "popup", "minutes": 30}]},
    )
    with app_ctx.app_context():
        assert ToBellTask.query.get(task_id).gcal_reminder_override == [{"method": "popup", "minutes": 30}]

    resp = client.post(
        f"/tools/to_bell/api/tasks/{task_id}/calendar",
        json={"reminders": "default"},
    )
    assert resp.status_code == 200
    with app_ctx.app_context():
        assert ToBellTask.query.get(task_id).gcal_reminder_override is None


def test_default_reminders_can_be_set(app_ctx):
    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    resp = client.put(
        "/tools/to_bell/api/google/reminders",
        json={"reminders": [{"method": "popup", "minutes": 30}, {"method": "email", "minutes": 120}]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["default_reminders"] == [
        {"method": "popup", "minutes": 30},
        {"method": "email", "minutes": 120},
    ]


def test_token_refresh_on_expiry(app_ctx, monkeypatch):
    from app.models import ToBellGoogleAccount
    from app.services import google_oauth, to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice", expired=True)

    refreshed = {"count": 0}

    def fake_refresh(refresh_token):
        refreshed["count"] += 1
        return {"access_token": "fresh-token", "expires_in": 3600}

    monkeypatch.setattr(google_oauth, "refresh_access_token", fake_refresh)
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")
    resp = client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})
    assert resp.status_code == 200
    assert refreshed["count"] == 1
    with app_ctx.app_context():
        account = ToBellGoogleAccount.query.filter_by(username="alice").first()
        assert account.access_token == "fresh-token"


def test_disconnect_revokes_and_clears(app_ctx, monkeypatch):
    from app.models import ToBellGoogleAccount, ToBellTask
    from app.services import google_oauth, to_bell_calendar

    _create_user(app_ctx, "alice")
    _enable_integration(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    fake = FakeCalendar()
    monkeypatch.setattr(to_bell_calendar.requests, "request", fake.request)
    revoked = {"count": 0}
    monkeypatch.setattr(google_oauth, "revoke", lambda token: revoked.__setitem__("count", revoked["count"] + 1) or True)

    client = app_ctx.test_client()
    _login(client, "alice")
    task_id = _make_task(app_ctx, "alice", due_at="2026-06-01")
    client.post(f"/tools/to_bell/api/tasks/{task_id}/calendar", json={})

    resp = client.post("/tools/to_bell/api/google/disconnect")
    assert resp.status_code == 200
    assert resp.get_json()["connected"] is False
    assert revoked["count"] == 1
    with app_ctx.app_context():
        assert ToBellGoogleAccount.query.filter_by(username="alice").first() is None
        task = ToBellTask.query.get(task_id)
        assert task.gcal_event_id is None


def test_oauth_callback_stores_connection(app_ctx, monkeypatch):
    from app.models import ToBellGoogleAccount
    from app.services import google_oauth

    _create_user(app_ctx, "alice")
    monkeypatch.setattr(google_oauth, "is_configured", lambda: True)
    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, redirect_uri: {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "calendar.events"},
    )
    monkeypatch.setattr(google_oauth, "fetch_userinfo", lambda token: {"email": "alice@example.com"})

    client = app_ctx.test_client()
    _login(client, "alice")

    with client.session_transaction() as sess:
        sess["tb_gcal_oauth_state"] = "state123"

    resp = client.get("/tools/to_bell/api/google/callback?state=state123&code=authcode", follow_redirects=False)
    assert resp.status_code == 302
    assert "google=connected" in resp.headers["Location"]
    with app_ctx.app_context():
        account = ToBellGoogleAccount.query.filter_by(username="alice").first()
        assert account is not None
        assert account.google_email == "alice@example.com"
        assert account.refresh_token == "r"


def test_oauth_callback_rejects_state_mismatch(app_ctx, monkeypatch):
    from app.services import google_oauth

    _create_user(app_ctx, "alice")
    monkeypatch.setattr(google_oauth, "is_configured", lambda: True)
    client = app_ctx.test_client()
    _login(client, "alice")
    with client.session_transaction() as sess:
        sess["tb_gcal_oauth_state"] = "expected"

    resp = client.get("/tools/to_bell/api/google/callback?state=wrong&code=x", follow_redirects=False)
    assert resp.status_code == 302
    assert "state_mismatch" in resp.headers["Location"]
