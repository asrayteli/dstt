import importlib.util
import io
import json
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


def _encrypted_pdf_bytes(password, label="LOCKED"):
    encrypted = io.BytesIO()
    with pikepdf.open(io.BytesIO(_make_pdf(label))) as pdf:
        pdf.save(
            encrypted,
            encryption=pikepdf.Encryption(owner=password, user=password, R=6),
        )
    return encrypted.getvalue()


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


def test_editor_window_route_redirects_to_editor_window_mode(client):
    response = client.get("/tools/pdf_power/editor", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tools/pdf_power/?editor_window=1")


def test_control_process_can_reorder_rotate_and_watermark_pages(client):
    plan = {
        "outputName": "ordered-control.pdf",
        "documents": [
            {"id": "doc-a", "uploadIndex": 0, "name": "first.pdf", "password": ""},
            {"id": "doc-b", "uploadIndex": 1, "name": "second.pdf", "password": ""},
        ],
        "pages": [
            {"documentId": "doc-b", "sourcePageIndex": 0, "rotation": 0},
            {"documentId": "doc-a", "sourcePageIndex": 1, "rotation": 90},
        ],
        "metadata": {"title": "Control Title"},
        "watermark": {
            "enabled": True,
            "text": "STAMP",
            "pages": "all",
            "position": "center",
            "color": "#94a3b8",
            "opacity": 0.2,
            "font_size": 42,
            "rotation": 45,
        },
        "outputPassword": {"enabled": False, "password": ""},
    }

    response = client.post(
        "/tools/pdf_power/control_process",
        data={
            "plan": json.dumps(plan),
            "pdfs": [
                (io.BytesIO(_make_pdf("FIRST", pages=2)), "first.pdf"),
                (io.BytesIO(_make_pdf("SECOND", pages=1)), "second.pdf"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.page_count == 2
    assert "SECOND-1" in doc[0].get_text("text")
    assert "FIRST-2" in doc[1].get_text("text")
    assert doc[1].rotation == 90
    assert doc.metadata["title"] == "Control Title"
    assert doc[0].search_for("STAMP")
    doc.close()


def test_control_process_can_unlock_input_pdf_when_output_password_is_off(client):
    plan = {
        "outputName": "decrypted-output.pdf",
        "documents": [
            {"id": "doc-a", "uploadIndex": 0, "name": "locked.pdf", "password": "input-secret"},
        ],
        "pages": [
            {"documentId": "doc-a", "sourcePageIndex": 0, "rotation": 0},
        ],
        "watermark": {"enabled": False},
        "metadata": {},
        "outputPassword": {"enabled": False, "password": ""},
    }

    response = client.post(
        "/tools/pdf_power/control_process",
        data={
            "plan": json.dumps(plan),
            "pdfs": [(io.BytesIO(_encrypted_pdf_bytes("input-secret")), "locked.pdf")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert not doc.needs_pass
    assert "LOCKED-1" in doc[0].get_text("text")
    doc.close()


def test_control_process_can_set_output_password(client):
    plan = {
        "outputName": "protected-output.pdf",
        "documents": [
            {"id": "doc-a", "uploadIndex": 0, "name": "plain.pdf", "password": ""},
        ],
        "pages": [
            {"documentId": "doc-a", "sourcePageIndex": 0, "rotation": 0},
        ],
        "watermark": {"enabled": False},
        "metadata": {},
        "outputPassword": {"enabled": True, "password": "output-secret"},
    }

    response = client.post(
        "/tools/pdf_power/control_process",
        data={
            "plan": json.dumps(plan),
            "pdfs": [(io.BytesIO(_make_pdf("PLAIN")), "plain.pdf")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    doc = fitz.open(stream=response.data, filetype="pdf")
    assert doc.needs_pass
    with pytest.raises(ValueError):
        doc[0].get_text("text")
    assert doc.authenticate("output-secret")
    assert "PLAIN-1" in doc[0].get_text("text")
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


def test_pdf_power_control_script_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    script_path = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "pdf_power_control.js"
    result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_pdf_power_editor_script_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    script_path = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "pdf_power_editor.js"
    result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_pdf_power_template_loads_editor_script_once():
    template_path = Path(__file__).resolve().parents[1] / "app" / "templates" / "pdf_power.html"
    raw = template_path.read_text(encoding="utf-8")
    assert raw.count("js/pdf_power_editor.js") == 1


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
