from flask import Blueprint, render_template, request, send_file, redirect, url_for, abort
import os, uuid, hashlib, json, io, base64
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import qrcode
from flask_login import login_required

share_bp = Blueprint("share", __name__, url_prefix="/tools/share")

# 絶対パスで保存場所を指定
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "shared_files")
META_PATH = os.path.join(UPLOAD_DIR, "meta.json")
MAX_FILE_SIZE = 10 * 1024**3  # 10GB

os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- メタ情報の読み書き ---
def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_meta(meta):
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

# --- 期限切れファイルを削除 ---
def cleanup_expired_files():
    meta = load_meta()
    updated_meta = {}
    now = datetime.utcnow()

    for uid, info in meta.items():
        file_path = os.path.join(UPLOAD_DIR, info["stored_filename"])
        if datetime.fromisoformat(info["expires_at"]) < now:
            try:
                os.remove(file_path)
            except FileNotFoundError:
                pass
        else:
            updated_meta[uid] = info

    save_meta(updated_meta)

cleanup_expired_files()


@share_bp.route("/", methods=["GET", "POST"])
@login_required
def upload_share():
    download_url = None
    qr_data = None

    if request.method == "POST":
        # ファイルサイズ確認
        content_length = request.content_length
        if content_length and content_length > MAX_FILE_SIZE:
            return "アップロードできるファイルは10GBまでです。", 413

        file = request.files.get("file")
        password = request.form.get("password", "").strip()
        if not file:
            return "ファイルを選択してください", 400

        uid = uuid.uuid4().hex[:8]
        filename = secure_filename(file.filename)
        stored_filename = f"{uid}_{filename}"
        save_path = os.path.join(UPLOAD_DIR, stored_filename)
        file.save(save_path)

        password_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()

        meta = load_meta()
        meta[uid] = {
            "filename": filename,
            "stored_filename": stored_filename,
            "password_hash": password_hash,
            "expires_at": expires_at
        }
        save_meta(meta)

        # ダウンロードURLを生成
        download_url = url_for("share.download_file", uid=uid, _external=True)

        # QRコード生成（保存せずにBase64エンコード）
        qr = qrcode.make(download_url)
        qr_buffer = io.BytesIO()
        qr.save(qr_buffer, format="PNG")
        qr_data = base64.b64encode(qr_buffer.getvalue()).decode("utf-8")

    return render_template("share.html", download_url=download_url, qr_data=qr_data)


@share_bp.route("/download/<uid>", methods=["GET", "POST"])
def download_file(uid):
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです", 404

    if datetime.utcnow() > datetime.fromisoformat(file_info["expires_at"]):
        return "このファイルのダウンロード期限は過ぎています", 410

    if file_info["password_hash"]:
        if request.method == "GET":
            return render_template("share_password.html", uid=uid)
        input_pass = request.form.get("password", "")
        input_hash = hashlib.sha256(input_pass.encode()).hexdigest()
        if input_hash != file_info["password_hash"]:
            return render_template("share_password.html", uid=uid, error="パスワードが違います")

    file_path = os.path.join(UPLOAD_DIR, file_info["stored_filename"])
    if not os.path.exists(file_path):
        return "ファイルが見つかりません", 404

    return send_file(file_path, as_attachment=True, download_name=file_info["filename"])
