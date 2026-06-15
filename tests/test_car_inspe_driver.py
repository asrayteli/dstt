import importlib.util
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (
    AccessBranch,
    AccessOffice,
    DriverDocument,
    DriverVehicleProfile,
    Employee,
    MailMessage,
    db,
)
from app.services import driver_doc_notify


def load_car_inspe_module():
    module_path = ROOT / "app" / "tools" / "car_inspe.py"
    spec = importlib.util.spec_from_file_location("car_inspe_driver_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _admin_user():
    return SimpleNamespace(is_authenticated=True, username="admin01", name="Admin", is_admin=True)


@pytest.fixture()
def client(tmp_path):
    module = load_car_inspe_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'driver.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.car_inspe_bp)
    # 既定は管理者（全営業所アクセス可）。スコープ系テストは login_office_user で切替える。
    module.current_user = _admin_user()
    with app.app_context():
        db.create_all()
    app.car_inspe_module = module
    return app, app.test_client()


def add_employee(app, number="1001", name="山田太郎", email="taro@example.com",
                 office_code="0001", office_name="本社営業所"):
    with app.app_context():
        db.session.add(Employee(
            employee_number=number,
            employee_name=name,
            office_code=office_code,
            office_name=office_name,
            email=email,
        ))
        db.session.commit()


def setup_office(app, code, name):
    """アクセス権マスタ（支店＋営業所）を作成し、AccessOffice.id を返す。"""
    with app.app_context():
        branch = AccessBranch.query.filter_by(name=f"支店-{code}").first()
        if branch is None:
            branch = AccessBranch(name=f"支店-{code}", code=f"B{code}")
            db.session.add(branch)
            db.session.flush()
        office = AccessOffice(branch_id=branch.id, name=name, code=code)
        db.session.add(office)
        db.session.commit()
        return office.id


def login_office_user(app, office_id, username="office01"):
    app.car_inspe_module.current_user = SimpleNamespace(
        is_authenticated=True, username=username, name=username, is_admin=False, office_id=office_id,
    )


def login_admin(app):
    app.car_inspe_module.current_user = _admin_user()


# --------------------------------------------------------------------------- #
# 基本CRUD（管理者）
# --------------------------------------------------------------------------- #
def test_create_driver_snapshots_employee(client):
    app, http = client
    add_employee(app)

    response = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})
    assert response.status_code == 200
    driver = response.get_json()["driver"]
    assert driver["employee_name"] == "山田太郎"
    assert driver["email"] == "taro@example.com"
    assert driver["office_name"] == "本社営業所"
    # 4種の書類枠が用意される（未登録）
    assert set(driver["documents"].keys()) == set(DriverDocument.DOC_TYPES)
    assert driver["status"] == "incomplete"


def test_create_rejects_unknown_and_duplicate(client):
    app, http = client
    add_employee(app)

    missing = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "9999"})
    assert missing.status_code == 404

    first = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})
    assert first.status_code == 200
    dup = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})
    assert dup.status_code == 409


def test_employee_search_marks_linked(client):
    app, http = client
    add_employee(app)
    http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})

    data = http.get("/tools/car_inspe/api/employees?q=山田").get_json()
    assert data["employees"][0]["has_profile"] is True


def test_save_document_sets_expiry_and_status(client):
    app, http = client
    add_employee(app)
    profile_id = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"}).get_json()["driver"]["id"]

    soon = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    response = http.post(
        f"/tools/car_inspe/api/drivers/{profile_id}/documents/inspection",
        data={"expiry_date": soon, "document_number": "ABC-123", "issued_date": "2025-04-01"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    driver = response.get_json()["driver"]
    assert driver["doc_statuses"]["inspection"] == "expiring"
    assert driver["documents"]["inspection"]["document_number"] == "ABC-123"
    assert driver["documents"]["inspection"]["issued_date"] == "20250401"
    assert driver["status"] == "expiring"


def test_save_document_uploads_file_and_downloads(client):
    app, http = client
    add_employee(app)
    profile_id = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"}).get_json()["driver"]["id"]

    response = http.post(
        f"/tools/car_inspe/api/drivers/{profile_id}/documents/license",
        data={
            "expiry_date": "2030-01-31",
            "file": (io.BytesIO(b"%PDF-1.4 test"), "license.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    doc = response.get_json()["driver"]["documents"]["license"]
    assert doc["has_pdf"] is True

    download = http.get(f"/tools/car_inspe/api/documents/{doc['id']}/download")
    assert download.status_code == 200
    assert b"%PDF" in download.data


def test_invalid_expiry_is_rejected(client):
    app, http = client
    add_employee(app)
    profile_id = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"}).get_json()["driver"]["id"]

    response = http.post(
        f"/tools/car_inspe/api/drivers/{profile_id}/documents/inspection",
        data={"expiry_date": "そのうち"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# 営業所スコープ（非管理者）
# --------------------------------------------------------------------------- #
def test_list_scoped_to_accessible_offices(client):
    app, http = client
    add_employee(app, number="1001", name="A", office_code="0001", office_name="営業所1")
    add_employee(app, number="2001", name="B", office_code="0002", office_name="営業所2")
    assert http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"}).status_code == 200
    assert http.post("/tools/car_inspe/api/drivers", json={"employee_number": "2001"}).status_code == 200

    off1 = setup_office(app, "0001", "営業所1")
    setup_office(app, "0002", "営業所2")
    login_office_user(app, off1)

    data = http.get("/tools/car_inspe/api/drivers").get_json()
    assert {d["employee_number"] for d in data["drivers"]} == {"1001"}
    assert data["summary"]["drivers"] == 1


def test_office_filter_outside_scope_is_forbidden(client):
    app, http = client
    off1 = setup_office(app, "0001", "営業所1")
    setup_office(app, "0002", "営業所2")
    login_office_user(app, off1)

    res = http.get("/tools/car_inspe/api/drivers?office=0002")
    assert res.status_code == 403


def test_create_outside_scope_is_forbidden(client):
    app, http = client
    add_employee(app, number="2001", office_code="0002", office_name="営業所2")
    off1 = setup_office(app, "0001", "営業所1")
    setup_office(app, "0002", "営業所2")
    login_office_user(app, off1)

    res = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "2001"})
    assert res.status_code == 403


def test_get_driver_outside_scope_is_forbidden(client):
    app, http = client
    add_employee(app, number="2001", office_code="0002", office_name="営業所2")
    created = http.post("/tools/car_inspe/api/drivers", json={"employee_number": "2001"}).get_json()["driver"]

    off1 = setup_office(app, "0001", "営業所1")
    setup_office(app, "0002", "営業所2")
    login_office_user(app, off1)

    assert http.get(f"/tools/car_inspe/api/drivers/{created['id']}").status_code == 403
    assert http.delete(f"/tools/car_inspe/api/drivers/{created['id']}").status_code == 403


def test_no_office_user_sees_empty(client):
    app, http = client
    add_employee(app, number="1001", office_code="0001")
    http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})

    app.car_inspe_module.current_user = SimpleNamespace(
        is_authenticated=True, username="z", name="z", is_admin=False,
    )
    data = http.get("/tools/car_inspe/api/drivers").get_json()
    assert data["drivers"] == []
    assert data.get("no_office") is True
    emp = http.get("/tools/car_inspe/api/employees?q=").get_json()
    assert emp["employees"] == []


# --------------------------------------------------------------------------- #
# 一括起票
# --------------------------------------------------------------------------- #
def test_bulk_create_all_in_office(client):
    app, http = client
    for n in ("1001", "1002", "1003"):
        add_employee(app, number=n, name=f"E{n}", office_code="0001", office_name="営業所1")
    # 1名は事前に登録済み → スキップされる想定
    http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1002"})

    off1 = setup_office(app, "0001", "営業所1")
    login_office_user(app, off1)

    res = http.post("/tools/car_inspe/api/bulk", json={"office": "0001", "all_in_office": True})
    assert res.status_code == 200
    body = res.get_json()
    assert body["created"] == 2
    assert body["skipped"] == 1

    data = http.get("/tools/car_inspe/api/drivers").get_json()
    assert data["summary"]["drivers"] == 3


def test_bulk_create_selected_only(client):
    app, http = client
    for n in ("1001", "1002", "1003"):
        add_employee(app, number=n, office_code="0001", office_name="営業所1")
    off1 = setup_office(app, "0001", "営業所1")
    login_office_user(app, off1)

    res = http.post("/tools/car_inspe/api/bulk", json={"office": "0001", "employee_numbers": ["1001", "1003"]})
    assert res.status_code == 200
    assert res.get_json()["created"] == 2

    nums = {d["employee_number"] for d in http.get("/tools/car_inspe/api/drivers").get_json()["drivers"]}
    assert nums == {"1001", "1003"}


def test_bulk_outside_scope_is_forbidden(client):
    app, http = client
    off1 = setup_office(app, "0001", "営業所1")
    setup_office(app, "0002", "営業所2")
    login_office_user(app, off1)

    res = http.post("/tools/car_inspe/api/bulk", json={"office": "0002", "all_in_office": True})
    assert res.status_code == 403


def test_bulk_candidates_marks_registered(client):
    app, http = client
    add_employee(app, number="1001", office_code="0001", office_name="営業所1")
    add_employee(app, number="1002", office_code="0001", office_name="営業所1")
    http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})

    off1 = setup_office(app, "0001", "営業所1")
    login_office_user(app, off1)

    data = http.get("/tools/car_inspe/api/bulk/candidates?office=0001").get_json()
    assert data["count"] == 2
    assert data["registered"] == 1
    assert data["unregistered"] == 1


# --------------------------------------------------------------------------- #
# 名簿同期
# --------------------------------------------------------------------------- #
def test_resync_updates_name_and_office(client):
    app, http = client
    add_employee(app, number="1001", name="旧名", office_code="0001", office_name="営業所1")
    http.post("/tools/car_inspe/api/drivers", json={"employee_number": "1001"})

    with app.app_context():
        emp = Employee.query.filter_by(employee_number="1001").first()
        emp.employee_name = "新名"
        emp.office_name = "営業所1改"
        db.session.commit()

    res = http.post("/tools/car_inspe/api/resync", json={})
    assert res.status_code == 200
    assert res.get_json()["updated"] == 1

    driver = http.get("/tools/car_inspe/api/drivers").get_json()["drivers"][0]
    assert driver["employee_name"] == "新名"
    assert driver["office_name"] == "営業所1改"


# --------------------------------------------------------------------------- #
# 期限通知
# --------------------------------------------------------------------------- #
def test_expiry_notification_queues_mail_once(client):
    app, http = client
    add_employee(app)
    with app.app_context():
        profile = DriverVehicleProfile(
            employee_number="1001",
            employee_name="山田太郎",
            email="taro@example.com",
            notify_enabled=True,
        )
        db.session.add(profile)
        db.session.flush()
        expiry = (datetime.now() + timedelta(days=10)).strftime("%Y%m%d")
        db.session.add(DriverDocument(profile_id=profile.id, doc_type="inspection", expiry_date=expiry))
        db.session.commit()

        first = driver_doc_notify.run_due_expiry_notifications()
        assert first["queued"] == 1
        assert MailMessage.query.count() == 1
        mail = MailMessage.query.first()
        assert mail.to_address == "taro@example.com"
        assert mail.category == "driver_doc_expiry"

        # 同じステージでは二重送信しない。
        second = driver_doc_notify.run_due_expiry_notifications()
        assert second["queued"] == 0
        assert MailMessage.query.count() == 1


def test_expiry_notification_skips_without_email(client):
    app, http = client
    with app.app_context():
        profile = DriverVehicleProfile(employee_number="2002", employee_name="鈴木", email="", notify_enabled=True)
        db.session.add(profile)
        db.session.flush()
        expiry = (datetime.now() + timedelta(days=5)).strftime("%Y%m%d")
        db.session.add(DriverDocument(profile_id=profile.id, doc_type="license", expiry_date=expiry))
        db.session.commit()

        summary = driver_doc_notify.run_due_expiry_notifications()
        assert summary["queued"] == 0
        assert MailMessage.query.count() == 0
