import importlib.util
import io
import zipfile
from pathlib import Path

from flask import Flask
from flask_login import LoginManager


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "tools" / "compress.py"
    spec = importlib.util.spec_from_file_location("compress_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_client(module):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    LoginManager(app)
    app.register_blueprint(module.compress_bp)
    return app.test_client()


def test_sanitize_archive_name_and_safe_filename():
    module = _load_module()
    assert module.safe_filename("../A<>:\"|?*\\B.txt") == "B.txt"
    assert module.sanitize_archive_name("  ") == "dstt_compressed"


def test_make_unique_name_generates_suffixes():
    module = _load_module()
    used = set()
    assert module.make_unique_name("report.pdf", used) == "report.pdf"
    assert module.make_unique_name("report.pdf", used) == "report (1).pdf"
    assert module.make_unique_name("report.pdf", used) == "report (2).pdf"


def test_preview_exclusion_and_counts():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/preview",
        json={
            "filenames": ["a.txt", "a.txt", "debug.log", "memo.tmp"],
            "format": "zip",
            "archive_name": "案件A",
            "exclude_exts": "log,tmp",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["archive_name"] == "案件A.zip"
    assert payload["included_count"] == 2
    assert payload["excluded_count"] == 2
    assert payload["included_preview"] == ["a.txt", "a (1).txt"]


def test_zip_build_with_root_folder_and_duplicate_names():
    module = _load_module()
    client = _build_client(module)

    data = {
        "format": "zip",
        "archive_name": "配布",
        "root_folder": "共通",
        "compress_level": "high",
        "files": [
            (io.BytesIO(b"hello"), "same.txt"),
            (io.BytesIO(b"world"), "same.txt"),
        ],
    }

    response = client.post("/tools/compress/", data=data, content_type="multipart/form-data")
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.data), "r") as zf:
        names = sorted(zf.namelist())

    assert names == ["共通/same (1).txt", "共通/same.txt"]
