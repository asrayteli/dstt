import sys
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import importlib.util

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))

from app.models import AccessBranch, AccessOffice, Site, SiteBranch, SiteContractMaster, User, db


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_test_module", module_path)
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
    return module, app.test_client()


def _owner():
    return SimpleNamespace(is_authenticated=True, username="owner01", name="Owner User")


def _other_owner():
    return SimpleNamespace(is_authenticated=True, username="owner02", name="Other Owner")


def _guest():
    return SimpleNamespace(is_authenticated=False)


def _employee_user(username="2001", *, office_id=None, name="Shared User"):
    return SimpleNamespace(is_authenticated=True, username=username, name=name, office_id=office_id, id=999)


def _token_from_url(value: str) -> str:
    return Path(urlparse(value).path).name


def _legacy_base_month(month: dict) -> dict:
    return {
        "year": month["year"],
        "month": month["month"],
        "required_capacity": month.get("required_capacity", 0),
        "entries_per_day": month.get("entries_per_day", {}),
    }


def _prepare_leave_mgr_data(tmp_path, user_id, *, accessible_calendar_ids):
    base = tmp_path / "static" / "leave_mgr"
    calendars_dir = base / "calendars"
    calendars_dir.mkdir(parents=True, exist_ok=True)
    (base / "permissions.json").write_text(
        json.dumps(
            {
                "admins": [],
                "user_calendars": {
                    user_id: accessible_calendar_ids,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (base / "calendar_meta.json").write_text(
        json.dumps(
            {
                calendar_id: {"name": f"{calendar_id} Office"}
                for calendar_id in accessible_calendar_ids
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_leave_calendar(tmp_path, calendar_id, year_month):
    path = tmp_path / "static" / "leave_mgr" / "calendars" / f"{calendar_id}_{year_month}.json"
    if not path.exists():
        return {"leaves": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _create_legacy_cloudshift_tables(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE cloudshift_projects (
                id VARCHAR(24) PRIMARY KEY,
                owner_user_id VARCHAR(80) NOT NULL,
                title VARCHAR(200) NOT NULL,
                mode VARCHAR(20) NOT NULL,
                employee_number VARCHAR(40) NOT NULL DEFAULT '',
                site_row_id INTEGER,
                site_id VARCHAR(20),
                site_name VARCHAR(200),
                view_token VARCHAR(128) NOT NULL,
                edit_token VARCHAR(128) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            );
            CREATE TABLE cloudshift_months (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id VARCHAR(24) NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                capacity_enabled BOOLEAN NOT NULL DEFAULT 0,
                required_capacity INTEGER NOT NULL DEFAULT 0,
                entries_per_day JSON NOT NULL DEFAULT '{}',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            );
            CREATE TABLE cloudshift_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id VARCHAR(24) NOT NULL,
                timestamp VARCHAR(32) NOT NULL,
                editor_name VARCHAR(100) NOT NULL DEFAULT '',
                editor_type VARCHAR(30) NOT NULL DEFAULT '',
                action VARCHAR(60) NOT NULL DEFAULT '',
                month_key VARCHAR(7)
            );
            """
        )


def test_owner_can_create_and_public_view_can_read(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Tokyo Team",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "required_capacity": "2",
        },
    )

    assert response.status_code == 200
    project_payload = response.get_json()["project"]
    assert project_payload["project"]["title"] == "Tokyo Team"
    assert project_payload["month"]["required_capacity"] == 2

    view_token = _token_from_url(project_payload["project"]["urls"]["view_url"])
    public_response = client.get(f"/tools/shiftersync/cloudshift/api/public/view/{view_token}")

    assert public_response.status_code == 200
    public_data = public_response.get_json()
    assert public_data["project"]["title"] == "Tokyo Team"
    assert public_data["month"]["year"] == 2026
    assert public_data["month"]["month"] == 4
    assert "edit_url" not in public_data["project"]["urls"]


def test_owner_draft_is_hidden_from_public_until_published(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Draft Team", "mode": "scene", "year": "2026", "month": "4"},
    )
    project_payload = create_response.get_json()["project"]
    project_id = project_payload["project"]["id"]
    view_token = _token_from_url(project_payload["project"]["urls"]["view_url"])

    draft_entries = dict(project_payload["month"]["entries_per_day"])
    draft_entries["1"] = [{"id": "draft-1", "value": "!A!Alice", "comment": "tentative"}]
    draft_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4/draft",
        json={"entries_per_day": draft_entries},
    )
    assert draft_response.status_code == 200
    owner_month = draft_response.get_json()["month"]
    assert owner_month["draft_entries_per_day"]["1"][0]["value"] == "!A!Alice"

    public_response = client.get(f"/tools/shiftersync/cloudshift/api/public/view/{view_token}")
    assert public_response.status_code == 200
    public_month = public_response.get_json()["month"]
    assert "draft_entries_per_day" not in public_month
    assert public_month["entries_per_day"]["1"] == []

    publish_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4/draft/publish",
        json={},
    )
    assert publish_response.status_code == 200
    published_month = publish_response.get_json()["month"]
    assert published_month["entries_per_day"]["1"][0]["value"] == "!A!Alice"
    assert published_month["draft_entries_per_day"]["1"][0]["value"] == "!A!Alice"

    public_after_publish = client.get(f"/tools/shiftersync/cloudshift/api/public/view/{view_token}")
    assert public_after_publish.get_json()["month"]["entries_per_day"]["1"][0]["value"] == "!A!Alice"


def test_draft_mode_starts_from_current_shift_and_publish_replaces_official_month(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Replace Draft", "mode": "scene", "year": "2026", "month": "5"},
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]
    official_entries = dict(month["entries_per_day"])
    official_entries["1"] = [{"id": "official-1", "value": "!A!Official", "comment": ""}]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5",
        json={"required_capacity": 0, "entries_per_day": official_entries, "base_month": month},
    )
    assert save_response.status_code == 200
    saved_month = save_response.get_json()["month"]
    assert saved_month["draft_entries_per_day"]["1"][0]["value"] == "!A!Official"

    draft_entries = dict(saved_month["draft_entries_per_day"])
    draft_entries["1"] = []
    draft_entries["2"] = [{"id": "draft-2", "value": "!P!Draft", "comment": ""}]
    draft_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5/draft",
        json={"entries_per_day": draft_entries},
    )
    assert draft_response.status_code == 200
    assert draft_response.get_json()["month"]["entries_per_day"]["1"][0]["value"] == "!A!Official"

    publish_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5/draft/publish",
        json={},
    )
    assert publish_response.status_code == 200
    published_month = publish_response.get_json()["month"]
    assert published_month["entries_per_day"]["1"] == []
    assert published_month["entries_per_day"]["2"][0]["value"] == "!P!Draft"
    assert published_month["draft_entries_per_day"] == published_month["entries_per_day"]


def test_json_cloudshift_project_can_be_migrated_to_db_without_deleting_source(tmp_path):
    module, client = _build_client(tmp_path)
    project = {
        "id": "jsonproj01",
        "owner_user_id": "owner01",
        "title": "Legacy JSON",
        "mode": "scene",
        "employee_number": "",
        "site_row_id": None,
        "site_id": "",
        "site_name": "",
        "view_token": "view-token",
        "edit_token": "edit-token",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "months": {
            "2026-04": {
                "year": 2026,
                "month": 4,
                "capacity_enabled": False,
                "required_capacity": 0,
                "entries_per_day": {"1": [{"id": "legacy-1", "value": "!A!Legacy", "comment": ""}]},
                "revision": 1,
                "created_at": "2026-04-01T00:00:00Z",
                "updated_at": "2026-04-01T00:00:00Z",
                "revision_snapshots": {},
            }
        },
    }
    shifts_dir = tmp_path / "instance" / "cloudshift" / "shifts"
    shifts_dir.mkdir(parents=True, exist_ok=True)
    source_path = shifts_dir / "jsonproj01.json"
    source_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with client.application.app_context():
        result = module.migrate_cloudshift_json_to_db()

    assert result["projects_imported"] == 1
    assert result["errors"] == []
    assert source_path.exists()

    module.current_user = _owner()
    detail_response = client.get("/tools/shiftersync/cloudshift/api/project/jsonproj01")
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail["project"]["title"] == "Legacy JSON"
    assert detail["month"]["entries_per_day"]["1"][0]["value"] == "!A!Legacy"


def test_account_share_specific_employee_can_view_but_not_edit(tmp_path):
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        db.session.add(User(username="2001", password_hash="x", name="Shared Employee"))
        db.session.commit()

    module.current_user = _owner()
    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Private Person", "mode": "person", "employee_number": "1001", "year": "2026", "month": "4"},
    )
    project_payload = create_response.get_json()["project"]
    project_id = project_payload["project"]["id"]

    share_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/account-shares",
        json={"share_office": False, "employee_numbers": ["2001"]},
    )
    assert share_response.status_code == 200

    module.current_user = _employee_user("2001")
    list_response = client.get("/tools/shiftersync/cloudshift/api/list")
    assert list_response.status_code == 200
    projects = list_response.get_json()["projects"]
    assert projects[0]["access_role"] == "viewer"
    assert projects[0]["share"]["role"] == "viewer"

    detail_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}")
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail["project"]["access_role"] == "viewer"
    assert detail["project"]["urls"] == {}

    month = detail["month"]
    edit_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": month["entries_per_day"], "base_month": month},
    )
    assert edit_response.status_code == 404


def test_account_share_same_office_lists_shared_project(tmp_path):
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        branch = AccessBranch(name="Tokyo", code="TK")
        db.session.add(branch)
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name="Shinjuku", code="S01")
        db.session.add(office)
        db.session.commit()
        office_id = office.id

    owner = _owner()
    owner.office_id = office_id
    owner.id = 100
    module.current_user = owner
    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Office Scene", "mode": "scene", "year": "2026", "month": "5"},
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    share_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/account-shares",
        json={"share_office": True, "employee_numbers": []},
    )
    assert share_response.status_code == 200

    module.current_user = _employee_user("3001", office_id=office_id)
    list_response = client.get("/tools/shiftersync/cloudshift/api/list")
    assert list_response.status_code == 200
    projects = list_response.get_json()["projects"]
    assert [project["id"] for project in projects] == [project_id]
    assert projects[0]["access_role"] == "viewer"


def test_master_shift_syncs_to_person_and_scene_projects(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="S001",
            site_name="Master Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9001",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    person = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Alice", "mode": "person", "employee_number": "1001", "year": "2026", "month": "4"},
    ).get_json()["project"]
    scene = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Master Site",
            "mode": "scene",
            "site_row_id": str(site_row_id),
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    master = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "April Master",
            "mode": "master",
            "master_target_type": "person",
            "master_people": json.dumps([{"employee_number": "1001", "name": "Alice"}]),
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]

    master_id = master["project"]["id"]
    master_entries = dict(master["month"]["entries_per_day"])
    master_entries["1"] = [
        {
            "id": "master-1",
            "value": "!A!Master Site",
            "comment": "from master",
            "employee_name": "Alice",
            "employee_number": "1001",
            "site_row_id": str(site_row_id),
            "site_id": "S001",
            "site_name": "Master Site",
        }
    ]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{master_id}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": master_entries, "base_month": master["month"]},
    )
    assert save_response.status_code == 200

    person_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person['project']['id']}",
        query_string={"month_key": "2026-04"},
    ).get_json()
    person_entry = person_detail["month"]["entries_per_day"]["1"][0]
    assert person_entry["value"] == "!A!Master Site"
    assert person_entry["site_row_id"] == str(site_row_id)
    assert person_entry["sync_source_type"] == "master_shift"

    scene_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene['project']['id']}",
        query_string={"month_key": "2026-04"},
    ).get_json()
    scene_entry = scene_detail["month"]["entries_per_day"]["1"][0]
    assert scene_entry["value"] == "!A!Alice"
    assert scene_entry["employee_name"] == "Alice"
    assert scene_entry["employee_number"] == "1001"
    assert scene_entry["sync_source_type"] == "master_shift"


def test_scene_master_create_recovers_legacy_cloudshift_schema(tmp_path):
    module = _load_cloudshift_module()
    db_path = tmp_path / "legacy-cloudshift.db"
    _create_legacy_cloudshift_tables(db_path)

    app = Flask(__name__, root_path=str(tmp_path), instance_path=str(tmp_path / "instance"))
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path.as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.cloudshift_bp)
    module.current_user = _owner()

    with app.app_context():
        db.create_all()
        site = Site(
            site_id="S900",
            site_name="Production Legacy Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9900",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    client = app.test_client()
    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Legacy Scene Master",
            "mode": "master",
            "master_target_type": "scene",
            "master_sites": json.dumps(
                [{"site_row_id": str(site_row_id), "site_id": "S900", "site_name": "Production Legacy Site"}]
            ),
            "year": "2026",
            "month": "4",
        },
    )
    assert create_response.status_code == 200

    project_id = create_response.get_json()["project"]["project"]["id"]
    detail_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}")
    assert detail_response.status_code == 200
    project = detail_response.get_json()["project"]
    assert project["mode"] == "master"
    assert project["master"]["target_type"] == "scene"
    assert project["master"]["site_count"] == 1


def test_cloudshift_site_link_uses_latest_site_record(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="S010",
            site_name="Old Site",
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

    person = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Alice", "mode": "person", "employee_number": "1001", "year": "2026", "month": "4"},
    ).get_json()["project"]
    project_id = person["project"]["id"]
    entries = dict(person["month"]["entries_per_day"])
    entries["1"] = [
        {
            "id": "person-1",
            "value": "!A!Old Site",
            "site_row_id": str(site_row_id),
            "site_id": "S010",
            "site_name": "Old Site",
        }
    ]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": entries, "base_month": person["month"]},
    )
    assert save_response.status_code == 200

    with client.application.app_context():
        site = db.session.get(Site, site_row_id)
        site.site_id = "S011"
        site.site_name = "New Site"
        db.session.commit()

    detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}",
        query_string={"month_key": "2026-04"},
    ).get_json()
    entry = detail["month"]["entries_per_day"]["1"][0]
    assert entry["site_row_id"] == str(site_row_id)
    assert entry["site_id"] == "S011"
    assert entry["site_name"] == "New Site"
    assert entry["value"] == "!A!New Site"


def test_person_master_sync_uses_latest_site_link(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="S020",
            site_name="Old Master Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9020",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    person = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Alice", "mode": "person", "employee_number": "1001", "year": "2026", "month": "4"},
    ).get_json()["project"]
    master = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "April Master",
            "mode": "master",
            "master_target_type": "person",
            "master_people": json.dumps([{"employee_number": "1001", "name": "Alice"}]),
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]

    with client.application.app_context():
        site = db.session.get(Site, site_row_id)
        site.site_id = "S021"
        site.site_name = "New Master Site"
        db.session.commit()

    person_entries = dict(person["month"]["entries_per_day"])
    person_entries["1"] = [
        {
            "id": "person-1",
            "value": "!A!Old Master Site",
            "site_row_id": str(site_row_id),
            "site_id": "S020",
            "site_name": "Old Master Site",
        }
    ]
    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person['project']['id']}/month/2026/4",
        json={"required_capacity": 0, "entries_per_day": person_entries, "base_month": person["month"]},
    )
    assert save_response.status_code == 200

    master_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{master['project']['id']}",
        query_string={"month_key": "2026-04"},
    ).get_json()
    master_entry = master_detail["month"]["entries_per_day"]["1"][0]
    assert master_entry["value"] == "!A!New Master Site"
    assert master_entry["employee_name"] == "Alice"
    assert master_entry["site_id"] == "S021"
    assert master_entry["site_name"] == "New Master Site"


def test_master_shift_rejects_mixed_people_and_sites(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="S002",
            site_name="Mixed Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9002",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Mixed Master",
            "mode": "master",
            "master_target_type": "person",
            "master_people": json.dumps([{"employee_number": "1001", "name": "Alice"}]),
            "master_sites": json.dumps([{"site_row_id": str(site_row_id), "site_id": "S002", "site_name": "Mixed Site"}]),
            "year": "2026",
            "month": "4",
        },
    )

    assert response.status_code == 400
    assert "個人マスターには現場を登録できません" in response.get_json()["error"]


def test_master_shift_infers_person_target_when_type_missing(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Inferred Master",
            "mode": "master",
            "master_people": json.dumps([{"employee_number": "1001", "name": "Alice"}]),
            "year": "2026",
            "month": "4",
        },
    )

    assert response.status_code == 200
    master = response.get_json()["project"]["project"]["master"]
    assert master["target_type"] == "person"
    assert master["people_count"] == 1


def test_master_shift_targets_can_be_edited_later(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="S003",
            site_name="Editable Site",
            site_manager_last="Owner",
            site_manager_first="Manager",
            site_manager_id="9003",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Editable Master",
            "mode": "master",
            "master_target_type": "person",
            "master_people": json.dumps([{"employee_number": "1001", "name": "Alice"}]),
            "year": "2026",
            "month": "4",
        },
    )
    assert create_response.status_code == 200
    project_id = create_response.get_json()["project"]["project"]["id"]

    update_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/meta",
        json={
            "title": "Editable Master",
            "master_target_type": "scene",
            "master_people": "",
            "master_sites": json.dumps([{"site_row_id": str(site_row_id), "site_id": "S003", "site_name": "Editable Site"}]),
        },
    )

    assert update_response.status_code == 200
    master = update_response.get_json()["project"]["project"]["master"]
    assert master["target_type"] == "scene"
    assert master["site_count"] == 1
    assert master["people_count"] == 0


def test_owner_can_compare_own_cloudshift_projects_for_conflicts(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    alpha = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Alpha Team",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    beta = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Beta Team",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]

    alpha_id = alpha["project"]["id"]
    beta_id = beta["project"]["id"]

    alpha_entries = dict(alpha["month"]["entries_per_day"])
    alpha_entries["1"] = [{"id": "alpha-1", "value": "!A!Alice", "comment": ""}]
    beta_entries = dict(beta["month"]["entries_per_day"])
    beta_entries["1"] = [{"id": "beta-1", "value": "!E!Alice", "comment": ""}]

    alpha_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{alpha_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "entries_per_day": alpha_entries,
            "base_month": alpha["month"],
        },
    )
    beta_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{beta_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "entries_per_day": beta_entries,
            "base_month": beta["month"],
        },
    )

    assert alpha_save.status_code == 200
    assert beta_save.status_code == 200

    compare_response = client.post(
        "/tools/shiftersync/cloudshift/api/conflict-check",
        json={
            "month_key": "2026-04",
            "project_ids": [alpha_id, beta_id],
        },
    )

    assert compare_response.status_code == 200
    payload = compare_response.get_json()
    assert payload["success"] is True
    assert payload["mode"] == "scene"
    assert payload["month_key"] == "2026-04"
    assert payload["targets"] == ["Alpha Team", "Beta Team"]
    assert [item["label"] for item in payload["sources"]] == ["Alpha Team", "Beta Team"]
    assert {item["entry"] for item in payload["conflicts"]} == {"!A!Alice", "!E!Alice"}
    assert payload["same_site_conflicts"] == []
    assert payload["matrix"]["1"][0][0]["display"] == "Alice 午前"
    assert payload["matrix"]["1"][1][0]["display"] == "Alice 早番"


def test_cloudshift_template_exposes_bulk_direct_date_selection_ui():
    script = (ROOT / "app" / "templates" / "_cloudshift_script.html").read_text(encoding="utf-8")
    style = (ROOT / "app" / "templates" / "_cloudshift_style.html").read_text(encoding="utf-8")
    html = (ROOT / "app" / "templates" / "cloudshift.html").read_text(encoding="utf-8")
    ss_common_js = (ROOT / "app" / "static" / "shiftersync" / "js" / "ss_common.js").read_text(encoding="utf-8")
    ss_common_css = (ROOT / "app" / "static" / "shiftersync" / "css" / "ss_common.css").read_text(encoding="utf-8")

    assert 'data-cloud-tab="conflict-check"' in html
    assert 'id="cloud-check-mode"' in html
    assert 'id="cloud-check-month"' in html
    assert 'id="cloud-check-run"' in html
    assert 'value="picked"' in script
    assert "日付を選んで選択" in script
    assert "openBulkEditModal().catch" not in script
    assert "openBulkEditModal();" in script
    assert "cloud-assist-filter-grid" in script
    assert "cloud-assist-switch" in script
    assert "data-assist-edit-list" in script
    assert 'data-assist-tab="details"' in script
    assert "custom_points" in script
    assert "day-assist-search" in script
    assert "他の候補者を探す" in script
    assert "最有力候補" in script
    assert "data-day-assist-top-add" in script
    assert "data-day-assist-record" in script
    assert "buildAssistDetailsPanel" in script
    assert "buildAssistSceneConflictNotice" in script
    assert "buildAssistDayTopCandidateCard" in script
    assert "openAssistDayActionModal" in script
    assert "data-assist-add-search-result-index" in script
    assert "window.CLOUDSHIFT_CAN_OPEN_SYNC_SOURCE" in script
    assert "window.CLOUDSHIFT_OPEN_SYNC_SOURCE" in script
    assert "openSyncedSourceEntry(entry)" in script
    assert "ShifterSync.openEntryModal(sourceDay, sourceEntryId)" in script
    assert "syncReturnContext" in script
    assert "maybeReturnToSyncOrigin" in script
    assert "selectProject(sourceProjectId, sourceMonthKey, { preserveSyncReturnContext: true })" in script
    assert "に戻りました" in script
    assert "ss-open-sync-source-btn" in ss_common_js
    assert "反映元を編集" in ss_common_js
    assert "openEntryModal" in ss_common_js
    assert "function notifyAssistAction" in script
    assert "function closeAssistRelatedModals" in script
    assert "preferred_weekdays" in script
    assert "blocked_weekdays" in script
    assert "has_scene_conflict" in script
    assert "scene_conflict_site_names" in script
    assert "data-assist-edit-profile" in script
    assert "option_aptitude" in script
    assert "data-assist-detail-toggle" in script
    assert "detailSections" in script
    assert "オプションなし" in script
    assert "曜日なし" in script
    assert "function bindAssistEditListEvents" in script
    assert "bindAssistEditListEvents(modal);" in script
    assert ".cloud-assist-filter-grid" in style
    assert ".cloud-assist-switch" in style
    assert ".cloud-assist-detail-grid" in style
    assert ".cloud-assist-detail-accordion" in style
    assert ".cloud-assist-weekday-picker" in style
    assert '.cloud-assist-panel[data-assist-panel="details"] .cloud-assist-section' in style
    assert '.cloud-assist-panel[data-assist-panel="edit"] .cloud-assist-stack' in style
    assert "@media (max-height: 900px)" in style
    assert "[data-assist-edit-list]" in style
    assert "`${base}/sites`" in script
    assert "data-day-assist-site-type" in script
    assert 'name="bulk_site_branch_row_id"' in script
    assert "data-day-assist-training-pick" in script
    assert "site_type" in script
    assert ".cloud-assist-result-card.is-conflict" in style
    assert ".cloud-assist-conflict-note" in style
    assert "day-assist-trigger" in ss_common_js
    assert "getSelectedOptionsForDay" in ss_common_js
    assert "function updateBranchWarning" in ss_common_js
    assert "day-branch-warning" in ss_common_js
    assert "return { code: 'unassigned'" not in ss_common_js
    assert "TEMP" in ss_common_js
    assert "TEMP" in script
    assert "warnings.missing.length" not in script
    assert "臨時便" in script
    assert "function resetCreateForm()" in script
    assert "if (tabName === 'create')" in script
    assert "if (tabName === 'conflict-check')" in script
    assert "function runConflictCheck()" in script
    assert "/tools/shiftersync/cloudshift/api/conflict-check" in script
    assert "cloud-check-entry" in script
    assert "resetCreateForm();" in script
    assert ".cloud-check-project-list" in style
    assert ".cloud-check-scroll" in style
    assert ".cloud-check-header-row" in style
    assert "overflow: visible;" in style
    assert ".cloud-check-entry.is-conflict" in style
    assert ".assist-btn" in ss_common_css
    assert "entry-drag-handle" in ss_common_js
    assert "function moveEntry" in ss_common_js
    assert ".entry-item.is-drop-before" in ss_common_css
    assert ".day-box.branch-warning" in ss_common_css
    assert ".entry-item-status.is-warning" in ss_common_css


def test_public_edit_save_writes_history_and_exports_comment(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Indoor Band",
            "mode": "person",
            "employee_number": "1001",
            "year": "2026",
            "month": "5",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    edit_token = _token_from_url(payload["project"]["urls"]["edit_url"])
    month = payload["month"]

    month_entries = dict(month["entries_per_day"])
    month_entries["1"] = [
        {"id": "entry-1", "value": "!A!Alice", "comment": "Morning only", "employee_number": "1001"},
    ]
    month_entries["2"] = [
        {"id": "entry-2", "value": "Hall-A", "comment": ""},
    ]

    module.current_user = _guest()
    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/month/2026/5",
        json={
            "editor_name": "Guest Editor",
            "required_capacity": 3,
            "entries_per_day": month_entries,
            "base_month": month,
        },
    )

    assert save_response.status_code == 200
    saved_payload = save_response.get_json()["project"]
    assert saved_payload["month"]["required_capacity"] == 3
    assert saved_payload["month"]["entries_per_day"]["1"][0]["value"] == "!A!Alice"
    assert saved_payload["month"]["entries_per_day"]["1"][0]["comment"] == "Morning only"

    module.current_user = _owner()
    history_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/history")
    assert history_response.status_code == 200
    history = history_response.get_json()["history"]
    assert history
    assert any(item["editor_name"] == "Guest Editor" for item in history)

    csv_export = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/export/csv",
        query_string={"month_key": "2026-05"},
    )
    assert csv_export.status_code == 200
    assert csv_export.content_type.startswith("text/csv")
    assert b"#comment,1,0,Morning only" in csv_export.data
    assert b"#employee_number,1,0,1001" in csv_export.data
    assert b"#project_employee_number,1001" in csv_export.data

    xlsx_export = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/export/xlsx",
        query_string={"month_key": "2026-05"},
    )
    assert xlsx_export.status_code == 200
    assert xlsx_export.content_type.startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(xlsx_export.data) > 100

    calendar_png_export = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/export/calendar_png",
        query_string={"month_key": "2026-05"},
    )
    assert calendar_png_export.status_code == 200
    assert calendar_png_export.content_type.startswith("image/png")
    assert len(calendar_png_export.data) > 100


def test_scene_assist_owner_can_manage_records_rules_and_search_prioritizes_dedicated(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Scene",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "notes": "火曜午前ルール",
            "assignments": [
                {"candidate_name": "Dedicated User", "employee_number": "1001", "role_type": "dedicated", "priority": 1},
                {"candidate_name": "Backup User", "employee_number": "1002", "role_type": "backup", "priority": 1},
                {"candidate_name": "Normal User", "employee_number": "1003", "role_type": "normal", "priority": 1},
            ],
        },
    )
    assert rule_response.status_code == 200
    assist_after_rule = rule_response.get_json()["assist"]
    assert len(assist_after_rule["rules"]) == 1

    record_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
        json={
            "date": "2026-03-31",
            "candidate_name": "Backup User",
            "employee_number": "1002",
            "shift_key": "A",
            "role_type": "backup",
            "notes": "前回対応",
        },
    )
    assert record_response.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    search_payload = search_response.get_json()
    results = search_payload["results"]
    assert [item["name"] for item in results[:3]] == ["Dedicated User", "Backup User", "Normal User"]
    assert results[0]["score"] > results[1]["score"] > results[2]["score"]
    assert search_payload["score_reference"]["theoretical_max"] is None
    assert search_payload["score_reference"]["single_rule_max"] == 1940
    assert search_payload["score_reference"]["single_record_max"] == 330
    assert results[0]["breakdown"]
    assert any(part["category"] == "rule" for part in results[0]["breakdown"])


def test_scene_assist_search_includes_registered_dedicated_candidate_from_master(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="01234",
            site_name="Master Linked Site",
            site_manager_last="管理",
            site_manager_first="担当",
            site_manager_id="9100",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()
        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="001",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()
        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
                site_id=site.site_id,
                site_branch=branch.site_branch,
                site_name=site.site_name,
                site_manager_id=site.site_manager_id,
                site_manager_name=site.manager_name(),
                cloudshift_option_key=branch.cloudshift_option_key,
                dedicated_employee_number="8801",
                dedicated_employee_name="登録専従者",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()
        site_row_id = site.id

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Master Linked Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert create_response.status_code == 200
    project_id = create_response.get_json()["project"]["project"]["id"]

    candidate_response = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/dedicated-candidate"
    )
    assert candidate_response.status_code == 200
    candidates = candidate_response.get_json()["candidates"]
    assert candidates[0]["employee_number"] == "8801"
    assert candidates[0]["contract_codes"] == ["01234001"]

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "O",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload["results"][0]["employee_number"] == "8801"
    assert payload["results"][0]["name"] == "登録専従者"
    assert any(part["category"] == "master" for part in payload["results"][0]["breakdown"])


def test_scene_project_backfills_siteplus_dedicated_rule_from_master(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    with client.application.app_context():
        site = Site(
            site_id="02222",
            site_name="Auto Rule Site",
            site_manager_last="管理",
            site_manager_first="担当",
            site_manager_id="9200",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()
        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="004",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()
        db.session.add(
            SiteContractMaster(
                contract_code="02222004",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
                site_id=site.site_id,
                site_branch=branch.site_branch,
                site_name=site.site_name,
                site_manager_id=site.site_manager_id,
                site_manager_name=site.manager_name(),
                cloudshift_option_key=branch.cloudshift_option_key,
                dedicated_employee_number="7711",
                dedicated_employee_name="自動 専従",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()
        site_row_id = site.id

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Auto Rule Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert create_response.status_code == 200
    project_id = create_response.get_json()["project"]["project"]["id"]

    assist_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist")
    assert assist_response.status_code == 200
    rules = assist_response.get_json()["assist"]["rules"]
    auto_rules = [rule for rule in rules if rule.get("source_type") == "siteplus_dedicated"]
    assert len(auto_rules) == 1
    assert auto_rules[0]["assignments"][0]["employee_number"] == "7711"
    assert auto_rules[0]["assignments"][0]["candidate_name"] == "自動 専従"
    assert auto_rules[0]["source_contract_code"] == "02222004"


def test_scene_assist_accepts_no_shift_and_weekday_only_matches_and_custom_points(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Expanded",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "",
            "notes": "曜日だけで使う",
            "assignments": [
                {
                    "candidate_name": "Weekday Dedicated",
                    "employee_number": "5101",
                    "role_type": "dedicated",
                    "priority": 1,
                    "custom_points": 55,
                }
            ],
        },
    )
    assert rule_response.status_code == 200
    saved_rule = rule_response.get_json()["rule"]
    assert saved_rule["shift_key"] == ""
    assert saved_rule["shift_label"] == "オプションなし"
    assert saved_rule["assignments"][0]["custom_points"] == 55

    record_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
        json={
            "date": "2026-03-31",
            "candidate_name": "Weekday Dedicated",
            "employee_number": "5101",
            "shift_key": "",
            "role_type": "dedicated",
        },
    )
    assert record_response.status_code == 200
    assert record_response.get_json()["record"]["shift_label"] == "オプションなし"

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload["query"]["shift_key"] == "A"
    assert payload["score_reference"]["single_rule_weekday_max"] == 1320
    assert payload["score_reference"]["single_record_weekday_max"] == 165
    assert payload["results"][0]["name"] == "Weekday Dedicated"
    assert payload["results"][0]["score"] == 540
    assert any(part["match_scope"] == "weekday" for part in payload["results"][0]["breakdown"])
    assert any(int(part.get("custom_points", 0) or 0) == 55 for part in payload["results"][0]["breakdown"])


def test_scene_assist_rule_accepts_no_weekday_and_search_uses_weekday_scope(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist No Weekday Rule",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": "",
            "shift_key": "",
            "assignments": [
                {
                    "candidate_name": "General Dedicated",
                    "employee_number": "5301",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    assert rule_response.status_code == 200
    saved_rule = rule_response.get_json()["rule"]
    assert saved_rule["weekday"] is None
    assert saved_rule["weekday_label"] == "曜日なし"
    assert saved_rule["shift_label"] == "オプションなし"

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload["results"][0]["name"] == "General Dedicated"
    assert payload["results"][0]["score"] == 320
    assert payload["results"][0]["breakdown"][0]["match_scope"] == "weekday"
    assert payload["results"][0]["breakdown"][0]["match_label"] == "曜日なし"


def test_scene_assist_search_allows_no_shift_query(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist No Shift Search",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    create_record = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
        json={
            "date": "2026-03-31",
            "candidate_name": "No Shift User",
            "employee_number": "5201",
            "shift_key": "",
            "role_type": "normal",
        },
    )
    assert create_record.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload["query"]["shift_label"] == "オプションなし"
    assert payload["results"][0]["name"] == "No Shift User"
    assert payload["results"][0]["breakdown"][0]["match_scope"] == "exact"


def test_scene_assist_search_marks_same_day_scene_conflicts_only_for_competing_sites(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    base_project = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Conflict Base",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    base_project_id = base_project["project"]["id"]

    conflict_project = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Conflict Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    conflict_project_id = conflict_project["project"]["id"]

    safe_project = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Safe Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    safe_project_id = safe_project["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{base_project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {
                    "candidate_name": "Conflict User",
                    "employee_number": "7001",
                    "role_type": "normal",
                    "priority": 1,
                }
            ],
        },
    )
    assert rule_response.status_code == 200

    conflict_entries = dict(conflict_project["month"]["entries_per_day"])
    conflict_entries["7"] = [
        {"id": "conflict-1", "value": "!E!Conflict User", "comment": "", "employee_number": "7001"}
    ]
    update_conflict_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{conflict_project_id}/month/2026/4",
        json={
            "entries_per_day": conflict_entries,
            "base_month": _legacy_base_month(conflict_project["month"]),
        },
    )
    assert update_conflict_response.status_code == 200

    safe_entries = dict(safe_project["month"]["entries_per_day"])
    safe_entries["7"] = [
        {"id": "safe-1", "value": "!P!Conflict User", "comment": "", "employee_number": "7001"}
    ]
    update_safe_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{safe_project_id}/month/2026/4",
        json={
            "entries_per_day": safe_entries,
            "base_month": _legacy_base_month(safe_project["month"]),
        },
    )
    assert update_safe_response.status_code == 200

    module.current_user = _other_owner()
    other_owner_project = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Other Owner Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    ).get_json()["project"]
    other_owner_project_id = other_owner_project["project"]["id"]
    other_owner_entries = dict(other_owner_project["month"]["entries_per_day"])
    other_owner_entries["7"] = [
        {"id": "other-1", "value": "!E!Conflict User", "comment": "", "employee_number": "7001"}
    ]
    update_other_owner_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{other_owner_project_id}/month/2026/4",
        json={
            "entries_per_day": other_owner_entries,
            "base_month": _legacy_base_month(other_owner_project["month"]),
        },
    )
    assert update_other_owner_response.status_code == 200

    module.current_user = _owner()
    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{base_project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    result = next(item for item in payload["results"] if item["name"] == "Conflict User")
    assert result["has_scene_conflict"] is True
    assert result["scene_conflict_site_names"] == ["Conflict Site"]
    assert result["scene_conflict_site_count"] == 1
    assert [item["project_title"] for item in result["scene_conflicts"]] == ["Conflict Site"]
    assert result["scene_conflicts"][0]["shift_key"] == "E"


def test_public_edit_assist_can_edit_records_but_not_rules(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Edit",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    payload = create_response.get_json()["project"]
    edit_token = _token_from_url(payload["project"]["urls"]["edit_url"])

    module.current_user = _guest()
    create_record = client.post(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/assist/records",
        json={
            "editor_name": "Guest Helper",
            "date": "2026-04-01",
            "candidate_name": "Guest Person",
            "employee_number": "2001",
            "shift_key": "P",
            "role_type": "normal",
        },
    )
    assert create_record.status_code == 200
    record_id = create_record.get_json()["record"]["id"]

    update_record = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/assist/records/{record_id}",
        json={
            "editor_name": "Guest Helper",
            "date": "2026-04-08",
            "candidate_name": "Guest Person",
            "employee_number": "2001",
            "shift_key": "P",
            "role_type": "backup",
            "notes": "差し替え",
        },
    )
    assert update_record.status_code == 200
    assert update_record.get_json()["record"]["role_type"] == "backup"

    forbidden_rule = client.post(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/assist/rules",
        json={
            "editor_name": "Guest Helper",
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {"candidate_name": "Guest Person", "employee_number": "2001", "role_type": "dedicated", "priority": 1}
            ],
        },
    )
    assert forbidden_rule.status_code == 403


def test_person_assist_bootstrap_and_sites_work_while_search_stays_scene_only(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Person",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]

    assist_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist")
    assert assist_response.status_code == 200
    assist_payload = assist_response.get_json()
    assert assist_payload["assist_mode"] == "person"
    assert assist_payload["assist"]["experienced_sites"] == []

    create_site = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/sites",
        json={
            "site_type": "experienced",
            "date": "2026-04-01",
            "site_name": "ABC公園",
            "shift_key": "O",
            "notes": "初回担当",
        },
    )
    assert create_site.status_code == 200
    created_site = create_site.get_json()["site"]
    assert created_site["site_type"] == "experienced"
    assert created_site["effective_from"] == "2026-04-02"
    assert created_site["shift_key"] == "O"

    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={"target_date": "2026-04-07", "shift_key": "A"},
    )
    assert response.status_code == 400
    assert "scene" in response.get_json()["error"]


def test_person_assist_experience_backfills_scene_records_and_search(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "佐藤さん",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    person_id = person_response.get_json()["project"]["project"]["id"]

    create_site = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites",
        json={
            "site_type": "experienced",
            "date": "2026-04-01",
            "site_name": "ABC公園",
            "shift_key": "O",
            "notes": "初回担当",
        },
    )
    assert create_site.status_code == 200

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "ABC公園",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert [item["name"] for item in assist_payload["profiles"]] == ["佐藤さん"]
    assert len(assist_payload["records"]) == 1
    record = assist_payload["records"][0]
    assert record["candidate_name"] == "佐藤さん"
    assert record["employee_number"] == "3001"
    assert record["date"] == "2026-04-02"
    assert record["shift_key"] == "O"
    assert record["role_type"] == "backup"
    assert record["notes"] == "初回担当"

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist/search",
        json={
            "target_date": "2026-04-08",
            "shift_key": "O",
        },
    )
    assert search_response.status_code == 200
    results = search_response.get_json()["results"]
    assert [item["name"] for item in results] == ["佐藤さん"]


def test_person_assist_site_type_update_and_delete_sync_scene_records(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "代務者A",
            "mode": "person",
            "employee_number": "3101",
            "year": "2026",
            "month": "4",
        },
    )
    person_id = person_response.get_json()["project"]["project"]["id"]

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "研修センター",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    create_training = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites",
        json={
            "site_type": "training",
            "date": "2026-04-03",
            "site_name": "研修センター",
            "shift_key": "A",
            "notes": "見学予定",
        },
    )
    assert create_training.status_code == 200
    site_id = create_training.get_json()["site"]["id"]
    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert assist_payload["records"] == []

    update_site = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites/{site_id}",
        json={
            "site_type": "experienced",
            "date": "2026-04-03",
            "site_name": "研修センター",
            "shift_key": "A",
            "notes": "見学完了",
        },
    )
    assert update_site.status_code == 200
    person_assist = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist"
    ).get_json()["assist"]
    assert len(person_assist["experienced_sites"]) == 1
    assert person_assist["training_sites"] == []

    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert len(assist_payload["records"]) == 1
    assert assist_payload["records"][0]["shift_key"] == "A"
    assert assist_payload["records"][0]["notes"] == "見学完了"

    delete_site = client.delete(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites/{site_id}"
    )
    assert delete_site.status_code == 200

    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert assist_payload["records"] == []


def test_person_assist_cross_owner_backfill_adds_auto_registration_note(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "佐藤さん",
            "mode": "person",
            "employee_number": "3201",
            "year": "2026",
            "month": "4",
        },
    )
    person_id = person_response.get_json()["project"]["project"]["id"]

    create_site = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites",
        json={
            "site_type": "experienced",
            "date": "2026-04-05",
            "site_name": "跨ぎ現場",
            "shift_key": "",
            "notes": "共有メモ",
        },
    )
    assert create_site.status_code == 200

    module.current_user = _other_owner()
    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "跨ぎ現場",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert len(assist_payload["records"]) == 1
    assert "共有メモ" in assist_payload["records"][0]["notes"]
    assert "佐藤さん からの自動実績登録" in assist_payload["records"][0]["notes"]


def test_scene_assist_search_uses_only_past_records(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Timeline",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    past_record = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
        json={
            "date": "2026-03-31",
            "candidate_name": "Past User",
            "employee_number": "4101",
            "shift_key": "A",
            "role_type": "normal",
        },
    )
    assert past_record.status_code == 200

    future_record = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
        json={
            "date": "2026-04-14",
            "candidate_name": "Future User",
            "employee_number": "4102",
            "shift_key": "A",
            "role_type": "dedicated",
        },
    )
    assert future_record.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200

    results = search_response.get_json()["results"]
    assert [item["name"] for item in results] == ["Past User"]
    assert results[0]["matched_record_count"] == 1


def test_scene_assist_rule_rejects_reversed_effective_period(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Rule Period",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "effective_from": "2026-04-30",
            "effective_to": "2026-04-01",
            "assignments": [
                {
                    "candidate_name": "Period User",
                    "employee_number": "4201",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "適用期間" in response.get_json()["error"]


def test_scene_assist_profile_preferred_weekday_adds_bonus(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Preferred Weekday",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {
                    "candidate_name": "Preferred User",
                    "employee_number": "6101",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    assert rule_response.status_code == 200
    profile_id = rule_response.get_json()["assist"]["profiles"][0]["id"]

    profile_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/profiles/{profile_id}",
        json={
            "active": True,
            "preferred_weekdays": [1],
            "blocked_weekdays": [],
        },
    )
    assert profile_response.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    result = payload["results"][0]
    assert result["name"] == "Preferred User"
    assert result["score"] == 970
    assert payload["score_reference"]["preferred_weekday_bonus"] == 30
    assert any(part["category"] == "profile" and part["points"] == 30 for part in result["breakdown"])


def test_scene_assist_profile_blocked_weekday_excludes_candidate(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Blocked Weekday",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {
                    "candidate_name": "Blocked User",
                    "employee_number": "6201",
                    "role_type": "dedicated",
                    "priority": 1,
                },
                {
                    "candidate_name": "Allowed User",
                    "employee_number": "6202",
                    "role_type": "backup",
                    "priority": 1,
                },
            ],
        },
    )
    assert rule_response.status_code == 200
    profiles = {item["name"]: item for item in rule_response.get_json()["assist"]["profiles"]}

    profile_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/profiles/{profiles['Blocked User']['id']}",
        json={
            "active": True,
            "preferred_weekdays": [],
            "blocked_weekdays": [1],
        },
    )
    assert profile_response.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload["score_reference"]["blocked_weekday_policy"] == "exclude"
    assert [item["name"] for item in payload["results"]] == ["Allowed User"]


def test_public_edit_assist_profile_updates_are_forbidden(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Profile Auth",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    edit_token = _token_from_url(payload["project"]["urls"]["edit_url"])

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {
                    "candidate_name": "Protected User",
                    "employee_number": "6301",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    profile_id = rule_response.get_json()["assist"]["profiles"][0]["id"]

    module.current_user = _guest()
    response = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/assist/profiles/{profile_id}",
        json={
            "editor_name": "Guest Helper",
            "preferred_weekdays": [1],
            "blocked_weekdays": [],
        },
    )
    assert response.status_code == 403


def test_scene_assist_option_aptitude_learning_adds_bonus_for_five_exact_vehicle_records(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Aptitude Bonus",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "O",
            "assignments": [
                {
                    "candidate_name": "Vehicle Expert",
                    "employee_number": "6401",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    assert rule_response.status_code == 200

    for date_text in ["2025-12-01", "2025-12-03", "2025-12-04", "2025-12-05", "2025-12-06"]:
        record_response = client.post(
            f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
            json={
                "date": date_text,
                "candidate_name": "Vehicle Expert",
                "employee_number": "6401",
                "shift_key": "O",
                "role_type": "normal",
            },
        )
        assert record_response.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "O",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    result = payload["results"][0]
    assert result["name"] == "Vehicle Expert"
    assert result["score"] == 965
    assert payload["score_reference"]["option_aptitude"]["max_bonus"] == 25
    assert any(
        part["category"] == "aptitude" and part["points"] == 25 and part["record_count"] == 5
        for part in result["breakdown"]
    )


def test_scene_assist_option_aptitude_learning_penalizes_missing_exact_timeslot_with_same_category_history(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Assist Aptitude Penalty",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    project_id = create_response.get_json()["project"]["project"]["id"]

    rule_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/rules",
        json={
            "weekday": 1,
            "shift_key": "A",
            "assignments": [
                {
                    "candidate_name": "Morning Unknown",
                    "employee_number": "6501",
                    "role_type": "dedicated",
                    "priority": 1,
                }
            ],
        },
    )
    assert rule_response.status_code == 200

    for date_text in ["2025-12-25", "2025-12-26"]:
        record_response = client.post(
            f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/records",
            json={
                "date": date_text,
                "candidate_name": "Morning Unknown",
                "employee_number": "6501",
                "shift_key": "P",
                "role_type": "normal",
            },
        )
        assert record_response.status_code == 200

    search_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist/search",
        json={
            "target_date": "2026-04-07",
            "shift_key": "A",
        },
    )
    assert search_response.status_code == 200
    payload = search_response.get_json()
    result = payload["results"][0]
    assert result["name"] == "Morning Unknown"
    assert result["score"] == 930
    assert payload["score_reference"]["option_aptitude"]["min_penalty"] == -10
    assert any(
        part["category"] == "aptitude" and part["points"] == -10 and part["record_count"] == 0
        for part in result["breakdown"]
    )


def test_owner_can_delete_last_month_and_keep_project(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Delete Case",
            "mode": "scene",
            "year": "2026",
            "month": "6",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]

    delete_response = client.delete(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/6"
    )
    assert delete_response.status_code == 200

    detail_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}")
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail["project"]["month_keys"] == []
    assert detail["month"] is None


def test_invalid_month_returns_400_instead_of_500(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Bad Month",
            "mode": "scene",
            "year": "2026",
            "month": "13",
        },
    )

    assert response.status_code == 400
    assert "月" in response.get_json()["error"]


def test_public_edit_can_merge_against_prior_revision_snapshot(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Merge Case",
            "mode": "scene",
            "year": "2026",
            "month": "7",
        },
    )
    payload = create_response.get_json()["project"]
    edit_token = _token_from_url(payload["project"]["urls"]["edit_url"])
    initial_month = payload["month"]

    module.current_user = _guest()
    first_entries = dict(initial_month["entries_per_day"])
    first_entries["1"] = [{"id": "entry-1", "value": "!A!Alice", "comment": ""}]
    first_save = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/month/2026/7",
        json={
            "editor_name": "Guest A",
            "required_capacity": 0,
            "entries_per_day": first_entries,
            "base_month": initial_month,
        },
    )
    assert first_save.status_code == 200
    latest_month = first_save.get_json()["month"]

    second_entries = dict(initial_month["entries_per_day"])
    second_entries["2"] = [{"id": "entry-2", "value": "!P!Bob", "comment": ""}]
    second_save = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/month/2026/7",
        json={
            "editor_name": "Guest B",
            "required_capacity": 0,
            "entries_per_day": second_entries,
            "base_month": initial_month,
        },
    )

    assert second_save.status_code == 200
    merged_month = second_save.get_json()["month"]
    assert latest_month["revision"] == 2
    assert merged_month["revision"] == 3
    assert merged_month["entries_per_day"]["1"][0]["value"] == "!A!Alice"
    assert merged_month["entries_per_day"]["2"][0]["value"] == "!P!Bob"


def test_owner_save_accepts_legacy_base_month_without_revision(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Owner Legacy Save",
            "mode": "scene",
            "year": "2026",
            "month": "8",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    month_entries = dict(month["entries_per_day"])
    month_entries["3"] = [{"id": "entry-3", "value": "Main Hall", "comment": ""}]
    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/8",
        json={
            "required_capacity": 2,
            "entries_per_day": month_entries,
            "base_month": _legacy_base_month(month),
        },
    )

    assert save_response.status_code == 200
    saved_month = save_response.get_json()["month"]
    assert saved_month["revision"] == 2
    assert saved_month["required_capacity"] == 2
    assert saved_month["entries_per_day"]["3"][0]["value"] == "Main Hall"


def test_public_edit_legacy_base_month_can_merge_from_snapshot(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Legacy Snapshot Merge",
            "mode": "scene",
            "year": "2026",
            "month": "9",
        },
    )
    payload = create_response.get_json()["project"]
    edit_token = _token_from_url(payload["project"]["urls"]["edit_url"])
    initial_month = payload["month"]

    module.current_user = _guest()
    first_entries = dict(initial_month["entries_per_day"])
    first_entries["1"] = [{"id": "entry-1", "value": "!A!Alice", "comment": ""}]
    first_save = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/month/2026/9",
        json={
            "editor_name": "Guest A",
            "required_capacity": 0,
            "entries_per_day": first_entries,
            "base_month": _legacy_base_month(initial_month),
        },
    )
    assert first_save.status_code == 200

    second_entries = dict(initial_month["entries_per_day"])
    second_entries["2"] = [{"id": "entry-2", "value": "!P!Bob", "comment": ""}]
    second_save = client.put(
        f"/tools/shiftersync/cloudshift/api/public/edit/{edit_token}/month/2026/9",
        json={
            "editor_name": "Guest B",
            "required_capacity": 0,
            "entries_per_day": second_entries,
            "base_month": _legacy_base_month(initial_month),
        },
    )

    assert second_save.status_code == 200
    merged_month = second_save.get_json()["month"]
    assert merged_month["revision"] == 3
    assert merged_month["entries_per_day"]["1"][0]["value"] == "!A!Alice"
    assert merged_month["entries_per_day"]["2"][0]["value"] == "!P!Bob"


def test_legacy_base_month_without_matching_snapshot_still_conflicts(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Legacy Conflict",
            "mode": "scene",
            "year": "2026",
            "month": "10",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    month_entries = dict(month["entries_per_day"])
    month_entries["1"] = [{"id": "entry-1", "value": "Current", "comment": ""}]
    first_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/10",
        json={
            "required_capacity": 0,
            "entries_per_day": month_entries,
            "base_month": month,
        },
    )
    assert first_save.status_code == 200

    stale_base = _legacy_base_month(month)
    stale_base["entries_per_day"] = dict(stale_base["entries_per_day"])
    stale_base["entries_per_day"]["2"] = [{"id": "ghost-entry", "value": "Ghost", "comment": ""}]
    stale_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/10",
        json={
            "required_capacity": 0,
            "entries_per_day": month_entries,
            "base_month": stale_base,
        },
    )

    assert stale_save.status_code == 409


def test_owner_can_sync_person_month_leaves_to_leave_mgr(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    _prepare_leave_mgr_data(tmp_path, "owner01", accessible_calendar_ids=["office_a", "office_b"])

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Leave Sync",
            "mode": "person",
            "employee_number": "1001",
            "year": "2026",
            "month": "5",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    month_entries = dict(month["entries_per_day"])
    month_entries["1"] = [
        {"id": "leave-paid", "value": "!PAID!有休", "comment": "午前休\n希望休"},
    ]
    month_entries["2"] = [
        {"id": "shift-a", "value": "Main Hall", "comment": ""},
    ]
    month_entries["3"] = [
        {"id": "leave-comp", "value": "!COMP!代休", "comment": ""},
    ]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5",
        json={
            "required_capacity": 0,
            "entries_per_day": month_entries,
            "base_month": month,
        },
    )
    assert save_response.status_code == 200

    calendars_response = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/leave-sync/calendars"
    )
    assert calendars_response.status_code == 200
    assert [item["calendar_id"] for item in calendars_response.get_json()["calendars"]] == ["office_a", "office_b"]

    sync_a = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/leave-sync/2026/5",
        json={"calendar_id": "office_a"},
    )
    assert sync_a.status_code == 200
    sync_a_payload = sync_a.get_json()
    assert sync_a_payload["created_total"] == 2
    assert sync_a_payload["removed_total"] == 0
    assert sync_a_payload["skipped_total"] == 0

    office_a = _load_leave_calendar(tmp_path, "office_a", "202605")
    assert len(office_a["leaves"]) == 2
    assert {leave["leave_type"] for leave in office_a["leaves"]} == {"有休", "代休"}
    assert {leave["name"] for leave in office_a["leaves"]} == {"Leave Sync"}
    assert office_a["leaves"][0]["leave_type"] == "有休"
    assert office_a["leaves"][0]["remarks"] == "[from CloudShift] / 午前休\n希望休"
    assert office_a["leaves"][0]["created_by"] == "owner01"
    assert office_a["leaves"][0]["source_project_id"] == project_id

    sync_b = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/leave-sync/2026/5",
        json={"calendar_id": "office_b"},
    )
    assert sync_b.status_code == 200
    sync_b_payload = sync_b.get_json()
    assert sync_b_payload["created_total"] == 2
    assert sync_b_payload["removed_total"] == 2

    office_a_after = _load_leave_calendar(tmp_path, "office_a", "202605")
    office_b_after = _load_leave_calendar(tmp_path, "office_b", "202605")
    assert office_a_after["leaves"] == []
    assert len(office_b_after["leaves"]) == 2
    assert office_b_after["leaves"][0]["source_calendar_id"] == "office_b"


def test_scene_project_cannot_sync_leaves(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    _prepare_leave_mgr_data(tmp_path, "owner01", accessible_calendar_ids=["office_a"])

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene Project",
            "mode": "scene",
            "year": "2026",
            "month": "5",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]

    sync_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/leave-sync/2026/5",
        json={"calendar_id": "office_a"},
    )

    assert sync_response.status_code == 400


def test_owner_can_restore_revision_and_snapshot_limit_stays_at_12(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Restore Case",
            "mode": "scene",
            "year": "2026",
            "month": "11",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    revisions = {1: month["entries_per_day"]["1"]}
    for revision in range(2, 15):
        month_entries = dict(month["entries_per_day"])
        month_entries["1"] = [
            {"id": f"entry-{revision}", "value": f"!A!Member {revision}", "comment": f"rev {revision}"}
        ]
        save_response = client.put(
            f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/11",
            json={
                "required_capacity": 0,
                "entries_per_day": month_entries,
                "base_month": month,
            },
        )
        assert save_response.status_code == 200
        month = save_response.get_json()["month"]
        revisions[revision] = month["entries_per_day"]["1"]

    revision_list = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/11/revisions"
    )
    assert revision_list.status_code == 200
    revision_payload = revision_list.get_json()
    revision_numbers = [item["revision"] for item in revision_payload["revisions"]]
    assert revision_payload["current_revision"] == 14
    assert revision_numbers == list(range(14, 1, -1))
    assert 1 not in revision_numbers

    restore_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/11/restore",
        json={"revision": 5},
    )
    assert restore_response.status_code == 200
    restored_month = restore_response.get_json()["month"]
    assert restored_month["revision"] == 15
    assert restored_month["entries_per_day"]["1"] == revisions[5]

    restored_list = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/11/revisions"
    )
    assert restored_list.status_code == 200
    restored_numbers = [item["revision"] for item in restored_list.get_json()["revisions"]]
    assert restored_numbers == list(range(15, 2, -1))
    assert 2 not in restored_numbers
    assert 14 in restored_numbers


def test_noop_save_keeps_revision_and_snapshots_unchanged(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Noop Save",
            "mode": "scene",
            "year": "2026",
            "month": "6",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/6",
        json={
            "required_capacity": month["required_capacity"],
            "entries_per_day": month["entries_per_day"],
            "base_month": month,
        },
    )

    assert save_response.status_code == 200
    saved_month = save_response.get_json()["month"]
    assert saved_month["revision"] == 1

    with client.application.app_context():
        stored = module._load_project(project_id)

    assert stored is not None
    assert stored["months"]["2026-06"]["revision"] == 1
    assert stored["months"]["2026-06"].get("revision_snapshots") == {}


def test_lock_timeout_returns_json_error_instead_of_500(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Lock Timeout",
            "mode": "scene",
            "year": "2026",
            "month": "7",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    month = payload["month"]

    with client.application.app_context():
        lock_path = module._locks_dir() / f"{project_id}.lock"
        lock_path.write_text("busy", encoding="utf-8")

    original_timeout = module.LOCK_TIMEOUT_SECONDS
    module.LOCK_TIMEOUT_SECONDS = 0
    try:
        save_response = client.put(
            f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/7",
            json={
                "required_capacity": month["required_capacity"],
                "entries_per_day": month["entries_per_day"],
                "base_month": month,
            },
        )
    finally:
        module.LOCK_TIMEOUT_SECONDS = original_timeout
        with client.application.app_context():
            if lock_path.exists():
                lock_path.unlink()

    assert save_response.status_code == 423
    assert save_response.get_json()["error"] == "別の更新処理が進行中です。少し待ってから再度お試しください"


def test_scene_month_summary_counts_people_options_and_comments(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene Summary",
            "mode": "scene",
            "year": "2026",
            "month": "4",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]

    summary_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4/summary",
        json={
            "required_capacity": 0,
            "entries_per_day": {
                "1": [
                    {
                        "id": "entry-a",
                        "value": "!A!Alice",
                        "comment": "Morning",
                        "employee_number": "1001",
                    },
                    {
                        "id": "entry-b",
                        "value": "!P!Bob",
                        "comment": "",
                    },
                ],
                "2": [
                    {
                        "id": "entry-c",
                        "value": "!M!Alice",
                        "comment": "",
                        "employee_number": "1001",
                    }
                ],
                "3": [
                    {
                        "id": "entry-d",
                        "value": "Charlie",
                        "comment": "",
                    }
                ],
            },
        },
    )

    assert summary_response.status_code == 200
    summary = summary_response.get_json()["summary"]
    assert summary["mode"] == "scene"
    overview = {item["label"]: item["value"] for item in summary["overview"]}
    assert overview["登録件数"] == "4件"
    assert overview["入力日数"] == "3日"
    assert overview["コメント"] == "1件 / 1日"

    sections = {section["title"]: section["items"] for section in summary["sections"]}
    people_items = {item["label"]: item for item in sections["人物別回数"]}
    assert people_items["Alice / 1001"]["count"] == 2
    assert people_items["Alice / 1001"]["meta"] == "2日 / コメント 1件"
    assert people_items["Bob"]["count"] == 1
    assert {item["label"]: item["count"] for item in sections["時間帯"]} == {"午前": 1, "午後": 1}
    assert {item["label"]: item["count"] for item in sections["車両"]} == {"マイクロ": 1}
    assert {item["label"]: item["count"] for item in sections["コメントが多い日"]} == {"1日": 1}


def test_person_month_summary_separates_worksites_and_leaves(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Person Summary",
            "mode": "person",
            "employee_number": "1001",
            "year": "2026",
            "month": "5",
        },
    )
    payload = create_response.get_json()["project"]
    project_id = payload["project"]["id"]
    view_token = _token_from_url(payload["project"]["urls"]["view_url"])

    summary_response = client.post(
        f"/tools/shiftersync/cloudshift/api/public/view/{view_token}/month/2026/5/summary",
        json={
            "required_capacity": 0,
            "entries_per_day": {
                "1": [{"id": "work-1", "value": "Main Hall", "comment": ""}],
                "2": [{"id": "work-2", "value": "!A!Main Hall", "comment": ""}],
                "3": [{"id": "leave-1", "value": "!PAID!有休", "comment": "vacation"}],
                "4": [{"id": "leave-2", "value": "!COMP!代休", "comment": ""}],
                "5": [{"id": "work-3", "value": "!W!Branch", "comment": ""}],
            },
        },
    )

    assert summary_response.status_code == 200
    summary = summary_response.get_json()["summary"]
    assert summary["mode"] == "person"
    overview = {item["label"]: item["value"] for item in summary["overview"]}
    assert overview["登録件数"] == "5件"
    assert overview["勤務登録"] == "3件"
    assert overview["休暇登録"] == "2件"

    sections = {section["title"]: section["items"] for section in summary["sections"]}
    worksites = {item["label"]: item["count"] for item in sections["現場別回数"]}
    assert worksites == {"Main Hall": 2, "Branch": 1}
    assert {item["label"]: item["count"] for item in sections["勤務帯"]} == {"午前": 1}
    assert {item["label"]: item["count"] for item in sections["休暇種別"]} == {"有休": 1, "代休": 1}
    assert {item["label"]: item["count"] for item in sections["車両"]} == {"ワゴン": 1}
