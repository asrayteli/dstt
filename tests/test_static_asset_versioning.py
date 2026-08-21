from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Flask, url_for

from app.static_assets import enable_static_asset_versioning, static_asset_version


def _make_app(static_folder: Path) -> Flask:
    app = Flask(__name__, static_folder=str(static_folder))
    enable_static_asset_versioning(app)
    return app


def test_static_urls_include_content_hash(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    asset = static_folder / "app.js"
    asset.write_bytes(b"console.log('current');\n")
    app = _make_app(static_folder)

    with app.test_request_context():
        generated = url_for("static", filename="app.js")

    expected = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
    assert urlsplit(generated).path.endswith("/app.js")
    assert parse_qs(urlsplit(generated).query) == {"v": [expected]}


def test_static_url_hash_changes_with_file_contents(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    asset = static_folder / "app.css"
    asset.write_bytes(b"body { color: black; }")
    first = static_asset_version(static_folder, "app.css")

    asset.write_bytes(b"body { color: white; }")
    second = static_asset_version(static_folder, "app.css")

    assert first != second


def test_explicit_static_version_is_preserved(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    (static_folder / "app.js").write_bytes(b"asset")
    app = _make_app(static_folder)

    with app.test_request_context():
        generated = url_for("static", filename="app.js", v="manual")

    assert parse_qs(urlsplit(generated).query) == {"v": ["manual"]}


def test_matching_versioned_asset_is_long_lived_and_immutable(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    asset = static_folder / "app.js"
    asset.write_bytes(b"asset")
    app = _make_app(static_folder)
    version = static_asset_version(static_folder, "app.js")

    response = app.test_client().get(f"/static/app.js?v={version}")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_wrong_or_missing_version_is_not_marked_immutable(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()
    (static_folder / "app.js").write_bytes(b"asset")
    app = _make_app(static_folder)

    wrong = app.test_client().get("/static/app.js?v=outdated")
    missing = app.test_client().get("/static/app.js")

    assert "immutable" not in wrong.headers.get("Cache-Control", "")
    assert "immutable" not in missing.headers.get("Cache-Control", "")


def test_missing_or_unsafe_static_asset_has_no_version(tmp_path):
    static_folder = tmp_path / "static"
    static_folder.mkdir()

    assert static_asset_version(static_folder, "missing.js") is None
    assert static_asset_version(static_folder, "../outside.js") is None
