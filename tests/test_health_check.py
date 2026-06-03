import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from datetime import date, datetime

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (
    db,
    Office,
    Employee,
    User,
    HealthCheckRecord,
    ToBellTask,
    ToBellUserSettings,
)


def _load_module():
    module_path = ROOT / "app" / "tools" / "health_check.py"
    spec = importlib.util.spec_from_file_location("health_check_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build(tmp_path, *, admin=True):
    module = _load_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'hc.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.health_check_bp)
    with app.app_context():
        db.create_all()

    # データ領域・管理者判定をテスト用に差し替え（実静的領域を汚さない）
    data_dir = tmp_path / "hc_data"
    data_dir.mkdir(exist_ok=True)
    module.get_data_path = lambda: str(data_dir)
    module.get_uploads_path = lambda: str(data_dir / "uploads")
    module._is_dstt_admin = lambda *a, **k: admin
    module.get_global_lead_days = lambda: 3
    module.current_user = SimpleNamespace(is_authenticated=True, username="tester01", name="検査太郎")
    return module, app


def _seed_basic(app):
    with app.app_context():
        db.session.add(Office(office_code="100", office_name="本社営業所", created_by="seed"))
        db.session.add(User(username="m001", password_hash="x", name="管理花子"))
        db.session.add(Employee(
            employee_number="E001", office_code="100", office_name="本社営業所",
            employee_name="社員一郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", hire_date=date(2020, 4, 1),
        ))
        db.session.add(Employee(
            employee_number="E002", office_code="100", office_name="本社営業所",
            employee_name="社員二郎", employee_type="嘱託", company_name="大新東",
            manager_name="管理花子",
        ))
        db.session.commit()


def test_bulk_create_and_dashboard(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    assert res.status_code == 200
    assert res.get_json()["created"] == 2

    # 二重起票はスキップ
    res2 = client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    assert res2.get_json()["created"] == 0

    listing = client.get("/tools/health_check/api/records?year=2026").get_json()
    assert listing["count"] == 2
    # 管理担当名から DSTTユーザーへ自動紐付け
    assert all(r["manager_user"] == "m001" for r in listing["records"])

    dash = client.get("/tools/health_check/api/dashboard?year=2026").get_json()
    assert dash["total"] == 2
    assert dash["examined"] == 0
    assert dash["unassigned"] == 0


def test_status_transitions_and_manual_record(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    # 手動（内勤者）レコード作成
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤三郎", "office_code": "100",
    })
    assert res.status_code == 200
    rid = res.get_json()["record"]["id"]
    assert res.get_json()["record"]["status"] == "未予約"

    # 予約 → 受診
    client.put(f"/tools/health_check/api/record/{rid}", json={"reservation_date": "2026-05-01"})
    client.put(f"/tools/health_check/api/record/{rid}", json={"exam_date": "2026-05-10"})
    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["status"] == "受診済"

    # 再検査 → 二次案内 → 二次完了
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": "2026-06-01"})
    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["status"] == "再検査対象"
    client.put(f"/tools/health_check/api/record/{rid}", json={"secondary_guide_sent_date": "2026-06-02"})
    assert client.get(f"/tools/health_check/api/record/{rid}").get_json()["status"] == "二次案内済"
    client.put(f"/tools/health_check/api/record/{rid}", json={"secondary_exam_date": "2026-06-20"})
    assert client.get(f"/tools/health_check/api/record/{rid}").get_json()["status"] == "二次完了"


def test_secondary_reminder_creates_tobell_task_when_opted_in(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    with app.app_context():
        # 担当者 m001 が連携をオプトイン
        db.session.add(ToBellUserSettings(username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()

    # 名簿連携レコードを作成（manager_user は自動で m001）
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001",
    })
    rid = res.get_json()["record"]["id"]

    # 推奨日が無い段階ではタスク無し
    with app.app_context():
        assert ToBellTask.query.filter_by(source_tool="health_check").count() == 0

    # 再検査＋推奨日を入力 → 二次検査リマインドが起票される
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": "2026-09-15"})
    with app.app_context():
        task = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").first()
        assert task is not None
        assert task.assigned_to == "m001"
        assert task.due_at is not None
        assert task.due_at.hour == 9  # 当日朝9時に通知が飛ぶ時刻設定

    # 受診完了でリマインドはアーカイブされる
    client.put(f"/tools/health_check/api/record/{rid}", json={"secondary_exam_date": "2026-09-20"})
    with app.app_context():
        task = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").first()
        assert task.status == "archived"


def test_linked_record_keeps_synced_fields(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"})
    rid = res.get_json()["record"]["id"]
    assert res.get_json()["record"]["employee_name"] == "社員一郎"

    # 名簿同期項目を空で送っても氏名・専従先は保持される（受診日だけ更新）
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "employee_name": "", "assignment_site": "", "exam_date": "2026-05-10"})
    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["employee_name"] == "社員一郎"
    assert rec["assignment_site"] == "大新東"
    assert rec["exam_date"] == "2026-05-10"


def test_manual_manager_override(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(User(username="m999", password_hash="x", name="別担当"))
        db.session.commit()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal", "employee_name": "内勤四郎", "office_code": "100"})
    rid = res.get_json()["record"]["id"]

    res2 = client.put(f"/tools/health_check/api/record/{rid}/manager", json={"manager_user": "m999"})
    assert res2.status_code == 200
    assert res2.get_json()["record"]["manager_user"] == "m999"

    # 存在しないユーザーは拒否
    bad = client.put(f"/tools/health_check/api/record/{rid}/manager", json={"manager_user": "nope"})
    assert bad.status_code == 400


def test_carryover_clears_exam_dates(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2025, "offices": ["100"]})
    # 2025の1件に受診日を設定
    rec = client.get("/tools/health_check/api/records?year=2025").get_json()["records"][0]
    client.put(f"/tools/health_check/api/record/{rec['id']}", json={"exam_date": "2025-05-01", "is_night_worker": True})

    res = client.post("/tools/health_check/api/carryover", json={"from_year": 2025, "to_year": 2026})
    assert res.get_json()["created"] == 2
    new_records = client.get("/tools/health_check/api/records?year=2026").get_json()["records"]
    # 固定情報（深夜区分）は引き継ぎ、受診日はクリア
    night = [r for r in new_records if r["is_night_worker"]]
    assert len(night) == 1
    assert all(r["exam_date"] is None for r in new_records)


def test_export_honors_status_filter(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    recs = client.get("/tools/health_check/api/records?year=2026").get_json()["records"]
    client.put(f"/tools/health_check/api/record/{recs[0]['id']}", json={"exam_date": "2026-05-01"})

    # 全件
    full = client.get("/tools/health_check/api/export?year=2026")
    assert full.status_code == 200
    assert full.headers["Content-Type"].startswith("text/csv")
    body_full = full.data.decode("utf-8-sig").strip().splitlines()
    assert len(body_full) == 3  # ヘッダ + 2件

    # 受診済のみ
    filtered = client.get("/tools/health_check/api/export?year=2026&status=受診済")
    body = filtered.data.decode("utf-8-sig").strip().splitlines()
    assert len(body) == 2  # ヘッダ + 1件


def test_dashboard_scoped_to_office(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        db.session.add(Office(office_code="200", office_name="第二営業所", created_by="seed"))
        db.session.add(Employee(
            employee_number="E900", office_code="200", office_name="第二営業所",
            employee_name="別社員", employee_type="正社員", company_name="大新東"))
        db.session.commit()
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100", "200"]})

    all_dash = client.get("/tools/health_check/api/dashboard?year=2026").get_json()
    assert all_dash["total"] == 3
    one = client.get("/tools/health_check/api/dashboard?year=2026&office=200").get_json()
    assert one["total"] == 1


def test_history_records_changes(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal", "employee_name": "履歴太郎", "office_code": "100"})
    rid = res.get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={"exam_date": "2026-05-10"})

    hist = client.get(f"/tools/health_check/api/history?record_id={rid}").get_json()["histories"]
    actions = {h["action"] for h in hist}
    assert "create" in actions and "update" in actions
    exam_change = [h for h in hist if h["field_name"] == "exam_date"]
    assert exam_change and exam_change[0]["new_value"] == "2026-05-10"


def test_fiscal_year_helper(tmp_path):
    module, app = _build(tmp_path)
    assert module.current_fiscal_year(date(2026, 3, 31)) == 2025
    assert module.current_fiscal_year(date(2026, 4, 1)) == 2026
    assert module.current_fiscal_year(date(2026, 12, 31)) == 2026


def test_name_normalization_matches_across_width_and_space(tmp_path):
    module, app = _build(tmp_path)
    with app.app_context():
        db.session.add(User(username="u1", password_hash="x", name="山田 太郎"))
        db.session.commit()
        # 全角スペース・余分な空白を含む名前でも一意解決できる
        assert module.resolve_manager_user("山田　太郎") == "u1"
        assert module.resolve_manager_user(" 山田太郎 ") == "u1"
