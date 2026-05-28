from __future__ import annotations

import io
import sys
from datetime import date
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


def _create_office(app_ctx, name: str):
    from app.models import AccessBranch, AccessOffice, db

    with app_ctx.app_context():
        branch = AccessBranch(name=f"{name}支店", code=f"{name}-b")
        db.session.add(branch)
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name=f"{name}営業所", code=f"{name}-o")
        db.session.add(office)
        db.session.commit()
        return office.id


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


def test_to_bell_can_create_complete_and_comment_task(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    create_response = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "請求内容を確認", "due_at": "2026-05-26", "tags": "請求,確認"},
    )
    assert create_response.status_code == 201
    task = create_response.get_json()
    assert task["title"] == "請求内容を確認"
    assert task["assigned_to"] == "alice"
    assert task["tags"][0]["name"] == "請求"

    subtask_response = client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/subtasks",
        json={"title": "金額を照合"},
    )
    assert subtask_response.status_code == 201
    subtask = subtask_response.get_json()

    update_subtask = client.put(
        f"/tools/to_bell/api/subtasks/{subtask['id']}",
        json={"is_done": True},
    )
    assert update_subtask.status_code == 200

    detail_response = client.get(f"/tools/to_bell/api/tasks/{task['id']}")
    assert detail_response.get_json()["progress"] == 100

    comment_response = client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/comments",
        json={"body": "担当へ確認済み"},
    )
    assert comment_response.status_code == 201

    complete_response = client.post(f"/tools/to_bell/api/tasks/{task['id']}/complete")
    assert complete_response.status_code == 200
    assert complete_response.get_json()["status"] == "done"


def test_to_bell_archive_keeps_record_but_hard_delete_removes_it(app_ctx):
    from app.models import ToBellTask, db

    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    first = client.post("/tools/to_bell/api/tasks", json={"title": "アーカイブ対象"}).get_json()
    second = client.post("/tools/to_bell/api/tasks", json={"title": "削除対象"}).get_json()

    archive_response = client.delete(f"/tools/to_bell/api/tasks/{first['id']}")
    assert archive_response.status_code == 200
    with app_ctx.app_context():
        archived = db.session.get(ToBellTask, first["id"])
        assert archived is not None
        assert archived.status == "archived"

    delete_response = client.delete(f"/tools/to_bell/api/tasks/{second['id']}?hard=1")
    assert delete_response.status_code == 200
    with app_ctx.app_context():
        assert db.session.get(ToBellTask, second["id"]) is None


def test_to_bell_page_renders(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    response = client.get("/tools/to_bell/")

    assert response.status_code == 200
    assert "To Bell".encode("utf-8") in response.data
    assert "/static/to_bell/to_bell.js" in response.get_data(as_text=True)
    assert 'type="time"' in response.get_data(as_text=True)
    assert 'id="tb-enable-push-notify"' in response.get_data(as_text=True)
    assert 'id="tb-open-notifier"' in response.get_data(as_text=True)
    assert 'id="tb-flash"' in response.get_data(as_text=True)
    # PWA 専用リロードボタンは存在し、既定では hidden（JS が standalone 判定で表示）
    assert 'id="tb-reload"' in response.get_data(as_text=True)


def test_to_bell_assignment_creates_unread_notification_and_dashboard_summary(app_ctx):
    office_id = _create_office(app_ctx, "same")
    _create_user(app_ctx, "owner", "Owner", office_id=office_id)
    _create_user(app_ctx, "worker", "Worker", office_id=office_id)

    owner_client = app_ctx.test_client()
    _login(owner_client, "owner")
    response = owner_client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "月次処理", "assigned_to": "worker"},
    )
    assert response.status_code == 201

    worker_client = app_ctx.test_client()
    _login(worker_client, "worker")
    notifications = worker_client.get("/tools/to_bell/api/notifications").get_json()["notifications"]
    assert len(notifications) == 1
    assert notifications[0]["is_read"] is False

    summary = worker_client.get("/api/tool-notifications").get_json()
    assert summary["to_bell"]["unread_count"] == 1
    assert summary["to_bell"]["action_count"] >= 1


def test_to_bell_tasks_are_limited_to_participants(app_ctx):
    office_id = _create_office(app_ctx, "participants")
    other_office_id = _create_office(app_ctx, "outside")
    _create_user(app_ctx, "alice", office_id=office_id)
    _create_user(app_ctx, "bob", office_id=office_id)
    _create_user(app_ctx, "charlie", office_id=other_office_id)

    client = app_ctx.test_client()
    _login(client, "alice")
    response = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "内部確認", "assigned_to": "bob"},
    )
    task_id = response.get_json()["id"]

    other_client = app_ctx.test_client()
    _login(other_client, "charlie")
    assert other_client.get(f"/tools/to_bell/api/tasks/{task_id}").status_code == 400
    assert other_client.get("/tools/to_bell/api/tasks").get_json()["tasks"] == []


def test_to_bell_rejects_assignment_outside_same_office(app_ctx):
    owner_office = _create_office(app_ctx, "owner")
    other_office = _create_office(app_ctx, "other")
    _create_user(app_ctx, "owner", "Owner", office_id=owner_office)
    _create_user(app_ctx, "same-worker", "Same", office_id=owner_office)
    _create_user(app_ctx, "other-worker", "Other", office_id=other_office)

    client = app_ctx.test_client()
    _login(client, "owner")
    page = client.get("/tools/to_bell/").get_data(as_text=True)
    assert "same-worker" in page
    assert "other-worker" not in page

    rejected = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "別営業所には出せない", "assigned_to": "other-worker"},
    )
    assert rejected.status_code == 400

    accepted = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "同じ営業所へ通知", "assigned_to": "same-worker"},
    )
    assert accepted.status_code == 201


def test_to_bell_quick_datetime_is_optional_and_due_tasks_notify(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    no_due = client.post("/tools/to_bell/api/tasks", json={"title": "あとで整理"})
    assert no_due.status_code == 201
    assert no_due.get_json()["due_at"] is None

    with_due = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "時間指定", "due_date": "2026-05-01", "due_time": "14:30"},
    )
    assert with_due.status_code == 201
    assert with_due.get_json()["due_at"].startswith("2026-05-01T14:30")

    due_tasks = client.get("/tools/to_bell/api/notifications/due-tasks").get_json()["tasks"]
    assert any(task["title"] == "時間指定" for task in due_tasks)
    assert all(task["title"] != "あとで整理" for task in due_tasks)


def test_to_bell_time_without_date_defaults_to_today(app_ctx):
    from datetime import date

    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    response = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "時刻だけ指定", "due_time": "09:00"},
    )
    assert response.status_code == 201
    due_at = response.get_json()["due_at"]
    assert due_at is not None
    assert due_at.startswith(f"{date.today().isoformat()}T09:00")


def test_to_bell_cleanup_deletes_records_older_than_60_days(app_ctx):
    from datetime import datetime, timedelta

    from app.models import ToBellNotification, ToBellTask, db
    from app.services.to_bell_service import cleanup_expired_records

    _create_user(app_ctx, "alice", "Alice")
    now = datetime(2026, 5, 26, 12, 0)
    old = now - timedelta(days=61)
    recent = now - timedelta(days=10)

    with app_ctx.app_context():
        db.session.add_all(
            [
                ToBellTask(title="古い完了", created_by="alice", assigned_to="alice", status="done", completed_at=old),
                ToBellTask(title="古い未対応", created_by="alice", assigned_to="alice", status="todo", due_at=old),
                ToBellTask(title="最近の完了", created_by="alice", assigned_to="alice", status="done", completed_at=recent),
                ToBellNotification(user_id="alice", title="古い通知", created_at=old),
            ]
        )
        db.session.commit()

        result = cleanup_expired_records(now=now)

        assert result["tasks"] == 2
        assert result["notifications"] == 1
        remaining_titles = {task.title for task in ToBellTask.query.all()}
        assert remaining_titles == {"最近の完了"}


def test_to_bell_notifier_and_service_worker_render(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    notifier = client.get("/tools/to_bell/notifier")
    assert notifier.status_code == 200
    assert "通知待受" in notifier.get_data(as_text=True)

    worker = client.get("/service-worker.js")
    assert worker.status_code == 200
    assert "self.addEventListener(\"push\"" in worker.get_data(as_text=True)


def test_to_bell_push_subscription_endpoints(app_ctx, monkeypatch):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    key_response = client.get("/tools/to_bell/api/push/public-key")
    assert key_response.status_code == 200
    assert key_response.get_json()["public_key"]

    subscription = {
        "endpoint": "https://example.test/push/1",
        "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
    }
    response = client.post("/tools/to_bell/api/push/subscribe", json={"subscription": subscription})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

    import app.tools.to_bell as to_bell_module

    monkeypatch.setattr(to_bell_module, "send_test_push", lambda user_id: {"sent": 1, "failed": 0})
    test_response = client.post("/tools/to_bell/api/push/test")
    assert test_response.status_code == 200
    assert test_response.get_json()["sent"] == 1

    unsubscribe_response = client.post(
        "/tools/to_bell/api/push/unsubscribe",
        json={"endpoint": subscription["endpoint"]},
    )
    assert unsubscribe_response.status_code == 200
    assert unsubscribe_response.get_json()["updated"] is True


def test_send_push_uses_fresh_vapid_claims_per_subscription(app_ctx, monkeypatch, tmp_path):
    """配信先が異なる複数端末（例: PCとiPhone）でも、それぞれ正しい aud で署名されること。

    pywebpush は渡した claims に aud を書き込む。claims を使い回すと 2 件目以降の
    aud が 1 件目の配信先のままになり、別の push サービスで失敗する（iPhone の
    テスト通知が「失敗」になっていた原因）。
    """
    from urllib.parse import urlparse

    from app.models import ToBellPushSubscription, db
    from app.services import to_bell_push

    _create_user(app_ctx, "alice", "Alice")

    pem = tmp_path / "vapid.pem"
    pem.write_text("dummy-key")
    monkeypatch.setattr(to_bell_push, "_vapid_private_key_path", lambda: pem)

    class FakeWebPushException(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    seen = []

    def fake_webpush(*, subscription_info, data, vapid_private_key, vapid_claims, ttl, timeout):
        endpoint = subscription_info["endpoint"]
        parsed = urlparse(endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        # pywebpush と同様に claims を破壊的に書き換える。
        if not vapid_claims.get("aud"):
            vapid_claims["aud"] = origin
        seen.append((endpoint, vapid_claims["aud"]))
        # aud が配信先 origin と一致しなければ push サービスは署名を拒否する。
        if vapid_claims["aud"] != origin:
            raise FakeWebPushException("VAPID audience mismatch")

    monkeypatch.setattr(to_bell_push, "webpush", fake_webpush)
    monkeypatch.setattr(to_bell_push, "WebPushException", FakeWebPushException)

    with app_ctx.app_context():
        db.session.add_all(
            [
                ToBellPushSubscription(
                    user_id="alice", endpoint="https://fcm.googleapis.com/fcm/send/pc",
                    p256dh="k", auth="a",
                ),
                ToBellPushSubscription(
                    user_id="alice", endpoint="https://web.push.apple.com/iphone",
                    p256dh="k", auth="a",
                ),
            ]
        )
        db.session.commit()
        result = to_bell_push.send_push_to_user("alice", title="t", body="b", url="/u")

    assert result == {"sent": 2, "failed": 0}
    assert {aud for _, aud in seen} == {
        "https://fcm.googleapis.com",
        "https://web.push.apple.com",
    }


def test_send_due_task_pushes_retries_after_failure(app_ctx, monkeypatch):
    """送信失敗は「配信済み」として確定せず、次回スケジュールで再送されること。"""
    from datetime import datetime, timedelta

    from app.models import ToBellPushDelivery, ToBellTask, db
    from app.services import to_bell_push

    _create_user(app_ctx, "alice", "Alice")
    calls = {"n": 0}

    def fake_send(user_id, *, title, body, url):
        calls["n"] += 1
        return {"sent": 0, "failed": 1} if calls["n"] == 1 else {"sent": 1, "failed": 0}

    monkeypatch.setattr(to_bell_push, "send_push_to_user", fake_send)

    with app_ctx.app_context():
        due = datetime.now() - timedelta(minutes=5)
        db.session.add(
            ToBellTask(title="期限切れ", created_by="alice", assigned_to="alice", status="todo", due_at=due)
        )
        db.session.commit()

        first = to_bell_push.send_due_task_pushes()
        assert first["failed"] == 1
        assert first["sent"] == 0

        second = to_bell_push.send_due_task_pushes()
        assert second["sent"] == 1
        assert calls["n"] == 2

        delivery = ToBellPushDelivery.query.filter_by(user_id="alice").one()
        assert delivery.status == "sent"

        third = to_bell_push.send_due_task_pushes()
        assert third["skipped"] == 1
        assert calls["n"] == 2


def test_send_due_task_pushes_without_subscription_is_not_retried(app_ctx, monkeypatch):
    """有効な購読が無い場合は確定状態として記録し、過去分が後から一斉配信されないこと。"""
    from datetime import datetime, timedelta

    from app.models import ToBellPushDelivery, ToBellTask, db
    from app.services import to_bell_push

    _create_user(app_ctx, "alice", "Alice")
    calls = {"n": 0}

    def fake_send(user_id, *, title, body, url):
        calls["n"] += 1
        return {"sent": 0, "failed": 0}

    monkeypatch.setattr(to_bell_push, "send_push_to_user", fake_send)

    with app_ctx.app_context():
        due = datetime.now() - timedelta(minutes=5)
        db.session.add(
            ToBellTask(title="購読なし", created_by="alice", assigned_to="alice", status="todo", due_at=due)
        )
        db.session.commit()

        to_bell_push.send_due_task_pushes()
        result = to_bell_push.send_due_task_pushes()

        assert calls["n"] == 1
        assert result["skipped"] == 1
        delivery = ToBellPushDelivery.query.filter_by(user_id="alice").one()
        assert delivery.status == "skipped"


def test_to_bell_project_crud_and_task_assignment(app_ctx):
    office_id = _create_office(app_ctx, "proj")
    _create_user(app_ctx, "alice", "Alice", office_id=office_id)
    client = app_ctx.test_client()
    _login(client, "alice")

    created = client.post("/tools/to_bell/api/projects", json={"name": "月次作業", "color": "#22c55e"})
    assert created.status_code == 201
    project = created.get_json()
    assert project["name"] == "月次作業"

    task = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "請求書を確認", "project_id": project["id"]},
    ).get_json()
    assert task["project"]["id"] == project["id"]
    assert task["project"]["name"] == "月次作業"

    projects = client.get("/tools/to_bell/api/projects").get_json()["projects"]
    assert projects[0]["open_count"] == 1

    # 更新と削除（削除してもタスクは残り、紐付けだけ外れる）
    renamed = client.put(f"/tools/to_bell/api/projects/{project['id']}", json={"name": "月次処理"})
    assert renamed.get_json()["name"] == "月次処理"

    assert client.delete(f"/tools/to_bell/api/projects/{project['id']}").status_code == 200
    detail = client.get(f"/tools/to_bell/api/tasks/{task['id']}").get_json()
    assert detail["project"] is None
    assert detail["project_id"] is None


def test_to_bell_project_not_visible_to_other_office(app_ctx):
    office_a = _create_office(app_ctx, "alpha")
    office_b = _create_office(app_ctx, "beta")
    _create_user(app_ctx, "alice", "Alice", office_id=office_a)
    _create_user(app_ctx, "carol", "Carol", office_id=office_b)

    alice = app_ctx.test_client()
    _login(alice, "alice")
    project = alice.post("/tools/to_bell/api/projects", json={"name": "営業所内のみ"}).get_json()

    carol = app_ctx.test_client()
    _login(carol, "carol")
    assert carol.get("/tools/to_bell/api/projects").get_json()["projects"] == []
    # 別営業所のプロジェクトには紐付けられない
    rejected = carol.post("/tools/to_bell/api/tasks", json={"title": "不可", "project_id": project["id"]})
    assert rejected.status_code == 400


def test_to_bell_template_from_task_and_instantiate(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    task = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "毎月の手順", "priority": "high", "tags": "月次"},
    ).get_json()
    client.post(f"/tools/to_bell/api/tasks/{task['id']}/subtasks", json={"title": "手順1"})
    client.post(f"/tools/to_bell/api/tasks/{task['id']}/subtasks", json={"title": "手順2"})

    template = client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/template",
        json={"name": "月次テンプレ"},
    )
    assert template.status_code == 201
    template_id = template.get_json()["id"]
    assert template.get_json()["subtask_count"] == 2

    templates = client.get("/tools/to_bell/api/templates").get_json()["templates"]
    assert any(item["id"] == template_id for item in templates)

    instance = client.post(f"/tools/to_bell/api/templates/{template_id}/instantiate")
    assert instance.status_code == 201
    new_task = instance.get_json()
    assert new_task["title"] == "毎月の手順"
    assert new_task["priority"] == "high"
    assert len(new_task["subtasks"]) == 2

    assert client.delete(f"/tools/to_bell/api/templates/{template_id}").status_code == 200
    assert client.get("/tools/to_bell/api/templates").get_json()["templates"] == []


def test_to_bell_blank_template_create_and_instantiate(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    template = client.post("/tools/to_bell/api/templates", json={"name": "空テンプレ"})
    assert template.status_code == 201
    instance = client.post(
        f"/tools/to_bell/api/templates/{template.get_json()['id']}/instantiate",
        json={"title": "上書きタイトル"},
    )
    assert instance.status_code == 201
    assert instance.get_json()["title"] == "上書きタイトル"


def test_to_bell_attachment_upload_download_delete(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    task = client.post("/tools/to_bell/api/tasks", json={"title": "添付つき"}).get_json()

    upload = client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/attachments",
        data={"file": (io.BytesIO(b"hello to bell"), "memo.txt")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    attachment = upload.get_json()
    assert attachment["file_name"] == "memo.txt"
    assert attachment["file_size"] == len(b"hello to bell")

    detail = client.get(f"/tools/to_bell/api/tasks/{task['id']}").get_json()
    assert len(detail["attachments"]) == 1

    download = client.get(f"/tools/to_bell/api/attachments/{attachment['id']}")
    assert download.status_code == 200
    assert download.data == b"hello to bell"

    assert client.delete(f"/tools/to_bell/api/attachments/{attachment['id']}").status_code == 200
    detail = client.get(f"/tools/to_bell/api/tasks/{task['id']}").get_json()
    assert detail["attachments"] == []


def test_to_bell_attachment_not_visible_to_outsider(app_ctx):
    office_id = _create_office(app_ctx, "att")
    other_office = _create_office(app_ctx, "att-out")
    _create_user(app_ctx, "alice", "Alice", office_id=office_id)
    _create_user(app_ctx, "mallory", "Mallory", office_id=other_office)

    alice = app_ctx.test_client()
    _login(alice, "alice")
    task = alice.post("/tools/to_bell/api/tasks", json={"title": "機密添付"}).get_json()
    attachment = alice.post(
        f"/tools/to_bell/api/tasks/{task['id']}/attachments",
        data={"file": (io.BytesIO(b"secret"), "secret.txt")},
        content_type="multipart/form-data",
    ).get_json()

    mallory = app_ctx.test_client()
    _login(mallory, "mallory")
    assert mallory.get(f"/tools/to_bell/api/attachments/{attachment['id']}").status_code == 404


def test_to_bell_kanban_board_and_pages_render(app_ctx):
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    client.post("/tools/to_bell/api/tasks", json={"title": "未着手タスク"})
    board = client.get("/tools/to_bell/api/tasks?filter=board").get_json()["tasks"]
    assert any(task["title"] == "未着手タスク" for task in board)

    pc_page = client.get("/tools/to_bell/").get_data(as_text=True)
    assert 'data-view="kanban"' in pc_page
    assert 'id="tb-templates"' in pc_page
    assert 'id="tb-project-list"' in pc_page
    # ビューワは両ページで提供する
    assert 'id="tb-viewer"' in pc_page
    assert 'data-viewer-close' in pc_page
    assert 'data-viewer-zoom="1"' in pc_page

    pwa_page = client.get("/tools/to_bell/pwa").get_data(as_text=True)
    assert 'id="tb-project-bar"' in pwa_page
    assert 'tobell-views-pwa' in pwa_page
    assert 'id="tb-viewer"' in pwa_page
    assert 'data-viewer-close' in pwa_page


def test_to_bell_attachment_image_mime_recorded(app_ctx):
    """画像添付のMIMEが保存され、フロントがプレビュー対象として扱えること。"""
    _create_user(app_ctx, "alice", "Alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    task = client.post("/tools/to_bell/api/tasks", json={"title": "画像つき"}).get_json()
    # 1x1 PNG（実体は最小限）
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    upload = client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/attachments",
        data={"file": (io.BytesIO(png_bytes), "shot.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    assert upload.get_json()["mime_type"] == "image/png"


def test_share_token_lets_anonymous_user_act_as_owner_on_to_bell_only(app_ctx):
    """共有URLでログイン無しに「そのユーザーとして」ToBell のみ操作できること。"""
    _create_user(app_ctx, "alice", "Alice")

    owner = app_ctx.test_client()
    _login(owner, "alice")
    issued = owner.post("/tools/to_bell/api/share/issue")
    assert issued.status_code == 200
    payload = issued.get_json()
    assert payload["active"] is True
    assert "/tools/to_bell/s/" in payload["url"]
    token = payload["url"].rsplit("/", 1)[-1]

    # ログインしていない別クライアントが共有URLを開く
    guest = app_ctx.test_client()
    landing = guest.get(f"/tools/to_bell/s/{token}")
    assert landing.status_code == 302
    assert landing.headers["Location"].endswith("/tools/to_bell/")

    # Cookie により alice として ToBell API を使える
    created = guest.post("/tools/to_bell/api/tasks", json={"title": "共有から追加"})
    assert created.status_code == 201
    assert created.get_json()["created_by"] == "alice"

    # ToBell 以外は使えない（ログインへリダイレクト）
    other = guest.get("/", follow_redirects=False)
    assert other.status_code in (301, 302)
    assert "/auth/login" in other.headers["Location"]


def test_share_session_cannot_manage_share_token(app_ctx):
    """共有トークンで入ったセッションは共有リンクの発行・無効化ができないこと。"""
    _create_user(app_ctx, "alice", "Alice")

    owner = app_ctx.test_client()
    _login(owner, "alice")
    token = owner.post("/tools/to_bell/api/share/issue").get_json()["url"].rsplit("/", 1)[-1]

    guest = app_ctx.test_client()
    guest.get(f"/tools/to_bell/s/{token}")

    assert guest.get("/tools/to_bell/api/share").status_code == 403
    assert guest.post("/tools/to_bell/api/share/issue").status_code == 403
    assert guest.post("/tools/to_bell/api/share/revoke").status_code == 403


def test_share_token_revoke_and_reissue_invalidate_old_token(app_ctx):
    """無効化・再発行で旧トークンが使えなくなること。"""
    _create_user(app_ctx, "alice", "Alice")

    owner = app_ctx.test_client()
    _login(owner, "alice")
    first = owner.post("/tools/to_bell/api/share/issue").get_json()["url"].rsplit("/", 1)[-1]

    # 無効化すると旧URLは 404
    owner.post("/tools/to_bell/api/share/revoke")
    assert app_ctx.test_client().get(f"/tools/to_bell/s/{first}").status_code == 404

    # 再発行すると新URLは有効、旧URLは無効のまま
    second = owner.post("/tools/to_bell/api/share/issue").get_json()["url"].rsplit("/", 1)[-1]
    assert second != first
    assert app_ctx.test_client().get(f"/tools/to_bell/s/{second}").status_code == 302
    assert app_ctx.test_client().get(f"/tools/to_bell/s/{first}").status_code == 404


def test_share_session_page_hides_sidebar_and_nav(app_ctx):
    """共有リンクで開いたページにはサイドバー/他ツール導線が出ないこと。"""
    _create_user(app_ctx, "alice", "Alice")

    owner = app_ctx.test_client()
    _login(owner, "alice")
    token = owner.post("/tools/to_bell/api/share/issue").get_json()["url"].rsplit("/", 1)[-1]

    # 通常ログインのページにはサイドバーがある
    normal = owner.get("/tools/to_bell/")
    normal_html = normal.get_data(as_text=True)
    assert 'id="app-sidebar"' in normal_html
    assert 'id="sidebar-toggle"' in normal_html

    # 共有リンクのセッションではサイドバー/トグル/共有ボタンが消える
    guest = app_ctx.test_client()
    guest.get(f"/tools/to_bell/s/{token}")
    shared = guest.get("/tools/to_bell/")
    shared_html = shared.get_data(as_text=True)
    assert 'id="app-sidebar"' not in shared_html
    assert 'id="sidebar-toggle"' not in shared_html
    assert 'id="tb-share-link"' not in shared_html
    # ただし ToBell 本体は表示される
    assert 'tobell-shell' in shared_html


def test_project_calendar_only_hides_tasks_from_list_and_kanban(app_ctx):
    office_id = _create_office(app_ctx, "calonly")
    _create_user(app_ctx, "alice", "Alice", office_id=office_id)
    client = app_ctx.test_client()
    _login(client, "alice")

    hidden = client.post(
        "/tools/to_bell/api/projects",
        json={"name": "カレンダー専用", "calendar_only": True},
    ).get_json()
    assert hidden["calendar_only"] is True

    open_project = client.post("/tools/to_bell/api/projects", json={"name": "通常"}).get_json()
    assert open_project["calendar_only"] is False

    today = date.today().isoformat()
    hidden_task = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "隠れタスク", "due_date": today, "project_id": hidden["id"]},
    ).get_json()
    visible_task = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "見えるタスク", "due_date": today, "project_id": open_project["id"]},
    ).get_json()

    # リスト（today フィルタ）には calendar_only プロジェクトのタスクは出てこない
    list_titles = [task["title"] for task in client.get("/tools/to_bell/api/tasks?filter=today&view=list").get_json()["tasks"]]
    assert "見えるタスク" in list_titles
    assert "隠れタスク" not in list_titles

    # カンバン（board フィルタ）でも隠す
    board_titles = [task["title"] for task in client.get("/tools/to_bell/api/tasks?filter=board&view=kanban").get_json()["tasks"]]
    assert "見えるタスク" in board_titles
    assert "隠れタスク" not in board_titles

    # カレンダーでは両方表示される
    calendar_titles = [task["title"] for task in client.get("/tools/to_bell/api/tasks?filter=board&view=calendar").get_json()["tasks"]]
    assert "見えるタスク" in calendar_titles
    assert "隠れタスク" in calendar_titles

    # プロジェクトで絞り込んだときは calendar_only でもリストに表示する
    filtered_titles = [
        task["title"]
        for task in client.get(
            f"/tools/to_bell/api/tasks?filter=board&view=list&project_id={hidden['id']}"
        ).get_json()["tasks"]
    ]
    assert filtered_titles == ["隠れタスク"]

    # 後から calendar_only を外せばリストにも復活する
    client.put(f"/tools/to_bell/api/projects/{hidden['id']}", json={"calendar_only": False})
    updated_titles = [task["title"] for task in client.get("/tools/to_bell/api/tasks?filter=today&view=list").get_json()["tasks"]]
    assert "隠れタスク" in updated_titles
    # 念のため未使用変数を握り潰す
    assert hidden_task["id"] and visible_task["id"]


def test_project_bulk_assign_existing_tasks(app_ctx):
    office_id = _create_office(app_ctx, "bulk")
    _create_user(app_ctx, "alice", "Alice", office_id=office_id)
    _create_user(app_ctx, "bob", "Bob", office_id=office_id)
    client = app_ctx.test_client()
    _login(client, "alice")

    project = client.post("/tools/to_bell/api/projects", json={"name": "請求まとめ"}).get_json()
    one = client.post("/tools/to_bell/api/tasks", json={"title": "請求A"}).get_json()
    two = client.post("/tools/to_bell/api/tasks", json={"title": "請求B"}).get_json()
    other = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "別作業", "project_id": project["id"]},
    ).get_json()

    # 紐づいていないタスクが候補として返る
    candidates = client.get(f"/tools/to_bell/api/projects/{project['id']}/assignable-tasks").get_json()["tasks"]
    candidate_ids = {task["id"] for task in candidates}
    assert one["id"] in candidate_ids
    assert two["id"] in candidate_ids
    assert other["id"] not in candidate_ids  # すでに紐づいているので候補に含まれない

    # 一括で紐づける
    assigned = client.post(
        f"/tools/to_bell/api/projects/{project['id']}/assign-tasks",
        json={"task_ids": [one["id"], two["id"]]},
    )
    assert assigned.status_code == 200
    assert assigned.get_json()["updated"] == 2

    after = client.get(f"/tools/to_bell/api/tasks/{one['id']}").get_json()
    assert after["project"]["id"] == project["id"]

    # 再度呼んでも、すでに紐づいているタスクは候補に出ない
    later = client.get(f"/tools/to_bell/api/projects/{project['id']}/assignable-tasks").get_json()["tasks"]
    assert all(task["id"] not in (one["id"], two["id"], other["id"]) for task in later)


def test_project_bulk_assign_skips_tasks_user_cannot_edit(app_ctx):
    office_id = _create_office(app_ctx, "bulk2")
    _create_user(app_ctx, "alice", "Alice", office_id=office_id)
    _create_user(app_ctx, "bob", "Bob", office_id=office_id)

    bob_client = app_ctx.test_client()
    _login(bob_client, "bob")
    bob_task = bob_client.post("/tools/to_bell/api/tasks", json={"title": "Bobだけのタスク"}).get_json()

    alice_client = app_ctx.test_client()
    _login(alice_client, "alice")
    project = alice_client.post("/tools/to_bell/api/projects", json={"name": "Alice集計"}).get_json()

    # Bob のタスクは Alice からは見えない → 紐づけられない（updated=0）
    assigned = alice_client.post(
        f"/tools/to_bell/api/projects/{project['id']}/assign-tasks",
        json={"task_ids": [bob_task["id"]]},
    )
    assert assigned.status_code == 200
    assert assigned.get_json()["updated"] == 0

    # Bob 側のタスクは元のまま（プロジェクト未設定）
    detail = bob_client.get(f"/tools/to_bell/api/tasks/{bob_task['id']}").get_json()
    assert detail["project"] is None
