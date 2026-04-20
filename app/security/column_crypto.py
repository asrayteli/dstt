"""SQLAlchemy カラム単位の透過的暗号化型。

用途
----
- ``EditHistory.old_value`` / ``EditHistory.new_value`` のように
  表示専用で検索されないが個人情報を含むテキスト列を暗号化する。

互換性
------
- 既存レコードに平文が入っていても壊れないよう、復号に失敗したら
  そのまま返す（= 既存平文はそのまま表示）。
- 新規書き込み時は常に暗号化される。
- プリフィックス ``enc1:`` を付けて、平文と暗号文を区別する。
"""
from __future__ import annotations

import base64
from typing import Optional

from sqlalchemy.types import Text, TypeDecorator

from .file_crypto import decrypt_bytes, encrypt_bytes


ENC_PREFIX = "enc1:"


def _encrypt_text(value: str) -> str:
    blob = encrypt_bytes(value.encode("utf-8"))
    return ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def _decrypt_text_or_passthrough(value: str) -> str:
    if not value.startswith(ENC_PREFIX):
        return value
    payload = value[len(ENC_PREFIX):]
    try:
        blob = base64.b64decode(payload)
        return decrypt_bytes(blob).decode("utf-8")
    except Exception:
        # 鍵の不整合や破損時は、アプリを停止させずに暗号文そのものを返す。
        # データ消失を防ぎ、後から鍵を戻せば再度正しく復号できる。
        return value


class EncryptedText(TypeDecorator):
    """暗号化つきテキスト列。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if value == "":
            return value
        if value.startswith(ENC_PREFIX):
            return value  # 二重暗号化を避ける
        return _encrypt_text(value)

    def process_result_value(self, value, dialect) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            return value
        return _decrypt_text_or_passthrough(value)
