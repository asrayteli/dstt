from flask import Blueprint, render_template, request, send_file, redirect, url_for, abort, jsonify, session
import os, uuid, hashlib, json, io, base64, zipfile, string, random
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import qrcode
from flask_login import login_required, current_user
from apscheduler.schedulers.background import BackgroundScheduler

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
    meta = load_meta()
    updated_meta = {}
    now = datetime.utcnow()

    for uid, info in meta.items():
        if datetime.fromisoformat(info["expires_at"]) < now:
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

    save_meta(updated_meta)

# 自動クリーンアップのスケジューラー設定
scheduler = BackgroundScheduler()
scheduler.add_job(func=cleanup_expired_files, trigger="interval", hours=1)
scheduler.start()

# 起動時にも一度実行
cleanup_expired_files()


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

            filename = secure_filename(file.filename)
            stored_filename = f"{uid}_{idx}_{filename}"
            save_path = os.path.join(UPLOAD_DIR, stored_filename)
            file.save(save_path)

            file_size = os.path.getsize(save_path)
            file_list.append({
                "original_name": filename,
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
        qr = qrcode.make(download_url)
        qr_buffer = io.BytesIO()
        qr.save(qr_buffer, format="PNG")
        qr_data = base64.b64encode(qr_buffer.getvalue()).decode("utf-8")

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
        # セッションで認証済みかチェック
        verified_uids = session.get('verified_uids', [])
        if uid not in verified_uids:
            # 未認証の場合
            if request.method == "GET":
                return render_template("share_password.html", uid=uid)
            input_pass = request.form.get("password", "")
            input_hash = hashlib.sha256(input_pass.encode()).hexdigest()
            if input_hash != file_info["password_hash"]:
                return render_template("share_password.html", uid=uid, error="パスワードが違います")
            # 認証成功 - セッションに保存
            if 'verified_uids' not in session:
                session['verified_uids'] = []
            session['verified_uids'].append(uid)
            session.modified = True

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
    """個別ファイルのダウンロード"""
    meta = load_meta()
    file_info = meta.get(uid)
    if not file_info:
        return "無効なURLです", 404

    if datetime.utcnow() > datetime.fromisoformat(file_info["expires_at"]):
        return "このファイルのダウンロード期限は過ぎています", 410

    # パスワード認証チェック
    if file_info.get("password_hash"):
        # POSTリクエストの場合、パスワードを検証
        if request.method == "POST":
            password = request.form.get("password", "")
            input_hash = hashlib.sha256(password.encode()).hexdigest()
            if input_hash != file_info["password_hash"]:
                return "パスワードが違います", 403
        else:
            # GETリクエストの場合、セッションで認証済みかチェック
            verified_uids = session.get('verified_uids', [])
            if uid not in verified_uids:
                return "認証が必要です", 403

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
        return jsonify({"success": True})  # パスワード不要

    password = request.json.get("password", "")
    input_hash = hashlib.sha256(password.encode()).hexdigest()

    if input_hash == file_info["password_hash"]:
        # セッションに認証済みUIDを保存
        if 'verified_uids' not in session:
            session['verified_uids'] = []
        if uid not in session['verified_uids']:
            session['verified_uids'].append(uid)
        session.modified = True
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "パスワードが違います"})
