"""大規模シフト堅牢化（2026-07-24 調査での修正）に対する回帰テスト。

対象:
- 下書き公開時にサーバー権威sync(source_type=='sync')を再取得し、削除済みsyncを
  liveへ復活させない（H-1）。
- 下書き保存にもsync保護を適用し、クライアントによるsync削除を無効化する（M-1）。
- レギュラー列の社員番号重複を設定保存時に拒否する（FIX-4）。
- 基準版未設定時にエクスポートの変更マーカーを誤検出しない（FIX-5）。
- 塗り色/主コードを先頭ローカル割当基準にする（FIX-7）。
- holiday_kind のみのセルを保存/逆同期で失わない（FIX-8）。
"""

from copy import deepcopy

from app.services.cloudshift_large import default_large_config
from app.tools.cloudshift_large_export import _baseline_changed, _primary_local_code_key
from tests.test_cloudshift import _build_client, _owner, _load_cloudshift_module


BASE = "/tools/shiftersync/cloudshift"


def _make_large(client):
    created = client.post(
        f"{BASE}/api/create",
        data={"title": "堅牢性検証", "mode": "large", "year": "2026", "month": "7"},
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
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config}
    ).status_code == 200
    assert client.put(
        f"{BASE}/api/project/{project_id}/month/2026/7",
        json={"base_month": {"revision": 1}, "entries_per_day": {"1": [{"member_id": "m1", "value": "A"}]}},
    ).status_code == 200
    return project_id


def _sync_assignment():
    return {
        "code_key": "SYNCX", "source_type": "sync",
        "sync_source_project_id": "srcP", "sync_source_month_key": "2026-07",
        "sync_source_day": "1", "sync_source_entry_id": "e1",
        "source_project_id": "srcP", "source_project_title": "個人シフト",
    }


def _has_sync(entries_for_day):
    return any(
        str(assignment.get("source_type")) == "sync"
        for entry in entries_for_day
        for assignment in (entry.get("assignments") or [])
    )


def test_publish_does_not_resurrect_deleted_sync(tmp_path):
    """下書きに固着した削除済みsyncを、公開でliveへ復活させない（H-1）。"""
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _make_large(client)
    with client.application.app_context():
        project = module._load_project(project_id)
        month = project["months"]["2026-07"]
        # live: 上流でsyncが削除された状態（ローカルAのみ）
        month["entries_per_day"] = {
            "1": [{"member_id": "m1", "value": "A",
                   "assignments": [{"code_key": "A", "source_type": "local"}]}],
        }
        # draft: 古いsyncが凍結されたまま(dirty) + day2のローカル編集
        month["draft_entries_per_day"] = {
            "1": [{"member_id": "m1", "value": "A",
                   "assignments": [{"code_key": "A", "source_type": "local"}, _sync_assignment()]}],
            "2": [{"member_id": "m1", "value": "A",
                   "assignments": [{"code_key": "A", "source_type": "local"}]}],
        }
        module._save_project(project)

    published = client.post(f"{BASE}/api/project/{project_id}/month/2026/7/draft/publish", json={})
    assert published.status_code == 200
    entries = published.get_json()["month"]["entries_per_day"]
    # 削除済みsyncは復活しない
    assert not _has_sync(entries.get("1", [])), "削除済みsync割当が公開でliveへ復活した"
    # ローカル編集は反映される
    assert entries.get("2") and entries["2"][0]["assignments"][0]["code_key"] == "A"


def test_draft_save_reinjects_server_sync(tmp_path):
    """下書き保存でサーバー権威syncを再注入し、クライアント削除を無効化する（M-1）。"""
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _make_large(client)
    with client.application.app_context():
        project = module._load_project(project_id)
        month = project["months"]["2026-07"]
        month["entries_per_day"] = {
            "1": [{"member_id": "m1", "value": "A",
                   "assignments": [{"code_key": "A", "source_type": "local"}, _sync_assignment()]}],
        }
        month["draft_entries_per_day"] = deepcopy(month["entries_per_day"])
        module._save_project(project)

    resp = client.put(
        f"{BASE}/api/project/{project_id}/month/2026/7/draft",
        json={"entries_per_day": {
            "1": [{"member_id": "m1", "value": "A",
                   "assignments": [{"code_key": "A", "source_type": "local"}]}],
        }},
    )
    assert resp.status_code == 200
    draft = resp.get_json()["month"]["draft_entries_per_day"]
    assert _has_sync(draft.get("1", [])), "下書き保存でサーバー権威syncが再注入されなかった"


def test_large_config_rejects_duplicate_regular_employee_number(tmp_path):
    """同一社員番号のレギュラー列を設定保存時に拒否する（FIX-4）。"""
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id = _make_large(client)
    config = default_large_config()
    config["members"] = [
        {"id": "m1", "display_name": "職員A", "employee_number": "1001",
         "employee_name": "A", "order": 10, "active": True, "column_type": "regular"},
        {"id": "m2", "display_name": "職員B", "employee_number": "1001",
         "employee_name": "B", "order": 20, "active": True, "column_type": "regular"},
    ]
    config["codes"].append({
        "key": "A", "label": "通常", "category": "work", "order": 10, "active": True,
        "times": {d: {"start": "09:00", "end": "18:00"} for d in ("weekday", "saturday", "holiday")},
    })
    resp = client.put(f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config})
    assert resp.status_code == 400
    assert "社員番号" in (resp.get_json() or {}).get("error", "")


def test_baseline_changed_false_without_baseline():
    """基準版未設定なら変更マーカーを付けない（FIX-5）。"""
    entry = {"member_id": "m1", "value": "A", "assignments": [{"code_key": "A", "source_type": "local"}]}
    assert _baseline_changed("1", "m1", entry, {"meta_data": {}}) is False
    # 基準版あり（空月）→ 追加セルは変更あり
    md = {"meta_data": {"baseline": {"entries_per_day": {"1": []}}}}
    assert _baseline_changed("1", "m1", entry, md) is True
    # 基準版に同一内容 → 変更なし
    md_same = {"meta_data": {"baseline": {"entries_per_day": {"1": [entry]}}}}
    assert _baseline_changed("1", "m1", entry, md_same) is False


def test_primary_local_code_key_prefers_local_over_external():
    """塗り色/主コードは先頭ローカル割当を優先する（FIX-7）。"""
    entry = {"value": "B", "assignments": [
        {"code_key": "B", "source_type": "scene"},
        {"code_key": "A", "source_type": "local"},
    ]}
    assert _primary_local_code_key(entry) == "A"
    # ローカルが無ければ value にフォールバック
    external_only = {"value": "B", "assignments": [{"code_key": "B", "source_type": "scene"}]}
    assert _primary_local_code_key(external_only) == "B"


def test_large_entry_has_content_keeps_holiday_kind_only_cell():
    """割当なしでも holiday_kind マークだけのセルは保持する（FIX-8）。"""
    module = _load_cloudshift_module()
    assert module._large_entry_has_content({"assignments": [], "comment": "", "holiday_kind": "legal"}) is True
    assert module._large_entry_has_content({"assignments": [], "comment": "", "holiday_kind": ""}) is False
