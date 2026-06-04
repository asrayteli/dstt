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
    Site,
    User,
    AccessBranch,
    AccessOffice,
    AccessDepartment,
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

    # 再検査＋推奨日（前日に達した日付）を入力 → 二次検査リマインドが起票される
    today = date.today().isoformat()
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": today})
    with app.app_context():
        task = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").first()
        assert task is not None
        assert task.assigned_to == "m001"
        assert task.due_at is not None
        assert task.due_at.hour == 9  # 当日朝9時にアラート
        # タイトル・本文は「{氏名}さんの{ジャンル}になりました。」
        assert task.title == "社員一郎さんの二次検査受診推奨日になりました。"
        assert task.description == "社員一郎さんの二次検査受診推奨日になりました。"

    # 受診完了でリマインドはアーカイブされる
    client.put(f"/tools/health_check/api/record/{rid}", json={"secondary_exam_date": "2026-09-20"})
    with app.app_context():
        task = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").first()
        assert task.status == "archived"


def test_reminders_materialize_day_before_at_nine(tmp_path):
    """予約日・受診日②・二次検査推奨日は前日にタスク化し、当日9時にアラート。
    本文は「{氏名}さんの{ジャンル}になりました。」。"""
    from datetime import datetime as _dt
    from app.services.to_bell_hooks import ensure_health_check_reminders

    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(ToBellUserSettings(username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"})
    rid = res.get_json()["record"]["id"]

    def _task(ref_type):
        return ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type=ref_type, status="todo"
        ).first()

    with app.app_context():
        rec = db.session.get(HealthCheckRecord, rid)
        rec.reservation_date = date(2026, 5, 20)
        db.session.commit()

        # 2日前：まだタスク化されない
        ensure_health_check_reminders(rec, now=_dt(2026, 5, 18, 10, 0), commit=True)
        assert _task("reservation") is None

        # 前日：タスク化され、due_at は当日9:00
        ensure_health_check_reminders(rec, now=_dt(2026, 5, 19, 6, 0), commit=True)
        task = _task("reservation")
        assert task is not None
        assert task.due_at == _dt(2026, 5, 20, 9, 0)
        assert task.assigned_to == "m001"
        assert task.title == "社員一郎さんの健康診断予約日になりました。"

        # 受診日が入ると予約日リマインドはクローズ
        rec.exam_date = date(2026, 5, 20)
        db.session.commit()
        ensure_health_check_reminders(rec, now=_dt(2026, 5, 20, 9, 0), commit=True)
        assert _task("reservation") is None


def test_night_second_reminder_message(tmp_path):
    """深夜従事者の受診日②リマインドの本文ジャンルは「受診日②」。"""
    from datetime import datetime as _dt
    from app.services.to_bell_hooks import ensure_health_check_reminders

    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(ToBellUserSettings(username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"})
    rid = res.get_json()["record"]["id"]

    with app.app_context():
        rec = db.session.get(HealthCheckRecord, rid)
        rec.is_night_worker = True
        rec.exam_date_2_target = date(2026, 11, 10)
        db.session.commit()
        ensure_health_check_reminders(rec, now=_dt(2026, 11, 9, 8, 0), commit=True)
        task = ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type="night_second", status="todo").first()
        assert task is not None
        assert task.title == "社員一郎さんの受診日②になりました。"
        assert task.due_at == _dt(2026, 11, 10, 9, 0)


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


def test_nasva_eligibility_by_age_at_fiscal_start(tmp_path):
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    with app.app_context():
        # 2026年度4/1時点で65歳（=64歳超）になる社員
        db.session.add(Employee(
            employee_number="E064", office_code="100", office_name="本社営業所",
            employee_name="高齢四郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", birth_date=date(1961, 4, 1),
        ))
        # 2026年度4/1時点で64歳ちょうど（NASVA対象外）
        db.session.add(Employee(
            employee_number="E063", office_code="100", office_name="本社営業所",
            employee_name="非対象五郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", birth_date=date(1962, 4, 1),
        ))
        db.session.commit()

    over = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E064",
    }).get_json()["record"]
    under = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E063",
    }).get_json()["record"]

    assert over["is_nasva_target"] is True
    assert over["age_at_fiscal_start"] == 65
    assert under["is_nasva_target"] is False
    assert under["age_at_fiscal_start"] == 64

    # NASVA日付が保存できること
    rid = over["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "nasva_reservation_date": "2026-05-10", "nasva_exam_date": "2026-05-20"})
    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["nasva_reservation_date"] == "2026-05-10"
    assert rec["nasva_exam_date"] == "2026-05-20"


def test_nasva_attachment_category(tmp_path):
    import io
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(Employee(
            employee_number="E099", office_code="100", office_name="本社営業所",
            employee_name="運転六郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", birth_date=date(1955, 1, 1),
        ))
        db.session.commit()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E099",
    }).get_json()["record"]["id"]

    res = client.post(
        f"/tools/health_check/api/record/{rid}/attachment",
        data={"file": (io.BytesIO(b"%PDF-1.4 test"), "nasva.pdf"), "category": "nasva"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.get_json()["attachment"]["category"] == "nasva"

    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert any(a["category"] == "nasva" for a in rec["attachments"])


def test_manual_record_saves_employee_type_and_birth_date(tmp_path):
    """手動モードでは社員区分（営業社員契約）と生年月日を入力・保存できる。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "pre_hire",
        "employee_name": "採用予定子", "office_code": "100",
        "employee_type": "営業社員（P契約）", "birth_date": "1990-08-15",
        "assignment_site": "現場リストの専従先", "manager_name": "エリア統括",
    })
    assert res.status_code == 200
    rid = res.get_json()["record"]["id"]

    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["employee_type"] == "営業社員（P契約）"
    assert rec["birth_date"] == "1990-08-15"
    assert rec["assignment_site"] == "現場リストの専従先"
    assert rec["manager_name"] == "エリア統括"


def test_area_managers_endpoint_filters_by_office_and_department(tmp_path):
    """管理担当候補は、対象営業所で担当が「エリアマネージャー」のDSTTユーザーのみ。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        db.session.add(Office(office_code="200", office_name="別営業所", created_by="seed"))
        branch = AccessBranch(name="本社支店", code="B1")
        db.session.add(branch)
        db.session.flush()
        off100 = AccessOffice(branch_id=branch.id, name="本社営業所", code="100")
        off200 = AccessOffice(branch_id=branch.id, name="別営業所", code="200")
        db.session.add_all([off100, off200])
        db.session.flush()
        am100 = AccessDepartment(office_id=off100.id, name="エリアマネージャー")
        other = AccessDepartment(office_id=off100.id, name="一般")
        am200 = AccessDepartment(office_id=off200.id, name="エリアマネージャー")
        db.session.add_all([am100, other, am200])
        db.session.flush()
        # 100のエリアマネージャー
        db.session.add(User(username="am1", password_hash="x", name="区域真理子",
                            office_id=off100.id, department_id=am100.id))
        # 100だがエリアマネージャーではない
        db.session.add(User(username="gen1", password_hash="x", name="一般花子",
                            office_id=off100.id, department_id=other.id))
        # 200のエリアマネージャー
        db.session.add(User(username="am2", password_hash="x", name="他所真理子",
                            office_id=off200.id, department_id=am200.id))
        db.session.commit()
    client = app.test_client()

    # 営業所100を指定 → 100のエリアマネージャーのみ
    data = client.get("/tools/health_check/api/area_managers?office=100").get_json()
    assert {m["username"] for m in data["managers"]} == {"am1"}
    # 営業所未指定（管理者は全営業所スコープ）→ 両営業所のエリアマネージャー
    data_all = client.get("/tools/health_check/api/area_managers").get_json()
    assert {m["username"] for m in data_all["managers"]} == {"am1", "am2"}


def test_sites_endpoint_searches_active_sites(tmp_path):
    """専従先名の候補は現場リストPLUS（Site）の有効な現場を検索して返す。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        db.session.add(Site(
            site_id="A0001", site_name="さくら配送センター",
            site_manager_last="現場", site_manager_first="太郎",
            site_manager_id="E001", site_register="seed", site_updater="seed",
            office_code="100", is_active=True,
        ))
        db.session.add(Site(
            site_id="A0002", site_name="休止中センター",
            site_manager_last="現場", site_manager_first="次郎",
            site_manager_id="E002", site_register="seed", site_updater="seed",
            office_code="100", is_active=False,
        ))
        db.session.commit()
    client = app.test_client()

    data = client.get("/tools/health_check/api/sites?search=さくら").get_json()
    names = {s["site_name"] for s in data["sites"]}
    assert "さくら配送センター" in names
    # 無効な現場は出ない
    data2 = client.get("/tools/health_check/api/sites?search=休止").get_json()
    assert data2["sites"] == []


def test_name_normalization_matches_across_width_and_space(tmp_path):
    module, app = _build(tmp_path)
    with app.app_context():
        db.session.add(User(username="u1", password_hash="x", name="山田 太郎"))
        db.session.commit()
        # 全角スペース・余分な空白を含む名前でも一意解決できる
        assert module.resolve_manager_user("山田　太郎") == "u1"
        assert module.resolve_manager_user(" 山田太郎 ") == "u1"
