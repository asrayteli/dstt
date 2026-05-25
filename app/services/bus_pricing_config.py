from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context

from .bus_pricing_master import BLOCK_MASTER


CONFIG_FILENAME = "bus_pricing_rates.json"


def config_path() -> Path:
    if has_app_context():
        return Path(current_app.instance_path) / CONFIG_FILENAME
    return Path("instance") / CONFIG_FILENAME


def load_effective_block_master() -> dict[str, dict]:
    master = copy.deepcopy(BLOCK_MASTER)
    data = load_rate_config()
    for block_id, override in data.get("blocks", {}).items():
        if block_id not in master or not isinstance(override, dict):
            continue
        _deep_update(master[block_id], override)
    return master


def load_rate_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_rate_config(block_master: dict[str, dict]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version_note": "管理画面で編集された貸切バス単価設定",
        "blocks": block_master,
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def reset_rate_config() -> None:
    path = config_path()
    if path.exists():
        path.unlink()


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
