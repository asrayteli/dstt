"""ToBell 使いやすさ改善（連携フィルタ分離 / 連携タスク自動削除 /
プロジェクト日付 / タスク名一括削除）の回帰テスト。"""
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

    db_path = tmp_path / "to_bell.db"
    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "TO_BELL_ATTACHMENT_DIR": str(tmp_path / "attachments"),
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def _create_user(app_ctx, username: str, name: str | None = None, office_id: int | None = None):
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name=name or username, office_id=office_id)
        db.session.add(user)
        db.session.commit()


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _add_integration_task(app_ctx, **kwargs):
    from app.models import ToBellTask, db

    defaults = dict(
        title="連携タスク",
        created_by="alice",
        assigned_to="alice",
        status="todo",
        source_tool="cloudshift",
        source_ref_type="leave_change_request",
        source_ref_id="x:1",
    )
    defaults.update(kwargs)
    with app_ctx.app_context():
        task = ToBellTask(**defaults)
        db.session.add(task)
        db.session.commit()
        return task.id


# ---- 要件2: 連携タスクは通常フィルタから除外、専用フィルタにのみ表示 ----

def test_integration_tasks_hidden_from_normal_filters(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    # 手動タスク
    client.post("/tools/to_bell/api/tasks", json={"title": "手動タスク"})
    # 連携タスク（CloudShift 由来）
    _add_integration_task(app_ctx, title="CloudShift連携タスク", source_ref_id="cs:1")

    for filt in ("today", "inbox", "assigned", "attention"):
        res = client.get(f"/tools/to_bell/api/tasks?filter={filt}")
        titles = {t["title"] for t in res.get_json()["tasks"]}
        assert "CloudShift連携タスク" not in titles, f"{filt} に連携タスクが漏れている"

    # board（カンバン/カレンダー）にも出ない
    res = client.get("/tools/to_bell/api/tasks?filter=board&view=kanban")
    titles = {t["title"] for t in res.get_json()["tasks"]}
    assert "CloudShift連携タスク" not in titles


def test_integration_filter_shows_only_integration_tasks(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    client.post("/tools/to_bell/api/tasks", json={"title": "手動タスク"})
    _add_integration_task(app_ctx, title="健診リマインド", source_tool="health_check",
                          source_ref_type="reservation", source_ref_id="1:alice")
    _add_integration_task(app_ctx, title="カレンダー取り込み", source_tool="google",
                          source_ref_type="calendar_event", source_ref_id="alice:ev1")

    res = client.get("/tools/to_bell/api/tasks?filter=integrations")
    titles = {t["title"] for t in res.get_json()["tasks"]}
    assert titles == {"健診リマインド", "カレンダー取り込み"}
    assert "手動タスク" not in titles


def test_summary_action_count_excludes_integration_tasks(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    _add_integration_task(app_ctx, title="連携のみ", source_ref_id="cs:only")
    res = client.get("/tools/to_bell/api/tasks?filter=today")
    summary = res.get_json()["summary"]
    # 連携タスクしかないので、タスク由来の要対応バッジは出ない
    labels = " ".join(b["label"] for b in summary.get("badges", []))
    assert "未着手" not in labels


# ---- 要件2: 連携タスクは追加から1か月で自動削除（カレンダーには触れない） ----

def test_cleanup_deletes_integration_tasks_after_one_month(app_ctx):
    from app.models import ToBellTask, db
    from app.services.to_bell_service import cleanup_expired_records

    _create_user(app_ctx, "alice", "Alice")
    now = datetime(2026, 6, 26, 12, 0)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=5)

    with app_ctx.app_context():
        db.session.add_all([
            ToBellTask(title="古い連携", created_by="alice", assigned_to="alice", status="todo",
                       source_tool="google", source_ref_type="calendar_event", source_ref_id="alice:old",
                       created_at=old),
            ToBellTask(title="最近の連携", created_by="alice", assigned_to="alice", status="todo",
                       source_tool="cloudshift", source_ref_type="leave_change_request", source_ref_id="cs:new",
                       created_at=recent),
            ToBellTask(title="古い手動", created_by="alice", assigned_to="alice", status="todo",
                       created_at=old),  # 手動・期限なしは消えない
        ])
        db.session.commit()

        result = cleanup_expired_records(now=now)
        assert result["integration_tasks"] == 1
        remaining = {t.title for t in ToBellTask.query.all()}
        assert remaining == {"最近の連携", "古い手動"}


# ---- 要件4: プロジェクトの日付 ----

def test_project_due_date_create_update_and_clear(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    created = client.post("/tools/to_bell/api/projects",
                          json={"name": "日付つきPJ", "due_date": "2026-07-15"}).get_json()
    assert created["due_at"] is not None
    assert created["due_at"].startswith("2026-07-15")

    # 一覧にも due_at が乗る
    listed = client.get("/tools/to_bell/api/projects").get_json()["projects"]
    target = next(p for p in listed if p["id"] == created["id"])
    assert target["due_at"].startswith("2026-07-15")

    # クリアできる
    updated = client.put(f"/tools/to_bell/api/projects/{created['id']}",
                         json={"due_date": ""}).get_json()
    assert updated["due_at"] is None


# ---- 要件6: タスク名による一括削除（設定から） ----

def test_bulk_delete_by_name_exact_and_contains(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    client.post("/tools/to_bell/api/tasks", json={"title": "毎日の点検"})
    client.post("/tools/to_bell/api/tasks", json={"title": "毎日の点検"})
    client.post("/tools/to_bell/api/tasks", json={"title": "毎日の点検（特別）"})
    client.post("/tools/to_bell/api/tasks", json={"title": "別のタスク"})

    # プレビュー（完全一致は2件）
    preview = client.get("/tools/to_bell/api/tasks/bulk-delete?name=毎日の点検&match=exact").get_json()
    assert preview["count"] == 2

    # 部分一致は3件
    preview2 = client.get("/tools/to_bell/api/tasks/bulk-delete?name=毎日の点検&match=contains").get_json()
    assert preview2["count"] == 3

    # 完全一致で削除 → 2件
    res = client.post("/tools/to_bell/api/tasks/bulk-delete", json={"name": "毎日の点検", "match": "exact"})
    assert res.get_json()["deleted"] == 2

    remaining = client.get("/tools/to_bell/api/tasks?filter=today").get_json()["tasks"]
    titles = sorted(t["title"] for t in remaining)
    assert titles == ["別のタスク", "毎日の点検（特別）"]


def test_bulk_delete_only_affects_own_tasks(app_ctx):
    from app.models import ToBellTask, db

    office = None
    _create_user(app_ctx, "alice", "Alice")
    _create_user(app_ctx, "bob", "Bob")
    client = app_ctx.test_client()
    _login(client, "alice")

    # bob だけが見られるタスク
    with app_ctx.app_context():
        db.session.add(ToBellTask(title="共通名", created_by="bob", assigned_to="bob", status="todo"))
        db.session.commit()
    # alice のタスク
    client.post("/tools/to_bell/api/tasks", json={"title": "共通名"})

    res = client.post("/tools/to_bell/api/tasks/bulk-delete", json={"name": "共通名"})
    assert res.get_json()["deleted"] == 1  # alice のものだけ

    with app_ctx.app_context():
        remaining = {t.created_by for t in ToBellTask.query.filter_by(title="共通名").all()}
        assert remaining == {"bob"}


def test_bulk_delete_requires_name(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    res = client.post("/tools/to_bell/api/tasks/bulk-delete", json={"name": "  "})
    assert res.status_code == 400
