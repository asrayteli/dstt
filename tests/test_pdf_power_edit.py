import importlib.util
import io
import json
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

fitz = pytest.importorskip("fitz")


def _load_pdf_power_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "tools" / "pdf_power.py"
    spec = importlib.util.spec_from_file_location("pdf_power_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_pdf(rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=420, height=320)
    page.insert_text((60, 90), "SOURCE_REGION")
    if rotation:
        page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data


def _png_bytes(color=(240, 40, 40)):
    image = Image.new("RGB", (30, 30), color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def pdf_power_module():
    return _load_pdf_power_module()


@pytest.fixture
def client(pdf_power_module):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(pdf_power_module.pdf_power_bp)
    return app.test_client()


def _post_edit(client, pdf_bytes, operations, uploads=None):
    data = {
        "pdf": (io.BytesIO(pdf_bytes), "input.pdf"),
        "operations": json.dumps(operations),
    }
    if uploads:
        data.update(uploads)
    return client.post("/tools/pdf_power/edit", data=data, content_type="multipart/form-data")






def _post_compress(client, pdf_bytes, filename="input.pdf", compression_level="medium"):
    data = {
        "pdf": (io.BytesIO(pdf_bytes), filename),
        "compression_level": compression_level,
    }
    return client.post("/tools/pdf_power/compress", data=data, content_type="multipart/form-data")

def test_round_to_tenth_helper(pdf_power_module):
    assert pdf_power_module._round_to_tenth(12.345) == 12.3
    assert pdf_power_module._round_to_tenth(12.36) == 12.4

def test_collect_affected_pages_for_edit(pdf_power_module):
    operations = [
        {"type": "add_text", "page": 1},
        {"type": "copy_region_paste", "source_page": 2, "target_page": 3},
        {"type": "add_image", "page": 3},
        {"type": "add_freehand", "page": 2, "points": [{"x": 10, "y": 10}, {"x": 20, "y": 20}]},
    ]
    assert pdf_power_module._collect_affected_pages_for_edit(operations, 5) == {0, 1, 2}


def test_edit_overlay_non_rotated_regression(client):
    operations = [
        {
            "type": "add_text",
            "page": 1,
            "x": 40,
            "y": 40,
            "width": 180,
            "height": 50,
            "text": "CASE_A_TEXT",
            "font_size": 14,
        },
        {
            "type": "add_shape",
            "page": 1,
            "shape": "rect",
            "x": 30,
            "y": 120,
            "width": 100,
            "height": 60,
            "stroke_color": "#1d4ed8",
            "fill_color": "#93c5fd",
        },
        {
            "type": "add_image",
            "page": 1,
            "x": 220,
            "y": 50,
            "width": 80,
            "height": 80,
            "image_ref": "imgA",
        },
    ]
    response = _post_edit(
        client,
        _make_pdf(rotation=0),
        operations,
        uploads={"imgA": (io.BytesIO(_png_bytes()), "imgA.png")},
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    assert page.rotation == 0
    assert page.search_for("CASE_A_TEXT")
    assert len(page.get_drawings()) > 0
    image_blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    assert len(image_blocks) >= 1
    doc.close()


def test_rotated_page_is_normalized_and_text_preserved(client):
    operations = [
        {
            "type": "add_text",
            "page": 1,
            "x": 45,
            "y": 45,
            "width": 180,
            "height": 60,
            "text": "ROT90_TEXT",
            "font_size": 14,
        }
    ]
    response = _post_edit(client, _make_pdf(rotation=90), operations)
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    assert page.rotation == 0
    hits = page.search_for("ROT90_TEXT")
    assert hits
    expected = fitz.Rect(45, 45, 225, 105)
    assert any(hit.intersects(expected) for hit in hits)
    doc.close()


def test_copy_region_paste_on_rotated_page(client):
    operations = [
        {
            "type": "copy_region_paste",
            "source_page": 1,
            "source_x": 45,
            "source_y": 60,
            "source_width": 180,
            "source_height": 80,
            "target_page": 1,
            "target_x": 220,
            "target_y": 180,
            "target_width": 160,
            "target_height": 70,
        }
    ]
    response = _post_edit(client, _make_pdf(rotation=270), operations)
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    assert page.rotation == 0
    assert len(page.get_images(full=True)) >= 1
    doc.close()


def test_same_image_ref_can_be_reused_multiple_times(client):
    operations = [
        {"type": "add_image", "page": 1, "x": 30, "y": 40, "width": 80, "height": 80, "image_ref": "imgShared"},
        {"type": "add_image", "page": 1, "x": 140, "y": 40, "width": 80, "height": 80, "image_ref": "imgShared"},
    ]
    response = _post_edit(
        client,
        _make_pdf(rotation=0),
        operations,
        uploads={"imgShared": (io.BytesIO(_png_bytes((40, 90, 220))), "shared.png")},
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    image_blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    assert len(image_blocks) >= 2
    doc.close()


def test_add_image_respects_requested_width_and_height(client):
    operations = [
        {
            "type": "add_image",
            "page": 1,
            "x": 40,
            "y": 50,
            "width": 160,
            "height": 60,
            "image_ref": "imgRect",
        }
    ]
    response = _post_edit(
        client,
        _make_pdf(rotation=0),
        operations,
        uploads={"imgRect": (io.BytesIO(_png_bytes((40, 90, 220))), "rect.png")},
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    image_blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    assert image_blocks
    inserted = fitz.Rect(image_blocks[0]["bbox"])
    expected = fitz.Rect(40, 50, 200, 110)
    assert abs(inserted.x0 - expected.x0) <= 1
    assert abs(inserted.y0 - expected.y0) <= 1
    assert abs(inserted.width - expected.width) <= 1
    assert abs(inserted.height - expected.height) <= 1
    doc.close()


def test_add_image_respects_requested_size_on_rotated_page(client):
    operations = [
        {
            "type": "add_image",
            "page": 1,
            "x": 55,
            "y": 65,
            "width": 150,
            "height": 70,
            "image_ref": "imgRot",
        }
    ]
    response = _post_edit(
        client,
        _make_pdf(rotation=90),
        operations,
        uploads={"imgRot": (io.BytesIO(_png_bytes((180, 60, 30))), "rot.png")},
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    assert page.rotation == 0
    image_blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    assert image_blocks
    inserted = fitz.Rect(image_blocks[0]["bbox"])
    expected = fitz.Rect(55, 65, 205, 135)
    assert abs(inserted.x0 - expected.x0) <= 1
    assert abs(inserted.y0 - expected.y0) <= 1
    assert abs(inserted.width - expected.width) <= 1
    assert abs(inserted.height - expected.height) <= 1
    doc.close()


def test_empty_image_data_returns_400_with_operation_index(client):
    operations = [
        {"type": "add_image", "page": 1, "x": 30, "y": 40, "width": 80, "height": 80, "image_ref": "imgEmpty"}
    ]
    response = _post_edit(
        client,
        _make_pdf(rotation=0),
        operations,
        uploads={"imgEmpty": (io.BytesIO(b""), "empty.png")},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert "Operation 1" in payload["error"]
    assert "empty" in payload["error"].lower()


def test_invalid_operations_json_returns_400(client):
    response = client.post(
        "/tools/pdf_power/edit",
        data={"pdf": (io.BytesIO(_make_pdf(rotation=0)), "input.pdf"), "operations": "{invalid"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_add_freehand_stroke(client):
    operations = [
        {
            "type": "add_freehand",
            "page": 1,
            "points": [
                {"x": 30, "y": 40},
                {"x": 90, "y": 60},
                {"x": 140, "y": 130},
            ],
            "stroke_color": "#ff0000",
            "stroke_width": 3,
            "stroke_style": "solid",
        }
    ]
    response = _post_edit(client, _make_pdf(rotation=0), operations)
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    page = doc[0]
    assert len(page.get_drawings()) > 0
    doc.close()


def test_compress_accepts_pdf_content_even_with_non_pdf_extension(client):
    response = _post_compress(client, _make_pdf(rotation=0), filename="input.bin")
    assert response.status_code == 200
    assert response.content_type.startswith("application/pdf")
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


def test_compress_rejects_invalid_pdf_content(client):
    response = _post_compress(client, b"not-a-pdf", filename="broken.pdf")
    assert response.status_code == 400
    payload = response.get_json()
    assert "PDFファイルが破損しています" in payload["error"]
