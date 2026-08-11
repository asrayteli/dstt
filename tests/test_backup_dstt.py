from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scripts.backup_dstt import create_snapshot, verify_snapshot


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO users(name) VALUES ('test user')")
        connection.commit()


def test_snapshot_contains_consistent_database_and_runtime_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    database = project / "instance" / "users.db"
    _database(database)
    secret = project / "instance" / "data_encryption.key"
    secret.write_text("test-key", encoding="utf-8")
    upload = project / "app" / "shared_files" / "document.pdf"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"test-pdf")

    snapshot, result = create_snapshot(
        project,
        tmp_path / "backups",
        database,
        runtime_paths=("instance", "app/shared_files"),
    )

    assert result["database_quick_check"] == "ok"
    assert result["database_tables"] == 1
    assert (snapshot / "runtime" / "instance" / "data_encryption.key").read_text() == "test-key"
    assert not (snapshot / "runtime" / "instance" / "users.db").exists()
    with closing(sqlite3.connect(snapshot / "database" / "users.db")) as connection:
        assert connection.execute("SELECT name FROM users").fetchone()[0] == "test user"
    assert verify_snapshot(snapshot)["files"] == 3


def test_verification_detects_changed_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    database = project / "instance" / "users.db"
    _database(database)
    runtime = project / "var" / "record.txt"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("original", encoding="utf-8")
    snapshot, _ = create_snapshot(
        project,
        tmp_path / "backups",
        database,
        runtime_paths=("var",),
    )
    (snapshot / "runtime" / "var" / "record.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_snapshot(snapshot)


def test_retention_only_removes_old_snapshot_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    database = project / "instance" / "users.db"
    _database(database)
    backup_root = tmp_path / "backups"
    for name in ("snapshot-20200101T000000Z", "snapshot-20200102T000000Z"):
        old = backup_root / name
        old.mkdir(parents=True)
        (old / "old.txt").write_text("old", encoding="utf-8")
    unrelated = backup_root / "do-not-delete"
    unrelated.mkdir()

    newest, _ = create_snapshot(
        project,
        backup_root,
        database,
        runtime_paths=(),
        keep=2,
    )

    remaining = sorted(path.name for path in backup_root.glob("snapshot-*") if path.is_dir())
    assert remaining == ["snapshot-20200102T000000Z", newest.name]
    assert unrelated.is_dir()
    latest = (backup_root / "LATEST").read_text(encoding="utf-8").strip()
    assert latest == newest.name
    manifest = json.loads((newest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 1


def test_snapshot_can_copy_an_external_data_root_without_duplicate_database(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_root = tmp_path / "data"
    database = data_root / "instance" / "users.db"
    _database(database)
    runtime = data_root / "tools" / "filepost" / "file.bin"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"external-data")

    snapshot, result = create_snapshot(
        project,
        tmp_path / "backups",
        database,
        data_root=data_root,
    )

    assert result["database_quick_check"] == "ok"
    assert (snapshot / "runtime" / "data" / "tools" / "filepost" / "file.bin").read_bytes() == b"external-data"
    assert not (snapshot / "runtime" / "data" / "instance" / "users.db").exists()
