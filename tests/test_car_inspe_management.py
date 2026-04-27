import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))

from app.models import Site, SiteBranch, SiteContractMaster, VehicleInspectionRecord, db


def load_car_inspe_module():
    if "pytesseract" not in sys.modules:
        pytesseract = types.ModuleType("pytesseract")
        pytesseract.image_to_string = lambda *_args, **_kwargs: ""
        pytesseract.pytesseract = types.SimpleNamespace(tesseract_cmd="")
        sys.modules["pytesseract"] = pytesseract

    module_path = ROOT / "app" / "tools" / "car_inspe.py"
    spec = importlib.util.spec_from_file_location("car_inspe_management_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def build_client(tmp_path):
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
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'car_inspe.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.car_inspe_bp)
    module.current_user = SimpleNamespace(is_authenticated=True, username="tester01", name="Test User")
    with app.app_context():
        db.create_all()
    return app, app.test_client()


def add_siteplus_site(app):
    with app.app_context():
        site = Site(
            site_id="01234",
            site_name="Test Site",
            site_manager_last="Manager",
            site_manager_first="One",
            site_manager_id="9001",
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


def test_contract_lookup_and_create_syncs_siteplus_vehicle_number(tmp_path):
    app, client = build_client(tmp_path)
    add_siteplus_site(app)

    lookup = client.get("/tools/car_inspe/api/site-contracts?q=01234001")
    assert lookup.status_code == 200
    assert lookup.get_json()["contracts"][0]["contract_code"] == "01234001"

    response = client.post(
        "/tools/car_inspe/api/records",
        json={
            "contract_code": "01234001",
            "vehicle_number": "A-12",
            "registration_number": "品川300あ1234",
            "expiry_date": "20261201",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["record"]["siteplus_linked"] is True
    assert payload["record"]["site_name"] == "Test Site"
    with app.app_context():
        row = db.session.get(SiteContractMaster, "01234001")
        assert row.vehicle_number == "A-12"


def test_create_requires_confirmation_for_unlinked_contract(tmp_path):
    app, client = build_client(tmp_path)

    response = client.post(
        "/tools/car_inspe/api/records",
        json={"contract_code": "99999001", "vehicle_number": "99"},
    )
    assert response.status_code == 409
    assert response.get_json()["requires_confirmation"] is True

    confirmed = client.post(
        "/tools/car_inspe/api/records",
        json={"contract_code": "99999001", "vehicle_number": "99", "confirm_missing_site": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["record"]["siteplus_linked"] is False
    with app.app_context():
        record = VehicleInspectionRecord.query.filter_by(contract_code="99999001", vehicle_number="99").one()
        assert record.source == "manual_unlinked"


def test_delete_record_removes_added_vehicle_record(tmp_path):
    app, client = build_client(tmp_path)
    with app.app_context():
        record = VehicleInspectionRecord(contract_code="99999001", vehicle_number="99", uploaded_by="tester01")
        db.session.add(record)
        db.session.commit()
        record_id = record.id

    response = client.delete(f"/tools/car_inspe/api/records/{record_id}")

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(VehicleInspectionRecord, record_id) is None
