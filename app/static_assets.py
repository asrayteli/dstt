from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from flask import Flask, request
from werkzeug.utils import safe_join


@lru_cache(maxsize=512)
def _content_hash(path: str, mtime_ns: int, size: int) -> str:
    """Return a short content hash, invalidated when the file metadata changes."""
    del mtime_ns, size
    digest = hashlib.sha256()
    with Path(path).open("rb") as asset:
        for chunk in iter(lambda: asset.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def static_asset_version(static_folder: str | Path, filename: str) -> str | None:
    """Return a stable content version for a file inside ``static_folder``."""
    joined = safe_join(str(static_folder), filename)
    if joined is None:
        return None

    path = Path(joined)
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return _content_hash(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def enable_static_asset_versioning(app: Flask) -> None:
    """Append a content hash to URLs built for Flask static assets."""

    @app.url_defaults
    def _append_static_asset_version(endpoint: str, values: dict[str, object]) -> None:
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        if not isinstance(filename, str) or not app.static_folder:
            return
        version = static_asset_version(app.static_folder, filename)
        if version:
            values["v"] = version

    @app.after_request
    def _cache_versioned_static_asset(response):
        if request.endpoint != "static" or not app.static_folder:
            return response
        filename = (request.view_args or {}).get("filename")
        requested_version = request.args.get("v")
        if not isinstance(filename, str) or not requested_version:
            return response
        current_version = static_asset_version(app.static_folder, filename)
        if requested_version == current_version:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
