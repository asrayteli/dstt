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

from app.models import Site, SiteBranch, db


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_site_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_app(tmp_path):
    module = _load_cloudshift_module()
    app = Flask(__name__, root_path=str(tmp_path), instance_path=str(tmp_path / "instance"))
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'cloudshift-sites.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.cloudshift_bp)
    with app.app_context():
        db.create_all()
    return module, app


def _owner():
    return SimpleNamespace(is_authenticated=True, username="owner01", name="Owner User")


def _create_site(app, *, site_id, site_name):
    with app.app_context():
        site = Site(
            site_id=site_id,
            site_name=site_name,
            site_manager_last="Manager",
            site_manager_first="One",
            site_manager_id="9001",
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        return site.id


def _create_branch(app, *, site_row_id, site_branch, option_key="O"):
    with app.app_context():
        branch = SiteBranch(
            site_row_id=site_row_id,
            site_branch=site_branch,
            cloudshift_option_key=option_key,
            site_register="owner01",
            site_updater="owner01",
            is_active=True,
        )
        db.session.add(branch)
        db.session.commit()
        return branch.id


def test_cloudshift_scene_create_can_link_site(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="12345", site_name="Alpha Site")
    client = app.test_client()

    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Alpha Shift",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )

    assert response.status_code == 200
    payload = response.get_json()["project"]["project"]
    assert payload["site"]["is_linked"] is True
    assert payload["site"]["site_row_id"] == site_row_id
    assert payload["site"]["site_id"] == "12345"
    assert payload["site"]["site_name"] == "Alpha Site"


def test_cloudshift_existing_scene_can_set_site_later(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="54321", site_name="Beta Site")
    client = app.test_client()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Legacy Shift",
            "mode": "scene",
            "year": "2026",
            "month": "5",
        },
    )
    assert create_response.status_code == 200
    project = create_response.get_json()["project"]["project"]
    assert project["site"]["is_linked"] is False

    update_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project['id']}/meta",
        json={
            "title": "Legacy Shift",
            "site_row_id": site_row_id,
        },
    )

    assert update_response.status_code == 200
    updated_project = update_response.get_json()["project"]["project"]
    assert updated_project["site"]["is_linked"] is True
    assert updated_project["site"]["site_id"] == "54321"
    assert updated_project["site"]["site_name"] == "Beta Site"


def test_cloudshift_month_save_preserves_site_branch_metadata(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="13579", site_name="Gamma Site")
    branch_row_id = _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="O")
    client = app.test_client()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Gamma Shift",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert create_response.status_code == 200
    project_payload = create_response.get_json()["project"]
    project_id = project_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": project_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "entry-001",
                        "value": "!O!Alice",
                        "comment": "大型担当",
                        "site_branch_row_id": str(branch_row_id),
                        "site_branch": "001",
                    }
                ]
            },
        },
    )

    assert save_response.status_code == 200
    month_payload = save_response.get_json()["month"]
    entry = month_payload["entries_per_day"]["1"][0]
    assert entry["site_branch_row_id"] == str(branch_row_id)
    assert entry["site_branch"] == "001"


def test_scene_month_save_applies_siteplus_option_from_branch(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="77777", site_name="Delta Site")
    branch_row_id = _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Delta Shift",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert create_response.status_code == 200
    project_payload = create_response.get_json()["project"]
    project_id = project_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": project_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "entry-branch-option",
                        "value": "Alice",
                        "comment": "branch mapped",
                        "site_branch_row_id": str(branch_row_id),
                        "site_branch": "001",
                    }
                ]
            },
        },
    )

    assert save_response.status_code == 200
    entry = save_response.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry["value"] == "!N1!Alice"
    assert entry["site_branch_row_id"] == str(branch_row_id)
    assert entry["site_branch"] == "001"


def test_person_experience_site_link_matches_scene_by_site_row_id(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    client = app.test_client()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_id = person_response.get_json()["project"]["project"]["id"]

    create_site_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/assist/sites",
        json={
            "site_type": "experienced",
            "date": "2026-04-01",
            "site_row_id": site_row_id,
            "site_name": "Legacy Name",
            "shift_key": "O",
            "notes": "大型担当",
        },
    )
    assert create_site_response.status_code == 200
    assist_site = create_site_response.get_json()["site"]
    assert assist_site["site_row_id"] == site_row_id
    assert assist_site["site_id"] == "24680"
    assert assist_site["site_name"] == "Linked Master Site"

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Different Scene Title",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    assist_payload = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/assist"
    ).get_json()["assist"]
    assert len(assist_payload["records"]) == 1
    record = assist_payload["records"][0]
    assert record["candidate_name"] == "Worker One"
    assert record["shift_key"] == "O"


def test_scene_month_syncs_assignment_to_person_project(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_id = person_response.get_json()["project"]["project"]["id"]

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "scene-entry-1",
                        "value": "!N1!Worker One",
                        "comment": "scene source",
                        "employee_number": "3001",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    person_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}",
        query_string={"month_key": "2026-04"},
    )
    assert person_detail.status_code == 200
    mirrored_entries = person_detail.get_json()["month"]["entries_per_day"]["1"]
    assert len(mirrored_entries) == 1
    entry = mirrored_entries[0]
    assert entry["value"] == "!N1!Linked Master Site"
    assert entry["site_row_id"] == str(site_row_id)
    assert entry["site_id"] == "24680"
    assert entry["site_name"] == "Linked Master Site"
    assert entry["sync_source_type"] == "scene_shift"
    assert entry["sync_source_project_id"] == scene_id


def test_new_person_project_receives_existing_scene_shift_sync(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {
                "3": [
                    {
                        "id": "scene-entry-existing",
                        "value": "!N1!Worker One",
                        "comment": "existing scene source",
                        "employee_number": "3001",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_month = person_response.get_json()["project"]["month"]
    mirrored_entries = person_month["entries_per_day"]["3"]
    assert len(mirrored_entries) == 1
    assert mirrored_entries[0]["value"] == "!N1!Linked Master Site"
    assert mirrored_entries[0]["sync_source_type"] == "scene_shift"


def test_person_month_syncs_assignment_to_scene_project(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    branch_row_id = _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_payload = person_response.get_json()["project"]
    person_id = person_payload["project"]["id"]

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": person_payload["month"],
            "entries_per_day": {
                "2": [
                    {
                        "id": "person-entry-1",
                        "value": "!N1!Linked Master Site",
                        "comment": "person source",
                        "site_row_id": str(site_row_id),
                        "site_id": "24680",
                        "site_name": "Linked Master Site",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    scene_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
        query_string={"month_key": "2026-04"},
    )
    assert scene_detail.status_code == 200
    mirrored_entries = scene_detail.get_json()["month"]["entries_per_day"]["2"]
    assert len(mirrored_entries) == 1
    entry = mirrored_entries[0]
    assert entry["value"] == "!N1!Worker One"
    assert entry["employee_number"] == "3001"
    assert entry["site_branch_row_id"] == str(branch_row_id)
    assert entry["site_branch"] == "001"
    assert entry["sync_source_type"] == "person_shift"
    assert entry["sync_source_project_id"] == person_id


def test_new_scene_project_receives_existing_person_shift_sync(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_payload = person_response.get_json()["project"]
    person_id = person_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": person_payload["month"],
            "entries_per_day": {
                "4": [
                    {
                        "id": "person-entry-existing",
                        "value": "!N1!Linked Master Site",
                        "comment": "existing person source",
                        "site_row_id": str(site_row_id),
                        "site_id": "24680",
                        "site_name": "Linked Master Site",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_month = scene_response.get_json()["project"]["month"]
    mirrored_entries = scene_month["entries_per_day"]["4"]
    assert len(mirrored_entries) == 1
    assert mirrored_entries[0]["value"] == "!N1!Worker One"
    assert mirrored_entries[0]["employee_number"] == "3001"
    assert mirrored_entries[0]["sync_source_type"] == "person_shift"


def test_new_month_receives_existing_opposite_shift_sync(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "5",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/5",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {
                "6": [
                    {
                        "id": "scene-entry-may",
                        "value": "!N1!Worker One",
                        "comment": "may scene source",
                        "employee_number": "3001",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_id = person_response.get_json()["project"]["project"]["id"]

    create_month_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/month",
        json={
            "year": 2026,
            "month": 5,
            "init_mode": "blank",
        },
    )
    assert create_month_response.status_code == 200
    person_month = create_month_response.get_json()["project"]["month"]
    mirrored_entries = person_month["entries_per_day"]["6"]
    assert len(mirrored_entries) == 1
    assert mirrored_entries[0]["value"] == "!N1!Linked Master Site"
    assert mirrored_entries[0]["sync_source_type"] == "scene_shift"


def test_deleting_source_month_removes_shift_sync_entries(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    client = app.test_client()

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Worker One",
            "mode": "person",
            "employee_number": "3001",
            "year": "2026",
            "month": "4",
        },
    )
    assert person_response.status_code == 200
    person_id = person_response.get_json()["project"]["project"]["id"]

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene One",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    assert scene_response.status_code == 200
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]

    save_response = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "scene-entry-1",
                        "value": "Worker One",
                        "comment": "scene source",
                        "employee_number": "3001",
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200

    person_before_delete = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}",
        query_string={"month_key": "2026-04"},
    )
    assert len(person_before_delete.get_json()["month"]["entries_per_day"]["1"]) == 1

    delete_response = client.delete(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4"
    )
    assert delete_response.status_code == 200

    person_after_delete = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}",
        query_string={"month_key": "2026-04"},
    )
    assert person_after_delete.status_code == 200
    assert person_after_delete.get_json()["month"]["entries_per_day"]["1"] == []


def _link_two_person_assignments_into_scene(client, *, day):
    """Create two person projects that both sync a same-day assignment into one scene project.

    Returns (scene_id, scene_month, synced_entries, person_a, person_b) for the
    destination scene book.
    """
    def _make_person(title, emp):
        resp = client.post(
            "/tools/shiftersync/cloudshift/api/create",
            data={"title": title, "mode": "person", "employee_number": emp, "year": "2026", "month": "4"},
        )
        assert resp.status_code == 200
        return resp.get_json()["project"]

    person_a = _make_person("Worker A", "3001")
    person_b = _make_person("Worker B", "3002")

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Scene One", "mode": "scene", "year": "2026", "month": "4", "site_row_id": str(_link_two_person_assignments_into_scene.site_row_id)},
    )
    assert scene_response.status_code == 200
    scene_id = scene_response.get_json()["project"]["project"]["id"]

    for person, entry_id in ((person_a, "person-a-1"), (person_b, "person-b-1")):
        save = client.put(
            f"/tools/shiftersync/cloudshift/api/project/{person['project']['id']}/month/2026/4",
            json={
                "required_capacity": 0,
                "base_month": person["month"],
                "entries_per_day": {
                    str(day): [{
                        "id": entry_id,
                        "value": "!N1!Linked Master Site",
                        "comment": "",
                        "site_row_id": str(_link_two_person_assignments_into_scene.site_row_id),
                        "site_id": "24680",
                        "site_name": "Linked Master Site",
                    }]
                },
            },
        )
        assert save.status_code == 200

    scene_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
        query_string={"month_key": "2026-04"},
    )
    assert scene_detail.status_code == 200
    scene_month = scene_detail.get_json()["month"]
    synced = scene_month["entries_per_day"][str(day)]
    assert len(synced) == 2
    assert all(entry["sync_source_type"] == "person_shift" for entry in synced)
    return scene_id, scene_month, synced, person_a, person_b


def test_synced_entries_can_be_reordered_within_same_day(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    _link_two_person_assignments_into_scene.site_row_id = _create_site(
        app, site_id="24680", site_name="Linked Master Site"
    )
    _create_branch(app, site_row_id=_link_two_person_assignments_into_scene.site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_id, scene_month, synced, person_a, person_b = _link_two_person_assignments_into_scene(client, day=5)

    # Put "Worker B" explicitly on top (the early/late ordering use case), regardless
    # of the order the two sources happened to sync in.
    worker_b_first = sorted(synced, key=lambda entry: entry["value"] != "!N1!Worker B")
    assert worker_b_first[0]["value"] == "!N1!Worker B"
    reordered = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_month,
            "entries_per_day": {"5": worker_b_first},
        },
    )
    assert reordered.status_code == 200

    def _scene_day_values():
        detail = client.get(
            f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
            query_string={"month_key": "2026-04"},
        )
        return [entry["value"] for entry in detail.get_json()["month"]["entries_per_day"]["5"]]

    # The manual order is honored after the save (and after the save-triggered re-sync).
    assert _scene_day_values() == ["!N1!Worker B", "!N1!Worker A"]

    # The order must also survive a later re-sync triggered by a source-side change.
    person_b_month = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_b['project']['id']}",
        query_string={"month_key": "2026-04"},
    ).get_json()["month"]
    resave_b = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person_b['project']['id']}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": person_b_month,
            "entries_per_day": {
                "5": [{
                    "id": "person-b-1",
                    "value": "!N1!Linked Master Site",
                    "comment": "updated note",
                    "site_row_id": str(_link_two_person_assignments_into_scene.site_row_id),
                    "site_id": "24680",
                    "site_name": "Linked Master Site",
                }]
            },
        },
    )
    assert resave_b.status_code == 200
    assert _scene_day_values() == ["!N1!Worker B", "!N1!Worker A"]


def test_reordering_synced_entries_does_not_modify_source(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    _link_two_person_assignments_into_scene.site_row_id = _create_site(
        app, site_id="24680", site_name="Linked Master Site"
    )
    _create_branch(app, site_row_id=_link_two_person_assignments_into_scene.site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_id, scene_month, synced, person_a, person_b = _link_two_person_assignments_into_scene(client, day=5)

    def _person_day(person):
        detail = client.get(
            f"/tools/shiftersync/cloudshift/api/project/{person['project']['id']}",
            query_string={"month_key": "2026-04"},
        )
        return detail.get_json()["month"]["entries_per_day"].get("5", [])

    person_a_before = _person_day(person_a)
    person_b_before = _person_day(person_b)

    # Reorder the synced entries in the destination scene and save.
    reordered = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_month,
            "entries_per_day": {"5": list(reversed(synced))},
        },
    )
    assert reordered.status_code == 200

    # The source person books must be untouched: same entries, content and order.
    assert _person_day(person_a) == person_a_before
    assert _person_day(person_b) == person_b_before
    # Each source still holds exactly its own single local (non-synced) assignment.
    for person_day in (person_a_before, person_b_before):
        assert len(person_day) == 1
        assert person_day[0]["value"] == "!N1!Linked Master Site"
        assert not str(person_day[0].get("sync_source_type") or "")


def test_synced_entry_can_be_ordered_above_a_local_entry(tmp_path):
    # The reported scenario: a scene book has a LOCAL person (佐藤) and a SYNCED
    # person (田中, from Tanaka's own book). The scene owner wants the synced 田中
    # to appear ABOVE the local 佐藤.
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    site_row_id = _create_site(app, site_id="24680", site_name="Linked Master Site")
    _create_branch(app, site_row_id=site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Scene A", "mode": "scene", "year": "2026", "month": "4", "site_row_id": str(site_row_id)},
    )
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]
    scene_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {"1": [{"id": "local-sato", "value": "!N1!佐藤", "comment": ""}]},
        },
    )
    assert scene_save.status_code == 200

    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "田中", "mode": "person", "employee_number": "4001", "year": "2026", "month": "4"},
    )
    person_payload = person_response.get_json()["project"]
    person_id = person_payload["project"]["id"]
    person_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": person_payload["month"],
            "entries_per_day": {"1": [{
                "id": "tanaka-1",
                "value": "!N1!Linked Master Site",
                "comment": "",
                "site_row_id": str(site_row_id),
                "site_id": "24680",
                "site_name": "Linked Master Site",
            }]},
        },
    )
    assert person_save.status_code == 200

    def _scene_day():
        detail = client.get(
            f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
            query_string={"month_key": "2026-04"},
        )
        return detail.get_json()["month"]["entries_per_day"]["1"]

    initial = _scene_day()
    assert len(initial) == 2
    # Local 佐藤 first, synced 田中 second — the starting order described in the report.
    assert not str(initial[0].get("sync_source_type") or "") and initial[0]["value"] == "!N1!佐藤"
    assert initial[1]["sync_source_type"] == "person_shift"
    synced_tanaka, local_sato = initial[1], initial[0]

    # Reorder so the SYNCED 田中 sits above the LOCAL 佐藤, then save from the scene book.
    scene_month_now = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
        query_string={"month_key": "2026-04"},
    ).get_json()["month"]
    reorder = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_month_now,
            "entries_per_day": {"1": [synced_tanaka, local_sato]},
        },
    )
    assert reorder.status_code == 200

    after = _scene_day()
    # Synced 田中 is now on top of local 佐藤, and the order survives the save-time re-sync.
    assert [entry["value"] for entry in after] == ["!N1!田中", "!N1!佐藤"]
    assert after[0]["sync_source_type"] == "person_shift"
    assert not str(after[1].get("sync_source_type") or "")

    # The source (Tanaka's book) is unaffected.
    tanaka_day = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}",
        query_string={"month_key": "2026-04"},
    ).get_json()["month"]["entries_per_day"]["1"]
    assert len(tanaka_day) == 1
    assert tanaka_day[0]["value"] == "!N1!Linked Master Site"
    assert not str(tanaka_day[0].get("sync_source_type") or "")


def test_synced_entry_cannot_be_moved_to_another_day_or_edited(tmp_path):
    module, app = _build_app(tmp_path)
    module.current_user = _owner()
    _link_two_person_assignments_into_scene.site_row_id = _create_site(
        app, site_id="24680", site_name="Linked Master Site"
    )
    _create_branch(app, site_row_id=_link_two_person_assignments_into_scene.site_row_id, site_branch="001", option_key="N1")
    client = app.test_client()

    scene_id, scene_month, synced, _person_a, _person_b = _link_two_person_assignments_into_scene(client, day=5)
    moved = dict(synced[0])
    moved["value"] = "!N1!Tampered"  # try to also change content

    # Client tries to move one synced entry to day 6 and edit it; backend must reject both.
    save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_month,
            "entries_per_day": {"5": [synced[1]], "6": [moved]},
        },
    )
    assert save.status_code == 200

    after = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}",
        query_string={"month_key": "2026-04"},
    )
    after_per_day = after.get_json()["month"]["entries_per_day"]
    # The synced entry stayed on day 5 (no cross-day move) and kept its original content.
    assert len(after_per_day["5"]) == 2
    assert "6" not in after_per_day or after_per_day["6"] == []
    assert all("Tampered" not in entry["value"] for entry in after_per_day["5"])
