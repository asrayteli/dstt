from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from app.services.cloudshift_large import default_large_config
from app.models import Site, db
from tests.test_cloudshift import _build_client, _owner


BASE = "/tools/shiftersync/cloudshift"


def _token(url):
    return Path(urlparse(url).path).name


def test_large_project_owner_public_baseline_and_exports(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    created = client.post(
        f"{BASE}/api/create",
        data={"title": "大規模現場", "mode": "large", "year": "2026", "month": "7", "required_capacity": "99"},
    )
    assert created.status_code == 200
    detail = created.get_json()["project"]
    project = detail["project"]
    project_id = project["id"]
    assert project["mode"] == "large"
    assert detail["month"]["required_capacity"] == 0
    assert len(project["large_config"]["codes"]) == 7

    config = default_large_config()
    config["members"] = [{
        "id": "m1", "display_name": "職員A", "employee_number": "1001",
        "employee_name": "職員A", "order": 10, "active": True,
    }]
    config["codes"].append({
        "key": "A", "label": "通常", "category": "work", "order": 10, "active": True,
        "times": {day_type: {"start": "09:00", "end": "18:00"} for day_type in ("weekday", "saturday", "holiday")},
        "color": "#dbeafe",
    })
    config["codes"].append({
        "key": "B", "label": "午後業務", "category": "work", "order": 20, "active": True,
        "times": {day_type: {"start": "13:00", "end": "18:00"} for day_type in ("weekday", "saturday", "holiday")},
        "color": "#dcfce7",
    })
    duplicate_config = deepcopy(config)
    duplicate_config["codes"].append(deepcopy(config["codes"][-1]))
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config",
        json={"large_config": duplicate_config},
    ).status_code == 400
    configured = client.put(f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config})
    assert configured.status_code == 200

    saved = client.put(
        f"{BASE}/api/project/{project_id}/month/2026/7",
        json={
            "base_month": {"revision": 1},
            "entries_per_day": {
                "1": [
                    {"member_id": "m1", "value": "A", "comment": "旧"},
                    {"member_id": "m1", "value": "A", "comment": "確定", "holiday_kind": "legal", "assignments": [
                        {"code_key": "A", "source_type": "local"},
                        {"code_key": "B", "source_type": "scene", "source_project_id": "scene-2", "source_project_title": "別現場"},
                    ]},
                ]
            },
            "meta_data": {"day_types": {"1": "holiday"}, "day_notes": {"1": "朝礼"}},
        },
    )
    assert saved.status_code == 200
    month = saved.get_json()["month"]
    assert month["revision"] == 2
    assert len(month["entries_per_day"]["1"]) == 1
    assert month["entries_per_day"]["1"][0]["employee_number"] == "1001"
    assert [item["code_key"] for item in month["entries_per_day"]["1"][0]["assignments"]] == ["A", "B"]
    assert month["meta_data"]["day_notes"] == {"1": "朝礼"}

    worktime = client.get(f"{BASE}/api/project/{project_id}/month/2026/7/worktime")
    assert worktime.status_code == 200
    totals = worktime.get_json()["result"]["people"][0]["totals"]
    assert totals["bind_total_minutes"] == 540
    assert totals["legal_holiday_work_minutes"] == 480

    baseline = client.post(f"{BASE}/api/project/{project_id}/month/2026/7/baseline", json={})
    assert baseline.status_code == 200
    assert baseline.get_json()["project"]["month"]["meta_data"]["baseline"]["revision"] == 2

    without_member = deepcopy(config)
    without_member["members"] = []
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config", json={"large_config": without_member}
    ).status_code == 409
    without_code = deepcopy(config)
    without_code["codes"] = [code for code in without_code["codes"] if code["key"] != "A"]
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config", json={"large_config": without_code}
    ).status_code == 409
    without_second_code = deepcopy(config)
    without_second_code["codes"] = [code for code in without_second_code["codes"] if code["key"] != "B"]
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config", json={"large_config": without_second_code}
    ).status_code == 200

    view_token = _token(project["urls"]["view_url"])
    public_detail = client.get(f"{BASE}/api/public/view/{view_token}")
    assert public_detail.status_code == 200
    assert public_detail.get_json()["project"]["large_config"]["members"][0]["id"] == "m1"
    assert client.get(f"{BASE}/api/public/view/{view_token}/month/2026/7/worktime").status_code == 200

    edit_token = _token(project["urls"]["edit_url"])
    pwa_token = _token(project["urls"]["pwa_url"])
    assert client.get(f"{BASE}/api/public/edit/{edit_token}").status_code == 200
    assert client.get(f"{BASE}/api/public/pwa/{pwa_token}").status_code == 200
    assert client.get(f"{BASE}/api/public/edit/{edit_token}/month/2026/7/worktime").status_code == 200
    assert client.get(f"{BASE}/api/public/pwa/{pwa_token}/month/2026/7/worktime").status_code == 200

    draft_entries = deepcopy(month["entries_per_day"])
    draft_entries["2"] = [{"member_id": "m1", "value": "A", "comment": "draft"}]
    draft = client.put(
        f"{BASE}/api/project/{project_id}/month/2026/7/draft",
        json={"entries_per_day": draft_entries},
    )
    assert draft.status_code == 200
    assert draft.get_json()["month"]["entries_per_day"]["2"] == []
    assert draft.get_json()["month"]["draft_entries_per_day"]["2"][0]["comment"] == "draft"
    assert client.get(f"{BASE}/api/public/view/{view_token}").get_json()["month"]["entries_per_day"]["2"] == []

    published = client.post(f"{BASE}/api/project/{project_id}/month/2026/7/draft/publish", json={})
    assert published.status_code == 200
    published_month = published.get_json()["month"]
    assert published_month["revision"] == 3
    assert published_month["entries_per_day"]["2"][0]["comment"] == "draft"

    public_entries = deepcopy(published_month["entries_per_day"])
    public_entries["3"] = [{"member_id": "m1", "value": "A", "comment": "public edit"}]
    public_saved = client.put(
        f"{BASE}/api/public/edit/{edit_token}/month/2026/7",
        json={
            "editor_name": "共有編集者",
            "base_month": {"revision": 3},
            "entries_per_day": public_entries,
            "meta_data": {
                "day_types": {"1": "holiday"},
                "day_notes": {"1": "朝礼"},
                "baseline": {"set_by": "injected", "entries_per_day": {}},
            },
        },
    )
    assert public_saved.status_code == 200
    public_month = public_saved.get_json()["month"]
    assert public_month["revision"] == 4
    assert public_month["entries_per_day"]["3"][0]["comment"] == "public edit"
    assert public_month["meta_data"]["baseline"]["set_by"] != "injected"
    assert client.put(
        f"{BASE}/api/public/edit/{edit_token}/month/2026/7",
        json={"editor_name": "共有編集者", "base_month": {"revision": 3}, "entries_per_day": public_entries},
    ).status_code == 409

    restored = client.post(
        f"{BASE}/api/project/{project_id}/month/2026/7/restore", json={"revision": 2}
    )
    assert restored.status_code == 200
    restored_month = restored.get_json()["month"]
    assert restored_month["entries_per_day"]["2"] == []
    assert restored_month["meta_data"]["baseline"]["revision"] == 2

    for kind, content_type in (("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
        exported = client.get(f"{BASE}/api/project/{project_id}/export/{kind}?month_key=2026-07&highlight=1")
        assert exported.status_code == 200
        assert content_type in exported.content_type
        assert len(exported.data) > 1000
    assert client.get(f"{BASE}/api/project/{project_id}/export/csv?month_key=2026-07").status_code == 400

    assert client.get(f"{BASE}/pwa/{pwa_token}/calendar.ics").status_code == 400
    assert client.get(f"{BASE}/api/project/{project_id}/assist").status_code == 400

    cleared = client.post(
        f"{BASE}/api/project/{project_id}/month/2026/7/baseline", json={"clear": True}
    )
    assert cleared.status_code == 200
    assert "baseline" not in cleared.get_json()["month"]["meta_data"]
    actions = [item["action"] for item in client.get(f"{BASE}/api/project/{project_id}/history").get_json()["history"]]
    assert "large_config_update" in actions
    assert "large_baseline_set" in actions
    assert "large_baseline_clear" in actions


def test_opening_large_project_catches_up_an_unsynced_saved_month(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    with client.application.app_context():
        site = Site(
            site_id="LS002",
            site_name="Catch-up site",
            site_manager_last="Manager",
            site_manager_first="One",
            site_manager_id="M-LS2",
            site_register="tester",
            site_updater="tester",
            office_code="LS",
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        site_row_id = site.id

    person = client.post(
        f"{BASE}/api/create",
        data={
            "title": "Regular A",
            "mode": "person",
            "employee_number": "1001",
            "year": "2026",
            "month": "7",
        },
    ).get_json()["project"]
    scene = client.post(
        f"{BASE}/api/create",
        data={
            "title": "Catch-up scene",
            "mode": "scene",
            "site_row_id": str(site_row_id),
            "year": "2026",
            "month": "7",
        },
    ).get_json()["project"]
    large = client.post(
        f"{BASE}/api/create",
        data={
            "title": "Catch-up large",
            "mode": "large",
            "site_row_id": str(site_row_id),
            "year": "2026",
            "month": "7",
        },
    ).get_json()["project"]

    config = default_large_config()
    config["members"] = [{
        "id": "regular-a",
        "display_name": "Regular A",
        "column_type": "regular",
        "employee_number": "1001",
        "employee_name": "Regular A",
        "active": True,
        "order": 10,
    }]
    config["codes"].append({
        "key": "E",
        "label": "Early",
        "category": "work",
        "active": True,
        "order": 10,
        "times": {
            kind: {"start": "09:00", "end": "18:00"}
            for kind in ("weekday", "saturday", "holiday")
        },
        "color": "#dbeafe",
    })
    assert client.put(
        f"{BASE}/api/project/{large['project']['id']}/large-config",
        json={"large_config": config},
    ).status_code == 200

    # Simulate data saved by the pre-sync server: persist the large entry without
    # invoking the post-save resync hook.
    with client.application.app_context():
        large_project = module._load_project(large["project"]["id"])
        large_month = large_project["months"]["2026-07"]
        large_month["entries_per_day"] = module._normalize_project_entries(
            large_project,
            {
                "1": [{
                    "member_id": "regular-a",
                    "assignments": [{"code_key": "E", "source_type": "local"}],
                }],
            },
            2026,
            7,
        )
        large_month["draft_entries_per_day"] = deepcopy(large_month["entries_per_day"])
        module._save_project(large_project)

    before = client.get(
        f"{BASE}/api/project/{person['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    assert before["entries_per_day"]["1"] == []

    assert client.get(
        f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07"
    ).status_code == 200

    person_after = client.get(
        f"{BASE}/api/project/{person['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    scene_after = client.get(
        f"{BASE}/api/project/{scene['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    assert person_after["entries_per_day"]["1"][0]["sync_source_type"] == "large_shift"
    assert scene_after["entries_per_day"]["1"][0]["sync_source_type"] == "large_shift"


def test_large_project_links_site_and_syncs_regular_and_substitute_people_both_ways(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    with client.application.app_context():
        site = Site(
            site_id="LS001",
            site_name="大規模第一現場",
            site_manager_last="責任者",
            site_manager_first="一郎",
            site_manager_id="M-LS",
            site_register="tester",
            site_updater="tester",
            office_code="LS",
            is_active=True,
        )
        other_site = Site(
            site_id="OS001",
            site_name="別現場",
            site_manager_last="責任者",
            site_manager_first="二郎",
            site_manager_id="M-OS",
            site_register="tester",
            site_updater="tester",
            office_code="OS",
            is_active=True,
        )
        db.session.add_all([site, other_site])
        db.session.commit()
        site_row_id = site.id
        other_site_row_id = other_site.id

    person_a = client.post(
        f"{BASE}/api/create",
        data={"title": "職員A", "mode": "person", "employee_number": "1001", "year": "2026", "month": "7"},
    ).get_json()["project"]
    person_b = client.post(
        f"{BASE}/api/create",
        data={"title": "代務B", "mode": "person", "employee_number": "2002", "year": "2026", "month": "7"},
    ).get_json()["project"]
    scene = client.post(
        f"{BASE}/api/create",
        data={"title": "第一現場", "mode": "scene", "site_row_id": str(site_row_id), "year": "2026", "month": "7"},
    ).get_json()["project"]
    other_scene = client.post(
        f"{BASE}/api/create",
        data={"title": "別現場", "mode": "scene", "site_row_id": str(other_site_row_id), "year": "2026", "month": "7"},
    ).get_json()["project"]
    large = client.post(
        f"{BASE}/api/create",
        data={"title": "大規模第一現場", "mode": "large", "site_row_id": str(site_row_id), "year": "2026", "month": "7"},
    ).get_json()["project"]
    assert large["project"]["site"]["site_row_id"] == site_row_id

    config = default_large_config()
    config["members"] = [
        {
            "id": "regular-a", "display_name": "職員A", "column_type": "regular",
            "employee_number": "1001", "employee_name": "職員A", "active": True, "order": 10,
        },
        {
            "id": "sub-1", "display_name": "代務1", "column_type": "substitute",
            "employee_number": "", "employee_name": "", "active": True, "order": 20,
        },
    ]
    for key, label, order in (("E", "早番", 10), ("L", "遅番", 20)):
        config["codes"].append({
            "key": key, "label": label, "category": "work", "active": True, "order": order,
            "times": {kind: {"start": "09:00", "end": "18:00"} for kind in ("weekday", "saturday", "holiday")},
            "color": "#dbeafe",
        })
    assert client.put(
        f"{BASE}/api/project/{large['project']['id']}/large-config",
        json={"large_config": config},
    ).status_code == 200

    saved_large = client.put(
        f"{BASE}/api/project/{large['project']['id']}/month/2026/7",
        json={
            "base_month": {"revision": 1},
            "entries_per_day": {
                "1": [
                    {"member_id": "regular-a", "assignments": [{"code_key": "E", "source_type": "local"}]},
                    {
                        "member_id": "sub-1", "employee_number": "2002", "employee_name": "代務B",
                        "assignments": [{"code_key": "L", "source_type": "local"}],
                    },
                ],
            },
        },
    )
    assert saved_large.status_code == 200

    person_a_detail = client.get(f"{BASE}/api/project/{person_a['project']['id']}?month_key=2026-07").get_json()
    person_b_detail = client.get(f"{BASE}/api/project/{person_b['project']['id']}?month_key=2026-07").get_json()
    scene_detail = client.get(f"{BASE}/api/project/{scene['project']['id']}?month_key=2026-07").get_json()
    assert person_a_detail["month"]["entries_per_day"]["1"][0]["value"] == "!E!大規模第一現場"
    assert person_b_detail["month"]["entries_per_day"]["1"][0]["value"] == "!L!大規模第一現場"
    assert {
        (entry["employee_number"], entry["value"])
        for entry in scene_detail["month"]["entries_per_day"]["1"]
    } == {("1001", "!E!職員A"), ("2002", "!L!代務B")}

    person_month = person_a_detail["month"]
    person_entries = deepcopy(person_month["entries_per_day"])
    person_entries["2"] = [{
        "id": "person-a-day-2",
        "value": "!P!別現場",
        "site_row_id": str(other_site_row_id),
        "site_id": "OS001",
        "site_name": "別現場",
    }]
    assert client.put(
        f"{BASE}/api/project/{person_a['project']['id']}/month/2026/7",
        json={"base_month": person_month, "entries_per_day": person_entries, "required_capacity": 0},
    ).status_code == 200
    large_detail = client.get(f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07").get_json()
    synced = large_detail["month"]["entries_per_day"]["2"][0]
    assert synced["member_id"] == "regular-a"
    assert synced["assignments"][0]["source_type"] == "sync"
    assert synced["assignments"][0]["code_key"] == "P"
    assert synced["assignments"][0]["source_site_name"] == "別現場"

    other_scene_detail = client.get(
        f"{BASE}/api/project/{other_scene['project']['id']}?month_key=2026-07"
    ).get_json()
    other_scene_entries = deepcopy(other_scene_detail["month"]["entries_per_day"])
    other_scene_entries["3"] = [{
        "id": "other-scene-day-3",
        "value": "!O!職員A",
        "employee_number": "1001",
        "employee_name": "職員A",
    }]
    assert client.put(
        f"{BASE}/api/project/{other_scene['project']['id']}/month/2026/7",
        json={
            "base_month": other_scene_detail["month"],
            "entries_per_day": other_scene_entries,
            "required_capacity": 0,
        },
    ).status_code == 200
    large_after_other_scene = client.get(
        f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    other_scene_sync = large_after_other_scene["entries_per_day"]["3"][0]
    assert other_scene_sync["member_id"] == "regular-a"
    assert other_scene_sync["assignments"][0]["source_type"] == "sync"
    assert other_scene_sync["assignments"][0]["code_key"] == "O"
    assert other_scene_sync["assignments"][0]["option_key"] == "O"
    assert other_scene_sync["assignments"][0]["custom_label"] == "大型"
    assert other_scene_sync["assignments"][0]["source_site_name"] == "別現場"

    primary_scene_detail = client.get(
        f"{BASE}/api/project/{scene['project']['id']}?month_key=2026-07"
    ).get_json()
    primary_scene_entries = deepcopy(primary_scene_detail["month"]["entries_per_day"])
    primary_scene_entries["4"] = [{
        "id": "primary-scene-substitute-day-4",
        "value": "!S!代務C",
        "employee_number": "3003",
        "employee_name": "代務C",
    }]
    assert client.put(
        f"{BASE}/api/project/{scene['project']['id']}/month/2026/7",
        json={
            "base_month": primary_scene_detail["month"],
            "entries_per_day": primary_scene_entries,
            "required_capacity": 0,
        },
    ).status_code == 200
    large_after_substitute = client.get(
        f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    substitute_sync = large_after_substitute["entries_per_day"]["4"][0]
    assert substitute_sync["member_id"] == "sub-1"
    assert substitute_sync["employee_number"] == "3003"
    assert substitute_sync["employee_name"] == "代務C"
    assert substitute_sync["assignments"][0]["source_type"] == "sync"

    large_detail = client.get(
        f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07"
    ).get_json()
    stripped_entries = deepcopy(large_detail["month"]["entries_per_day"])
    stripped_entries["2"] = []
    preserved = client.put(
        f"{BASE}/api/project/{large['project']['id']}/month/2026/7",
        json={
            "base_month": {"revision": large_detail["month"]["revision"]},
            "entries_per_day": stripped_entries,
        },
    )
    assert preserved.status_code == 200
    assert preserved.get_json()["month"]["entries_per_day"]["2"][0]["assignments"][0]["source_type"] == "sync"

    person_after = client.get(
        f"{BASE}/api/project/{person_a['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    person_without_local_day_two = deepcopy(person_after["entries_per_day"])
    person_without_local_day_two["2"] = []
    assert client.put(
        f"{BASE}/api/project/{person_a['project']['id']}/month/2026/7",
        json={
            "base_month": person_after,
            "entries_per_day": person_without_local_day_two,
            "required_capacity": 0,
        },
    ).status_code == 200
    large_after_removal = client.get(
        f"{BASE}/api/project/{large['project']['id']}?month_key=2026-07"
    ).get_json()["month"]
    assert large_after_removal["entries_per_day"]["2"] == []
