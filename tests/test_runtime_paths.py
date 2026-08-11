from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Flask

from app import create_app
from app.runtime_paths import configured_data_root, instance_path, runtime_path
from scripts.migrate_runtime_data import migrate


def test_runtime_path_preserves_legacy_location_without_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DSTT_DATA_DIR", raising=False)
    legacy = Path("legacy") / "files"
    assert configured_data_root() is None
    assert instance_path() is None
    assert runtime_path("tools", "files", legacy=legacy) == legacy


def test_runtime_path_uses_application_data_root(tmp_path: Path) -> None:
    app = Flask(__name__)
    app.config["DSTT_DATA_DIR"] = str(tmp_path / "data")
    with app.app_context():
        assert runtime_path("tools", "files", legacy="ignored") == tmp_path / "data" / "tools" / "files"


def test_create_app_places_flask_instance_under_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "DSTT_DATA_DIR": str(data_root),
            "TO_BELL_PUSH_SCHEDULER_ENABLED": False,
        }
    )
    assert Path(app.instance_path) == data_root / "instance"
    assert app.config["DSTT_DATA_DIR"] == str(data_root.resolve())


def test_migration_copies_database_secrets_and_runtime_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    instance = project / "instance"
    instance.mkdir(parents=True)
    database = instance / "users.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO users(name) VALUES ('migrated')")
        connection.commit()
    (instance / "secret_key").write_text("secret", encoding="utf-8")
    upload = project / "app" / "shared_files" / "upload.bin"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"payload")

    data_root = tmp_path / "dstt-data"
    result = migrate(project, data_root, database)

    assert result["database"]["quick_check"] == "ok"
    assert (data_root / "instance" / "secret_key").read_text(encoding="utf-8") == "secret"
    assert (data_root / "tools" / "filepost" / "upload.bin").read_bytes() == b"payload"
    with closing(sqlite3.connect(data_root / "instance" / "users.db")) as connection:
        assert connection.execute("SELECT name FROM users").fetchone()[0] == "migrated"
