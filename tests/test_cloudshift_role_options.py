"""代務（SUB）/ 研修（TRAIN）オプションと経験自動登録の統合テスト。

目的: ユーザーが entry に役割オプションを付けるだけで経験がサーバーへ自動登録され、
アシスト検索と自動作成（shift-engine）に反映されることを固定する。
既存データ（手動実績・他ソースの自動実績）を壊さないことも検証する。
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
from app.tools.shiftersync_check import is_duplicate_by_rules  # noqa: E402


def _load_cloudshift_module():
    module_path = ROOT / "app" / "tools" / "cloudshift.py"
    spec = importlib.util.spec_from_file_location("cloudshift_role_option_module", module_path)
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


def _create_scene_project(module, app, *, site_row_id="10"):
    client = app.test_client()
    module.current_user = _owner()
    resp = client.post(
        f"{BASE}/api/create",
        data={"title": "Role Site", "mode": "scene", "year": "2026", "month": "4",
              "required_capacity": "1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    project_id = resp.get_json()["project"]["project"]["id"]
    with app.app_context():
        project = module._load_project(project_id)
        if site_row_id:
            project["site_row_id"] = site_row_id
        module._save_project(project)
    return client, project_id


def _save_month_entries(module, app, client, project_id, entries_per_day):
    with app.app_context():
        project = module._load_project(project_id)
        base_month = project["months"]["2026-04"]
    resp = client.put(
        f"{BASE}/api/project/{project_id}/month/2026/4",
        json={"required_capacity": 1, "entries_per_day": entries_per_day,
              "base_month": base_month},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp


def _auto_records(module, app, project_id):
    with app.app_context():
        project = module._load_project(project_id)
        return [
            r for r in (project.get("assist") or {}).get("records", [])
            if r.get("source_type") == module.ROLE_OPTION_ASSIST_SOURCE
        ], project


# ---------------------------------------------------------------------------
# オプション定義・重複ルール
# ---------------------------------------------------------------------------


def test_role_options_registered_in_labels():
    module = _load_cloudshift_module()
    assert module.OPTION_LABELS["SUB"] == "代務"
    assert module.OPTION_LABELS["TRAIN"] == "研修"


def test_second_option_does_not_affect_duplicate_check():
    """代務・研修は「第二オプション」。entry の値に入らないため重複判定に影響しない。"""
    from app.tools.shiftersync_format import normalize_entry, entry_second_option
    from app.tools.shiftersync_check import compare_shift_payloads

    # 旧形式 `!SUB!名前` は正規化で第二オプションへ移行し、値は素の名前になる
    entry = normalize_entry({"value": "!SUB!田中", "employee_number": "E1"})
    assert entry["second_option"] == "SUB"
    assert entry["value"] == "田中"
    assert entry_second_option(entry) == "SUB"

    # 午前シフト + 代務（第二オプション）は、重複判定では午前としてのみ扱う
    morning_with_sub = normalize_entry(
        {"value": "!A!田中", "second_option": "SUB", "employee_number": "E1"}
    )
    assert morning_with_sub["value"] == "!A!田中"
    assert morning_with_sub["second_option"] == "SUB"

    # 同日同名で「午前＋代務」と「遅番」→ A と L は共存可（第二オプションは無視）
    payload = {
        "mode": "scene", "year": 2026, "month": 4, "title": "S1",
        "entries_per_day": {
            "1": [
                {"id": "a", "value": "!A!田中", "second_option": "SUB", "employee_number": "E1"},
                {"id": "b", "value": "!L!田中", "employee_number": "E1"},
            ]
        },
    }
    result = compare_shift_payloads([payload])
    same_site = [c for c in result["same_site_conflicts"] if c["date"] == 1]
    assert same_site == []

    # is_duplicate_by_rules 自体も代務/研修を終日拘束扱いしない
    assert is_duplicate_by_rules("SUB", "A") is False
    assert is_duplicate_by_rules("TRAIN", "M") is False
    # 既存ルールの非回帰
    assert is_duplicate_by_rules("A", "L") is False
    assert is_duplicate_by_rules("A", "E") is True


# ---------------------------------------------------------------------------
# 経験の自動登録（月保存フック）
# ---------------------------------------------------------------------------


def test_save_month_auto_registers_role_experience():
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()

    # 事前に手動実績を登録しておく（自動登録に巻き込まれないことの検証用）
    with app.app_context():
        project = module._load_project(project_id)
        assist = module._ensure_assist(project)
        manual = module._assist_record_from_payload(
            assist,
            {"date": "2026-04-01", "candidate_name": "手動太郎",
             "employee_number": "E900", "shift_key": "A", "role_type": "normal",
             "notes": "手動登録"},
            actor_name="tester",
        )
        assist["records"].append(manual)
        module._save_project(project)

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "3": [{"id": "e3", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    autos, project = _auto_records(module, app, project_id)
    by_key = {(r["employee_number"], r["shift_key"]): r for r in autos}
    assert set(by_key) == {("E101", "SUB"), ("E102", "TRAIN")}
    sub = by_key[("E101", "SUB")]
    assert sub["source_month_key"] == "2026-04"
    assert sub["source_occurrences"] == 2
    assert sub["date"] == "2026-04-02"  # 月内の最新該当日
    assert sub["candidate_name"] == "田中"
    # プロファイルも自動作成される
    profiles = {p["employee_number"] for p in project["assist"]["profiles"]}
    assert {"E101", "E102"} <= profiles
    # 手動実績は無傷
    manual_records = [r for r in project["assist"]["records"] if not r.get("source_type")]
    assert any(r["employee_number"] == "E900" for r in manual_records)


def test_save_month_updates_and_removes_auto_records():
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "3": [{"id": "e3", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    # 2日の代務を消す → occurrences が 1 に更新
    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "3": [{"id": "e3", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })
    autos, _ = _auto_records(module, app, project_id)
    sub = next(r for r in autos if r["shift_key"] == "SUB")
    assert sub["source_occurrences"] == 1
    assert sub["date"] == "2026-04-01"

    # 代務 entry を全部消す → SUB の自動実績だけ解除され、TRAIN は残る
    _save_month_entries(module, app, client, project_id, {
        "3": [{"id": "e3", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })
    autos, _ = _auto_records(module, app, project_id)
    assert [r["shift_key"] for r in autos] == ["TRAIN"]


def test_employee_number_missing_is_not_registered():
    """社員番号の無い entry は自動登録しない（名前だけの照合はしない原則）。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()
    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!番号なし", "comment": ""}],
    })
    autos, _ = _auto_records(module, app, project_id)
    assert autos == []


def _experienced_sites(module, app, project_id):
    with app.app_context():
        project = module._load_project(project_id)
        return (project.get("assist") or {}).get("experienced_sites", []), project


def test_second_option_reflects_experienced_sites():
    """代務・研修いずれもアシストの「経験済み現場」へ自動反映される（要件4/5）。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    module.current_user = _owner()

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    sites, _ = _experienced_sites(module, app, project_id)
    autos = [s for s in sites if s.get("source_type") == module.ROLE_OPTION_ASSIST_SOURCE]
    by_emp = {(s["employee_number"], s["shift_key"]): s for s in autos}
    assert set(by_emp) == {("E101", "SUB"), ("E102", "TRAIN")}
    assert all(str(s["site_row_id"]) == "10" for s in autos)

    # 代務 entry を消すと、その経験済み現場の自動分だけ解除される
    _save_month_entries(module, app, client, project_id, {
        "2": [{"id": "e2", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })
    sites, _ = _experienced_sites(module, app, project_id)
    autos = [s for s in sites if s.get("source_type") == module.ROLE_OPTION_ASSIST_SOURCE]
    assert {(s["employee_number"], s["shift_key"]) for s in autos} == {("E102", "TRAIN")}


def test_training_option_removes_training_required_site():
    """研修第二オプションは「研修要現場」一覧から該当を削除する（要件5）。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    module.current_user = _owner()

    # 事前に E102 × 現場10 の「研修要現場」を手動登録しておく
    with app.app_context():
        project = module._load_project(project_id)
        assist = module._ensure_assist(project)
        assist["training_sites"].append({
            "id": "t1", "kind": "training", "employee_number": "E102",
            "site_row_id": "10", "site_id": "", "site_name": "現場10",
            "shift_key": "", "date": "2026-03-01",
        })
        module._save_project(project)

    _save_month_entries(module, app, client, project_id, {
        "3": [{"id": "e3", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    with app.app_context():
        project = module._load_project(project_id)
        training = (project.get("assist") or {}).get("training_sites", [])
    # 研修要現場から削除されている
    assert all(t.get("id") != "t1" for t in training)


# ---------------------------------------------------------------------------
# 自動作成（shift-engine）への反映
# ---------------------------------------------------------------------------


def test_role_experience_feeds_shift_engine():
    """SUB/TRAIN オプションを付けるだけで、その人が自動作成の適格候補になる。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    # 既定の最低基準（専従・経験者のみ）のまま plan する
    resp = client.post(
        f"{BASE}/api/project/{project_id}/shift-engine/plan",
        json={"year": 2026, "month": 4},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    engine_assignments = [a for a in data["result"]["assignments"] if a["source"] == "engine"]
    assigned_numbers = {a["employee_number"] for a in engine_assignments}
    # 代務の E101、研修の E102 の両方が配置対象になる
    assert assigned_numbers == {"E101", "E102"}

    # 要件どおり代務・研修いずれも「経験済み現場」として反映され、経験者扱いになる
    panels = data["result"]["candidate_panels"]
    factor_labels = {
        c["employee_number"]: {f["label"] for f in c["factors"]}
        for p in panels for c in p["candidates"]
    }
    assert "経験者" in factor_labels.get("E101", set())
    assert "経験者" in factor_labels.get("E102", set())


# ---------------------------------------------------------------------------
# アシスト検索への反映（点数差）
# ---------------------------------------------------------------------------


def test_assist_search_scores_substitute_above_training():
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!TRAIN!山田", "employee_number": "E102", "comment": ""}],
    })

    resp = client.post(
        f"{BASE}/api/project/{project_id}/assist/search",
        json={"target_date": "2026-04-20", "shift_key": "", "limit": 10},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    candidates = {c["employee_number"]: c for c in resp.get_json()["results"]}
    assert "E101" in candidates and "E102" in candidates
    # 代務（=実績）は研修より高得点
    assert candidates["E101"]["score"] > candidates["E102"]["score"]
    assert any("代務実績" in reason for reason in candidates["E101"]["reasons"])
    assert any("研修実績" in reason for reason in candidates["E102"]["reasons"])
    train_breakdown = [b for b in candidates["E102"]["breakdown"] if b["label"] == "研修実績"]
    assert train_breakdown and train_breakdown[0]["base_points"] == module.ASSIST_TRAINING_RECORD_POINTS
