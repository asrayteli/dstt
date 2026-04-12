import sys
import io
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

from app.models import Employee, Office, SiteBranch, SiteContractMaster, db


def _load_siteplus_module():
    module_path = ROOT / "app" / "tools" / "siteplus.py"
    spec = importlib.util.spec_from_file_location("siteplus_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_test_module_for_siteplus", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(tmp_path):
    module = _load_siteplus_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'siteplus.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.siteplus_bp)
    with app.app_context():
        db.create_all()
    return module, app.test_client()


def _build_client_with_cloudshift(tmp_path):
    module = _load_siteplus_module()
    cloudshift_module = _load_cloudshift_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'siteplus_cloudshift.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.siteplus_bp)
    app.register_blueprint(cloudshift_module.cloudshift_bp)
    with app.app_context():
        db.create_all()
    return module, cloudshift_module, app.test_client()


def _user(user_id="tester01"):
    return SimpleNamespace(is_authenticated=True, username=user_id, name="Test User")


def test_siteplus_can_create_site_and_branch_with_zero_padding(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    site_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "123",
            "site_name": "Alpha",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9001",
        },
    )

    assert site_response.status_code == 200
    site_payload = site_response.get_json()["site"]
    assert site_payload["site_id"] == "00123"
    assert site_payload["site_register"] == "tester01"

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "1",
            "cloudshift_option_key": "PENDING",
        },
    )

    assert branch_response.status_code == 200
    branch_payload = branch_response.get_json()["branch"]
    assert branch_payload["site_branch"] == "001"
    assert branch_payload["cloudshift_option_key"] == "PENDING"

    list_response = client.get("/tools/siteplus/api/sites")
    assert list_response.status_code == 200
    sites = list_response.get_json()["sites"]
    assert len(sites) == 1
    assert sites[0]["branches"][0]["site_branch"] == "001"


def test_siteplus_page_renders(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    response = client.get("/tools/siteplus/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/tools/siteplus/api/sites" in html
    assert 'id="segment-modal"' in html
    assert 'bulk_segment' in html
    assert 'branch-segment-note' in html
    assert 'selectedSiteIds' in html
    assert '表示中の現場を全選択' in html
    assert 'openBulkSiteEditModal' in html
    assert 'id="unified-site-editor-modal"' in html
    assert 'data-editor-tab="segment"' in html


def test_siteplus_duplicate_name_warning_can_be_confirmed(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    first_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "100",
            "site_name": "同名現場",
            "site_manager_last": "佐藤",
            "site_manager_first": "花子",
            "site_manager_id": "9002",
        },
    )
    assert first_response.status_code == 200

    duplicate_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "101",
            "site_name": "同名現場",
            "site_manager_last": "田中",
            "site_manager_first": "次郎",
            "site_manager_id": "9003",
        },
    )

    assert duplicate_response.status_code == 409
    duplicate_payload = duplicate_response.get_json()
    assert duplicate_payload["requires_duplicate_confirmation"] is True
    assert duplicate_payload["duplicates"][0]["site_id"] == "00100"
    assert duplicate_payload["duplicates"][0]["site_branch"] == "000"

    confirmed_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "101",
            "site_name": "同名現場",
            "site_manager_last": "田中",
            "site_manager_first": "次郎",
            "site_manager_id": "9003",
            "confirm_duplicate": True,
        },
    )
    assert confirmed_response.status_code == 200


def test_siteplus_site_update_requires_preview_confirmation(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    create_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "222",
            "site_name": "Beta",
            "site_manager_last": "高橋",
            "site_manager_first": "一郎",
            "site_manager_id": "9004",
        },
    )
    site_id = create_response.get_json()["site"]["id"]

    preview_response = client.put(
        f"/tools/siteplus/api/sites/{site_id}/preview",
        json={
            "site_id": "333",
            "site_name": "Gamma",
            "site_manager_last": "高橋",
            "site_manager_first": "二郎",
            "site_manager_id": "9004",
            "is_active": True,
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.get_json()
    assert preview_payload["requires_change_confirmation"] is True
    assert {item["field"] for item in preview_payload["changes"]} >= {"site_id", "site_name", "site_manager_first"}

    update_response = client.put(
        f"/tools/siteplus/api/sites/{site_id}",
        json={
            "site_id": "333",
            "site_name": "Gamma",
            "site_manager_last": "高橋",
            "site_manager_first": "二郎",
            "site_manager_id": "9004",
            "is_active": True,
            "confirm_changes": True,
        },
    )

    assert update_response.status_code == 200
    updated_site = update_response.get_json()["site"]
    assert updated_site["site_id"] == "00333"
    assert updated_site["site_name"] == "Gamma"
    assert updated_site["site_manager_first"] == "二郎"


def test_siteplus_site_delete_hides_site_from_cloudshift_api(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    create_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "444",
            "site_name": "Delta",
            "site_manager_last": "中村",
            "site_manager_first": "三郎",
            "site_manager_id": "9005",
        },
    )
    site_payload = create_response.get_json()["site"]

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "2",
            "cloudshift_option_key": "O",
        },
    )
    assert branch_response.status_code == 200

    before_delete = client.get("/tools/siteplus/api/cloudshift/sites")
    assert before_delete.status_code == 200
    assert before_delete.get_json()["sites"][0]["site_id"] == "00444"

    delete_needs_confirm = client.delete(f"/tools/siteplus/api/sites/{site_payload['id']}")
    assert delete_needs_confirm.status_code == 409
    assert delete_needs_confirm.get_json()["requires_confirmation"] is True

    delete_response = client.delete(
        f"/tools/siteplus/api/sites/{site_payload['id']}",
        json={"confirm": True},
    )
    assert delete_response.status_code == 200

    after_delete = client.get("/tools/siteplus/api/cloudshift/sites")
    assert after_delete.status_code == 200
    assert after_delete.get_json()["sites"] == []


def test_siteplus_site_hard_delete_removes_rows(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    create_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "445",
            "site_name": "Hard Delete Site",
            "site_manager_last": "削除",
            "site_manager_first": "対象",
            "site_manager_id": "9006",
        },
    )
    assert create_response.status_code == 200
    site_payload = create_response.get_json()["site"]

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "3",
            "cloudshift_option_key": "O",
        },
    )
    assert branch_response.status_code == 200

    delete_needs_confirm = client.delete(
        f"/tools/siteplus/api/sites/{site_payload['id']}",
        json={"mode": "hard"},
    )
    assert delete_needs_confirm.status_code == 409
    assert delete_needs_confirm.get_json()["delete_mode"] == "hard"

    delete_response = client.delete(
        f"/tools/siteplus/api/sites/{site_payload['id']}",
        json={"confirm": True, "mode": "hard"},
    )
    assert delete_response.status_code == 200
    assert delete_response.get_json()["delete_mode"] == "hard"

    with client.application.app_context():
        assert db.session.get(module.Site, site_payload["id"]) is None
        assert SiteBranch.query.filter_by(site_row_id=site_payload["id"]).count() == 0
        assert SiteContractMaster.query.filter_by(site_row_id=site_payload["id"]).count() == 0


def test_siteplus_branch_hard_delete_removes_branch_and_contract_master(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    create_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "446",
            "site_name": "Branch Hard Delete Site",
            "site_manager_last": "枝",
            "site_manager_first": "削除",
            "site_manager_id": "9007",
        },
    )
    assert create_response.status_code == 200
    site_payload = create_response.get_json()["site"]

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "4",
            "cloudshift_option_key": "O",
        },
    )
    assert branch_response.status_code == 200
    branch_payload = branch_response.get_json()["branch"]

    delete_needs_confirm = client.delete(
        f"/tools/siteplus/api/branches/{branch_payload['id']}",
        json={"mode": "hard"},
    )
    assert delete_needs_confirm.status_code == 409
    assert delete_needs_confirm.get_json()["delete_mode"] == "hard"

    delete_response = client.delete(
        f"/tools/siteplus/api/branches/{branch_payload['id']}",
        json={"confirm": True, "mode": "hard"},
    )
    assert delete_response.status_code == 200
    assert delete_response.get_json()["delete_mode"] == "hard"

    with client.application.app_context():
        assert db.session.get(SiteBranch, branch_payload["id"]) is None
        assert SiteContractMaster.query.filter_by(site_branch_row_id=branch_payload["id"]).count() == 0
        assert db.session.get(module.Site, site_payload["id"]) is not None


def test_siteplus_can_register_dedicated_employee_from_contract_code(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    site_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "555",
            "site_name": "Dedicated Site",
            "site_manager_last": "管理",
            "site_manager_first": "担当",
            "site_manager_id": "9010",
        },
    )
    assert site_response.status_code == 200
    site_payload = site_response.get_json()["site"]

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "3",
            "cloudshift_option_key": "O",
        },
    )
    assert branch_response.status_code == 200
    branch_payload = branch_response.get_json()["branch"]

    contract_code = "00555003"
    with client.application.app_context():
        db.session.add(Office(office_code="TOKYO", office_name="Tokyo", created_by="tester01"))
        db.session.add(
            Employee(
                employee_number="7001",
                office_code="TOKYO",
                office_name="Tokyo",
                employee_name="専従 太郎",
                contract_code=contract_code,
            )
        )
        db.session.commit()

    candidates_response = client.get(
        f"/tools/siteplus/api/contract-master/{contract_code}/dedicated-candidates"
    )
    assert candidates_response.status_code == 200
    candidates_payload = candidates_response.get_json()
    assert candidates_payload["candidates"][0]["employee_number"] == "7001"

    save_response = client.put(
        f"/tools/siteplus/api/contract-master/{contract_code}/dedicated",
        json={"employee_number": "7001"},
    )
    assert save_response.status_code == 200
    saved_item = save_response.get_json()["item"]
    assert saved_item["dedicated_employee_number"] == "7001"
    assert saved_item["dedicated_employee_name"] == "専従 太郎"

    list_response = client.get("/tools/siteplus/api/sites")
    assert list_response.status_code == 200
    listed_branch = list_response.get_json()["sites"][0]["branches"][0]
    assert listed_branch["id"] == branch_payload["id"]
    assert listed_branch["contract_code"] == contract_code
    assert listed_branch["dedicated_employee_number"] == "7001"

    with client.application.app_context():
        master_row = db.session.get(SiteContractMaster, contract_code)
        assert master_row is not None
        assert master_row.dedicated_employee_number == "7001"


def test_siteplus_dedicated_registration_syncs_cloudshift_rule(tmp_path):
    module, cloudshift_module, client = _build_client_with_cloudshift(tmp_path)
    module.current_user = _user()
    cloudshift_module.current_user = _user()

    site_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "556",
            "site_name": "Dedicated Sync Site",
            "site_manager_last": "管理",
            "site_manager_first": "担当",
            "site_manager_id": "9011",
        },
    )
    assert site_response.status_code == 200
    site_payload = site_response.get_json()["site"]

    branch_response = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={
            "site_branch": "7",
            "cloudshift_option_key": "O",
        },
    )
    assert branch_response.status_code == 200

    create_response = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={
            "title": "Dedicated Sync Site",
            "mode": "scene",
            "year": "2026",
            "month": "4",
            "site_row_id": str(site_payload["id"]),
        },
    )
    assert create_response.status_code == 200
    project_id = create_response.get_json()["project"]["project"]["id"]

    contract_code = "00556007"
    with client.application.app_context():
        db.session.add(Office(office_code="TOKYO", office_name="Tokyo", created_by="tester01"))
        db.session.add(
            Employee(
                employee_number="7002",
                office_code="TOKYO",
                office_name="Tokyo",
                employee_name="同期 花子",
                contract_code=contract_code,
            )
        )
        db.session.commit()

    save_response = client.put(
        f"/tools/siteplus/api/contract-master/{contract_code}/dedicated",
        json={"employee_number": "7002"},
    )
    assert save_response.status_code == 200
    assert save_response.get_json()["cloudshift_synced"] is True

    assist_response = client.get(f"/tools/shiftersync/cloudshift/api/project/{project_id}/assist")
    assert assist_response.status_code == 200
    rules = assist_response.get_json()["assist"]["rules"]
    auto_rules = [rule for rule in rules if rule.get("source_type") == "siteplus_dedicated"]
    assert len(auto_rules) == 1
    assert auto_rules[0]["assignments"][0]["employee_number"] == "7002"
    assert auto_rules[0]["source_contract_code"] == contract_code


def test_siteplus_import_site_table_updates_contract_master_segment(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    csv_text = "\n".join(
        [
            "契約コード,セグメント,担当者姓,担当者名,担当者ID,現場名",
            "01234001,一般,山田,太郎,9001,取込現場",
        ]
    )

    response = client.post(
        "/tools/siteplus/api/import-site-table",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "site_table.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    summary = response.get_json()["summary"]
    assert summary["processed_rows"] == 1

    with client.application.app_context():
        master_row = db.session.get(SiteContractMaster, "01234001")
        assert master_row is not None
        assert master_row.segment == "一般"
        assert master_row.site_id == "01234"
        assert master_row.site_branch == "001"


def test_siteplus_api_sites_backfills_missing_contract_master_rows(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    with client.application.app_context():
        from app.models import Site, SiteBranch

        site = Site(
            site_id="88888",
            site_name="既存現場",
            site_manager_last="山田",
            site_manager_first="太郎",
            site_manager_id="0108486",
            site_register="tester01",
            site_updater="tester01",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()
        db.session.add(
            SiteBranch(
                site_row_id=site.id,
                site_branch="001",
                cloudshift_option_key="PENDING",
                site_register="tester01",
                site_updater="tester01",
                is_active=True,
            )
        )
        db.session.commit()

    response = client.get("/tools/siteplus/api/sites")
    assert response.status_code == 200

    with client.application.app_context():
        master_row = db.session.get(SiteContractMaster, "88888001")
        assert master_row is not None
        assert master_row.site_manager_id == "0108486"


def test_siteplus_can_update_segment_per_branch_and_per_site(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    site_response = client.post(
        "/tools/siteplus/api/sites",
        json={
            "site_id": "77777",
            "site_name": "Segment Site",
            "site_manager_last": "山田",
            "site_manager_first": "太郎",
            "site_manager_id": "9009",
        },
    )
    assert site_response.status_code == 200
    site_payload = site_response.get_json()["site"]

    first_branch = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={"site_branch": "1", "cloudshift_option_key": "PENDING"},
    )
    second_branch = client.post(
        f"/tools/siteplus/api/sites/{site_payload['id']}/branches",
        json={"site_branch": "2", "cloudshift_option_key": "PENDING"},
    )
    assert first_branch.status_code == 200
    assert second_branch.status_code == 200

    branch_segment_response = client.put(
        "/tools/siteplus/api/contract-master/77777001/segment",
        json={"segment": "一般"},
    )
    assert branch_segment_response.status_code == 200
    assert branch_segment_response.get_json()["item"]["segment"] == "一般"

    bulk_response = client.put(
        f"/tools/siteplus/api/sites/{site_payload['id']}/segments",
        json={"segment": "旅客"},
    )
    assert bulk_response.status_code == 200
    assert bulk_response.get_json()["updated_count"] == 2

    list_response = client.get("/tools/siteplus/api/sites")
    assert list_response.status_code == 200
    branches = list_response.get_json()["sites"][0]["branches"]
    branch_segments = {branch["contract_code"]: branch["segment"] for branch in branches}
    assert branch_segments["77777001"] == "旅客"
    assert branch_segments["77777002"] == "旅客"
