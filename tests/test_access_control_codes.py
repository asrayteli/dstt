"""Integration tests for the office-code / group-permission access control layer."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _stub_optional_deps():
    if "openpyxl" in sys.modules:
        return

    openpyxl = types.ModuleType("openpyxl")
    openpyxl.Workbook = object
    openpyxl.load_workbook = lambda *_args, **_kwargs: None

    styles = types.ModuleType("openpyxl.styles")
    styles.Font = object
    styles.PatternFill = object
    styles.Border = object
    styles.Side = object
    styles.Alignment = object

    sys.modules["openpyxl"] = openpyxl
    sys.modules["openpyxl.styles"] = styles

    if "qrcode" not in sys.modules:
        qrcode = types.ModuleType("qrcode")
        qrcode.make = lambda *_args, **_kwargs: types.SimpleNamespace(save=lambda *_a, **_k: None)
        sys.modules["qrcode"] = qrcode


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    _stub_optional_deps()
    from app import create_app
    from app.models import db

    db_path = tmp_path / "test.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test",
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _mk_user(**kwargs):
    from app.models import User
    u = User(username=kwargs.pop("username"), password_hash="x", **kwargs)
    return u


def test_office_code_data_access(app_ctx):
    from app.models import db, AccessBranch, AccessOffice, UserAccessibleOffice
    from app.access_control import user_office_codes, user_can_access_office_code

    with app_ctx.app_context():
        b = AccessBranch(name="東京支店", code="T01")
        db.session.add(b)
        db.session.flush()
        o1 = AccessOffice(branch_id=b.id, name="新宿営業所", code="S01")
        o2 = AccessOffice(branch_id=b.id, name="渋谷営業所", code="S02")
        o3 = AccessOffice(branch_id=b.id, name="品川営業所", code="S03")
        db.session.add_all([o1, o2, o3])
        db.session.flush()

        user = _mk_user(username="tanaka", branch_id=b.id, office_id=o1.id)
        db.session.add(user)
        db.session.flush()

        db.session.add(UserAccessibleOffice(user_id=user.id, office_id=o2.id))
        db.session.commit()

        codes = user_office_codes(user)
        assert "S01" in codes
        assert "S02" in codes
        assert "S03" not in codes

        assert user_can_access_office_code("S01", user)
        assert user_can_access_office_code("S02", user)
        assert not user_can_access_office_code("S03", user)
        assert not user_can_access_office_code(None, user)


def test_admin_sees_everything(app_ctx):
    from app.models import db
    from app.access_control import user_can_access_office_code, user_has_tool_access

    with app_ctx.app_context():
        admin = _mk_user(username="admin", is_admin=True)
        db.session.add(admin)
        db.session.commit()

        assert user_can_access_office_code("ANY", admin)
        assert user_can_access_office_code(None, admin)
        assert user_has_tool_access("siteplus", admin)
        assert user_has_tool_access("leave_mgr", admin)


def test_group_tool_permission_branch_only(app_ctx):
    from app.models import db, AccessBranch, AccessOffice, GroupToolPermission
    from app.access_control import user_has_tool_access

    with app_ctx.app_context():
        b = AccessBranch(name="東京支店", code="T01")
        db.session.add(b)
        db.session.flush()
        o = AccessOffice(branch_id=b.id, name="新宿営業所", code="S01")
        db.session.add(o)
        db.session.flush()

        user = _mk_user(username="taro", branch_id=b.id, office_id=o.id)
        other = _mk_user(username="jiro")
        db.session.add_all([user, other])
        db.session.flush()

        rule = GroupToolPermission(
            tool_key="siteplus",
            branch_id=b.id,
            office_id=None,
            department_id=None,
        )
        db.session.add(rule)
        db.session.commit()

        assert user_has_tool_access("siteplus", user)
        assert not user_has_tool_access("siteplus", other)


def test_group_tool_permission_branch_and_department(app_ctx):
    from app.models import db, AccessBranch, AccessOffice, AccessDepartment, GroupToolPermission
    from app.access_control import user_has_tool_access

    with app_ctx.app_context():
        b = AccessBranch(name="東京支店", code="T01")
        db.session.add(b)
        db.session.flush()
        o = AccessOffice(branch_id=b.id, name="新宿営業所", code="S01")
        db.session.add(o)
        db.session.flush()
        d_sales = AccessDepartment(office_id=o.id, name="営業担当")
        d_ops = AccessDepartment(office_id=o.id, name="運用担当")
        db.session.add_all([d_sales, d_ops])
        db.session.flush()

        sales_user = _mk_user(
            username="sales",
            branch_id=b.id,
            office_id=o.id,
            department_id=d_sales.id,
        )
        ops_user = _mk_user(
            username="ops",
            branch_id=b.id,
            office_id=o.id,
            department_id=d_ops.id,
        )
        db.session.add_all([sales_user, ops_user])
        db.session.flush()

        rule = GroupToolPermission(
            tool_key="leave_mgr",
            branch_id=b.id,
            office_id=None,
            department_id=d_sales.id,
        )
        db.session.add(rule)
        db.session.commit()

        assert user_has_tool_access("leave_mgr", sales_user)
        assert not user_has_tool_access("leave_mgr", ops_user)


def test_unassigned_user_has_no_access(app_ctx):
    from app.models import db
    from app.access_control import (
        user_has_tool_access,
        user_can_access_office_code,
        get_accessible_nav_items,
    )

    with app_ctx.app_context():
        user = _mk_user(username="nobody")
        db.session.add(user)
        db.session.commit()

        assert not user_has_tool_access("siteplus", user)
        assert not user_has_tool_access("leave_mgr", user)
        assert not user_can_access_office_code("ANY", user)
        nav = get_accessible_nav_items(user)
        sensitive_keys = {item["tool_key"] for item in nav if item.get("category") == "sensitive"}
        assert not sensitive_keys


def test_branch_code_and_office_code_persistence(app_ctx):
    from app.models import db, AccessBranch, AccessOffice

    with app_ctx.app_context():
        b = AccessBranch(name="大阪支店", code="O01")
        db.session.add(b)
        db.session.flush()
        o = AccessOffice(branch_id=b.id, name="梅田営業所", code="U01")
        db.session.add(o)
        db.session.commit()

        reread = AccessBranch.query.filter_by(code="O01").first()
        assert reread is not None
        assert reread.name == "大阪支店"

        reread_o = AccessOffice.query.filter_by(code="U01").first()
        assert reread_o is not None
        assert reread_o.name == "梅田営業所"


def test_branch_only_user_inherits_all_offices_under_branch(app_ctx):
    from app.models import db, AccessBranch, AccessOffice
    from app.access_control import user_office_codes, user_can_access_office_code

    with app_ctx.app_context():
        b = AccessBranch(name="東京支店", code="T01")
        other_b = AccessBranch(name="大阪支店", code="O01")
        db.session.add_all([b, other_b])
        db.session.flush()

        o1 = AccessOffice(branch_id=b.id, name="新宿営業所", code="S01")
        o2 = AccessOffice(branch_id=b.id, name="渋谷営業所", code="S02")
        outside = AccessOffice(branch_id=other_b.id, name="梅田営業所", code="U01")
        db.session.add_all([o1, o2, outside])
        db.session.flush()

        branch_user = _mk_user(username="bm", branch_id=b.id)
        db.session.add(branch_user)
        db.session.commit()

        codes = user_office_codes(branch_user)
        assert codes == {"S01", "S02"}
        assert user_can_access_office_code("S01", branch_user)
        assert user_can_access_office_code("S02", branch_user)
        assert not user_can_access_office_code("U01", branch_user)


def test_user_with_office_does_not_auto_inherit_branch_offices(app_ctx):
    from app.models import db, AccessBranch, AccessOffice
    from app.access_control import user_office_codes

    with app_ctx.app_context():
        b = AccessBranch(name="東京支店", code="T01")
        db.session.add(b)
        db.session.flush()
        o1 = AccessOffice(branch_id=b.id, name="新宿営業所", code="S01")
        o2 = AccessOffice(branch_id=b.id, name="渋谷営業所", code="S02")
        db.session.add_all([o1, o2])
        db.session.flush()

        # branch + 主営業所 = 主営業所のみ（自動展開しない）
        u = _mk_user(username="staff", branch_id=b.id, office_id=o1.id)
        db.session.add(u)
        db.session.commit()

        assert user_office_codes(u) == {"S01"}


def test_dynamic_sensitive_tool_can_be_assigned_by_admin_api(app_ctx):
    from app.models import db, User, UserToolPermission

    with app_ctx.app_context():
        admin = User(username="admin", password_hash="x", is_admin=True)
        user = User(username="target", password_hash="x")
        db.session.add_all([admin, user])
        db.session.commit()
        target_id = user.id

    client = app_ctx.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "admin"
        session["_fresh"] = True

    setting = client.put(
        "/tools/user_management/api/tool-settings/datecalc",
        json={"access_type": "sensitive"},
    )
    assert setting.status_code == 200

    update = client.put(
        f"/tools/user_management/api/users/{target_id}/tools",
        json={"tool_keys": ["datecalc"]},
    )
    assert update.status_code == 200

    with app_ctx.app_context():
        assert UserToolPermission.query.filter_by(user_id=target_id, tool_key="datecalc").one()


def test_tool_settings_reject_malformed_category_id(app_ctx):
    from app.models import db, User

    with app_ctx.app_context():
        admin = User(username="admin", password_hash="x", is_admin=True)
        db.session.add(admin)
        db.session.commit()

    client = app_ctx.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "admin"
        session["_fresh"] = True

    single = client.put(
        "/tools/user_management/api/tool-settings/datecalc",
        json={"category_id": "abc"},
    )
    assert single.status_code == 400

    bulk = client.put(
        "/tools/user_management/api/tool-settings/bulk",
        json={"updates": [{"tool_key": "datecalc", "category_id": "abc"}]},
    )
    assert bulk.status_code == 400


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = user.get_id()
        sess["_fresh"] = True


def test_car_inspe_is_blocked_by_sensitive_tool_guard(app_ctx):
    from app.models import db, User

    with app_ctx.app_context():
        user = _mk_user(username="driver_admin")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = app_ctx.test_client()
    with app_ctx.app_context():
        user = db.session.get(User, user_id)
        _login(client, user)

    response = client.get("/tools/car_inspe/")
    assert response.status_code == 403


def test_dynamic_sensitive_tool_settings_drive_grant_apis(app_ctx):
    from app.models import db, AccessBranch, AccessOffice, GroupToolPermission, ToolSettings, User, UserToolPermission

    with app_ctx.app_context():
        admin = _mk_user(username="admin", is_admin=True)
        staff = _mk_user(username="staff")
        branch = AccessBranch(name="Main", code="B01")
        db.session.add_all([admin, staff, branch])
        db.session.flush()
        office = AccessOffice(branch_id=branch.id, name="Office", code="S01")
        db.session.add(office)
        setting = db.session.get(ToolSettings, "powervote")
        if setting is None:
            setting = ToolSettings(tool_key="powervote")
            db.session.add(setting)
        setting.access_type = "sensitive"
        db.session.commit()
        admin_id = admin.id
        staff_id = staff.id
        branch_id = branch.id

    client = app_ctx.test_client()
    with app_ctx.app_context():
        _login(client, db.session.get(User, admin_id))

    response = client.put(
        f"/tools/user_management/api/users/{staff_id}/tools",
        json={"tool_keys": ["powervote"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    with app_ctx.app_context():
        assert UserToolPermission.query.filter_by(user_id=staff_id, tool_key="powervote").first() is not None

    group_response = client.post(
        "/tools/user_management/api/group-tool-permissions",
        json={"tool_key": "powervote", "branch_id": branch_id},
    )
    assert group_response.status_code == 200, group_response.get_data(as_text=True)

    with app_ctx.app_context():
        assert GroupToolPermission.query.filter_by(tool_key="powervote", branch_id=branch_id).first() is not None
