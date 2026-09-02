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


def test_promoting_api_grant_to_full_replaces_the_row(app_ctx):
    from app.access_control import set_tool_access
    from app.models import User
    from app.tool_api import normalize_tool_scope

    user_id = _make_user(app_ctx, "ss7", api=["pluslist"])
    with app_ctx.app_context():
        set_tool_access(user_id, ["pluslist"], granted_by="system")
        user = User.query.filter_by(username="ss7").first()
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
