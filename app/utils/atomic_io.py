"""ファイルをアトミックに書き込むためのヘルパー。

権限ファイル(permissions.json)等を ``open(path, "w")`` で直接書くと、書き込み
途中でプロセスが落ちた場合にファイルが切り詰められて壊れ、権限情報が失われる
（最悪、全員が締め出される）。一時ファイルに書いてから ``os.replace`` で差し替える
ことで、対象ファイルが常に「書き込み前」か「書き込み後」のどちらか一貫した状態に
保たれる（同一ファイルシステム上では os.replace はアトミック）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """payload を JSON としてアトミックに path へ書き込む。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=ensure_ascii, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        # 差し替え成功時は tmp は消えている。失敗時の残骸だけ掃除する。
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
