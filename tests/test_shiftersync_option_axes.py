from __future__ import annotations

from datetime import date

import pytest

from app.tools import cloudshift
from app.tools.shiftersync_check import (
    compare_shift_payloads,
    leave_work_conflict,
    person_conflicts,
    vehicle_conflicts,
)
from app.tools.shiftersync_format import entry_options, normalize_entry


def axes(**values):
    return {
        "time": values.get("time"),
        "vehicle": values.get("vehicle"),
        "car": values.get("car"),
        "leave": values.get("leave"),
        "second": values.get("second"),
    }


def test_legacy_vehicle_only_is_all_day_for_person_conflict():
    legacy = normalize_entry({"value": "!O!山田"})
    assert entry_options(legacy) == axes(vehicle="O")
    assert person_conflicts(entry_options(legacy), axes(time="A")) is True


def test_legacy_large_vehicle_vs_other_site_morning_is_reported_as_person_duplicate():
    result = compare_shift_payloads(
        [
            {
                "year": 2026,
                "month": 4,
                "mode": "scene",
                "title": "現場A",
                "entries_per_day": {"1": [{"value": "!O!山田"}]},
            },
            {
                "year": 2026,
                "month": 4,
                "mode": "scene",
                "title": "現場B",
                "entries_per_day": {"1": [{"value": "!A!山田"}]},
            },
        ]
    )
    assert sorted(item["entry"] for item in result["conflicts"]) == ["!A!山田", "!O!山田"]


def test_explicit_axes_win_and_legacy_value_uses_display_priority():
    normalized = normalize_entry(
        {
            "value": "!O!山田",
            "time_option": "A",
            "vehicle_option": "O",
            "car_option": "N1",
        }
    )
    assert entry_options(normalized) == axes(time="A", vehicle="O", car="N1")
    assert normalized["value"] == "!A!山田"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("A", "E", True),
        ("P", "L", True),
        ("A", "P", False),
        ("A", "L", False),
        ("P", "E", False),
        ("E", "L", False),
        ("A", "A", True),
        (None, "P", True),
        (None, "TEMP", True),
    ],
)
def test_person_conflict_time_groups(left, right, expected):
    assert person_conflicts(axes(time=left), axes(time=right)) is expected


def test_person_conflict_ignores_vehicle_car_and_second_axes():
    left = axes(time="A", vehicle="O", car="N1", second="SUB")
    right = axes(time="E", vehicle="M", car="N5", second="TRAIN")
    assert person_conflicts(left, right) is True


def test_leave_work_is_separate_from_person_conflict():
    leave = axes(leave="PAID")
    work = axes(time="A")
    assert person_conflicts(leave, work) is False
    assert leave_work_conflict(leave, work) is True
    assert leave_work_conflict(leave, axes(leave="COMP")) is False


def test_compare_payloads_reports_leave_work_outside_person_duplicates():
    result = compare_shift_payloads(
        [
            {
                "year": 2026,
                "month": 4,
                "mode": "person",
                "title": "山田",
                "entries_per_day": {"1": [{"value": "!PAID!山田"}]},
            },
            {
                "year": 2026,
                "month": 4,
                "mode": "person",
                "title": "山田",
                "entries_per_day": {"1": [{"value": "!A!山田"}]},
            },
        ]
    )
    assert result["conflicts"] == []
    assert sorted(item["entry"] for item in result["leave_work_conflicts"]) == ["!A!山田", "!PAID!山田"]


def test_vehicle_conflict_requires_same_site_car_and_overlapping_time():
    morning = axes(time="A", vehicle="O", car="N1")
    early = axes(time="E", vehicle="M", car="N1")
    afternoon = axes(time="P", vehicle="O", car="N1")
    other_car = axes(time="A", vehicle="O", car="N2")
    all_day = axes(car="N1")
    assert vehicle_conflicts(morning, early, same_site=True) is True
    assert vehicle_conflicts(morning, afternoon, same_site=True) is False
    assert vehicle_conflicts(morning, other_car, same_site=True) is False
    assert vehicle_conflicts(morning, morning, same_site=False) is False
    assert vehicle_conflicts(morning, all_day, same_site=True) is True


def test_vehicle_warning_requires_two_different_people():
    result = compare_shift_payloads(
        [
            {
                "year": 2026,
                "month": 4,
                "mode": "scene",
                "entries_per_day": {
                    "1": [
                        {"value": "!A!山田", "car_option": "N1", "employee_number": "001"},
                        {"value": "!E!山田", "car_option": "N1", "employee_number": "001"},
                        {"value": "!A!佐藤", "car_option": "N1", "employee_number": "002"},
                    ],
                    "2": [
                        {"value": "!A!山田", "car_option": "N1", "employee_number": "001"},
                        {"value": "!E!山田", "car_option": "N1", "employee_number": "001"},
                    ],
                },
            }
        ]
    )
    assert sorted(item["entry"] for item in result["vehicle_conflicts"]) == [
        "!A!佐藤",
        "!A!山田",
        "!E!山田",
    ]
    assert {item["date"] for item in result["vehicle_conflicts"]} == {1}


def test_branch_default_preserves_time_and_sets_vehicle(monkeypatch):
    branch = cloudshift._SceneBranchInfo(10, "O", "大型便")
    monkeypatch.setattr(cloudshift, "_active_scene_branches_for_project", lambda _project: [branch])
    result = cloudshift._scene_entry_with_siteplus_defaults(
        {"id": "scene-1", "site_row_id": "1"},
        {"id": "entry-1", "value": "!A!山田"},
    )
    assert result["time_option"] == "A"
    assert result["vehicle_option"] == "O"
    assert result["value"] == "!A!山田"


def test_book_sync_preserves_every_option_axis():
    synced = cloudshift._build_person_synced_entry_from_scene(
        {"id": "scene-1", "title": "現場A", "site_name": "現場A"},
        {
            "id": "entry-1",
            "value": "!A!山田",
            "time_option": "A",
            "vehicle_option": "O",
            "car_option": "N1",
        },
        month_key="2026-04",
        day_key="1",
    )
    assert entry_options(synced) == axes(time="A", vehicle="O", car="N1")
    assert synced["value"] == "!A!現場A"


def test_sync_mirror_is_not_counted_as_an_original_conflict_record():
    project = {
        "id": "scene-1",
        "title": "現場",
        "mode": "scene",
        "months": {
            "2026-04": {
                "entries_per_day": {
                    "1": [
                        {"id": "local", "value": "!A!山田", "employee_number": "001"},
                        {
                            "id": "mirror",
                            "value": "!P!山田",
                            "employee_number": "001",
                            "sync_source_type": "person_shift",
                        },
                    ]
                }
            }
        },
    }
    records = cloudshift._conflict_records_for_project(project, 2026, 4)
    assert [record["person_key"] for record in records] == ["001"]

    comparison = compare_shift_payloads(
        [
            {
                "year": 2026,
                "month": 4,
                "mode": "scene",
                "title": "現場A",
                "entries_per_day": project["months"]["2026-04"]["entries_per_day"],
            },
            {
                "year": 2026,
                "month": 4,
                "mode": "scene",
                "title": "現場B",
                "entries_per_day": {"1": [{"value": "!P!山田", "employee_number": "001"}]},
            },
        ]
    )
    assert comparison["conflicts"] == []


def test_large_mirror_code_named_a_is_treated_as_all_day_when_source_is_absent():
    comparison = compare_shift_payloads(
        [
            {
                "project_id": "scene-copy", "year": 2026, "month": 4, "mode": "scene",
                "title": "Large mirror site",
                "entries_per_day": {"1": [{
                    "id": "large-copy", "value": "!A!Worker", "employee_number": "001",
                    "sync_source_type": "large_shift", "sync_source_project_id": "large-1",
                    "sync_source_month_key": "2026-04", "sync_source_day": "1",
                    "sync_source_entry_id": "large-entry:A",
                }]},
            },
            {
                "project_id": "scene-1", "year": 2026, "month": 4, "mode": "scene",
                "title": "Other Site",
                "entries_per_day": {"1": [{"value": "!P!Worker", "employee_number": "001"}]},
            },
        ]
    )
    assert sorted(item["entry"] for item in comparison["conflicts"]) == ["!A!Worker", "!P!Worker"]


def test_ledger_mirror_is_skipped_when_its_source_book_is_selected():
    comparison = compare_shift_payloads(
        [
            {
                "project_id": "scene-copy", "year": 2026, "month": 4, "mode": "scene",
                "title": "Mirror Site", "entries_per_day": {"1": [{
                    "id": "copy", "value": "!P!Worker", "employee_number": "001",
                    "sync_source_type": "person_shift", "sync_source_project_id": "person-source",
                    "sync_source_month_key": "2026-04", "sync_source_day": "1",
                    "sync_source_entry_id": "source-entry",
                }]},
            },
            {
                "project_id": "person-source", "year": 2026, "month": 4, "mode": "scene",
                "title": "Selected canonical source", "entries_per_day": {},
            },
            {
                "project_id": "other", "year": 2026, "month": 4, "mode": "scene",
                "title": "Other Site",
                "entries_per_day": {"1": [{"value": "!P!Worker", "employee_number": "001"}]},
            },
        ]
    )
    assert comparison["conflicts"] == []


def test_assist_includes_large_book_as_all_day(monkeypatch):
    scene = {
        "id": "scene-1",
        "mode": "scene",
        "owner_user_id": "owner-1",
        "created_office_ids": [10],
    }
    large = {
        "id": "large-1",
        "title": "大規模帳",
        "mode": cloudshift.LARGE_MODE,
        "owner_user_id": "other-owner",
        "created_office_ids": [10],
    }
    monkeypatch.setattr(
        cloudshift,
        "_iter_project_summaries_for_month",
        lambda _month, mode=None: [large] if mode == cloudshift.LARGE_MODE else [],
    )
    monkeypatch.setattr(
        cloudshift,
        "_conflict_records_for_project",
        lambda *_args: [
            {
                "day": 1,
                "person_key": "001",
                "person_label": "山田",
                "employee_number": "001",
                "options": axes(),
            }
        ],
    )

    entries = cloudshift._assist_scene_conflict_entries(scene, date(2026, 4, 1))
    conflicts = cloudshift._assist_scene_conflicts_for_candidate(
        entries,
        shift_key="A",
        candidate_name="山田",
        employee_number="001",
    )
    assert [(item["project_id"], item["shift_label"]) for item in conflicts] == [
        ("large-1", "終日"),
    ]
