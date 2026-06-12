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


def _create_person_project(module, app, client, *, title, employee_number):
    module.current_user = _owner()
    resp = client.post(
        f"{BASE}/api/create",
        data={"title": title, "mode": "person", "year": "2026", "month": "4",
              "employee_number": employee_number},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["project"]["project"]["id"]


def _person_assist_payload(client, project_id):
    resp = client.get(f"{BASE}/api/project/{project_id}/assist")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["assist"]


def test_second_option_reflects_person_book_experienced_sites():
    """scene 帳の代務/研修は、社員番号一致の person 帳の「経験済み現場」にも反映される。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!三木", "employee_number": "E101", "comment": ""}],
        "2": [{"id": "e2", "value": "!TRAIN!三木", "employee_number": "E101", "comment": ""}],
    })

    assist = _person_assist_payload(client, person_id)
    autos = {s["shift_key"]: s for s in assist["experienced_sites"]}
    assert set(autos) == {"SUB", "TRAIN"}
    assert all(s["source_label"] for s in autos.values())
    assert all(s["site_name"] == "Role Site" for s in autos.values())

    # 代務 entry を消すと person 側の自動分も解除される
    _save_month_entries(module, app, client, project_id, {
        "2": [{"id": "e2", "value": "!TRAIN!三木", "employee_number": "E101", "comment": ""}],
    })
    assist = _person_assist_payload(client, person_id)
    assert [s["shift_key"] for s in assist["experienced_sites"]] == ["TRAIN"]


def test_training_option_removes_person_book_training_site():
    """研修第二オプションは person 帳の「研修要現場」からも該当現場を削除する。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    # person 帳に同じ現場の研修要現場を手動登録しておく（現場名で照合される）
    with app.app_context():
        person = module._load_project(person_id)
        assist = module._ensure_person_assist(person)
        assist["training_sites"].append({
            "id": "t1", "kind": "training", "site_row_id": None, "site_id": "",
            "site_name": "Role Site", "shift_key": "", "date": "2026-03-01",
        })
        module._save_project(person)

    _save_month_entries(module, app, client, project_id, {
        "3": [{"id": "e3", "value": "!TRAIN!三木", "employee_number": "E101", "comment": ""}],
    })

    assist = _person_assist_payload(client, person_id)
    assert all(t["id"] != "t1" for t in assist["training_sites"])
    assert [s["shift_key"] for s in assist["experienced_sites"]] == ["TRAIN"]


def test_person_book_created_after_scene_save_is_backfilled():
    """person 帳を後から作成しても、既存 scene 帳の代務/研修経験が取り込まれる。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    module.current_user = _owner()

    _save_month_entries(module, app, client, project_id, {
        "5": [{"id": "e1", "value": "!TRAIN!佐藤", "employee_number": "E202", "comment": ""}],
    })

    person_id = _create_person_project(module, app, client, title="佐藤 一郎", employee_number="E202")
    assist = _person_assist_payload(client, person_id)
    assert [s["shift_key"] for s in assist["experienced_sites"]] == ["TRAIN"]
    assert assist["experienced_sites"][0]["source_label"]


def test_role_option_person_site_does_not_double_count_on_scene():
    """第二オプション由来の person 経験は scene へ person_experience として再連携しない。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!三木", "employee_number": "E101", "comment": ""}],
    })

    # scene 側のバックフィルを明示的に再実行しても person_experience 実績は増えない
    with app.app_context():
        scene = module._load_project(project_id)
        module._backfill_scene_project_from_person_experience(scene, actor_name="tester")
        scene = module._load_project(project_id)
        records = (scene.get("assist") or {}).get("records", [])
    source_types = {str(r.get("source_type") or "") for r in records}
    assert module.PERSON_ASSIST_AUTO_SOURCE not in source_types
    assert module.ROLE_OPTION_ASSIST_SOURCE in source_types


def test_publish_draft_registers_role_experience():
    """仮保存→公開のパスでも代務/研修の経験が自動登録される。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app, site_row_id="10")
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")
    module.current_user = _owner()

    resp = client.put(
        f"{BASE}/api/project/{project_id}/month/2026/4/draft",
        json={"required_capacity": 1, "entries_per_day": {
            "1": [{"id": "e1", "value": "!SUB!三木", "employee_number": "E101", "comment": ""}],
        }},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # 仮保存の時点では登録されない
    autos, _ = _auto_records(module, app, project_id)
    assert autos == []

    resp = client.post(f"{BASE}/api/project/{project_id}/month/2026/4/draft/publish")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    autos, _ = _auto_records(module, app, project_id)
    assert [(r["employee_number"], r["shift_key"]) for r in autos] == [("E101", "SUB")]
    assist = _person_assist_payload(client, person_id)
    assert [s["shift_key"] for s in assist["experienced_sites"]] == ["SUB"]


def test_person_book_second_option_registers_own_sites():
    """個人シフト帳に直接入力した代務/研修が、その人の経験済み現場へ自動登録される。"""
    module, app = _build()
    client = app.test_client()
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    # 研修要現場を手動登録しておく（研修オプションで削除されることの検証用）
    with app.app_context():
        person = module._load_project(person_id)
        assist = module._ensure_person_assist(person)
        assist["training_sites"].append({
            "id": "t1", "kind": "training", "site_row_id": None, "site_id": "",
            "site_name": "千葉養護", "shift_key": "", "date": "2026-03-01",
        })
        module._save_project(person)

    # 現場リンク付き（研修）と名前だけ（代務）の両方を直接入力
    _save_month_entries(module, app, client, person_id, {
        "1": [{"id": "p1", "value": "!A!千葉養護", "second_option": "TRAIN",
               "site_name": "千葉養護", "comment": ""}],
        "2": [{"id": "p2", "value": "!M!日本生命成田", "second_option": "SUB", "comment": ""}],
    })

    assist = _person_assist_payload(client, person_id)
    by_site = {(s["site_name"], s["shift_key"]): s for s in assist["experienced_sites"]}
    assert set(by_site) == {("千葉養護", "TRAIN"), ("日本生命成田", "SUB")}
    assert all(s["source_label"] for s in by_site.values())
    # 研修要現場から削除されている
    assert all(t["id"] != "t1" for t in assist["training_sites"])

    # 研修 entry を消すと、その自動分だけ解除される
    _save_month_entries(module, app, client, person_id, {
        "2": [{"id": "p2", "value": "!M!日本生命成田", "second_option": "SUB", "comment": ""}],
    })
    assist = _person_assist_payload(client, person_id)
    assert {(s["site_name"], s["shift_key"]) for s in assist["experienced_sites"]} == {("日本生命成田", "SUB")}


def test_person_book_second_option_syncs_to_scene_book():
    """個人帳の第二オプションは同期で scene 帳へ伝わり、scene 側の実績にも自動登録される。"""
    module, app = _build()
    client = app.test_client()
    module.current_user = _owner()
    resp = client.post(
        f"{BASE}/api/create",
        data={"title": "千葉養護", "mode": "scene", "year": "2026", "month": "4",
              "required_capacity": "1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    scene_id = resp.get_json()["project"]["project"]["id"]
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    _save_month_entries(module, app, client, person_id, {
        "1": [{"id": "p1", "value": "!A!千葉養護", "second_option": "TRAIN",
               "site_name": "千葉養護", "comment": ""}],
    })

    with app.app_context():
        scene = module._load_project(scene_id)
        synced = scene["months"]["2026-04"]["entries_per_day"].get("1", [])
    # 同期 entry に第二オプションと社員番号が引き継がれる
    assert [(e.get("second_option"), e.get("employee_number")) for e in synced] == [("TRAIN", "E101")]
    # scene 帳の保存を待たずに自動実績が登録される
    autos, _ = _auto_records(module, app, scene_id)
    assert [(r["employee_number"], r["shift_key"]) for r in autos] == [("E101", "TRAIN")]

    # scene 帳を保存しても person 帳の経験済み現場は二重にならない
    _save_month_entries(module, app, client, scene_id, {
        "1": [{"id": "s1", "value": "!A!三木", "second_option": "TRAIN",
               "employee_number": "E101", "comment": ""}],
    })
    assist = _person_assist_payload(client, person_id)
    chiba = [s for s in assist["experienced_sites"]
             if s["site_name"] == "千葉養護" and s["shift_key"] == "TRAIN"]
    assert len(chiba) == 1


def test_person_book_save_reads_whole_month_including_synced_entries():
    """月保存時は編集中の月全体を読み直し、同期 entry の研修/代務も
    （entry に変更がなくても）反映する。"""
    module, app = _build()
    client = app.test_client()
    person_id = _create_person_project(module, app, client, title="三木 利秋", employee_number="E101")

    with app.app_context():
        person = module._load_project(person_id)
        assist = module._ensure_person_assist(person)
        assist["training_sites"].append({
            "id": "t1", "kind": "training", "site_row_id": None, "site_id": "",
            "site_name": "千葉養護", "shift_key": "", "date": "2026-03-01",
        })
        # 旧データ相当: scene 帳から同期済みの研修 entry（アシスト未反映）を直接持たせる
        person["months"]["2026-04"]["entries_per_day"]["10"] = [{
            "id": "sync_legacy", "value": "!A!千葉養護", "second_option": "TRAIN",
            "comment": "", "employee_number": "", "site_name": "千葉養護",
            "sync_source_type": module.SHIFT_SYNC_SCENE_SOURCE,
            "sync_source_project_id": "legacy-scene", "sync_source_month_key": "2026-04",
            "sync_source_day": "10", "sync_source_entry_id": "legacy-entry",
        }]
        module._save_project(person)

    # entry を変更しない保存（同期 entry はサーバー側で保持される）
    _save_month_entries(module, app, client, person_id, {})

    assist = _person_assist_payload(client, person_id)
    assert {(s["site_name"], s["shift_key"]) for s in assist["experienced_sites"]} == {("千葉養護", "TRAIN")}
    assert all(s["source_label"] for s in assist["experienced_sites"])
    # 研修要現場からも削除されている
    assert all(t["id"] != "t1" for t in assist["training_sites"])


def test_scene_save_without_entry_changes_still_registers_role_options():
    """entry に変更のない保存でも、月全体を読み直して代務/研修を反映する（scene 帳）。"""
    module, app = _build()
    client, project_id = _create_scene_project(module, app)
    module.current_user = _owner()
    # 保存フックを通さずに entry を直接持たせる（過去の反映漏れ相当）
    with app.app_context():
        project = module._load_project(project_id)
        project["months"]["2026-04"]["entries_per_day"]["1"] = [
            {"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""},
        ]
        module._save_project(project)

    # 同一内容の保存（変更なし）
    _save_month_entries(module, app, client, project_id, {
        "1": [{"id": "e1", "value": "!SUB!田中", "employee_number": "E101", "comment": ""}],
    })
    autos, _ = _auto_records(module, app, project_id)
    assert [(r["employee_number"], r["shift_key"]) for r in autos] == [("E101", "SUB")]


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
