"""大規模シフトの横断重複チェック（2026-07-24 追加機能）のテスト。

- cross_mode_conflicts の単体（時間帯重複/オプションルール/休み/同一帳除外/人物別）
- _conflict_records_for_project が同期鏡像を除外すること
- /api/project/<id>/large-conflict-check の統合（大規模×個人の二重配置検出）
"""

from app.services.cloudshift_large import default_large_config
from app.tools.shiftersync_check import cross_mode_conflicts
from tests.test_cloudshift import _build_client, _owner


BASE = "/tools/shiftersync/cloudshift"


def _rec(book_id, person, day, **kw):
    base = {
        "book_id": book_id, "book_label": book_id, "book_mode": "large",
        "person_key": person, "person_label": person, "day": day,
        "option": None, "is_leave": False, "time_range": None, "display": book_id,
    }
    base.update(kw)
    return base


# --- cross_mode_conflicts 単体 ---

def test_cross_book_work_without_time_is_double_booking():
    conflicts = cross_mode_conflicts([_rec("A", "0001", 3), _rec("B", "0001", 3)])
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "double_booking"


def test_non_overlapping_time_ranges_do_not_conflict():
    conflicts = cross_mode_conflicts([
        _rec("A", "0001", 3, time_range=(480, 720)),
        _rec("B", "0001", 3, time_range=(780, 1080)),
    ])
    assert conflicts == []


def test_overlapping_time_ranges_conflict():
    conflicts = cross_mode_conflicts([
        _rec("A", "0001", 3, time_range=(480, 720)),
        _rec("B", "0001", 3, time_range=(600, 900)),
    ])
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "time_overlap"


def test_same_book_pairs_are_ignored():
    assert cross_mode_conflicts([_rec("A", "0001", 3), _rec("A", "0001", 3)]) == []


def test_leave_plus_work_is_notice_but_two_leaves_are_not():
    notice = cross_mode_conflicts([_rec("A", "0001", 3, is_leave=True), _rec("B", "0001", 3)])
    assert len(notice) == 1 and notice[0]["kind"] == "leave_work"
    assert cross_mode_conflicts([
        _rec("A", "0001", 3, is_leave=True), _rec("B", "0001", 3, is_leave=True)
    ]) == []


def test_different_person_no_conflict():
    assert cross_mode_conflicts([_rec("A", "0001", 3), _rec("B", "0002", 3)]) == []


def test_option_rules_honored_across_books():
    # A(午前) と P(午後) は両立可 → 衝突なし
    assert cross_mode_conflicts([
        _rec("A", "0001", 3, book_mode="scene", option="A"),
        _rec("B", "0001", 3, book_mode="scene", option="P"),
    ]) == []
    # A と A は衝突
    same = cross_mode_conflicts([
        _rec("A", "0001", 3, book_mode="scene", option="A"),
        _rec("B", "0001", 3, book_mode="scene", option="A"),
    ])
    assert len(same) == 1 and same[0]["kind"] == "double_booking"


# --- 鏡像除外（正規化） ---

def test_conflict_records_excludes_synced_mirror(tmp_path):
    module, client = _build_client(tmp_path)
    project = {
        "id": "P", "title": "個人シフト", "mode": "person", "employee_number": "1001",
        "months": {"2026-07": {"year": 2026, "month": 7, "entries_per_day": {
            "1": [
                {"value": "○○現場"},
                {"value": "××現場", "sync_source_type": "large_shift", "sync_source_project_id": "L"},
            ],
        }}},
    }
    with client.application.app_context():
        records = module._conflict_records_for_project(project, 2026, 7)
    # 同期鏡像(sync_source_type付き)は除外され、実配置1件のみが残る
    assert len(records) == 1
    assert "○○現場" in records[0]["display"]


# --- API 統合 ---

def _make_large_with_work(client):
    created = client.post(
        f"{BASE}/api/create",
        data={"title": "大規模現場", "mode": "large", "year": "2026", "month": "7"},
    )
    assert created.status_code == 200
    project_id = created.get_json()["project"]["project"]["id"]
    config = default_large_config()
    config["members"] = [{
        "id": "m1", "display_name": "職員A", "employee_number": "1001",
        "employee_name": "職員A", "order": 10, "active": True, "column_type": "regular",
    }]
    config["codes"].append({
        "key": "A", "label": "通常", "category": "work", "order": 10, "active": True,
        "times": {d: {"start": "09:00", "end": "18:00"} for d in ("weekday", "saturday", "holiday")},
        "color": "#dbeafe",
    })
    assert client.put(f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config}).status_code == 200
    assert client.put(
        f"{BASE}/api/project/{project_id}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [
            {"member_id": "m1", "value": "A", "assignments": [{"code_key": "A", "source_type": "local"}]},
        ]}},
    ).status_code == 200
    return project_id


def test_large_conflict_check_detects_cross_book_double_booking(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    large_id = _make_large_with_work(client)
    # 同じ社員(1001)が同日に個人シフトで別現場に配置されている
    person = client.post(
        f"{BASE}/api/create",
        data={"title": "職員A個人", "mode": "person", "employee_number": "1001", "year": "2026", "month": "7"},
    )
    assert person.status_code == 200
    person_id = person.get_json()["project"]["project"]["id"]
    assert client.put(
        f"{BASE}/api/project/{person_id}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [{"value": "別現場"}]}},
    ).status_code == 200

    resp = client.post(f"{BASE}/api/project/{large_id}/large-conflict-check", json={"month_key": "2026-07"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["conflict_count"] >= 1
    conflict = data["conflicts"][0]
    assert conflict["day"] == 1
    assert conflict["person_key"] == "1001"
    assert large_id in (conflict["left"]["book_id"], conflict["right"]["book_id"])


def test_large_conflict_check_rejects_non_large(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    scene = client.post(
        f"{BASE}/api/create",
        data={"title": "現場", "mode": "scene", "year": "2026", "month": "7"},
    ).get_json()["project"]["project"]
    resp = client.post(f"{BASE}/api/project/{scene['id']}/large-conflict-check", json={"month_key": "2026-07"})
    assert resp.status_code == 400
