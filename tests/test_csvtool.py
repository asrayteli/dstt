from io import BytesIO
from pathlib import Path
import sys

import pytest
from flask_login import login_user

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def csvtool_client():
    from app import create_app
    from app.models import User, db

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test",
        }
    )

    with app.app_context():
        db.create_all()
        db.session.add(User(username="tester", password_hash="x", name="Tester"))
        db.session.commit()

    @app.route("/_login_test")
    def _login_test():
        login_user(User.query.filter_by(username="tester").one())
        return "ok"

    with app.test_client() as client:
        client.get("/_login_test")
        yield client


def test_upload_headerless_csv_does_not_sniff_digit_as_delimiter(csvtool_client):
    raw = "001,Tokyo\n002,Osaka,EXTRA\n\n003,Nagoya\n".encode("utf-8")

    response = csvtool_client.post(
        "/tools/csvtool/api/upload",
        data={
            "file": (BytesIO(raw), "headerless.csv"),
            "has_header": "false",
            "delimiter": "auto",
            "preserve_empty_rows": "true",
            "row_width_strategy": "expand",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["delimiter"] == ","
    assert payload["headers"] == ["列1", "列2", "列3"]
    assert payload["data"] == [
        ["001", "Tokyo", ""],
        ["002", "Osaka", "EXTRA"],
        ["", "", ""],
        ["003", "Nagoya", ""],
    ]


def test_download_rejects_bad_encoding_and_can_protect_formula_cells(csvtool_client):
    bad = csvtool_client.post(
        "/tools/csvtool/api/download",
        json={
            "headers": ["id"],
            "data": [["1"]],
            "encoding": "bad-encoding",
            "delimiter": ",",
            "filename": "x.csv",
        },
    )
    assert bad.status_code == 400

    good = csvtool_client.post(
        "/tools/csvtool/api/download",
        json={
            "headers": ["id", "cmd"],
            "data": [["1", "=1+1"]],
            "encoding": "utf-8-sig",
            "delimiter": ",",
            "filename": "x.csv",
            "protectFormulas": True,
        },
    )
    assert good.status_code == 200
    assert b",'=1+1" in good.data

    unencodable = csvtool_client.post(
        "/tools/csvtool/api/download",
        json={
            "headers": ["emoji"],
            "data": [["😀"]],
            "encoding": "cp932",
            "delimiter": ",",
            "filename": "x.csv",
        },
    )
    assert unencodable.status_code == 400


def test_validate_supports_column_types_and_custom_rules(csvtool_client):
    response = csvtool_client.post(
        "/tools/csvtool/api/validate",
        json={
            "headers": ["id", "code", "date"],
            "data": [["1", "A", "2024-01-01"], ["x", "", "bad"]],
            "columnTypes": {"0": "number", "2": "date"},
            "rules": [{"col": 1, "type": "required", "severity": "error"}],
        },
    )

    assert response.status_code == 200
    issues = response.get_json()["issues"]
    assert any(issue["type"] == "type" and issue["col"] == 0 for issue in issues)
    assert any(issue["type"] == "type" and issue["col"] == 2 for issue in issues)
    assert any(issue["type"] == "required" and issue["col"] == 1 for issue in issues)
