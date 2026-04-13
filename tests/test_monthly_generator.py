import sys
from pathlib import Path
import importlib.util

from flask import Flask
from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))

from app.models import Site, SiteBranch, SiteContractMaster, db


def _load_monthly_module():
    module_path = ROOT / "app" / "tools" / "monthly_generator.py"
    spec = importlib.util.spec_from_file_location("monthly_generator_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_app(tmp_path):
    module = _load_monthly_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'monthly_generator.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.monthly_generator_bp)
    with app.app_context():
        db.create_all()
    return module, app


def _write_subject_csv(path: Path, subject_name: str, contract_code: str = "01234001", site_name: str = "テスト現場"):
    header = [f"h{i}" for i in range(25)]
    row = [""] * 25
    row[5] = "契約種別"
    row[8] = contract_code
    row[9] = "株式会社テスト"
    row[10] = site_name
    row[11] = "SUBJ"
    row[12] = subject_name
    row[13] = "100"
    path.write_text(",".join(header) + "\n" + ",".join(row) + "\n", encoding="utf-8")


def _write_report_xlsx(path: Path):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    workbook.save(path)
    workbook.close()


def test_monthly_generator_db_mode_uses_site_contract_master(tmp_path):
    module, app = _build_app(tmp_path)
    subject_name = next(iter(module.EXPENSE_MAPPING.keys()))
    expense_category = module.EXPENSE_MAPPING[subject_name]

    subject_path = tmp_path / "subject.csv"
    report_path = tmp_path / "report.xlsx"
    _write_subject_csv(subject_path, subject_name)
    _write_report_xlsx(report_path)

    with app.app_context():
        site = Site(
            site_id="01234",
            site_name="テスト現場",
            site_manager_last="山田",
            site_manager_first="太郎",
            site_manager_id="9001",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()

        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="001",
            cloudshift_option_key="PENDING",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()

        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
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

        result = module.process_monthly_data(
            str(subject_path),
            None,
            str(report_path),
            4,
            "Sheet1",
            site_source="db",
            site_manager_id="9001",
        )

    assert result["success"] is True
    assert result["debug"]["site_dict_count"] == 1
    assert abs(result["data"]["一般"][expense_category]) == 100
    assert result["debug"]["segment_contract_codes"]["一般"] == ["01234001"]
    assert result["debug"]["segment_contract_codes"]["役員"] == []
    assert result["debug"]["segment_contract_codes"]["旅客"] == []


def test_monthly_generator_page_renders_both_site_source_panels(tmp_path):
    module, app = _build_app(tmp_path)
    client = app.test_client()

    response = client.get("/tools/monthly_generator/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="site-source"' in html
    assert 'id="site-source-file-panel"' in html
    assert 'id="site-source-db-panel"' in html


def test_monthly_generator_db_mode_matches_manager_id_without_leading_zero(tmp_path):
    module, app = _build_app(tmp_path)
    subject_name = next(iter(module.EXPENSE_MAPPING.keys()))

    subject_path = tmp_path / "subject_zero.csv"
    report_path = tmp_path / "report_zero.xlsx"
    _write_subject_csv(subject_path, subject_name)
    _write_report_xlsx(report_path)

    with app.app_context():
        site = Site(
            site_id="01234",
            site_name="ゼロ現場",
            site_manager_last="山田",
            site_manager_first="太郎",
            site_manager_id="0108486",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()

        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="001",
            cloudshift_option_key="PENDING",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()

        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
                site_id="01234",
                site_branch="001",
                site_name="ゼロ現場",
                site_manager_id="0108486",
                site_manager_name="山田 太郎",
                segment="一般",
                cloudshift_option_key="PENDING",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()

        result = module.process_monthly_data(
            str(subject_path),
            None,
            str(report_path),
            4,
            "Sheet1",
            site_source="db",
            site_manager_id="108486",
        )

    assert result["success"] is True
    assert result["debug"]["site_dict_count"] == 1


def test_monthly_generator_requires_confirmation_for_missing_subject_sites(tmp_path):
    module, app = _build_app(tmp_path)
    subject_name = next(iter(module.EXPENSE_MAPPING.keys()))

    subject_path = tmp_path / "subject_missing.csv"
    report_path = tmp_path / "report_missing.xlsx"
    _write_subject_csv(subject_path, subject_name, contract_code="99990001", site_name="科目側のみ現場")
    _write_report_xlsx(report_path)

    with app.app_context():
        site = Site(
            site_id="01234",
            site_name="PLUS現場",
            site_manager_last="山田",
            site_manager_first="太郎",
            site_manager_id="9001",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()

        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="001",
            cloudshift_option_key="PENDING",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()

        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
                site_id="01234",
                site_branch="001",
                site_name="PLUS現場",
                site_manager_id="9001",
                site_manager_name="山田 太郎",
                segment="一般",
                cloudshift_option_key="PENDING",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()

        result = module.process_monthly_data(
            str(subject_path),
            None,
            str(report_path),
            4,
            "Sheet1",
            site_source="db",
            site_manager_id="9001",
        )

    assert result["confirmation_required"] is True
    assert result["missing_subject_sites"] == [
        {"contract_code": "01234001", "site_name": "PLUS現場"}
    ]
    assert result["details"] == [
        "契約コード: 01234001 / 現場名: PLUS現場"
    ]


def test_monthly_generator_can_ignore_missing_subject_sites(tmp_path):
    module, app = _build_app(tmp_path)
    subject_name = next(iter(module.EXPENSE_MAPPING.keys()))

    subject_path = tmp_path / "subject_ignore.csv"
    report_path = tmp_path / "report_ignore.xlsx"
    _write_subject_csv(subject_path, subject_name, contract_code="99990001", site_name="科目側のみ現場")
    _write_report_xlsx(report_path)

    with app.app_context():
        site = Site(
            site_id="01234",
            site_name="PLUS現場",
            site_manager_last="山田",
            site_manager_first="太郎",
            site_manager_id="9001",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(site)
        db.session.flush()

        branch = SiteBranch(
            site_row_id=site.id,
            site_branch="001",
            cloudshift_option_key="PENDING",
            site_register="tester",
            site_updater="tester",
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()

        db.session.add(
            SiteContractMaster(
                contract_code="01234001",
                site_row_id=site.id,
                site_branch_row_id=branch.id,
                site_id="01234",
                site_branch="001",
                site_name="PLUS現場",
                site_manager_id="9001",
                site_manager_name="山田 太郎",
                segment="一般",
                cloudshift_option_key="PENDING",
                is_active=True,
                source="siteplus",
            )
        )
        db.session.commit()

        result = module.process_monthly_data(
            str(subject_path),
            None,
            str(report_path),
            4,
            "Sheet1",
            site_source="db",
            site_manager_id="9001",
            ignore_missing_subject_sites=True,
        )

    assert result["success"] is True
    assert "warnings" in result
    assert "科目別推移表に見つからない現場 1 件を無視して処理を続行しました。" in result["warnings"]
    assert result["debug"]["missing_subject_sites"] == [
        {"contract_code": "01234001", "site_name": "PLUS現場"}
    ]


def test_monthly_generator_summary_js_uses_backend_keys():
    js_path = ROOT / "app" / "static" / "monthly_generator" / "js" / "monthly_generator.js"
    script = js_path.read_text(encoding="utf-8")

    assert "基本売上" in script
    assert "その他売上" in script
    assert "材料" in script
    assert "労務" in script
    assert "経費" in script

    assert "基本請負費" not in script
    assert "その他請負費" not in script
    assert "販管費" not in script
    assert "人件費" not in script
    assert "window.progressHideTimeout" in script
    assert "renderSegmentContractCodes" in script
    assert "対象契約コード" in script
