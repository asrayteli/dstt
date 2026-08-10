import os
import shutil
import tempfile

from flask import (
    Blueprint,
    after_this_request,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required

from app.services.compress_service import (
    ALLOWED_FORMATS,
    MAX_FILES,
    MAX_TOTAL_SIZE,
    CompressValidationError,
    build_preview,
    create_archive,
    make_unique_name,
    safe_filename,
    sanitize_archive_name,
    sanitize_root_folder,
    save_included_uploads,
    should_exclude,
    build_arcname,
    plan_files,
)

compress_bp = Blueprint("compress", __name__, url_prefix="/tools/compress")

# 通常のリクエスト上限は小さく保ちつつ、このツールが従来から正式に対応している
# 合計1GiBの圧縮だけは維持する。multipart 境界やファイル名などの余裕を64MiB取る。
COMPRESS_REQUEST_LIMIT = MAX_TOTAL_SIZE + 64 * 1024 * 1024


@compress_bp.before_request
def _allow_documented_compress_upload_size():
    if request.method == "POST" and request.endpoint == "compress.compress_tool":
        request.max_content_length = COMPRESS_REQUEST_LIMIT


@compress_bp.route("/", methods=["GET", "POST"])
@login_required
def compress_tool():
    if request.method == "GET":
        return render_template(
            "compress.html",
            allowed_formats=sorted(ALLOWED_FORMATS),
            max_files=MAX_FILES,
            max_total_size=MAX_TOTAL_SIZE,
        )

    files = [file for file in request.files.getlist("files") if file and file.filename]
    fmt = request.form.get("format", "zip")
    password = request.form.get("password", "").strip() if fmt == "zip" else ""
    archive_name = sanitize_archive_name(request.form.get("archive_name", ""))
    root_folder = sanitize_root_folder(request.form.get("root_folder", ""))
    compress_level = request.form.get("compress_level", "standard")
    exclude_exts = request.form.get("exclude_exts", "")
    exclude_patterns = request.form.get("exclude_patterns", "")

    temp_dir = tempfile.mkdtemp()
    archive_path = os.path.join(temp_dir, f"{archive_name}.{fmt}")
    upload_dir = os.path.join(temp_dir, "upload_files")

    try:
        if fmt not in ALLOWED_FORMATS:
            raise CompressValidationError("圧縮形式の指定が不正です。")

        file_paths = request.form.getlist("file_paths")
        planned = plan_files(
            [file.filename for file in files],
            relative_paths=file_paths,
            exclude_exts=exclude_exts,
            exclude_patterns=exclude_patterns,
        )
        entries, _total_size = save_included_uploads(files, upload_dir, planned)
        create_archive(
            archive_path=archive_path,
            fmt=fmt,
            entries=entries,
            root_folder=root_folder,
            password=password,
            compress_level=compress_level,
        )
    except CompressValidationError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        flash(str(exc), "error")
        return redirect(url_for("compress.compress_tool"))
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
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

    try:
        payload = build_preview(
            filenames=data.get("filenames", []),
            relative_paths=data.get("file_paths", []),
            sizes=data.get("sizes", []),
            fmt=data.get("format", "zip"),
            archive_name=str(data.get("archive_name", "")),
            root_folder=str(data.get("root_folder", "")),
            exclude_exts=str(data.get("exclude_exts", "")),
            exclude_patterns=str(data.get("exclude_patterns", "")),
        )
    except CompressValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(payload)
