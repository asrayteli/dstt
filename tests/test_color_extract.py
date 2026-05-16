import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

import fitz
import pytest
from flask import Flask
from PIL import Image


def _load_color_extract_module():
    module_path = ROOT / "app" / "tools" / "color_extract.py"
    spec = importlib.util.spec_from_file_location("color_extract_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def client():
    module = _load_color_extract_module()
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(module.color_extract_bp)
    with app.test_client() as c:
        yield c


def _make_sample_png_bytes():
    image = Image.new("RGBA", (16, 16), (0, 0, 255, 255))
    for x in range(8):
        for y in range(16):
            image.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _make_sample_pdf_bytes():
    doc = fitz.open()
    page = doc.new_page(width=120, height=120)
    page.draw_rect(fitz.Rect(0, 0, 60, 120), color=(1, 0, 0), fill=(1, 0, 0))
    page.draw_rect(fitz.Rect(60, 0, 120, 120), color=(0, 0, 1), fill=(0, 0, 1))
    data = doc.tobytes()
    doc.close()
    return data


def _make_multi_page_pdf_bytes():
    doc = fitz.open()
    p1 = doc.new_page(width=120, height=120)
    p1.draw_rect(fitz.Rect(0, 0, 120, 120), color=(1, 0, 0), fill=(1, 0, 0))
    p2 = doc.new_page(width=120, height=120)
    p2.draw_rect(fitz.Rect(0, 0, 120, 120), color=(0, 1, 0), fill=(0, 1, 0))
    data = doc.tobytes()
    doc.close()
    return data


def test_preview_png_returns_image(client):
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000", "#00ff00"]),
        "tolerance": "20",
        "transparent_output": "true",
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/preview", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("image/png")
    assert len(response.data) > 0


def test_process_png_can_download_jpg(client):
    payload = {
        "mode": "replace",
        "target_colors": json.dumps(["#ff0000"]),
        "replacement_color": "#00ff00",
        "tolerance": "5",
        "transparent_output": "false",
        "output_format": "jpg",
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("image/jpeg")
    assert "attachment" in response.headers.get("Content-Disposition", "")
    assert "sample_processed.jpg" in response.headers.get("Content-Disposition", "")


def test_preview_and_process_pdf(client):
    pdf_bytes = _make_sample_pdf_bytes()
    payload_base = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "10",
        "transparent_output": "true",
    }

    preview = client.post(
        "/tools/color_extract/preview",
        data={**payload_base, "file": (io.BytesIO(pdf_bytes), "sample.pdf")},
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    assert preview.content_type.startswith("image/png")

    processed = client.post(
        "/tools/color_extract/process",
        data={**payload_base, "file": (io.BytesIO(pdf_bytes), "sample.pdf")},
        content_type="multipart/form-data",
    )
    assert processed.status_code == 200
    assert processed.content_type.startswith("application/pdf")
    assert "attachment" in processed.headers.get("Content-Disposition", "")
    assert len(processed.data) > 0


def test_invalid_target_colors_returns_400(client):
    payload = {
        "mode": "exclude",
        "target_colors": "not-json",
        "tolerance": "20",
        "transparent_output": "true",
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/preview", data=payload, content_type="multipart/form-data")

    assert response.status_code == 400
    data = response.get_json()
    assert "target_colors" in data["error"]


def test_pdf_content_with_non_pdf_extension_is_still_processed(client):
    pdf_bytes = _make_sample_pdf_bytes()
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "10",
        "transparent_output": "true",
        "file": (io.BytesIO(pdf_bytes), "sample.bin"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("application/pdf")


def test_invalid_pdf_returns_400_with_clear_message(client):
    invalid_pdf_like = b"%PDF-1.4\nnot-a-real-pdf"
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "10",
        "transparent_output": "true",
        "file": (io.BytesIO(invalid_pdf_like), "broken.pdf"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 400
    data = response.get_json()
    assert "PDF" in data["error"]


def test_process_region_limits_effect(client):
    src = Image.new("RGBA", (20, 20), (0, 0, 255, 255))
    for x in range(20):
        for y in range(20):
            if x < 10:
                src.putpixel((x, y), (255, 0, 0, 255))
    src_buf = io.BytesIO()
    src.save(src_buf, format="PNG")

    payload = {
        "mode": "replace",
        "target_colors": json.dumps(["#ff0000"]),
        "replacement_color": "#00ff00",
        "tolerance": "10",
        "transparent_output": "false",
        "output_format": "png",
        "process_region": json.dumps({"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}),
        "file": (io.BytesIO(src_buf.getvalue()), "sample.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    assert out.getpixel((2, 2))[:3] == (255, 0, 0)
    assert out.getpixel((15, 2))[:3] == (0, 0, 255)


def test_invalid_region_returns_400(client):
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "10",
        "transparent_output": "true",
        "process_region": json.dumps({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}),
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/preview", data=payload, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "処理範囲" in response.get_json()["error"]


def test_auto_tune_returns_suggestion(client):
    payload = {
        "mode": "exclude",
        "preset": "stamp_red",
        "color_metric": "deltae",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "20",
        "saturation_min": "0",
        "lightness_min": "0",
        "lightness_max": "255",
        "enable_preprocess": "true",
        "transparent_output": "true",
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/auto_tune", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    data = response.get_json()
    assert "suggested_tolerance" in data
    assert 0 <= data["suggested_tolerance"] <= 120


@pytest.mark.parametrize(
    "preset",
    ["stamp_red", "blue_pen", "highlighter", "keep_black_text", "whiten_background"],
)
def test_preview_accepts_supported_presets(client, preset):
    payload = {
        "mode": "exclude",
        "preset": preset,
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "30",
        "transparent_output": "true",
        "file": (io.BytesIO(_make_sample_png_bytes()), f"{preset}.png"),
    }
    response = client.post("/tools/color_extract/preview", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("image/png")


def test_stamp_red_preset_can_reduce_red_noise(client):
    img = Image.new("RGBA", (30, 20), (255, 255, 255, 255))
    for x in range(5, 25):
        for y in range(6, 14):
            img.putpixel((x, y), (220, 40, 40, 255))
    img.putpixel((1, 1), (220, 35, 35, 255))
    img.putpixel((28, 18), (225, 45, 45, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    payload = {
        "mode": "exclude",
        "preset": "stamp_red",
        "color_metric": "deltae",
        "target_colors": json.dumps(["#dd2222"]),
        "tolerance": "30",
        "saturation_min": "0",
        "lightness_min": "0",
        "lightness_max": "255",
        "enable_preprocess": "true",
        "enable_postprocess": "true",
        "min_component_area": "10",
        "transparent_output": "false",
        "output_format": "png",
        "file": (io.BytesIO(buf.getvalue()), "stamp.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    assert out.getpixel((10, 10))[:3] == (255, 255, 255)
    assert out.getpixel((1, 1))[:3] != (255, 255, 255)


def test_preview_pdf_returns_page_headers(client):
    pdf_bytes = _make_multi_page_pdf_bytes()
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "20",
        "transparent_output": "true",
        "preview_page": "2",
        "file": (io.BytesIO(pdf_bytes), "multi.pdf"),
    }
    response = client.post("/tools/color_extract/preview", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.headers.get("X-PDF-Total-Pages") == "2"
    assert response.headers.get("X-PDF-Page") == "2"


def test_process_region_png_output_for_image(client):
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#ff0000"]),
        "tolerance": "20",
        "transparent_output": "true",
        "output_region_png": "true",
        "process_region": json.dumps({"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}),
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("image/png")
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    assert out.size == (8, 16)


def test_process_region_png_output_for_pdf_current_page(client):
    pdf_bytes = _make_multi_page_pdf_bytes()
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#00ff00"]),
        "tolerance": "20",
        "transparent_output": "true",
        "output_region_png": "true",
        "preview_page": "2",
        "process_region": json.dumps({"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}),
        "file": (io.BytesIO(pdf_bytes), "multi.pdf"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.content_type.startswith("image/png")
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    assert out.width > 0 and out.height > 0


def test_process_with_rect_fill_transparent_paint_action(client):
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#00ff00"]),
        "tolerance": "1",
        "transparent_output": "false",
        "output_format": "png",
        "paint_actions": json.dumps(
            [
                {
                    "type": "rect_fill",
                    "x": 0.0,
                    "y": 0.0,
                    "w": 0.5,
                    "h": 1.0,
                    "color": "#00000000",
                    "page": None,
                }
            ]
        ),
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")
    assert response.status_code == 200
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    assert out.getpixel((2, 2))[3] == 0
    assert out.getpixel((12, 2))[3] == 255


def test_process_with_free_draw_paint_action(client):
    payload = {
        "mode": "exclude",
        "target_colors": json.dumps(["#00ff00"]),
        "tolerance": "1",
        "transparent_output": "false",
        "output_format": "png",
        "paint_actions": json.dumps(
            [
                {
                    "type": "free_draw",
                    "points": [{"x": 0.6, "y": 0.5}, {"x": 0.9, "y": 0.5}],
                    "size": 4,
                    "color": "#00FF00FF",
                    "page": None,
                }
            ]
        ),
        "file": (io.BytesIO(_make_sample_png_bytes()), "sample.png"),
    }
    response = client.post("/tools/color_extract/process", data=payload, content_type="multipart/form-data")
    assert response.status_code == 200
    out = Image.open(io.BytesIO(response.data)).convert("RGBA")
    px = out.getpixel((13, 8))
    assert px[:3] == (0, 255, 0)
