"""バグ修正・仕様変更の回帰テスト。

対象:
  * 期日を指定しないタスクは「追加した日の終日」になる（仕様変更）
  * プロジェクトを開いた状態での追加は、そのプロジェクトのタスクになる（仕様変更）
  * 終日タスクを詳細から無変更保存しても終日のまま（通知がONに化けない）
  * 一括削除の LIKE ワイルドカード
  * sensitive ツール（pluslist / siteplus / CloudShift）への到達
  * 共有リンクの操作範囲
  * プロジェクト並び順の書き換え権限
  * 連携タスクの自動削除と、カレンダー表示
  * 添付ファイル実体の後始末
  * Googleカレンダー取り込み（範囲変更時のフル再取得）
"""
from __future__ import annotations

import io
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

    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'to_bell_hardening.db'}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "TO_BELL_ATTACHMENT_DIR": str(tmp_path / "attachments"),
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def _office(app_ctx, name: str) -> int:
    from app.models import AccessBranch, AccessOffice, db

    with app_ctx.app_context():
        branch = AccessBranch(name=f"{name}支店", code=f"{name}-b")
        db.session.add(branch)
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name=f"{name}営業所", code=f"{name}-o")
        db.session.add(office)
        db.session.commit()
        return office.id


def _user(app_ctx, username: str, *, office_id: int | None = None, tools: tuple[str, ...] = ()):
    from app.models import User, UserToolPermission, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name=username, office_id=office_id)
        db.session.add(user)
        db.session.flush()
        for tool_key in tools:
            db.session.add(UserToolPermission(user_id=user.id, tool_key=tool_key))
        db.session.commit()


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _client(app_ctx, username: str):
    client = app_ctx.test_client()
    _login(client, username)
    return client


# ---------------------------------------------------------------- 仕様変更

def test_task_without_due_date_becomes_all_day_task_for_today(app_ctx):
    from app.services.to_bell_service import local_today

    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")

    created = client.post("/tools/to_bell/api/tasks", json={"title": "期日なしで追加"}).get_json()
    assert created["due_at"] == f"{local_today().isoformat()}T23:59:59"
    assert created["due_all_day"] is True

    # 終日なので通知（プッシュ／前面通知）の対象にはならない。
    due = client.get("/tools/to_bell/api/notifications/due-tasks").get_json()["tasks"]
    assert all(task["id"] != created["id"] for task in due)

    # カレンダービューには当日のタスクとして並ぶ。
    calendar = client.get("/tools/to_bell/api/tasks?filter=board&view=calendar").get_json()["tasks"]
    assert created["id"] in [task["id"] for task in calendar]


def test_template_instantiation_also_gets_today_all_day_due(app_ctx):
    from app.services.to_bell_service import local_today

    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    template = client.post("/tools/to_bell/api/templates", json={"name": "定型作業"}).get_json()

    task = client.post(f"/tools/to_bell/api/templates/{template['id']}/instantiate", json={}).get_json()
    assert task["due_at"] == f"{local_today().isoformat()}T23:59:59"


def test_due_date_can_still_be_cleared_to_reach_inbox(app_ctx):
    """既定で期日が入っても、消せば従来どおり受信箱へ移せる。"""
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post("/tools/to_bell/api/tasks", json={"title": "あとで整理"}).get_json()

    cleared = client.put(f"/tools/to_bell/api/tasks/{task['id']}", json={"due_at": ""}).get_json()
    assert cleared["due_at"] is None

    inbox = client.get("/tools/to_bell/api/tasks?filter=inbox").get_json()["tasks"]
    assert task["id"] in [row["id"] for row in inbox]


def test_task_added_while_project_selected_belongs_to_that_project(app_ctx):
    """プロジェクトを開いた状態での追加は、そのプロジェクトのタスクになる。"""
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    project = client.post("/tools/to_bell/api/projects", json={"name": "月次作業"}).get_json()

    # 画面が絞り込み中に送る project_id をそのまま検証する。
    task = client.post(
        "/tools/to_bell/api/tasks",
        json={"title": "請求書の確認", "project_id": project["id"]},
    ).get_json()
    assert task["project_id"] == project["id"]
    assert task["project"]["name"] == "月次作業"

    listing = client.get(f"/tools/to_bell/api/tasks?project_id={project['id']}").get_json()["tasks"]
    assert task["id"] in [row["id"] for row in listing]


def test_template_instantiation_uses_selected_project(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    project = client.post("/tools/to_bell/api/projects", json={"name": "車検更新対応"}).get_json()
    template = client.post("/tools/to_bell/api/templates", json={"name": "点検手配"}).get_json()

    task = client.post(
        f"/tools/to_bell/api/templates/{template['id']}/instantiate",
        json={"project_id": project["id"]},
    ).get_json()
    assert task["project_id"] == project["id"]


# ---------------------------------------------------------------- 終日の維持

def test_all_day_task_survives_unchanged_detail_save(app_ctx):
    """詳細パネルを開いてそのまま保存しても、終日タスクのままにする。

    datetime-local は秒を持てないため、23:59:59 が 23:59:00 に落ちて
    「時刻指定あり＝通知する」タスクに化ける不具合の回帰テスト。
    """
    from app.models import ToBellTask, db
    from app.services.to_bell_service import has_explicit_notification_time

    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post(
        "/tools/to_bell/api/tasks", json={"title": "日付だけ", "due_date": "2026-08-20"}
    ).get_json()
    assert task["due_all_day"] is True

    # 画面が送る値（datetime-local の値 + 終日チェック）をそのまま再現する。
    saved = client.put(
        f"/tools/to_bell/api/tasks/{task['id']}",
        json={"title": "日付だけ", "due_at": task["due_at"][:16], "due_all_day": True},
    ).get_json()
    assert saved["due_at"] == "2026-08-20T23:59:59"
    assert saved["due_all_day"] is True

    with app_ctx.app_context():
        assert has_explicit_notification_time(db.session.get(ToBellTask, task["id"])) is False


def test_all_day_survives_a_client_that_omits_the_flag(app_ctx):
    """古いキャッシュの JS など、終日フラグを送らないクライアントでも化けさせない。"""
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post(
        "/tools/to_bell/api/tasks", json={"title": "日付だけ", "due_date": "2026-08-20"}
    ).get_json()

    saved = client.put(
        f"/tools/to_bell/api/tasks/{task['id']}", json={"due_at": "2026-08-20T23:59"}
    ).get_json()
    assert saved["due_at"] == "2026-08-20T23:59:59"
    assert saved["due_all_day"] is True


def test_explicit_2359_on_a_new_day_is_kept_as_a_real_time(app_ctx):
    """別の日の 23:59 を指定した場合は、そのまま時刻指定として扱う。"""
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post(
        "/tools/to_bell/api/tasks", json={"title": "日付だけ", "due_date": "2026-08-20"}
    ).get_json()

    saved = client.put(
        f"/tools/to_bell/api/tasks/{task['id']}", json={"due_at": "2026-08-25T23:59"}
    ).get_json()
    assert saved["due_at"] == "2026-08-25T23:59:00"
    assert saved["due_all_day"] is False


def test_notification_link_can_open_an_integration_task(app_ctx):
    """連携タスクは通常フィルタに出さないが、通知リンク（?task=<id>）からは開ける。"""
    from app.models import ToBellTask, db

    _user(app_ctx, "alice")
    with app_ctx.app_context():
        task = ToBellTask(
            title="健診リマインド",
            status="todo",
            priority="normal",
            due_at=datetime(2026, 8, 20, 9, 0),
            created_by="alice",
            assigned_to="alice",
            source_tool="health_check",
            source_ref_type="reservation",
            source_ref_id="1:alice",
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    client = _client(app_ctx, "alice")
    assert client.get("/tools/to_bell/api/tasks?filter=today").get_json()["tasks"] == []
    detail = client.get(f"/tools/to_bell/api/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.get_json()["title"] == "健診リマインド"


def test_unchecking_all_day_sets_an_explicit_time(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post(
        "/tools/to_bell/api/tasks", json={"title": "時刻を付ける", "due_date": "2026-08-20"}
    ).get_json()

    saved = client.put(
        f"/tools/to_bell/api/tasks/{task['id']}",
        json={"due_at": "2026-08-20T09:30", "due_all_day": False},
    ).get_json()
    assert saved["due_at"] == "2026-08-20T09:30:00"
    assert saved["due_all_day"] is False


# ---------------------------------------------------------------- 一括削除

def test_bulk_delete_treats_like_wildcards_literally(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    for title in ("請求確認", "月次作業", "車検更新"):
        client.post("/tools/to_bell/api/tasks", json={"title": title})
    client.post("/tools/to_bell/api/tasks", json={"title": "100%完了"})

    preview = client.get("/tools/to_bell/api/tasks/bulk-delete?name=%25&match=contains").get_json()
    assert preview["count"] == 1  # 「100%完了」だけ。全件一致しない。
    assert preview["samples"] == ["100%完了"]

    deleted = client.post(
        "/tools/to_bell/api/tasks/bulk-delete", json={"name": "%", "match": "contains"}
    ).get_json()
    assert deleted["deleted"] == 1
    assert client.get("/tools/to_bell/api/tasks?filter=today").get_json()["tasks"]


# ---------------------------------------------------------------- 権限

def test_pluslist_master_needs_pluslist_tool_access(app_ctx):
    """ToBell の連携トグルだけでは sensitive ツールの名簿に到達できない。"""
    from app.models import Employee, db

    _user(app_ctx, "nobody")
    _user(app_ctx, "staff", tools=("pluslist",))
    with app_ctx.app_context():
        db.session.add(Employee(employee_number="0001", office_code="001", employee_name="山田太郎"))
        db.session.commit()

    outsider = _client(app_ctx, "nobody")
    outsider.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"pluslist.linkage": True}},
    )
    # 自分でトグルを押しても有効にならない。
    settings = outsider.get("/tools/to_bell/api/settings").get_json()
    assert settings["integrations"]["pluslist.linkage"] is False
    catalog = {item["key"]: item for item in settings["catalog"]}
    assert catalog["pluslist.linkage"]["available"] is False
    assert catalog["pluslist.linkage"]["requires_tool"] == "pluslist"
    assert outsider.get("/tools/to_bell/api/links/employees/search?q=山田").status_code == 403

    # pluslist のアクセス権があれば従来どおり使える。
    allowed = _client(app_ctx, "staff")
    allowed.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"pluslist.linkage": True}},
    )
    found = allowed.get("/tools/to_bell/api/links/employees/search?q=山田").get_json()
    assert [row["employee_name"] for row in found["results"]] == ["山田太郎"]


def test_cloudshift_broadcast_skips_users_without_shiftersync_access(app_ctx):
    """CloudShift 由来の通知は、ShifterSync を使える人にだけ配る。"""
    from app.models import ToBellTask
    from app.services.to_bell_hooks import on_cloudshift_leave_change_request
    from app.services.to_bell_integrations import update_integrations

    _user(app_ctx, "shiftuser", tools=("shiftersync",))
    _user(app_ctx, "outsider")
    with app_ctx.app_context():
        for name in ("shiftuser", "outsider"):
            update_integrations(name, {"integrations": {"cloudshift.leave_change_request": True}})
        on_cloudshift_leave_change_request(
            request_payload={"id": "r1", "day": "15", "month_key": "2026-05"},
            project_title="5月シフト",
            project_id="p1",
        )
        owners = {task.created_by for task in ToBellTask.query.filter_by(source_tool="cloudshift").all()}
    assert owners == {"shiftuser"}


def test_project_order_is_owned_by_its_creator(app_ctx):
    from app.models import ToBellProject, db

    office_id = _office(app_ctx, "A")
    _user(app_ctx, "owner", office_id=office_id)
    _user(app_ctx, "member", office_id=office_id)

    owner = _client(app_ctx, "owner")
    ids = [
        owner.post(
            "/tools/to_bell/api/projects", json={"name": name, "visibility_scope": "office"}
        ).get_json()["id"]
        for name in ("A", "B", "C")
    ]

    member = _client(app_ctx, "member")
    result = member.post(
        "/tools/to_bell/api/projects/reorder", json={"ordered_ids": list(reversed(ids))}
    ).get_json()
    assert result["updated"] == 0

    with app_ctx.app_context():
        assert all(db.session.get(ToBellProject, pid).sort_order == 0 for pid in ids)

    # 作成者は並べ替えられる。
    assert owner.post(
        "/tools/to_bell/api/projects/reorder", json={"ordered_ids": list(reversed(ids))}
    ).get_json()["updated"] == 2


def test_client_cannot_forge_an_integration_task(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    created = client.post(
        "/tools/to_bell/api/tasks",
        json={
            "title": "偽の連携タスク",
            "source_tool": "cloudshift",
            "source_ref_type": "leave_change_request",
            "source_ref_id": "x:1",
        },
    ).get_json()
    assert created["source_tool"] == ""
    assert created["source_ref_id"] == ""


# ---------------------------------------------------------------- 共有リンク

def _share_client(app_ctx, owner: str):
    owner_client = _client(app_ctx, owner)
    url = owner_client.post("/tools/to_bell/api/share/issue").get_json()["url"]
    token = url.rsplit("/", 1)[-1]
    guest = app_ctx.test_client()
    assert guest.get(f"/tools/to_bell/s/{token}").status_code == 302
    return owner_client, guest


def test_share_link_cannot_reach_outside_to_bell(app_ctx):
    _user(app_ctx, "alice", tools=("pluslist", "siteplus"))
    owner, guest = _share_client(app_ctx, "alice")
    owner.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"pluslist.linkage": True, "siteplus.linkage": True}},
    )
    project = owner.post("/tools/to_bell/api/projects", json={"name": "月次"}).get_json()
    task = owner.post("/tools/to_bell/api/tasks", json={"title": "共有前タスク"}).get_json()

    assert guest.get("/tools/to_bell/api/links/employees/search?q=a").status_code == 403
    assert guest.get("/tools/to_bell/api/links/sites/search?q=a").status_code == 403
    assert guest.post(f"/tools/to_bell/api/projects/{project['id']}/notify", json={"title": "x"}).status_code == 403
    assert guest.delete(f"/tools/to_bell/api/tasks/{task['id']}?hard=1").status_code == 403
    assert guest.post(f"/tools/to_bell/api/tasks/{task['id']}/calendar", json={}).status_code == 403
    assert guest.get("/tools/to_bell/api/push/subscriptions").status_code == 403
    assert guest.post(
        "/tools/to_bell/api/tasks/bulk-delete", json={"name": "共有前タスク"}
    ).status_code == 403


def test_share_link_can_still_do_everyday_task_work(app_ctx):
    _user(app_ctx, "alice")
    owner, guest = _share_client(app_ctx, "alice")

    created = guest.post("/tools/to_bell/api/tasks", json={"title": "外出先で追加"})
    assert created.status_code == 201
    task_id = created.get_json()["id"]
    assert guest.post(f"/tools/to_bell/api/tasks/{task_id}/complete").status_code == 200
    assert guest.post(
        f"/tools/to_bell/api/tasks/{task_id}/comments", json={"body": "対応しました"}
    ).status_code == 201
    assert guest.delete(f"/tools/to_bell/api/tasks/{task_id}").status_code == 200  # アーカイブは可


# ---------------------------------------------------------------- 表示の整合

def test_calendar_only_project_tasks_are_excluded_from_the_badge(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    project = client.post(
        "/tools/to_bell/api/projects",
        json={"name": "カレンダー専用", "calendar_only": True, "visibility_scope": "private"},
    ).get_json()
    client.post("/tools/to_bell/api/tasks", json={"title": "隠れタスク", "project_id": project["id"]})

    data = client.get("/tools/to_bell/api/tasks?filter=attention&view=list").get_json()
    assert data["tasks"] == []
    assert data["summary"]["action_count"] == 0


def test_integration_tasks_appear_on_the_calendar_view(app_ctx):
    """取り込んだ予定・連携タスクはカレンダーに並ぶ（リスト・カンバンには出さない）。"""
    from app.models import ToBellTask, db

    _user(app_ctx, "alice")
    with app_ctx.app_context():
        db.session.add(
            ToBellTask(
                title="取り込んだ予定",
                status="todo",
                priority="normal",
                due_at=datetime(2026, 9, 1, 10, 0),
                created_by="alice",
                assigned_to="alice",
                source_tool="google",
                source_ref_type="calendar_event",
                source_ref_id="alice:e1",
            )
        )
        db.session.commit()

    client = _client(app_ctx, "alice")
    titles = lambda qs: [t["title"] for t in client.get(f"/tools/to_bell/api/tasks?{qs}").get_json()["tasks"]]
    assert titles("filter=board&view=calendar") == ["取り込んだ予定"]
    assert titles("filter=integrations&view=list") == ["取り込んだ予定"]
    assert titles("filter=board&view=kanban") == []
    assert titles("filter=today&view=list") == []


def test_archived_tasks_have_a_place_to_come_back_from(app_ctx):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post("/tools/to_bell/api/tasks", json={"title": "しまっておく"}).get_json()
    assert client.delete(f"/tools/to_bell/api/tasks/{task['id']}").status_code == 200

    archived = client.get("/tools/to_bell/api/tasks?filter=archived").get_json()["tasks"]
    assert [row["id"] for row in archived] == [task["id"]]

    client.post(f"/tools/to_bell/api/tasks/{task['id']}/reopen")
    assert client.get("/tools/to_bell/api/tasks?filter=archived").get_json()["tasks"] == []


def test_overdue_filter_uses_the_current_time(app_ctx):
    from app.models import ToBellTask, db
    from app.services.local_time import local_now

    _user(app_ctx, "alice")
    now = local_now()
    with app_ctx.app_context():
        db.session.add(
            ToBellTask(
                title="今朝までのタスク",
                status="todo",
                priority="normal",
                due_at=now - timedelta(hours=2),
                created_by="alice",
                assigned_to="alice",
            )
        )
        db.session.commit()

    client = _client(app_ctx, "alice")
    overdue = client.get("/tools/to_bell/api/tasks?filter=overdue").get_json()["tasks"]
    assert [row["title"] for row in overdue] == ["今朝までのタスク"]


def test_task_less_notifications_clear_the_action_badge(app_ctx):
    office_id = _office(app_ctx, "B")
    _user(app_ctx, "alice", office_id=office_id)
    _user(app_ctx, "bob", office_id=office_id)

    alice = _client(app_ctx, "alice")
    project = alice.post(
        "/tools/to_bell/api/projects", json={"name": "月次", "visibility_scope": "office"}
    ).get_json()
    assert alice.post(
        f"/tools/to_bell/api/projects/{project['id']}/notify", json={"title": "確認お願いします"}
    ).get_json()["sent"] == 1

    bob = _client(app_ctx, "bob")
    assert bob.get("/tools/to_bell/api/tasks?filter=attention").get_json()["summary"]["action_count"] == 1
    bob.post("/tools/to_bell/api/notifications/read-all")
    summary = bob.get("/tools/to_bell/api/tasks?filter=attention").get_json()["summary"]
    assert summary["action_count"] == 0
    assert summary["unread_count"] == 0


# ---------------------------------------------------------------- 後始末

def test_deleting_a_task_removes_its_attachment_files(app_ctx, tmp_path):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post("/tools/to_bell/api/tasks", json={"title": "添付付き"}).get_json()
    assert client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/attachments",
        data={"file": (io.BytesIO(b"hello"), "note.txt")},
        content_type="multipart/form-data",
    ).status_code == 201

    directory = tmp_path / "attachments"
    assert list(directory.iterdir())
    assert client.delete(f"/tools/to_bell/api/tasks/{task['id']}?hard=1").status_code == 200
    assert list(directory.iterdir()) == []


def test_bulk_delete_removes_attachment_files_too(app_ctx, tmp_path):
    _user(app_ctx, "alice")
    client = _client(app_ctx, "alice")
    task = client.post("/tools/to_bell/api/tasks", json={"title": "掃除対象"}).get_json()
    client.post(
        f"/tools/to_bell/api/tasks/{task['id']}/attachments",
        data={"file": (io.BytesIO(b"x" * 32), "a.bin")},
        content_type="multipart/form-data",
    )
    directory = tmp_path / "attachments"
    assert list(directory.iterdir())

    assert client.post(
        "/tools/to_bell/api/tasks/bulk-delete", json={"name": "掃除対象"}
    ).get_json()["deleted"] == 1
    assert list(directory.iterdir()) == []


def test_future_integration_tasks_survive_the_retention_sweep(app_ctx):
    """まだ先の予定は 30 日ルールで消さない（差分同期では復活しないため）。"""
    from app.models import ToBellTask, db, utc_now
    from app.services.to_bell_service import cleanup_expired_records

    _user(app_ctx, "alice")
    with app_ctx.app_context():
        future = ToBellTask(
            title="2か月先の会議",
            status="todo",
            priority="normal",
            due_at=utc_now() + timedelta(days=60),
            created_by="alice",
            assigned_to="alice",
            source_tool="google",
            source_ref_type="calendar_event",
            source_ref_id="alice:future",
            created_at=utc_now() - timedelta(days=31),
        )
        past = ToBellTask(
            title="先月の会議",
            status="todo",
            priority="normal",
            due_at=utc_now() - timedelta(days=35),
            created_by="alice",
            assigned_to="alice",
            source_tool="google",
            source_ref_type="calendar_event",
            source_ref_id="alice:past",
            created_at=utc_now() - timedelta(days=40),
        )
        db.session.add_all([future, past])
        db.session.commit()
        future_id, past_id = future.id, past.id

        cleanup_expired_records()
        assert db.session.get(ToBellTask, future_id) is not None
        assert db.session.get(ToBellTask, past_id) is None


# ---------------------------------------------------------------- カレンダー同期

def test_archiving_a_synced_task_clears_the_calendar_link(app_ctx, monkeypatch):
    from app.models import ToBellGoogleAccount, ToBellTask, db, utc_now
    from app.services import to_bell_calendar

    _user(app_ctx, "alice")
    calls = []
    with app_ctx.app_context():
        db.session.add(
            ToBellGoogleAccount(
                username="alice",
                refresh_token="r",
                access_token="a",
                token_expiry=utc_now() + timedelta(hours=1),
            )
        )
        task = ToBellTask(
            title="会議",
            status="todo",
            priority="normal",
            due_at=datetime(2026, 9, 1, 10, 0),
            created_by="alice",
            assigned_to="alice",
            gcal_event_id="evt-1",
            gcal_synced_by="alice",
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    monkeypatch.setattr(
        to_bell_calendar,
        "_calendar_request",
        lambda method, path, account, json=None, allow_missing=False: calls.append(method) or {},
    )
    client = _client(app_ctx, "alice")
    assert client.delete(f"/tools/to_bell/api/tasks/{task_id}").status_code == 200

    with app_ctx.app_context():
        task = db.session.get(ToBellTask, task_id)
        assert calls == ["DELETE"]
        assert task.gcal_event_id is None
        assert task.gcal_synced_by is None


def test_patching_a_vanished_event_is_not_reported_as_success(app_ctx):
    """404 を成功扱いにすると「送ったつもりで実体が無い」状態が黙って続く。"""
    from app.services.to_bell_calendar import ToBellCalendarEventMissing, _calendar_request

    class _Resp:
        status_code = 404
        text = ""

        def json(self):
            return {}

    import app.services.to_bell_calendar as cal

    class _Account:
        username = "alice"
        refresh_token = "r"
        access_token = "a"
        token_expiry = datetime.max

    original = cal.requests.request
    cal.requests.request = lambda *a, **kw: _Resp()
    try:
        with pytest.raises(ToBellCalendarEventMissing):
            _calendar_request("PATCH", "/calendars/primary/events/x", _Account())
        assert _calendar_request("DELETE", "/calendars/primary/events/x", _Account(), allow_missing=True) == {}
    finally:
        cal.requests.request = original
