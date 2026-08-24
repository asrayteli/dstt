"""大規模シフトの横断重複チェック（2026-07-24 追加機能）のテスト。

- cross_mode_conflicts の単体（時間帯重複/オプションルール/休み/同一帳除外/人物別）
- _conflict_records_for_project が同期鏡像を除外すること
- /api/project/<id>/large-conflict-check の統合（大規模×個人の二重配置検出）
"""

from app.models import AccessBranch, AccessOffice, db
from app.services.cloudshift_large import default_large_config
from app.tools.shiftersync_check import cross_mode_conflicts
from tests.test_cloudshift import _build_client, _employee_user, _owner


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


def test_same_synced_origin_is_counted_once_across_mirror_books():
    mirrors = [
        _rec("scene-A", "0001", 3, book_mode="scene", origin_key="master:3:entry-1"),
        _rec("scene-B", "0001", 3, book_mode="scene", origin_key="master:3:entry-1"),
        _rec("other", "0001", 3, book_mode="person", origin_key="other:3:entry-2"),
    ]
    conflicts = cross_mode_conflicts(mirrors)
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "double_booking"


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

def test_conflict_records_keep_master_and_resolved_substitute_mirrors(tmp_path):
    module, client = _build_client(tmp_path)
    project = {
        "id": "S", "title": "scene", "mode": "scene",
        "months": {"2026-07": {"year": 2026, "month": 7, "entries_per_day": {
            "1": [
                {
                    "id": "master-copy", "value": "!A!Master Worker", "employee_number": "1001",
                    "sync_source_type": "master_shift", "sync_source_project_id": "M",
                    "sync_source_month_key": "2026-07", "sync_source_day": "1",
                    "sync_source_entry_id": "master-entry",
                },
                {
                    "id": "sub-copy", "value": "!P!Substitute Worker", "employee_number": "1002",
                    "sync_source_type": "substitute_shift", "sync_source_project_id": "R",
                    "sync_source_month_key": "2026-07", "sync_source_day": "1",
                    "sync_source_entry_id": "sub-entry",
                },
                {
                    "id": "ledger-copy", "value": "!A!Ledger Worker", "employee_number": "1003",
                    "sync_source_type": "person_shift", "sync_source_project_id": "P",
                    "sync_source_month_key": "2026-07", "sync_source_day": "1",
                    "sync_source_entry_id": "person-entry",
                },
            ],
        }}},
    }
    with client.application.app_context():
        records = module._conflict_records_for_project(project, 2026, 7)
    assert [record["employee_number"] for record in records] == ["1001", "1002"]
    assert [record["origin_key"] for record in records] == [
        "M:2026-07:1:master-entry",
        "R:2026-07:1:sub-entry",
    ]


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


# --- 既存の重複チェックタブ(/api/conflict-check)へ大規模を擬似現場として載せる ---

def test_conflict_check_tab_expands_large_into_pseudo_scenes(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    large_id = _make_large_with_work(client)  # 職員A(emp 1001) が 1日 に勤務
    # 同じ 職員A が別の現場シフトにも 1日 に配置されている
    scene = client.post(
        f"{BASE}/api/create",
        data={"title": "別現場", "mode": "scene", "year": "2026", "month": "7"},
    ).get_json()["project"]["project"]
    assert client.put(
        f"{BASE}/api/project/{scene['id']}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [{"value": "職員A"}]}},
    ).status_code == 200

    resp = client.post(
        f"{BASE}/api/conflict-check",
        json={"month_key": "2026-07", "project_ids": [large_id, scene["id"]]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    # 大規模は列(人)ごとの擬似現場ソースへ展開される
    labels = [str(source.get("label") or "") for source in data.get("sources", [])]
    assert any("職員A" in label for label in labels), labels
    # 職員A の 1日 の二重配置（大規模×別現場）が検出される
    assert len(data.get("conflicts", [])) >= 1


def test_conflict_check_tab_large_only_runs_without_error(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    large_id = _make_large_with_work(client)
    resp = client.post(
        f"{BASE}/api/conflict-check",
        json={"month_key": "2026-07", "project_ids": [large_id]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    # 大規模単体でも擬似現場列が並び、（別人なので）衝突は無い
    assert any("職員A" in str(source.get("label") or "") for source in data.get("sources", []))
    assert data.get("conflicts") == []


def test_conflict_check_allows_shared_books(tmp_path):
    """共有された(非所有)シフト帳も明示選択すれば重複チェックに使える。"""
    module, client = _build_client(tmp_path)
    with client.application.app_context():
        branch = AccessBranch(name="Tokyo", code="CKT")
        db.session.add(branch)
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name="Shinjuku", code="CKO")
        db.session.add(office)
        db.session.commit()
        office_id = office.id

    # オーナーAが現場帳を作成し、職員Xを1日に配置して営業所へ共有する
    owner_a = _owner()
    owner_a.office_id = office_id
    owner_a.id = 100
    module.current_user = owner_a
    shared = client.post(
        f"{BASE}/api/create",
        data={"title": "A現場", "mode": "scene", "year": "2026", "month": "7"},
    ).get_json()["project"]["project"]
    assert client.put(
        f"{BASE}/api/project/{shared['id']}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [{"value": "職員X"}]}},
    ).status_code == 200
    assert client.put(
        f"{BASE}/api/project/{shared['id']}/account-shares",
        json={"share_office": True, "employee_numbers": []},
    ).status_code == 200

    # 同じ営業所の利用者Bが自分の現場帳を作り、職員Xを同日に配置
    module.current_user = _employee_user("3001", office_id=office_id)
    own = client.post(
        f"{BASE}/api/create",
        data={"title": "B現場", "mode": "scene", "year": "2026", "month": "7"},
    ).get_json()["project"]["project"]
    assert client.put(
        f"{BASE}/api/project/{own['id']}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [{"value": "職員X"}]}},
    ).status_code == 200

    # Bが自分の帳＋共有帳を選んで比較 → 404にならず、二重配置を検出する
    resp = client.post(
        f"{BASE}/api/conflict-check",
        json={"month_key": "2026-07", "project_ids": [own["id"], shared["id"]]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data.get("conflicts", [])) >= 1
