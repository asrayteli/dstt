import io
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

from app.models import SiteContractMaster, db


def _load_sat_module():
    module_path = ROOT / "app" / "tools" / "subject_analysis_tool.py"
    spec = importlib.util.spec_from_file_location("subject_analysis_tool_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(tmp_path):
    module = _load_sat_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'sat.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.subject_analysis_tool_bp)
    with app.app_context():
        db.create_all()
    return module, app.test_client()


def _user(user_id="tester01"):
    return SimpleNamespace(is_authenticated=True, username=user_id, name="Test User")


def _subject_csv_bytes():
    header = [f"h{i}" for i in range(25)]
    row = [""] * 25
    row[5] = "基本請負料"
    row[8] = "01234001"
    row[9] = "株式会社テスト"
    row[10] = "テスト現場"
    row[11] = "CODE"
    row[12] = "基本請負料"
    row[13] = "100"
    csv_text = ",".join(header) + "\n" + ",".join(row) + "\n"
    return csv_text.encode("utf-8")


def test_subject_analysis_tool_db_mode_uses_site_contract_master(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    with client.application.app_context():
        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=1,
                site_branch_row_id=1,
                site_id="01234",
                site_branch="001",
                site_name="テスト現場",
                site_manager_id="9001",
                site_manager_name="山田 太郎",
                segment="一般",
                cloudshift_option_key="PENDING",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()

    response = client.post(
        "/tools/subject_analysis_tool/api/upload",
        data={
            "subject_file": (io.BytesIO(_subject_csv_bytes()), "subject.csv"),
            "site_source": "db",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["metadata"]["site_source"] == "db"
    assert payload["data"]["site_master"]["01234001"]["site_manager_id"] == "9001"
    assert payload["data"]["site_master"]["01234001"]["segment"] == "一般"
    assert payload["data"]["site_mapping"]["01234001"] == "一般"


def test_subject_analysis_tool_page_renders_site_source_modes(tmp_path):
    module, client = _build_client(tmp_path)
    module.current_user = _user()

    response = client.get("/tools/subject_analysis_tool/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="site-source"' in html
    assert 'id="prev-year-panel"' in html
    assert 'id="site-source-file-panel"' in html
    assert 'id="site-source-db-panel"' in html
    assert 'id="manager-id-filter"' in html
