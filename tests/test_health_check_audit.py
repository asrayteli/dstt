"""健診PLUS（health_check）アクセス制御の監査用 再現テスト。

要配慮個人情報（健康情報）を扱うツールのため、営業所スコープ外の閲覧・改変・
通知（＝第三者への健康情報の漏えい）が起きないことを検証する。

各テストは「現状の挙動」を実証するために書いている。
  - FAIL するもの = 現状コードの欠陥（修正が必要）
  - PASS するもの = 現状の防御が効いていることの確認（回帰防止）
どちらであるかは各テストの docstring に明示する。

fixtures は tests/test_health_check.py と同一（_build / _seed_basic /
_setup_two_offices / _as_user）を踏襲している。
"""
import sys
import io
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from datetime import date

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (  # noqa: E402
    db,
    Office,
    Employee,
    Site,
    User,
    AccessBranch,
    AccessOffice,
    UserAccessibleOffice,
    HealthCheckRecord,
    HealthCheckEditHistory,
    ToBellTask,
    ToBellUserSettings,
)


# ============================================================
# fixtures（tests/test_health_check.py と同じ構成）
# ============================================================

def _load_module():
    module_path = ROOT / "app" / "tools" / "health_check.py"
    spec = importlib.util.spec_from_file_location("health_check_audit_module", module_path)
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
    # ToBellフックは本物の app.tools.health_check.get_office_health_officers を呼ぶため、
    # 同じテスト用データ領域を参照するよう本物モジュール側も合わせて差し替える。
    import app.tools.health_check as real_hc
    real_hc.get_data_path = lambda: str(data_dir)
    real_hc.get_uploads_path = lambda: str(data_dir / "uploads")
    return module, app


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


def _add_user(app, *, username, name, office_code=None, opt_in=False):
    """営業所コード指定でDSTTユーザーを追加する（office_code=None は無所属）。"""
    with app.app_context():
        office_id = None
        if office_code:
            office = AccessOffice.query.filter_by(code=office_code).first()
            office_id = office.id if office else None
        user = User(username=username, password_hash="x", name=name, office_id=office_id)
        db.session.add(user)
        if opt_in:
            db.session.add(ToBellUserSettings(
                username=username, integrations={"health_check.linkage": True}, preferences={}))
        db.session.commit()
        return user.id, office_id


def _as_user(module, *, username, uid, office_id, admin=False):
    module._is_dstt_admin = lambda *a, **k: admin
    module.current_user = SimpleNamespace(
        is_authenticated=True, username=username, name=username,
        id=uid, office_id=office_id, branch_id=None, department_id=None, is_admin=admin,
    )


# ============================================================
# 1. GET /api/settings が全営業所の「健康診断担当」を無条件に返す
# ============================================================

def test_api_settings_get_is_restricted_to_dstt_admin(tmp_path):
    """修正済み（回帰防止）。

    GET /api/settings は `load_settings()` をそのまま返す。settings.json には
    `health_officers`（営業所コード → username 配列）が全営業所分入っているため、
    以前は @login_required だけで営業所100 の一般利用者が営業所200 の
    健康診断担当を取得できていた。現在は DSTT管理者のみ。
    自営業所の担当者一覧が要る場合は /api/office_officers を使う。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200")

    # 事前に（DSTT管理者が）両営業所の健康診断担当を設定した状態を作る
    module.save_settings({
        "global_lead_days": 3,
        "health_officers": {"100": ["u1"], "200": ["u2"]},
    })

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    body = client.get("/tools/health_check/api/settings")
    assert body.status_code == 403, (
        "一般利用者が GET /api/settings で全営業所の健康診断担当を取得できてしまう: "
        f"{body.get_json()!r}"
    )

    # 自営業所の担当者は従来どおり取得できる（通知先の確認に必要）
    own = client.get("/tools/health_check/api/office_officers?office=100")
    assert own.status_code == 200
    assert [o["username"] for o in own.get_json()["officers"]] == ["u1"]

    # 他営業所は空で返る（存在も担当者名も漏らさない）
    other = client.get("/tools/health_check/api/office_officers?office=200")
    assert other.get_json()["officers"] == []


# ============================================================
# 2. 健康診断担当に「その営業所と無関係なユーザー」を設定できる
# ============================================================

def test_health_admin_can_appoint_out_of_office_user_as_health_officer(tmp_path):
    """FAIL（欠陥）。

    app/tools/health_check.py:1631-1636 の POST /api/admin/health_officers は
    `User.username` の実在チェックしかしていない。対象営業所へのアクセス権は
    一切検証しないため、営業所100 の健診PLUS管理者が
    「営業所200 のユーザー」「どこにも所属しないユーザー」を
    営業所100 の健康診断担当（＝既定の通知先）に指定できる。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200")
    _add_user(app, username="nobody", name="無所属子", office_code=None)

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    module.save_permissions({"admins": ["u1"]})  # u1 は営業所100の健診PLUS管理者
    client = app.test_client()

    res = client.post("/tools/health_check/api/admin/health_officers",
                      json={"office_code": "100", "users": ["u2", "nobody"]})
    assigned = {o["username"] for o in (res.get_json() or {}).get("officers", [])}
    assert res.status_code != 200 or not assigned, (
        "営業所100の管理者が、営業所100に権限の無いユーザーを健康診断担当に設定できた: "
        f"status={res.status_code} officers={assigned!r}"
    )


def test_out_of_office_health_officer_receives_health_info_via_tobell(tmp_path):
    """FAIL（欠陥・実害の実証）。

    上記の設定ミス／悪用の結果、ToBell 経由で
    「{氏名}さんの二次検査受診推奨日になりました。」というタスク・通知が
    営業所200 のユーザー（健診PLUS の閲覧権限が無い相手）に配信される。
    氏名＋二次検査対象という健康情報そのものの漏えいになる。
    app/services/to_bell_hooks.py:340-368 (_hc_recipients) 参照。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200", opt_in=True)

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    module.save_permissions({"admins": ["u1"]})
    client = app.test_client()

    # 営業所100の健康診断担当に、営業所200のユーザー u2 を指定
    client.post("/tools/health_check/api/admin/health_officers",
                json={"office_code": "100", "users": ["u2"]})

    # 営業所100の社員のレコードを起票し、二次検査推奨日を入れる
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E1",
    }).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": date.today().isoformat(),
    })

    with app.app_context():
        tasks = ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type="secondary_exam").all()
        leaked = [t for t in tasks if t.assigned_to == "u2"]
        assert not leaked, (
            "営業所200のユーザーに、営業所100社員の健康情報が通知された: "
            f"{[t.title for t in leaked]}"
        )


# ============================================================
# 3. GET /api/sites に営業所スコープが無い
# ============================================================

def test_api_sites_returns_sites_of_every_office(tmp_path):
    """FAIL（欠陥）。

    app/tools/health_check.py:1288-1305 の GET /api/sites は
    `Site.is_active` でしか絞っておらず、`Site.office_code` を一切見ない。
    健診PLUS を使える利用者なら、権限の無い営業所の現場（専従先）名を
    全件列挙できる。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        db.session.add(Site(
            site_id="S100", site_name="本社担当の現場",
            site_manager_last="現場", site_manager_first="太郎",
            site_manager_id="E1", site_register="seed", site_updater="seed",
            office_code="100", is_active=True))
        db.session.add(Site(
            site_id="S200", site_name="第二営業所だけの現場",
            site_manager_last="現場", site_manager_first="次郎",
            site_manager_id="E2", site_register="seed", site_updater="seed",
            office_code="200", is_active=True))
        db.session.commit()

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    names = {s["site_name"] for s in client.get(
        "/tools/health_check/api/sites").get_json()["sites"]}
    assert "第二営業所だけの現場" not in names, (
        f"営業所200の現場が営業所100の利用者に見えている: {names!r}")


# ============================================================
# 4. 編集履歴がレコード削除後も残り、record_id の再利用で他営業所へ漏れる
# ============================================================

def test_edit_history_survives_record_delete_and_leaks_via_record_id_reuse(tmp_path):
    """FAIL（欠陥）。

    - api_delete_record (app/tools/health_check.py:845-865) はレコードを削除するが
      HealthCheckEditHistory は削除しない（record_id は FK ではなく素の Integer）。
    - api_history (app/tools/health_check.py:1377-1393) の認可は
      「現在その record_id を持つレコード」に対してのみ行われる。
    SQLite（および ROWID 再利用が起きる構成）では削除された ID が次の INSERT で
    再利用されるため、他営業所の利用者が新規作成したレコードの ID で
    「削除済み・他営業所レコードの健康項目の旧値／新値と氏名」を取得できてしまう。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    u2_id, off200_id = _add_user(app, username="u2", name="第二太郎", office_code="200")
    client = app.test_client()

    # --- 営業所200 の利用者が健康情報を入力して、その後レコードを削除 ---
    _as_user(module, username="u2", uid=u2_id, office_id=off200_id, admin=False)
    victim_id = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2",
    }).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{victim_id}", json={
        "needs_recheck": True,
        "secondary_result": "肝機能 要精密検査",
        "medical_institution": "第二営業所健診センター",
    })
    assert client.delete(f"/tools/health_check/api/record/{victim_id}").status_code == 200

    # --- 営業所100 の利用者が自分の営業所でレコードを新規作成 ---
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    new_id = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "100",
    }).get_json()["record"]["id"]
    assert new_id == victim_id, (
        "前提（ID再利用）が成立しなかったためこのシナリオは再現できない: "
        f"victim={victim_id} new={new_id}")

    histories = client.get(
        f"/tools/health_check/api/history?record_id={new_id}").get_json()["histories"]
    blob = json.dumps(histories, ensure_ascii=False)
    assert "社員200" not in blob and "肝機能 要精密検査" not in blob, (
        "削除された営業所200レコードの編集履歴（氏名・二次検査結果）が、"
        f"営業所100の利用者に見えている: {blob}")


def test_edit_history_rows_are_not_purged_when_record_is_deleted(tmp_path):
    """FAIL（欠陥・上記の根本原因を単体で実証）。

    削除された健診レコードの編集履歴（要配慮個人情報の旧値・新値）が
    DB に残り続ける。孤児となった行は以後どの認可判定にも紐付かない。
    """
    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    client = app.test_client()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2",
    }).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "secondary_result": "血糖値 要再検査"})
    client.delete(f"/tools/health_check/api/record/{rid}")

    with app.app_context():
        rows = HealthCheckEditHistory.query.filter_by(record_id=rid).all()
        leaked = [r for r in rows if (r.new_value or "") == "血糖値 要再検査"]
        assert not rows, (
            "レコード削除後も編集履歴が残っている（うち健康情報を含む行 "
            f"{len(leaked)} 件 / 全 {len(rows)} 件）")


# ============================================================
# 5. 追加通知先の営業所フィルタが DSTT管理者の操作時だけ無効化される
# ============================================================

def test_admin_can_persist_out_of_office_extra_notify_user(tmp_path):
    """FAIL（欠陥）。

    _serialize_extra_notify_users (app/tools/health_check.py:175-176) は
    `if code and not _is_dstt_admin():` のときだけ営業所フィルタを掛ける。
    フィルタの目的は「宛先が営業所外の人でないこと」の担保なのに、
    判定材料が『操作者が管理者か』になっているため、DSTT管理者が編集すると
    営業所外のユーザーが追加通知先として保存されてしまう。
    internal / pre_hire レコードは _sync_linked_record を通らないので
    以後も再フィルタされず、恒久的に残る。
    """
    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200", opt_in=True)
    client = app.test_client()

    created = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "100",
        "extra_notify_users": ["u2"],
    })
    assert created.status_code == 200
    assert created.get_json()["record"]["extra_notify_users"] == [], (
        "営業所100のレコードに、営業所200のユーザーが追加通知先として保存された: "
        f"{created.get_json()['record']['extra_notify_users']!r}")


def test_out_of_office_extra_notify_user_receives_health_info_via_tobell(tmp_path):
    """FAIL（欠陥・実害の実証）。

    上の結果、営業所200 のユーザーに営業所100 社員の
    「{氏名}さんの二次検査受診推奨日になりました。」が ToBell で届く。
    """
    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200", opt_in=True)
    client = app.test_client()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "100",
        "extra_notify_users": ["u2"],
    }).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "needs_recheck": True, "secondary_recommended_date": date.today().isoformat()})

    with app.app_context():
        leaked = [t for t in ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type="secondary_exam").all()
            if t.assigned_to == "u2"]
        assert not leaked, (
            "営業所200のユーザーに営業所100社員の健康情報が通知された: "
            f"{[t.title for t in leaked]}")


def test_admin_can_assign_out_of_office_manager_user(tmp_path):
    """FAIL（欠陥）。

    _manager_user_allowed (app/tools/health_check.py:505) も
    `if not code or _is_dstt_admin(): return True` と、
    『宛先の営業所』ではなく『操作者が管理者か』で判定している。
    DSTT管理者が営業所100のレコードの担当者に営業所200のユーザーを指定でき、
    internal / pre_hire レコードでは _sync_linked_record が走らないため
    そのまま ToBell の通知先として固定される。
    """
    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200", opt_in=True)
    client = app.test_client()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "100"}).get_json()["record"]["id"]
    res = client.put(f"/tools/health_check/api/record/{rid}/manager",
                     json={"manager_user": "u2"})
    assert res.status_code == 400, (
        "営業所100のレコードの担当者に、営業所200のユーザーを設定できた: "
        f"status={res.status_code}")


# ============================================================
# 6. ToBell の日次スイープが「転属前の営業所」の担当者へ通知し続ける
# ============================================================

def test_tobell_sweep_notifies_previous_office_after_employee_moves(tmp_path):
    """FAIL（欠陥）。

    sweep_health_check_reminders (app/services/to_bell_hooks.py:567-604) は
    Employee からの再同期を行わず、DB に残った古い record.office_code /
    manager_user のまま _hc_recipients を解決する
    （app/services/to_bell_hooks.py:340-368）。
    社員が営業所100→200 に転属しても、誰かが健診PLUS の画面で当該レコードを
    開いて同期するまで、旧営業所100 の健康診断担当に
    「{氏名}さんの…になりました。」が配信され続ける。
    """
    from app.services.to_bell_hooks import sweep_health_check_reminders

    module, app = _build(tmp_path, admin=True)
    _setup_two_offices(app)
    _add_user(app, username="h100", name="本社健診担当", office_code="100", opt_in=True)
    client = app.test_client()

    client.post("/tools/health_check/api/admin/health_officers",
                json={"office_code": "100", "users": ["h100"]})
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E1",
    }).get_json()["record"]["id"]
    client.put(f"/tools/health_check/api/record/{rid}", json={
        "reservation_date": date.today().isoformat()})

    # 社員が営業所200へ転属（健診PLUS を開かないまま夜間バッチが走る）
    with app.app_context():
        emp = Employee.query.filter_by(employee_number="E1").first()
        emp.office_code = "200"
        emp.office_name = "第二"
        db.session.commit()
        # 既存の予約リマインドを一旦クローズし、スイープでの再起票だけを見る
        for task in ToBellTask.query.filter_by(source_tool="health_check").all():
            task.status = "archived"
        db.session.commit()

        sweep_health_check_reminders()

        revived = [t for t in ToBellTask.query.filter_by(
            source_tool="health_check", source_ref_type="reservation").all()
            if t.assigned_to == "h100" and t.status == "todo"]
        assert not revived, (
            "転属後も旧営業所100の健康診断担当に健康情報が通知された: "
            f"{[t.title for t in revived]}")


# ============================================================
# 7.（回帰確認）office_code はペイロードから書き換えられない
# ============================================================

def test_office_code_is_not_writable_via_create_or_update_payload(tmp_path):
    """PASS（現状の防御が効いている）。

    office_code は TEXT_FIELDS / DATE_FIELDS / BOOL_FIELDS のいずれにも
    含まれないため _apply_payload では反映されず、作成時も
    has_office_access を通過した値しか入らない。スコープ脱出はできない。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    # 作成時に他営業所のコードを指定 → 403
    denied = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "200"})
    assert denied.status_code == 403

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "internal",
        "employee_name": "内勤太郎", "office_code": "100"}).get_json()["record"]["id"]

    # 更新ペイロードでの上書きは無視される
    client.put(f"/tools/health_check/api/record/{rid}", json={"office_code": "200"})
    with app.app_context():
        assert db.session.get(HealthCheckRecord, rid).office_code == "100"

    # linked 作成時に office_code を偽装しても Employee 側が優先される
    linked_id = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked",
        "employee_number": "E1", "office_code": "200"}).get_json()["record"]["id"]
    with app.app_context():
        assert db.session.get(HealthCheckRecord, linked_id).office_code == "100"


def test_linked_record_cannot_be_created_for_out_of_scope_employee(tmp_path):
    """PASS（現状の防御が効いている）。

    employee_number 経由でも employee_id 直指定でも、
    has_office_access(employee.office_code) が効いて 403 になる。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    with app.app_context():
        e2_id = Employee.query.filter_by(employee_number="E2").first().id
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    by_number = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2"})
    assert by_number.status_code == 403
    by_id = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_id": e2_id})
    assert by_id.status_code == 403


# ============================================================
# 7.（回帰確認）添付ファイルの IDOR / パストラバーサル
# ============================================================

def test_attachment_endpoints_are_gated_by_record_office_scope(tmp_path):
    """PASS（現状の防御が効いている）。

    添付の GET / PATCH / DELETE はいずれも
    `attachment.record_id != record_id` の突合と _check_record_access を通す。
    他営業所の利用者からは 403、record_id を偽装すると 404 になる。
    stored_path は "<年度>/<uuid>.<ext>" とサーバ生成のため
    ../ を混入させる余地が無い（パストラバーサル無し）。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    u2_id, off200_id = _add_user(app, username="u2", name="第二太郎", office_code="200")
    client = app.test_client()

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E1",
    }).get_json()["record"]["id"]
    aid = client.post(
        f"/tools/health_check/api/record/{rid}/attachment",
        data={"file": (io.BytesIO(b"%PDF-1.4 secret"), "kenshin.pdf")},
        content_type="multipart/form-data",
    ).get_json()["attachment"]["id"]

    with app.app_context():
        stored = db.session.get(
            __import__("app.models", fromlist=["HealthCheckAttachment"]).HealthCheckAttachment, aid
        ).stored_path
        assert ".." not in stored and not Path(stored).is_absolute()

    # 他営業所の利用者からは全メソッド 403
    _as_user(module, username="u2", uid=u2_id, office_id=off200_id, admin=False)
    assert client.get(f"/tools/health_check/api/record/{rid}/attachment/{aid}").status_code == 403
    assert client.patch(f"/tools/health_check/api/record/{rid}/attachment/{aid}",
                        json={"title": "x"}).status_code == 403
    assert client.delete(f"/tools/health_check/api/record/{rid}/attachment/{aid}").status_code == 403

    # 自分のレコードIDに付け替えてもクロス参照できない（404）
    own = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E2",
    }).get_json()["record"]["id"]
    assert client.get(
        f"/tools/health_check/api/record/{own}/attachment/{aid}").status_code == 404


# ============================================================
# 8.（回帰確認）その他エンドポイントのスコープ
# ============================================================

def test_users_and_office_officers_and_history_list_are_scoped(tmp_path):
    """PASS（現状の防御が効いている）。

    - GET /api/users は自分の営業所と重なるユーザーのみ返す。
    - GET /api/office_officers は権限の無い営業所を指定すると空を返す。
    - GET /api/history（record_id 無し）は DSTT管理者以外には空を返す。
    - GET /api/integration は current_user 固定で他人の設定を触れない。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _add_user(app, username="u2", name="第二太郎", office_code="200")
    module.save_settings({"global_lead_days": 3, "health_officers": {"200": ["u2"]}})

    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    usernames = {u["username"] for u in client.get(
        "/tools/health_check/api/users").get_json()["users"]}
    assert "u2" not in usernames

    officers = client.get(
        "/tools/health_check/api/office_officers?office=200").get_json()["officers"]
    assert officers == []

    assert client.get("/tools/health_check/api/history").get_json()["histories"] == []


def test_health_plus_admin_cannot_widen_office_scope(tmp_path):
    """PASS（現状の防御が効いている）。

    健診PLUS管理者フラグは営業所スコープを広げない。
    can_admin_office → has_office_access（DSTTアクセス権）で判定されるため、
    権限の無い営業所への健康診断担当設定は 403、GET も自営業所のみ。
    健診PLUS管理者自身の指定/解除は DSTT管理者専用。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    module.save_permissions({"admins": ["u1"]})
    client = app.test_client()

    listed = client.get("/tools/health_check/api/admin/health_officers").get_json()
    assert {o["code"] for o in listed["offices"]} == {"100"}
    assert client.post("/tools/health_check/api/admin/health_officers",
                       json={"office_code": "200", "users": ["u1"]}).status_code == 403
    assert client.get("/tools/health_check/api/admin/permissions").status_code == 403
    assert client.post("/tools/health_check/api/admin/grant",
                       json={"user_id": "u1"}).status_code == 403
    assert client.post("/tools/health_check/api/settings",
                       json={"global_lead_days": 30}).status_code == 403
    # レコード面でも 200 は見えない
    assert client.get("/tools/health_check/api/records?office=200").status_code == 403
    assert client.get("/tools/health_check/api/dashboard?office=200").status_code == 403
    assert client.get("/tools/health_check/api/export?office=200").status_code == 403


def test_record_scope_follows_employee_office_move_and_deletion(tmp_path):
    """PASS（現状の防御が効いている）。

    linked レコードのスコープは Employee の現在の営業所（_record_access_office）で
    判定され、社員が転属すると旧営業所からは 403 になる。
    Employee 行が消えた場合は最後に同期された record.office_code にフォールバックし、
    _scoped_query の outerjoin 側とも一致する（フェイルクローズ）。
    """
    module, app = _build(tmp_path, admin=False)
    uid, off100_id = _setup_two_offices(app)
    u2_id, off200_id = _add_user(app, username="u2", name="第二太郎", office_code="200")
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    client = app.test_client()

    rid = client.post("/tools/health_check/api/record", json={
        "target_year": 2026, "record_type": "linked", "employee_number": "E1",
    }).get_json()["record"]["id"]

    with app.app_context():
        emp = Employee.query.filter_by(employee_number="E1").first()
        emp.office_code = "200"
        db.session.commit()

    assert client.get(f"/tools/health_check/api/record/{rid}").status_code == 403
    assert client.get("/tools/health_check/api/records?year=2026").get_json()["count"] == 0

    # 転属先の利用者からは見える → その閲覧で record.office_code が 200 に同期される
    _as_user(module, username="u2", uid=u2_id, office_id=off200_id, admin=False)
    assert client.get(f"/tools/health_check/api/record/{rid}").status_code == 200

    # Employee 行を物理削除しても、旧営業所(100)には戻らない
    with app.app_context():
        db.session.delete(Employee.query.filter_by(employee_number="E1").first())
        db.session.commit()
    _as_user(module, username="u1", uid=uid, office_id=off100_id, admin=False)
    assert client.get(f"/tools/health_check/api/record/{rid}").status_code == 403
    assert client.get("/tools/health_check/api/records?year=2026").get_json()["count"] == 0
