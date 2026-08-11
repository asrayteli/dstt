#!/usr/bin/env python3
"""Create and verify a restorable DSTT filesystem snapshot.

The SQLite database is copied with SQLite's online backup API, so the result is
consistent even while Gunicorn is serving requests.  Runtime files and secrets
are copied into the same atomic snapshot and protected with owner-only modes.
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
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SNAPSHOT_PREFIX = "snapshot-"
MANIFEST_NAME = "manifest.json"
DEFAULT_RUNTIME_PATHS = (
    "instance",
    "app/shared_files",
    "var",
    "app/static/calendar_outputs",
    "app/static/health_check/uploads",
    "app/static/health_check/permissions.json",
    "app/static/health_check/settings.json",
    "app/static/leave_mgr/calendars",
    "app/static/leave_mgr/permissions.json",
    "app/static/leave_mgr/calendar_meta.json",
    "app/static/monthly_generator/uploads",
    "app/static/pluslist/uploads",
    "app/static/pluslist/pluslist_permissions.json",
    "app/static/subject_analysis_tool/uploads",
    "app/tools/report_relater_presets.json",
)
DATABASE_SIDECARS = {"users.db", "users.db-wal", "users.db-shm"}


def _owner_only_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(stat.S_IRWXU)


def _owner_only_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_quick_check(path: Path) -> tuple[str, int]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
        )
    return result, table_count


def _backup_sqlite(source: Path, destination: Path) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)
    _owner_only_file(destination)
    quick_check, table_count = _sqlite_quick_check(destination)
    if quick_check != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
    return {
        "path": destination.as_posix(),
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
        "quick_check": quick_check,
        "table_count": table_count,
    }


def _ignore_instance_database(directory: str, names: list[str]) -> set[str]:
    del directory
    return set(names) & DATABASE_SIDECARS


def _copy_runtime_path(source: Path, destination: Path, project_root: Path) -> int:
    if source.is_dir():
        ignore = _ignore_instance_database if source == project_root / "instance" else None
        shutil.copytree(source, destination, ignore=ignore)
        if os.name != "nt":
            for directory, _, filenames in os.walk(destination):
                Path(directory).chmod(stat.S_IRWXU)
                for filename in filenames:
                    _owner_only_file(Path(directory) / filename)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        _owner_only_file(destination)
    return 1


def _copy_data_root(source: Path, destination: Path, database: Path) -> None:
    excluded = {
        database.resolve(),
        Path(f"{database}-wal").resolve(),
        Path(f"{database}-shm").resolve(),
    }
    for path in source.rglob("*"):
        if path.resolve() in excluded:
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            _owner_only_file(target)
    if os.name != "nt":
        for directory, _, _ in os.walk(destination):
            Path(directory).chmod(stat.S_IRWXU)


def _manifest_files(snapshot: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        files.append(
            {
                "path": path.relative_to(snapshot).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files


def verify_snapshot(snapshot: Path) -> dict[str, object]:
    manifest_path = snapshot / MANIFEST_NAME
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported snapshot format")
    for item in manifest.get("files", []):
        path = snapshot / item["path"]
        if not path.is_file():
            raise FileNotFoundError(f"Snapshot file is missing: {item['path']}")
        if path.stat().st_size != item["bytes"] or _sha256(path) != item["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {item['path']}")
    database = snapshot / "database" / "users.db"
    quick_check, table_count = _sqlite_quick_check(database)
    if quick_check != "ok":
        raise RuntimeError(f"Restored SQLite quick_check failed: {quick_check}")
    expected_tables = manifest["database"]["table_count"]
    if table_count != expected_tables:
        raise RuntimeError(
            f"Restored table count differs: expected {expected_tables}, got {table_count}"
        )
    return {
        "snapshot": str(snapshot),
        "files": len(manifest.get("files", [])),
        "bytes": sum(int(item["bytes"]) for item in manifest.get("files", [])),
        "database_quick_check": quick_check,
        "database_tables": table_count,
    }


def _remove_old_snapshots(destination: Path, keep: int) -> list[str]:
    if keep < 1:
        raise ValueError("--keep must be at least 1")
    snapshots = sorted(
        (path for path in destination.iterdir() if path.is_dir() and path.name.startswith(SNAPSHOT_PREFIX)),
        key=lambda path: path.name,
        reverse=True,
    )
    removed: list[str] = []
    for path in snapshots[keep:]:
        resolved = path.resolve()
        if resolved.parent != destination.resolve() or not path.name.startswith(SNAPSHOT_PREFIX):
            raise RuntimeError(f"Refusing to remove unexpected path: {resolved}")
        shutil.rmtree(resolved)
        removed.append(path.name)
    return removed


def create_snapshot(
    project_root: Path,
    destination: Path,
    database: Path,
    runtime_paths: Iterable[str] = DEFAULT_RUNTIME_PATHS,
    keep: int | None = None,
    data_root: Path | None = None,
) -> tuple[Path, dict[str, object]]:
    project_root = project_root.resolve()
    destination = destination.resolve()
    database = database.resolve()
    if data_root is not None:
        data_root = data_root.resolve()
        if not data_root.is_dir():
            raise FileNotFoundError(f"DSTT data root does not exist: {data_root}")
    _owner_only_directory(destination)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"{SNAPSHOT_PREFIX}{stamp}"
    if final.exists():
        final = destination / f"{SNAPSHOT_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
    staging = destination / f".partial-{uuid.uuid4().hex}"
    _owner_only_directory(staging)
    try:
        database_info = _backup_sqlite(database, staging / "database" / "users.db")
        copied_sources: list[str] = []
        if data_root is not None:
            _copy_data_root(data_root, staging / "runtime" / "data", database)
            copied_sources.append(str(data_root))
        else:
            for relative in runtime_paths:
                source = (project_root / relative).resolve()
                try:
                    source.relative_to(project_root)
                except ValueError as exc:
                    raise ValueError(f"Runtime path escapes project root: {relative}") from exc
                if not source.exists():
                    continue
                _copy_runtime_path(source, staging / "runtime" / relative, project_root)
                copied_sources.append(relative)
        files = _manifest_files(staging)
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root),
            "database_source": str(database),
            "database": database_info,
            "runtime_sources": copied_sources,
            "files": files,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _owner_only_file(manifest_path)
        verify_snapshot(staging)
        staging.rename(final)
        latest = destination / "LATEST"
        latest.write_text(final.name + "\n", encoding="utf-8")
        _owner_only_file(latest)
        if keep is not None:
            manifest["removed_snapshots"] = _remove_old_snapshots(destination, keep)
        return final, verify_snapshot(final)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _parser() -> argparse.ArgumentParser:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=default_root)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--keep", type=int)
    parser.add_argument("--verify-only", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.verify_only:
        result = verify_snapshot(args.verify_only.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.destination is None:
        _parser().error("--destination is required unless --verify-only is used")
    project_root = args.project_root.resolve()
    database = (
        args.database
        or (args.data_root / "instance" / "users.db" if args.data_root else project_root / "instance" / "users.db")
    ).resolve()
    snapshot, result = create_snapshot(
        project_root=project_root,
        destination=args.destination,
        database=database,
        keep=args.keep,
        data_root=args.data_root,
    )
    result["snapshot"] = str(snapshot)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
