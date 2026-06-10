"""ファイルA更新時の退職者フラグ運用のテスト。

ファイルに含まれない既存社員は物理削除されず、is_retired=True が立ち
（is_deleted は False のまま）、サーバーに保持される。再びファイルに
載れば自動で在籍中に戻る。
"""

import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from flask import Flask

from app.models import Employee, Office, db


ROOT = Path(__file__).resolve().parents[1]


def _load_pluslist_module():
    module_path = ROOT / "app" / "tools" / "pluslist.py"
    spec = importlib.util.spec_from_file_location("pluslist_retire_test_module", module_path)
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
        for num, name in (("0000001", "在籍 一郎"), ("0000002", "退職 二郎")):
            db.session.add(Employee(
                employee_number=num,
                office_code="100",
                office_name="本社",
                employee_name=name,
                address1="東京都千代田区1-1-1",
                is_deleted=False,
                is_retired=False,
            ))
        db.session.commit()

    return app, module


def _make_file_a_xlsx(rows):
    """ファイルA（社員基本情報）のxlsxを生成。

    rows: [(社員番号, 社員名称), ...]
    """
    records = []
    for num, name in rows:
        records.append({
            "暫定会社略称": "DST",
            "社員番号": num,
            "社員名称": name,
            "所属コード［＊］": "100",
            "所属名称": "本社",
            "住所１": "東京都千代田区1-1-1",
        })
    df = pd.DataFrame(records)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf


def _upload_and_import(client, rows):
    data = {"file": (_make_file_a_xlsx(rows), "fileA.xlsx")}
    resp = client.post("/tools/pluslist/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True
    session_key = payload["session_key"]

    resp = client.post("/tools/pluslist/api/import", json={"session_key": session_key})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return payload, resp.get_json()


def test_missing_employee_is_retired_not_deleted(tmp_path):
    app, module = _build_client(tmp_path)
    client = app.test_client()

    # ファイルAに 0000001 のみ含める（0000002 は欠落 → 退職）
    preview, result = _upload_and_import(client, [("0000001", "在籍 一郎")])

    # プレビュー上は「退職(=delete_count)」として1件
    assert preview["stats"]["delete_count"] == 1
    assert result["deleted"] == 1

    with app.app_context():
        retired = Employee.query.filter_by(employee_number="0000002").first()
        # 物理削除されず保持されている
        assert retired is not None
        # 退職フラグが立ち、削除フラグは立たない
        assert retired.is_retired is True
        assert retired.is_deleted is False

    # 既定の一覧では退職者は出ない
    resp = client.get("/tools/pluslist/api/employees")
    nums = [e["employee_number"] for e in resp.get_json()["employees"]]
    assert "0000001" in nums
    assert "0000002" not in nums

    # 「退職者を表示」(show_deleted=true) では出る
    resp = client.get("/tools/pluslist/api/employees?show_deleted=true")
    employees = resp.get_json()["employees"]
    nums = [e["employee_number"] for e in employees]
    assert "0000002" in nums
    retired_dict = next(e for e in employees if e["employee_number"] == "0000002")
    assert retired_dict["is_retired"] is True


def test_already_retired_not_recounted(tmp_path):
    app, module = _build_client(tmp_path)
    client = app.test_client()

    # 1回目: 0000002 を退職にする
    _upload_and_import(client, [("0000001", "在籍 一郎")])
    # 2回目: 同じファイル（0000002 は引き続き欠落）。既に退職済みなので再カウントされない
    preview, result = _upload_and_import(client, [("0000001", "在籍 一郎")])
    assert preview["stats"]["delete_count"] == 0
    assert result["deleted"] == 0


def test_retired_employee_reactivated_when_back_in_file(tmp_path):
    app, module = _build_client(tmp_path)
    client = app.test_client()

    # 退職させる
    _upload_and_import(client, [("0000001", "在籍 一郎")])
    with app.app_context():
        assert Employee.query.filter_by(employee_number="0000002").first().is_retired is True

    # 再びファイルに載せる → 在籍中に戻る
    _upload_and_import(client, [("0000001", "在籍 一郎"), ("0000002", "退職 二郎")])
    with app.app_context():
        emp = Employee.query.filter_by(employee_number="0000002").first()
        assert emp.is_retired is False
        assert emp.is_deleted is False

    # 既定の一覧に再び出る
    resp = client.get("/tools/pluslist/api/employees")
    nums = [e["employee_number"] for e in resp.get_json()["employees"]]
    assert "0000002" in nums
