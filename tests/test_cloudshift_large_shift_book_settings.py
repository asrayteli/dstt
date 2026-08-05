"""大規模シフトの「シフト帳の設定」（共有 / オーナー変更 / 共有URL / 復元）。

個人シフト・現場シフトと同じ設定モーダルを大規模シフトでも開けるようにした分の
回帰テスト。申請タブ（個人シフト専用）と通知先タブ（現場シフト専用）はモード判定で
出さないため、大規模シフトのタブは 4 つになる。
"""

import re
from copy import deepcopy
from pathlib import Path

from app.models import User, db
from app.services.cloudshift_large import default_large_config
from tests.test_cloudshift import _build_client, _owner


ROOT = Path(__file__).resolve().parents[1]
BASE = "/tools/shiftersync/cloudshift"
SCRIPT = ROOT / "app" / "templates" / "_cloudshift_script.html"
WORKSPACE = ROOT / "app" / "templates" / "cloudshift.html"


def _create_large_project(client, *, title="大規模現場", year=2026, month=7):
    created = client.post(
        f"{BASE}/api/create",
        data={"title": title, "mode": "large", "year": str(year), "month": str(month)},
    )
    assert created.status_code == 200
    detail = created.get_json()["project"]
    project_id = detail["project"]["id"]

    config = default_large_config()
    config["members"] = [
        {
            "id": "m1",
            "display_name": "職員A",
            "employee_number": "1001",
            "employee_name": "職員A",
            "order": 10,
            "active": True,
        }
    ]
    config["codes"].append(
        {
            "key": "A",
            "label": "通常",
            "category": "work",
            "order": 10,
            "active": True,
            "times": {
                day_type: {"start": "09:00", "end": "18:00"}
                for day_type in ("weekday", "saturday", "holiday")
            },
            "color": "#dbeafe",
        }
    )
    assert client.put(
        f"{BASE}/api/project/{project_id}/large-config", json={"large_config": config}
    ).status_code == 200
    return project_id, detail


def _save_large_month(client, project_id, *, revision, entries, year=2026, month=7):
    saved = client.put(
        f"{BASE}/api/project/{project_id}/month/{year}/{month}",
        json={"base_month": {"revision": revision}, "entries_per_day": entries},
    )
    assert saved.status_code == 200, saved.get_json()
    return saved.get_json()["month"]


# ---------------------------------------------------------------- サーバー側


def test_large_project_settings_endpoint_is_available_to_the_owner(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, _detail = _create_large_project(client)

    settings = client.get(f"{BASE}/api/project/{project_id}/settings")
    assert settings.status_code == 200
    payload = settings.get_json()
    # 共有URLタブが必要とする3種のURLが揃っていること。
    assert set(payload["urls"]) >= {"view_url", "edit_url", "pwa_url"}


def test_large_project_account_shares_can_be_managed(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, _detail = _create_large_project(client)

    shares = client.get(f"{BASE}/api/project/{project_id}/account-shares")
    assert shares.status_code == 200
    assert "shares" in shares.get_json()


def test_large_project_owner_transfer_info_is_available(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, _detail = _create_large_project(client)

    info = client.get(f"{BASE}/api/project/{project_id}/owner")
    assert info.status_code == 200
    assert info.get_json()["can_transfer"] is True


def test_large_project_urls_can_be_regenerated(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, detail = _create_large_project(client)
    before = detail["project"]["urls"]["view_url"]

    regenerated = client.post(
        f"{BASE}/api/project/{project_id}/tokens/regenerate", json={"targets": ["view"]}
    )
    assert regenerated.status_code == 200
    urls = regenerated.get_json()["urls"]
    assert urls["view_url"] != before
    # 個別再発行なので他のURLは据え置き。
    assert urls["edit_url"] == detail["project"]["urls"]["edit_url"]


def test_large_project_month_can_be_restored_from_a_revision(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, detail = _create_large_project(client)

    first = _save_large_month(
        client,
        project_id,
        revision=detail["month"]["revision"],
        entries={"1": [{"id": "e1", "member_id": "m1", "value": "A", "comment": "初回"}]},
    )
    second = _save_large_month(
        client,
        project_id,
        revision=first["revision"],
        entries={"1": [{"id": "e1", "member_id": "m1", "value": "A", "comment": "書き換え後"}]},
    )
    assert second["entries_per_day"]["1"][0]["comment"] == "書き換え後"

    revisions = client.get(f"{BASE}/api/project/{project_id}/month/2026/7/revisions")
    assert revisions.status_code == 200
    catalog = revisions.get_json()["revisions"]
    assert len(catalog) >= 2
    # 現在版が先頭で、過去版も件数付きで並ぶ。
    assert catalog[0]["is_current"] is True
    target = next(item for item in catalog if not item["is_current"])
    assert target["entry_count"] >= 1

    restored = client.post(
        f"{BASE}/api/project/{project_id}/month/2026/7/restore",
        json={"revision": target["revision"]},
    )
    assert restored.status_code == 200
    entries = restored.get_json()["month"]["entries_per_day"]["1"]
    assert entries[0]["comment"] == "初回"
    # 大規模シフト固有の割当構造が壊れずに戻ること。
    assert entries[0]["member_id"] == "m1"
    assert entries[0]["assignments"][0]["code_key"] == "A"


def test_large_project_restore_keeps_the_large_config(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, detail = _create_large_project(client)
    first = _save_large_month(
        client,
        project_id,
        revision=detail["month"]["revision"],
        entries={"1": [{"id": "e1", "member_id": "m1", "value": "A"}]},
    )
    _save_large_month(
        client,
        project_id,
        revision=first["revision"],
        entries={"1": [{"id": "e1", "member_id": "m1", "value": ""}]},
    )
    client.post(
        f"{BASE}/api/project/{project_id}/month/2026/7/restore", json={"revision": first["revision"]}
    )

    project = client.get(f"{BASE}/api/project/{project_id}").get_json()["project"]
    members = project["large_config"]["members"]
    assert [member["id"] for member in members] == ["m1"]


def test_large_project_owner_can_be_transferred(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _owner()
    project_id, _detail = _create_large_project(client)
    with client.application.app_context():
        db.session.add(User(username="2001", password_hash="x", name="新担当"))
        db.session.commit()

    transferred = client.put(
        f"{BASE}/api/project/{project_id}/owner",
        json={"new_owner_user_id": "2001", "share_with_previous_owner": True},
    )
    assert transferred.status_code == 200

    listed = client.get(f"{BASE}/api/list").get_json()["projects"]
    entry = next(item for item in listed if item["id"] == project_id)
    assert entry["access_role"] == "viewer"


# ---------------------------------------------------------------- 画面側の配線


def _script_text():
    return SCRIPT.read_text(encoding="utf-8")


def test_shift_book_settings_button_is_shown_for_large_projects():
    script = _script_text()
    assert (
        "$('cloud-open-shift-book-settings').classList.toggle('cloud-hidden', "
        "!canManageProject || project.mode === 'substitute');" in script
    )
    # 大規模だけを弾いていた旧条件が戻っていないこと。
    assert "project.mode === 'substitute' || project.mode === 'large'" not in script


def test_standalone_owner_transfer_button_is_gone():
    """オーナー変更の入口は設定モーダルのタブへ一本化した。"""
    script = _script_text()
    workspace = WORKSPACE.read_text(encoding="utf-8")
    assert 'id="cloud-transfer-owner"' not in workspace
    assert "cloud-transfer-owner" not in script
    assert "openOwnerTransferModal" not in script
    assert "cloud-owner-transfer-modal" not in script
    # タブ側のフォームは残っていること。
    assert 'data-shift-settings-tab="owner"' in script
    assert "ownerTransferFormHtml()" in script


def test_large_settings_modal_offers_exactly_the_four_generic_tabs():
    """申請（個人）・通知先（現場）は出さず、共有/オーナー/URL/復元の4つになる。"""
    script = _script_text()
    body = script[script.index("function renderShiftBookSettingsModalBody"):]
    body = body[: body.index("function renderLeaveChangeRequestCard")]

    assert "const supportsLeaveRequests = project && project.mode === 'person';" in body
    assert "const supportsPushTargets = project && project.mode === 'scene';" in body
    for tab in ("share", "owner", "urls"):
        assert re.search(rf'data-shift-settings-tab="{tab}"', body), tab
    # 復元タブは月があるときだけ（モード共通）。
    assert 'hasMonth ? `<button type="button" class="cloud-settings-tab' in body
    assert 'data-shift-settings-tab="restore"' in body
