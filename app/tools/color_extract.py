import io
import json
import os

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import login_required
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from app.services.color_extract_service import (
    apply_color_operation,
    parse_hex_color,
    process_pdf_bytes,
    render_pdf_first_page_preview,
)


color_extract_bp = Blueprint("color_extract", __name__, url_prefix="/tools/color_extract")

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


def _is_allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _parse_settings(form) -> tuple[str, list[tuple[int, int, int]], int, bool, tuple[int, int, int] | None]:
    mode = (form.get("mode") or "exclude").strip().lower()
    if mode not in {"extract", "exclude", "replace"}:
        raise ValueError("mode は extract / exclude / replace のいずれかを指定してください。")

    raw_colors = form.get("target_colors", "[]")
    try:
        color_list = json.loads(raw_colors)
    except json.JSONDecodeError as exc:
        raise ValueError("target_colors の形式が不正です。") from exc

    if not isinstance(color_list, list) or not color_list:
        raise ValueError("対象色を1つ以上指定してください。")

    target_colors = [parse_hex_color(c) for c in color_list]

    tolerance = int(form.get("tolerance", 20))
    tolerance = max(0, min(120, tolerance))

    transparent_output = _parse_bool(form.get("transparent_output"), default=True)

    replacement_color = None
    if mode == "replace":
        replacement_color = parse_hex_color(form.get("replacement_color", "#000000"))

    return mode, target_colors, tolerance, transparent_output, replacement_color


@color_extract_bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template("color_extract.html")


@color_extract_bp.route("/preview", methods=["POST"])
@login_required
def preview():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "ファイルを選択してください。"}), 400

    filename = secure_filename(file.filename)
    if not _is_allowed(filename):
        return jsonify({"error": "対応していないファイル形式です。"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "ファイルサイズ上限(100MB)を超えています。"}), 413

    try:
        mode, target_colors, tolerance, transparent_output, replacement_color = _parse_settings(request.form)
        ext = filename.rsplit(".", 1)[1].lower()

        if ext == "pdf":
            png_bytes = render_pdf_first_page_preview(
                pdf_bytes=file_bytes,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
            )
        else:
            image = Image.open(io.BytesIO(file_bytes))
            processed = apply_color_operation(
                image=image,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
            )
            buf = io.BytesIO()
            processed.save(buf, format="PNG")
            png_bytes = buf.getvalue()

        return send_file(
            io.BytesIO(png_bytes),
            mimetype="image/png",
            as_attachment=False,
            download_name="preview.png",
        )
    except (ValueError, UnidentifiedImageError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "プレビュー生成に失敗しました。"}), 500


@color_extract_bp.route("/process", methods=["POST"])
@login_required
def process():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "ファイルを選択してください。"}), 400

    filename = secure_filename(file.filename)
    if not _is_allowed(filename):
        return jsonify({"error": "対応していないファイル形式です。"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "ファイルサイズ上限(100MB)を超えています。"}), 413

    try:
        mode, target_colors, tolerance, transparent_output, replacement_color = _parse_settings(request.form)
        ext = filename.rsplit(".", 1)[1].lower()

        base_name = os.path.splitext(filename)[0]
        if ext == "pdf":
            out_bytes = process_pdf_bytes(
                pdf_bytes=file_bytes,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
            )
            return send_file(
                io.BytesIO(out_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"{base_name}_processed.pdf",
            )

        image = Image.open(io.BytesIO(file_bytes))
        processed = apply_color_operation(
            image=image,
            mode=mode,
            target_colors=target_colors,
            tolerance=tolerance,
            transparent_output=transparent_output,
            replacement_color=replacement_color,
        )

        output_format = (request.form.get("output_format") or "png").lower()
        if output_format == "jpg":
            buf = io.BytesIO()
            processed.convert("RGB").save(buf, format="JPEG", quality=95)
            buf.seek(0)
            return send_file(
                buf,
                mimetype="image/jpeg",
                as_attachment=True,
                download_name=f"{base_name}_processed.jpg",
            )

        buf = io.BytesIO()
        processed.save(buf, format="PNG")
        buf.seek(0)
        return send_file(
            buf,
            mimetype="image/png",
            as_attachment=True,
            download_name=f"{base_name}_processed.png",
        )

    except (ValueError, UnidentifiedImageError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "ファイル処理に失敗しました。"}), 500
