from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for, after_this_request
import os, tempfile, tarfile, zipfile, unicodedata, re
from werkzeug.utils import secure_filename
import pyzipper
import shutil
from flask_login import login_required

compress_bp = Blueprint("compress", __name__, url_prefix="/tools/compress")


def safe_filename(filename):
    """日本語などの非ASCII文字を保持しつつ、危険な文字を除去するファイル名サニタイズ"""
    # パス区切り文字を除去
    filename = filename.replace("/", "").replace("\\", "")
    # NULLバイトを除去
    filename = filename.replace("\x00", "")
    # Unicode正規化
    filename = unicodedata.normalize("NFC", filename)
    # 先頭のドットを除去（隠しファイル防止）
    filename = filename.lstrip(".")
    # 制御文字を除去
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    # 空文字の場合はフォールバック
    if not filename:
        filename = "unnamed_file"
    return filename


@compress_bp.route("/", methods=["GET", "POST"])
@login_required
def compress_tool():
    if request.method == "GET":
        return render_template("compress.html")

    files = request.files.getlist("files")
    fmt = request.form.get("format")
    password = request.form.get("password", "").strip()
    archive_name = request.form.get("archive_name", "").strip()

    # ファイル未選択のバリデーション
    if not files or all(f.filename == "" for f in files):
        flash("ファイルが選択されていません。", "error")
        return redirect(url_for("compress.compress_tool"))

    if not archive_name:
        archive_name = "dstt_compressed"

    temp_dir = tempfile.mkdtemp()
    archive_path = os.path.join(temp_dir, f"{archive_name}.{fmt}")

    upload_dir = os.path.join(temp_dir, "upload_files")
    os.makedirs(upload_dir, exist_ok=True)

    saved_files = []

    for file in files:
        filename = safe_filename(file.filename)
        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)
        saved_files.append((file_path, filename))

    if fmt == "zip":
        if password:
            # パスワード付き ZIP（暗号化あり）
            mode = pyzipper.ZIP_DEFLATED
            with pyzipper.AESZipFile(archive_path, 'w', compression=mode, encryption=pyzipper.WZ_AES) as zipf:
                zipf.setpassword(password.encode())
                zipf.setencryption(pyzipper.WZ_AES, nbits=256)
                for file_path, filename in saved_files:
                    zipf.write(file_path, arcname=filename)
        else:
            # パスワードなし ZIP（通常圧縮）
            with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
                for file_path, filename in saved_files:
                    zipf.write(file_path, arcname=filename)

    elif fmt == "tar":
        with tarfile.open(archive_path, "w") as tarf:
            for file_path, filename in saved_files:
                tarf.add(file_path, arcname=filename)

    elif fmt == "tar.gz":
        with tarfile.open(archive_path, "w:gz") as tarf:
            for file_path, filename in saved_files:
                tarf.add(file_path, arcname=filename)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    return send_file(archive_path, as_attachment=True, download_name=f"{archive_name}.{fmt}")
