import importlib.util
import base64
import io
import json
from pathlib import Path

import fitz
from flask import Flask
from PIL import Image


def _load_powerstamp_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "tools" / "powerstamp.py"
    spec = importlib.util.spec_from_file_location("powerstamp_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_test_app(module):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "app" / "templates"))
    app.secret_key = "test"
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(module.powerstamp_bp)
    return app


def _transparent_png_data_url():
    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def test_powerstamp_screen_is_available():
    module = _load_powerstamp_module()
    app = _make_test_app(module)

    client = app.test_client()
    response = client.get("/tools/powerstamp/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "PowerSTAMP" in html
    assert "実寸PDFを開く（印刷用）" in html
    assert "detectedSize" in html
    assert "JPG/PNG/PDF" in html
    assert "長3封筒" in html
    assert "角2封筒" in html
    assert ">A5<" in html
    assert ">A6<" in html
    assert "郵便番号（7桁）" in html
    assert "postalCodeInput" in html
    assert "社員名簿PLUS" in html
    assert "plusEmployeeSearchInput" in html
    assert "applyPlusEmployeeBtn" in html
    assert "undoBtn" in html
    assert "redoBtn" in html
    assert "saveLocalTemplateBtn" in html
    assert "selectedPropertiesPanel" in html
    assert "別タブプレビュー" not in html
    assert "実物照合タブ" not in html
    assert "saveTemplateBtn" not in html


def test_powerstamp_exact_pdf_uses_requested_mm_page_size():
    module = _load_powerstamp_module()
    app = _make_test_app(module)
    client = app.test_client()

    response = client.post(
        "/tools/powerstamp/api/exact-pdf",
        json={
            "image": _transparent_png_data_url(),
            "page": {"w_mm": 120, "h_mm": 235},
            "filename": "stamp.pdf",
        },
    )

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"

    doc = fitz.open(stream=response.data, filetype="pdf")
    try:
        assert doc.page_count == 1
        page = doc[0]
        assert abs(page.rect.width - (120 * 72 / 25.4)) < 0.01
        assert abs(page.rect.height - (235 * 72 / 25.4)) < 0.01
    finally:
        doc.close()


def test_powerstamp_seed_templates_include_exact_papers_and_zip_guides():
    seed_path = Path(__file__).resolve().parents[1] / "app" / "data" / "powerstamp_templates.seed.json"
    templates = json.loads(seed_path.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in templates}

    for name in [
        "用紙テンプレ_A4_210x297mm_300dpi",
        "用紙テンプレ_A3_297x420mm_300dpi",
        "用紙テンプレ_A5_148x210mm_300dpi",
        "用紙テンプレ_A6_105x148mm_300dpi",
        "用紙テンプレ_B4_257x364mm_300dpi",
        "用紙テンプレ_B5_182x257mm_300dpi",
    ]:
        assert name in by_name

    naga3 = by_name["封筒テンプレ_長3_120x235mm_300dpi"]["data"]["guide_mm"]["boxes"][0]
    kaku2 = by_name["封筒テンプレ_角2_240x332mm_300dpi"]["data"]["guide_mm"]["boxes"][0]

    assert naga3["type"] == "zip7"
    assert naga3["x_mm"] == 64.3
    assert naga3["y_mm"] == 12
    assert naga3["w_mm"] == 47.7
    assert naga3["h_mm"] == 8
    assert naga3["group_gap_mm"] == 2.8
    assert naga3["digit_font_size_mm"] == 5.0

    assert kaku2["type"] == "zip7"
    assert kaku2["x_mm"] == 140
    assert kaku2["y_mm"] == 17
    assert kaku2["w_mm"] == 83
    assert kaku2["h_mm"] == 15
    assert kaku2["inter_gap_mm"] == 2.0
    assert kaku2["group_gap_mm"] == 4.0
    assert kaku2["digit_font_size_mm"] == 5.0


def test_powerstamp_preview_and_verify_routes_are_removed():
    module = _load_powerstamp_module()
    app = _make_test_app(module)
    client = app.test_client()

    assert client.get("/tools/powerstamp/preview").status_code == 404
    assert client.get("/tools/powerstamp/verify").status_code == 404


def test_powerstamp_manual_matches_current_screen():
    from app.manuals import get_manual

    manual = get_manual("powerstamp")
    text = "\n".join(manual["steps"])

    assert "Undo / Redo" in text
    assert "選択中の編集" in text
    assert "別タブでプレビュー" not in text
    assert "見比べタブ" not in text
