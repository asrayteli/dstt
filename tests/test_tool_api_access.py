"""ツール間API専用許可（UserToolPermission.scope='api'）の統合テスト。

「社員名簿PLUS本体は見せないが、ShifterSync などツール間API経由の参照だけは許す」
という付与を、実 app（create_app の before_request ガード込み）で検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OFFICE_CODE = "S01"


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "tool_api.db"
    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        _seed_master(db)
    yield app


def _seed_master(db):
    from app.models import AccessBranch, AccessOffice, Employee, Office, Site

    branch = AccessBranch(name="東京支店", code="T01")
    db.session.add(branch)
    db.session.flush()
    office = AccessOffice(branch_id=branch.id, name="新宿営業所", code=OFFICE_CODE)
    db.session.add(office)
    db.session.flush()

    db.session.add(Office(office_code=OFFICE_CODE, office_name="新宿営業所", created_by="system"))
    db.session.add(
        Employee(
            employee_number="E001",
            office_code=OFFICE_CODE,
            office_name="新宿営業所",
            employee_name="山田 太郎",
            employee_kana="ヤマダ タロウ",
            job_title="運転士",
            postal_code="123-4567",
            address1="東京都千代田区丸の内1-1-1",
            address2="DSTTビル",
            mansion_name="101号室",
            is_deleted=False,
            is_retired=False,
        )
    )
    db.session.add(
        Site(
            site_id="0001",
            site_name="テスト現場",
            site_manager_last="大新",
            site_manager_first="太郎",
            site_manager_id="E001",
            site_register="system",
            site_updater="system",
            office_code=OFFICE_CODE,
            is_active=True,
        )
    )
    db.session.commit()
    return branch.id, office.id


def _make_user(app_ctx, username: str, *, full=(), api=(), is_admin=False):
    """ユーザーを作り、通常許可 / ツール間API専用許可を付ける。"""
    from app.access_control import set_tool_access_scopes
    from app.models import AccessBranch, AccessOffice, User, db
    from app.tool_api import TOOL_SCOPE_API, TOOL_SCOPE_FULL

    with app_ctx.app_context():
        branch = AccessBranch.query.first()
        office = AccessOffice.query.first()
        user = User(
            username=username,
            password_hash="hash",
            name=username,
            is_admin=is_admin,
            branch_id=branch.id,
            office_id=office.id,
        )
        db.session.add(user)
        db.session.commit()
        scopes = {key: TOOL_SCOPE_FULL for key in full}
        scopes.update({key: TOOL_SCOPE_API for key in api})
        if scopes:
            set_tool_access_scopes(user.id, scopes, granted_by="system")
        return user.id


def _client(app_ctx, username: str):
    client = app_ctx.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True
    return client


# ------------------------------------------------------------------
# レジストリ自体の健全性
# ------------------------------------------------------------------

def test_registry_is_consistent():
    from app.tool_api import registry_problems

    assert registry_problems() == []


def test_registered_endpoints_exist_and_are_read_only(app_ctx):
    """登録名が実在し、参照専用（GET/HEAD）であること。

    ここが崩れると許可判定が黙って効かなくなる（＝従来どおり提供元ツールの
    許可が要る）か、更新系まで API 専用許可で通ってしまう。
    """
    from app.tool_api import TOOL_API_ENDPOINTS

    rules = {}
    for rule in app_ctx.url_map.iter_rules():
        rules.setdefault(rule.endpoint, set()).update(rule.methods or ())

    for name, spec in TOOL_API_ENDPOINTS.items():
        assert name in rules, f"{name} が url_map に無い"
        assert rules[name] <= {"GET", "HEAD", "OPTIONS"}, f"{name} が参照専用でない"
        assert name.split(".", 1)[0] == spec.provider


# ------------------------------------------------------------------
# API専用許可の効き方
# ------------------------------------------------------------------

def test_api_scope_alone_cannot_reach_anything(app_ctx):
    """利用側ツールの許可が無ければ、API専用許可だけでは何も見られない。"""
    _make_user(app_ctx, "apionly", api=["pluslist"])
    client = _client(app_ctx, "apionly")

    assert client.get("/tools/pluslist/").status_code == 403
    assert client.get("/tools/pluslist/api/search_employee?q=山田").status_code == 403


def test_anonymous_cannot_use_inter_tool_api(app_ctx):
    """未ログインはツール間APIでも通らない（ログイン画面へ）。"""
    anon = app_ctx.test_client()
    resp = anon.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code in (302, 401, 403)
    if resp.status_code == 302:
        assert "/auth/login" in (resp.location or "")


def test_api_scope_with_consumer_tool_can_use_inter_tool_api(app_ctx):
    """API専用許可 + ShifterSync許可 → ツール間APIだけ通る。"""
    _make_user(app_ctx, "ss", full=["shiftersync"], api=["pluslist"])
    client = _client(app_ctx, "ss")

    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload and payload[0]["employee_number"] == "E001"
    # 住所などの個人情報は返さない
    assert set(payload[0]) == {
        "employee_number",
        "employee_name",
        "office_name",
        "job_title",
    }


def test_api_scope_does_not_open_the_provider_tool(app_ctx):
    """本体UIも、レジストリ未登録のAPIも通さない。"""
    _make_user(app_ctx, "ss2", full=["shiftersync"], api=["pluslist"])
    client = _client(app_ctx, "ss2")

    assert client.get("/tools/pluslist/").status_code == 403
    # 名簿一覧API（ツール間APIではない）は従来どおり拒否
    assert client.get("/tools/pluslist/api/employees").status_code == 403
    assert client.get("/tools/pluslist/api/export/csv").status_code == 403
    assert client.get("/manual/pluslist").status_code == 403


def test_full_grant_still_returns_every_field(app_ctx):
    """社員名簿PLUS本体の許可がある人は従来どおり全項目（封筒印刷など）。"""
    _make_user(app_ctx, "plus", full=["pluslist"])
    client = _client(app_ctx, "plus")

    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    row = resp.get_json()[0]
    assert row["postal_code"] == "123-4567"
    assert row["address1"] == "東京都千代田区丸の内1-1-1"


def test_admin_is_unrestricted(app_ctx):
    _make_user(app_ctx, "boss", is_admin=True)
    client = _client(app_ctx, "boss")

    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    assert "address1" in resp.get_json()[0]


def test_siteplus_inter_tool_api(app_ctx):
    _make_user(app_ctx, "ss3", full=["shiftersync"], api=["siteplus"])
    client = _client(app_ctx, "ss3")

    assert client.get("/tools/siteplus/").status_code == 403
    resp = client.get("/tools/siteplus/api/cloudshift/sites?q=0001")
    assert resp.status_code == 200
    assert [s["site_id"] for s in resp.get_json()["sites"]] == ["0001"]


def test_office_scope_still_applies_to_api_scope(app_ctx):
    """API専用許可でも、営業所スコープの絞り込みは効き続ける。"""
    from app.models import AccessBranch, AccessOffice, User, db

    _make_user(app_ctx, "ss4", full=["shiftersync"], api=["pluslist"])
    with app_ctx.app_context():
        branch = AccessBranch.query.first()
        other = AccessOffice(branch_id=branch.id, name="別営業所", code="S99")
        db.session.add(other)
        db.session.flush()
        user = User.query.filter_by(username="ss4").first()
        user.office_id = other.id
        db.session.commit()

    client = _client(app_ctx, "ss4")
    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    assert resp.get_json() == []


# ------------------------------------------------------------------
# 既存の権限判定を汚さないこと
# ------------------------------------------------------------------

def test_api_scope_is_not_a_normal_grant(app_ctx):
    from app.access_control import (
        get_accessible_nav_items,
        user_has_tool_access,
        usernames_with_tool_access,
    )
    from app.models import User

    _make_user(app_ctx, "ss5", full=["shiftersync"], api=["pluslist"])
    with app_ctx.app_context():
        user = User.query.filter_by(username="ss5").first()
        assert user_has_tool_access("shiftersync", user)
        assert not user_has_tool_access("pluslist", user)
        keys = {item.get("key") for item in get_accessible_nav_items(user)}
        assert "shiftersync" in keys
        assert "pluslist" not in keys
        # 一斉通知の宛先解決では API 専用許可を数えない
        assert usernames_with_tool_access(["ss5"], "pluslist") == set()


def test_full_grant_sync_keeps_api_grants(app_ctx):
    """権限テンプレートなど通常許可だけを扱う経路がAPI専用許可を消さない。"""
    from app.access_control import set_tool_access
    from app.models import User
    from app.tool_api import TOOL_SCOPE_API, normalize_tool_scope

    user_id = _make_user(app_ctx, "ss6", full=["shiftersync"], api=["pluslist"])
    with app_ctx.app_context():
        set_tool_access(user_id, ["leave_mgr"], granted_by="system")
        user = User.query.filter_by(username="ss6").first()
        scopes = {
            perm.tool_key: normalize_tool_scope(perm.scope)
            for perm in user.tool_permissions
        }
        assert scopes == {"leave_mgr": "full", "pluslist": TOOL_SCOPE_API}


def test_full_only_sync_does_not_promote_an_api_grant(app_ctx):
    """通常許可だけを扱う経路は、個別のAPI専用許可を黙って格上げしない。"""
    from app.access_control import set_tool_access
    from app.models import User
    from app.tool_api import normalize_tool_scope

    user_id = _make_user(app_ctx, "ss7", api=["pluslist"])
    with app_ctx.app_context():
        set_tool_access(user_id, ["pluslist"], granted_by="system")
        user = User.query.filter_by(username="ss7").first()
        rows = list(user.tool_permissions)
        assert len(rows) == 1
        assert normalize_tool_scope(rows[0].scope) == "api"


def test_scope_aware_sync_can_promote_an_api_grant(app_ctx):
    """両スコープを管理する経路（管理画面の保存）では格上げできる。"""
    from app.access_control import set_tool_access_scopes
    from app.models import User
    from app.tool_api import TOOL_SCOPE_FULL, normalize_tool_scope

    user_id = _make_user(app_ctx, "ss7b", api=["pluslist"])
    with app_ctx.app_context():
        set_tool_access_scopes(
            user_id, {"pluslist": TOOL_SCOPE_FULL}, granted_by="system"
        )
        user = User.query.filter_by(username="ss7b").first()
        rows = list(user.tool_permissions)
        assert len(rows) == 1
        assert normalize_tool_scope(rows[0].scope) == "full"


# ------------------------------------------------------------------
# 管理画面API
# ------------------------------------------------------------------

def test_admin_can_grant_api_scope(app_ctx):
    _make_user(app_ctx, "boss2", is_admin=True)
    target_id = _make_user(app_ctx, "member", full=["shiftersync"])
    client = _client(app_ctx, "boss2")

    resp = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": ["shiftersync"], "api_tool_keys": ["pluslist"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool_keys"] == ["shiftersync"]
    assert body["api_tool_keys"] == ["pluslist"]


def test_admin_cannot_grant_api_scope_for_non_provider_tools(app_ctx):
    """ツール間APIを持たないツールにはAPI専用許可を付けられない。"""
    _make_user(app_ctx, "boss3", is_admin=True)
    target_id = _make_user(app_ctx, "member2")
    client = _client(app_ctx, "boss3")

    resp = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": [], "api_tool_keys": ["health_check", "shiftersync"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["api_tool_keys"] == []


def test_same_tool_in_both_lists_prefers_full(app_ctx):
    _make_user(app_ctx, "boss4", is_admin=True)
    target_id = _make_user(app_ctx, "member3")
    client = _client(app_ctx, "boss4")

    resp = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": ["pluslist"], "api_tool_keys": ["pluslist"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool_keys"] == ["pluslist"]
    assert body["api_tool_keys"] == []


# ------------------------------------------------------------------
# ToBell 連携（ツール間APIそのもの）
# ------------------------------------------------------------------

def test_to_bell_linkage_accepts_api_scope(app_ctx):
    from app.services.to_bell_integrations import has_tool_access

    _make_user(app_ctx, "tb", api=["pluslist"])
    _make_user(app_ctx, "tb_none")
    with app_ctx.app_context():
        # ToBell は公開ツールなので、API専用許可があれば紐付けを使える
        assert has_tool_access("tb", "pluslist.linkage")
        assert not has_tool_access("tb_none", "pluslist.linkage")
        # ShifterSync 由来の連携は提供元ではないので影響を受けない
        assert not has_tool_access("tb", "cloudshift.shift_update")


def test_permission_change_is_visible_immediately(app_ctx):
    """付与直後の判定がリクエスト内キャッシュで古いまま固定されないこと。"""
    from app.access_control import set_tool_access_scopes, user_has_tool_api_access
    from app.models import User
    from app.tool_api import TOOL_SCOPE_API, TOOL_SCOPE_FULL

    user_id = _make_user(app_ctx, "ss8", full=["shiftersync"])
    with app_ctx.test_request_context("/"):
        user = User.query.filter_by(username="ss8").first()
        assert not user_has_tool_api_access("pluslist", "shiftersync", user)
        set_tool_access_scopes(
            user_id,
            {"shiftersync": TOOL_SCOPE_FULL, "pluslist": TOOL_SCOPE_API},
            granted_by="system",
        )
        assert user_has_tool_api_access("pluslist", "shiftersync", user)


def test_omitting_api_tool_keys_keeps_existing_api_grants(app_ctx):
    """api_tool_keys を送らない古いクライアントがAPI専用許可を消さないこと。"""
    _make_user(app_ctx, "boss5", is_admin=True)
    target_id = _make_user(app_ctx, "member4", full=["shiftersync"], api=["pluslist"])
    client = _client(app_ctx, "boss5")

    resp = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": ["shiftersync", "leave_mgr"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["api_tool_keys"] == ["pluslist"]


def test_empty_api_tool_keys_revokes_api_grants(app_ctx):
    _make_user(app_ctx, "boss6", is_admin=True)
    target_id = _make_user(app_ctx, "member5", full=["shiftersync"], api=["pluslist"])
    client = _client(app_ctx, "boss6")

    resp = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": ["shiftersync"], "api_tool_keys": []},
    )
    assert resp.status_code == 200
    assert resp.get_json()["api_tool_keys"] == []


# ------------------------------------------------------------------
# 個別付与 > グループ付与 の優先順位
# ------------------------------------------------------------------

def _add_group_rule(app_ctx, tool_key: str):
    from app.models import AccessBranch, GroupToolPermission, db

    with app_ctx.app_context():
        branch = AccessBranch.query.first()
        db.session.add(
            GroupToolPermission(
                tool_key=tool_key,
                branch_id=branch.id,
                office_id=None,
                department_id=None,
            )
        )
        db.session.commit()


def test_individual_api_grant_overrides_group_full_grant(app_ctx):
    """グループ付与で通常許可でも、個人のAPI専用許可があれば本体は開けない。"""
    from app.access_control import get_accessible_nav_items, user_has_tool_access
    from app.models import User

    _add_group_rule(app_ctx, "pluslist")
    _make_user(app_ctx, "grp", full=["shiftersync"], api=["pluslist"])

    with app_ctx.app_context():
        user = User.query.filter_by(username="grp").first()
        assert not user_has_tool_access("pluslist", user)
        keys = {item.get("key") for item in get_accessible_nav_items(user)}
        assert "pluslist" not in keys

    client = _client(app_ctx, "grp")
    assert client.get("/tools/pluslist/").status_code == 403
    assert client.get("/tools/pluslist/api/employees").status_code == 403
    # ツール間APIは使えるが、項目は絞られる
    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    assert set(resp.get_json()[0]) == {
        "employee_number",
        "employee_name",
        "office_name",
        "job_title",
    }


def test_group_full_grant_alone_still_opens_the_tool(app_ctx):
    """個別付与が無ければ、従来どおりグループ付与で本体を使える。"""
    from app.access_control import user_has_tool_access
    from app.models import User

    _add_group_rule(app_ctx, "pluslist")
    _make_user(app_ctx, "grp2")

    with app_ctx.app_context():
        user = User.query.filter_by(username="grp2").first()
        assert user_has_tool_access("pluslist", user)

    client = _client(app_ctx, "grp2")
    assert client.get("/tools/pluslist/").status_code == 200
    resp = client.get("/tools/pluslist/api/search_employee?q=山田")
    assert resp.status_code == 200
    assert "address1" in resp.get_json()[0]


def test_individual_full_grant_also_wins_over_group(app_ctx):
    """個別付与の通常許可はグループ付与と同じ結果（矛盾しないことの確認）。"""
    from app.access_control import user_has_tool_access
    from app.models import User

    _add_group_rule(app_ctx, "pluslist")
    _make_user(app_ctx, "grp3", full=["pluslist"])
    with app_ctx.app_context():
        user = User.query.filter_by(username="grp3").first()
        assert user_has_tool_access("pluslist", user)


def test_batch_access_helper_honours_the_same_precedence(app_ctx):
    """一斉通知の宛先解決も同じ優先順位で判定する。"""
    from app.access_control import usernames_with_tool_access
    from app.models import User

    _add_group_rule(app_ctx, "pluslist")
    _make_user(app_ctx, "grp4", full=["shiftersync"], api=["pluslist"])
    _make_user(app_ctx, "grp5")

    from app.access_control import user_has_tool_access

    with app_ctx.app_context():
        allowed = usernames_with_tool_access(["grp4", "grp5"], "pluslist")
        assert allowed == {"grp5"}
        # ツール間API経由の判定なら grp4 も対象になる
        via = usernames_with_tool_access(
            ["grp4", "grp5"], "pluslist", via_tool="to_bell"
        )
        assert via == {"grp4", "grp5"}

        # まとめ判定と1人ずつの判定が食い違わないこと
        for username in ("grp4", "grp5"):
            user = User.query.filter_by(username=username).first()
            assert (username in allowed) == user_has_tool_access("pluslist", user)


# ------------------------------------------------------------------
# 管理画面に出す実効状態が、実際の判定と一致すること
# ------------------------------------------------------------------

def test_described_state_matches_actual_access(app_ctx):
    """describe_tool_access() の state と user_has_tool_access() が食い違わない。

    画面表示と実挙動がずれると権限事故になるので、組み合わせを総当たりで突き合わせる。
    """
    from app.access_control import (
        _user_tool_scopes,
        describe_tool_access,
        _group_granted_tool_keys,
        is_admin_user,
        tool_requires_permission,
        user_has_tool_access,
    )
    from app.models import ToolSettings, User, db

    _add_group_rule(app_ctx, "pluslist")
    _add_group_rule(app_ctx, "leave_mgr")
    cases = [
        ("m_none", {}),
        ("m_full", {"full": ["pluslist"]}),
        ("m_api", {"api": ["pluslist"]}),
        ("m_api_ss", {"full": ["shiftersync"], "api": ["pluslist"]}),
        ("m_group_only", {}),
        ("m_admin", {"is_admin": True}),
    ]
    for username, kwargs in cases:
        _make_user(app_ctx, username, **kwargs)

    with app_ctx.app_context():
        # 非表示ツールのケースも混ぜる
        row = db.session.get(ToolSettings, "health_check")
        if row is None:
            row = ToolSettings(tool_key="health_check")
            db.session.add(row)
        row.is_visible = False
        row.access_type = "sensitive"
        db.session.commit()

        tool_keys = ["pluslist", "siteplus", "leave_mgr", "shiftersync", "health_check", "calc"]
        for username, _ in cases:
            user = User.query.filter_by(username=username).first()
            scopes = _user_tool_scopes(user)
            group_keys = _group_granted_tool_keys(user)
            admin = is_admin_user(user)
            for tool_key in tool_keys:
                described = describe_tool_access(
                    tool_key,
                    is_admin=admin,
                    individual_scope=scopes.get(tool_key),
                    by_group=tool_key in group_keys,
                    requires_permission=tool_requires_permission(tool_key),
                    is_visible=tool_key != "health_check",
                )
                actual = user_has_tool_access(tool_key, user)
                assert (described["state"] == "full") == actual, (
                    username,
                    tool_key,
                    described,
                    actual,
                )


def test_admin_api_exposes_effective_state(app_ctx):
    _add_group_rule(app_ctx, "pluslist")
    _make_user(app_ctx, "boss7", is_admin=True)
    target_id = _make_user(app_ctx, "member6", full=["shiftersync"], api=["pluslist"])
    client = _client(app_ctx, "boss7")

    resp = client.get(f"/tools/user_management/api/users/{target_id}/tools")
    assert resp.status_code == 200
    access = resp.get_json()["tool_access"]
    assert access["pluslist"]["state"] == "api"
    assert access["pluslist"]["source"] == "individual"
    assert access["pluslist"]["by_group"] is True
    assert access["pluslist"]["group_overridden"] is True
    assert access["shiftersync"]["state"] == "full"
    assert access["shiftersync"]["source"] == "individual"
    assert access["siteplus"]["state"] == "none"
    # 公開ツールは要許可ではないので含めない（画面側で補う）
    assert "calc" not in access


def test_admin_ui_covers_every_access_state():
    """describe_tool_access() が返しうる (state, source) を管理画面が全部描けること。

    片方だけ増やすと「バッジが出ない／⛔許可なしと誤表示」になるので、
    テキストレベルで対応表の網羅を確かめる。
    """
    import re

    html = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    block = html.split("const PERM_STATE_VIEW = ", 1)[1].split("\n};", 1)[0]

    expected = {
        "full": {"admin", "public", "individual", "group"},
        "api": {"individual"},
        "none": {"hidden", "none"},
    }
    # state ごとのブロックへ切り分ける
    positions = {}
    for state in expected:
        match = re.search(rf"^\s*{state}:\s*\{{", block, re.MULTILINE)
        assert match, f"PERM_STATE_VIEW に state={state} が無い"
        positions[state] = match.start()
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    for index, (state, start) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else len(block)
        section = block[start:end]
        for source in expected[state]:
            assert re.search(rf"^\s*{source}:\s*\{{", section, re.MULTILINE), (
                f"PERM_STATE_VIEW[{state}] に source={source} が無い"
            )


def test_describe_tool_access_states_are_exhaustive():
    """入力の組み合わせから出る (state, source) が、想定した集合に収まること。"""
    from itertools import product

    from app.access_control import describe_tool_access

    seen = set()
    for is_admin, scope, by_group, requires, visible in product(
        [True, False], [None, "full", "api"], [True, False], [True, False], [True, False]
    ):
        result = describe_tool_access(
            "pluslist",
            is_admin=is_admin,
            individual_scope=scope,
            by_group=by_group,
            requires_permission=requires,
            is_visible=visible,
        )
        seen.add((result["state"], result["source"]))

    assert seen == {
        ("full", "admin"),
        ("full", "public"),
        ("full", "individual"),
        ("full", "group"),
        ("api", "individual"),
        ("none", "hidden"),
        ("none", "none"),
    }
