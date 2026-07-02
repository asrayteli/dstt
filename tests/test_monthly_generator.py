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


def test_monthly_generator_expense_mapping_matches_current_company_rules():
    module = _load_monthly_module()

    expected_mapping = {
        "外注費": "材料",
        "消耗品費": "材料",
        "保健衛生費": "材料",
        "燃料費": "材料",
        "タイヤ費": "材料",
        "傭車費": "材料",
        "被服費": "材料",
        "修繕費": "材料",
        "賃借料": "材料",
        "保険料": "材料",
        "家賃地代": "材料",
        "減価償却費": "材料",
        "減価償却費（ﾘｰｽ資産）": "材料",
        "租税公課": "材料",
        "支払手数料": "材料",
        "材料その他": "材料",
        "給料": "労務",
        "賞与": "労務",
        "賞与引当金繰入額": "労務",
        "退職金": "労務",
        "退職給付費用": "労務",
        "法定福利費": "労務",
        "福利厚生費": "労務",
        "通勤費": "労務",
        "労務費振替": "労務",
        "労務費その他": "労務",
        "雇用調整助成金": "労務",
        "広告宣伝費": "経費",
        "募集費": "経費",
        "旅費交通費": "経費",
        "水道光熱費": "経費",
        "通信費": "経費",
        "リース料": "経費",
        "交際接待費": "経費",
        "会議費": "経費",
        "文化教育費": "経費",
        "事故負担金": "経費",
        "寄付金": "経費",
        "会費": "経費",
        "雑費": "経費",
        "経費その他": "経費",
    }

    assert module.EXPENSE_MAPPING == expected_mapping


def test_monthly_generator_ignores_unknown_subjects_with_warning(tmp_path):
    module, app = _build_app(tmp_path)

    subject_path = tmp_path / "subject_unknown.csv"
    site_path = tmp_path / "site_unknown.csv"
    report_path = tmp_path / "report_unknown.xlsx"

    header = [f"h{i}" for i in range(25)]
    known_row = [""] * 25
    known_row[5] = "契約種別"
    known_row[8] = "01234001"
    known_row[9] = "株式会社テスト"
    known_row[10] = "テスト現場"
    known_row[11] = "SUBJ"
    known_row[12] = "雑費"
    known_row[13] = "100"

    ignored_row = [""] * 25
    ignored_row[5] = "契約種別"
    ignored_row[8] = "01234001"
    ignored_row[9] = "株式会社テスト"
    ignored_row[10] = "テスト現場"
    ignored_row[11] = "SUBJ"
    ignored_row[12] = "新聞図書費"
    ignored_row[13] = "50"

    ignored_row_2 = ignored_row.copy()
    ignored_row_2[12] = "運賃"
    ignored_row_2[13] = "75"

    rows = [header, known_row, ignored_row, ignored_row_2]
    subject_path.write_text(
        "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    site_path.write_text("契約コード,セグメント\n01234001,一般\n", encoding="utf-8")
    _write_report_xlsx(report_path)

    with app.app_context():
        result = module.process_monthly_data(
            str(subject_path),
            str(site_path),
            str(report_path),
            4,
            "Sheet1",
        )

    assert result["success"] is True
    assert result["data"]["一般"]["経費"] == 100
    assert "warnings" in result
    assert (
        "経費対照表に未登録のため、以下の科目は計上せず無視しました: 新聞図書費、運賃"
        in result["warnings"]
    )

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


def test_process_rejects_invalid_target_month_without_500(tmp_path):
    """対象月が非整数/範囲外でも 500 にせず 400 を返す（DoS・例外漏洩防止）。"""
    _module, app = _build_app(tmp_path)
    client = app.test_client()

    # 非整数
    resp = client.post(
        "/tools/monthly_generator/api/process",
        data={"target_month": "abc", "sheet_name": "Sheet1"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "対象月" in resp.get_json()["error"]

    # 範囲外
    resp = client.post(
        "/tools/monthly_generator/api/process",
        data={"target_month": "13", "sheet_name": "Sheet1"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400

    # 未指定
    resp = client.post(
        "/tools/monthly_generator/api/process",
        data={"sheet_name": "Sheet1"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


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


def _build_full_app(tmp_path, monkeypatch):
    """本物の create_app + ログインで download の認可を検証するためのアプリ。"""
    import pytest  # noqa: F401
    from werkzeug.security import generate_password_hash

    from app import create_app
    from app.models import User, db as app_db

    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'mg_auth.db').as_posix()}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )
    with app.app_context():
        app_db.drop_all()
        app_db.create_all()
        for name in ("alice", "bob"):
            app_db.session.add(
                User(username=name, password_hash=generate_password_hash("secret"), name=name)
            )
        app_db.session.commit()
    return app


def _login_as(client, username):
    response = client.post(
        "/auth/login", data={"username": username, "password": "secret"}
    )
    assert response.status_code == 302


def test_download_rejects_other_users_files_and_traversal(tmp_path, monkeypatch):
    """出力ファイルは所有者（ファイル名の自ユーザーID接頭辞）のみ取得・削除できる。"""
    app = _build_full_app(tmp_path, monkeypatch)

    module = _load_monthly_module()
    with app.app_context():
        upload_folder = Path(module.get_upload_folder())
    target = upload_folder / "alice_20260101000000_report_output.xlsx"
    target.write_bytes(b"secret-xlsx")

    # 他ユーザー（bob）はファイル名を知っていても取得できない
    client = app.test_client()
    _login_as(client, "bob")
    denied = client.get(
        "/tools/monthly_generator/api/download/alice_20260101000000_report_output.xlsx"
    )
    assert denied.status_code == 404
    assert target.exists()  # 削除もされない

    # パストラバーサル形のファイル名は拒否
    traversal = client.get("/tools/monthly_generator/api/download/..")
    assert traversal.status_code == 404

    # 所有者（alice）は取得でき、取得後にファイルは削除される
    client2 = app.test_client()
    _login_as(client2, "alice")
    allowed = client2.get(
        "/tools/monthly_generator/api/download/alice_20260101000000_report_output.xlsx"
    )
    assert allowed.status_code == 200
    assert allowed.data == b"secret-xlsx"
    assert not target.exists()
