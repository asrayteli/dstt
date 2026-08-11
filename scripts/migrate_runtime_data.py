#!/usr/bin/env python3
"""Copy DSTT mutable state from the checkout into ``DSTT_DATA_DIR``.

Run this while ``dstt.service`` is stopped for the final cut-over. The command
is idempotent after it has created its ownership marker, and verifies every
copied file plus the SQLite database before reporting success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


MARKER = ".dstt-data-root"
DATABASE_FILES = {"users.db", "users.db-wal", "users.db-shm"}
PATH_MAPPINGS = (
    ("app/shared_files", "tools/filepost"),
    ("var/camera_scanner", "tools/camera_scanner"),
    ("var/car_inspe", "tools/car_inspe"),
    ("var/to_bell/attachments", "tools/to_bell/attachments"),
    ("app/static/health_check", "tools/health_check"),
    ("app/static/leave_mgr", "tools/leave_mgr"),
    ("app/static/monthly_generator/uploads", "tools/monthly_generator/uploads"),
    ("app/static/pluslist", "tools/pluslist"),
    ("app/static/subject_analysis_tool/uploads", "tools/subject_analysis_tool/uploads"),
    ("app/tools/report_relater_presets.json", "tools/report_relater/presets.json"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_path(source: Path, destination: Path, copied: list[tuple[Path, Path]]) -> None:
    if source.is_file():
        _copy_file(source, destination)
        copied.append((source, destination))
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            _copy_file(path, target)
            copied.append((path, target))


def _copy_instance(source: Path, destination: Path, copied: list[tuple[Path, Path]]) -> None:
    if not source.exists():
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if relative.parts and relative.parts[0] == "cloudshift":
            continue
        if path.name in DATABASE_FILES:
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            _copy_file(path, target)
            copied.append((path, target))


def _sqlite_backup(source: Path, destination: Path) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
        source_tables = int(
            source_db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        )
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)
    destination_uri = f"file:{destination.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(destination_uri, uri=True)) as destination_db:
        check = str(destination_db.execute("PRAGMA quick_check").fetchone()[0])
        destination_tables = int(
            destination_db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        )
    if check != "ok" or destination_tables != source_tables:
        raise RuntimeError(
            f"SQLite verification failed: check={check}, "
            f"source_tables={source_tables}, destination_tables={destination_tables}"
        )
    return {
        "source_tables": source_tables,
        "destination_tables": destination_tables,
        "quick_check": check,
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
    }


def _protect_tree(root: Path) -> None:
    if os.name == "nt":
        return
    for directory, _, filenames in os.walk(root):
        Path(directory).chmod(stat.S_IRWXU)
        for filename in filenames:
            (Path(directory) / filename).chmod(stat.S_IRUSR | stat.S_IWUSR)


def migrate(project_root: Path, data_root: Path, database: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    data_root = data_root.resolve()
    database = database.resolve()
    if data_root == project_root or project_root in data_root.parents:
        raise ValueError("DSTT_DATA_DIR must be outside the source checkout")
    marker = data_root / MARKER
    if data_root.exists() and any(data_root.iterdir()) and not marker.is_file():
        raise RuntimeError(
            f"Destination is non-empty and is not a DSTT data root: {data_root}"
        )
    data_root.mkdir(parents=True, exist_ok=True)
    marker.write_text("DSTT mutable data root\n", encoding="utf-8")

    copied: list[tuple[Path, Path]] = []
    _copy_instance(project_root / "instance", data_root / "instance", copied)
    cloudshift = project_root / "instance" / "cloudshift"
    if cloudshift.exists():
        _copy_path(cloudshift, data_root / "tools" / "cloudshift", copied)
    for source_relative, destination_relative in PATH_MAPPINGS:
        source = project_root / source_relative
        if source.exists():
            _copy_path(source, data_root / destination_relative, copied)

    database_result = _sqlite_backup(database, data_root / "instance" / "users.db")
    for source, destination in copied:
        if source.stat().st_size != destination.stat().st_size or _sha256(source) != _sha256(destination):
            raise RuntimeError(f"Copied file verification failed: {source}")
    report = {
        "format_version": 1,
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "data_root": str(data_root),
        "copied_files": len(copied),
        "copied_bytes": sum(destination.stat().st_size for _, destination in copied),
        "database": database_result,
    }
    report_path = data_root / "migration-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _protect_tree(data_root)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--project-root", type=Path, default=default_root)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    database = args.database or args.project_root / "instance" / "users.db"
    result = migrate(args.project_root, args.data_root, database)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
