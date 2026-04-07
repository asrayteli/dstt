import sys
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


def _load_siteplus_module():
    module_path = ROOT / "app" / "tools" / "siteplus.py"
    spec = importlib.util.spec_from_file_location("siteplus_test_module", module_path)
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
    assert "/tools/siteplus/api/sites" in response.get_data(as_text=True)


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
