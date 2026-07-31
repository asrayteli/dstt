"""CloudShift「シフト帳のオーナー変更」（所有権の移譲）のテスト。

担当の異動などで持ち主が変わったときに、削除→再作成せずに所有権だけを
別の DSTT アカウントへ移せること、元オーナーへの自動共有を選べることを確認する。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import importlib.util

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))

from app.models import (
    AccessBranch,
    AccessOffice,
    CloudShiftProject,
    CloudShiftProjectVisibility,
    CloudShiftTemplate,
    Site,
    User,
    db,
)


API = "/tools/shiftersync/cloudshift/api"


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_owner_transfer_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(tmp_path):
    module = _load_cloudshift_module()
    app = Flask(__name__, root_path=str(tmp_path), instance_path=str(tmp_path / "instance"))
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'cloudshift.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.cloudshift_bp)
    with app.app_context():
        db.create_all()
        db.session.add(User(username="owner01", password_hash="x", name="Old Owner"))
        db.session.add(User(username="2001", password_hash="x", name="New Owner"))
        db.session.commit()
    return module, app.test_client()


def _owner():
    return SimpleNamespace(is_authenticated=True, username="owner01", name="Old Owner", id=1)


def _new_owner():
    return SimpleNamespace(is_authenticated=True, username="2001", name="New Owner", id=2)


def _outsider():
    return SimpleNamespace(is_authenticated=True, username="9999", name="Outsider", id=3)


def _create_person_project(client, *, employee_number="1001", title="Person A"):
    response = client.post(
        f"{API}/create",
        data={
            "title": title,
            "mode": "person",
            "employee_number": employee_number,
            "year": "2026",
            "month": "4",
        },
    )
    assert response.status_code == 200
    return response.get_json()["project"]["project"]["id"]


def test_owner_transfer_hands_over_project_and_shares_back_to_previous_owner(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)

    response = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": True},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["owner"]["user_id"] == "2001"
    assert payload["shared_with_previous_owner"] is True
    assert [item["employee_number"] for item in payload["shares"]["employees"]] == ["owner01"]

    # 新オーナーはオーナーとして一覧に出て、月を保存できる。
    module.current_user = _new_owner()
    listed = client.get(f"{API}/list").get_json()["projects"]
    assert [item["id"] for item in listed] == [project_id]
    assert listed[0]["access_role"] == "owner"

    detail = client.get(f"{API}/project/{project_id}").get_json()
    assert detail["access_role"] == "owner"
    month = detail["month"]
    saved = client.put(
        f"{API}/project/{project_id}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": month["entries_per_day"], "base_month": month},
    )
    assert saved.status_code == 200

    # 元オーナーは閲覧共有として残る（編集はできない）。
    module.current_user = _owner()
    listed = client.get(f"{API}/list").get_json()["projects"]
    assert [item["id"] for item in listed] == [project_id]
    assert listed[0]["access_role"] == "viewer"
    rejected = client.put(
        f"{API}/project/{project_id}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": month["entries_per_day"], "base_month": month},
    )
    assert rejected.status_code == 404
    # オーナーではなくなったので、再度の移譲もできない。
    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "owner01", "share_with_previous_owner": False},
    ).status_code == 404


def test_owner_transfer_without_share_back_removes_project_from_previous_owner(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)

    response = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    )
    assert response.status_code == 200
    assert response.get_json()["shares"]["employees"] == []

    module.current_user = _owner()
    assert client.get(f"{API}/list").get_json()["projects"] == []
    assert client.get(f"{API}/project/{project_id}").status_code == 404

    module.current_user = _new_owner()
    assert [item["id"] for item in client.get(f"{API}/list").get_json()["projects"]] == [project_id]


def test_owner_transfer_keeps_months_history_and_templates(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)

    template_response = client.post(
        f"{API}/project/{project_id}/templates",
        json={
            "name": "Weekly",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "日勤"}]},
        },
    )
    assert template_response.status_code == 200
    template_id = template_response.get_json()["template"]["id"]

    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    ).status_code == 200

    with client.application.app_context():
        assert db.session.get(CloudShiftTemplate, template_id).owner_user_id == "2001"

    module.current_user = _new_owner()
    detail = client.get(f"{API}/project/{project_id}").get_json()
    assert detail["project"]["month_keys"] == ["2026-04"]
    assert detail["project"]["urls"]["view_url"]
    assert client.get(f"{API}/project/{project_id}/templates").get_json()["templates"][0]["id"] == template_id

    actions = [item["action"] for item in client.get(f"{API}/project/{project_id}/history").get_json()["history"]]
    assert "project_created" in actions
    assert "project_owner_transferred" in actions


def test_owner_transfer_rejects_unknown_account_and_current_owner(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)

    missing = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "8888", "share_with_previous_owner": True},
    )
    assert missing.status_code == 404
    assert "DSTTアカウントが見つかりません" in missing.get_json()["error"]

    myself = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "owner01", "share_with_previous_owner": True},
    )
    assert myself.status_code == 400

    empty = client.put(f"{API}/project/{project_id}/owner", json={"share_with_previous_owner": True})
    assert empty.status_code == 400

    # オーナーは変わっていない。
    assert client.get(f"{API}/project/{project_id}/owner").get_json()["owner"]["user_id"] == "owner01"


def test_owner_transfer_candidate_preview_reports_blockers_before_submitting(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)
    assert client.put(
        f"{API}/project/{project_id}/account-shares",
        json={"share_office": False, "employee_numbers": ["2001"]},
    ).status_code == 200

    info = client.get(f"{API}/project/{project_id}/owner").get_json()
    assert info["can_transfer"] is True
    assert info["owner"]["user_id"] == "owner01"
    assert info["candidate"] is None

    ok = client.get(f"{API}/project/{project_id}/owner?employee_number=2001").get_json()["candidate"]
    assert ok["error"] == ""
    assert ok["has_account"] is True
    # 既に共有先の相手でも移譲できる（オーナーになると共有先からは外れる）。
    assert ok["already_shared"] is True

    unknown = client.get(f"{API}/project/{project_id}/owner?employee_number=8888").get_json()["candidate"]
    assert unknown["has_account"] is False
    assert "DSTTアカウントが見つかりません" in unknown["error"]

    myself = client.get(f"{API}/project/{project_id}/owner?employee_number=owner01").get_json()["candidate"]
    assert myself["is_current_owner"] is True
    assert myself["error"]


def test_owner_transfer_rejected_when_new_owner_already_owns_same_target(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)

    # 同じ社員に紐づくシフト帳を移譲先が既に持っている状態を作る（レガシーの重複）。
    with client.application.app_context():
        db.session.add(
            CloudShiftProject(
                id="dup000000000000000001",
                owner_user_id="2001",
                title="Person A (dup)",
                mode="person",
                employee_number="1001",
                view_token="dup-view-token",
                edit_token="dup-edit-token",
                account_shares={},
                assist={},
                extra_data={},
                created_at="2026-04-01T00:00:00+09:00",
                updated_at="2026-04-01T00:00:00+09:00",
            )
        )
        db.session.commit()

    preview = client.get(f"{API}/project/{project_id}/owner?employee_number=2001").get_json()["candidate"]
    assert preview["duplicate_project"]["title"] == "Person A (dup)"
    assert "既に所持しています" in preview["error"]

    blocked = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": True},
    )
    assert blocked.status_code == 409

    with client.application.app_context():
        assert db.session.get(CloudShiftProject, project_id).owner_user_id == "owner01"


def test_owner_transfer_unhides_project_for_the_new_owner(tmp_path):
    """新オーナーが共有時に一覧から非表示にしていても、移譲後は一覧に出る。"""
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)
    assert client.put(
        f"{API}/project/{project_id}/account-shares",
        json={"share_office": False, "employee_numbers": ["2001"]},
    ).status_code == 200

    module.current_user = _new_owner()
    assert client.post(
        f"{API}/project/{project_id}/visibility", json={"hidden": True}
    ).status_code == 200
    assert client.get(f"{API}/list").get_json()["projects"] == []

    module.current_user = _owner()
    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    ).status_code == 200

    module.current_user = _new_owner()
    assert [item["id"] for item in client.get(f"{API}/list").get_json()["projects"]] == [project_id]
    with client.application.app_context():
        assert CloudShiftProjectVisibility.query.filter_by(
            user_id="2001", project_id=project_id
        ).first() is None


def test_owner_transfer_keeps_office_share_of_the_previous_owner(tmp_path):
    """営業所共有は移譲前の営業所のまま維持する（共有先が突然見えなくならない）。"""
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        branch = AccessBranch(name="Tokyo", code="TK")
        db.session.add(branch)
        db.session.flush()
        old_office = AccessOffice(branch_id=branch.id, name="Shinjuku", code="S01")
        new_office = AccessOffice(branch_id=branch.id, name="Shibuya", code="S02")
        db.session.add_all([old_office, new_office])
        db.session.commit()
        old_office_id, new_office_id = old_office.id, new_office.id

    owner = _owner()
    owner.office_id = old_office_id
    module.current_user = owner
    project_id = _create_person_project(client)
    assert client.put(
        f"{API}/project/{project_id}/account-shares",
        json={"share_office": True, "employee_numbers": []},
    ).status_code == 200

    new_owner = _new_owner()
    new_owner.office_id = new_office_id
    module.current_user = owner
    transferred = client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    )
    assert transferred.status_code == 200
    office_share = transferred.get_json()["shares"]["office"]
    assert office_share["enabled"] is True
    assert office_share["office_ids"] == [old_office_id]

    # 元の営業所のメンバーは引き続き閲覧できる。
    module.current_user = SimpleNamespace(
        is_authenticated=True, username="3001", name="Same Office", office_id=old_office_id, id=4
    )
    # 一覧には営業所ごとの要代務シフト帳も並ぶため、対象の帳簿だけを取り出して確認する。
    shared = client.get(f"{API}/list").get_json()["projects"]
    target = next(item for item in shared if item["id"] == project_id)
    assert target["access_role"] == "viewer"


def test_owner_transfer_freezes_created_office_of_the_previous_owner(tmp_path):
    """作成元営業所を持たないレガシー帳簿は、移譲時に元オーナー基準で確定させる。

    確定しないと、スポット検索の営業所スコープが移譲先の営業所へ移ってしまう。
    """
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        branch = AccessBranch(name="Tokyo", code="TK")
        db.session.add(branch)
        db.session.flush()
        old_office = AccessOffice(branch_id=branch.id, name="Shinjuku", code="S01")
        new_office = AccessOffice(branch_id=branch.id, name="Shibuya", code="S02")
        db.session.add_all([old_office, new_office])
        db.session.flush()
        old_office_id, new_office_id = old_office.id, new_office.id
        User.query.filter_by(username="owner01").first().office_id = old_office_id
        User.query.filter_by(username="2001").first().office_id = new_office_id
        db.session.commit()

    owner = _owner()
    owner.office_id = old_office_id
    module.current_user = owner
    project_id = _create_person_project(client)

    # 作成元営業所を持たないレガシー状態を再現する。
    with client.application.app_context():
        row = db.session.get(CloudShiftProject, project_id)
        extra = dict(row.extra_data or {})
        extra.pop("created_office_ids", None)
        row.extra_data = extra
        db.session.commit()

    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    ).status_code == 200

    with client.application.app_context():
        row = db.session.get(CloudShiftProject, project_id)
        assert row.owner_user_id == "2001"
        assert row.extra_data["created_office_ids"] == [old_office_id]


def test_scene_shift_owner_transfer_keeps_site_link_and_blocks_duplicate_owner(tmp_path):
    """現場シフトは現場（site_id）で紐づけを判定する。移譲後も現場設定は保たれる。"""
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        site = Site(
            site_id="S010",
            site_name="Shinjuku Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9010",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    module.current_user = _owner()
    project_id = client.post(
        f"{API}/create",
        data={
            "title": "Shinjuku Scene",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    ).get_json()["project"]["project"]["id"]

    # 同じ現場のシフト帳を移譲先が既に持っていれば拒否される。
    with client.application.app_context():
        db.session.add(
            CloudShiftProject(
                id="dup000000000000000002",
                owner_user_id="2001",
                title="Shinjuku Scene (dup)",
                mode="scene",
                employee_number="",
                site_row_id=site_row_id,
                site_id="S010",
                site_name="Shinjuku Site",
                view_token="dup-scene-view",
                edit_token="dup-scene-edit",
                account_shares={},
                assist={},
                extra_data={},
                created_at="2026-04-01T00:00:00+09:00",
                updated_at="2026-04-01T00:00:00+09:00",
            )
        )
        db.session.commit()
    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": True},
    ).status_code == 409

    # 重複を解消すれば移譲でき、現場の紐づけはそのまま残る。
    with client.application.app_context():
        db.session.delete(db.session.get(CloudShiftProject, "dup000000000000000002"))
        db.session.commit()
    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": True},
    ).status_code == 200

    module.current_user = _new_owner()
    site_payload = client.get(f"{API}/project/{project_id}").get_json()["project"]["site"]
    assert site_payload["site_id"] == "S010"
    assert site_payload["site_name"] == "Shinjuku Site"


def test_owner_transfer_is_owner_only(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_person_project(client)
    assert client.put(
        f"{API}/project/{project_id}/account-shares",
        json={"share_office": False, "employee_numbers": ["2001"]},
    ).status_code == 200

    # 共有されているだけの相手も、無関係のユーザーも操作できない。
    module.current_user = _new_owner()
    assert client.get(f"{API}/project/{project_id}/owner").status_code == 404
    assert client.put(
        f"{API}/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": False},
    ).status_code == 404

    module.current_user = _outsider()
    assert client.get(f"{API}/project/{project_id}/owner").status_code == 404

    with client.application.app_context():
        assert db.session.get(CloudShiftProject, project_id).owner_user_id == "owner01"
