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

    db_path = tmp_path / "to_bell_import.db"
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


def _enable_import(app_ctx, username: str):
    from app.services.to_bell_integrations import update_integrations

    with app_ctx.app_context():
        update_integrations(username, {"integrations": {"google.calendar_import": True}})


def _connect_account(app_ctx, username: str, *, sync_token: str | None = None, last_import_at=None):
    from app.models import ToBellGoogleAccount, db

    with app_ctx.app_context():
        db.session.add(
            ToBellGoogleAccount(
                username=username,
                google_email="alice@example.com",
                refresh_token="refresh-xyz",
                access_token="access-abc",
                token_expiry=datetime.utcnow() + timedelta(hours=1),
                scope="calendar.events",
                calendar_sync_token=sync_token,
                last_import_at=last_import_at,
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


class FakeGet:
    """requests.get の差し替え。pages を順番に返す。"""

    def __init__(self, pages: list[dict]):
        self.pages = list(pages)
        self.calls: list[dict] = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        page = self.pages.pop(0) if self.pages else {"items": []}
        return FakeResponse(page.get("__status", 200), page)


def _patch_get(monkeypatch, fake: FakeGet):
    from app.services import to_bell_calendar_import

    monkeypatch.setattr(to_bell_calendar_import.requests, "get", fake)


def _event(event_id, summary, *, start=None, status="confirmed", organizer_self=None, source_title=None):
    ev: dict = {"id": event_id, "summary": summary, "status": status}
    if start is not None:
        ev["start"] = start
    if organizer_self is not None:
        ev["organizer"] = {"self": organizer_self}
    if source_title is not None:
        ev["source"] = {"title": source_title}
    return ev


def _timed(dt_str):
    return {"dateTime": dt_str, "timeZone": "Asia/Tokyo"}


# ---------- フィルタモード ----------

def test_tb_suffix_mode_imports_only_marked_events(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")

    fake = FakeGet([
        {
            "items": [
                _event("e1", "請求書提出 TB", start={"date": "2026-06-01"}),
                _event("e2", "雑談", start={"date": "2026-06-02"}),
                _event("e3", "会議TB", start=_timed("2026-06-03T10:00:00+09:00")),
            ],
            "nextSyncToken": "tok-1",
        }
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 2
        titles = sorted(t.title for t in ToBellTask.query.all())
        assert titles == ["会議", "請求書提出"]  # 末尾TBは除去される


def test_organizer_mode_imports_only_self_events(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "organizer")

    fake = FakeGet([
        {
            "items": [
                _event("e1", "自分の予定", start={"date": "2026-06-01"}, organizer_self=True),
                _event("e2", "招待された会議", start={"date": "2026-06-02"}, organizer_self=False),
            ],
            "nextSyncToken": "tok-1",
        }
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 1
        assert [t.title for t in ToBellTask.query.all()] == ["自分の予定"]


def test_all_mode_imports_everything_except_tobell_origin(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {
            "items": [
                _event("e1", "予定A", start={"date": "2026-06-01"}),
                _event("e2", "予定B", start={"date": "2026-06-02"}),
                _event("e3", "ToBell由来", start={"date": "2026-06-03"}, source_title="ToBell"),
            ],
            "nextSyncToken": "tok-1",
        }
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 2  # ToBell由来は除外
        assert ToBellTask.query.count() == 2


# ---------- due_at の解釈 ----------

def test_timed_event_sets_due_at_with_local_time(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "打合せ", start=_timed("2026-06-03T10:30:00+09:00"))], "nextSyncToken": "t"}
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        task = ToBellTask.query.first()
        assert task.due_at == datetime(2026, 6, 3, 10, 30, 0)


def test_all_day_event_sets_due_at_to_end_of_day(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp
    from app.services.to_bell_service import has_explicit_notification_time

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([{"items": [_event("e1", "終日", start={"date": "2026-06-05"})], "nextSyncToken": "t"}])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        task = ToBellTask.query.first()
        assert task.due_at == datetime(2026, 6, 5, 23, 59, 59)
        # 終日は「明示時刻なし」扱い（プッシュ通知の対象外）。
        assert has_explicit_notification_time(task) is False


# ---------- 追従（更新・キャンセル） ----------

def test_event_update_follows_into_task(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "旧タイトル", start={"date": "2026-06-01"})], "nextSyncToken": "t1"},
        {"items": [_event("e1", "新タイトル", start={"date": "2026-06-09"})], "nextSyncToken": "t2"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        s2 = imp.import_for_user("alice")
        assert s2["updated"] == 1
        assert ToBellTask.query.count() == 1
        task = ToBellTask.query.first()
        assert task.title == "新タイトル"
        assert task.due_at == datetime(2026, 6, 9, 23, 59, 59)


def test_cancelled_event_completes_task(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "t1"},
        {"items": [_event("e1", "予定", status="cancelled")], "nextSyncToken": "t2"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        s2 = imp.import_for_user("alice")
        assert s2["completed"] == 1
        task = ToBellTask.query.first()
        assert task.status == "done"
        assert task.completed_at is not None


def test_archived_task_is_not_resurrected(app_ctx, monkeypatch):
    from app.models import ToBellTask, db
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "t1"},
        {"items": [_event("e1", "予定（編集後）", start={"date": "2026-06-02"})], "nextSyncToken": "t2"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        task = ToBellTask.query.first()
        task.status = "archived"  # ユーザーが削除した想定
        db.session.commit()
        s2 = imp.import_for_user("alice")
        assert s2["updated"] == 0
        assert s2["skipped"] == 1
        assert ToBellTask.query.first().status == "archived"


# ---------- 重複・syncToken・410 ----------

def test_reimport_same_event_is_idempotent(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "t1"},
        {"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "t2"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        s2 = imp.import_for_user("alice")
        assert s2["created"] == 0
        assert s2["skipped"] == 1
        assert ToBellTask.query.count() == 1


def test_sync_token_is_persisted(app_ctx, monkeypatch):
    from app.services import to_bell_calendar, to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([{"items": [], "nextSyncToken": "sync-token-123"}])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")
        account = to_bell_calendar.get_account("alice")
        assert account.calendar_sync_token == "sync-token-123"
        assert account.last_import_at is not None


def test_expired_sync_token_triggers_full_resync(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar, to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice", sync_token="stale-token")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"__status": 410},  # syncToken 失効
        {"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "fresh"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 1
        assert ToBellTask.query.count() == 1
        account = to_bell_calendar.get_account("alice")
        assert account.calendar_sync_token == "fresh"
    # 2回目の取得は syncToken 無し（フル）でリクエストされている。
    assert "syncToken" not in fake.calls[1]["params"]


# ---------- ガード ----------

def test_full_sync_pagination_keeps_window_stable(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")  # syncToken 無し → フル取得
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {
            "items": [_event("e1", "予定1", start={"date": "2026-06-01"})],
            "nextPageToken": "page-2",
        },
        {
            "items": [_event("e2", "予定2", start={"date": "2026-06-02"})],
            "nextSyncToken": "final",
        },
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 2
        assert ToBellTask.query.count() == 2

    # 2ページとも同じ時間窓で要求し、2ページ目は pageToken を使う。
    assert len(fake.calls) == 2
    assert fake.calls[0]["params"]["timeMin"] == fake.calls[1]["params"]["timeMin"]
    assert fake.calls[0]["params"]["timeMax"] == fake.calls[1]["params"]["timeMax"]
    assert "pageToken" not in fake.calls[0]["params"]
    assert fake.calls[1]["params"]["pageToken"] == "page-2"


def test_incremental_sync_sends_synctoken_with_pagetoken(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice", sync_token="prev-token")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {"items": [_event("e1", "A", start={"date": "2026-06-01"})], "nextPageToken": "p2"},
        {"items": [_event("e2", "B", start={"date": "2026-06-02"})], "nextSyncToken": "next"},
    ])
    _patch_get(monkeypatch, fake)

    with app_ctx.app_context():
        imp.import_for_user("alice")

    # 増分取得では全ページで syncToken を維持し、2ページ目で pageToken を併用する（公式パターン）。
    assert fake.calls[0]["params"]["syncToken"] == "prev-token"
    assert "timeMin" not in fake.calls[0]["params"]
    assert fake.calls[1]["params"]["syncToken"] == "prev-token"
    assert fake.calls[1]["params"]["pageToken"] == "p2"


def test_initial_full_sync_uses_consistent_params(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")  # syncToken 無し
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([{"items": [], "nextSyncToken": "t"}])
    _patch_get(monkeypatch, fake)
    with app_ctx.app_context():
        imp.import_for_user("alice")

    p = fake.calls[0]["params"]
    assert p["singleEvents"] == "true"
    assert p["showDeleted"] == "true"  # 初回からキャンセルも見える＝増分と一致
    assert "orderBy" not in p          # orderBy は syncToken と併用不可なので一切使わない
    assert "syncToken" not in p
    assert "timeMin" in p and "timeMax" in p


def test_incremental_sync_params_match_initial(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice", sync_token="prev")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([{"items": [], "nextSyncToken": "t"}])
    _patch_get(monkeypatch, fake)
    with app_ctx.app_context():
        imp.import_for_user("alice")

    p = fake.calls[0]["params"]
    assert p["singleEvents"] == "true"
    assert p["showDeleted"] == "true"
    assert "orderBy" not in p
    assert p["syncToken"] == "prev"
    # syncToken 利用時は時間窓を再送しない（重複/不整合パラメータを避ける）。
    assert "timeMin" not in p and "timeMax" not in p


def test_one_bad_event_does_not_abort_batch(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar, to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")

    fake = FakeGet([
        {
            "items": [
                _event("e1", "良い予定", start={"date": "2026-06-01"}),
                _event("e2", "壊れた予定", start={"date": "2026-06-02"}),
            ],
            "nextSyncToken": "tok",
        }
    ])
    _patch_get(monkeypatch, fake)

    orig = imp._process_event

    def patched(username, event, mode, summary):
        if event.get("id") == "e2":
            raise RuntimeError("boom")
        return orig(username, event, mode, summary)

    monkeypatch.setattr(imp, "_process_event", patched)

    with app_ctx.app_context():
        summary = imp.import_for_user("alice")
        assert summary["created"] == 1
        assert summary["failed"] == 1
        # 不正イベントがあっても良いイベントは保存され、syncToken も前進する（詰まらない）。
        assert ToBellTask.query.count() == 1
        account = to_bell_calendar.get_account("alice")
        assert account.calendar_sync_token == "tok"


def test_import_requires_integration_enabled(app_ctx):
    from app.services import to_bell_calendar_import as imp
    from app.services.to_bell_calendar import ToBellCalendarError

    _create_user(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        with pytest.raises(ToBellCalendarError):
            imp.import_for_user("alice")


def test_import_requires_connection(app_ctx):
    from app.services import to_bell_calendar_import as imp
    from app.services.to_bell_calendar import ToBellCalendarError

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    with app_ctx.app_context():
        with pytest.raises(ToBellCalendarError):
            imp.import_for_user("alice")


# ---------- スケジューラの間隔判定 ----------

def test_run_due_imports_skips_manual(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_interval("alice", "manual")

    called = {"n": 0}
    monkeypatch.setattr(imp, "import_for_user", lambda u: called.__setitem__("n", called["n"] + 1))

    with app_ctx.app_context():
        stats = imp.run_due_imports()
        assert stats["users"] == 1
        assert stats["ran"] == 0
        assert called["n"] == 0


def test_run_due_imports_respects_interval(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    # 1時間間隔・最終取り込みが10分前 → まだ実行しない。
    _connect_account(app_ctx, "alice", last_import_at=datetime.utcnow() - timedelta(minutes=10))
    with app_ctx.app_context():
        imp.set_import_interval("alice", "1h")

    called = {"n": 0}
    monkeypatch.setattr(imp, "import_for_user", lambda u: called.__setitem__("n", called["n"] + 1))

    with app_ctx.app_context():
        stats = imp.run_due_imports()
        assert stats["ran"] == 0
        assert called["n"] == 0


def test_run_due_imports_runs_when_due(app_ctx, monkeypatch):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice", last_import_at=datetime.utcnow() - timedelta(minutes=90))
    with app_ctx.app_context():
        imp.set_import_interval("alice", "1h")

    called = {"n": 0}
    monkeypatch.setattr(imp, "import_for_user", lambda u: called.__setitem__("n", called["n"] + 1))

    with app_ctx.app_context():
        stats = imp.run_due_imports()
        assert stats["ran"] == 1
        assert called["n"] == 1


# ---------- API エンドポイント ----------

def test_status_endpoint_includes_import_fields(app_ctx, monkeypatch):
    from app.services import google_oauth

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    monkeypatch.setattr(google_oauth, "is_configured", lambda: True)

    status = client.get("/tools/to_bell/api/google/status").get_json()
    assert status["import_enabled"] is True
    assert status["import_mode"] == "tb_suffix"
    assert status["import_interval"] == "15m"
    assert status["last_import_at"] is None


def test_import_settings_endpoint_persists(app_ctx):
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    resp = client.put(
        "/tools/to_bell/api/google/import-settings",
        json={"mode": "organizer", "interval": "3h"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["import_mode"] == "organizer"
    assert data["import_interval"] == "3h"
    with app_ctx.app_context():
        assert imp.get_import_mode("alice") == "organizer"
        assert imp.get_import_interval("alice") == "3h"


def test_import_settings_rejects_invalid_mode(app_ctx):
    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    resp = client.put("/tools/to_bell/api/google/import-settings", json={"mode": "bogus"})
    assert resp.status_code == 400


def test_import_now_endpoint_runs(app_ctx, monkeypatch):
    from app.models import ToBellTask
    from app.services import to_bell_calendar_import as imp

    _create_user(app_ctx, "alice")
    _enable_import(app_ctx, "alice")
    _connect_account(app_ctx, "alice")
    with app_ctx.app_context():
        imp.set_import_mode("alice", "all")
    client = app_ctx.test_client()
    _login(client, "alice")

    fake = FakeGet([{"items": [_event("e1", "予定", start={"date": "2026-06-01"})], "nextSyncToken": "t"}])
    _patch_get(monkeypatch, fake)

    resp = client.post("/tools/to_bell/api/google/import")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"]["created"] == 1
    with app_ctx.app_context():
        assert ToBellTask.query.count() == 1
