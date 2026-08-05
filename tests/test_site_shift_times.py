"""現場リストPLUS の勤怠時間/出勤時間と、ShifterSync/CloudShift への連携のテスト。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))

from app.models import Site, SiteBranch, db
from app.site_shift_times import (
    format_attendance_times,
    format_report_time,
    format_shift_time_label,
    normalize_attendance_times,
    normalize_time_text,
    parse_attendance_times,
    parse_time_text,
    resolve_shift_times,
)
from app.tools.shiftersync_format import (
    entry_shift_time_label,
    normalize_entry,
    parse_csv_text,
    serialize_csv_text,
)


def _load_module(name: str, relative_path: str):
    module_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _user(user_id="tester01"):
    return SimpleNamespace(is_authenticated=True, username=user_id, name="Test User")


def _build_siteplus_client(tmp_path):
    module = _load_module("siteplus_shift_times_module", "app/tools/siteplus.py")
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'siteplus_times.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.siteplus_bp)
    with app.app_context():
        db.create_all()
    module.current_user = _user()
    return module, app, app.test_client()


def _build_cloudshift_app(tmp_path):
    module = _load_module("cloudshift_shift_times_module", "app/tools/cloudshift.py")
    app = Flask(__name__, root_path=str(tmp_path), instance_path=str(tmp_path / "instance"))
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'cloudshift_times.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.cloudshift_bp)
    with app.app_context():
        db.create_all()
    module.current_user = SimpleNamespace(is_authenticated=True, username="owner01", name="Owner")
    return module, app


def _create_site(app, *, site_id, site_name, attendance_times=None, report_time=None):
    with app.app_context():
        site = Site(
            site_id=site_id,
            site_name=site_name,
            site_manager_last="Manager",
            site_manager_first="One",
            site_manager_id="9001",
            site_register="owner01",
            site_updater="owner01",
            attendance_times=attendance_times,
            report_time=report_time,
            is_active=True,
        )
        db.session.add(site)
        db.session.commit()
        return site.id


# ---------------------------------------------------------------- 正規化 / 整形


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9:40", "09:40"),
        ("09:40", "09:40"),
        ("0940", "09:40"),
        ("940", "09:40"),
        ("９：４０", "09:40"),
        ("9時40分", "09:40"),
        ("24:00", "24:00"),
        ("25:00", ""),
        ("10:70", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_time_text(raw, expected):
    assert normalize_time_text(raw) == expected


def test_normalize_attendance_times_accepts_multiple_shapes():
    expected = [{"start": "10:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}]
    assert normalize_attendance_times(expected) == expected
    assert normalize_attendance_times("10:00~13:00, 14:00~19:00") == expected
    assert normalize_attendance_times('[{"start": "10:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}]') == expected
    assert normalize_attendance_times([["10:00", "13:00"], ["14:00", "19:00"]]) == expected
    assert normalize_attendance_times(None) == []


def test_normalize_attendance_times_caps_range_count():
    many = [{"start": "01:00", "end": "02:00"}] * 10
    assert len(normalize_attendance_times(many)) == 6


def test_parse_attendance_times_rejects_incomplete_range():
    with pytest.raises(ValueError):
        parse_attendance_times([{"start": "10:00", "end": ""}])
    with pytest.raises(ValueError):
        parse_attendance_times([{"start": "abc", "end": "19:00"}])
    with pytest.raises(ValueError):
        parse_attendance_times([{"start": "01:00", "end": "02:00"}] * 7)


def test_parse_time_text_rejects_garbage_but_allows_blank():
    assert parse_time_text("", "出勤時間") == ""
    with pytest.raises(ValueError):
        parse_time_text("あさ", "出勤時間")


def test_display_formatting_drops_leading_zero_on_hour():
    assert format_report_time("09:40") == "9:40"
    assert format_report_time("10:00") == "10:00"
    assert format_attendance_times([{"start": "10:00", "end": "19:00"}]) == "10:00~19:00"
    assert (
        format_attendance_times([{"start": "09:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}])
        == "9:00~13:00 / 14:00~19:00"
    )


def test_format_shift_time_label_respects_show_flags():
    attendance = [{"start": "10:00", "end": "19:00"}]
    assert format_shift_time_label(attendance, "09:40") == "勤怠：10:00~19:00、出勤：9:40"
    assert format_shift_time_label(attendance, "09:40", show_report=False) == "勤怠：10:00~19:00"
    assert format_shift_time_label(attendance, "09:40", show_attendance=False) == "出勤：9:40"
    assert format_shift_time_label([], "", show_attendance=True, show_report=True) == ""


def test_resolve_shift_times_uses_branch_as_per_field_override():
    site = {"attendance_times": [{"start": "10:00", "end": "19:00"}], "report_time": "09:40"}
    assert resolve_shift_times(site, None) == {
        "attendance_times": [{"start": "10:00", "end": "19:00"}],
        "report_time": "09:40",
    }
    # 枝番号側に値がある項目だけ上書きされ、無い項目は親現場を引き継ぐ。
    branch = {"attendance_times": [{"start": "08:00", "end": "17:00"}], "report_time": ""}
    assert resolve_shift_times(site, branch) == {
        "attendance_times": [{"start": "08:00", "end": "17:00"}],
        "report_time": "09:40",
    }
    branch = {"attendance_times": [], "report_time": "07:30"}
    assert resolve_shift_times(site, branch) == {
        "attendance_times": [{"start": "10:00", "end": "19:00"}],
        "report_time": "07:30",
    }


# ---------------------------------------------------------------- 現場リストPLUS


def test_siteplus_site_create_and_update_round_trips_shift_times(tmp_path):
    _module, _app, client = _build_siteplus_client(tmp_path)

    created = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "123",
            "site_name": "Alpha",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "attendance_times": [{"start": "10:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}],
            "report_time": "9:40",
        },
    )
    assert created.status_code == 200
    site = created.get_json()["site"]
    assert site["attendance_times"] == [
        {"start": "10:00", "end": "13:00"},
        {"start": "14:00", "end": "19:00"},
    ]
    assert site["report_time"] == "09:40"

    # 勤怠/出勤を含まない更新は既存値を保持する（一括編集などで消えないこと）。
    updated = client.put(
        f"/tools/siteplus/api/sites/{site['id']}",
        json={
            "site_id": "123",
            "site_name": "Alpha",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9002",
            "confirm_changes": True,
            "confirm_duplicate": True,
        },
    )
    assert updated.status_code == 200
    assert updated.get_json()["site"]["report_time"] == "09:40"


def test_siteplus_rejects_invalid_shift_times(tmp_path):
    _module, _app, client = _build_siteplus_client(tmp_path)

    response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "200",
            "site_name": "Bad",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "report_time": "あさ",
        },
    )
    assert response.status_code == 400
    assert "出勤時間" in response.get_json()["error"]


def test_siteplus_site_update_preview_reports_readable_shift_time_diff(tmp_path):
    _module, _app, client = _build_siteplus_client(tmp_path)
    site = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "300",
            "site_name": "Preview",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
        },
    ).get_json()["site"]

    preview = client.put(
        f"/tools/siteplus/api/sites/{site['id']}/preview",
        json={
            "site_id": "300",
            "site_name": "Preview",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "attendance_times": [{"start": "10:00", "end": "19:00"}],
            "report_time": "9:40",
        },
    )
    assert preview.status_code == 200
    changes = {change["field"]: change for change in preview.get_json()["changes"]}
    assert changes["attendance_times"]["before"] == "未設定"
    assert changes["attendance_times"]["after"] == "10:00~19:00"
    assert changes["report_time"]["after"] == "9:40"


def test_siteplus_cloudshift_apis_expose_resolved_shift_times(tmp_path):
    _module, _app, client = _build_siteplus_client(tmp_path)
    site = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "400",
            "site_name": "Linked",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "attendance_times": [{"start": "10:00", "end": "19:00"}],
            "report_time": "9:40",
        },
    ).get_json()["site"]

    client.post(
        f"/tools/siteplus/api/sites/{site['id']}/branches",
        json={
            "site_branch": "1",
            "cloudshift_option_key": "O",
            "attendance_times": [{"start": "08:00", "end": "17:00"}],
        },
    )

    sites_payload = client.get("/tools/siteplus/api/cloudshift/sites").get_json()["sites"]
    assert sites_payload[0]["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert sites_payload[0]["report_time"] == "09:40"

    branches_payload = client.get("/tools/siteplus/api/cloudshift/sites/00400/branches").get_json()
    resolved = branches_payload["branches"][0]["resolved_shift_times"]
    assert resolved["attendance_times"] == [{"start": "08:00", "end": "17:00"}]
    # 枝番号は出勤時間を上書きしていないので親現場の値を引き継ぐ。
    assert resolved["report_time"] == "09:40"


# ---------------------------------------------------------------- entry 正規化 / CSV


def test_normalize_entry_carries_shift_time_fields():
    entry = normalize_entry(
        {
            "value": "!O!Alpha",
            "site_row_id": "3",
            "showAttendanceTime": "1",
            "show_report_time": True,
            "attendance_times": [{"start": "10:00", "end": "19:00"}],
            "reportTime": "9:40",
        }
    )
    assert entry["show_attendance_time"] is True
    assert entry["show_report_time"] is True
    assert entry["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert entry["report_time"] == "09:40"
    assert entry_shift_time_label(entry) == "勤怠：10:00~19:00、出勤：9:40"


def test_entry_shift_time_label_hides_unchecked_parts():
    entry = normalize_entry(
        {
            "value": "Alpha",
            "show_attendance_time": False,
            "show_report_time": True,
            "attendance_times": [{"start": "10:00", "end": "19:00"}],
            "report_time": "09:40",
        }
    )
    assert entry_shift_time_label(entry) == "出勤：9:40"


def test_csv_round_trip_preserves_shift_times():
    entries = {
        "1": [
            {
                "id": "entry-1",
                "value": "Alpha",
                "comment": "備考",
                "show_attendance_time": True,
                "show_report_time": True,
                "attendance_times": [{"start": "10:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}],
                "report_time": "09:40",
            }
        ]
    }
    csv_text = serialize_csv_text("person", 2026, 4, "Tester", 0, entries)
    parsed = parse_csv_text(csv_text)
    entry = parsed["entries_per_day"]["1"][0]
    assert entry["show_attendance_time"] is True
    assert entry["show_report_time"] is True
    assert entry["attendance_times"] == [
        {"start": "10:00", "end": "13:00"},
        {"start": "14:00", "end": "19:00"},
    ]
    assert entry["report_time"] == "09:40"


def test_legacy_csv_without_shift_time_rows_still_parses():
    csv_text = "person,2026,4,Tester\n日付,現場\n1,Alpha\n#comment,1,0,備考\n"
    entry = parse_csv_text(csv_text)["entries_per_day"]["1"][0]
    assert entry["show_attendance_time"] is False
    assert entry["attendance_times"] == []
    assert entry["report_time"] == ""


# ---------------------------------------------------------------- CloudShift 連携


def _create_person_project_with_entry(module, app, *, site_row_id, entry_extra):
    client = app.test_client()
    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Person Shift", "mode": "person", "year": "2026", "month": "4"},
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
                        "value": "Alpha Site",
                        "comment": "現地集合",
                        "site_row_id": str(site_row_id),
                        "site_id": "12345",
                        "site_name": "Alpha Site",
                        **entry_extra,
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200
    return client, project_id, save_response.get_json()["month"]


def test_cloudshift_person_entry_pulls_shift_times_from_site_master(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )

    _client, _project_id, month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    entry = month["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert entry["report_time"] == "09:40"
    assert entry_shift_time_label(entry) == "勤怠：10:00~19:00、出勤：9:40"


def test_cloudshift_person_entry_follows_site_master_updates(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client, project_id, _month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    with app.app_context():
        site = db.session.get(Site, site_row_id)
        site.attendance_times = [{"start": "08:00", "end": "12:00"}, {"start": "13:00", "end": "17:00"}]
        site.report_time = "07:30"
        db.session.commit()

    reloaded = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}?month_key=2026-04")
    assert reloaded.status_code == 200
    entry = reloaded.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [
        {"start": "08:00", "end": "12:00"},
        {"start": "13:00", "end": "17:00"},
    ]
    assert entry["report_time"] == "07:30"


def test_cloudshift_person_entry_keeps_snapshot_when_site_row_is_gone(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client, project_id, _month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    with app.app_context():
        db.session.delete(db.session.get(Site, site_row_id))
        db.session.commit()

    reloaded = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}?month_key=2026-04")
    assert reloaded.status_code == 200
    entry = reloaded.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert entry["report_time"] == "09:40"


def test_cloudshift_branch_override_wins_over_site(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    with app.app_context():
        branch = SiteBranch(
            site_row_id=site_row_id,
            site_branch="001",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            attendance_times=[{"start": "08:00", "end": "17:00"}],
            is_active=True,
        )
        db.session.add(branch)
        db.session.commit()
        branch_row_id = branch.id

    _client, _project_id, month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={
            "show_attendance_time": True,
            "show_report_time": True,
            "site_branch_row_id": str(branch_row_id),
            "site_branch": "001",
        },
    )

    entry = month["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "08:00", "end": "17:00"}]
    # 枝番号は出勤時間を上書きしていないので親現場を引き継ぐ。
    assert entry["report_time"] == "09:40"


def test_cloudshift_calendar_day_map_places_shift_time_next_to_comment(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    with app.app_context():
        day_map = module._calendar_day_map(
            {
                "year": 2026,
                "month": 4,
                "entries_per_day": {
                    "1": [
                        {
                            "id": "entry-1",
                            "value": "Alpha Site",
                            "comment": "現地集合",
                            "show_attendance_time": True,
                            "show_report_time": True,
                            "attendance_times": [{"start": "10:00", "end": "19:00"}],
                            "report_time": "09:40",
                        }
                    ]
                },
            }
        )
    assert day_map[1][0]["shift_time"] == "勤怠：10:00~19:00、出勤：9:40"
    assert day_map[1][0]["comment"] == "現地集合"


def test_cloudshift_shift_times_hidden_when_toggles_are_off(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    _client, _project_id, month = _create_person_project_with_entry(
        module, app, site_row_id=site_row_id, entry_extra={}
    )

    entry = month["entries_per_day"]["1"][0]
    assert entry["show_attendance_time"] is False
    assert entry["show_report_time"] is False
    assert entry_shift_time_label(entry) == ""


def test_public_view_payload_includes_resolved_shift_times(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="12345",
        site_name="Alpha Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client, project_id, _month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    detail = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}").get_json()
    view_token = detail["project"]["urls"]["view_url"].rsplit("/", 1)[-1]

    public = client.get(f"/tools/shiftersync/cloudshift/api/public/view/{view_token}?month_key=2026-04")
    assert public.status_code == 200
    entry = public.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry_shift_time_label(entry) == "勤怠：10:00~19:00、出勤：9:40"


def test_calendar_png_export_renders_with_shift_times(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    month_data = {
        "year": 2026,
        "month": 4,
        "capacity_enabled": False,
        "required_capacity": 0,
        "entries_per_day": {
            "1": [
                {
                    "id": "entry-1",
                    "value": "Alpha Site",
                    "comment": "現地集合",
                    "show_attendance_time": True,
                    "show_report_time": True,
                    "attendance_times": [{"start": "10:00", "end": "13:00"}, {"start": "14:00", "end": "19:00"}],
                    "report_time": "09:40",
                }
            ]
        },
    }
    with app.app_context():
        buffer = module._calendar_png_bytes_for_month("Person Shift", "person", month_data)
    assert buffer.getbuffer().nbytes > 0


def test_calendar_pdf_export_renders_with_shift_times(tmp_path):
    shiftersync = _load_module("shiftersync_shift_times_module", "app/tools/shiftersync.py")
    output = tmp_path / "calendar.pdf"
    day_map = {
        1: [
            {
                "title": "Alpha Site",
                "shift_time": "勤怠：10:00~19:00、出勤：9:40",
                "comment": "現地集合",
            }
        ],
        # shift_time キーを持たない従来の呼び出しでも壊れないこと。
        2: [{"title": "Beta Site", "comment": ""}],
    }
    shiftersync.generate_pdf_calendar(output, 2026, 4, "person", "Person Shift", day_map)
    assert output.exists() and output.stat().st_size > 0


# ---------------------------------------------------------------- 現場シフト


def _create_scene_project_with_entry(module, app, *, site_row_id, entry_extra):
    client = app.test_client()
    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Scene Shift",
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
                        "id": "scene-entry-1",
                        "value": "山田 太郎",
                        "comment": "点呼あり",
                        "employee_name": "山田 太郎",
                        "employee_number": "1234",
                        **entry_extra,
                    }
                ]
            },
        },
    )
    assert save_response.status_code == 200
    return client, project_id, save_response.get_json()["month"]


def test_scene_entry_pulls_shift_times_from_the_books_own_site(tmp_path):
    """現場シフトはエントリに現場リンクが無いので、帳簿の現場から時間を引く。"""
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="22222",
        site_name="Scene Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )

    _client, _project_id, month = _create_scene_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    entry = month["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert entry["report_time"] == "09:40"
    assert entry_shift_time_label(entry) == "勤怠：10:00~19:00、出勤：9:40"


def test_scene_entry_branch_overrides_the_books_site_times(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="22222",
        site_name="Scene Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    with app.app_context():
        branch = SiteBranch(
            site_row_id=site_row_id,
            site_branch="001",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            attendance_times=[{"start": "08:00", "end": "17:00"}],
            report_time="07:30",
            is_active=True,
        )
        db.session.add(branch)
        db.session.commit()
        branch_row_id = branch.id

    _client, _project_id, month = _create_scene_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={
            "show_attendance_time": True,
            "show_report_time": True,
            "site_branch_row_id": str(branch_row_id),
            "site_branch": "001",
        },
    )

    entry = month["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "08:00", "end": "17:00"}]
    assert entry["report_time"] == "07:30"


def test_scene_entry_shift_times_stay_hidden_when_toggles_are_off(tmp_path):
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="22222",
        site_name="Scene Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    _client, _project_id, month = _create_scene_project_with_entry(
        module, app, site_row_id=site_row_id, entry_extra={}
    )
    entry = month["entries_per_day"]["1"][0]
    assert entry_shift_time_label(entry) == ""


def test_scene_shift_times_reach_the_persons_own_shift_book(tmp_path):
    """現場シフトでONにした勤怠/出勤が、同期先の個人シフトにも出ること。"""
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="22222",
        site_name="Scene Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    with app.app_context():
        branch = SiteBranch(
            site_row_id=site_row_id,
            site_branch="001",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            attendance_times=[{"start": "08:00", "end": "17:00"}],
            is_active=True,
        )
        db.session.add(branch)
        db.session.commit()
        branch_row_id = branch.id

    client = app.test_client()
    person_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "山田 太郎",
            "mode": "person",
            "year": "2026",
            "month": "4",
            "employee_number": "1234",
        },
    )
    assert person_response.status_code == 200
    person_id = person_response.get_json()["project"]["project"]["id"]

    _scene_client, _scene_id, _month = _create_scene_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={
            "show_attendance_time": True,
            "show_report_time": True,
            "site_branch_row_id": str(branch_row_id),
            "site_branch": "001",
        },
    )

    person_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{person_id}?month_key=2026-04"
    )
    assert person_detail.status_code == 200
    mirrored = [
        entry
        for entry in person_detail.get_json()["month"]["entries_per_day"]["1"]
        if entry.get("sync_source_type") == "scene_shift"
    ]
    assert len(mirrored) == 1
    entry = mirrored[0]
    # 枝番号の上書きも引き継ぐが、枝番号そのものは個人シフトに表示しない。
    assert entry["attendance_times"] == [{"start": "08:00", "end": "17:00"}]
    assert entry["report_time"] == "09:40"
    assert entry["site_branch_row_id"] == ""
    assert entry_shift_time_label(entry) == "勤怠：8:00~17:00、出勤：9:40"


def test_scene_toggle_change_propagates_to_the_person_shift_later(tmp_path):
    """後からチェックを入れ直した場合も、同期先の個人シフトへ反映されること。"""
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="33333",
        site_name="Toggle Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client = app.test_client()
    person_id = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "佐藤 花子",
            "mode": "person",
            "year": "2026",
            "month": "4",
            "employee_number": "5678",
        },
    ).get_json()["project"]["project"]["id"]

    # まずチェックを入れずに現場シフトへ追加する。
    scene_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Toggle Scene",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    )
    scene_payload = scene_response.get_json()["project"]
    scene_id = scene_payload["project"]["id"]
    entry = {
        "id": "scene-entry-1",
        "value": "佐藤 花子",
        "employee_name": "佐藤 花子",
        "employee_number": "5678",
    }
    first_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": scene_payload["month"],
            "entries_per_day": {"1": [entry]},
        },
    )
    assert first_save.status_code == 200

    def person_mirror():
        detail = client.get(
            f"/tools/shiftersync/cloudshift/api/project/{person_id}?month_key=2026-04"
        ).get_json()
        return [
            item
            for item in detail["month"]["entries_per_day"]["1"]
            if item.get("sync_source_type") == "scene_shift"
        ]

    assert entry_shift_time_label(person_mirror()[0]) == ""

    # あとからチェックを入れて保存し直す。
    second_save = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": first_save.get_json()["month"],
            "entries_per_day": {
                "1": [{**entry, "show_attendance_time": True, "show_report_time": True}]
            },
        },
    )
    assert second_save.status_code == 200
    assert entry_shift_time_label(person_mirror()[0]) == "勤怠：10:00~19:00、出勤：9:40"


def test_person_toggle_reaches_the_scene_shift_book(tmp_path):
    """個人シフトでONにした分が、同じ現場の現場シフト帳のミラーにも出ること。"""
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="44444",
        site_name="Mirror Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client = app.test_client()
    scene_payload = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Mirror Scene",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_row_id),
        },
    ).get_json()["project"]
    scene_id = scene_payload["project"]["id"]

    _client, _person_id, _month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    scene_detail = client.get(
        f"/tools/shiftersync/cloudshift/api/project/{scene_id}?month_key=2026-04"
    ).get_json()
    mirrored = [
        item
        for item in scene_detail["month"]["entries_per_day"]["1"]
        if item.get("sync_source_type") == "person_shift"
    ]
    assert len(mirrored) == 1
    assert entry_shift_time_label(mirrored[0]) == "勤怠：10:00~19:00、出勤：9:40"


def test_shift_time_branch_hint_ignores_a_branch_from_another_site(tmp_path):
    """他現場の枝番号IDが紛れ込んでも、親現場の値へ安全に落ちること。"""
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="55555",
        site_name="Own Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    other_site_row_id = _create_site(
        app,
        site_id="66666",
        site_name="Other Site",
        attendance_times=[{"start": "01:00", "end": "02:00"}],
    )
    with app.app_context():
        foreign_branch = SiteBranch(
            site_row_id=other_site_row_id,
            site_branch="001",
            cloudshift_option_key="O",
            site_register="owner01",
            site_updater="owner01",
            attendance_times=[{"start": "03:00", "end": "04:00"}],
            is_active=True,
        )
        db.session.add(foreign_branch)
        db.session.commit()
        foreign_branch_id = foreign_branch.id

    _client, _project_id, month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={
            "show_attendance_time": True,
            "show_report_time": True,
            "site_branch_row_id": str(foreign_branch_id),
        },
    )
    entry = month["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == [{"start": "10:00", "end": "19:00"}]
    assert entry["report_time"] == "09:40"


def test_csv_round_trip_preserves_the_shift_time_branch_hint():
    entries = {
        "1": [
            {
                "id": "entry-1",
                "value": "Alpha",
                "show_attendance_time": True,
                "attendance_times": [{"start": "08:00", "end": "17:00"}],
                "shift_time_branch_row_id": "42",
            }
        ]
    }
    parsed = parse_csv_text(serialize_csv_text("person", 2026, 4, "Tester", 0, entries))
    assert parsed["entries_per_day"]["1"][0]["shift_time_branch_row_id"] == "42"


def test_siteplus_can_clear_shift_times_explicitly(tmp_path):
    """明示的に空を送ったときだけクリアされること（省略時の保持と区別できる）。"""
    _module, _app, client = _build_siteplus_client(tmp_path)
    site = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "700",
            "site_name": "Clearable",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "attendance_times": [{"start": "10:00", "end": "19:00"}],
            "report_time": "9:40",
        },
    ).get_json()["site"]

    cleared = client.put(
        f"/tools/siteplus/api/sites/{site['id']}",
        json={
            "site_id": "700",
            "site_name": "Clearable",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
            "attendance_times": [],
            "report_time": "",
            "confirm_changes": True,
            "confirm_duplicate": True,
        },
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["site"]["attendance_times"] == []
    assert cleared.get_json()["site"]["report_time"] == ""


def test_person_entry_without_a_site_link_shows_nothing(tmp_path):
    """現場を選ばずに手入力しただけのエントリは、チェックしても何も出ない。"""
    module, app = _build_cloudshift_app(tmp_path)
    client = app.test_client()
    project_payload = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "Freetext", "mode": "person", "year": "2026", "month": "4"},
    ).get_json()["project"]
    project_id = project_payload["project"]["id"]

    saved = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": project_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "free-1",
                        "value": "どこかの現場",
                        "show_attendance_time": True,
                        "show_report_time": True,
                    }
                ]
            },
        },
    )
    assert saved.status_code == 200
    entry = saved.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry["attendance_times"] == []
    assert entry_shift_time_label(entry) == ""


def test_entry_linked_by_site_id_only_still_resolves_times(tmp_path):
    """site_row_id が無く契約番号だけのエントリでも現場マスタから引けること。"""
    module, app = _build_cloudshift_app(tmp_path)
    _create_site(
        app,
        site_id="88888",
        site_name="Id Only Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client = app.test_client()
    project_payload = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": "IdOnly", "mode": "person", "year": "2026", "month": "4"},
    ).get_json()["project"]
    project_id = project_payload["project"]["id"]

    saved = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/4",
        json={
            "required_capacity": 0,
            "base_month": project_payload["month"],
            "entries_per_day": {
                "1": [
                    {
                        "id": "idonly-1",
                        "value": "Id Only Site",
                        "site_id": "88888",
                        "site_name": "Id Only Site",
                        "show_attendance_time": True,
                        "show_report_time": True,
                    }
                ]
            },
        },
    )
    assert saved.status_code == 200
    entry = saved.get_json()["month"]["entries_per_day"]["1"][0]
    assert entry_shift_time_label(entry) == "勤怠：10:00~19:00、出勤：9:40"


def test_shift_times_survive_both_soft_and_hard_site_deletion(tmp_path):
    """マニュアルの記載どおり、現場を消してもシフトの表示が空にならないこと。

    論理削除は現場行が残るのでマスタの値を読み続け、完全削除はエントリ側の
    スナップショットへフォールバックする。
    """
    module, app = _build_cloudshift_app(tmp_path)
    site_row_id = _create_site(
        app,
        site_id="99999",
        site_name="Doomed Site",
        attendance_times=[{"start": "10:00", "end": "19:00"}],
        report_time="09:40",
    )
    client, project_id, _month = _create_person_project_with_entry(
        module,
        app,
        site_row_id=site_row_id,
        entry_extra={"show_attendance_time": True, "show_report_time": True},
    )

    def current_label():
        detail = client.get(
            f"/tools/shiftersync/cloudshift/api/project/{project_id}?month_key=2026-04"
        ).get_json()
        return entry_shift_time_label(detail["month"]["entries_per_day"]["1"][0])

    # 論理削除（is_active=False）でも表示は変わらない。
    with app.app_context():
        db.session.get(Site, site_row_id).is_active = False
        db.session.commit()
    assert current_label() == "勤怠：10:00~19:00、出勤：9:40"

    # 完全削除でもスナップショットが残る。
    with app.app_context():
        db.session.delete(db.session.get(Site, site_row_id))
        db.session.commit()
    assert current_label() == "勤怠：10:00~19:00、出勤：9:40"


def test_shift_time_toggles_live_in_the_option_popup_not_the_add_form():
    """勤怠/出勤のチェックは日付セルの追加欄ではなく「オプション選択」に置く。

    追加欄はコメント欄の上に埋もれて見落とされやすかったため、オプションと同じ
    ポップアップへ移した。日別の選択はオプション・第二オプションと同じく state で持つ。
    """
    source = (ROOT / "app" / "static" / "shiftersync" / "js" / "ss_common.js").read_text(encoding="utf-8")

    # オプション選択ポップアップ側にセクションがある。
    assert "function createShiftTimeSection(dayKey)" in source
    assert "secondCol.append(createShiftTimeSection(dayKey));" in source
    assert "現場の時間表示" in source

    # 追加欄に置いていた旧実装が戻っていないこと。
    assert "buildShiftTimeToggleRow" not in source
    assert "entry-show-attendance-time" not in source
    assert "entry-show-report-time" not in source

    # 日別の選択状態は state 管理で、カレンダー再構築と追加後にリセットされる。
    assert "selectedShiftTimeToggles: {}," in source
    assert "state.selectedShiftTimeToggles = {};" in source
    assert "clearSelectedShiftTimeTogglesForDay(dayKey);" in source

    # 「設定」ボタンのラベルにも状態を出す。
    add_entry = source[source.index("function updateOptionSelectButton"):]
    add_entry = add_entry[: add_entry.index("function showOptionPopup")]
    assert "shiftTimes.show_attendance_time" in add_entry
    assert "'勤怠'" in add_entry
    assert "'出勤'" in add_entry
