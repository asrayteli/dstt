import io
import json
import os

from flask import Blueprint, jsonify, make_response, render_template, request, send_file
from flask_login import login_required
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from app.services.color_extract_service import (
    apply_paint_actions,
    apply_color_operation,
    crop_image_to_region,
    detect_pdf_bytes,
    normalize_pdf_bytes,
    parse_hex_color,
    parse_hex_color_rgba,
    process_pdf_bytes,
    render_pdf_page_preview,
    suggest_tolerance,
)


color_extract_bp = Blueprint("color_extract", __name__, url_prefix="/tools/color_extract")
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _detect_input_kind(filename: str, file_bytes: bytes) -> str:
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    looks_pdf = detect_pdf_bytes(file_bytes)
    if ext == "pdf" or looks_pdf:
        return "pdf"
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return "image"
    raise ValueError("対応していないファイル形式です。")


def _parse_region(form) -> tuple[float, float, float, float] | None:
    raw_region = (form.get("process_region") or "").strip()
    if not raw_region:
        return None

    try:
        region = json.loads(raw_region)
    except json.JSONDecodeError as exc:
        raise ValueError("処理範囲の形式が不正です。") from exc

    if not isinstance(region, dict):
        raise ValueError("処理範囲の形式が不正です。")

    try:
        x = float(region.get("x"))
        y = float(region.get("y"))
        w = float(region.get("w"))
        h = float(region.get("h"))
    except (TypeError, ValueError) as exc:
        raise ValueError("処理範囲の値が不正です。") from exc

    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("処理範囲は画像内で指定してください。")
    if x + w > 1.0001 or y + h > 1.0001:
        raise ValueError("処理範囲は画像内で指定してください。")

    return (x, y, w, h)


def _parse_settings(
    form,
) -> tuple[
    str,
    list[tuple[int, int, int]],
    int,
    bool,
    tuple[int, int, int] | None,
    tuple[float, float, float, float] | None,
    bool,
    int,
    list[dict],
    dict,
]:
    mode = (form.get("mode") or "exclude").strip().lower()
    if mode not in {"extract", "exclude", "replace"}:
        raise ValueError("mode は extract / exclude / replace を指定してください。")

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

    region = _parse_region(form)
    output_region_png = _parse_bool(form.get("output_region_png"), default=False)
    preview_page = int(form.get("preview_page", 1))
    preview_page = max(1, min(9999, preview_page))
    raw_paint_actions = form.get("paint_actions", "[]")
    try:
        paint_actions = json.loads(raw_paint_actions)
    except json.JSONDecodeError as exc:
        raise ValueError("paint_actions の形式が不正です。") from exc
    if not isinstance(paint_actions, list):
        raise ValueError("paint_actions の形式が不正です。")
    normalized_actions: list[dict] = []
    for action in paint_actions[:500]:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type", "")).strip().lower()
        if action_type not in {"rect_fill", "free_draw"}:
            continue
        color = str(action.get("color", "#000000ff"))
        parse_hex_color_rgba(color)
        page = action.get("page")
        if page is not None:
            try:
                page = max(1, int(page))
            except Exception:
                page = 1
        if action_type == "rect_fill":
            normalized_actions.append(
                {
                    "type": "rect_fill",
                    "x": float(action.get("x", 0.0)),
                    "y": float(action.get("y", 0.0)),
                    "w": float(action.get("w", 0.0)),
                    "h": float(action.get("h", 0.0)),
                    "color": color,
                    "page": page,
                    "size": int(action.get("size", 8)),
                }
            )
            continue
        points = action.get("points") if isinstance(action.get("points"), list) else []
        norm_points = []
        for p in points[:2000]:
            if not isinstance(p, dict):
                continue
            norm_points.append({"x": float(p.get("x", 0.0)), "y": float(p.get("y", 0.0))})
        normalized_actions.append(
            {
                "type": "free_draw",
                "points": norm_points,
                "color": color,
                "page": page,
                "size": int(action.get("size", 8)),
            }
        )

    preset = (form.get("preset") or "none").strip().lower()
    supported_presets = {
        "none",
        "stamp_red",
        "blue_pen",
        "highlighter",
        "keep_black_text",
        "whiten_background",
    }
    if preset not in supported_presets:
        raise ValueError("preset の指定が不正です。")

    color_metric = (form.get("color_metric") or "deltae").strip().lower()
    if color_metric not in {"deltae", "rgb"}:
        raise ValueError("color_metric は deltae / rgb を指定してください。")

    saturation_min = int(form.get("saturation_min", 0))
    saturation_min = max(0, min(255, saturation_min))

    lightness_min = int(form.get("lightness_min", 0))
    lightness_max = int(form.get("lightness_max", 255))
    lightness_min = max(0, min(255, lightness_min))
    lightness_max = max(0, min(255, lightness_max))
    if lightness_min > lightness_max:
        lightness_min, lightness_max = lightness_max, lightness_min

    min_component_area = int(form.get("min_component_area", 0))
    min_component_area = max(0, min(100000, min_component_area))

    operation_options = {
        "preset": preset,
        "color_metric": color_metric,
        "enable_preprocess": _parse_bool(form.get("enable_preprocess"), default=True),
        "enable_postprocess": _parse_bool(form.get("enable_postprocess"), default=True),
        "saturation_min": saturation_min,
        "lightness_min": lightness_min,
        "lightness_max": lightness_max,
        "min_component_area": min_component_area,
    }

    return mode, target_colors, tolerance, transparent_output, replacement_color, region, output_region_png, preview_page, normalized_actions, operation_options


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
    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "ファイルサイズ上限(100MB)を超えています。"}), 413

    try:
        mode, target_colors, tolerance, transparent_output, replacement_color, region, _, preview_page, paint_actions, operation_options = _parse_settings(request.form)
        input_kind = _detect_input_kind(filename, file_bytes)

        if input_kind == "pdf":
            normalized_pdf = normalize_pdf_bytes(file_bytes)
            png_bytes, total_pages, current_page = render_pdf_page_preview(
                pdf_bytes=normalized_pdf,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
                region=region,
                page_number=preview_page,
                paint_actions=paint_actions,
                **operation_options,
            )
            resp = make_response(
                send_file(
                    io.BytesIO(png_bytes),
                    mimetype="image/png",
                    as_attachment=False,
                    download_name="preview.png",
                )
            )
            resp.headers["X-PDF-Total-Pages"] = str(total_pages)
            resp.headers["X-PDF-Page"] = str(current_page)
            return resp
        else:
            image = Image.open(io.BytesIO(file_bytes))
            processed = apply_color_operation(
                image=image,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
                region=region,
                **operation_options,
            )
            processed = apply_paint_actions(processed, paint_actions)
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
    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "ファイルサイズ上限(100MB)を超えています。"}), 413

    try:
        mode, target_colors, tolerance, transparent_output, replacement_color, region, output_region_png, preview_page, paint_actions, operation_options = _parse_settings(request.form)
        input_kind = _detect_input_kind(filename, file_bytes)

        base_name = os.path.splitext(filename)[0] or "processed"
        if input_kind == "pdf":
            normalized_pdf = normalize_pdf_bytes(file_bytes)
            if output_region_png:
                if region is None:
                    raise ValueError("範囲のみ透過PNG保存では、処理範囲の指定が必須です。")
                png_bytes, _, _ = render_pdf_page_preview(
                    pdf_bytes=normalized_pdf,
                    mode=mode,
                    target_colors=target_colors,
                    tolerance=tolerance,
                    transparent_output=transparent_output,
                    replacement_color=replacement_color,
                    zoom=2.0,
                    region=region,
                    page_number=preview_page,
                    paint_actions=paint_actions,
                    **operation_options,
                )
                image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                cropped = crop_image_to_region(image, region)
                buf = io.BytesIO()
                cropped.save(buf, format="PNG")
                buf.seek(0)
                return send_file(
                    buf,
                    mimetype="image/png",
                    as_attachment=True,
                    download_name=f"{base_name}_region.png",
                )
            out_bytes = process_pdf_bytes(
                pdf_bytes=normalized_pdf,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
                region=region,
                paint_actions=paint_actions,
                **operation_options,
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
            region=region,
            **operation_options,
        )
        processed = apply_paint_actions(processed, paint_actions)
        if output_region_png:
            if region is None:
                raise ValueError("範囲のみ透過PNG保存では、処理範囲の指定が必須です。")
            cropped = crop_image_to_region(processed.convert("RGBA"), region)
            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            buf.seek(0)
            return send_file(
                buf,
                mimetype="image/png",
                as_attachment=True,
                download_name=f"{base_name}_region.png",
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


@color_extract_bp.route("/auto_tune", methods=["POST"])
@login_required
def auto_tune():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "ファイルを選択してください。"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "ファイルサイズ上限(100MB)を超えています。"}), 413

    try:
        _, target_colors, _, _, _, region, _, preview_page, _, operation_options = _parse_settings(request.form)
        input_kind = _detect_input_kind(secure_filename(file.filename), file_bytes)

        if input_kind == "pdf":
            normalized_pdf = normalize_pdf_bytes(file_bytes)
            preview_png, _, _ = render_pdf_page_preview(
                pdf_bytes=normalized_pdf,
                mode="exclude",
                target_colors=target_colors,
                tolerance=0,
                transparent_output=False,
                replacement_color=None,
                region=region,
                page_number=preview_page,
                **operation_options,
            )
            image = Image.open(io.BytesIO(preview_png))
        else:
            image = Image.open(io.BytesIO(file_bytes))

        result = suggest_tolerance(
            image=image,
            target_colors=target_colors,
            region=region,
            color_metric=operation_options["color_metric"],
            preset=operation_options["preset"],
            enable_preprocess=operation_options["enable_preprocess"],
            saturation_min=operation_options["saturation_min"],
            lightness_min=operation_options["lightness_min"],
            lightness_max=operation_options["lightness_max"],
        )
        return jsonify(result)
    except (ValueError, UnidentifiedImageError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "自動推定に失敗しました。"}), 500
