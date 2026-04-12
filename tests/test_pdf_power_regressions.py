import importlib.util
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

fitz = pytest.importorskip("fitz")
pikepdf = pytest.importorskip("pikepdf")


def _load_pdf_power_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "tools" / "pdf_power.py"
    spec = importlib.util.spec_from_file_location("pdf_power_regression_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_pdf(label="SOURCE_REGION", pages=1):
    doc = fitz.open()
    for page_number in range(pages):
        page = doc.new_page(width=420, height=320)
        page.insert_text((60, 90), f"{label}-{page_number + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _png_bytes(color=(40, 120, 220)):
    image = Image.new("RGB", (48, 48), color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def pdf_power_module():
    return _load_pdf_power_module()


@pytest.fixture
def client(pdf_power_module):
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(pdf_power_module.pdf_power_bp)
    return app.test_client()


def test_split_accepts_uppercase_pdf_extension(client):
    response = client.post(
        "/tools/pdf_power/split_merge",
        data={
            "mode": "split",
            "range": "1",
            "pdfs": [(io.BytesIO(_make_pdf()), "INPUT.PDF")],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


def test_merge_preserves_distinct_files_even_when_names_match(client):
    response = client.post(
        "/tools/pdf_power/split_merge",
        data={
            "mode": "merge",
            "pdfs": [
                (io.BytesIO(_make_pdf("FIRST")), "same.pdf"),
                (io.BytesIO(_make_pdf("SECOND")), "same.pdf"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 2
    assert "FIRST-1" in doc[0].get_text("text")
    assert "SECOND-1" in doc[1].get_text("text")
    doc.close()


def test_watermark_route_returns_pdf(client):
    response = client.post(
        "/tools/pdf_power/watermark",
        data={
            "pdf": (io.BytesIO(_make_pdf()), "input.pdf"),
            "watermark_text": "DRAFT",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 1
    assert doc[0].search_for("DRAFT")
    doc.close()


def test_convert_to_pdf_handles_mixed_text_and_image_uploads(client):
    response = client.post(
        "/tools/pdf_power/convert",
        data={
            "direction": "to_pdf",
            "files": [
                (io.BytesIO("HELLO TEXT".encode("utf-8")), "note.txt"),
                (io.BytesIO(_png_bytes()), "photo.png"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 2
    assert doc[0].search_for("HELLO TEXT")
    assert len(doc[1].get_images(full=True)) >= 1
    doc.close()


def test_pdf_power_template_scripts_are_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    template_path = Path(__file__).resolve().parents[1] / "app" / "templates" / "pdf_power.html"
    raw = template_path.read_text(encoding="utf-8")
    script_parts = re.findall(r"<script[^>]*>(.*?)</script>", raw, re.S)
    js = "\n".join(script_parts)
    js = re.sub(r"\{\{[^\r\n]*\}\}", "0", js)
    js = re.sub(r"\{%[^\r\n]*%\}", "", js)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".js", encoding="utf-8") as tmp:
            tmp.write(js)
            temp_path = tmp.name
        result = subprocess.run([node, "--check", temp_path], capture_output=True, text=True)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    assert result.returncode == 0, result.stderr or result.stdout


def test_password_remove_with_wrong_password_returns_html_error_screen(client):
    protected_pdf = io.BytesIO()
    with pikepdf.open(io.BytesIO(_make_pdf("LOCKED"))) as pdf:
        pdf.save(
            protected_pdf,
            encryption=pikepdf.Encryption(owner="secret", user="secret", R=6),
        )
    protected_pdf.seek(0)

    response = client.post(
        "/tools/pdf_power/password",
        data={
            "pdf": (protected_pdf, "locked.pdf"),
            "mode": "remove",
            "password": "wrong-password",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert not response.is_json
    body = response.get_data(as_text=True)
    assert "入力されたパスワードが正しくありません" in body
    assert 'value="remove" checked' in body
