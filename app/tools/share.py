from flask import Blueprint, render_template, request, send_file, url_for, jsonify, session
import os, uuid, hashlib, hmac, json, io, base64, zipfile, string, random, logging, threading, time
from datetime import datetime, timedelta
from flask_login import login_required, current_user

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover - optional dependency fallback
    BackgroundScheduler = None

share_bp = Blueprint("share", __name__, url_prefix="/tools/share")
logger = logging.getLogger(__name__)

# 絶対パスで保存場所を指定
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "shared_files")
META_PATH = os.path.join(UPLOAD_DIR, "meta.json")
MAX_FILE_SIZE = 10 * 1024**3  # 10GB
_CLEANUP_LOCK_PATH = os.path.join(UPLOAD_DIR, ".cleanup.lock")
_CLEANUP_LOCK_STALE_SECONDS = 2 * 60 * 60
_scheduler = None
_scheduler_lock = threading.Lock()

os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- メタ情報の読み書き ---
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


def _issue_download_token(uid):
    token = uuid.uuid4().hex
    tokens = dict(session.get("download_tokens") or {})
    tokens[token] = uid
    session["download_tokens"] = tokens
    session.modified = True
    return token


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

# --- 5桁表示ID生成（衝突チェック付き）---
def generate_display_id():
    """5桁の英数字ID（大文字+数字）を生成、既存IDと重複しない"""
    meta = load_meta()
    existing_ids = {info.get("display_id") for info in meta.values() if info.get("display_id")}

    max_attempts = 100
    for _ in range(max_attempts):
        display_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        if display_id not in existing_ids:
            return display_id

    # 100回試行して重複した場合は6桁にフォールバック
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# --- 期限切れファイルを削除 ---
def cleanup_expired_files():
    """期限切れファイルとメタデータを削除"""
    lock_fd = _acquire_cleanup_lock()
    if lock_fd is None:
        return
    meta = load_meta()
    updated_meta = {}
    expired_uids = []
    now = datetime.utcnow()

    try:
        for uid, info in meta.items():
            try:
                expired = datetime.fromisoformat(info["expires_at"]) < now
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping shared file cleanup for invalid metadata uid=%s", uid)
                updated_meta[uid] = info
                continue

            if expired:
                expired_uids.append(uid)
                # 複数ファイルに対応
                if "files" in info:  # 新形式
                    for file_info in info["files"]:
                        file_path = os.path.join(UPLOAD_DIR, file_info["stored_name"])
                        try:
                            os.remove(file_path)
                        except FileNotFoundError:
                            pass
                else:  # 旧形式（互換性のため）
                    file_path = os.path.join(UPLOAD_DIR, info.get("stored_filename", ""))
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


@share_bp.route("/", methods=["GET", "POST"])
@login_required
def upload_share():
    download_url = None
    qr_data = None
    display_id = None

    if request.method == "POST":
        # ファイルサイズ確認
        content_length = request.content_length
        if content_length and content_length > MAX_FILE_SIZE:
            return "アップロードできるファイルは10GBまでです。", 413

        # 複数ファイル取得
        files = request.files.getlist("files")
        password = request.form.get("password", "").strip()
        memo = request.form.get("memo", "").strip()

        if not files or all(f.filename == "" for f in files):
            return "ファイルを選択してください", 400

        # UID生成（衝突チェック）
        uid = uuid.uuid4().hex[:8]
        meta = load_meta()
        while uid in meta:
            uid = uuid.uuid4().hex[:8]

        # 5桁表示ID生成
        display_id = generate_display_id()

        # ファイル保存
        file_list = []
        for idx, file in enumerate(files):
            if file.filename == "":
                continue

            # オリジナルのファイル名を保持（日本語対応）
            original_filename = file.filename

            # パストラバーサル攻撃を防ぐため、危険な文字を除去
            # ただし日本語は保持する
            safe_original = original_filename.replace("..", "").replace("/", "").replace("\\", "")

            # 拡張子を取得
            _, ext = os.path.splitext(original_filename)

            # 保存時のファイル名は安全なUID+インデックス+拡張子
            stored_filename = f"{uid}_{idx}{ext}"
            save_path = os.path.join(UPLOAD_DIR, stored_filename)
            file.save(save_path)

            file_size = os.path.getsize(save_path)
            file_list.append({
                "original_name": safe_original,  # 日本語を含むオリジナル名
                "stored_name": stored_filename,
                "size": file_size
            })

        if not file_list:
            return "有効なファイルがありません", 400

        # メタデータ作成
        password_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
        now = datetime.utcnow()
        expires_at = (now + timedelta(days=7)).isoformat()

        meta[uid] = {
            "display_id": display_id,
            "memo": memo,
            "uploader": current_user.username if hasattr(current_user, 'username') else "unknown",
            "password_hash": password_hash,
            "expires_at": expires_at,
            "created_at": now.isoformat(),
            "files": file_list
        }
        save_meta(meta)

        # ダウンロードURLを生成
        download_url = url_for("share.download_file", uid=uid, _external=True)

        # QRコード生成（保存せずにBase64エンコード）
        qr_data = _make_qr_data(download_url)

    return render_template("share.html", download_url=download_url, qr_data=qr_data, display_id=display_id)


@share_bp.route("/download/<uid>", methods=["GET", "POST"])
def download_file(uid):
    """全ファイルをZIPで一括ダウンロード（複数ファイルの場合）または単一ファイルダウンロード"""
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです", 404

    if datetime.utcnow() > datetime.fromisoformat(file_info["expires_at"]):
        return "このファイルのダウンロード期限は過ぎています", 410

    # パスワード認証
    if file_info.get("password_hash"):
        if request.method == "GET":
            return render_template("share_password.html", uid=uid)
        input_pass = request.form.get("password", "")
        input_hash = hashlib.sha256(input_pass.encode()).hexdigest()
        if input_hash != file_info["password_hash"]:
            return render_template("share_password.html", uid=uid, error="パスワードが違います")

    # 新形式（複数ファイル対応）
    if "files" in file_info:
        files = file_info["files"]

        # 単一ファイルの場合は直接ダウンロード
        if len(files) == 1:
            file_path = os.path.join(UPLOAD_DIR, files[0]["stored_name"])
            if not os.path.exists(file_path):
                return "ファイルが見つかりません", 404
            return send_file(file_path, as_attachment=True, download_name=files[0]["original_name"])

        # 複数ファイルの場合はZIPで返す
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for f in files:
                file_path = os.path.join(UPLOAD_DIR, f["stored_name"])
                if os.path.exists(file_path):
                    zip_file.write(file_path, arcname=f["original_name"])

        zip_buffer.seek(0)
        download_name = f"{file_info.get('display_id', uid)}_files.zip"
        return send_file(zip_buffer, as_attachment=True, download_name=download_name, mimetype='application/zip')

    # 旧形式（互換性のため）
    else:
        file_path = os.path.join(UPLOAD_DIR, file_info.get("stored_filename", ""))
        if not os.path.exists(file_path):
            return "ファイルが見つかりません", 404
        return send_file(file_path, as_attachment=True, download_name=file_info.get("filename", "download"))


@share_bp.route("/download/<uid>/<int:file_index>", methods=["GET", "POST"])
def download_single_file(uid, file_index):
    """個別ファイルのダウンロード（一括ダウンロードと同じフロー）"""
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです", 404

    if datetime.utcnow() > datetime.fromisoformat(file_info["expires_at"]):
        return "このファイルのダウンロード期限は過ぎています", 410

    # パスワード認証（一括ダウンロードと同じ方式）
    if file_info.get("password_hash"):
        if request.method == "GET":
            # パスワード入力画面を表示
            return render_template("share_password.html", uid=uid, file_index=file_index)
        # POSTの場合、パスワードを検証
        input_pass = request.form.get("password", "")
        input_hash = hashlib.sha256(input_pass.encode()).hexdigest()
        if input_hash != file_info["password_hash"]:
            return render_template("share_password.html", uid=uid, file_index=file_index, error="パスワードが違います")

    if "files" not in file_info or file_index >= len(file_info["files"]):
        return "ファイルが見つかりません", 404

    file_data = file_info["files"][file_index]
    file_path = os.path.join(UPLOAD_DIR, file_data["stored_name"])

    if not os.path.exists(file_path):
        return "ファイルが見つかりません", 404

    return send_file(file_path, as_attachment=True, download_name=file_data["original_name"])


@share_bp.route("/api/list", methods=["GET"])
@login_required
def get_file_list():
    """ファイルリストをJSON形式で返す"""
    meta = load_meta()
    file_list = []

    for uid, info in meta.items():
        # 期限切れチェック
        if datetime.utcnow() > datetime.fromisoformat(info["expires_at"]):
            continue

        # 新形式
        if "files" in info:
            file_count = len(info["files"])
            total_size = sum(f.get("size", 0) for f in info["files"])
            files_detail = info["files"]
        # 旧形式
        else:
            file_count = 1
            file_path = os.path.join(UPLOAD_DIR, info.get("stored_filename", ""))
            total_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            files_detail = [{
                "original_name": info.get("filename", "unknown"),
                "stored_name": info.get("stored_filename", ""),
                "size": total_size
            }]

        file_list.append({
            "uid": uid,
            "display_id": info.get("display_id", uid[:5].upper()),
            "memo": info.get("memo", ""),
            "uploader": info.get("uploader", "unknown"),
            "file_count": file_count,
            "total_size": total_size,
            "files": files_detail,
            "has_password": bool(info.get("password_hash")),
            "expires_at": info["expires_at"],
            "created_at": info.get("created_at", info["expires_at"])
        })

    # 作成日時で降順ソート（新しい順）
    file_list.sort(key=lambda x: x["created_at"], reverse=True)

    return jsonify(file_list)


@share_bp.route("/api/delete/<uid>", methods=["POST"])
@login_required
def delete_file(uid):
    """ファイルを削除（自分がアップロードしたもののみ）"""
    meta = load_meta()
    file_info = meta.get(uid)

    if not file_info:
        return jsonify({"success": False, "error": "ファイルが見つかりません"}), 404

    # アップロードユーザーチェック（セキュリティのため）
    uploader = file_info.get("uploader", "")
    current_username = current_user.username if hasattr(current_user, 'username') else ""

    if uploader != current_username:
        return jsonify({"success": False, "error": "削除権限がありません"}), 403

    # ファイル削除
    if "files" in file_info:
        for f in file_info["files"]:
            file_path = os.path.join(UPLOAD_DIR, f["stored_name"])
            try:
                os.remove(file_path)
            except FileNotFoundError:
                pass
    else:
        # 旧形式
        file_path = os.path.join(UPLOAD_DIR, file_info.get("stored_filename", ""))
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass

    # メタデータから削除
    del meta[uid]
    save_meta(meta)

    return jsonify({"success": True})


@share_bp.route("/api/verify_password/<uid>", methods=["POST"])
def verify_password(uid):
    """パスワード検証API（JavaScript用）"""
    meta = load_meta()
    file_info = meta.get(uid)

    if not file_info:
        return jsonify({"success": False, "error": "無効なUID"}), 404

    if not file_info.get("password_hash"):
        return jsonify({"success": True, "token": _issue_download_token(uid)})

    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")
    input_hash = hashlib.sha256(password.encode()).hexdigest()

    if hmac.compare_digest(input_hash, file_info["password_hash"]):
        return jsonify({"success": True, "token": _issue_download_token(uid)})
    else:
        return jsonify({"success": False, "error": "パスワードが違います"})
