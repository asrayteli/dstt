import sys
import json
import io
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
    UserAccessibleOffice,
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
    # ToBellフックは本物の app.tools.health_check.get_office_health_officers を呼ぶため、
    # 同じテスト用データ領域を参照するよう本物モジュール側も合わせて差し替える。
    import app.tools.health_check as real_hc
    real_hc.get_data_path = lambda: str(data_dir)
    real_hc.get_uploads_path = lambda: str(data_dir / "uploads")
    return module, app


def _grant_office(app, username: str, code: str = "100", name: str = "本社営業所"):
    """通知の宛先になれるよう、DSTTのアクセス権（営業所）をユーザーに付与する。

    健診PLUSの通知先は「その営業所にアクセスできる人」に限られる。
    以前は操作者がDSTT管理者なら宛先の営業所を検証していなかったため、
    アクセス権の無いユーザーでも宛先にできてしまっていた。
    """
    with app.app_context():
        office = _get_or_create_access_office(code, name)
        user = User.query.filter_by(username=username).first()
        if user is not None and user.office_id is None:
            user.office_id = office.id
        db.session.commit()


def _get_or_create_access_office(code: str, name: str) -> AccessOffice:
    """アクセス権営業所を取得または作成する（app_context 内で呼ぶこと）。

    AccessBranch.name / .code と AccessOffice.code はいずれも UNIQUE のため、
    テストごとに作り直すと衝突する。必ずこの関数を通して使い回す。
    """
    office = AccessOffice.query.filter_by(code=code).first()
    if office is not None:
        return office
    branch = AccessBranch.query.filter_by(code="B1").first()
    if branch is None:
        branch = AccessBranch.query.first()
    if branch is None:
        branch = AccessBranch(code="B1", name="本社支店")
        db.session.add(branch)
        db.session.flush()
    office = AccessOffice.query.filter_by(branch_id=branch.id, name=name).first()
    if office is None:
        office = AccessOffice(code=code, name=name, branch_id=branch.id)
        db.session.add(office)
        db.session.flush()
    return office


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
    _grant_office(app, "m001")


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
    _grant_office(app, "m999")

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


def test_attachment_title_set_and_rename(tmp_path):
    """添付ファイルにタイトル（選択肢/手動）を付与でき、後から変更できる。"""
    import io
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(Employee(
            employee_number="E100", office_code="100", office_name="本社営業所",
            employee_name="健診七子", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", birth_date=date(1980, 1, 1),
        ))
        db.session.commit()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E100",
    }).get_json()["record"]["id"]

    # アップロード時にタイトルを付与
    res = client.post(
        f"/tools/health_check/api/record/{rid}/attachment",
        data={"file": (io.BytesIO(b"%PDF-1.4 test"), "result.pdf"),
              "category": "health", "title": "健康診断結果"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    att = res.get_json()["attachment"]
    assert att["title"] == "健康診断結果"
    aid = att["id"]

    # PATCH でタイトルを変更
    res = client.patch(
        f"/tools/health_check/api/record/{rid}/attachment/{aid}",
        json={"title": "二次検査結果"},
    )
    assert res.status_code == 200
    assert res.get_json()["attachment"]["title"] == "二次検査結果"

    # 空文字でクリアできる（ファイル名表示に戻る）
    res = client.patch(
        f"/tools/health_check/api/record/{rid}/attachment/{aid}",
        json={"title": "   "},
    )
    assert res.status_code == 200
    assert res.get_json()["attachment"]["title"] == ""

    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert any(a["id"] == aid for a in rec["attachments"])


def test_attachment_inline_preview_disposition(tmp_path):
    """?inline=1 はプレビュー用にインライン配信、既定はダウンロード（attachment）。"""
    import io
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "添付子", "office_code": "100"}).get_json()["record"]["id"]
    up = client.post(
        f"/tools/health_check/api/record/{rid}/attachment",
        data={"file": (io.BytesIO(b"%PDF-1.4 test"), "result.pdf"), "category": "health"},
        content_type="multipart/form-data",
    )
    aid = up.get_json()["attachment"]["id"]

    # 既定はダウンロード（attachment）
    dl = client.get(f"/tools/health_check/api/record/{rid}/attachment/{aid}")
    assert dl.status_code == 200
    assert "attachment" in dl.headers.get("Content-Disposition", "")

    # inline=1 はインライン配信（プレビュー用）
    pv = client.get(f"/tools/health_check/api/record/{rid}/attachment/{aid}?inline=1")
    assert pv.status_code == 200
    assert "attachment" not in pv.headers.get("Content-Disposition", "inline")


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
        off100 = _get_or_create_access_office("100", "本社営業所")
        off200 = _get_or_create_access_office("200", "別営業所")
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


def test_bulk_create_excludes_retired_employees(tmp_path):
    """退職者（retirement_date あり）は一括起票の既定対象から外す。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        db.session.add(Employee(
            employee_number="E777", office_code="100", office_name="本社営業所",
            employee_name="退職済子", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", retirement_date="2025-03-31",
        ))
        db.session.commit()
    client = app.test_client()

    res = client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    assert res.get_json()["created"] == 2  # 在籍2名のみ。退職者は除外される
    listing = client.get("/tools/health_check/api/records?year=2026").get_json()
    names = {r["employee_name"] for r in listing["records"]}
    assert "退職済子" not in names


def test_employees_candidate_includes_birth_date(tmp_path):
    """名簿候補APIは生年月日を返す（新規登録時のNASVA判定に使う）。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        emp = Employee.query.filter_by(employee_number="E001").first()
        emp.birth_date = date(1958, 5, 1)
        db.session.commit()
    client = app.test_client()
    data = client.get("/tools/health_check/api/employees?search=E001").get_json()
    target = [e for e in data["employees"] if e["employee_number"] == "E001"]
    assert target and target[0]["birth_date"] == "1958-05-01"


def test_notify_kind_off_suppresses_that_reminder(tmp_path):
    """通知種別を個別にOFFにすると、その種別のリマインドは起票されない。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        # 担当者 m001 はオプトイン済みだが、二次検査の通知だけOFF
        db.session.add(ToBellUserSettings(
            username="m001",
            integrations={"health_check.linkage": True},
            preferences={"health_check_notify": {"secondary_exam": False}},
        ))
        db.session.commit()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"})
    rid = res.get_json()["record"]["id"]

    today = date.today().isoformat()
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": today})
    with app.app_context():
        # 二次検査の通知はOFFなので起票されない
        task = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").first()
        assert task is None


def test_integration_api_saves_notify_kinds(tmp_path):
    """連携APIで通知種別の個別ON/OFFを取得・保存できる。既定は全種別ON。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()

    got = client.get("/tools/health_check/api/integration").get_json()
    assert got["kinds"] == {"reservation": True, "night_second": True, "secondary_exam": True}

    res = client.post("/tools/health_check/api/integration", json={
        "enabled": True, "kinds": {"reservation": False}})
    assert res.get_json()["kinds"]["reservation"] is False

    got2 = client.get("/tools/health_check/api/integration").get_json()
    assert got2["enabled"] is True
    assert got2["kinds"]["reservation"] is False
    assert got2["kinds"]["secondary_exam"] is True  # 指定しない種別はONのまま


def test_recheck_items_structured_storage(tmp_path):
    """再検査項目は [{name, value}] の構造で保存・取得でき、CSVには整形して出る。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "再検太郎", "office_code": "100"})
    rid = res.get_json()["record"]["id"]

    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True,
        "recheck_items": [
            {"name": "血圧", "value": "要再検査"},
            {"name": "肝機能", "value": ""},
            {"name": "", "value": ""},  # 空項目は捨てられる
        ],
    })
    rec = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert rec["recheck_items_list"] == [
        {"name": "血圧", "value": "要再検査"},
        {"name": "肝機能", "value": ""},
    ]

    # CSVには「項目：内容 / 項目」の形で整形される
    csv_body = client.get("/tools/health_check/api/export?year=2026").data.decode("utf-8-sig")
    assert "血圧：要再検査 / 肝機能" in csv_body


def test_recheck_items_legacy_plain_text_compat(tmp_path):
    """旧仕様のプレーンテキストの再検査項目も1項目として読める。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "旧子", "office_code": "100"})
    rid = res.get_json()["record"]["id"]
    with app.app_context():
        rec = db.session.get(HealthCheckRecord, rid)
        rec.recheck_items = "尿検査の再検査"  # 旧プレーンテキスト
        db.session.commit()
    got = client.get(f"/tools/health_check/api/record/{rid}").get_json()
    assert got["recheck_items_list"] == [{"name": "尿検査の再検査", "value": ""}]


def test_retired_hidden_by_default(tmp_path):
    """退職者フラグのあるレコードは既定で非表示、include_retired=true で表示。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    recs = client.get("/tools/health_check/api/records?year=2026").get_json()["records"]
    rid = recs[0]["id"]
    with app.app_context():
        rec = db.session.get(HealthCheckRecord, rid)
        rec.is_retired = True
        db.session.commit()

    default = client.get("/tools/health_check/api/records?year=2026").get_json()
    assert default["count"] == 1  # 退職者は隠れる
    assert all(not r["is_retired"] for r in default["records"])

    with_retired = client.get("/tools/health_check/api/records?year=2026&include_retired=true").get_json()
    assert with_retired["count"] == 2


def test_retired_synced_from_employee(tmp_path):
    """名簿PLUSの退職者フラグ(is_retired)が健診レコードへ同期される。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    with app.app_context():
        emp = Employee.query.filter_by(employee_number="E001").first()
        emp.is_retired = True
        db.session.commit()
    client = app.test_client()
    rec = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"}).get_json()["record"]
    assert rec["is_retired"] is True


def test_exempt_excluded_from_dashboard_counts(tmp_path):
    """受診非対象者はヒーローエリアの各カウントから除外される。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})
    recs = client.get("/tools/health_check/api/records?year=2026").get_json()["records"]
    client.put(f"/tools/health_check/api/record/{recs[0]['id']}", json={"is_exempt": True})

    dash = client.get("/tools/health_check/api/dashboard?year=2026").get_json()
    assert dash["total"] == 1   # 2名のうち1名は非対象で除外
    assert dash["exempt"] == 1
    # 非対象者も一覧には表示される
    listing = client.get("/tools/health_check/api/records?year=2026").get_json()
    assert listing["count"] == 2


def test_kintone_flag_roundtrip(tmp_path):
    """kintoneフラグをレコードごとに設定・取得できる。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "キン子", "office_code": "100", "is_kintone": True})
    rid = res.get_json()["record"]["id"]
    assert res.get_json()["record"]["is_kintone"] is True
    client.put(f"/tools/health_check/api/record/{rid}", json={"is_kintone": False})
    assert client.get(f"/tools/health_check/api/record/{rid}").get_json()["is_kintone"] is False


def test_health_officer_is_default_notify_recipient(tmp_path):
    """営業所の健康診断担当も既定の通知先になり、オプトインしていれば起票される。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        # 健康診断担当 hofficer と、管理担当 m001 の双方がオプトイン
        db.session.add(User(username="hofficer", password_hash="x", name="健診担当子"))
        db.session.add(ToBellUserSettings(username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.add(ToBellUserSettings(username="hofficer", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()
    _grant_office(app, "hofficer")

    # 営業所100の健康診断担当として hofficer を設定
    res = client.post("/tools/health_check/api/admin/health_officers",
                      json={"office_code": "100", "users": ["hofficer"]})
    assert res.status_code == 200

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"}).get_json()["record"]["id"]
    today = date.today().isoformat()
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": today})

    with app.app_context():
        tasks = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").all()
        assignees = {t.assigned_to for t in tasks}
        assert assignees == {"m001", "hofficer"}  # 管理担当＋健康診断担当の二人に届く


def test_extra_notify_user_receives_reminder(tmp_path):
    """レコード個別の追加通知先も、オプトインしていれば通知される。"""
    module, app = _build(tmp_path)
    _seed_basic(app)
    client = app.test_client()
    with app.app_context():
        db.session.add(User(username="extra01", password_hash="x", name="追加宛先子"))
        db.session.add(ToBellUserSettings(username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.add(ToBellUserSettings(username="extra01", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()
    _grant_office(app, "extra01")

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001"}).get_json()["record"]["id"]
    today = date.today().isoformat()
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": today,
        "extra_notify_users": ["extra01"]})

    with app.app_context():
        tasks = ToBellTask.query.filter_by(source_tool="health_check", source_ref_type="secondary_exam").all()
        assert {t.assigned_to for t in tasks} == {"m001", "extra01"}

    # 追加宛先を外すと、その人のリマインドはクローズされる
    client.put(f"/tools/health_check/api/record/{rid}", json={"extra_notify_users": []})
    with app.app_context():
        active = ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type="secondary_exam", status="todo").all()
        assert {t.assigned_to for t in active} == {"m001"}


def _setup_two_offices(app):
    """営業所100/200 と DSTTアクセス権(AccessOffice)を用意し、社員を1名ずつ登録する。"""
    with app.app_context():
        db.session.add(Office(office_code="100", office_name="本社", created_by="seed"))
        db.session.add(Office(office_code="200", office_name="第二", created_by="seed"))
        branch = AccessBranch(name="支店", code="B1")
        db.session.add(branch)
        db.session.flush()
        off100 = AccessOffice(branch_id=branch.id, name="本社", code="100")
        off200 = AccessOffice(branch_id=branch.id, name="第二", code="200")
        db.session.add_all([off100, off200])
        db.session.flush()
        db.session.add(Employee(employee_number="E1", office_code="100", office_name="本社",
                                employee_name="社員100", company_name="大新東"))
        db.session.add(Employee(employee_number="E2", office_code="200", office_name="第二",
                                employee_name="社員200", company_name="大新東"))
        u1 = User(username="u1", password_hash="x", name="一般太郎", office_id=off100.id)
        db.session.add(u1)
        db.session.commit()
        return u1.id, off100.id


def _as_user(module, *, username, uid, office_id, admin=False):
    module._is_dstt_admin = lambda *a, **k: admin
    module.current_user = SimpleNamespace(
        is_authenticated=True, username=username, name=username,
        id=uid, office_id=office_id, branch_id=None, department_id=None, is_admin=admin,
    )


def test_access_office_without_records_is_available_for_manual_create(tmp_path):
    module, app = _build(tmp_path, admin=False)
    with app.app_context():
        branch = AccessBranch(name="Branch", code="B300")
        db.session.add(branch)
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name="Empty Office", code="300")
        db.session.add(office)
        db.session.flush()
        user = User(username="empty-user", password_hash="x", name="Empty User", office_id=office.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id
        office_id = office.id

    _as_user(module, username="empty-user", uid=uid, office_id=office_id, admin=False)
    client = app.test_client()

    page = client.get("/tools/health_check/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '"code": "300"' in html
    assert "Empty Office" in html

    dashboard = client.get("/tools/health_check/api/dashboard?year=2026")
    assert dashboard.status_code == 200
    assert dashboard.get_json()["total"] == 0
    listing = client.get("/tools/health_check/api/records?year=2026")
    assert listing.status_code == 200
    assert listing.get_json()["count"] == 0

    created = client.post("/tools/health_check/api/record", json={
        "target_year": 2026,
        "record_type": "pre_hire",
        "employee_name": "Pre Hire",
        "office_code": "300",
    })
    assert created.status_code == 200
    assert created.get_json()["record"]["office_code"] == "300"


def test_multiple_access_offices_render_hero_scope_switcher(tmp_path):
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        off200_id = AccessOffice.query.filter_by(code="200").first().id
        db.session.add(UserAccessibleOffice(user_id=uid, office_id=off200_id))
        db.session.commit()

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    page = client.get("/tools/health_check/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="hero-office-button"' in html
    assert 'id="office-scope-modal"' in html
    assert "openOfficeScopeModal" in html
    assert '"code": "100"' in html
    assert '"code": "200"' in html


def test_office_scope_synced_with_dstt_access(tmp_path):
    """非管理者は DSTT で付与された営業所のレコードのみ閲覧・作成でき、他営業所は403。"""
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    # 自分の営業所(100)の社員はレコード作成可
    r100 = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E1"})
    assert r100.status_code == 200
    # 他営業所(200)の社員は作成不可（403）
    r200 = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2"})
    assert r200.status_code == 403

    listing = client.get("/tools/health_check/api/records?year=2026").get_json()
    assert {x["office_code"] for x in listing["records"]} == {"100"}


def test_health_admin_scoped_to_own_office(tmp_path):
    """健診PLUS管理者は『自分の営業所』のみ管理でき、他営業所にはアクセスできない。"""
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    # u1 を健診PLUS管理者に指定
    module.save_permissions({"admins": ["u1"]})
    client = app.test_client()

    # 健康診断担当の管理画面は自分の営業所(100)のみ
    officers = client.get("/tools/health_check/api/admin/health_officers").get_json()
    assert {o["code"] for o in officers["offices"]} == {"100"}

    # 自営業所(100)の健康診断担当は設定できる
    ok = client.post("/tools/health_check/api/admin/health_officers",
                     json={"office_code": "100", "users": ["u1"]})
    assert ok.status_code == 200
    # 他営業所(200)の健康診断担当は設定できない（403）
    ng = client.post("/tools/health_check/api/admin/health_officers",
                     json={"office_code": "200", "users": ["u1"]})
    assert ng.status_code == 403

    # 健診PLUS管理者でも他営業所(200)のレコードにはアクセスできない
    bad = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2"})
    assert bad.status_code == 403

    # 健診PLUS管理者の管理（指定/解除）はDSTT管理者のみ → u1は不可
    assert client.get("/tools/health_check/api/admin/permissions").status_code == 403
    assert client.post("/tools/health_check/api/admin/grant", json={"user_id": "x"}).status_code == 403


def test_dstt_admin_full_access_all_offices(tmp_path):
    """DSTT管理者は全営業所を閲覧・編集でき、管理者指定もできる。"""
    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100", "200"]})
    listing = client.get("/tools/health_check/api/records?year=2026").get_json()
    assert {x["office_code"] for x in listing["records"]} == {"100", "200"}

    # 全営業所の健康診断担当を管理できる
    officers = client.get("/tools/health_check/api/admin/health_officers").get_json()
    assert {o["code"] for o in officers["offices"]} == {"100", "200"}

    # 健診PLUS管理者の指定はDSTT管理者のみ可能
    g = client.post("/tools/health_check/api/admin/grant", json={"user_id": "u1"})
    assert g.status_code == 200
    perms = client.get("/tools/health_check/api/admin/permissions").get_json()
    assert any(u["user_id"] == "u1" for u in perms["users"])


def test_extra_notify_and_manager_are_limited_to_record_office(tmp_path):
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        off200 = AccessOffice.query.filter_by(code="200").first()
        db.session.add(User(username="u2", password_hash="x", name="User 2", office_id=off200.id))
        db.session.commit()

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()
    created = client.post("/tools/health_check/api/record", json={
        "target_year": 2026,
        "record_type": "internal",
        "employee_name": "Internal",
        "office_code": "100",
        "extra_notify_users": ["u1", "u2"],
    })
    assert created.status_code == 200
    record = created.get_json()["record"]
    assert record["extra_notify_users"] == ["u1"]

    bad_manager = client.put(
        f"/tools/health_check/api/record/{record['id']}/manager",
        json={"manager_user": "u2"},
    )
    assert bad_manager.status_code == 400


def test_linked_record_scope_uses_current_employee_office(tmp_path):
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        off200 = AccessOffice.query.filter_by(code="200").first()
        u2 = User(username="u2", password_hash="x", name="User 2", office_id=off200.id)
        db.session.add(u2)
        db.session.commit()
        u2_id = u2.id
        off200_id = off200.id
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    created = client.post("/tools/health_check/api/record", json={
        "target_year": 2026,
        "record_type": "linked",
        "employee_number": "E1",
    })
    assert created.status_code == 200
    record_id = created.get_json()["record"]["id"]
    upload = client.post(
        f"/tools/health_check/api/record/{record_id}/attachment",
        data={"file": (io.BytesIO(b"fake-png"), "check.png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    attachment_id = upload.get_json()["attachment"]["id"]

    with app.app_context():
        emp = Employee.query.filter_by(employee_number="E1").first()
        emp.office_code = "200"
        emp.office_name = "Office 200"
        db.session.commit()

    listing = client.get("/tools/health_check/api/records?year=2026")
    assert listing.status_code == 200
    assert listing.get_json()["records"] == []

    updated = client.put(f"/tools/health_check/api/record/{record_id}", json={"remarks": "old office edit"})
    assert updated.status_code == 403
    download_old = client.get(f"/tools/health_check/api/record/{record_id}/attachment/{attachment_id}")
    assert download_old.status_code == 403
    history_old = client.get(f"/tools/health_check/api/history?record_id={record_id}")
    assert history_old.status_code == 403

    _as_user(module, username="u2", uid=u2_id, office_id=off200_id, admin=False)
    download_new = client.get(f"/tools/health_check/api/record/{record_id}/attachment/{attachment_id}")
    assert download_new.status_code == 200
    history_new = client.get(f"/tools/health_check/api/history?record_id={record_id}")
    assert history_new.status_code == 200


def test_linked_record_list_syncs_all_records_and_refilters_notify_users(tmp_path):
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        off100 = AccessOffice.query.filter_by(code="100").first()
        off200 = AccessOffice.query.filter_by(code="200").first()
        db.session.add(UserAccessibleOffice(user_id=uid, office_id=off200.id))
        db.session.add(User(username="manager100", password_hash="x", name="Manager 100", office_id=off100.id))
        db.session.add(User(username="manager200", password_hash="x", name="Manager 200", office_id=off200.id))
        db.session.commit()

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    for employee_number in ("E1", "E2"):
        created = client.post("/tools/health_check/api/record", json={
            "target_year": 2026,
            "record_type": "linked",
            "employee_number": employee_number,
        })
        assert created.status_code == 200

    with app.app_context():
        for number, name in (("E1", "Moved 1"), ("E2", "Moved 2")):
            emp = Employee.query.filter_by(employee_number=number).first()
            emp.office_code = "100"
            emp.office_name = "Office 100"
            emp.employee_name = name
        HealthCheckRecord.query.filter_by(employee_number="E1").update({
            "manager_user": "manager200",
            "extra_notify_users": json.dumps(["manager100", "manager200"], ensure_ascii=False),
        })
        HealthCheckRecord.query.filter_by(employee_number="E2").update({"employee_name": "stale"})
        db.session.commit()

    listing = client.get("/tools/health_check/api/records?year=2026")
    assert listing.status_code == 200
    records = sorted(listing.get_json()["records"], key=lambda row: row["employee_number"])
    assert [row["employee_name"] for row in records] == ["Moved 1", "Moved 2"]
    assert records[0]["manager_user"] in (None, "")
    assert records[0]["extra_notify_users"] == ["manager100"]
