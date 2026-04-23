from flask import Blueprint, render_template, request, jsonify, current_app, send_file
from flask_login import login_required
import base64
import binascii
import io
import os
import json

import fitz

powerstamp_bp = Blueprint("powerstamp", __name__, url_prefix="/tools/powerstamp")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SEED_STORE_PATH = os.path.join(BASE_DIR, "..", "data", "powerstamp_templates.seed.json")
MAX_EXACT_PDF_IMAGE_BYTES = 25 * 1024 * 1024
MIN_PAGE_MM = 10
MAX_PAGE_MM = 600


def _load_templates():
    if not os.path.exists(SEED_STORE_PATH):
        return []
    try:
        with open(SEED_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _mm_to_pt(value_mm: float) -> float:
    return value_mm * 72 / 25.4


def _positive_float(value, default=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _decode_png_data_url(data_url: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("PNG data URLを指定してください")

    encoded = data_url[len(prefix):]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PNGデータを読み取れません") from exc

    if len(data) > MAX_EXACT_PDF_IMAGE_BYTES:
        raise ValueError("PNGデータが大きすぎます")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG形式ではありません")
    return data


def _build_exact_pdf_from_png(png_bytes: bytes, page_w_mm: float, page_h_mm: float) -> bytes:
    page_w_pt = _mm_to_pt(page_w_mm)
    page_h_pt = _mm_to_pt(page_h_mm)
    doc = fitz.open()
    try:
        page = doc.new_page(width=page_w_pt, height=page_h_pt)
        page.insert_image(
            fitz.Rect(0, 0, page_w_pt, page_h_pt),
            stream=png_bytes,
            keep_proportion=False,
            overlay=True,
        )
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()


def _safe_pdf_filename(name: str | None) -> str:
    raw = os.path.basename(str(name or "powerstamp-exact-print.pdf")).strip()
    if not raw:
        raw = "powerstamp-exact-print.pdf"
    raw = raw.replace("\r", "").replace("\n", "")
    if not raw.lower().endswith(".pdf"):
        raw += ".pdf"
    return raw


@powerstamp_bp.route("/", methods=["GET"])
@login_required
def powerstamp():
    return render_template("powerstamp.html")


@powerstamp_bp.route("/api/templates", methods=["GET"])
@login_required
def list_templates():
    templates = _load_templates()
    summary = [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "owner": t.get("owner"),
        }
        for t in templates
    ]
    summary.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return jsonify({"templates": summary, "count": len(summary)})


@powerstamp_bp.route("/api/templates/<template_id>", methods=["GET"])
@login_required
def get_template(template_id):
    templates = _load_templates()
    for t in templates:
        if t.get("id") == template_id:
            return jsonify({"template": t})
    return jsonify({"error": "テンプレートが見つかりません"}), 404


@powerstamp_bp.route("/api/exact-pdf", methods=["POST"])
@login_required
def exact_pdf():
    payload = request.get_json(silent=True) or {}
    page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
    page_w_mm = _positive_float(page.get("w_mm"))
    page_h_mm = _positive_float(page.get("h_mm"))

    if page_w_mm is None or page_h_mm is None:
        return jsonify({"error": "PDFページサイズ(mm)を指定してください"}), 400
    if not (MIN_PAGE_MM <= page_w_mm <= MAX_PAGE_MM and MIN_PAGE_MM <= page_h_mm <= MAX_PAGE_MM):
        return jsonify({"error": f"PDFページサイズは{MIN_PAGE_MM}mm以上{MAX_PAGE_MM}mm以下にしてください"}), 400

    try:
        png_bytes = _decode_png_data_url(payload.get("image"))
        pdf_bytes = _build_exact_pdf_from_png(png_bytes, page_w_mm, page_h_mm)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        current_app.logger.exception("PowerSTAMP exact PDF generation failed")
        return jsonify({"error": "実寸PDFの作成に失敗しました"}), 500

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=_safe_pdf_filename(payload.get("filename")),
    )
