# app/tools/user_management.py

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from app.models import (
    db,
    User,
    AccessBranch,
    AccessOffice,
    AccessDepartment,
    UserToolPermission,
)
from app.access_control import (
    LEGACY_ADMIN_USERNAME,
    TOOL_ACCESS_CATEGORIES,
    is_admin_user,
    set_tool_access,
)
from app.navigation import NAV_ITEMS
from app.announcement_store import (
    list_announcements,
    create_announcement,
    delete_announcement,
)
import re
import secrets
import string

user_management_bp = Blueprint("user_management", __name__, url_prefix="/tools/user_management")


def is_admin():
    """管理者権限チェック。新しいis_adminフラグ + レガシーハードコード両対応。"""
    return is_admin_user()


def generate_random_password(length=12):
    """安全なランダムパスワードを生成"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(alphabet) for i in range(length))
    return password


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name or "unknown",
        "is_admin": bool(user.is_admin) or user.username == LEGACY_ADMIN_USERNAME,
        "is_legacy_admin": user.username == LEGACY_ADMIN_USERNAME,
        "branch_id": user.branch_id,
        "office_id": user.office_id,
        "department_id": user.department_id,
        "branch_name": user.branch.name if user.branch else None,
        "office_name": user.user_office.name if user.user_office else None,
        "department_name": user.department.name if user.department else None,
        "tool_keys": [p.tool_key for p in user.tool_permissions],
    }


# ============================================================
# ユーザーCRUD
# ============================================================

@user_management_bp.route("/api/users", methods=["GET"])
@login_required
def get_users():
    """全ユーザー一覧取得（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    users = User.query.order_by(User.username).all()
    return jsonify({"users": [_serialize_user(u) for u in users]})


@user_management_bp.route("/api/users", methods=["POST"])
@login_required
def create_user():
    """新規ユーザー作成（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    username = data.get('username', '').strip()
    name = data.get('name', '').strip()
    password = data.get('password', '').strip()

    if not username:
        return jsonify({"error": "ユーザーIDは空にできません"}), 400
    if not name:
        return jsonify({"error": "日本語名は空にできません"}), 400
    if not password:
        return jsonify({"error": "パスワードは空にできません"}), 400
    if not re.match(r'^[a-zA-Z0-9]+$', username):
        return jsonify({"error": "ユーザーIDは英数字のみ使用可能です"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "そのユーザーIDは既に存在します"}), 400

    try:
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            name=name,
            is_admin=bool(data.get('is_admin', False)),
            branch_id=data.get('branch_id'),
            office_id=data.get('office_id'),
            department_id=data.get('department_id'),
        )
        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"ユーザー「{username}」（{name}）を作成しました",
            "user": _serialize_user(new_user),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"ユーザー作成に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
def delete_user(user_id):
    """ユーザー削除（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404
    if user.username == current_user.username:
        return jsonify({"error": "自分自身を削除することはできません"}), 400
    if user.username == LEGACY_ADMIN_USERNAME:
        return jsonify({"error": "初期管理者は削除できません"}), 400

    try:
        username = user.username
        name = user.name or "unknown"
        db.session.delete(user)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"ユーザー「{username}」（{name}）を削除しました",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"ユーザー削除に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/users/<int:user_id>/password", methods=["PUT"])
@login_required
def change_password(user_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    data = request.json or {}
    new_password = data.get('password', '').strip()
    generate_auto = data.get('generate_auto', False)

    if generate_auto:
        new_password = generate_random_password()
    if not new_password:
        return jsonify({"error": "パスワードは空にできません"}), 400

    try:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"ユーザー「{user.username}」のパスワードを変更しました",
            "new_password": new_password,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"パスワード変更に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/users/<int:user_id>/profile", methods=["PUT"])
@login_required
def update_user_profile(user_id):
    """ユーザーの支店/営業所/担当/管理者フラグを更新"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    data = request.json or {}

    def _coerce_id(value):
        if value in (None, "", "null", "None"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    branch_id = _coerce_id(data.get("branch_id"))
    office_id = _coerce_id(data.get("office_id"))
    department_id = _coerce_id(data.get("department_id"))

    # 階層整合性チェック
    if office_id is not None:
        office = AccessOffice.query.get(office_id)
        if not office:
            return jsonify({"error": "指定された営業所は存在しません"}), 400
        if branch_id is None:
            branch_id = office.branch_id
        elif office.branch_id != branch_id:
            return jsonify({"error": "営業所と支店の組み合わせが不正です"}), 400

    if department_id is not None:
        department = AccessDepartment.query.get(department_id)
        if not department:
            return jsonify({"error": "指定された担当は存在しません"}), 400
        if office_id is None:
            office_id = department.office_id
            office = AccessOffice.query.get(office_id)
            if branch_id is None and office is not None:
                branch_id = office.branch_id
        elif department.office_id != office_id:
            return jsonify({"error": "担当と営業所の組み合わせが不正です"}), 400

    if "name" in data:
        name = str(data.get("name") or "").strip()
        if name:
            user.name = name

    if "is_admin" in data:
        if user.username == LEGACY_ADMIN_USERNAME and not data.get("is_admin"):
            return jsonify({"error": "初期管理者の権限は外せません"}), 400
        user.is_admin = bool(data.get("is_admin"))

    user.branch_id = branch_id
    user.office_id = office_id
    user.department_id = department_id

    try:
        db.session.commit()
        return jsonify({"success": True, "user": _serialize_user(user)})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/users/<int:user_id>/tools", methods=["GET"])
@login_required
def get_user_tools(user_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    return jsonify({
        "user_id": user.id,
        "username": user.username,
        "tool_keys": [p.tool_key for p in user.tool_permissions],
    })


@user_management_bp.route("/api/users/<int:user_id>/tools", methods=["PUT"])
@login_required
def update_user_tools(user_id):
    """ユーザーに付与するツール（sensitiveカテゴリ）を一括更新"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    data = request.json or {}
    tool_keys = data.get("tool_keys")
    if not isinstance(tool_keys, list):
        return jsonify({"error": "tool_keysはリストで指定してください"}), 400

    # sensitiveカテゴリのみ付与対象（publicは常時アクセス可能）
    sensitive_keys = {k for k, c in TOOL_ACCESS_CATEGORIES.items() if c == "sensitive"}
    desired = {str(k).strip() for k in tool_keys if str(k).strip() in sensitive_keys}

    try:
        set_tool_access(user.id, desired, granted_by=current_user.username)
        db.session.refresh(user)
        return jsonify({
            "success": True,
            "user_id": user.id,
            "tool_keys": [p.tool_key for p in user.tool_permissions],
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"ツール権限の更新に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/generate-password", methods=["GET"])
@login_required
def api_generate_password():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    return jsonify({"password": generate_random_password()})


# ============================================================
# 支店 / 営業所 / 担当 マスタ
# ============================================================

@user_management_bp.route("/api/organization", methods=["GET"])
@login_required
def get_organization():
    """組織階層（支店 > 営業所 > 担当）をツリーで取得"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    branches = AccessBranch.query.order_by(AccessBranch.name).all()
    return jsonify({
        "branches": [b.to_dict(include_children=True) for b in branches]
    })


@user_management_bp.route("/api/branches", methods=["POST"])
@login_required
def create_branch():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "支店名は必須です"}), 400
    if AccessBranch.query.filter_by(name=name).first():
        return jsonify({"error": "同名の支店が既に存在します"}), 400

    branch = AccessBranch(name=name)
    db.session.add(branch)
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict(include_children=True)})


@user_management_bp.route("/api/branches/<int:branch_id>", methods=["PUT"])
@login_required
def update_branch(branch_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    branch = AccessBranch.query.get(branch_id)
    if not branch:
        return jsonify({"error": "支店が見つかりません"}), 404

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "支店名は必須です"}), 400

    dup = AccessBranch.query.filter(
        AccessBranch.name == name,
        AccessBranch.id != branch_id,
    ).first()
    if dup:
        return jsonify({"error": "同名の支店が既に存在します"}), 400

    branch.name = name
    db.session.commit()
    return jsonify({"success": True, "branch": branch.to_dict(include_children=True)})


@user_management_bp.route("/api/branches/<int:branch_id>", methods=["DELETE"])
@login_required
def delete_branch(branch_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    branch = AccessBranch.query.get(branch_id)
    if not branch:
        return jsonify({"error": "支店が見つかりません"}), 404

    # 所属ユーザーを解除
    User.query.filter_by(branch_id=branch_id).update(
        {"branch_id": None, "office_id": None, "department_id": None}
    )
    db.session.delete(branch)
    db.session.commit()
    return jsonify({"success": True})


@user_management_bp.route("/api/offices", methods=["POST"])
@login_required
def create_office():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    branch_id = data.get("branch_id")
    if not name or not branch_id:
        return jsonify({"error": "支店と営業所名は必須です"}), 400

    branch = AccessBranch.query.get(branch_id)
    if not branch:
        return jsonify({"error": "支店が見つかりません"}), 404
    if AccessOffice.query.filter_by(branch_id=branch_id, name=name).first():
        return jsonify({"error": "同支店内に同名の営業所があります"}), 400

    office = AccessOffice(branch_id=branch_id, name=name)
    db.session.add(office)
    db.session.commit()
    return jsonify({"success": True, "office": office.to_dict(include_children=True)})


@user_management_bp.route("/api/offices/<int:office_id>", methods=["PUT"])
@login_required
def update_office(office_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    office = AccessOffice.query.get(office_id)
    if not office:
        return jsonify({"error": "営業所が見つかりません"}), 404

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "営業所名は必須です"}), 400

    dup = AccessOffice.query.filter(
        AccessOffice.branch_id == office.branch_id,
        AccessOffice.name == name,
        AccessOffice.id != office_id,
    ).first()
    if dup:
        return jsonify({"error": "同支店内に同名の営業所があります"}), 400

    office.name = name
    db.session.commit()
    return jsonify({"success": True, "office": office.to_dict(include_children=True)})


@user_management_bp.route("/api/offices/<int:office_id>", methods=["DELETE"])
@login_required
def delete_office(office_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    office = AccessOffice.query.get(office_id)
    if not office:
        return jsonify({"error": "営業所が見つかりません"}), 404

    User.query.filter_by(office_id=office_id).update(
        {"office_id": None, "department_id": None}
    )
    db.session.delete(office)
    db.session.commit()
    return jsonify({"success": True})


@user_management_bp.route("/api/departments", methods=["POST"])
@login_required
def create_department():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    office_id = data.get("office_id")
    if not name or not office_id:
        return jsonify({"error": "営業所と担当名は必須です"}), 400

    office = AccessOffice.query.get(office_id)
    if not office:
        return jsonify({"error": "営業所が見つかりません"}), 404
    if AccessDepartment.query.filter_by(office_id=office_id, name=name).first():
        return jsonify({"error": "同営業所内に同名の担当があります"}), 400

    department = AccessDepartment(office_id=office_id, name=name)
    db.session.add(department)
    db.session.commit()
    return jsonify({"success": True, "department": department.to_dict()})


@user_management_bp.route("/api/departments/<int:department_id>", methods=["PUT"])
@login_required
def update_department(department_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    department = AccessDepartment.query.get(department_id)
    if not department:
        return jsonify({"error": "担当が見つかりません"}), 404

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "担当名は必須です"}), 400

    dup = AccessDepartment.query.filter(
        AccessDepartment.office_id == department.office_id,
        AccessDepartment.name == name,
        AccessDepartment.id != department_id,
    ).first()
    if dup:
        return jsonify({"error": "同営業所内に同名の担当があります"}), 400

    department.name = name
    db.session.commit()
    return jsonify({"success": True, "department": department.to_dict()})


@user_management_bp.route("/api/departments/<int:department_id>", methods=["DELETE"])
@login_required
def delete_department(department_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    department = AccessDepartment.query.get(department_id)
    if not department:
        return jsonify({"error": "担当が見つかりません"}), 404

    User.query.filter_by(department_id=department_id).update({"department_id": None})
    db.session.delete(department)
    db.session.commit()
    return jsonify({"success": True})


# ============================================================
# ツールカタログ
# ============================================================

@user_management_bp.route("/api/tools", methods=["GET"])
@login_required
def get_tools_catalog():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    items = []
    for nav in NAV_ITEMS:
        key = nav.get("key") or nav.get("href", "").strip("/").rsplit("/", 1)[-1]
        items.append({
            "key": key,
            "label": nav.get("label"),
            "icon": nav.get("icon"),
            "description": nav.get("description"),
            "href": nav.get("href"),
            "category": TOOL_ACCESS_CATEGORIES.get(key, "public"),
        })
    return jsonify({"tools": items})


# ============================================================
# 管理者ページ（HTML）
# ============================================================

@user_management_bp.route("/admin", methods=["GET"])
@login_required
def admin_page():
    if not is_admin():
        return ("管理者権限が必要です", 403)
    return render_template("admin.html")


@user_management_bp.route("/announcements", methods=["GET"])
@login_required
def announcement_admin_page():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    return render_template("admin_announcements.html")


@user_management_bp.route("/api/announcements", methods=["GET"])
@login_required
def get_announcements():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    return jsonify({"announcements": list_announcements()})


@user_management_bp.route("/api/announcements", methods=["POST"])
@login_required
def post_announcement():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    title = str(data.get("title", "")).strip()
    content = str(data.get("content", "")).strip()
    if not content:
        return jsonify({"error": "本文は必須です"}), 400

    item = create_announcement(
        title=title if title else "お知らせ",
        content=content,
        created_by=current_user.username,
    )
    return jsonify({"success": True, "announcement": item})


@user_management_bp.route("/api/announcements/<int:announcement_id>", methods=["DELETE"])
@login_required
def remove_announcement(announcement_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    ok = delete_announcement(announcement_id)
    if not ok:
        return jsonify({"error": "お知らせが見つかりません"}), 404
    return jsonify({"success": True})
