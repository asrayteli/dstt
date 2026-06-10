"""ファイルC（メールアドレス・会社携帯）のインポート機能のテスト。

- 1行目はヘッダーとして無視される
- B列=社員番号(7桁) / D列=メールアドレス / H列=電話番号(11桁)
- Excelで頭の0が落ちた社員番号・電話番号をサーバー側で0埋め補完する
- 社員番号をキーに既存社員へ紐付けて更新する
"""

import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from openpyxl import Workbook

from app.models import Employee, Office, db


ROOT = Path(__file__).resolve().parents[1]


def _load_pluslist_module():
    module_path = ROOT / "app" / "tools" / "pluslist.py"
    spec = importlib.util.spec_from_file_location("pluslist_contact_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(tmp_path):
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
    module.is_admin = lambda _user_id: True

    with app.app_context():
        db.create_all()
        db.session.add(Office(office_code="100", office_name="本社", created_by="tester"))
        # 社員番号は7桁・頭が0
        db.session.add(Employee(
            employee_number="0012345",
            office_code="100",
            office_name="本社",
            employee_name="山田 太郎",
            address1="東京都千代田区1-1-1",
            is_deleted=False,
        ))
        db.session.commit()

    return app, module


def _make_contact_xlsx():
    """B列:社員番号 / D列:メール / H列:電話 のxlsxを生成（1行目ヘッダー）。"""
    wb = Workbook()
    ws = wb.active
    # 1行目: ヘッダー（無視される）
    ws.append(["A", "社員番号", "C", "メール", "E", "F", "G", "電話番号"])
    # 2行目: データ。Excelで頭の0が落ちた数値を想定（社員番号12345, 電話9012345678）
    row = [None] * 8
    row[1] = 12345          # B列 -> 0012345 に補完される
    row[3] = "taro@example.com"  # D列
    row[7] = 9012345678     # H列 -> 09012345678 に補完される
    ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_padding_helper_zero_fills():
    module = _load_pluslist_module()
    assert module._pad_leading_zeros(12345, 7) == "0012345"
    assert module._pad_leading_zeros(9012345678, 11) == "09012345678"
    assert module._pad_leading_zeros("12345.0", 7) == "0012345"
    assert module._pad_leading_zeros(None, 7) is None
    assert module._pad_leading_zeros("", 7) is None
    # 桁数を超える場合は切り捨てない
    assert module._pad_leading_zeros("123456789", 7) == "123456789"


def test_contact_upload_and_import_links_by_employee_number(tmp_path):
    app, module = _build_client(tmp_path)
    client = app.test_client()

    # アップロード（プレビュー）
    data = {"file": (_make_contact_xlsx(), "contact.xlsx")}
    resp = client.post(
        "/tools/pluslist/api/upload-contact",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["stats"]["success_count"] == 1
    preview = payload["preview"][0]
    assert preview["employee_number"] == "0012345"
    assert preview["email"] == "taro@example.com"
    assert preview["company_mobile"] == "09012345678"
    assert preview["employee_exists"] is True
    assert preview["has_access"] is True

    session_key = payload["session_key"]

    # インポート実行
    resp = client.post(
        "/tools/pluslist/api/import-contact",
        json={"session_key": session_key},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()
    assert result["success"] is True
    assert result["stats"]["success_count"] == 1

    # DBに反映されたか確認
    with app.app_context():
        emp = Employee.query.filter_by(employee_number="0012345").first()
        assert emp.email == "taro@example.com"
        assert emp.company_mobile == "09012345678"


def test_contact_rejects_non_xlsx(tmp_path):
    app, module = _build_client(tmp_path)
    client = app.test_client()

    data = {"file": (BytesIO(b"dummy"), "contact.xls")}
    resp = client.post(
        "/tools/pluslist/api/upload-contact",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert ".xlsx" in resp.get_json()["error"]
