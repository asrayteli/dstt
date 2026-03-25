import sys
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
    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
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
        {"id": "entry-1", "value": "!A!Alice", "comment": "Morning only"},
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

    xlsx_export = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/export/xlsx",
        query_string={"month_key": "2026-05"},
    )
    assert xlsx_export.status_code == 200
    assert xlsx_export.content_type.startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(xlsx_export.data) > 100


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
