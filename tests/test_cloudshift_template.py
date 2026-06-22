import sys
from datetime import date
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

from app.models import db


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_template_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(tmp_path):
    module = _load_cloudshift_module()
    app = Flask(
        __name__,
        root_path=str(tmp_path),
        instance_path=str(tmp_path / "instance"),
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
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


def _create_project(client, *, mode="scene", year=2026, month=4, title="Tpl Team"):
    response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": title, "mode": mode, "year": str(year), "month": str(month)},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["project"]["project"]["id"]


def _create_month(client, project_id, year, month):
    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month",
        json={"year": year, "month": month, "init_mode": "blank"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _create_template(client, project_id, payload):
    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates",
        json=payload,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["template"]


def _get_month_entries(client, project_id, month_key):
    response = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}?month_key={month_key}"
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    inner = payload["project"] if "project" in payload and "month" in payload["project"] else payload
    return inner["month"]["entries_per_day"]


def _values(entries_for_day):
    return [entry.get("value") for entry in (entries_for_day or [])]


def test_create_list_get_and_delete_template(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)

    template = _create_template(
        client,
        project_id,
        {
            "name": "通常パターン",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {
                "1": [{"value": "A"}],
                "2": [{"value": "B"}, {"value": "C"}],
            },
            "options": {"apply_mode": "overwrite", "target_filter": "all"},
        },
    )
    assert template["name"] == "通常パターン"
    assert template["basis"] == "date"
    assert template["filled_day_count"] == 2
    assert template["entry_count"] == 3
    assert template["mode"] == "scene"

    list_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates")
    assert list_response.status_code == 200
    templates = list_response.get_json()["templates"]
    assert len(templates) == 1
    assert templates[0]["id"] == template["id"]

    detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}"
    )
    assert detail.status_code == 200
    slots = detail.get_json()["template"]["slots"]
    assert _values(slots["1"]) == ["A"]
    assert _values(slots["2"]) == ["B", "C"]

    delete_response = client.delete(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}"
    )
    assert delete_response.status_code == 200
    after = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates")
    assert after.get_json()["templates"] == []


def test_apply_template_date_basis_overwrite(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 5)

    template = _create_template(
        client,
        project_id,
        {
            "name": "日付固定",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "X"}], "10": [{"value": "Y"}]},
            "options": {"apply_mode": "overwrite"},
        },
    )

    apply_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "date", "apply_mode": "overwrite"},
    )
    assert apply_response.status_code == 200, apply_response.get_data(as_text=True)
    assert apply_response.get_json()["applied_days"] == 2

    entries = _get_month_entries(client, project_id, "2026-05")
    assert _values(entries["1"]) == ["X"]
    assert _values(entries["10"]) == ["Y"]
    assert _values(entries.get("2")) == []
    # 反映で採番された id は重複しない実在の id である。
    assert entries["1"][0]["id"]
    assert entries["1"][0]["id"] != entries["10"][0]["id"]


def test_apply_template_weekday_basis(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 5)

    # 代表月(2026-04)の最初の月曜にだけエントリを置く。
    first_monday = next(day for day in range(1, 31) if date(2026, 4, day).weekday() == 0)
    template = _create_template(
        client,
        project_id,
        {
            "name": "毎週月曜",
            "basis": "weekday",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {str(first_monday): [{"value": "MON"}]},
            "options": {"apply_mode": "overwrite", "holiday_mode": "as_weekday"},
        },
    )

    apply_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "weekday", "holiday_mode": "as_weekday"},
    )
    assert apply_response.status_code == 200, apply_response.get_data(as_text=True)

    entries = _get_month_entries(client, project_id, "2026-05")
    import calendar as _calendar

    days_in_may = _calendar.monthrange(2026, 5)[1]
    for day in range(1, days_in_may + 1):
        expected = ["MON"] if date(2026, 5, day).weekday() == 0 else []
        assert _values(entries.get(str(day))) == expected, f"day {day}"


def test_apply_modes_append_and_fill_empty(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 6)

    # 対象月の1日に既存予定を入れておく。
    save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/6",
        json={
            "base_month": {"year": 2026, "month": 6, "required_capacity": 0, "entries_per_day": {}},
            "required_capacity": 0,
            "entries_per_day": {"1": [{"value": "EXIST"}]},
        },
    )
    assert save.status_code == 200, save.get_data(as_text=True)

    template = _create_template(
        client,
        project_id,
        {
            "name": "追加用",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "NEW"}], "2": [{"value": "NEW2"}]},
        },
    )

    # append: 既存に足す
    append_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 6, "basis": "date", "apply_mode": "append"},
    )
    assert append_response.status_code == 200, append_response.get_data(as_text=True)
    entries = _get_month_entries(client, project_id, "2026-06")
    assert _values(entries["1"]) == ["EXIST", "NEW"]
    assert _values(entries["2"]) == ["NEW2"]

    # fill_empty: 予定がある日(1日)は触らず、空き日(2日は既にNEW2が入った)も既存維持
    fill_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 6, "basis": "date", "apply_mode": "fill_empty"},
    )
    assert fill_response.status_code == 200
    entries = _get_month_entries(client, project_id, "2026-06")
    assert _values(entries["1"]) == ["EXIST", "NEW"]  # 変化なし
    assert _values(entries["2"]) == ["NEW2"]  # 変化なし


def test_apply_target_filter_weekday_only(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 5)

    template = _create_template(
        client,
        project_id,
        {
            "name": "全日同じ",
            "basis": "weekday",
            "representative_year": 2026,
            "representative_month": 4,
            # 代表月の全日に共通エントリ → 全曜日のパターンが埋まる
            "entries_per_day": {str(day): [{"value": "WORK"}] for day in range(1, 31)},
        },
    )

    apply_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "weekday", "target_filter": "weekday"},
    )
    assert apply_response.status_code == 200, apply_response.get_data(as_text=True)

    entries = _get_month_entries(client, project_id, "2026-05")
    holidays = module._project_holiday_day_set(2026, 5)
    import calendar as _calendar

    for day in range(1, _calendar.monthrange(2026, 5)[1] + 1):
        weekday = date(2026, 5, day).weekday()
        is_business_day = weekday < 5 and day not in holidays
        expected = ["WORK"] if is_business_day else []
        assert _values(entries.get(str(day))) == expected, f"day {day}"


def test_update_template(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)
    template = _create_template(
        client,
        project_id,
        {
            "name": "旧名",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}]},
        },
    )

    update = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}",
        json={
            "name": "新名",
            "basis": "weekday",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}], "2": [{"value": "B"}]},
            "options": {"apply_mode": "append"},
        },
    )
    assert update.status_code == 200, update.get_data(as_text=True)
    updated = update.get_json()["template"]
    assert updated["name"] == "新名"
    assert updated["basis"] == "weekday"
    assert updated["filled_day_count"] == 2
    assert updated["options"]["apply_mode"] == "append"


def test_person_mode_template(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, mode="person", title="Person Book")
    _create_month(client, project_id, 2026, 5)

    template = _create_template(
        client,
        project_id,
        {
            "name": "個人テンプレ",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"3": [{"value": "現場X"}]},
        },
    )
    assert template["mode"] == "person"

    apply_response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "date"},
    )
    assert apply_response.status_code == 200, apply_response.get_data(as_text=True)
    entries = _get_month_entries(client, project_id, "2026-05")
    assert _values(entries["3"]) == ["現場X"]


def test_template_access_control_other_owner_blocked(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)
    template = _create_template(
        client,
        project_id,
        {
            "name": "秘密",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}]},
        },
    )

    module.current_user = _other_owner()
    blocked = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates")
    assert blocked.status_code == 404
    blocked_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}"
    )
    assert blocked_detail.status_code == 404


def test_template_deleted_with_project(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)
    template = _create_template(
        client,
        project_id,
        {
            "name": "消える",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}]},
        },
    )
    with client.application.app_context():
        assert db.session.get(module.CloudShiftTemplate, template["id"]) is not None

    delete = client.delete(f"/tools/shiftersync/cloudshift/api/project/{project_id}")
    assert delete.status_code == 200
    with client.application.app_context():
        assert db.session.get(module.CloudShiftTemplate, template["id"]) is None


def test_template_editor_page_renders(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)

    page = client.get(f"/tools/shiftersync/cloudshift/project/{project_id}/template-editor")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "CS_TEMPLATE_EDITOR" in html
    assert "cloudshift_template_editor.js" in html
    assert "shiftGrid" in html


def test_apply_weekday_holiday_modes(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 5)

    # 代表月(2026-04)で各曜日の初出日に「WD<曜日>」を置く。
    entries = {}
    seen = set()
    for day in range(1, 31):
        weekday = date(2026, 4, day).weekday()
        if weekday in seen:
            continue
        seen.add(weekday)
        entries[str(day)] = [{"value": f"WD{weekday}"}]
    template = _create_template(
        client,
        project_id,
        {
            "name": "曜日別",
            "basis": "weekday",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": entries,
        },
    )

    holidays = module._project_holiday_day_set(2026, 5)
    assert 4 in holidays  # 2026-05-04 (月) は祝日

    # as_weekday: 祝日でも実際の曜日として扱う → 5/4(月) は WD0
    client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "weekday", "holiday_mode": "as_weekday"},
    )
    entries_may = _get_month_entries(client, project_id, "2026-05")
    assert _values(entries_may["4"]) == ["WD0"]

    # as_sunday: 祝日は日曜パターン → 5/4(月・祝) は WD6
    client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "weekday", "holiday_mode": "as_sunday"},
    )
    entries_may = _get_month_entries(client, project_id, "2026-05")
    assert _values(entries_may["4"]) == ["WD6"]
    # 平日(祝日でない木曜など)は実曜日のまま
    first_thursday = next(day for day in range(1, 32) if date(2026, 5, day).weekday() == 3)
    assert _values(entries_may[str(first_thursday)]) == ["WD3"]

    # skip: 祝日は触らない → 一度クリアしてから skip 適用しても祝日は空のまま
    client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "weekday", "holiday_mode": "skip"},
    )
    entries_may = _get_month_entries(client, project_id, "2026-05")
    # 5/4 は祝日なので skip。直前の as_sunday 適用結果(WD6)が温存される。
    assert _values(entries_may["4"]) == ["WD6"]


def test_apply_overwrite_clears_local_entries(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    _create_month(client, project_id, 2026, 5)

    # 対象月の5日に既存のローカル予定を入れる。
    client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5",
        json={
            "base_month": {"year": 2026, "month": 5, "required_capacity": 0, "entries_per_day": {}},
            "required_capacity": 0,
            "entries_per_day": {"5": [{"value": "OLD"}]},
        },
    )

    # 5日のパターンが空のテンプレートを overwrite で反映 → 5日はクリアされる。
    template = _create_template(
        client,
        project_id,
        {
            "name": "上書きクリア",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "NEW"}]},
        },
    )
    client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 5, "basis": "date", "apply_mode": "overwrite"},
    )
    entries = _get_month_entries(client, project_id, "2026-05")
    assert _values(entries["1"]) == ["NEW"]
    assert _values(entries["5"]) == []  # overwrite で空パターンに置き換わりクリア


def test_create_template_requires_name(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client)
    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates",
        json={
            "name": "   ",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}]},
        },
    )
    assert response.status_code == 400


def test_apply_requires_existing_target_month(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _create_project(client, year=2026, month=4)
    template = _create_template(
        client,
        project_id,
        {
            "name": "未作成月へ",
            "basis": "date",
            "representative_year": 2026,
            "representative_month": 4,
            "entries_per_day": {"1": [{"value": "A"}]},
        },
    )
    # 2026-12 はまだ存在しない
    response = client.post(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/templates/{template['id']}/apply",
        json={"year": 2026, "month": 12, "basis": "date"},
    )
    assert response.status_code == 404
