"""Resolve mutable application data outside the source checkout.

``DSTT_DATA_DIR`` is intentionally the only deployment-level root. When it
is unset, callers keep their historical paths for backwards compatibility.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from flask import current_app, has_app_context


def configured_data_root(config: Mapping[str, object] | None = None) -> Path | None:
    raw = config.get("DSTT_DATA_DIR") if config else None
    if not raw and has_app_context():
        raw = current_app.config.get("DSTT_DATA_DIR")
    if not raw:
        raw = os.environ.get("DSTT_DATA_DIR")
    if not raw:
        return None
    return Path(str(raw)).expanduser().resolve()


def prepare_data_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(stat.S_IRWXU)


def instance_path(config: Mapping[str, object] | None = None) -> Path | None:
    root = configured_data_root(config)
    return root / "instance" if root else None


def runtime_path(*parts: str, legacy: str | os.PathLike[str]) -> Path:
    root = configured_data_root()
    return root.joinpath(*parts) if root else Path(legacy)
