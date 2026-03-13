import importlib.util
from pathlib import Path

from flask import Flask


def _load_powerstamp_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "tools" / "powerstamp.py"
    spec = importlib.util.spec_from_file_location("powerstamp_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_powerstamp_screen_is_available():
    module = _load_powerstamp_module()
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "app" / "templates"))
    app.secret_key = "test"
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(module.powerstamp_bp)

    client = app.test_client()
    response = client.get("/tools/powerstamp/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "PowerSTAMP" in html
    assert "印刷（スタンプのみ）" in html
    assert "サイズ推定" in html
    assert "JPG/PNG/PDF" in html
    assert "郵便番号（7桁）" in html
    assert "postalCodeInput" in html
