"""アトミックJSON書き込みヘルパーの検証。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.atomic_io import write_json_atomic


def test_writes_json_and_roundtrips(tmp_path):
    target = tmp_path / "permissions.json"
    payload = {"admins": ["alice"], "user_offices": {"bob": "tokyo"}}
    write_json_atomic(target, payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deep" / "data.json"
    write_json_atomic(target, {"x": 1})
    assert target.exists()


def test_no_leftover_temp_files(tmp_path):
    target = tmp_path / "permissions.json"
    write_json_atomic(target, {"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "permissions.json"]
    assert leftovers == []


def test_failed_write_preserves_existing_file(tmp_path):
    target = tmp_path / "permissions.json"
    write_json_atomic(target, {"admins": ["alice"]})

    # シリアライズ不能なオブジェクトで書き込み失敗を起こす。
    class NotSerializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {"bad": NotSerializable()})

    # 既存ファイルは壊れず元の内容のまま、tmp残骸も無い。
    assert json.loads(target.read_text(encoding="utf-8")) == {"admins": ["alice"]}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "permissions.json"]
    assert leftovers == []


def test_overwrites_existing_content(tmp_path):
    target = tmp_path / "permissions.json"
    write_json_atomic(target, {"v": 1})
    write_json_atomic(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
