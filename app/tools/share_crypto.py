"""AES-256-GCM at-rest encryption for FILE POST stored files.

File layout when encryption is active:
    HEADER (24 bytes)
        magic     [4]  = b"DSE\\x01"
        block_sz  [4]  = uint32 LE plaintext block size (default 1 MiB)
        salt      [16] = random per-file
    BLOCKS (until EOF)
        nonce     [12]
        tag       [16]
        clen      [4]  = uint32 LE ciphertext byte length
        ciphertext[clen]

Encryption: AES-256-GCM with per-file key = HKDF(master, salt, info=b"filepost").
AAD per block = uint64 LE block index, so reorder/truncation is detected.

If DSTT_SHARE_ENCRYPTION_KEY is not set, files are written in plain form.
On read, files lacking the magic bytes are streamed as-is so legacy data
keeps working.
"""

from __future__ import annotations

import base64
import logging
import os
import struct

logger = logging.getLogger(__name__)

MAGIC = b"DSE\x01"
HEADER_SIZE = 4 + 4 + 16
DEFAULT_BLOCK_SIZE = 1 << 20  # 1 MiB plaintext per block
NONCE_SIZE = 12
TAG_SIZE = 16
HKDF_INFO = b"filepost-at-rest"
_KEY_ENV = "DSTT_SHARE_ENCRYPTION_KEY"

_master_key_cache: bytes | None = None
_master_key_loaded = False


def _load_master_key() -> bytes | None:
    global _master_key_cache, _master_key_loaded
    if _master_key_loaded:
        return _master_key_cache
    _master_key_loaded = True
    raw = (os.environ.get(_KEY_ENV) or "").strip()
    if not raw:
        _master_key_cache = None
        return None
    candidates = []
    try:
        candidates.append(base64.b64decode(raw, validate=True))
    except Exception:
        pass
    try:
        candidates.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(raw))
    except Exception:
        pass
    for key in candidates:
        if len(key) == 32:
            _master_key_cache = key
            return key
    logger.error(
        "%s is set but is not a 32-byte key (base64 or hex). Encryption disabled.",
        _KEY_ENV,
    )
    _master_key_cache = None
    return None


def encryption_enabled() -> bool:
    return _load_master_key() is not None


def _derive_file_key(salt: bytes) -> bytes:
    from Cryptodome.Hash import SHA256
    from Cryptodome.Protocol.KDF import HKDF

    master = _load_master_key()
    if not master:
        raise RuntimeError("Encryption requested but no master key configured")
    return HKDF(master, 32, salt, SHA256, context=HKDF_INFO)


def encrypt_file(src_path: str, dst_path: str, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
    """Encrypt src_path → dst_path. Must only be called when encryption_enabled()."""
    from Cryptodome.Cipher import AES

    salt = os.urandom(16)
    key = _derive_file_key(salt)
    block_size = max(64 * 1024, int(block_size))

    tmp_path = f"{dst_path}.{os.getpid()}.enc.tmp"
    try:
        with open(src_path, "rb") as src, open(tmp_path, "wb") as dst:
            dst.write(MAGIC)
            dst.write(struct.pack("<I", block_size))
            dst.write(salt)
            block_index = 0
            while True:
                plaintext = src.read(block_size)
                if not plaintext:
                    if block_index == 0:
                        # empty file: still emit a single zero-length block so
                        # readers can verify integrity end-to-end.
                        nonce = os.urandom(NONCE_SIZE)
                        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
                        cipher.update(struct.pack("<Q", block_index))
                        ciphertext, tag = cipher.encrypt_and_digest(b"")
                        dst.write(nonce)
                        dst.write(tag)
                        dst.write(struct.pack("<I", len(ciphertext)))
                        dst.write(ciphertext)
                    break
                nonce = os.urandom(NONCE_SIZE)
                cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
                cipher.update(struct.pack("<Q", block_index))
                ciphertext, tag = cipher.encrypt_and_digest(plaintext)
                dst.write(nonce)
                dst.write(tag)
                dst.write(struct.pack("<I", len(ciphertext)))
                dst.write(ciphertext)
                block_index += 1
        os.replace(tmp_path, dst_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


def is_encrypted_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False


def iter_plaintext(path: str, chunk_size: int = 65536):
    """Yield plaintext bytes for an encrypted or plain file.

    If the file lacks the magic header, contents are streamed verbatim so
    pre-encryption files continue to work.
    """
    with open(path, "rb") as f:
        head = f.read(4)
        if head != MAGIC:
            # plaintext file — emit head + rest as-is
            if head:
                yield head
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    return
                yield buf
            return
        from Cryptodome.Cipher import AES

        block_sz_raw = f.read(4)
        salt = f.read(16)
        if len(block_sz_raw) != 4 or len(salt) != 16:
            raise ValueError("Corrupted encrypted file header")
        key = _derive_file_key(salt)
        block_index = 0
        while True:
            header = f.read(NONCE_SIZE + TAG_SIZE + 4)
            if not header:
                return
            if len(header) != NONCE_SIZE + TAG_SIZE + 4:
                raise ValueError("Truncated encrypted block header")
            nonce = header[:NONCE_SIZE]
            tag = header[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
            (clen,) = struct.unpack("<I", header[NONCE_SIZE + TAG_SIZE:])
            ciphertext = f.read(clen)
            if len(ciphertext) != clen:
                raise ValueError("Truncated encrypted block body")
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            cipher.update(struct.pack("<Q", block_index))
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            block_index += 1
            if plaintext:
                yield plaintext


def plaintext_size(path: str, fallback_size: int | None = None) -> int:
    """Return plaintext byte length. For plain files just statvfs; for encrypted
    files we don't store a length so callers should keep stored size in meta."""
    if fallback_size is not None and fallback_size >= 0:
        return fallback_size
    try:
        if not is_encrypted_file(path):
            return os.path.getsize(path)
    except OSError:
        return 0
    total = 0
    for chunk in iter_plaintext(path):
        total += len(chunk)
    return total
