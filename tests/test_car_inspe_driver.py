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

from app.models import DriverDocument, DriverVehicleProfile, Employee, MailMessage, db
from app.services import driver_doc_notify


def load_car_inspe_module():
    module_path = ROOT / "app" / "tools" / "car_inspe.py"
    spec = importlib.util.spec_from_file_location("car_inspe_driver_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


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
    module.current_user = SimpleNamespace(is_authenticated=True, username="tester01", name="Test User")
    with app.app_context():
        db.create_all()
    return app, app.test_client()


def add_employee(app, number="1001", name="山田太郎", email="taro@example.com"):
    with app.app_context():
        db.session.add(Employee(
            employee_number=number,
            employee_name=name,
            office_code="0001",
            office_name="本社営業所",
            email=email,
        ))
        db.session.commit()


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
        data={"expiry_date": soon, "document_number": "ABC-123"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    driver = response.get_json()["driver"]
    assert driver["doc_statuses"]["inspection"] == "expiring"
    assert driver["documents"]["inspection"]["document_number"] == "ABC-123"
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
