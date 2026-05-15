from __future__ import annotations

import fnmatch
import os
import re
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pyzipper

ALLOWED_FORMATS = {"zip", "tar", "tar.gz", "tar.bz2", "tar.xz"}
MAX_FILES = 500
MAX_TOTAL_SIZE = 1024 * 1024 * 1024
DEFAULT_ARCHIVE_NAME = "dstt_compressed"


class CompressValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveEntry:
    source_path: str
    archive_name: str


@dataclass(frozen=True)
class PlannedFile:
    original_name: str
    safe_name: str
    archive_name: str | None
    size: int
    excluded_reason: str | None = None

    @property
    def included(self) -> bool:
        return self.excluded_reason is None


def safe_filename(filename: str) -> str:
    """Return a filename that cannot escape the archive or filesystem target."""
    filename = (filename or "").replace("\\", "/").split("/")[-1]
    filename = filename.replace("\x00", "")
    filename = unicodedata.normalize("NFC", filename)
    filename = filename.lstrip(".")
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    filename = re.sub(r'[<>:"|?*]', "", filename)
    filename = filename.strip()
    return filename or "unnamed_file"


def safe_archive_path(path: str) -> str:
    parts = []
    for part in (path or "").replace("\\", "/").split("/"):
        raw = part.strip()
        if not raw or raw in {".", ".."}:
            continue
        cleaned = safe_filename(raw)
        if cleaned and cleaned != "unnamed_file":
            parts.append(cleaned)
    return "/".join(parts) or "unnamed_file"


def sanitize_archive_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return DEFAULT_ARCHIVE_NAME
    cleaned = safe_filename(raw)
    cleaned = re.sub(r"\.+$", "", cleaned).strip()
    if not cleaned or cleaned == "unnamed_file":
        return DEFAULT_ARCHIVE_NAME
    return cleaned


def sanitize_root_folder(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    cleaned = safe_filename(raw)
    return "" if cleaned == "unnamed_file" else cleaned


def parse_csv_values(raw: str) -> list[str]:
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def normalize_exclude_exts(raw: str | Iterable[str]) -> list[str]:
    values = parse_csv_values(raw) if isinstance(raw, str) else list(raw)
    normalized = []
    for value in values:
        ext = value.strip().lower()
        if not ext:
            continue
        normalized.append(ext if ext.startswith(".") else f".{ext}")
    return normalized


def normalize_exclude_patterns(raw: str | Iterable[str]) -> list[str]:
    values = parse_csv_values(raw) if isinstance(raw, str) else list(raw)
    return [value.strip().lower() for value in values if value.strip()]


def exclusion_reason(path: str, exclude_exts: list[str], exclude_patterns: list[str]) -> str | None:
    lower_path = safe_archive_path(path).lower()
    lower_name = lower_path.rsplit("/", 1)[-1]
    ext = os.path.splitext(lower_name)[1]
    if ext and ext in exclude_exts:
        return f"拡張子 {ext}"
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(lower_path, pattern) or fnmatch.fnmatch(lower_name, pattern):
            return f"パターン {pattern}"
    return None


def should_exclude(filename: str, exclude_exts: list[str], exclude_patterns: list[str]) -> bool:
    return exclusion_reason(filename, exclude_exts, exclude_patterns) is not None


def make_unique_name(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        used_names.add(filename)
        return filename

    stem, ext = os.path.splitext(filename)
    idx = 1
    while True:
        candidate = f"{stem} ({idx}){ext}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        idx += 1


def make_unique_archive_path(path: str, used_names: set[str]) -> str:
    safe_path = safe_archive_path(path)
    if safe_path not in used_names:
        used_names.add(safe_path)
        return safe_path

    directory, filename = os.path.split(safe_path)
    stem, ext = os.path.splitext(filename)
    idx = 1
    while True:
        candidate_name = f"{stem} ({idx}){ext}"
        candidate = f"{directory}/{candidate_name}" if directory else candidate_name
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        idx += 1


def build_arcname(filename: str, root_folder: str) -> str:
    safe_name = safe_archive_path(filename)
    root = sanitize_root_folder(root_folder)
    if not root:
        return safe_name
    return f"{root}/{safe_name}"


def get_compresslevel(level: str) -> int:
    if level == "high":
        return 9
    return 6


def _normalize_sizes(sizes: Iterable[int | str | None] | None, count: int) -> list[int]:
    raw_sizes = list(sizes or [])
    normalized = []
    for idx in range(count):
        try:
            value = int(raw_sizes[idx]) if idx < len(raw_sizes) and raw_sizes[idx] is not None else 0
        except (TypeError, ValueError):
            value = 0
        normalized.append(max(0, value))
    return normalized


def plan_files(
    filenames: Iterable[str],
    *,
    relative_paths: Iterable[str] | None = None,
    sizes: Iterable[int | str | None] | None = None,
    exclude_exts: str | Iterable[str] = "",
    exclude_patterns: str | Iterable[str] = "",
) -> list[PlannedFile]:
    raw_names = [str(name) for name in filenames if str(name).strip()]
    raw_paths = list(relative_paths or [])
    file_sizes = _normalize_sizes(sizes, len(raw_names))
    exts = normalize_exclude_exts(exclude_exts)
    patterns = normalize_exclude_patterns(exclude_patterns)
    used_names: set[str] = set()
    planned: list[PlannedFile] = []

    for idx, (original_name, size) in enumerate(zip(raw_names, file_sizes)):
        relative_path = str(raw_paths[idx]).strip() if idx < len(raw_paths) else ""
        safe_name = safe_archive_path(relative_path or original_name)
        reason = exclusion_reason(safe_name, exts, patterns)
        if reason:
            planned.append(
                PlannedFile(
                    original_name=original_name,
                    safe_name=safe_name,
                    archive_name=None,
                    size=size,
                    excluded_reason=reason,
                )
            )
            continue
        archive_name = make_unique_archive_path(safe_name, used_names)
        planned.append(
            PlannedFile(
                original_name=original_name,
                safe_name=safe_name,
                archive_name=archive_name,
                size=size,
            )
        )
    return planned


def build_preview(
    *,
    filenames: Iterable[str],
    relative_paths: Iterable[str] | None = None,
    sizes: Iterable[int | str | None] | None = None,
    fmt: str = "zip",
    archive_name: str = "",
    root_folder: str = "",
    exclude_exts: str | Iterable[str] = "",
    exclude_patterns: str | Iterable[str] = "",
) -> dict:
    if fmt not in ALLOWED_FORMATS:
        raise CompressValidationError("圧縮形式の指定が不正です。")

    planned = plan_files(
        filenames,
        relative_paths=relative_paths,
        sizes=sizes,
        exclude_exts=exclude_exts,
        exclude_patterns=exclude_patterns,
    )
    included = [item for item in planned if item.included]
    excluded = [item for item in planned if not item.included]
    renamed_count = sum(1 for item in included if item.archive_name != item.safe_name)
    total_size = sum(item.size for item in included)

    warnings = []
    if not included:
        warnings.append("対象ファイルがありません。")
    if len(planned) > MAX_FILES:
        warnings.append(f"一度に処理できるファイル数は{MAX_FILES}件までです。")
    if total_size > MAX_TOTAL_SIZE:
        warnings.append(f"合計サイズは{format_bytes(MAX_TOTAL_SIZE)}までです。")

    root = sanitize_root_folder(root_folder)
    return {
        "archive_name": f"{sanitize_archive_name(archive_name)}.{fmt}",
        "root_folder": root,
        "total": len(planned),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "renamed_count": renamed_count,
        "total_size": total_size,
        "formatted_total_size": format_bytes(total_size),
        "included_preview": [build_arcname(item.archive_name or item.safe_name, root) for item in included[:8]],
        "excluded_preview": [item.safe_name for item in excluded[:8]],
        "excluded_reasons": [
            {"name": item.safe_name, "reason": item.excluded_reason or ""}
            for item in excluded[:8]
        ],
        "files": [
            {
                "original_name": item.original_name,
                "safe_name": item.safe_name,
                "archive_name": build_arcname(item.archive_name or item.safe_name, root) if item.included else "",
                "size": item.size,
                "formatted_size": format_bytes(item.size),
                "included": item.included,
                "excluded_reason": item.excluded_reason or "",
            }
            for item in planned
        ],
        "warnings": warnings,
    }


def validate_planned_files(planned: list[PlannedFile], total_size: int) -> None:
    if not planned:
        raise CompressValidationError("ファイルが選択されていません。")
    if len(planned) > MAX_FILES:
        raise CompressValidationError(f"一度に処理できるファイル数は{MAX_FILES}件までです。")
    if not any(item.included for item in planned):
        raise CompressValidationError("除外設定により、圧縮対象ファイルが0件になりました。")
    if total_size > MAX_TOTAL_SIZE:
        raise CompressValidationError(f"合計サイズは{format_bytes(MAX_TOTAL_SIZE)}までです。")


def validate_planned_file_count(planned: list[PlannedFile]) -> None:
    validate_planned_files(planned, 0)


def create_archive(
    *,
    archive_path: str,
    fmt: str,
    entries: Iterable[ArchiveEntry],
    root_folder: str = "",
    password: str = "",
    compress_level: str = "standard",
) -> None:
    if fmt not in ALLOWED_FORMATS:
        raise CompressValidationError("圧縮形式の指定が不正です。")

    compresslevel = get_compresslevel(compress_level)
    entry_list = list(entries)

    if fmt == "zip":
        if password:
            with pyzipper.AESZipFile(
                archive_path,
                "w",
                compression=pyzipper.ZIP_DEFLATED,
                compresslevel=compresslevel,
            ) as zipf:
                zipf.setpassword(password.encode("utf-8"))
                zipf.setencryption(pyzipper.WZ_AES, nbits=256)
                for entry in entry_list:
                    zipf.write(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))
        else:
            with zipfile.ZipFile(
                archive_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=compresslevel,
            ) as zipf:
                for entry in entry_list:
                    zipf.write(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))
        return

    if fmt == "tar":
        with tarfile.open(archive_path, "w") as tarf:
            for entry in entry_list:
                tarf.add(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))
        return

    if fmt == "tar.gz":
        with tarfile.open(archive_path, "w:gz", compresslevel=compresslevel) as tarf:
            for entry in entry_list:
                tarf.add(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))
        return

    if fmt == "tar.bz2":
        with tarfile.open(archive_path, "w:bz2", compresslevel=compresslevel) as tarf:
            for entry in entry_list:
                tarf.add(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))
        return

    if fmt == "tar.xz":
        with tarfile.open(archive_path, "w:xz") as tarf:
            for entry in entry_list:
                tarf.add(entry.source_path, arcname=build_arcname(entry.archive_name, root_folder))


def save_included_uploads(files, upload_dir: str, planned: list[PlannedFile]) -> tuple[list[ArchiveEntry], int]:
    validate_planned_file_count(planned)
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    entries: list[ArchiveEntry] = []
    total_size = 0

    for storage, plan in zip(files, planned):
        if not plan.included or not plan.archive_name:
            continue
        target_path = os.path.join(upload_dir, plan.archive_name)
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        storage.save(target_path)
        size = os.path.getsize(target_path)
        total_size += size
        entries.append(ArchiveEntry(source_path=target_path, archive_name=plan.archive_name))

    validate_planned_files(planned, total_size)
    return entries, total_size


def format_bytes(num_bytes: int) -> str:
    size = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
