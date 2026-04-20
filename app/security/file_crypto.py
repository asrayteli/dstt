"""AES-256-GCM による一時ファイル／カラム値の暗号化ユーティリティ。

設計方針
--------
- 暗号アルゴリズム: AES-256-GCM（pycryptodomex）。
- 鍵解決順序:
  1. 環境変数 ``DSTT_DATA_ENCRYPTION_KEY_B64``（Base64エンコードの32バイト）
  2. ``<instance>/data_encryption.key`` を自動生成して利用
- 既存機能を壊さないため、鍵未設定のテスト環境でも自動生成で動作する。
- 本番で環境変数指定を必須にしたい場合は ``DSTT_REQUIRE_ENCRYPTION_KEY_ENV=1`` を設定。

ファイル形式
------------
``DSTT1\\x00`` (6B) + nonce (12B) + tag (16B) + ciphertext
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from Cryptodome.Cipher import AES


CIPHER_MAGIC = b"DSTT1\x00"
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32

_KEY_CACHE: Optional[bytes] = None


class EncryptionKeyMissingError(RuntimeError):
    """必須設定のはずの暗号鍵環境変数が未設定。"""


def reset_key_cache_for_tests() -> None:
    """テスト用: プロセス内の鍵キャッシュを破棄する。"""
    global _KEY_CACHE
    _KEY_CACHE = None


def _load_or_create_instance_key(instance_path: str) -> bytes:
    key_path = Path(instance_path) / "data_encryption.key"
    if key_path.exists():
        content = key_path.read_bytes().strip()
        try:
            raw = base64.b64decode(content)
            if len(raw) == KEY_SIZE:
                return raw
        except Exception:
            pass
    key_path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(KEY_SIZE)
    key_path.write_bytes(base64.b64encode(raw))
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return raw


def _resolve_instance_path() -> str:
    try:
        from flask import current_app
        return current_app.instance_path
    except RuntimeError:
        return os.path.join(os.getcwd(), "instance")


def get_data_encryption_key() -> bytes:
    """現在の暗号鍵を返す。プロセス内でキャッシュする。"""
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE

    env_key = (os.environ.get("DSTT_DATA_ENCRYPTION_KEY_B64") or "").strip()
    if env_key:
        try:
            raw = base64.b64decode(env_key)
        except Exception as exc:
            raise RuntimeError(
                "DSTT_DATA_ENCRYPTION_KEY_B64 の Base64 デコードに失敗しました"
            ) from exc
        if len(raw) != KEY_SIZE:
            raise RuntimeError(
                f"DSTT_DATA_ENCRYPTION_KEY_B64 は {KEY_SIZE} バイトにデコードされる必要があります"
            )
        _KEY_CACHE = raw
        return raw

    require_env = (os.environ.get("DSTT_REQUIRE_ENCRYPTION_KEY_ENV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if require_env:
        raise EncryptionKeyMissingError(
            "本番設定: DSTT_DATA_ENCRYPTION_KEY_B64 が未設定です。"
        )

    _KEY_CACHE = _load_or_create_instance_key(_resolve_instance_path())
    return _KEY_CACHE


def encrypt_bytes(plaintext: bytes, aad: bytes = b"") -> bytes:
    """バイト列を AES-256-GCM で暗号化する。戻り値は magic+nonce+tag+ct。"""
    if plaintext is None:
        raise ValueError("plaintext must not be None")
    key = get_data_encryption_key()
    nonce = secrets.token_bytes(NONCE_SIZE)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return CIPHER_MAGIC + nonce + tag + ct


def decrypt_bytes(blob: bytes, aad: bytes = b"") -> bytes:
    """``encrypt_bytes`` で作られたバイト列を復号する。"""
    if not blob or not blob.startswith(CIPHER_MAGIC):
        raise ValueError("Unknown cipher format")
    body = blob[len(CIPHER_MAGIC):]
    if len(body) < NONCE_SIZE + TAG_SIZE:
        raise ValueError("Ciphertext too short")
    nonce = body[:NONCE_SIZE]
    tag = body[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
    ct = body[NONCE_SIZE + TAG_SIZE:]
    key = get_data_encryption_key()
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    return cipher.decrypt_and_verify(ct, tag)


def is_encrypted_blob(blob: bytes) -> bool:
    return bool(blob) and blob.startswith(CIPHER_MAGIC)


# ------------------------------------------------------------------
# 高レベルラッパー（JSON / ファイル）
# ------------------------------------------------------------------


def encrypt_json_to_file(path: str, data: Any) -> None:
    """JSON 直列化可能なオブジェクトを暗号化してファイルに書き出す。"""
    serialized = json.dumps(data, ensure_ascii=False).encode("utf-8")
    blob = encrypt_bytes(serialized)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(blob)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def decrypt_json_from_file(path: str) -> Any:
    """``encrypt_json_to_file`` で書かれたファイルを読み込む。

    旧式の平文 JSON ファイルも（移行期の後方互換として）読める。
    """
    with open(path, "rb") as f:
        blob = f.read()
    if not blob:
        raise ValueError("Encrypted file is empty")
    if is_encrypted_blob(blob):
        plaintext = decrypt_bytes(blob)
        return json.loads(plaintext.decode("utf-8"))
    # 後方互換: 既存の平文 JSON
    return json.loads(blob.decode("utf-8"))


def try_safe_unlink(path: str) -> None:
    """存在すれば削除し、例外は握りつぶす（cleanup 用途）。"""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
