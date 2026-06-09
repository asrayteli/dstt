"""shift-engine API（context / plan / apply-draft）の統合テスト。

既存 test_cloudshift.py と同じく、cloudshift モジュールを隔離ロードして
in-memory DB の Flask アプリにマウントして検証する。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import db  # noqa: E402


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_engine_api_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build():
    module = _load_cloudshift_module()
    import tempfile

    tmp = tempfile.mkdtemp()
    app = Flask(
        __name__,
        root_path=tmp,
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{Path(tmp) / 'cs.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.cloudshift_bp)
    with app.app_context():
        db.create_all()
    return module, app


def _owner():
    return SimpleNamespace(is_authenticated=True, username="owner01", name="Owner")


BASE = "/tools/shiftersync/cloudshift"


def _create_scene_project_with_candidate(module, app):
    client = app.test_client()
    module.current_user = _owner()
    resp = client.post(
        f"{BASE}/api/create",
        data={"title": "Engine Site", "mode": "scene", "year": "2026", "month": "4",
              "required_capacity": "1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    project_id = resp.get_json()["project"]["project"]["id"]

    # 候補者を assist 経由で 1 名用意する（Employee 不在でも在籍扱い）
    with app.app_context():
        project = module._load_project(project_id)
        project["assist"] = {
            "version": 1,
            "profiles": [{"employee_number": "E001", "name": "佐藤", "preferred_weekdays": [], "blocked_weekdays": []}],
            "records": [],
            "rules": [],
            "experienced_sites": [{"employee_number": "E001", "site_row_id": "10"}],
            "training_sites": [],
        }
        module._save_project(project)
    return client, project_id


def test_context_endpoint_returns_summary():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()
    resp = client.get(f"{BASE}/api/project/{project_id}/shift-engine/context?year=2026&month=4")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["mode"] == "scene"
    assert data["base_revision"] >= 1
    assert data["demand_source"] == "required_capacity_fallback"
    assert data["candidate_count"] >= 1
    assert "shift_engine_settings" in data
    assert isinstance(data["emptiness_factors"], list)


def test_plan_endpoint_generates_result():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()
    resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/plan",
        json={"year": 2026, "month": 4, "preferences": {"eligibility_baseline": "any"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["result"]["status"] in ("feasible", "partial")
    assert data["request_hash"]
    assert data["result"]["solver_backend"] == "greedy_repair_v1"
    # capacity=1 を 30 日に展開 → 候補 1 名なら少なくとも一部は埋まる
    assert data["result"]["score"]["assigned_count"] >= 1
    assert "draft_preview" in data


def test_apply_draft_saves_and_checks_revision():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()

    # まず plan して request_hash と base_revision を得る
    ctx_resp = client.get(f"{BASE}/api/project/{project_id}/shift-engine/context?year=2026&month=4")
    base_revision = ctx_resp.get_json()["base_revision"]
    plan_resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/plan",
        json={"year": 2026, "month": 4, "preferences": {"eligibility_baseline": "any"}},
    )
    request_hash = plan_resp.get_json()["request_hash"]

    # 正しい revision で apply-draft → 成功
    apply_resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/apply-draft",
        json={"year": 2026, "month": 4, "base_revision": base_revision,
              "request_hash": request_hash, "preferences": {"eligibility_baseline": "any"}},
    )
    assert apply_resp.status_code == 200, apply_resp.get_data(as_text=True)
    assert apply_resp.get_json()["success"] is True

    # 下書きが保存されている
    with app.app_context():
        project = module._load_project(project_id)
        month = project["months"]["2026-04"]
        draft = month.get("draft_entries_per_day") or {}
        assert any(draft.get(str(d)) for d in range(1, 31))

    # 古い revision で apply-draft → 409
    stale_resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/apply-draft",
        json={"year": 2026, "month": 4, "base_revision": 999,
              "request_hash": request_hash, "preferences": {"eligibility_baseline": "any"}},
    )
    assert stale_resp.status_code == 409


def test_apply_draft_rejects_hash_mismatch():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()
    ctx_resp = client.get(f"{BASE}/api/project/{project_id}/shift-engine/context?year=2026&month=4")
    base_revision = ctx_resp.get_json()["base_revision"]
    resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/apply-draft",
        json={"year": 2026, "month": 4, "base_revision": base_revision,
              "request_hash": "deadbeef", "preferences": {"eligibility_baseline": "any"}},
    )
    assert resp.status_code == 409


def test_preview_window_renders_for_owner():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()
    resp = client.get(f"{BASE}/project/{project_id}/shift-engine/preview?year=2026&month=4")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    html = resp.get_data(as_text=True)
    # 専用プレビューUI（CloudShift UI 流用・DSTT chrome なし）であること
    assert "cloud-engine-preview-page" in html
    assert "engine_preview" in html
    assert "cloud-editor-panel" in html  # CloudShift エディタのホスト
    # DSTT サイドバー（base.html）を継承していない
    assert "<!DOCTYPE html>" in html


def test_preview_window_requires_editor():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = SimpleNamespace(is_authenticated=True, username="intruder", name="X")
    resp = client.get(f"{BASE}/project/{project_id}/shift-engine/preview?year=2026&month=4")
    assert resp.status_code == 404


def test_preview_window_rejects_missing_month():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    module.current_user = _owner()
    resp = client.get(f"{BASE}/project/{project_id}/shift-engine/preview")
    assert resp.status_code == 404


def test_plan_requires_editor():
    module, app = _build()
    client, project_id = _create_scene_project_with_candidate(module, app)
    # 別ユーザー（非所有者）→ 404
    module.current_user = SimpleNamespace(is_authenticated=True, username="intruder", name="X")
    resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/plan",
        json={"year": 2026, "month": 4},
    )
    assert resp.status_code == 404
