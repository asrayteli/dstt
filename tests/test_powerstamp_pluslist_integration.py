import importlib.util
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from app.models import Employee, Office, db


ROOT = Path(__file__).resolve().parents[1]


def _load_pluslist_module():
    module_path = ROOT / "app" / "tools" / "pluslist.py"
    spec = importlib.util.spec_from_file_location("pluslist_powerstamp_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_pluslist_client(tmp_path):
    module = _load_pluslist_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'pluslist.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.pluslist_bp)

    module.current_user = SimpleNamespace(is_authenticated=True, username="tester")
    module.get_user_offices = lambda _user_id: ["100"]

    with app.app_context():
        db.create_all()
        db.session.add(Office(office_code="100", office_name="本社", created_by="tester"))
        db.session.add(Employee(
            employee_number="E001",
            office_code="100",
            office_name="本社",
            employee_name="山田 太郎",
            employee_kana="ヤマダ タロウ",
            postal_code="123-4567",
            address1="東京都千代田区丸の内1-1-1",
            address2="DSTTビル",
            mansion_name="101号室",
            is_deleted=False,
        ))
        db.session.commit()

    return app.test_client()


def test_pluslist_search_employee_exposes_envelope_fields_for_powerstamp(tmp_path):
    client = _build_pluslist_client(tmp_path)

    response = client.get("/tools/pluslist/api/search_employee?q=山田")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload[0]["employee_number"] == "E001"
    assert payload[0]["postal_code"] == "123-4567"
    assert payload[0]["address1"] == "東京都千代田区丸の内1-1-1"
    assert payload[0]["address2"] == "DSTTビル"
    assert payload[0]["mansion_name"] == "101号室"
