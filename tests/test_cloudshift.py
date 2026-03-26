import sys
import json
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
    app.register_blueprint(module.cloudshift_bp)
    return module, app.test_client()


def _owner():
    return SimpleNamespace(is_authenticated=True, username="owner01", name="Owner User")


def _guest():
    return SimpleNamespace(is_authenticated=False)


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
