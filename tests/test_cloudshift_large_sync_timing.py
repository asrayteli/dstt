"""大規模シフトの同期タイミング（開いたときに取り込む設計）の回帰テスト。

設計:
- 大規模帳 → 他帳（個人・現場）: 大規模帳の保存・公開・設定変更の時点で押し出す。
- 他帳 → 大規模帳: 他帳の保存時には書き込まず、大規模帳を「開いたタイミング」
  （オーナー・共有ユーザー・共有URLのいずれから開いても）で表示月を取り込む。
  ソース側でエントリが消えていた場合も、開いたときに残留同期を剥がす。
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.services.cloudshift_large import default_large_config
from app.models import Site, db
from tests.test_cloudshift import _build_client, _owner, _employee_user


BASE = "/tools/shiftersync/cloudshift"
JST = timezone(timedelta(hours=9))


def _token(url):
    return Path(urlparse(url).path).name


def _add_site(client, site_id, site_name):
    with client.application.app_context():
        site = Site(
            site_id=site_id, site_name=site_name,
            site_manager_last="M", site_manager_first="1", site_manager_id=f"M-{site_id}",
            site_register="t", site_updater="t", office_code="TS", is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        return site.id


def _create(client, data):
    res = client.post(f"{BASE}/api/create", data=data)
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["project"]


def _configure_large(client, project_id, *, member_number="1001", member_name="職員A"):
    config = default_large_config()
    config["members"] = [{
        "id": "mem-a", "display_name": member_name, "column_type": "regular",
        "employee_number": member_number, "employee_name": member_name,
        "active": True, "order": 10,
    }]
    config["codes"].append({
        "key": "早番", "label": "早番", "category": "work", "active": True, "order": 10,
        "times": {kind: {"start": "07:00", "end": "16:00"} for kind in ("weekday", "saturday", "holiday")},
        "color": "#dbeafe",
    })
    res = client.put(f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config})
    assert res.status_code == 200, res.get_data(as_text=True)


def _setup(client, *, year, month):
    site_row_id = _add_site(client, "LS900", "第一営業所")
    other_row_id = _add_site(client, "OS900", "第二営業所")
    person = _create(client, {
        "title": "職員A", "mode": "person", "employee_number": "1001",
        "year": str(year), "month": str(month),
    })
    large = _create(client, {
        "title": "第一営業所 大規模", "mode": "large", "site_row_id": str(site_row_id),
        "year": str(year), "month": str(month),
    })
    _configure_large(client, large["project"]["id"])
    return person["project"], large["project"], site_row_id, other_row_id


def _save_person_other_site_entry(client, person_id, year, month, day, other_row_id):
    month_data = client.get(
        f"{BASE}/api/project/{person_id}?month_key={year:04d}-{month:02d}"
    ).get_json()["month"]
    entries = deepcopy(month_data["entries_per_day"])
    entries[str(day)] = [{
        "id": f"p{day}", "value": "!E!第二営業所",
        "site_row_id": str(other_row_id), "site_id": "OS900", "site_name": "第二営業所",
    }]
    res = client.put(
        f"{BASE}/api/project/{person_id}/month/{year}/{month}",
        json={"base_month": month_data, "entries_per_day": entries, "required_capacity": 0},
    )
    assert res.status_code == 200, res.get_data(as_text=True)


def _large_month_raw(module, client, large_id, month_key):
    with client.application.app_context():
        project = module._load_project(large_id)
        return deepcopy((project.get("months") or {}).get(month_key) or {})


def _seed_unsynced_large_entry(module, client, large_id, month_key, day):
    year, month = (int(part) for part in month_key.split("-"))
    with client.application.app_context():
        project = module._load_project(large_id)
        month_data = project["months"][month_key]
        entries = module._normalize_project_entries(
            project,
            {
                **month_data["entries_per_day"],
                str(day): [{
                    "member_id": "mem-a",
                    "assignments": [{"code_key": "早番", "source_type": "local"}],
                }],
            },
            year,
            month,
        )
        month_data["entries_per_day"] = entries
        month_data["draft_entries_per_day"] = deepcopy(entries)
        module._save_project(project)


def test_other_book_save_does_not_write_into_large_until_opened(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    _save_person_other_site_entry(client, person["id"], 2026, 8, 2, other_row_id)

    # 個人シフトの保存時点では大規模帳へ書き込まない（開いたときに取り込む設計）。
    raw = _large_month_raw(module, client, large["id"], month_key)
    assert raw["entries_per_day"].get("2", []) == []

    # 大規模帳を開くと、表示月に取り込まれる。
    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}")
    assert opened.status_code == 200
    synced = opened.get_json()["month"]["entries_per_day"]["2"][0]
    assert synced["member_id"] == "mem-a"
    assert synced["assignments"][0]["source_type"] == "sync"
    assert synced["assignments"][0]["source_site_name"] == "第二営業所"


def test_open_without_month_key_syncs_displayed_calendar_month(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    now = datetime.now(JST)
    person, large, _site_row_id, other_row_id = _setup(client, year=now.year, month=now.month)
    current_key = f"{now.year:04d}-{now.month:02d}"
    next_year, next_month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)

    # 大規模帳に翌月も作る（「最新月」と「表示される当月」が分かれる状態）。
    res = client.post(
        f"{BASE}/api/project/{large['id']}/month",
        json={"year": next_year, "month": next_month},
    )
    assert res.status_code == 200, res.get_data(as_text=True)

    _save_person_other_site_entry(client, person["id"], now.year, now.month, 3, other_row_id)

    # month_key 未指定で開く → 表示されるのは実カレンダーの当月なので、当月が同期される。
    opened = client.get(f"{BASE}/api/project/{large['id']}")
    assert opened.status_code == 200
    payload = opened.get_json()
    assert payload["active_month_key"] == current_key
    synced = payload["month"]["entries_per_day"]["3"][0]
    assert synced["assignments"][0]["source_type"] == "sync"


def test_public_edit_open_syncs_both_directions(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"
    edit_token = _token(large["urls"]["edit_url"])

    # 取り込み方向: 個人シフトの保存後、共有編集URLで開くと反映される。
    _save_person_other_site_entry(client, person["id"], 2026, 8, 5, other_row_id)
    opened = client.get(f"{BASE}/api/public/edit/{edit_token}?month_key={month_key}")
    assert opened.status_code == 200
    synced = opened.get_json()["month"]["entries_per_day"]["5"][0]
    assert synced["assignments"][0]["source_type"] == "sync"

    # 押し出し方向: 同期されないまま残った大規模帳のローカル入力も、開いた時点で個人シフトへ届く。
    _seed_unsynced_large_entry(module, client, large["id"], month_key, 7)
    assert client.get(f"{BASE}/api/public/edit/{edit_token}?month_key={month_key}").status_code == 200
    person_month = client.get(
        f"{BASE}/api/project/{person['id']}?month_key={month_key}"
    ).get_json()["month"]
    assert [entry["value"] for entry in person_month["entries_per_day"]["7"]] == ["!早番!第一営業所"]


def test_public_view_open_pulls_other_book_shifts(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"
    view_token = _token(large["urls"]["view_url"])

    _save_person_other_site_entry(client, person["id"], 2026, 8, 6, other_row_id)
    opened = client.get(f"{BASE}/api/public/view/{view_token}?month_key={month_key}")
    assert opened.status_code == 200
    synced = opened.get_json()["month"]["entries_per_day"]["6"][0]
    assert synced["assignments"][0]["source_type"] == "sync"


def test_open_strips_stale_sync_after_source_entry_removed(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    _save_person_other_site_entry(client, person["id"], 2026, 8, 20, other_row_id)
    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}")
    assert opened.get_json()["month"]["entries_per_day"]["20"], "前提: 開いた時点で同期される"

    # ソース側のエントリが（同期の押し出しを経ずに）消えた状態を作る。
    with client.application.app_context():
        source = module._load_project(person["id"])
        source_month = source["months"][month_key]
        source_month["entries_per_day"]["20"] = []
        source_month["draft_entries_per_day"]["20"] = []
        module._save_project(source)

    # 開いたときの取り込みで、残留した同期割当が剥がされる。
    reopened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}")
    assert reopened.get_json()["month"]["entries_per_day"]["20"] == []


def test_shared_viewer_open_triggers_catch_up(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    _save_person_other_site_entry(client, person["id"], 2026, 8, 9, other_row_id)
    with client.application.app_context():
        project = module._load_project(large["id"])
        project["account_shares"] = {"employees": [{"employee_number": "2001", "name": "共有ユーザー"}]}
        module._save_project(project)

    module.current_user = _employee_user("2001")
    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}")
    assert opened.status_code == 200
    payload = opened.get_json()
    assert payload["access_role"] == "viewer"
    synced = payload["month"]["entries_per_day"]["9"][0]
    assert synced["assignments"][0]["source_type"] == "sync"


def test_open_survives_legacy_rows_with_unknown_member(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, _other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    # 過去データ相当: 設定から消えたメンバーIDの行 + 正常なローカル入力を直接保存する。
    _seed_unsynced_large_entry(module, client, large["id"], month_key, 12)
    with client.application.app_context():
        project = module._load_project(large["id"])
        month_data = project["months"][month_key]
        month_data["entries_per_day"]["13"] = [{
            "id": "legacy13", "member_id": "ghost-member", "value": "早番",
            "assignments": [{"id": "la13", "code_key": "早番", "source_type": "local"}],
            "holiday_kind": "", "time_override": None, "bind_override_minutes": None,
            "comment": "", "employee_number": "", "employee_name": "",
        }]
        module._save_project(project)

    # 不正な行があっても開けるし、正常な行の押し出しは実行される。
    assert client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}").status_code == 200
    person_month = client.get(
        f"{BASE}/api/project/{person['id']}?month_key={month_key}"
    ).get_json()["month"]
    assert [entry["value"] for entry in person_month["entries_per_day"]["12"]] == ["!早番!第一営業所"]


def test_month_copy_on_large_does_not_duplicate_sync_assignments(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    # day2: 個人シフト由来の同期割当 / day3: ローカル入力＋手入力の「他現場」割当
    _save_person_other_site_entry(client, person["id"], 2026, 8, 2, other_row_id)
    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}").get_json()["month"]
    entries = deepcopy(opened["entries_per_day"])
    entries["3"] = [{"member_id": "mem-a", "assignments": [
        {"code_key": "早番", "source_type": "local"},
        {"code_key": "E", "source_type": "scene", "source_site_name": "第二営業所", "custom_label": "応援"},
    ]}]
    assert client.put(
        f"{BASE}/api/project/{large['id']}/month/2026/8",
        json={"base_month": {"revision": opened["revision"]}, "entries_per_day": entries},
    ).status_code == 200

    copied = client.post(
        f"{BASE}/api/project/{large['id']}/month",
        json={"year": 2026, "month": 9, "init_mode": "copy", "source_month_key": month_key},
    )
    assert copied.status_code == 200, copied.get_data(as_text=True)
    with client.application.app_context():
        project = module._load_project(large["id"])
        new_month = project["months"]["2026-09"]
    # 同期割当は月キーが合わず剥がせない「ゾンビ」になるためコピーしない。
    # ローカル割当と手入力の「他現場」割当（ユーザー入力）は残す。
    assert new_month["entries_per_day"].get("2", []) == []
    day3 = new_month["entries_per_day"]["3"][0]
    assert [item["source_type"] for item in day3["assignments"]] == ["local", "scene"]


def test_open_prunes_orphan_sync_assignments(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    _person, large, _site_row_id, _other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    with client.application.app_context():
        project = module._load_project(large["id"])
        month_data = project["months"][month_key]
        month_data["entries_per_day"]["15"] = [{
            "id": "ce_20260815_mem-a", "member_id": "mem-a", "value": "X",
            "assignments": [{
                "id": "zombie1", "code_key": "X", "source_type": "sync",
                "sync_source_type": "person_shift",
                "sync_source_project_id": "deleted-project-id",
                "sync_source_month_key": "2026-07",  # 月キーも不一致
                "sync_source_day": "15", "sync_source_entry_id": "gone",
            }],
            "holiday_kind": "", "time_override": None, "bind_override_minutes": None,
            "comment": "", "employee_number": "", "employee_name": "",
        }]
        module._save_project(project)

    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}")
    assert opened.status_code == 200
    assert opened.get_json()["month"]["entries_per_day"]["15"] == []


def test_large_leave_code_pushes_mapped_leave_to_person_book(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, _other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    month_data = client.get(
        f"{BASE}/api/project/{large['id']}?month_key={month_key}"
    ).get_json()["month"]
    entries = deepcopy(month_data["entries_per_day"])
    entries["4"] = [{
        "member_id": "mem-a",
        "assignments": [{"code_key": "有休", "source_type": "local"}],
    }]
    res = client.put(
        f"{BASE}/api/project/{large['id']}/month/2026/8",
        json={"base_month": {"revision": month_data["revision"]}, "entries_per_day": entries},
    )
    assert res.status_code == 200, res.get_data(as_text=True)

    person_month = client.get(
        f"{BASE}/api/project/{person['id']}?month_key={month_key}"
    ).get_json()["month"]
    # 休みコードは leave_kind に応じた既存 CloudShift の休暇オプションへ変換される。
    assert [entry["value"] for entry in person_month["entries_per_day"]["4"]] == ["!PAID!第一営業所"]
    # 現場シフト帳へは勤務コードだけを送る（休みは送らない）ため、同期対象は個人のみ。
    scene_like = [
        entry for entry in person_month["entries_per_day"]["4"]
        if entry.get("sync_source_type") != "large_shift"
    ]
    assert scene_like == []


def test_time_override_makes_synced_only_cell_count_in_worktime(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    person, large, _site_row_id, other_row_id = _setup(client, year=2026, month=8)
    month_key = "2026-08"

    _save_person_other_site_entry(client, person["id"], 2026, 8, 2, other_row_id)
    opened = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}").get_json()["month"]
    assert opened["entries_per_day"]["2"][0]["assignments"][0]["source_type"] == "sync"

    # 同期のみのセルは時刻上書きが無い間は集計されない。
    before = client.get(f"{BASE}/api/project/{large['id']}/month/2026/8/worktime").get_json()["result"]
    day2_before = before["people"][0]["days"][1]
    assert (day2_before["category"], day2_before["bind_minutes"]) == ("external", 0)

    # 大規模帳から時刻を設定すると、その時間で拘束・労働が集計される。
    entries = deepcopy(opened["entries_per_day"])
    entries["2"][0]["time_override"] = {"start": "08:00", "end": "17:30"}
    res = client.put(
        f"{BASE}/api/project/{large['id']}/month/2026/8",
        json={"base_month": {"revision": opened["revision"]}, "entries_per_day": entries},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    after = client.get(f"{BASE}/api/project/{large['id']}/month/2026/8/worktime").get_json()["result"]
    day2 = after["people"][0]["days"][1]
    assert (day2["category"], day2["bind_minutes"], day2["work_minutes"]) == ("work", 570, 510)
    assert (day2["start"], day2["end"]) == ("8:00", "17:30")

    # 上書きは保存後も同期割当と共存して保持される。
    saved = client.get(f"{BASE}/api/project/{large['id']}?month_key={month_key}").get_json()["month"]
    entry = saved["entries_per_day"]["2"][0]
    assert entry["time_override"] == {"start": "08:00", "end": "17:30"}
    assert entry["assignments"][0]["source_type"] == "sync"
