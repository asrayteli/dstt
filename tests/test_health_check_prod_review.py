"""本格稼働レビュー用の再現テスト（健診PLUS）。

各テストは「現状の挙動」を実証するために書いている。
FAIL するものは修正が必要な欠陥、PASS するものは欠陥挙動そのものを固定している
（コメントでどちらかを明示する）。
"""
import sys
import csv
import json
import io
import importlib.util
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from datetime import date, datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (
    db,
    Office,
    Employee,
    User,
    HealthCheckRecord,
    HealthCheckEditHistory,
    ToBellTask,
)


def _load_module():
    module_path = ROOT / "app" / "tools" / "health_check.py"
    spec = importlib.util.spec_from_file_location("hc_prod_review_module", module_path)
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


def _seed(app):
    with app.app_context():
        db.session.add(Office(office_code="100", office_name="本社営業所", created_by="seed"))
        db.session.add(User(username="m001", password_hash="x", name="管理花子"))
        db.session.add(Employee(
            employee_number="E001", office_code="100", office_name="本社営業所",
            employee_name="社員一郎", employee_type="正社員", company_name="大新東",
            manager_name="管理花子", hire_date=date(2020, 4, 1),
        ))
        db.session.commit()


# ============================================================
# 1. ToBell同期の失敗が、利用者の保存を黙って破棄する
# ============================================================

def test_tobell_failure_silently_discards_the_users_edit(tmp_path, monkeypatch):
    """ensure_health_check_reminders 内で例外（例: SQLite の database is locked）が
    起きると except 節が db.session.rollback() を呼ぶ。これは同一セッションに
    載っている『利用者の編集内容』まで巻き戻す。にもかかわらず API は
    success: True を返すため、利用者は保存できたと信じてしまう。

    期待挙動: 保存は成功し、リマインダー同期だけが失敗する（設計書の
    「例外は飲み込み、健診側の保存処理を妨げない」の通り）。
    """
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001",
    })
    assert res.status_code == 200
    record_id = res.get_json()["record"]["id"]

    import app.services.to_bell_hooks as hooks

    def _boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(hooks, "_close_reminder_tasks_for_record", _boom)

    res = client.put(f"/tools/health_check/api/record/{record_id}", json={
        "reservation_date": "2026-05-20",
        "medical_institution": "大新東健診センター",
        "remarks": "本人希望で午前中",
    })

    # API は成功を返す
    assert res.status_code == 200
    assert res.get_json()["success"] is True

    # ...が、実際には何も保存されていない
    with app.app_context():
        saved = db.session.get(HealthCheckRecord, record_id)
        assert saved.reservation_date == date(2026, 5, 20), (
            "利用者の入力(予約日)が保存されていない。APIはsuccess:Trueを返している"
        )
        assert saved.medical_institution == "大新東健診センター"


def test_tobell_failure_silently_discards_new_record(tmp_path, monkeypatch):
    """新規作成でも同様。success:True + レコードJSONを返すのに、DBには存在しない。"""
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    import app.services.to_bell_hooks as hooks

    def _boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(hooks, "_close_reminder_tasks_for_record", _boom)

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤三郎", "office_code": "100",
        "reservation_date": "2026-06-01",
    })
    assert res.status_code == 200
    assert res.get_json()["success"] is True

    with app.app_context():
        assert HealthCheckRecord.query.filter_by(employee_name="内勤三郎").count() == 1, (
            "作成成功を返したのにレコードが存在しない"
        )


# ============================================================
# 2. 監査証跡（編集履歴）が全体200件で頭打ち
# ============================================================

def test_edit_history_is_capped_globally_at_200_rows(tmp_path):
    """修正済み（回帰防止）。

    以前の MAX_EDIT_HISTORY=200 は「レコード毎」ではなく「テーブル全体」の
    上限だったため、数十人分の編集をしただけで最初の対象者の監査証跡が
    消えていた。現在は MAX_EDIT_HISTORY_PER_RECORD としてレコード単位で剪定する。
    """
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001",
    })
    first_id = res.get_json()["record"]["id"]

    # 1人目に対して数回編集し、その履歴を残す
    for i in range(3):
        client.put(f"/tools/health_check/api/record/{first_id}", json={
            "medical_institution": f"医療機関{i}",
        })

    with app.app_context():
        assert HealthCheckEditHistory.query.filter_by(record_id=first_id).count() > 0

    # その後、他の対象者の登録・編集が積み重なる（実運用では数百人規模）
    for n in range(40):
        r = client.post("/tools/health_check/api/record", json={
            "target_year": 2026, "record_type": "internal",
            "employee_name": f"内勤{n}", "office_code": "100",
        })
        rid = r.get_json()["record"]["id"]
        client.put(f"/tools/health_check/api/record/{rid}", json={
            "reservation_date": "2026-05-01", "exam_date": "2026-05-10",
            "medical_institution": "A", "remarks": "B", "is_night_worker": True,
            "needs_recheck": True, "secondary_recommended_date": "2026-06-01",
        })

    with app.app_context():
        total = HealthCheckEditHistory.query.count()
        first_remaining = HealthCheckEditHistory.query.filter_by(record_id=first_id).count()
        assert first_remaining > 0, (
            f"1人目の編集履歴が全消滅した（全体{total}件の上限に押し出された）。"
            "要配慮個人情報の監査証跡として機能しない"
        )
        # レコード単位の上限は守られている
        assert first_remaining <= module.MAX_EDIT_HISTORY_PER_RECORD


# ============================================================
# 3. 添付ファイルの content_type が利用者入力のまま inline 配信される
# ============================================================

def test_attachment_inline_serves_client_supplied_content_type(tmp_path):
    """アップロード時の Content-Type（クライアント指定）をそのまま保存し、
    ?inline=1 でその Content-Type のまま配信する。拡張子は pdf/jpg/png に
    限っているが、中身と Content-Type は検証していないため、
    text/html を宣言した「.pdf」を保存＝同一オリジンでの保存型XSSになる。
    アプリ全体に CSP は無い（app/__init__.py の _apply_default_security_headers）。
    """
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E001",
    })
    record_id = res.get_json()["record"]["id"]

    payload = b"<html><body><script>alert(document.cookie)</script></body></html>"
    res = client.post(
        f"/tools/health_check/api/record/{record_id}/attachment",
        data={"file": (io.BytesIO(payload), "kenshin_result.pdf", "text/html")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    attachment_id = res.get_json()["attachment"]["id"]

    dl = client.get(
        f"/tools/health_check/api/record/{record_id}/attachment/{attachment_id}?inline=1"
    )
    assert dl.status_code == 200
    assert "text/html" not in dl.headers.get("Content-Type", ""), (
        f"クライアント指定の Content-Type がそのまま返る: {dl.headers.get('Content-Type')} "
        "→ 同一オリジンでの保存型XSS"
    )


# ============================================================
# 4. CSVエクスポートに数式インジェクション対策が無い
# ============================================================

def test_csv_export_has_no_formula_injection_guard(tmp_path):
    """備考などの自由入力が = で始まると、Excel で数式として実行される。
    本リポジトリには CloudShift 用の同種ガード
    (tests/test_cloudshift_export_formula_guard.py) が既にあるが、
    健診PLUS のエクスポートには適用されていない。
    """
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤三郎", "office_code": "100",
    })
    record_id = res.get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{record_id}", json={
        "remarks": '=cmd|\'/c calc\'!A1',
    })

    csv_bytes = client.get("/tools/health_check/api/export?year=2026").data
    text = csv_bytes.decode("utf-8-sig")
    rows = list(csv.reader(StringIO(text)))
    header, body = rows[0], [r for r in rows[1:] if r]
    remarks_idx = header.index("備考")
    cell = body[0][remarks_idx]
    assert cell.startswith("'"), (
        f"危険な数式がそのままCSVに出力されている（先頭に ' のエスケープが無い）: {cell!r}"
    )
    assert not cell.startswith("="), "Excelが数式として解釈する形のまま"


# ============================================================
# 5. 一覧APIにページネーションが無く、同期でN+1クエリが出る
# ============================================================

def test_records_list_is_paginated_and_avoids_n_plus_1_queries(tmp_path):
    """修正済み（回帰防止）。

    設計書 §7 の「一覧（フィルタ・ページネーション）」を実装した。
    あわせて _sync_record_list が Employee を一括プリフェッチし、
    ユーザー→営業所の解決をリクエスト内でメモ化するため、
    レコード数に比例したSQLが出なくなった。
    """
    module, app = _build(tmp_path)
    _seed(app)

    with app.app_context():
        for n in range(300):
            db.session.add(Employee(
                employee_number=f"P{n:04d}", office_code="100", office_name="本社営業所",
                employee_name=f"社員{n}", employee_type="正社員", company_name="大新東",
                manager_name="管理花子",
            ))
        db.session.commit()

    client = app.test_client()
    client.post("/tools/health_check/api/bulk_create", json={"target_year": 2026, "offices": ["100"]})

    from sqlalchemy import event
    counter = {"n": 0}

    with app.app_context():
        engine = db.engine

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        res = client.get("/tools/health_check/api/records?year=2026")
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    body = res.get_json()
    # 全件数は total で返し、1ページ分だけ records に載せる
    assert body["total"] == 301
    assert body["page"] == 1
    assert len(body["records"]) == body["per_page"] == 100
    assert body["pages"] == 4

    assert counter["n"] < 50, (
        f"一覧1回の表示で {counter['n']} 本のSQLが発行された（N+1）"
    )

    # 最終ページは端数だけ返る
    last = client.get("/tools/health_check/api/records?year=2026&page=4").get_json()
    assert len(last["records"]) == 1 and last["page"] == 4

    # per_page=0 は全件（CSV突合など、意図して全部見たいとき用）
    allrec = client.get("/tools/health_check/api/records?year=2026&per_page=0").get_json()
    assert len(allrec["records"]) == 301


# ============================================================
# 6. 退職者・受診非対象者にもリマインドが起票され続ける
# ============================================================

def test_reminders_fire_for_retired_and_exempt_people(tmp_path):
    """一覧・ダッシュボードからは除外される「退職者」「受診非対象者」でも、
    ToBellリマインドは条件を満たす限り起票され続ける。
    ensure_health_check_reminders / sweep_health_check_reminders のどちらにも
    is_retired / is_exempt の除外が無い。
    """
    module, app = _build(tmp_path)
    _seed(app)
    client = app.test_client()

    with app.app_context():
        from app.models import ToBellUserSettings
        db.session.add(ToBellUserSettings(
            username="m001", integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()

    res = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "退職済一郎", "office_code": "100",
    })
    rid = res.get_json()["record"]["id"]

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "reservation_date": yesterday,
        "is_retired": True,
        "is_exempt": True,
    })
    with app.app_context():
        rec = db.session.get(HealthCheckRecord, rid)
        rec.manager_user = "m001"
        db.session.commit()

    client.put(f"/tools/health_check/api/record/{rid}", json={"remarks": "再保存"})

    with app.app_context():
        tasks = ToBellTask.query.filter_by(source_tool="health_check").all()
        active = [t for t in tasks if t.status not in ("done", "archived")]
        assert not active, (
            f"退職者かつ受診非対象者に対して {len(active)} 件のリマインドが起票された: "
            f"{[t.title for t in active]}"
        )
