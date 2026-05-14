from pathlib import Path
import shutil
import subprocess
import sys

from flask import Flask
from flask_login import LoginManager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.navigation import NAV_ITEMS
from app.tools.calc import calc_bp


def _build_client():
    app = Flask(
        __name__,
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return None

    @app.context_processor
    def inject_navigation():
        return {
            "app_navigation_items": NAV_ITEMS,
            "current_user_id": "tester",
            "app_version": "test",
            "default_page_title": lambda: "DSTT - test",
        }

    app.register_blueprint(calc_bp)
    return app.test_client()


def test_calc_page_renders_static_assets_and_modes():
    client = _build_client()

    response = client.get("/tools/calc/")

    assert response.status_code == 200
    assert b"/static/calc/calc.css" in response.data
    assert b"/static/calc/calc_engine.js" in response.data
    assert b"/static/calc/calc_ui.js" in response.data
    assert "通常".encode("utf-8") in response.data
    assert "税計算".encode("utf-8") in response.data
    assert "時間計算".encode("utf-8") in response.data
    assert "まぁまぁ便利な電卓".encode("utf-8") in str(NAV_ITEMS).encode("utf-8")


def test_calc_sources_do_not_call_eval_or_function_constructor():
    paths = [
        ROOT / "app" / "templates" / "calc.html",
        ROOT / "app" / "static" / "calc" / "calc_engine.js",
        ROOT / "app" / "static" / "calc" / "calc_ui.js",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "eval(" not in combined
    assert "new Function" not in combined
    assert "Function(" not in combined


def test_calc_engine_core_behaviour_with_node():
    if shutil.which("node") is None:
        return

    script = r"""
    const engine = require('./app/static/calc/calc_engine.js');
    const assert = require('assert');
    assert.strictEqual(engine.computeExpression('1+2*3').value, 7);
    assert.strictEqual(engine.computeExpression('(1+2)*3').value, 9);
    assert.strictEqual(engine.computeExpression('50%').value, 0.5);
    assert.throws(() => engine.computeExpression('1/0'), /0で割る/);
    assert.strictEqual(engine.calculateTaxIncluded(1000, 10).total, 1100);
    assert.strictEqual(engine.calculateTaxExcluded(1100, 10).base, 1000);
    assert.strictEqual(engine.computeTimeExpression('1:30 + 0:45').text, '2:15');
    assert.strictEqual(engine.computeTimeExpression('01:30 + 00:45').text, '2:15');
    assert.throws(() => engine.computeTimeExpression('1:000'), /時間/);
    assert.throws(() => engine.computeTimeExpression('000:00'), /時間/);
    """

    subprocess.run(["node", "-e", script], cwd=ROOT, check=True)
