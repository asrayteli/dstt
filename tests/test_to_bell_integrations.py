from __future__ import annotations

import sys
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


def _create_user(app_ctx, username: str):
    from app.models import User, db

    with app_ctx.app_context():
        db.session.add(User(username=username, password_hash="hash", name=username.title()))
        db.session.commit()


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def test_integrations_default_all_off(app_ctx):
    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    resp = client.get("/tools/to_bell/api/settings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["integrations"]
    assert all(value is False for value in data["integrations"].values())
    # カタログには全ての連携キーが含まれる
    keys = {item["key"] for item in data["catalog"]}
    assert "cloudshift.leave_change_request" in keys
    assert "cloudshift.shift_update" in keys
    assert "filepost.attachment_overflow" in keys
    assert "filepost.project_files" in keys
    assert "pluslist.linkage" in keys
    assert "siteplus.linkage" in keys


def test_toggle_persists_per_user(app_ctx):
    _create_user(app_ctx, "alice")
    _create_user(app_ctx, "bob")

    alice = app_ctx.test_client()
    _login(alice, "alice")
    alice.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"pluslist.linkage": True, "cloudshift.shift_update": True}},
    )

    bob = app_ctx.test_client()
    _login(bob, "bob")
    bob_settings = bob.get("/tools/to_bell/api/settings").get_json()
    # 他人の設定に影響されない
    assert bob_settings["integrations"]["pluslist.linkage"] is False
    assert bob_settings["integrations"]["cloudshift.shift_update"] is False

    alice_settings = alice.get("/tools/to_bell/api/settings").get_json()
    assert alice_settings["integrations"]["pluslist.linkage"] is True
    assert alice_settings["integrations"]["cloudshift.shift_update"] is True


def test_link_apis_require_per_feature_permission(app_ctx):
    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    # pluslist.linkage が OFF の状態
    resp = client.get("/tools/to_bell/api/links/employees?target_type=task&target_id=1")
    assert resp.status_code == 403
    resp = client.get("/tools/to_bell/api/links/sites?target_type=task&target_id=1")
    assert resp.status_code == 403


def test_employee_link_lifecycle(app_ctx):
    from app.models import Employee, db

    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")
    client.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"pluslist.linkage": True}},
    )

    with app_ctx.app_context():
        emp = Employee(employee_number="E001", office_code="X-OFFICE", employee_name="山田太郎")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

    task = client.post("/tools/to_bell/api/tasks", json={"title": "T"}).get_json()

    add = client.post(
        "/tools/to_bell/api/links/employees",
        json={"target_type": "task", "target_id": task["id"], "employee_id": emp_id},
    )
    assert add.status_code == 201
    link_id = add.get_json()["link_id"]

    listing = client.get(f"/tools/to_bell/api/links/employees?target_type=task&target_id={task['id']}").get_json()
    assert len(listing["links"]) == 1
    assert listing["links"][0]["employee_name"] == "山田太郎"

    # 重複登録しても件数は増えない（UNIQUE 制約）
    again = client.post(
        "/tools/to_bell/api/links/employees",
        json={"target_type": "task", "target_id": task["id"], "employee_id": emp_id},
    )
    assert again.status_code == 201
    listing2 = client.get(f"/tools/to_bell/api/links/employees?target_type=task&target_id={task['id']}").get_json()
    assert len(listing2["links"]) == 1

    delete = client.delete(f"/tools/to_bell/api/links/employees/{link_id}")
    assert delete.status_code == 200


def test_cloudshift_leave_change_request_hook_only_notifies_opted_in(app_ctx):
    from app.models import ToBellNotification, ToBellTask
    from app.services.to_bell_hooks import on_cloudshift_leave_change_request
    from app.services.to_bell_integrations import update_integrations

    _create_user(app_ctx, "alice")
    _create_user(app_ctx, "bob")

    with app_ctx.app_context():
        update_integrations("alice", {"integrations": {"cloudshift.leave_change_request": True}})
        # bob は OFF のまま

        on_cloudshift_leave_change_request(
            request_payload={
                "id": "req-1",
                "day": 10,
                "month_key": "2026-05",
                "old_leave_type": "有給",
                "requested_leave_type": "代休",
                "request_comment": "",
            },
            project_title="テスト現場",
            project_id="p1",
            audience=["alice", "bob"],
        )

        assert ToBellTask.query.filter_by(created_by="alice", source_tool="cloudshift").count() == 1
        assert ToBellTask.query.filter_by(created_by="bob", source_tool="cloudshift").count() == 0
        # 通知も alice にだけ届く
        assert ToBellNotification.query.filter_by(user_id="alice", event_type="leave_change_request").count() == 1
        assert ToBellNotification.query.filter_by(user_id="bob").count() == 0

        # 同じ申請の再フックでは重複作成されない
        on_cloudshift_leave_change_request(
            request_payload={
                "id": "req-1",
                "day": 10,
                "month_key": "2026-05",
                "old_leave_type": "有給",
                "requested_leave_type": "代休",
                "request_comment": "",
            },
            project_title="テスト現場",
            project_id="p1",
            audience=["alice"],
        )
        assert ToBellTask.query.filter_by(created_by="alice", source_tool="cloudshift").count() == 1


def test_cloudshift_substitute_update_hook_broadcasts_to_enabled_users(app_ctx):
    from app.models import ToBellTask
    from app.services.to_bell_hooks import on_cloudshift_substitute_updated
    from app.services.to_bell_integrations import update_integrations

    _create_user(app_ctx, "alice")
    _create_user(app_ctx, "bob")
    _create_user(app_ctx, "carol")

    with app_ctx.app_context():
        update_integrations("alice", {"integrations": {"cloudshift.shift_update": True}})
        update_integrations("bob", {"integrations": {"cloudshift.shift_update": True}})
        # carol は OFF

        on_cloudshift_substitute_updated(
            substitute_project_id="sub-A",
            substitute_project_title="A営業所 要代務シフト帳",
            month_key="2026-05",
            day_key="20",
            action="substitute_request_created",
        )

        assert ToBellTask.query.filter_by(created_by="alice").count() == 1
        assert ToBellTask.query.filter_by(created_by="bob").count() == 1
        assert ToBellTask.query.filter_by(created_by="carol").count() == 0


def test_save_project_fires_substitute_hook_for_all_update_paths(app_ctx):
    """`_save_project()` レベルで一元的にフックしているため、
    どのルート (代務要請ルート / 汎用月更新ルート / ドラフトpublish 等) でも
    要代務プロジェクトの保存はフックを発火させる。"""
    from app.models import ToBellTask
    from app.services.to_bell_integrations import update_integrations
    from app.tools.cloudshift import (
        SUBSTITUTE_MODE, SUBSTITUTE_TITLE,
        _build_month_payload, _save_project, _share_token, _site_storage_fields,
        _substitute_project_id, _utcnow_iso,
    )

    _create_user(app_ctx, "alice")
    with app_ctx.app_context():
        update_integrations("alice", {"integrations": {"cloudshift.shift_update": True}})
        pid = _substitute_project_id(1)
        project = {
            "id": pid,
            "owner_user_id": "",
            "title": SUBSTITUTE_TITLE,
            "mode": SUBSTITUTE_MODE,
            "employee_number": "",
            **_site_storage_fields(None),
            "master_target_type": "",
            "master_people": [],
            "master_sites": [],
            "substitute_office_id": 1,
            "view_token": _share_token(),
            "edit_token": _share_token(),
            "account_shares": {"office": {"enabled": True, "office_ids": [1]}, "employees": [], "updated_at": _utcnow_iso(), "updated_by": "system"},
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "months": {"2026-05": _build_month_payload(2026, 5, False, 0, {
                "15": [{"id": "e1", "value": "代務", "employee_name": "山田",
                        "site_name": "現場A", "substitute_request_type": "person"}]
            })},
        }

    # POSTリクエストコンテキストで _save_project を呼ぶ (= 任意のmutation routeを模擬)
    with app_ctx.test_request_context("/", method="POST"):
        _save_project(project)

    with app_ctx.app_context():
        tasks = ToBellTask.query.filter_by(
            source_tool="cloudshift", source_ref_type="substitute_update"
        ).all()
        assert len(tasks) == 1
        assert tasks[0].assigned_to == "alice"


def test_save_project_during_get_does_not_fire_substitute_hook(app_ctx):
    """GET (= 初期化系) では誤発火しない。"""
    from app.models import ToBellTask
    from app.services.to_bell_integrations import update_integrations
    from app.tools.cloudshift import (
        SUBSTITUTE_MODE, SUBSTITUTE_TITLE,
        _build_month_payload, _save_project, _share_token, _site_storage_fields,
        _substitute_project_id, _utcnow_iso,
    )

    _create_user(app_ctx, "alice")
    with app_ctx.app_context():
        update_integrations("alice", {"integrations": {"cloudshift.shift_update": True}})
        pid = _substitute_project_id(2)
        project = {
            "id": pid,
            "owner_user_id": "",
            "title": SUBSTITUTE_TITLE,
            "mode": SUBSTITUTE_MODE,
            "employee_number": "",
            **_site_storage_fields(None),
            "master_target_type": "",
            "master_people": [],
            "master_sites": [],
            "substitute_office_id": 2,
            "view_token": _share_token(),
            "edit_token": _share_token(),
            "account_shares": {"office": {"enabled": True, "office_ids": [2]}, "employees": [], "updated_at": _utcnow_iso(), "updated_by": "system"},
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "months": {"2026-05": _build_month_payload(2026, 5, False, 0, {})},
        }

    with app_ctx.test_request_context("/api/list", method="GET"):
        _save_project(project)

    with app_ctx.app_context():
        assert ToBellTask.query.filter_by(source_tool="cloudshift").count() == 0


def test_filepost_threshold_endpoint_reports_feature_state(app_ctx):
    _create_user(app_ctx, "alice")
    client = app_ctx.test_client()
    _login(client, "alice")

    resp = client.get("/tools/to_bell/api/integrations/filepost/threshold")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["threshold_bytes"] == 25 * 1024 * 1024
    assert data["overflow_enabled"] is False
    assert data["project_files_enabled"] is False

    client.put(
        "/tools/to_bell/api/settings/integrations",
        json={"integrations": {"filepost.attachment_overflow": True}},
    )
    data2 = client.get("/tools/to_bell/api/integrations/filepost/threshold").get_json()
    assert data2["overflow_enabled"] is True
    assert data2["project_files_enabled"] is False


def test_substitute_save_with_changes_creates_detailed_memo_and_pushes(app_ctx):
    """要代務シフト帳に新規シフト追加 →
    (1) メモに「何月何日: 誰/どこ」が記載される
    (2) 即時プッシュ送信が走る
    """
    from unittest.mock import patch
    from app.models import AccessBranch, AccessOffice, User, ToBellTask, db
    from app.access_control import grant_tool_access
    from app.tools.cloudshift import _ensure_substitute_project_for_office_month, _substitute_project_id

    with app_ctx.app_context():
        branch = AccessBranch(name="b", code="B")
        db.session.add(branch); db.session.flush()
        office = AccessOffice(id=99, branch_id=branch.id, name="o", code="O99")
        db.session.add(office)
        u = User(username="alice", password_hash="x", name="Alice", office_id=99)
        db.session.add(u); db.session.commit()
        grant_tool_access(u.id, ["shiftersync"], "system")

    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = "alice"; s["_fresh"] = True
    c.put("/tools/to_bell/api/settings/integrations",
          json={"integrations": {"cloudshift.shift_update": True}})

    with app_ctx.app_context():
        with app_ctx.test_request_context("/api/list", method="GET"):
            _ensure_substitute_project_for_office_month(office_id=99, year=2026, month=5)
        pid = _substitute_project_id(99)

    push_calls = []
    def fake_push(user_id, *, title, body, url):
        push_calls.append({"user": user_id, "title": title, "body": body, "url": url})
        return {"sent": 1, "failed": 0}

    with patch("app.services.to_bell_push.send_push_to_user", side_effect=fake_push):
        resp = c.put(f"/tools/shiftersync/cloudshift/api/project/{pid}/month/2026/5", json={
            "capacity_enabled": False, "required_capacity": 0,
            "entries_per_day": {
                "15": [{"id": "r1", "value": "代務要請", "employee_name": "山田太郎",
                        "site_name": "現場A", "substitute_request_type": "person"}],
                "20": [{"id": "r2", "value": "代務要請", "site_name": "現場B",
                        "substitute_request_type": "scene", "substitute_unassigned_helper": True}],
            },
        })
        assert resp.status_code == 200

    with app_ctx.app_context():
        tasks = ToBellTask.query.filter_by(source_tool="cloudshift",
                                           source_ref_type="substitute_update",
                                           assigned_to="alice").all()
        assert len(tasks) == 1
        memo = tasks[0].description
        assert "5月15日" in memo and "山田太郎" in memo and "現場A" in memo
        assert "5月20日" in memo and "現場B" in memo and "代務者未設定" in memo

    # 即時プッシュが1回送られている
    assert len(push_calls) == 1
    assert push_calls[0]["user"] == "alice"
    assert "山田太郎" in push_calls[0]["body"]


def test_substitute_save_with_no_changes_does_not_push(app_ctx):
    """差分なし保存ではプッシュもタスクも増えない。"""
    from unittest.mock import patch
    from app.models import AccessBranch, AccessOffice, User, ToBellTask, db
    from app.access_control import grant_tool_access
    from app.tools.cloudshift import _ensure_substitute_project_for_office_month, _substitute_project_id

    with app_ctx.app_context():
        branch = AccessBranch(name="b", code="Bx")
        db.session.add(branch); db.session.flush()
        office = AccessOffice(id=88, branch_id=branch.id, name="o", code="O88")
        db.session.add(office)
        u = User(username="alice", password_hash="x", name="Alice", office_id=88)
        db.session.add(u); db.session.commit()
        grant_tool_access(u.id, ["shiftersync"], "system")

    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = "alice"; s["_fresh"] = True
    c.put("/tools/to_bell/api/settings/integrations",
          json={"integrations": {"cloudshift.shift_update": True}})

    with app_ctx.app_context():
        with app_ctx.test_request_context("/api/list", method="GET"):
            _ensure_substitute_project_for_office_month(office_id=88, year=2026, month=5)
        pid = _substitute_project_id(88)

    payload = {
        "capacity_enabled": False, "required_capacity": 0,
        "entries_per_day": {"10": [{"id": "x1", "value": "代務",
                                    "employee_name": "佐藤", "site_name": "現場X",
                                    "substitute_request_type": "person"}]},
    }
    push_calls = []
    def fake_push(user_id, *, title, body, url):
        push_calls.append(user_id); return {"sent": 1, "failed": 0}

    with patch("app.services.to_bell_push.send_push_to_user", side_effect=fake_push):
        c.put(f"/tools/shiftersync/cloudshift/api/project/{pid}/month/2026/5", json=payload)
        c.put(f"/tools/shiftersync/cloudshift/api/project/{pid}/month/2026/5", json=payload)

    with app_ctx.app_context():
        assert ToBellTask.query.filter_by(source_tool="cloudshift").count() == 1
    # 1回目だけ
    assert len(push_calls) == 1
