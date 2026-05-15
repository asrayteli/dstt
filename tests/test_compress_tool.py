import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pyzipper
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
    root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(root / "app" / "templates"),
        static_folder=str(root / "app" / "static"),
    )
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return None

    app.context_processor(
        lambda: {
            "current_user_id": "tester",
            "app_navigation_items": [],
            "app_version": "test",
            "default_page_title": lambda: "DSTT - test",
        }
    )
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


def test_preview_exclusion_counts_size_and_reasons():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/preview",
        json={
            "filenames": ["a.txt", "a.txt", "debug.log", "memo.tmp"],
            "sizes": [10, 20, 30, 40],
            "format": "zip",
            "archive_name": "caseA",
            "exclude_exts": "log,tmp",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["archive_name"] == "caseA.zip"
    assert payload["included_count"] == 2
    assert payload["excluded_count"] == 2
    assert payload["renamed_count"] == 1
    assert payload["total_size"] == 30
    assert payload["included_preview"] == ["a.txt", "a (1).txt"]
    assert payload["excluded_reasons"] == [
        {"name": "debug.log", "reason": "拡張子 .log"},
        {"name": "memo.tmp", "reason": "拡張子 .tmp"},
    ]


def test_preview_preserves_folder_paths_and_renames_within_folder():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/preview",
        json={
            "filenames": ["a.txt", "a.txt", "b.txt"],
            "file_paths": ["docs/a.txt", "docs/a.txt", "../unsafe/b.txt"],
            "format": "zip",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["included_preview"] == ["docs/a.txt", "docs/a (1).txt", "unsafe/b.txt"]
    assert payload["renamed_count"] == 1


def test_zip_build_with_root_folder_and_duplicate_names():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/",
        data={
            "format": "zip",
            "archive_name": "bundle",
            "root_folder": "shared",
            "compress_level": "high",
            "files": [
                (io.BytesIO(b"hello"), "same.txt"),
                (io.BytesIO(b"world"), "same.txt"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.data), "r") as zf:
        names = sorted(zf.namelist())

    assert names == ["shared/same (1).txt", "shared/same.txt"]


def test_zip_build_preserves_uploaded_folder_structure():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/",
        data={
            "format": "zip",
            "archive_name": "folders",
            "file_paths": ["docs/a.txt", "docs/nested/b.txt"],
            "files": [
                (io.BytesIO(b"alpha"), "a.txt"),
                (io.BytesIO(b"beta"), "b.txt"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data), "r") as zf:
        assert sorted(zf.namelist()) == ["docs/a.txt", "docs/nested/b.txt"]
        assert zf.read("docs/nested/b.txt") == b"beta"


def test_password_zip_contains_files_directly_not_inner_zip():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/",
        data={
            "format": "zip",
            "archive_name": "secret_bundle",
            "password": "secret",
            "files": [
                (io.BytesIO(b"alpha"), "alpha.txt"),
                (io.BytesIO(b"beta"), "beta.txt"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    with pyzipper.AESZipFile(io.BytesIO(response.data), "r") as zf:
        zf.setpassword(b"secret")
        assert sorted(zf.namelist()) == ["alpha.txt", "beta.txt"]
        assert zf.read("alpha.txt") == b"alpha"


def test_tar_gz_build_with_root_folder():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/",
        data={
            "format": "tar.gz",
            "archive_name": "bundle",
            "root_folder": "root",
            "files": [(io.BytesIO(b"hello"), "hello.txt")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.data), mode="r:gz") as tarf:
        assert tarf.getnames() == ["root/hello.txt"]
        assert tarf.extractfile("root/hello.txt").read() == b"hello"


def test_tar_xz_build_with_folder_structure():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/",
        data={
            "format": "tar.xz",
            "archive_name": "bundle",
            "file_paths": ["folder/hello.txt"],
            "files": [(io.BytesIO(b"hello"), "hello.txt")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.data), mode="r:xz") as tarf:
        assert tarf.getnames() == ["folder/hello.txt"]
        assert tarf.extractfile("folder/hello.txt").read() == b"hello"


def test_preview_sanitizes_dangerous_root_folder_and_warns_on_size_limit():
    module = _load_module()
    client = _build_client(module)

    response = client.post(
        "/tools/compress/preview",
        json={
            "filenames": ["payload.txt"],
            "sizes": [module.MAX_TOTAL_SIZE + 1],
            "format": "zip",
            "root_folder": "../unsafe",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["root_folder"] == "unsafe"
    assert payload["included_preview"] == ["unsafe/payload.txt"]
    assert payload["warnings"]


def test_compress_page_does_not_advertise_7z():
    module = _load_module()
    client = _build_client(module)

    response = client.get("/tools/compress/")

    assert response.status_code == 200
    assert "ZIP / TAR / TAR.GZ / TAR.BZ2 / TAR.XZ".encode("utf-8") in response.data
    assert "フォルダを選択".encode("utf-8") in response.data
    assert b"7z" not in response.data
