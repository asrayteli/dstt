from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for, after_this_request, jsonify
import fnmatch
import os
import re
import shutil
import tarfile
import tempfile
import unicodedata
import zipfile

import pyzipper
from flask_login import login_required

compress_bp = Blueprint("compress", __name__, url_prefix="/tools/compress")

ALLOWED_FORMATS = {"zip", "tar", "tar.gz"}
MAX_FILES = 500


def safe_filename(filename: str) -> str:
    """日本語を保持しつつ危険な文字を除去した安全なファイル名を返す。"""
    filename = (filename or "").replace("\\", "/").split("/")[-1]
    filename = filename.replace("\x00", "")
    filename = unicodedata.normalize("NFC", filename)
    filename = filename.lstrip(".")
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    filename = re.sub(r'[<>:"|?*]', "", filename)
    filename = filename.strip()
    return filename or "unnamed_file"


def sanitize_archive_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "dstt_compressed"
    cleaned = safe_filename(raw)
    cleaned = re.sub(r"\.+$", "", cleaned).strip()
    if not cleaned or cleaned == "unnamed_file":
        return "dstt_compressed"
    return cleaned


def parse_csv_values(raw: str) -> list[str]:
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def should_exclude(filename: str, exclude_exts: list[str], exclude_patterns: list[str]) -> bool:
    lower = filename.lower()
    ext = os.path.splitext(lower)[1]
    if ext and ext in exclude_exts:
        return True
    return any(fnmatch.fnmatch(lower, p.lower()) for p in exclude_patterns)


def make_unique_name(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        used_names.add(filename)
        return filename

    stem, ext = os.path.splitext(filename)
    idx = 1
    while True:
        candidate = f"{stem} ({idx}){ext}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        idx += 1


def build_arcname(filename: str, root_folder: str) -> str:
    if not root_folder:
        return filename
    return f"{root_folder}/{filename}"


def get_compresslevel(level: str) -> int:
    # 要望により「速度優先」は設けず、標準/高圧縮のみ
    if level == "high":
        return 9
    return 6


@compress_bp.route("/", methods=["GET", "POST"])
@login_required
def compress_tool():
    if request.method == "GET":
        return render_template("compress.html")

    files = request.files.getlist("files")
    fmt = request.form.get("format", "zip")
    password = request.form.get("password", "").strip()
    archive_name = sanitize_archive_name(request.form.get("archive_name", "").strip())
    root_folder = safe_filename(request.form.get("root_folder", "").strip()) if request.form.get("root_folder", "").strip() else ""
    compress_level = request.form.get("compress_level", "standard")
    exclude_exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in parse_csv_values(request.form.get("exclude_exts", ""))]
    exclude_patterns = parse_csv_values(request.form.get("exclude_patterns", ""))

    if fmt not in ALLOWED_FORMATS:
        flash("圧縮形式の指定が不正です。", "error")
        return redirect(url_for("compress.compress_tool"))

    valid_files = [f for f in files if f and f.filename]
    if not valid_files:
        flash("ファイルが選択されていません。", "error")
        return redirect(url_for("compress.compress_tool"))

    if len(valid_files) > MAX_FILES:
        flash(f"一度に処理できるファイル数は{MAX_FILES}件までです。", "error")
        return redirect(url_for("compress.compress_tool"))

    temp_dir = tempfile.mkdtemp()
    archive_path = os.path.join(temp_dir, f"{archive_name}.{fmt}")
    upload_dir = os.path.join(temp_dir, "upload_files")
    os.makedirs(upload_dir, exist_ok=True)

    saved_files: list[tuple[str, str]] = []
    used_names: set[str] = set()

    try:
        for file in valid_files:
            filename = safe_filename(file.filename)
            if should_exclude(filename, exclude_exts, exclude_patterns):
                continue

            unique_name = make_unique_name(filename, used_names)
            file_path = os.path.join(upload_dir, unique_name)
            file.save(file_path)
            saved_files.append((file_path, unique_name))

        if not saved_files:
            flash("除外設定により、圧縮対象ファイルが0件になりました。", "error")
            return redirect(url_for("compress.compress_tool"))

        compresslevel = get_compresslevel(compress_level)

        if fmt == "zip":
            if password:
                inner_zip_path = os.path.join(temp_dir, f"{archive_name}_inner.zip")
                with zipfile.ZipFile(inner_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as inner_zipf:
                    for file_path, filename in saved_files:
                        inner_zipf.write(file_path, arcname=build_arcname(filename, root_folder))

                with pyzipper.AESZipFile(archive_path, "w", compression=pyzipper.ZIP_DEFLATED, compresslevel=compresslevel) as zipf:
                    zipf.setpassword(password.encode())
                    zipf.setencryption(pyzipper.WZ_AES, nbits=256)
                    zipf.write(inner_zip_path, arcname=f"{archive_name}.zip")
            else:
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as zipf:
                    for file_path, filename in saved_files:
                        zipf.write(file_path, arcname=build_arcname(filename, root_folder))

        elif fmt == "tar":
            with tarfile.open(archive_path, "w") as tarf:
                for file_path, filename in saved_files:
                    tarf.add(file_path, arcname=build_arcname(filename, root_folder))

        elif fmt == "tar.gz":
            with tarfile.open(archive_path, "w:gz", compresslevel=compresslevel) as tarf:
                for file_path, filename in saved_files:
                    tarf.add(file_path, arcname=build_arcname(filename, root_folder))

    except Exception:
        flash("圧縮処理でエラーが発生しました。設定と入力ファイルを確認してください。", "error")
        return redirect(url_for("compress.compress_tool"))

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    return send_file(archive_path, as_attachment=True, download_name=f"{archive_name}.{fmt}")


@compress_bp.route("/preview", methods=["POST"])
@login_required
def preview_compress():
    data = request.get_json(silent=True) or {}

    fmt = data.get("format", "zip")
    archive_name = sanitize_archive_name(str(data.get("archive_name", "")).strip())
    root_folder = safe_filename(str(data.get("root_folder", "")).strip()) if str(data.get("root_folder", "")).strip() else ""
    exclude_exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in parse_csv_values(str(data.get("exclude_exts", "")))]
    exclude_patterns = parse_csv_values(str(data.get("exclude_patterns", "")))

    if fmt not in ALLOWED_FORMATS:
        return jsonify({"error": "圧縮形式の指定が不正です。"}), 400

    raw_filenames = data.get("filenames", [])
    filenames = [safe_filename(str(n)) for n in raw_filenames if str(n).strip()]

    included = []
    excluded = []
    used_names: set[str] = set()

    for name in filenames:
        if should_exclude(name, exclude_exts, exclude_patterns):
            excluded.append(name)
            continue
        included.append(make_unique_name(name, used_names))

    return jsonify(
        {
            "archive_name": f"{archive_name}.{fmt}",
            "root_folder": root_folder,
            "total": len(filenames),
            "included_count": len(included),
            "excluded_count": len(excluded),
            "included_preview": included[:8],
            "excluded_preview": excluded[:8],
            "warnings": ["対象ファイルがありません。"] if not included else [],
        }
    )
