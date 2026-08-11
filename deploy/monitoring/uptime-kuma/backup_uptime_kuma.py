#!/usr/bin/env python3
"""Create and verify portable Uptime Kuma data snapshots."""

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


SNAPSHOT_PREFIX = "snapshot-"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "kuma.db"
DATABASE_SIDECARS = {"kuma.db-wal", "kuma.db-shm"}


def _protect_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(stat.S_IRWXU)


def _protect_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_info(path: Path) -> dict[str, object]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        )
    return {
        "quick_check": quick_check,
        "table_count": table_count,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _backup_database(source: Path, destination: Path) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(f"Uptime Kuma database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)
    _protect_file(destination)
    result = _sqlite_info(destination)
    if result["quick_check"] != "ok":
        raise RuntimeError(f"Uptime Kuma SQLite quick_check failed: {result['quick_check']}")
    return result


def _copy_non_database_files(source: Path, destination: Path) -> None:
    excluded = {DATABASE_NAME, *DATABASE_SIDECARS}
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if relative.parts and relative.parts[0] == "screenshots":
            # Browser-monitor screenshots are cache, not configuration.
            continue
        if path.parent == source and path.name in excluded:
            continue
        if path.is_dir():
            _protect_directory(target)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            _protect_file(target)


def _manifest_files(snapshot: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        result.append(
            {
                "path": path.relative_to(snapshot).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return result


def verify_snapshot(snapshot: Path) -> dict[str, object]:
    manifest_path = snapshot / MANIFEST_NAME
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported Uptime Kuma backup format")
    for item in manifest.get("files", []):
        path = snapshot / item["path"]
        if not path.is_file():
            raise FileNotFoundError(f"Snapshot file is missing: {item['path']}")
        if path.stat().st_size != item["bytes"] or _sha256(path) != item["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {item['path']}")
    database = snapshot / "data" / DATABASE_NAME
    database_info = _sqlite_info(database)
    if database_info["quick_check"] != "ok":
        raise RuntimeError("Restored Uptime Kuma database failed quick_check")
    expected_tables = int(manifest["database"]["table_count"])
    if database_info["table_count"] != expected_tables:
        raise RuntimeError(
            "Restored Uptime Kuma table count differs: "
            f"expected {expected_tables}, got {database_info['table_count']}"
        )
    return {
        "snapshot": str(snapshot),
        "files": len(manifest.get("files", [])),
        "bytes": sum(int(item["bytes"]) for item in manifest.get("files", [])),
        "database_quick_check": database_info["quick_check"],
        "database_tables": database_info["table_count"],
    }


def _remove_old_snapshots(destination: Path, keep: int) -> list[str]:
    if keep < 1:
        raise ValueError("--keep must be at least 1")
    snapshots = sorted(
        (
            path
            for path in destination.iterdir()
            if path.is_dir() and path.name.startswith(SNAPSHOT_PREFIX)
        ),
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


def create_snapshot(data_directory: Path, destination: Path, keep: int = 14) -> tuple[Path, dict[str, object]]:
    data_directory = data_directory.resolve()
    destination = destination.resolve()
    if not data_directory.is_dir():
        raise FileNotFoundError(f"Uptime Kuma data directory does not exist: {data_directory}")
    _protect_directory(destination)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"{SNAPSHOT_PREFIX}{stamp}"
    if final.exists():
        final = destination / f"{SNAPSHOT_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
    staging = destination / f".partial-{uuid.uuid4().hex}"
    _protect_directory(staging)
    try:
        database_info = _backup_database(
            data_directory / DATABASE_NAME,
            staging / "data" / DATABASE_NAME,
        )
        _copy_non_database_files(data_directory, staging / "data")
        files = _manifest_files(staging)
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(data_directory),
            "database": database_info,
            "files": files,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _protect_file(manifest_path)
        verify_snapshot(staging)
        staging.rename(final)
        latest = destination / "LATEST"
        latest.write_text(final.name + "\n", encoding="utf-8")
        _protect_file(latest)
        manifest["removed_snapshots"] = _remove_old_snapshots(destination, keep)
        return final, verify_snapshot(final)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-directory", type=Path, default=Path("/var/lib/uptime-kuma"))
    parser.add_argument("--destination", type=Path, default=Path("/var/backups/uptime-kuma"))
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        result = verify_snapshot(args.verify_only.resolve())
    else:
        snapshot, result = create_snapshot(
            args.data_directory,
            args.destination,
            args.keep,
        )
        result["snapshot"] = str(snapshot)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
