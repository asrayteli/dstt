import importlib.util
import sqlite3
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "monitoring"
    / "uptime-kuma"
    / "backup_uptime_kuma.py"
)
SPEC = importlib.util.spec_from_file_location("backup_uptime_kuma", SCRIPT)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(backup)


def _create_data_root(root: Path) -> None:
    root.mkdir()
    with sqlite3.connect(root / "kuma.db") as connection:
        connection.execute("CREATE TABLE monitor (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO monitor (name) VALUES ('DSTT readiness')")
    (root / "upload").mkdir()
    (root / "upload" / "logo.png").write_bytes(b"portable-monitor-data")
    (root / "screenshots").mkdir()
    (root / "screenshots" / "cache.png").write_bytes(b"cache")


def test_snapshot_is_verified_and_excludes_cache(tmp_path):
    data = tmp_path / "data"
    destination = tmp_path / "backups"
    _create_data_root(data)

    snapshot, result = backup.create_snapshot(data, destination, keep=2)

    assert result["database_quick_check"] == "ok"
    assert result["database_tables"] == 1
    assert (snapshot / "data" / "upload" / "logo.png").is_file()
    assert not (snapshot / "data" / "screenshots").exists()
    assert backup.verify_snapshot(snapshot)["database_quick_check"] == "ok"


def test_verification_rejects_modified_files(tmp_path):
    data = tmp_path / "data"
    destination = tmp_path / "backups"
    _create_data_root(data)
    snapshot, _ = backup.create_snapshot(data, destination)
    (snapshot / "data" / "upload" / "logo.png").write_bytes(b"changed")

    with pytest.raises(ValueError, match="checksum mismatch"):
        backup.verify_snapshot(snapshot)
