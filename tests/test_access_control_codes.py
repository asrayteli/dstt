"""Integration tests for the office-code / group-permission access control layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "test.db"
    monkeypatch.chdir(tmp_path)

    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"

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
