from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import math
import mimetypes
import os
import random
import re
import shutil
import string
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file, session, stream_with_context, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from . import share_crypto

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover - optional dependency fallback
    BackgroundScheduler = None

share_bp = Blueprint("share", __name__, url_prefix="/tools/share")
logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "shared_files")
META_PATH = os.path.join(UPLOAD_DIR, "meta.json")
MAX_FILE_SIZE = 10 * 1024**3  # 10 GiB
CHUNK_SIZE = 50 * 1024**2  # 50 MiB
MAX_CHUNK_SIZE = CHUNK_SIZE + (2 * 1024**2)
_CLEANUP_LOCK_PATH = os.path.join(UPLOAD_DIR, ".cleanup.lock")
_CLEANUP_LOCK_STALE_SECONDS = 2 * 60 * 60
_CHUNK_SESSION_TTL_SECONDS = 24 * 60 * 60
_META_LOCK_PATH = os.path.join(UPLOAD_DIR, ".meta.lock")
_META_LOCK_STALE_SECONDS = 60
_META_LOCK_TIMEOUT_SECONDS = 30.0
MAX_EXPIRES_DAYS = 90
DOWNLOAD_TOKEN_TTL_SECONDS = 60 * 60
DOWNLOAD_TOKEN_MAX_PER_SESSION = 32
MAX_PREVIEW_PDF_BYTES = 50 * 1024**2
MAX_DOWNLOAD_LIMIT = 100000
PREVIEW_CACHE_DIR = os.path.join(UPLOAD_DIR, ".previews")
_DOWNLOAD_SIGNER_SALT = "filepost-download-token"
_UPLOAD_SIGNER_SALT = "filepost-chunk-upload"
_PREVIEW_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
    "Referrer-Policy": "no-referrer",
}
_scheduler = None
_scheduler_lock = threading.Lock()
_meta_lock = threading.RLock()

os.makedirs(UPLOAD_DIR, exist_ok=True)


@contextmanager
def _meta_critical_section(timeout: float = _META_LOCK_TIMEOUT_SECONDS):
    with _meta_lock:
        deadline = time.time() + timeout
        fd = None
        while True:
            try:
                fd = os.open(_META_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(_META_LOCK_PATH) > _META_LOCK_STALE_SECONDS:
                        os.remove(_META_LOCK_PATH)
                        continue
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("Failed to acquire shared meta lock")
                time.sleep(0.05)
        try:
            try:
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            except OSError:
                pass
            yield
        finally:
            try:
                os.close(fd)
            finally:
                try:
                    os.remove(_META_LOCK_PATH)
                except FileNotFoundError:
                    pass


def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_meta(meta):
    tmp_path = f"{META_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, META_PATH)


def _chunk_root() -> str:
    return os.path.join(UPLOAD_DIR, ".chunks")


def _chunk_session_dir(upload_id: str) -> str:
    return os.path.join(_chunk_root(), upload_id)


def _chunk_session_path(upload_id: str) -> str:
    return os.path.join(_chunk_session_dir(upload_id), "session.json")


def _load_chunk_session(upload_id: str) -> dict | None:
    if not _valid_token(upload_id):
        return None
    path = _chunk_session_path(upload_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_chunk_session(upload_id: str, payload: dict) -> None:
    session_dir = _chunk_session_dir(upload_id)
    os.makedirs(session_dir, exist_ok=True)
    tmp_path = os.path.join(session_dir, f"session.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, _chunk_session_path(upload_id))


def _valid_token(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,80}", str(value or "")))


def _safe_text(value: str | None, limit: int) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return value[:limit]


def _safe_original_name(filename: str | None) -> str:
    cleaned = _safe_text(filename or "file", 255)
    # Strip any directory parts (handles "..", "/", "\\", "C:\\foo\\bar", etc.).
    cleaned = cleaned.replace("\\", "/").split("/")[-1]
    cleaned = Path(cleaned).name
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
    cleaned = cleaned.strip(" .")
    return cleaned or "file"


def _safe_extension(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", ext or ""):
        return ""
    return ext


def _safe_zip_name(value: str | None, fallback: str) -> str:
    name = _safe_text(value, 120) or fallback
    name = name.replace("..", "").replace("/", "").replace("\\", "").strip(" .")
    name = name or fallback
    if not name.lower().endswith(".zip"):
        name = f"{name}.zip"
    return name


def _current_username() -> str:
    try:
        return current_user.username if getattr(current_user, "is_authenticated", False) else ""
    except Exception:
        return ""


def _is_current_uploader(info: dict) -> bool:
    username = _current_username()
    return bool(username and username == info.get("uploader"))


def _download_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_DOWNLOAD_SIGNER_SALT)


def _decode_download_token(token: str) -> str | None:
    if not isinstance(token, str):
        return None
    try:
        data = _download_signer().loads(token, max_age=DOWNLOAD_TOKEN_TTL_SECONDS)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if isinstance(data, dict):
        uid = data.get("uid")
        if isinstance(uid, str):
            return uid
    return None


def _prune_download_tokens(tokens: list) -> list:
    cleaned: list[str] = []
    for token in tokens:
        if not isinstance(token, str):
            continue
        if _decode_download_token(token) is None:
            continue
        cleaned.append(token)
    if len(cleaned) > DOWNLOAD_TOKEN_MAX_PER_SESSION:
        cleaned = cleaned[-DOWNLOAD_TOKEN_MAX_PER_SESSION:]
    return cleaned


def _normalize_session_tokens(raw) -> list:
    if isinstance(raw, list):
        return list(raw)
    # legacy dict storage from earlier revisions
    if isinstance(raw, dict):
        return [tok for tok in raw.keys() if isinstance(tok, str)]
    return []


def _issue_download_token(uid: str) -> str:
    tokens = _prune_download_tokens(_normalize_session_tokens(session.get("download_tokens")))
    token = _download_signer().dumps({"uid": uid})
    tokens.append(token)
    if len(tokens) > DOWNLOAD_TOKEN_MAX_PER_SESSION:
        tokens = tokens[-DOWNLOAD_TOKEN_MAX_PER_SESSION:]
    session["download_tokens"] = tokens
    session.modified = True
    return token


def _has_download_token(uid: str) -> bool:
    raw = _normalize_session_tokens(session.get("download_tokens"))
    tokens = _prune_download_tokens(raw)
    if tokens != raw:
        session["download_tokens"] = tokens
        session.modified = True
    for token in tokens:
        if _decode_download_token(token) == uid:
            return True
    return False


def _upload_signer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_UPLOAD_SIGNER_SALT)


def _sign_upload_id(inner_uuid: str, username: str) -> str:
    return _upload_signer().dumps({"u": inner_uuid, "n": username})


def _resolve_upload_id(token: str, username: str) -> str | None:
    """Return the inner uuid bound to this signed token & username, or None."""
    if not isinstance(token, str) or not token:
        return None
    # backwards-compat: if a bare uuid (pre-A3) is sent and the session file
    # still exists, accept it.
    if _valid_token(token):
        legacy = _load_chunk_session(token)
        if legacy and legacy.get("uploader") == username:
            return token
    try:
        data = _upload_signer().loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("n") != username:
        return None
    inner = data.get("u")
    if not isinstance(inner, str) or not _valid_token(inner):
        return None
    return inner


def _hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)


def _verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if "$" in stored_hash or stored_hash.startswith("pbkdf2:") or stored_hash.startswith("scrypt:"):
        try:
            return check_password_hash(stored_hash, password)
        except Exception:
            return False
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy, stored_hash)


def _can_preview(uid: str, info: dict) -> bool:
    return not info.get("password_hash") or _is_current_uploader(info) or _has_download_token(uid)


def _make_qr_data(download_url):
    try:
        import qrcode
    except ImportError:
        logger.warning("qrcode is not installed; FILE POST QR preview is disabled")
        return None

    qr = qrcode.make(download_url)
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, format="PNG")
    return base64.b64encode(qr_buffer.getvalue()).decode("utf-8")


def _acquire_cleanup_lock():
    try:
        fd = os.open(_CLEANUP_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        return fd
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(_CLEANUP_LOCK_PATH) > _CLEANUP_LOCK_STALE_SECONDS:
                os.remove(_CLEANUP_LOCK_PATH)
                return _acquire_cleanup_lock()
        except OSError:
            return None
        return None


def _release_cleanup_lock(fd):
    try:
        os.close(fd)
    finally:
        try:
            os.remove(_CLEANUP_LOCK_PATH)
        except FileNotFoundError:
            pass


def _resolve_expires_at(mode: str | None, custom_value: str | None = None) -> datetime:
    now = datetime.utcnow()
    max_dt = now + timedelta(days=MAX_EXPIRES_DAYS)
    mode = (mode or "7d").strip().lower()
    if mode == "1h":
        return now + timedelta(hours=1)
    if mode == "today":
        return now + timedelta(days=1)
    if mode == "3d":
        return now + timedelta(days=3)
    if mode == "14d":
        return now + timedelta(days=14)
    if mode == "custom" and custom_value:
        try:
            parsed = datetime.fromisoformat(str(custom_value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            if parsed > now:
                return min(parsed, max_dt)
        except ValueError:
            pass
    return now + timedelta(days=7)


def _file_count(info: dict) -> int:
    if "files" in info:
        return len(info["files"])
    return 1


def _files_for_info(info: dict) -> list[dict]:
    if "files" in info:
        return info["files"]
    file_path = os.path.join(UPLOAD_DIR, info.get("stored_filename", ""))
    total_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    return [
        {
            "original_name": info.get("filename", "download"),
            "stored_name": info.get("stored_filename", ""),
            "size": total_size,
            "mime_type": mimetypes.guess_type(info.get("filename", ""))[0] or "",
        }
    ]


def _preview_kind(file_info: dict) -> str | None:
    original_name = str(file_info.get("original_name") or "")
    mime_type = str(file_info.get("mime_type") or mimetypes.guess_type(original_name)[0] or "")
    ext = Path(original_name).suffix.lower()
    if mime_type in {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}:
        return "image"
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return "image"
    if mime_type == "application/pdf" or ext == ".pdf":
        return "pdf"
    return None


def _with_preview_metadata(uid: str, info: dict, files_detail: list[dict]) -> list[dict]:
    allow_preview = _can_preview(uid, info)
    decorated = []
    for idx, file_info in enumerate(files_detail):
        item = dict(file_info)
        kind = _preview_kind(item)
        item["preview_kind"] = kind
        item["preview_url"] = url_for("share.preview_file", uid=uid, file_index=idx) if allow_preview and kind else ""
        decorated.append(item)
    return decorated


def _cleanup_stale_chunk_sessions(now: float | None = None) -> None:
    root = _chunk_root()
    if not os.path.isdir(root):
        return
    now = now or time.time()
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        try:
            if now - entry.stat().st_mtime > _CHUNK_SESSION_TTL_SECONDS:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            logger.warning("Failed to inspect stale FILE POST chunk session %s", entry.path)


def cleanup_expired_files():
    lock_fd = _acquire_cleanup_lock()
    if lock_fd is None:
        return
    meta = load_meta()
    updated_meta = {}
    expired_uids = []
    now = datetime.utcnow()

    try:
        _cleanup_stale_chunk_sessions()
        for uid, info in meta.items():
            try:
                expired = datetime.fromisoformat(info["expires_at"]) < now
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping shared file cleanup for invalid metadata uid=%s", uid)
                updated_meta[uid] = info
                continue

            if expired:
                expired_uids.append(uid)
                for file_info in _files_for_info(info):
                    file_path = os.path.join(UPLOAD_DIR, file_info.get("stored_name", ""))
                    try:
                        os.remove(file_path)
                    except FileNotFoundError:
                        pass
            else:
                updated_meta[uid] = info

        latest_meta = load_meta()
        if latest_meta == meta:
            save_meta(updated_meta)
        else:
            for uid in expired_uids:
                latest_info = latest_meta.get(uid)
                try:
                    if latest_info and datetime.fromisoformat(latest_info["expires_at"]) < now:
                        latest_meta.pop(uid, None)
                except (KeyError, TypeError, ValueError):
                    pass
            save_meta(latest_meta)
    finally:
        _release_cleanup_lock(lock_fd)


def _config_bool(app, key, env_key, default=True):
    value = app.config.get(key)
    if value is None:
        value = os.environ.get(env_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def init_share_cleanup(app):
    if app.config.get("TESTING"):
        return
    if not _config_bool(app, "SHARE_CLEANUP_SCHEDULER_ENABLED", "DSTT_SHARE_CLEANUP_SCHEDULER", True):
        return

    cleanup_expired_files()

    if BackgroundScheduler is None:
        logger.warning("APScheduler is not installed; shared file cleanup scheduler is disabled")
        return

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=cleanup_expired_files,
            trigger="interval",
            hours=1,
            id="share_cleanup_expired_files",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        _scheduler.start()


def _coerce_max_downloads(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return min(parsed, MAX_DOWNLOAD_LIMIT)


def _create_share_record(
    *,
    files: list[dict],
    title: str,
    memo: str,
    zip_name: str,
    password: str = "",
    password_hash: str | None = None,
    expires_mode: str,
    expires_at_custom: str | None = None,
    max_downloads: int | None = None,
) -> dict:
    now = datetime.utcnow()
    expires_at = _resolve_expires_at(expires_mode, expires_at_custom)
    if password_hash is None:
        password_hash = _hash_password(password) if password else None

    with _meta_critical_section():
        meta = load_meta()
        uid = uuid.uuid4().hex[:8]
        while uid in meta:
            uid = uuid.uuid4().hex[:8]

        existing_ids = {info.get("display_id") for info in meta.values() if info.get("display_id")}
        display_id = None
        for _ in range(100):
            candidate = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
            if candidate not in existing_ids:
                display_id = candidate
                break
        if display_id is None:
            display_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

        meta[uid] = {
            "display_id": display_id,
            "title": _safe_text(title, 120),
            "memo": _safe_text(memo, 200),
            "zip_name": _safe_text(zip_name, 120),
            "uploader": _current_username() or "unknown",
            "password_hash": password_hash,
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
            "expires_mode": expires_mode or "7d",
            "files": files,
            "download_count": 0,
            "max_downloads": _coerce_max_downloads(max_downloads),
            "revoked_at": None,
        }
        save_meta(meta)

    download_url = url_for("share.download_file", uid=uid, _external=True)
    return {
        "uid": uid,
        "display_id": display_id,
        "download_url": download_url,
        "qr_data": _make_qr_data(download_url),
        "expires_at": expires_at.isoformat(),
    }


def _is_revoked(info: dict) -> bool:
    return bool(info.get("revoked_at"))


def _is_expired(info: dict) -> bool:
    try:
        return datetime.utcnow() > datetime.fromisoformat(info["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True


def _download_limit_reached(info: dict) -> bool:
    cap = info.get("max_downloads")
    if cap is None or cap == "":
        return False
    try:
        cap_int = int(cap)
    except (TypeError, ValueError):
        return False
    if cap_int <= 0:
        return False
    try:
        used = int(info.get("download_count") or 0)
    except (TypeError, ValueError):
        used = 0
    return used >= cap_int


def _increment_download_count(uid: str) -> None:
    with _meta_critical_section():
        meta = load_meta()
        info = meta.get(uid)
        if not info:
            return
        try:
            info["download_count"] = int(info.get("download_count") or 0) + 1
        except (TypeError, ValueError):
            info["download_count"] = 1
        meta[uid] = info
        save_meta(meta)


def _store_uploaded_payload(src_path: str, dst_path: str) -> None:
    """Move (or encrypt) a finalised plaintext file at src_path to dst_path."""
    if share_crypto.encryption_enabled():
        share_crypto.encrypt_file(src_path, dst_path)
        try:
            os.remove(src_path)
        except FileNotFoundError:
            pass
    else:
        os.replace(src_path, dst_path)


def _iter_file_plaintext(path: str):
    return share_crypto.iter_plaintext(path)


def _read_file_plaintext(path: str, max_bytes: int | None = None) -> bytes:
    buf = bytearray()
    for chunk in _iter_file_plaintext(path):
        buf.extend(chunk)
        if max_bytes is not None and len(buf) > max_bytes:
            raise ValueError("plaintext exceeds max_bytes")
    return bytes(buf)


@share_bp.route("/", methods=["GET", "POST"])
@login_required
def upload_share():
    download_url = None
    qr_data = None
    display_id = None

    if request.method == "POST":
        content_length = request.content_length
        if content_length and content_length > MAX_FILE_SIZE:
            return "アップロードできるファイルは合計10GBまでです。", 413

        files = request.files.getlist("files")
        password = request.form.get("password", "").strip()
        memo = request.form.get("memo", "").strip()
        title = request.form.get("title", "").strip()
        zip_name = request.form.get("zip_name", "").strip()
        expires_mode = request.form.get("expires_mode", "7d")
        expires_at_custom = request.form.get("expires_at_custom")
        max_downloads = _coerce_max_downloads(request.form.get("max_downloads"))

        if not files or all(f.filename == "" for f in files):
            return "ファイルを選択してください。", 400

        total_size = 0
        file_list = []
        uid_for_storage = uuid.uuid4().hex[:8]
        for idx, file in enumerate(files):
            if file.filename == "":
                continue
            original_name = _safe_original_name(file.filename)
            ext = _safe_extension(original_name)
            stored_filename = f"{uid_for_storage}_{idx}{ext}"
            save_path = os.path.join(UPLOAD_DIR, stored_filename)
            tmp_plain = f"{save_path}.{os.getpid()}.{threading.get_ident()}.plain.tmp"
            file.save(tmp_plain)
            file_size = os.path.getsize(tmp_plain)
            total_size += file_size
            if file_size <= 0:
                try:
                    os.remove(tmp_plain)
                except FileNotFoundError:
                    pass
                return "0バイトのファイルはアップロードできません。", 400
            if total_size > MAX_FILE_SIZE:
                try:
                    os.remove(tmp_plain)
                except FileNotFoundError:
                    pass
                return "アップロードできるファイルは合計10GBまでです。", 413
            _store_uploaded_payload(tmp_plain, save_path)

            file_list.append(
                {
                    "original_name": original_name,
                    "stored_name": stored_filename,
                    "size": file_size,
                    "mime_type": file.mimetype or mimetypes.guess_type(original_name)[0] or "",
                    "encrypted": share_crypto.encryption_enabled(),
                }
            )

        if not file_list:
            return "有効なファイルがありません。", 400

        result = _create_share_record(
            files=file_list,
            title=title,
            memo=memo,
            zip_name=zip_name,
            password=password,
            expires_mode=expires_mode,
            expires_at_custom=expires_at_custom,
            max_downloads=max_downloads,
        )
        download_url = result["download_url"]
        qr_data = result["qr_data"]
        display_id = result["display_id"]

    return render_template(
        "share.html",
        download_url=download_url,
        qr_data=qr_data,
        display_id=display_id,
        chunk_size=CHUNK_SIZE,
        max_file_size=MAX_FILE_SIZE,
    )


@share_bp.route("/api/chunk/init", methods=["POST"])
@login_required
def init_chunk_upload():
    payload = request.get_json(silent=True) or {}
    raw_files = payload.get("files") or []
    if not isinstance(raw_files, list) or not raw_files:
        return jsonify({"success": False, "error": "ファイル情報がありません。"}), 400

    files = []
    total_size = 0
    for raw in raw_files:
        try:
            size = int(raw.get("size", 0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "ファイルサイズが不正です。"}), 400
        if size <= 0:
            return jsonify({"success": False, "error": "0バイトのファイルはアップロードできません。"}), 400
        total_size += size
        if total_size > MAX_FILE_SIZE:
            return jsonify({"success": False, "error": "アップロードできるファイルは合計10GBまでです。"}), 413

        original_name = _safe_original_name(raw.get("name"))
        files.append(
            {
                "original_name": original_name,
                "size": size,
                "mime_type": _safe_text(raw.get("type"), 120) or mimetypes.guess_type(original_name)[0] or "",
                "chunk_count": max(1, math.ceil(size / CHUNK_SIZE)),
            }
        )

    inner_id = uuid.uuid4().hex
    uploader = _current_username() or "unknown"
    raw_password = str(payload.get("password") or "")
    session_payload = {
        "upload_id": inner_id,
        "uploader": uploader,
        "created_at": datetime.utcnow().isoformat(),
        "title": _safe_text(payload.get("title"), 120),
        "memo": _safe_text(payload.get("memo"), 200),
        "zip_name": _safe_text(payload.get("zip_name"), 120),
        "expires_mode": _safe_text(payload.get("expires_mode") or "7d", 20),
        "expires_at_custom": _safe_text(payload.get("expires_at_custom"), 40),
        "password_hash": _hash_password(raw_password) if raw_password else None,
        "max_downloads": _coerce_max_downloads(payload.get("max_downloads")),
        "files": files,
    }
    _save_chunk_session(inner_id, session_payload)
    signed = _sign_upload_id(inner_id, uploader)
    # `upload_id` stays the inner uuid so older clients (and direct file-path
    # access from tests) keep working; `signed_upload_id` is what
    # security-conscious clients should round-trip.
    return jsonify({
        "success": True,
        "upload_id": inner_id,
        "signed_upload_id": signed,
        "chunk_size": CHUNK_SIZE,
    })


@share_bp.route("/api/chunk/upload", methods=["POST"])
@login_required
def upload_chunk():
    if request.content_length and request.content_length > MAX_CHUNK_SIZE:
        return jsonify({"success": False, "error": "チャンクサイズが大きすぎます。"}), 413

    signed_upload_id = request.form.get("upload_id", "")
    try:
        file_index = int(request.form.get("file_index", ""))
        chunk_index = int(request.form.get("chunk_index", ""))
    except ValueError:
        return jsonify({"success": False, "error": "チャンク情報が不正です。"}), 400

    username = _current_username() or "unknown"
    upload_id = _resolve_upload_id(signed_upload_id, username)
    if not upload_id:
        return jsonify({"success": False, "error": "アップロード権限がありません。"}), 403

    upload_session = _load_chunk_session(upload_id)
    if not upload_session:
        return jsonify({"success": False, "error": "アップロードセッションが見つかりません。"}), 404
    if upload_session.get("uploader") != username:
        return jsonify({"success": False, "error": "アップロード権限がありません。"}), 403
    if file_index < 0 or file_index >= len(upload_session.get("files", [])):
        return jsonify({"success": False, "error": "ファイル番号が不正です。"}), 400

    file_meta = upload_session["files"][file_index]
    expected_chunks = int(file_meta.get("chunk_count") or 1)
    if chunk_index < 0 or chunk_index >= expected_chunks:
        return jsonify({"success": False, "error": "チャンク番号が不正です。"}), 400

    chunk = request.files.get("chunk")
    if not chunk:
        return jsonify({"success": False, "error": "チャンクデータがありません。"}), 400

    chunk_dir = os.path.join(_chunk_session_dir(upload_id), f"file_{file_index}")
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f"{chunk_index}.part")
    tmp_path = f"{chunk_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    chunk.save(tmp_path)
    chunk_size = os.path.getsize(tmp_path)
    if chunk_size > CHUNK_SIZE:
        os.remove(tmp_path)
        return jsonify({"success": False, "error": "チャンクサイズが50MBを超えています。"}), 413
    os.replace(tmp_path, chunk_path)

    uploaded_count = len([p for p in os.scandir(chunk_dir) if p.is_file() and p.name.endswith(".part")])
    return jsonify({"success": True, "uploaded_chunks": uploaded_count, "expected_chunks": expected_chunks})


@share_bp.route("/api/chunk/complete", methods=["POST"])
@login_required
def complete_chunk_upload():
    payload = request.get_json(silent=True) or {}
    signed_upload_id = payload.get("upload_id", "")
    username = _current_username() or "unknown"
    upload_id = _resolve_upload_id(signed_upload_id, username)
    if not upload_id:
        return jsonify({"success": False, "error": "アップロード権限がありません。"}), 403
    upload_session = _load_chunk_session(upload_id)
    if not upload_session:
        return jsonify({"success": False, "error": "アップロードセッションが見つかりません。"}), 404
    if upload_session.get("uploader") != username:
        return jsonify({"success": False, "error": "アップロード権限がありません。"}), 403

    for file_index, file_meta in enumerate(upload_session.get("files", [])):
        chunk_dir = os.path.join(_chunk_session_dir(upload_id), f"file_{file_index}")
        expected_chunks = int(file_meta.get("chunk_count") or 1)
        missing = [
            chunk_index
            for chunk_index in range(expected_chunks)
            if not os.path.exists(os.path.join(chunk_dir, f"{chunk_index}.part"))
        ]
        if missing:
            return jsonify({"success": False, "error": f"{file_meta['original_name']} の未送信チャンクがあります。"}), 400
        received_size = sum(os.path.getsize(os.path.join(chunk_dir, f"{chunk_index}.part")) for chunk_index in range(expected_chunks))
        if received_size != int(file_meta.get("size") or 0):
            return jsonify({"success": False, "error": f"{file_meta['original_name']} の受信サイズが一致しません。"}), 400

    stored_files = []
    completed = False
    storage_uid = uuid.uuid4().hex[:8]
    try:
        for file_index, file_meta in enumerate(upload_session.get("files", [])):
            chunk_dir = os.path.join(_chunk_session_dir(upload_id), f"file_{file_index}")
            expected_chunks = int(file_meta.get("chunk_count") or 1)
            missing = [
                chunk_index
                for chunk_index in range(expected_chunks)
                if not os.path.exists(os.path.join(chunk_dir, f"{chunk_index}.part"))
            ]
            if missing:
                return jsonify({"success": False, "error": f"{file_meta['original_name']} の未送信チャンクがあります。"}), 400

            ext = _safe_extension(file_meta["original_name"])
            stored_name = f"{storage_uid}_{file_index}{ext}"
            final_path = os.path.join(UPLOAD_DIR, stored_name)
            tmp_plain_path = f"{final_path}.{os.getpid()}.{threading.get_ident()}.plain.tmp"
            written_size = 0
            with open(tmp_plain_path, "wb") as final:
                for chunk_index in range(expected_chunks):
                    chunk_path = os.path.join(chunk_dir, f"{chunk_index}.part")
                    written_size += os.path.getsize(chunk_path)
                    with open(chunk_path, "rb") as chunk_file:
                        shutil.copyfileobj(chunk_file, final, length=1024 * 1024)

            expected_size = int(file_meta.get("size") or 0)
            if written_size != expected_size:
                try:
                    os.remove(tmp_plain_path)
                except FileNotFoundError:
                    pass
                return jsonify({"success": False, "error": f"{file_meta['original_name']} の結合サイズが一致しません。"}), 400
            _store_uploaded_payload(tmp_plain_path, final_path)

            stored_files.append(
                {
                    "original_name": file_meta["original_name"],
                    "stored_name": stored_name,
                    "size": written_size,
                    "mime_type": file_meta.get("mime_type", ""),
                    "encrypted": share_crypto.encryption_enabled(),
                }
            )

        result = _create_share_record(
            files=stored_files,
            title=upload_session.get("title", ""),
            memo=upload_session.get("memo", ""),
            zip_name=upload_session.get("zip_name", ""),
            password_hash=upload_session.get("password_hash"),
            expires_mode=upload_session.get("expires_mode", "7d"),
            expires_at_custom=upload_session.get("expires_at_custom", ""),
            max_downloads=upload_session.get("max_downloads"),
        )
        completed = True
    finally:
        if completed:
            shutil.rmtree(_chunk_session_dir(upload_id), ignore_errors=True)

    return jsonify({"success": True, **result})


def _check_download_password(uid: str, file_info: dict, file_index: int | None = None):
    if not file_info.get("password_hash"):
        return None
    if request.method == "GET" and _has_download_token(uid):
        return None
    if request.method == "GET":
        return render_template("share_password.html", uid=uid, file_index=file_index)

    input_pass = request.form.get("password", "")
    if not _verify_password(input_pass, file_info.get("password_hash", "")):
        return render_template("share_password.html", uid=uid, file_index=file_index, error="パスワードが違います。")
    _issue_download_token(uid)
    return None


class _ZipStreamBuffer:
    """A write-only, non-seekable file-like used as a sink for zipfile."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0

    def write(self, data) -> int:
        self._buf.extend(data)
        self._pos += len(data)
        return len(data)

    def flush(self) -> None:  # pragma: no cover - required interface
        pass

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return False

    def drain(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def _stream_single_file_response(uid: str, file_data: dict):
    file_path = os.path.join(UPLOAD_DIR, file_data["stored_name"])
    if not os.path.exists(file_path):
        return "ファイルが見つかりません。", 404

    def generate():
        try:
            for chunk in _iter_file_plaintext(file_path):
                yield chunk
        except Exception:
            logger.exception("FILE POST single download stream failed uid=%s", uid)
            return

    encoded = _safe_original_name(file_data.get("original_name") or "download")
    quoted = _rfc5987_filename(encoded)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{encoded}\"; filename*=UTF-8''{quoted}",
        "X-Content-Type-Options": "nosniff",
    }
    size = file_data.get("size")
    if isinstance(size, int) and size >= 0:
        headers["Content-Length"] = str(size)
    mimetype = file_data.get("mime_type") or mimetypes.guess_type(file_data.get("original_name") or "")[0] or "application/octet-stream"
    response = Response(stream_with_context(generate()), mimetype=mimetype, headers=headers)
    _increment_download_count(uid)
    return response


def _stream_zip_response(uid: str, file_info: dict, files: list[dict]):
    fallback = file_info.get("title") or f"{file_info.get('display_id', uid)}_files"
    download_name = _safe_zip_name(file_info.get("zip_name"), fallback)

    def generate():
        sink = _ZipStreamBuffer()
        try:
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                used_names = set()
                for item in files:
                    file_path = os.path.join(UPLOAD_DIR, item["stored_name"])
                    if not os.path.exists(file_path):
                        continue
                    arcname = item["original_name"]
                    if arcname in used_names:
                        base, ext = os.path.splitext(arcname)
                        counter = 2
                        while f"{base}_{counter}{ext}" in used_names:
                            counter += 1
                        arcname = f"{base}_{counter}{ext}"
                    used_names.add(arcname)
                    zinfo = zipfile.ZipInfo(filename=arcname)
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    with zf.open(zinfo, mode="w", force_zip64=True) as entry:
                        for chunk in _iter_file_plaintext(file_path):
                            entry.write(chunk)
                            out = sink.drain()
                            if out:
                                yield out
                    out = sink.drain()
                    if out:
                        yield out
            tail = sink.drain()
            if tail:
                yield tail
        except Exception:
            logger.exception("FILE POST zip stream failed uid=%s", uid)
            return

    quoted = _rfc5987_filename(download_name)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{download_name}\"; filename*=UTF-8''{quoted}",
        "X-Content-Type-Options": "nosniff",
    }
    response = Response(stream_with_context(generate()), mimetype="application/zip", headers=headers)
    _increment_download_count(uid)
    return response


def _rfc5987_filename(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _download_guard(file_info: dict):
    if _is_revoked(file_info):
        return "この共有は所有者により取り消されました。", 410
    if _is_expired(file_info):
        return "このファイルのダウンロード期限は過ぎています。", 410
    if _download_limit_reached(file_info):
        return "ダウンロード回数の上限に達しました。", 410
    return None


@share_bp.route("/download/<uid>", methods=["GET", "POST"])
def download_file(uid):
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです。", 404

    blocked = _download_guard(file_info)
    if blocked is not None:
        return blocked

    password_response = _check_download_password(uid, file_info)
    if password_response is not None:
        return password_response

    files = _files_for_info(file_info)
    if len(files) == 1:
        return _stream_single_file_response(uid, files[0])
    return _stream_zip_response(uid, file_info, files)


@share_bp.route("/download/<uid>/<int:file_index>", methods=["GET", "POST"])
def download_single_file(uid, file_index):
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです。", 404

    blocked = _download_guard(file_info)
    if blocked is not None:
        return blocked

    password_response = _check_download_password(uid, file_info, file_index=file_index)
    if password_response is not None:
        return password_response

    files = _files_for_info(file_info)
    if file_index < 0 or file_index >= len(files):
        return "ファイルが見つかりません。", 404

    return _stream_single_file_response(uid, files[file_index])


def _preview_response(payload: bytes, mimetype: str):
    response = Response(payload, mimetype=mimetype)
    for header, value in _PREVIEW_HEADERS.items():
        response.headers[header] = value
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


def _cached_pdf_preview(uid: str, file_index: int, file_path: str) -> bytes | None:
    os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(PREVIEW_CACHE_DIR, f"{uid}_{file_index}.png")
    try:
        src_mtime = os.path.getmtime(file_path)
    except OSError:
        return None
    try:
        if os.path.getmtime(cache_path) >= src_mtime:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        pass
    return None


def _store_pdf_preview(uid: str, file_index: int, payload: bytes) -> None:
    os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(PREVIEW_CACHE_DIR, f"{uid}_{file_index}.png")
    tmp_path = f"{cache_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(payload)
        os.replace(tmp_path, cache_path)
    except OSError:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


@share_bp.route("/preview/<uid>/<int:file_index>", methods=["GET"])
def preview_file(uid, file_index):
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです。", 404
    if _is_revoked(file_info):
        return "この共有は取り消されました。", 410
    if _is_expired(file_info):
        return "このファイルの期限は過ぎています。", 410
    if not _can_preview(uid, file_info):
        return "プレビューにはパスワード確認が必要です。", 403

    files = _files_for_info(file_info)
    if file_index < 0 or file_index >= len(files):
        return "ファイルが見つかりません。", 404

    item = files[file_index]
    kind = _preview_kind(item)
    if not kind:
        return "このファイルはプレビューできません。", 415

    file_path = os.path.join(UPLOAD_DIR, item["stored_name"])
    if not os.path.exists(file_path):
        return "ファイルが見つかりません。", 404

    declared_size = item.get("size")
    if not isinstance(declared_size, int):
        declared_size = None

    if kind == "image":
        mimetype = item.get("mime_type") or mimetypes.guess_type(item.get("original_name", ""))[0] or "image/jpeg"
        try:
            payload = _read_file_plaintext(file_path)
        except Exception:
            logger.exception("FILE POST image preview decryption failed uid=%s", uid)
            return "プレビューを表示できません。", 500
        return _preview_response(payload, mimetype)

    # PDF — cap input size to protect the renderer.
    if declared_size is not None and declared_size > MAX_PREVIEW_PDF_BYTES:
        return "プレビューできるPDFサイズの上限(50MB)を超えています。", 415

    cached = _cached_pdf_preview(uid, file_index, file_path)
    if cached is not None:
        return _preview_response(cached, "image/png")

    try:
        plaintext = _read_file_plaintext(file_path, max_bytes=MAX_PREVIEW_PDF_BYTES)
    except ValueError:
        return "プレビューできるPDFサイズの上限(50MB)を超えています。", 415
    except Exception:
        logger.exception("FILE POST PDF preview read failed uid=%s", uid)
        return "PDFプレビューを作成できませんでした。", 500

    try:
        import fitz

        doc = fitz.open(stream=plaintext, filetype="pdf")
        if doc.page_count < 1:
            doc.close()
            return "PDFにページがありません。", 415
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
        data = pix.tobytes("png")
        doc.close()
    except Exception:
        logger.exception("Failed to render FILE POST PDF preview uid=%s file_index=%s", uid, file_index)
        return "PDFプレビューを作成できませんでした。", 500

    _store_pdf_preview(uid, file_index, data)
    return _preview_response(data, "image/png")


@share_bp.route("/api/list", methods=["GET"])
@login_required
def get_file_list():
    meta = load_meta()
    file_list = []

    for uid, info in meta.items():
        try:
            if datetime.utcnow() > datetime.fromisoformat(info["expires_at"]):
                continue
        except (KeyError, TypeError, ValueError):
            continue

        files_detail = _files_for_info(info)
        total_size = sum(f.get("size", 0) for f in files_detail)
        file_list.append(
            {
                "uid": uid,
                "display_id": info.get("display_id", uid[:5].upper()),
                "title": info.get("title", ""),
                "memo": info.get("memo", ""),
                "zip_name": info.get("zip_name", ""),
                "uploader": info.get("uploader", "unknown"),
                "file_count": _file_count(info),
                "total_size": total_size,
                "files": _with_preview_metadata(uid, info, files_detail),
                "has_password": bool(info.get("password_hash")),
                "expires_at": info["expires_at"],
                "created_at": info.get("created_at", info["expires_at"]),
                "download_url": url_for("share.download_file", uid=uid, _external=True),
                "download_count": int(info.get("download_count") or 0),
                "max_downloads": info.get("max_downloads"),
                "revoked_at": info.get("revoked_at"),
            }
        )

    file_list.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify(file_list)


def _require_owner_share(uid: str):
    meta = load_meta()
    info = meta.get(uid)
    if not info:
        return None, None, (jsonify({"success": False, "error": "ファイルが見つかりません。"}), 404)
    if not _is_current_uploader(info):
        return None, None, (jsonify({"success": False, "error": "権限がありません。"}), 403)
    return meta, info, None


@share_bp.route("/api/share/<uid>/revoke", methods=["POST"])
@login_required
def revoke_share(uid):
    with _meta_critical_section():
        meta, info, err = _require_owner_share(uid)
        if err is not None:
            return err
        info["revoked_at"] = datetime.utcnow().isoformat()
        meta[uid] = info
        save_meta(meta)
    return jsonify({"success": True, "revoked_at": info["revoked_at"]})


@share_bp.route("/api/share/<uid>/extend", methods=["POST"])
@login_required
def extend_share(uid):
    payload = request.get_json(silent=True) or {}
    raw_value = payload.get("expires_at_custom") or payload.get("expires_at")
    if not raw_value:
        return jsonify({"success": False, "error": "有効期限が指定されていません。"}), 400
    new_expires = _resolve_expires_at("custom", str(raw_value))
    now = datetime.utcnow()
    if new_expires <= now:
        return jsonify({"success": False, "error": "有効期限は現在より後に設定してください。"}), 400
    with _meta_critical_section():
        meta, info, err = _require_owner_share(uid)
        if err is not None:
            return err
        info["expires_at"] = new_expires.isoformat()
        info["revoked_at"] = None
        meta[uid] = info
        save_meta(meta)
    return jsonify({"success": True, "expires_at": info["expires_at"]})


@share_bp.route("/api/share/<uid>/limits", methods=["POST"])
@login_required
def update_share_limits(uid):
    payload = request.get_json(silent=True) or {}
    if "max_downloads" not in payload:
        return jsonify({"success": False, "error": "max_downloads が指定されていません。"}), 400
    new_cap = _coerce_max_downloads(payload.get("max_downloads"))
    with _meta_critical_section():
        meta, info, err = _require_owner_share(uid)
        if err is not None:
            return err
        info["max_downloads"] = new_cap
        meta[uid] = info
        save_meta(meta)
    return jsonify({"success": True, "max_downloads": new_cap})


@share_bp.route("/api/chunk/status", methods=["GET"])
@login_required
def chunk_status():
    signed = request.args.get("upload_id", "")
    username = _current_username() or "unknown"
    upload_id = _resolve_upload_id(signed, username)
    if not upload_id:
        return jsonify({"success": False, "error": "アップロードセッションが見つかりません。"}), 404
    upload_session = _load_chunk_session(upload_id)
    if not upload_session or upload_session.get("uploader") != username:
        return jsonify({"success": False, "error": "アップロードセッションが見つかりません。"}), 404
    progress = []
    for file_index, file_meta in enumerate(upload_session.get("files", [])):
        chunk_dir = os.path.join(_chunk_session_dir(upload_id), f"file_{file_index}")
        uploaded = []
        if os.path.isdir(chunk_dir):
            for entry in os.scandir(chunk_dir):
                if entry.is_file() and entry.name.endswith(".part"):
                    try:
                        uploaded.append(int(entry.name[:-5]))
                    except ValueError:
                        continue
        uploaded.sort()
        progress.append({
            "original_name": file_meta.get("original_name", ""),
            "size": int(file_meta.get("size") or 0),
            "chunk_count": int(file_meta.get("chunk_count") or 1),
            "uploaded": uploaded,
        })
    return jsonify({
        "success": True,
        "upload_id": signed,
        "chunk_size": CHUNK_SIZE,
        "files": progress,
        "title": upload_session.get("title", ""),
        "created_at": upload_session.get("created_at", ""),
    })


@share_bp.route("/api/chunk/incomplete", methods=["GET"])
@login_required
def list_incomplete_uploads():
    username = _current_username() or "unknown"
    root = _chunk_root()
    incomplete = []
    if not os.path.isdir(root):
        return jsonify(incomplete)
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        upload_id = entry.name
        if not _valid_token(upload_id):
            continue
        upload_session = _load_chunk_session(upload_id)
        if not upload_session or upload_session.get("uploader") != username:
            continue
        files = upload_session.get("files", [])
        total = sum(int(f.get("size") or 0) for f in files)
        uploaded_bytes = 0
        per_file = []
        for file_index, file_meta in enumerate(files):
            chunk_dir = os.path.join(_chunk_session_dir(upload_id), f"file_{file_index}")
            uploaded_chunks = 0
            if os.path.isdir(chunk_dir):
                for f in os.scandir(chunk_dir):
                    if f.is_file() and f.name.endswith(".part"):
                        uploaded_chunks += 1
                        try:
                            uploaded_bytes += f.stat().st_size
                        except OSError:
                            continue
            per_file.append({
                "original_name": file_meta.get("original_name", ""),
                "size": int(file_meta.get("size") or 0),
                "chunk_count": int(file_meta.get("chunk_count") or 1),
                "uploaded_chunks": uploaded_chunks,
            })
        incomplete.append({
            "upload_id": _sign_upload_id(upload_id, username),
            "title": upload_session.get("title", ""),
            "created_at": upload_session.get("created_at", ""),
            "total_size": total,
            "uploaded_bytes": min(uploaded_bytes, total),
            "files": per_file,
        })
    incomplete.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(incomplete)


@share_bp.route("/api/delete/<uid>", methods=["POST"])
@login_required
def delete_file(uid):
    with _meta_critical_section():
        meta = load_meta()
        file_info = meta.get(uid)

        if not file_info:
            return jsonify({"success": False, "error": "ファイルが見つかりません。"}), 404

        if not _is_current_uploader(file_info):
            return jsonify({"success": False, "error": "削除権限がありません。"}), 403

        for item in _files_for_info(file_info):
            file_path = os.path.join(UPLOAD_DIR, item.get("stored_name", ""))
            try:
                os.remove(file_path)
            except FileNotFoundError:
                pass

        del meta[uid]
        save_meta(meta)
    return jsonify({"success": True})


@share_bp.route("/api/verify_password/<uid>", methods=["POST"])
def verify_password(uid):
    meta = load_meta()
    file_info = meta.get(uid)

    if not file_info:
        return jsonify({"success": False, "error": "無効なUIDです。"}), 404

    if not file_info.get("password_hash"):
        return jsonify({"success": True, "token": _issue_download_token(uid)})

    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")

    if _verify_password(password, file_info.get("password_hash", "")):
        return jsonify({"success": True, "token": _issue_download_token(uid)})
    return jsonify({"success": False, "error": "パスワードが違います。"})
