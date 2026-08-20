"""健診PLUS 本人あてメール通知のテスト。

前提: 社員は DSTT のアカウントを持たない。したがって
  - メールはそれ単体で完結していること（DSTT への導線を書かない）
  - 送信可否は営業所ごとの管理者設定だけで決まること（本人のオプトインは無い）
"""
import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from datetime import date, datetime, time, timedelta

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (
    db, Office, Employee, User, AccessBranch, AccessOffice,
    HealthCheckRecord, MailMessage, ToBellUserSettings,
)
from app.services import mail_service


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "hc_mail_module", ROOT / "app" / "tools" / "health_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(tmp_path, *, admin=True):
    module = _load_module()
    app = Flask(__name__, root_path=str(ROOT / "app"), template_folder="templates",
                instance_path=str(tmp_path / "instance"))
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'hcm.db').as_posix()}"
    db.init_app(app)
    app.register_blueprint(module.health_check_bp)
    with app.app_context():
        db.create_all()
    data_dir = tmp_path / "hc_data"
    data_dir.mkdir(exist_ok=True)
    module.get_data_path = lambda: str(data_dir)
    module.get_uploads_path = lambda: str(data_dir / "uploads")
    module._is_dstt_admin = lambda *a, **k: admin
    module.get_global_lead_days = lambda: 3
    module.current_user = SimpleNamespace(is_authenticated=True, username="tester01", name="検査太郎")
    import app.tools.health_check as real_hc
    real_hc.get_data_path = lambda: str(data_dir)
    real_hc.get_uploads_path = lambda: str(data_dir / "uploads")
    return module, app


def _seed(app, *, email="shain@example.co.jp"):
    with app.app_context():
        branch = AccessBranch(code="B1", name="本社支店")
        db.session.add(branch); db.session.flush()
        office = AccessOffice(code="100", name="本社営業所", branch_id=branch.id)
        db.session.add(office); db.session.flush()
        db.session.add(Office(office_code="100", office_name="本社営業所", created_by="s"))
        db.session.add(User(username="m001", password_hash="x", name="管理花子",
                            office_id=office.id))
        db.session.add(Employee(
            employee_number="E001", office_code="100", office_name="本社営業所",
            employee_name="社員一郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", email=email,
        ))
        db.session.commit()


def _make_record(client, *, recheck_date=None):
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001",
    }).get_json()["record"]["id"]
    if recheck_date:
        client.put(f"/tools/health_check/api/record/{rid}", json={
            "exam_date": "2026-04-01", "needs_recheck": True,
            "secondary_recommended_date": recheck_date,
        })
    return rid


def _enable_mail(client, kinds=("secondary_exam",)):
    return client.post("/tools/health_check/api/admin/mail_notify", json={
        "office_code": "100",
        "enabled": True,
        "kinds": {k: (k in kinds) for k in ("reservation", "night_second", "secondary_exam")},
    })


# ============================================================
# 送信可否
# ============================================================

def test_no_mail_is_sent_until_an_admin_enables_the_office(tmp_path):
    """既定は送らない。管理者が営業所ごとに明示的に有効化したときだけ送る。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _make_record(client, recheck_date=date.today().isoformat())
    with app.app_context():
        assert MailMessage.query.count() == 0


def test_mail_is_queued_when_office_is_enabled(tmp_path):
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    assert _enable_mail(client).status_code == 200
    _make_record(client, recheck_date=date.today().isoformat())

    with app.app_context():
        msgs = MailMessage.query.all()
        assert len(msgs) == 1
        m = msgs[0]
        assert m.to_address == "shain@example.co.jp"
        assert m.category == "health_check"
        assert "二次検査" in m.subject
        assert m.scheduled_at == datetime.combine(date.today(), time(9, 0))


def test_disabled_kind_is_not_sent(tmp_path):
    """種別ごとのON/OFFが効く（二次検査のみ有効 → 予約日では送らない）。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client, kinds=("secondary_exam",))
    rid = _make_record(client)
    client.put(f"/tools/health_check/api/record/{rid}",
               json={"reservation_date": date.today().isoformat()})
    with app.app_context():
        assert MailMessage.query.count() == 0


def test_opt_out_and_exempt_and_retired_are_never_mailed(tmp_path):
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    today = date.today().isoformat()

    for flag in ("email_opt_out", "is_exempt"):
        rid = client.post("/tools/health_check/api/record", json={
            "target_year": 2026, "record_type": "internal",
            "employee_name": f"内勤_{flag}", "office_code": "100",
        }).get_json()["record"]["id"]
        client.put(f"/tools/health_check/api/record/{rid}", json={
            "employee_email": "naikin@example.co.jp",
            "exam_date": "2026-04-01", "needs_recheck": True,
            "secondary_recommended_date": today,
            flag: True,
        })
    with app.app_context():
        assert MailMessage.query.count() == 0, [m.to_address for m in MailMessage.query.all()]


def test_no_email_address_is_skipped_without_error(tmp_path):
    module, app = _build(tmp_path)
    _seed(app, email="")
    client = app.test_client()
    _enable_mail(client)
    _make_record(client, recheck_date=date.today().isoformat())
    with app.app_context():
        assert MailMessage.query.count() == 0


def test_future_date_is_not_sent_early(tmp_path):
    """対象日の前日に達するまでは送らない（ToBellと同じ発火条件）。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    future = (date.today() + timedelta(days=30)).isoformat()
    _make_record(client, recheck_date=future)
    with app.app_context():
        assert MailMessage.query.count() == 0


def test_same_date_is_not_sent_twice(tmp_path):
    """保存のたびに再送しない（dedupe_key）。日付が変われば送り直す。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    today = date.today()
    rid = _make_record(client, recheck_date=today.isoformat())
    for _ in range(3):
        client.put(f"/tools/health_check/api/record/{rid}", json={"remarks": "再保存"})
    with app.app_context():
        assert MailMessage.query.count() == 1

    # 推奨日を変更したら、新しい日付で1通追加される
    client.put(f"/tools/health_check/api/record/{rid}",
               json={"secondary_recommended_date": (today - timedelta(days=1)).isoformat()})
    with app.app_context():
        assert MailMessage.query.count() == 2
        rows = MailMessage.query.order_by(MailMessage.created_at).all()
        assert rows[0].status == "canceled"
        assert rows[1].status == "queued"


def test_automatic_mail_waits_until_target_day_nine(tmp_path, monkeypatch):
    """前日にキューへ積んでも、本人への実送信は対象日9:00まで待つ。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    target = date.today()
    _make_record(client, recheck_date=target.isoformat())

    app.config["MAIL_HOST"] = "smtp.example.com"
    app.config["MAIL_FROM"] = "noreply@example.com"
    monkeypatch.setattr(mail_service, "_send_smtp", lambda message, settings: None)
    with app.app_context():
        before = mail_service.dispatch_pending(now=datetime.combine(target, time(8, 59)))
        assert before["selected"] == 0
        assert MailMessage.query.first().status == "queued"

        at_nine = mail_service.dispatch_pending(now=datetime.combine(target, time(9, 0)))
        assert at_nine["sent"] == 1
        assert MailMessage.query.first().status == "sent"


def test_completion_and_mail_setting_off_cancel_pending_mail(tmp_path):
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    rid = _make_record(client, recheck_date=date.today().isoformat())

    # 受診完了を保存した時点で、まだ送っていない自動メールは取り消す。
    client.put(f"/tools/health_check/api/record/{rid}",
               json={"secondary_exam_date": date.today().isoformat()})
    with app.app_context():
        assert MailMessage.query.first().status == "canceled"

    # 再度対象にしてキューへ積んだ後、営業所設定をOFFにしても即時に取り消す。
    client.put(f"/tools/health_check/api/record/{rid}", json={"secondary_exam_date": ""})
    with app.app_context():
        assert MailMessage.query.first().status == "queued"
    client.post("/tools/health_check/api/admin/mail_notify", json={
        "office_code": "100", "enabled": False, "kinds": {},
    })
    with app.app_context():
        assert MailMessage.query.first().status == "canceled"


# ============================================================
# 本文（社員はDSTTを見られない前提）
# ============================================================

def test_mail_body_is_self_contained_and_never_mentions_dstt(tmp_path):
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    _make_record(client, recheck_date=date(2026, 6, 1).isoformat())

    with app.app_context():
        body = MailMessage.query.first().body_text
        lowered = body.lower()
        for banned in ("dstt", "http://", "https://", "ログイン", "システムで確認", "健診plus"):
            assert banned not in lowered and banned not in body, (
                f"社員はDSTTアカウントを持たないのに、到達できない導線が本文にある: {banned!r}\n{body}"
            )
        # 本人が行動できる情報（種別と日付、問い合わせ先）は入っている
        assert "社員一郎" in body
        assert "2026年6月1日" in body
        assert "二次検査" in body
        assert "本社営業所" in body and "管理花子" in body


def test_mail_body_omits_clinical_detail(tmp_path):
    """検査項目・所見といった健康状態の詳細は本文に載せない。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    rid = _make_record(client)
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "exam_date": "2026-04-01", "needs_recheck": True,
        "secondary_recommended_date": date.today().isoformat(),
        "recheck_items": [{"name": "肝機能", "value": "γ-GTP 高値"}],
        "secondary_result": "要精密検査",
    })
    with app.app_context():
        body = MailMessage.query.first().body_text
        for leaked in ("肝機能", "γ-GTP", "要精密検査"):
            assert leaked not in body, f"健康状態の詳細が本文に漏れている: {leaked}"


# ============================================================
# コントロールパネル API
# ============================================================

def test_mail_notify_settings_require_admin(tmp_path):
    module, app = _build(tmp_path, admin=False)
    _seed(app)
    client = app.test_client()
    assert client.post("/tools/health_check/api/admin/mail_notify", json={
        "office_code": "100", "enabled": True, "kinds": {}}).status_code == 403


def test_bulk_apply_sets_every_accessible_office(tmp_path):
    """一括操作：閲覧できる全営業所へまとめて設定できる。"""
    module, app = _build(tmp_path)
    _seed(app)
    with app.app_context():
        branch = AccessBranch.query.first()
        db.session.add(AccessOffice(code="200", name="第二営業所", branch_id=branch.id))
        db.session.commit()
    client = app.test_client()

    res = client.post("/tools/health_check/api/admin/mail_notify/bulk", json={
        "enabled": True,
        "kinds": {"reservation": True, "night_second": False, "secondary_exam": True},
    })
    assert res.status_code == 200
    assert set(res.get_json()["applied"]) == {"100", "200"}

    listing = client.get("/tools/health_check/api/admin/mail_notify").get_json()["offices"]
    by_code = {o["code"]: o for o in listing}
    assert by_code["100"]["enabled"] is True
    assert by_code["100"]["kinds"]["secondary_exam"] is True
    assert by_code["200"]["kinds"]["night_second"] is False


def test_preview_reports_who_would_and_would_not_receive(tmp_path):
    """プレビュー：送信対象と、送れない理由の内訳を返す。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()
    _enable_mail(client)
    today = date.today().isoformat()
    _make_record(client, recheck_date=today)

    # メール未登録の内勤者
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤三郎", "office_code": "100"}).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "exam_date": "2026-04-01", "needs_recheck": True,
        "secondary_recommended_date": today})

    res = client.get("/tools/health_check/api/mail_preview?year=2026&kind=secondary_exam")
    assert res.status_code == 200
    body = res.get_json()
    assert body["sendable"] == 1
    reasons = {r["reason"]: r["count"] for r in body["blocked"]}
    assert reasons.get("メールアドレス未登録") == 1
    assert body["sample"]["subject"]
    assert "DSTT" not in body["sample"]["body"]
