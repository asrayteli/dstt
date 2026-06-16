# app/tools/user_management.py

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from app.models import (
    db,
    DsttLoginLog,
    InboundMail,
    MailMessage,
    User,
    AccessBranch,
    AccessOffice,
    AccessDepartment,
    FilePostDownloadLog,
    FilePostUploadLog,
    GroupToolPermission,
    Site,
    ToolCategory,
    ToolSettings,
    ToBellTask,
    UserAccessibleOffice,
    UserActivityLog,
    UserLoginLog,
    UserToolPermission,
)
from app.services import mail_service
from app.services import mail_inbox
from app.access_control import (
    TOOL_ACCESS_CATEGORIES,
    _user_satisfies_group_rule,
    is_admin_user,
    is_legacy_admin_username,
    set_tool_access,
    tool_category,
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
from datetime import datetime, time, timedelta, timezone
from sqlalchemy import or_

user_management_bp = Blueprint("user_management", __name__, url_prefix="/tools/user_management")


def is_admin():
    """管理者権限チェック。新しいis_adminフラグ + レガシーハードコード両対応。"""
    return is_admin_user()


def generate_random_password(length=12):
    """安全なランダムパスワードを生成"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(alphabet) for i in range(length))
    return password


def _assignable_tool_keys() -> set[str]:
    keys = set()
    for nav in NAV_ITEMS:
        key = nav.get("key") or nav.get("href", "").strip("/").rsplit("/", 1)[-1]
        if key and tool_category(key) == "sensitive":
            keys.add(key)
    return keys


def _user_matching_group_rules(user: User, rules) -> dict:
    """ユーザーに適用されるグループ付与ルールを tool_key 毎にまとめて返す。

    戻り値: {tool_key: [{"branch_name": .., "office_name": .., "department_name": ..}, ...]}
    画面側で「グループ許可」のバッジと付与元の表示に利用する。
    """
    matched: dict[str, list] = {}
    for rule in rules:
        if not _user_satisfies_group_rule(user, rule):
            continue
        matched.setdefault(rule.tool_key, []).append({
            "branch_name": rule.branch.name if rule.branch else None,
            "office_name": rule.office.name if rule.office else None,
            "department_name": rule.department.name if rule.department else None,
        })
    return matched


def _serialize_user(user: User, group_rules=None) -> dict:
    extra_offices = (
        UserAccessibleOffice.query.filter_by(user_id=user.id).all()
        if user.id is not None
        else []
    )
    if group_rules is None:
        group_rules = GroupToolPermission.query.all()
    group_sources = _user_matching_group_rules(user, group_rules)
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name or "unknown",
        "is_admin": is_admin_user(user),
        "is_legacy_admin": is_legacy_admin_username(user.username),
        "branch_id": user.branch_id,
        "office_id": user.office_id,
        "department_id": user.department_id,
        "branch_name": user.branch.name if user.branch else None,
        "branch_code": user.branch.code if user.branch else None,
        "office_name": user.user_office.name if user.user_office else None,
        "office_code": user.user_office.code if user.user_office else None,
        "department_name": user.department.name if user.department else None,
        "extra_office_ids": [row.office_id for row in extra_offices],
        "tool_keys": [p.tool_key for p in user.tool_permissions],
        "group_tool_keys": list(group_sources.keys()),
        "group_tool_sources": group_sources,
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
    group_rules = GroupToolPermission.query.all()
    return jsonify({
        "users": [_serialize_user(u, group_rules=group_rules) for u in users]
    })


@user_management_bp.route("/api/login-logs", methods=["GET"])
@login_required
def get_login_logs():
    """DSTT successful login history for administrators."""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    try:
        limit = int(request.args.get("limit", 500))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 1000))

    logs = (
        DsttLoginLog.query
        .order_by(DsttLoginLog.logged_in_at.desc(), DsttLoginLog.id.desc())
        .limit(limit)
        .all()
    )
    return jsonify({
        "logs": [log.to_dict() for log in logs],
        "limit": limit,
    })


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
            is_admin=bool(data.get('is_admin', False)) or is_legacy_admin_username(username),
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
    if is_legacy_admin_username(user.username):
        return jsonify({"error": "初期管理者は削除できません"}), 400
    if user.username == current_user.username:
        return jsonify({"error": "自分自身を削除することはできません"}), 400
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
        if is_legacy_admin_username(user.username) and not data.get("is_admin"):
            return jsonify({"error": "初期管理者の権限は外せません"}), 400
        user.is_admin = True if is_legacy_admin_username(user.username) else bool(data.get("is_admin"))

    user.branch_id = branch_id
    user.office_id = office_id
    user.department_id = department_id

    try:
        db.session.commit()
        return jsonify({"success": True, "user": _serialize_user(user)})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新に失敗しました: {str(e)}"}), 500


@user_management_bp.route("/api/users/<int:user_id>/offices", methods=["GET"])
@login_required
def get_user_extra_offices(user_id):
    """ユーザーの追加営業所アクセス（主営業所以外）を取得"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    rows = UserAccessibleOffice.query.filter_by(user_id=user_id).all()
    return jsonify({
        "user_id": user_id,
        "office_ids": [r.office_id for r in rows],
    })


@user_management_bp.route("/api/users/<int:user_id>/offices", methods=["PUT"])
@login_required
def update_user_extra_offices(user_id):
    """ユーザーの追加営業所アクセスを一括更新（主営業所は除いたリスト）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    data = request.json or {}
    raw_ids = data.get("office_ids")
    if not isinstance(raw_ids, list):
        return jsonify({"error": "office_idsはリストで指定してください"}), 400

    desired_ids: set[int] = set()
    for v in raw_ids:
        try:
            desired_ids.add(int(v))
        except (TypeError, ValueError):
            return jsonify({"error": f"不正なoffice_id: {v}"}), 400

    # 主営業所はここから除外する（重複防止）
    if user.office_id is not None:
        desired_ids.discard(int(user.office_id))

    # 存在確認
    if desired_ids:
        found = {
            o.id for o in AccessOffice.query.filter(AccessOffice.id.in_(desired_ids)).all()
        }
        missing = desired_ids - found
        if missing:
            return jsonify({"error": f"存在しない営業所ID: {sorted(missing)}"}), 400

    existing_rows = UserAccessibleOffice.query.filter_by(user_id=user_id).all()
    existing = {row.office_id: row for row in existing_rows}
    to_add = desired_ids - set(existing.keys())
    to_remove = set(existing.keys()) - desired_ids

    try:
        for oid in to_add:
            db.session.add(UserAccessibleOffice(user_id=user_id, office_id=oid))
        for oid in to_remove:
            db.session.delete(existing[oid])
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新に失敗しました: {str(e)}"}), 500

    rows = UserAccessibleOffice.query.filter_by(user_id=user_id).all()
    return jsonify({
        "success": True,
        "user_id": user_id,
        "office_ids": [r.office_id for r in rows],
    })


@user_management_bp.route("/api/users/<int:user_id>/tools", methods=["GET"])
@login_required
def get_user_tools(user_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    group_sources = _user_matching_group_rules(user, GroupToolPermission.query.all())
    return jsonify({
        "user_id": user.id,
        "username": user.username,
        "tool_keys": [p.tool_key for p in user.tool_permissions],
        "group_tool_keys": list(group_sources.keys()),
        "group_tool_sources": group_sources,
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
    sensitive_keys = _assignable_tool_keys()
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


def _normalize_code(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


@user_management_bp.route("/api/branches", methods=["POST"])
@login_required
def create_branch():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    code = _normalize_code(data.get("code"))
    if not name:
        return jsonify({"error": "支店名は必須です"}), 400
    if AccessBranch.query.filter_by(name=name).first():
        return jsonify({"error": "同名の支店が既に存在します"}), 400
    if code and AccessBranch.query.filter_by(code=code).first():
        return jsonify({"error": "同コードの支店が既に存在します"}), 400

    branch = AccessBranch(name=name, code=code)
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

    if "code" in data:
        code = _normalize_code(data.get("code"))
        if code:
            dup_code = AccessBranch.query.filter(
                AccessBranch.code == code,
                AccessBranch.id != branch_id,
            ).first()
            if dup_code:
                return jsonify({"error": "同コードの支店が既に存在します"}), 400
        branch.code = code

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
    code = _normalize_code(data.get("code"))
    branch_id = data.get("branch_id")
    if not name or not branch_id:
        return jsonify({"error": "支店と営業所名は必須です"}), 400

    branch = AccessBranch.query.get(branch_id)
    if not branch:
        return jsonify({"error": "支店が見つかりません"}), 404
    if AccessOffice.query.filter_by(branch_id=branch_id, name=name).first():
        return jsonify({"error": "同支店内に同名の営業所があります"}), 400
    if code and AccessOffice.query.filter_by(code=code).first():
        return jsonify({"error": "同コードの営業所が既に存在します"}), 400

    office = AccessOffice(branch_id=branch_id, name=name, code=code)
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

    if "code" in data:
        code = _normalize_code(data.get("code"))
        if code:
            dup_code = AccessOffice.query.filter(
                AccessOffice.code == code,
                AccessOffice.id != office_id,
            ).first()
            if dup_code:
                return jsonify({"error": "同コードの営業所が既に存在します"}), 400
        office.code = code

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
            "category": tool_category(key),
        })
    return jsonify({"tools": items})


# ============================================================
# グループツール権限（支店/営業所/担当スコープの一括付与）
# ============================================================


def _coerce_optional_int(value):
    if value in (None, "", "null", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_category_id(value):
    if value in (None, "", "null", "None"):
        return None, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, "category_idは数値で指定してください"


def _validate_group_scope(branch_id, office_id, department_id):
    """グループルールの支店/営業所/担当の整合性を確認。"""
    if branch_id is None and office_id is None and department_id is None:
        return "支店・営業所・担当のいずれか1つ以上を指定してください"

    if office_id is not None:
        office = AccessOffice.query.get(office_id)
        if not office:
            return "指定された営業所は存在しません"
        if branch_id is not None and office.branch_id != branch_id:
            return "営業所と支店の組み合わせが不正です"

    if department_id is not None:
        department = AccessDepartment.query.get(department_id)
        if not department:
            return "指定された担当は存在しません"
        if office_id is not None and department.office_id != office_id:
            return "担当と営業所の組み合わせが不正です"
        if branch_id is not None and department.office.branch_id != branch_id:
            return "担当と支店の組み合わせが不正です"
    return None


@user_management_bp.route("/api/group-tool-permissions", methods=["GET"])
@login_required
def list_group_tool_permissions():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    rows = GroupToolPermission.query.order_by(GroupToolPermission.tool_key).all()
    return jsonify({"permissions": [r.to_dict() for r in rows]})


@user_management_bp.route("/api/group-tool-permissions", methods=["POST"])
@login_required
def create_group_tool_permission():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    tool_key = str(data.get("tool_key", "")).strip()
    if not tool_key:
        return jsonify({"error": "tool_keyは必須です"}), 400
    sensitive_keys = _assignable_tool_keys()
    if tool_key not in sensitive_keys:
        return jsonify({"error": f"{tool_key} はグループ付与対象のツールではありません"}), 400

    branch_id = _coerce_optional_int(data.get("branch_id"))
    office_id = _coerce_optional_int(data.get("office_id"))
    department_id = _coerce_optional_int(data.get("department_id"))

    err = _validate_group_scope(branch_id, office_id, department_id)
    if err:
        return jsonify({"error": err}), 400

    dup = GroupToolPermission.query.filter_by(
        tool_key=tool_key,
        branch_id=branch_id,
        office_id=office_id,
        department_id=department_id,
    ).first()
    if dup:
        return jsonify({"error": "同じスコープの付与が既に存在します"}), 400

    row = GroupToolPermission(
        tool_key=tool_key,
        branch_id=branch_id,
        office_id=office_id,
        department_id=department_id,
        granted_by=current_user.username,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "permission": row.to_dict()})


@user_management_bp.route("/api/group-tool-permissions/<int:permission_id>", methods=["DELETE"])
@login_required
def delete_group_tool_permission(permission_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    row = GroupToolPermission.query.get(permission_id)
    if not row:
        return jsonify({"error": "付与が見つかりません"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


# ============================================================
# siteplus 管理（現場 ↔ 営業所コード）
# ============================================================


@user_management_bp.route("/api/siteplus/sites", methods=["GET"])
@login_required
def list_siteplus_sites_for_admin():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    sites = Site.query.order_by(Site.site_id).all()
    return jsonify({
        "sites": [
            {
                "id": s.id,
                "site_id": s.site_id,
                "site_name": s.site_name,
                "site_manager_last": s.site_manager_last,
                "site_manager_first": s.site_manager_first,
                "site_manager_id": s.site_manager_id,
                "site_manager_name": s.manager_name(),
                "office_code": s.office_code,
                "is_active": s.is_active,
            }
            for s in sites
        ]
    })


@user_management_bp.route("/api/siteplus/accessible-offices", methods=["GET"])
@login_required
def list_siteplus_accessible_offices():
    """管理者向け：営業所コード入力補助用に全営業所を返す。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    offices = (
        AccessOffice.query.join(AccessBranch, AccessOffice.branch_id == AccessBranch.id)
        .order_by(AccessBranch.name, AccessOffice.name)
        .all()
    )
    return jsonify({
        "offices": [
            {
                "code": o.code or "",
                "name": o.name,
                "branch_name": o.branch.name if o.branch else "",
                "branch_code": (o.branch.code or "") if o.branch else "",
            }
            for o in offices
            if o.code
        ]
    })


# ============================================================
# ツールカテゴリ管理
# ============================================================

@user_management_bp.route("/api/tool-categories", methods=["GET"])
@login_required
def get_tool_categories():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    cats = ToolCategory.query.order_by(ToolCategory.sort_order, ToolCategory.id).all()
    return jsonify({"categories": [c.to_dict(include_tools=True) for c in cats]})


@user_management_bp.route("/api/tool-categories", methods=["POST"])
@login_required
def create_tool_category():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "カテゴリ名を入力してください"}), 400
    if ToolCategory.query.filter_by(name=name).first():
        return jsonify({"error": "同じ名前のカテゴリが既に存在します"}), 400
    max_order = db.session.query(db.func.max(ToolCategory.sort_order)).scalar() or 0
    cat = ToolCategory(name=name, sort_order=max_order + 1)
    db.session.add(cat)
    db.session.commit()
    return jsonify({"category": cat.to_dict(), "message": f"カテゴリ「{name}」を作成しました"})


@user_management_bp.route("/api/tool-categories/<int:cat_id>", methods=["PUT"])
@login_required
def update_tool_category(cat_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    cat = ToolCategory.query.get(cat_id)
    if not cat:
        return jsonify({"error": "カテゴリが見つかりません"}), 404
    data = request.get_json(force=True)
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"error": "カテゴリ名を入力してください"}), 400
        dup = ToolCategory.query.filter(ToolCategory.name == name, ToolCategory.id != cat_id).first()
        if dup:
            return jsonify({"error": "同じ名前のカテゴリが既に存在します"}), 400
        cat.name = name
    if "sort_order" in data:
        cat.sort_order = int(data["sort_order"])
    db.session.commit()
    return jsonify({"category": cat.to_dict(), "message": "カテゴリを更新しました"})


@user_management_bp.route("/api/tool-categories/<int:cat_id>", methods=["DELETE"])
@login_required
def delete_tool_category(cat_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    cat = ToolCategory.query.get(cat_id)
    if not cat:
        return jsonify({"error": "カテゴリが見つかりません"}), 404
    ToolSettings.query.filter_by(category_id=cat_id).update({"category_id": None})
    db.session.delete(cat)
    db.session.commit()
    return jsonify({"message": f"カテゴリ「{cat.name}」を削除しました"})


@user_management_bp.route("/api/tool-categories/reorder", methods=["PUT"])
@login_required
def reorder_tool_categories():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    data = request.get_json(force=True)
    order = data.get("order", [])
    for i, cat_id in enumerate(order):
        cat = ToolCategory.query.get(int(cat_id))
        if cat:
            cat.sort_order = i
    db.session.commit()
    return jsonify({"message": "カテゴリの並び順を更新しました"})


# ============================================================
# ツール設定（公開/非公開 + カテゴリ割り当て）
# ============================================================

@user_management_bp.route("/api/tool-settings", methods=["GET"])
@login_required
def get_tool_settings():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    settings = {ts.tool_key: ts for ts in ToolSettings.query.all()}
    items = []
    for nav in NAV_ITEMS:
        key = nav.get("key") or nav.get("href", "").strip("/").rsplit("/", 1)[-1]
        ts = settings.get(key)
        items.append({
            "tool_key": key,
            "label": nav.get("label"),
            "icon": nav.get("icon"),
            "description": nav.get("description"),
            "access_type": ts.access_type if ts else TOOL_ACCESS_CATEGORIES.get(key, "public"),
            "is_visible": ts.is_visible if ts else True,
            "category_id": ts.category_id if ts else None,
            "category_name": ts.category.name if ts and ts.category else None,
            "sort_order": ts.sort_order if ts else 0,
        })
    return jsonify({"tools": items})


@user_management_bp.route("/api/tool-settings/<tool_key>", methods=["PUT"])
@login_required
def update_tool_setting(tool_key):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    data = request.get_json(force=True)
    ts = ToolSettings.query.get(tool_key)
    if not ts:
        ts = ToolSettings(tool_key=tool_key)
        db.session.add(ts)
    if "is_visible" in data:
        ts.is_visible = bool(data["is_visible"])
    if "access_type" in data:
        if data["access_type"] in ("public", "sensitive"):
            ts.access_type = data["access_type"]
    if "category_id" in data:
        cat_id, error = _coerce_optional_category_id(data["category_id"])
        if error:
            return jsonify({"error": error}), 400
        if cat_id is not None and not ToolCategory.query.get(cat_id):
            return jsonify({"error": "指定されたカテゴリが見つかりません"}), 400
        ts.category_id = cat_id
    if "sort_order" in data:
        ts.sort_order = int(data["sort_order"])
    db.session.commit()
    return jsonify({"tool": ts.to_dict(), "message": "ツール設定を更新しました"})


@user_management_bp.route("/api/tool-settings/bulk", methods=["PUT"])
@login_required
def bulk_update_tool_settings():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    data = request.get_json(force=True)
    updates = data.get("updates", [])
    for upd in updates:
        key = upd.get("tool_key")
        if not key:
            continue
        ts = ToolSettings.query.get(key)
        if not ts:
            ts = ToolSettings(tool_key=key)
            db.session.add(ts)
        if "is_visible" in upd:
            ts.is_visible = bool(upd["is_visible"])
        if "access_type" in upd:
            if upd["access_type"] in ("public", "sensitive"):
                ts.access_type = upd["access_type"]
        if "category_id" in upd:
            cat_id, error = _coerce_optional_category_id(upd["category_id"])
            if error:
                return jsonify({"error": error}), 400
            if cat_id is not None and not ToolCategory.query.get(cat_id):
                return jsonify({"error": "指定されたカテゴリが見つかりません"}), 400
            ts.category_id = cat_id
        if "sort_order" in upd:
            ts.sort_order = int(upd["sort_order"])
    db.session.commit()
    return jsonify({"message": f"{len(updates)}件のツール設定を更新しました"})


@user_management_bp.route("/api/siteplus/sites/<int:site_row_id>/office-code", methods=["PUT"])
@login_required
def update_siteplus_site_office_code(site_row_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    site = Site.query.get(site_row_id)
    if not site:
        return jsonify({"error": "現場が見つかりません"}), 404

    data = request.json or {}
    site.office_code = _normalize_code(data.get("office_code"))
    db.session.commit()
    return jsonify({
        "success": True,
        "site": {
            "id": site.id,
            "site_id": site.site_id,
            "office_code": site.office_code,
        },
    })


@user_management_bp.route("/api/siteplus/sites/office-code/bulk", methods=["PUT"])
@login_required
def bulk_update_siteplus_office_codes():
    """一括更新。
    インライン保存: {"items": [{"id": 1, "office_code": "112010"}, ...]}
    一括編集モーダル: {"ids": [1,2,3], "office_code": "112010"?, "is_active": bool?}
    """
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.json or {}
    items = data.get("items")
    ids = data.get("ids")

    if isinstance(items, list):
        updated = 0
        missing: list = []
        try:
            for entry in items:
                sid = _coerce_optional_int(entry.get("id")) if isinstance(entry, dict) else None
                if sid is None:
                    continue
                site = Site.query.get(sid)
                if not site:
                    missing.append(sid)
                    continue
                site.office_code = _normalize_code(entry.get("office_code"))
                updated += 1
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": f"一括更新に失敗しました: {str(e)}"}), 500
        return jsonify({"success": True, "updated": updated, "missing": missing})

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "items または ids を指定してください"}), 400

    has_office = "office_code" in data
    has_active = "is_active" in data
    if not has_office and not has_active:
        return jsonify({"error": "変更する項目がありません"}), 400

    office_value = _normalize_code(data.get("office_code")) if has_office else None
    active_value = bool(data.get("is_active")) if has_active else None

    updated = 0
    missing = []
    try:
        for raw in ids:
            sid = _coerce_optional_int(raw)
            if sid is None:
                continue
            site = Site.query.get(sid)
            if not site:
                missing.append(sid)
                continue
            if has_office:
                site.office_code = office_value
            if has_active:
                site.is_active = active_value
            updated += 1
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"一括更新に失敗しました: {str(e)}"}), 500

    return jsonify({"success": True, "updated": updated, "missing": missing})


# ============================================================
# leave_mgr 管理（カレンダー ↔ 営業所コード）
# ============================================================


@user_management_bp.route("/api/leave-mgr/calendars", methods=["GET"])
@login_required
def list_leave_mgr_calendars_for_admin():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    from app.tools.leave_mgr import (
        load_calendar_meta,
        get_calendar_office_code,
    )
    meta = load_calendar_meta() or {}
    result = []
    for cal_id, info in meta.items():
        result.append({
            "calendar_id": cal_id,
            "name": info.get("name") if isinstance(info, dict) else None,
            "office_code": get_calendar_office_code(cal_id),
        })
    result.sort(key=lambda x: x["calendar_id"])
    return jsonify({"calendars": result})


@user_management_bp.route("/api/leave-mgr/calendars/<string:calendar_id>/office-code", methods=["PUT"])
@login_required
def update_leave_mgr_calendar_office_code(calendar_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    from app.tools.leave_mgr import set_calendar_office_code

    data = request.json or {}
    code = _normalize_code(data.get("office_code"))
    ok, err = set_calendar_office_code(calendar_id, code)
    if not ok:
        return jsonify({"error": err or "更新に失敗しました"}), 400
    return jsonify({"success": True, "calendar_id": calendar_id, "office_code": code})


# ============================================================
# アクセス管理（ユーザー別ログ）
# ============================================================


JST = timezone(timedelta(hours=9))


def _dt_to_iso(value):
    return value.isoformat() if value else None


def _utc_to_jst_iso(value):
    """Serialize UTC-stored audit timestamps as JST without rewriting stored data."""
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).isoformat(timespec="seconds")


def _parse_datetime_param(value, *, end_of_day=False):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) <= 10:
            base = datetime.fromisoformat(raw)
            return datetime.combine(base.date(), time.max if end_of_day else time.min)
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _limit_param(default=100, maximum=500):
    try:
        value = int(request.args.get("limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _apply_range(query, column, *, stored_as_utc=True):
    start = _parse_datetime_param(request.args.get("from"))
    end = _parse_datetime_param(request.args.get("to"), end_of_day=True)
    if stored_as_utc:
        if start is not None:
            start = start - timedelta(hours=9)
        if end is not None:
            end = end - timedelta(hours=9)
    if start is not None:
        query = query.filter(column >= start)
    if end is not None:
        query = query.filter(column <= end)
    return query


def _tool_label_map():
    return {
        item.get("key") or str(item.get("href", "")).strip("/").rsplit("/", 1)[-1]: item.get("label")
        for item in NAV_ITEMS
    }


@user_management_bp.route("/api/access-management/users", methods=["GET"])
@login_required
def access_management_users():
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    users = User.query.order_by(User.name, User.username).all()
    return jsonify({
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "display_name": user.name or user.username,
                "label": f"{user.name or user.username}（{user.username}）",
                "is_admin": is_admin_user(user),
            }
            for user in users
        ]
    })


@user_management_bp.route("/api/access-management/all-logins", methods=["GET"])
@login_required
def access_management_all_logins():
    """ユーザー未選択時に表示する全ユーザーのDSTTログイン履歴"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    q = str(request.args.get("q") or "").strip()
    like = f"%{q}%"
    limit = _limit_param()

    query = _apply_range(DsttLoginLog.query, DsttLoginLog.logged_in_at, stored_as_utc=False)
    if q:
        query = query.filter(
            or_(
                DsttLoginLog.username.ilike(like),
                DsttLoginLog.name.ilike(like),
                DsttLoginLog.ip_address.ilike(like),
                DsttLoginLog.user_agent.ilike(like),
            )
        )
    logs = (
        query.order_by(DsttLoginLog.logged_in_at.desc(), DsttLoginLog.id.desc())
        .limit(limit)
        .all()
    )

    return jsonify({
        "logs": [log.to_dict() for log in logs],
        "limit": limit,
        "count": len(logs),
    })


@user_management_bp.route("/api/access-management/users/<int:user_id>", methods=["GET"])
@login_required
def access_management_user_detail(user_id):
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404

    username = user.username
    q = str(request.args.get("q") or "").strip()
    like = f"%{q}%"
    limit = _limit_param()
    tool_labels = _tool_label_map()

    login_query = _apply_range(
        UserLoginLog.query.filter_by(username=username),
        UserLoginLog.logged_in_at,
    )
    if q:
        login_query = login_query.filter(
            or_(
                UserLoginLog.ip_address.ilike(like),
                UserLoginLog.user_agent.ilike(like),
            )
        )
    login_logs = login_query.order_by(UserLoginLog.logged_in_at.desc()).limit(limit).all()

    activity_query = _apply_range(
        UserActivityLog.query.filter_by(username=username),
        UserActivityLog.created_at,
    )
    if q:
        activity_query = activity_query.filter(
            or_(
                UserActivityLog.tool_key.ilike(like),
                UserActivityLog.tool_label.ilike(like),
                UserActivityLog.path.ilike(like),
                UserActivityLog.endpoint.ilike(like),
            )
        )
    activity_logs = activity_query.order_by(UserActivityLog.created_at.desc()).limit(limit).all()

    task_query = _apply_range(
        ToBellTask.query.filter(
            or_(
                ToBellTask.created_by == username,
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
            )
        ),
        ToBellTask.updated_at,
    )
    if q:
        task_query = task_query.filter(
            or_(
                ToBellTask.title.ilike(like),
                ToBellTask.description.ilike(like),
                ToBellTask.status.ilike(like),
                ToBellTask.priority.ilike(like),
                ToBellTask.source_tool.ilike(like),
            )
        )
    tasks = task_query.order_by(ToBellTask.updated_at.desc()).limit(limit).all()

    upload_query = _apply_range(
        FilePostUploadLog.query.filter_by(uploader=username),
        FilePostUploadLog.uploaded_at,
    )
    if q:
        upload_query = upload_query.filter(
            or_(
                FilePostUploadLog.file_name.ilike(like),
                FilePostUploadLog.share_uid.ilike(like),
                FilePostUploadLog.display_id.ilike(like),
            )
        )
    uploads = upload_query.order_by(FilePostUploadLog.uploaded_at.desc()).limit(limit).all()

    download_query = _apply_range(
        FilePostDownloadLog.query.filter(
            or_(
                FilePostDownloadLog.uploader == username,
                FilePostDownloadLog.downloader == username,
            )
        ),
        FilePostDownloadLog.downloaded_at,
    )
    if q:
        download_query = download_query.filter(
            or_(
                FilePostDownloadLog.file_name.ilike(like),
                FilePostDownloadLog.share_uid.ilike(like),
                FilePostDownloadLog.display_id.ilike(like),
                FilePostDownloadLog.downloader.ilike(like),
                FilePostDownloadLog.downloader_label.ilike(like),
                FilePostDownloadLog.ip_address.ilike(like),
            )
        )
    downloads = download_query.order_by(FilePostDownloadLog.downloaded_at.desc()).limit(limit).all()

    login_success_count = UserLoginLog.query.filter_by(username=username, success=True).count()
    last_login = (
        UserLoginLog.query.filter_by(username=username, success=True)
        .order_by(UserLoginLog.logged_in_at.desc())
        .first()
    )
    tool_total = UserActivityLog.query.filter_by(username=username).count()
    upload_total = FilePostUploadLog.query.filter_by(uploader=username).count()
    download_total = FilePostDownloadLog.query.filter(
        or_(FilePostDownloadLog.uploader == username, FilePostDownloadLog.downloader == username)
    ).count()
    task_total = ToBellTask.query.filter(
        or_(
            ToBellTask.created_by == username,
            ToBellTask.assigned_to == username,
            ToBellTask.reviewer_id == username,
        )
    ).count()

    tool_rows = (
        db.session.query(UserActivityLog.tool_key, db.func.count(UserActivityLog.id))
        .filter(UserActivityLog.username == username)
        .group_by(UserActivityLog.tool_key)
        .order_by(db.func.count(UserActivityLog.id).desc())
        .limit(12)
        .all()
    )
    task_status_rows = (
        db.session.query(ToBellTask.status, db.func.count(ToBellTask.id))
        .filter(
            or_(
                ToBellTask.created_by == username,
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
            )
        )
        .group_by(ToBellTask.status)
        .all()
    )

    return jsonify({
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.name or user.username,
            "is_admin": is_admin_user(user),
        },
        "summary": {
            "last_login": _utc_to_jst_iso(last_login.logged_in_at) if last_login else None,
            "login_count": login_success_count,
            "tool_activity_count": tool_total,
            "to_bell_task_count": task_total,
            "filepost_upload_count": upload_total,
            "filepost_download_count": download_total,
            "top_tools": [
                {
                    "tool_key": key,
                    "tool_label": tool_labels.get(key, key),
                    "count": count,
                }
                for key, count in tool_rows
            ],
            "task_status_counts": [
                {"status": status or "unknown", "count": count}
                for status, count in task_status_rows
            ],
        },
        "login_logs": [
            {
                "id": row.id,
                "success": bool(row.success),
                "logged_in_at": _utc_to_jst_iso(row.logged_in_at),
                "ip_address": row.ip_address or "",
                "user_agent": row.user_agent or "",
            }
            for row in login_logs
        ],
        "activity_logs": [
            {
                "id": row.id,
                "tool_key": row.tool_key,
                "tool_label": row.tool_label or tool_labels.get(row.tool_key, row.tool_key),
                "method": row.method,
                "path": row.path,
                "endpoint": row.endpoint or "",
                "status_code": row.status_code,
                "duration_ms": row.duration_ms,
                "ip_address": row.ip_address or "",
                "created_at": _utc_to_jst_iso(row.created_at),
            }
            for row in activity_logs
        ],
        "to_bell_tasks": [
            {
                "id": task.id,
                "title": task.title,
                "description": task.description or "",
                "status": task.status,
                "priority": task.priority,
                "created_by": task.created_by,
                "assigned_to": task.assigned_to or "",
                "reviewer_id": task.reviewer_id or "",
                "due_at": _dt_to_iso(task.due_at),
                "completed_at": _utc_to_jst_iso(task.completed_at),
                "created_at": _utc_to_jst_iso(task.created_at),
                "updated_at": _utc_to_jst_iso(task.updated_at),
                "subtasks": [
                    {"title": sub.title, "is_done": bool(sub.is_done)}
                    for sub in task.subtasks
                ],
            }
            for task in tasks
        ],
        "filepost_uploads": [
            {
                "id": row.id,
                "share_uid": row.share_uid,
                "display_id": row.display_id or "",
                "file_name": row.file_name,
                "file_index": row.file_index,
                "file_size": int(row.file_size or 0),
                "is_internal": bool(row.is_internal),
                "ip_address": row.ip_address or "",
                "uploaded_at": _utc_to_jst_iso(row.uploaded_at),
            }
            for row in uploads
        ],
        "filepost_downloads": [
            {
                "id": row.id,
                "share_uid": row.share_uid,
                "display_id": row.display_id or "",
                "uploader": row.uploader or "",
                "downloader": row.downloader or "",
                "downloader_label": row.downloader_label or "外部/匿名",
                "file_name": row.file_name,
                "file_index": row.file_index,
                "download_type": row.download_type,
                "file_size": int(row.file_size or 0),
                "ip_address": row.ip_address or "",
                "downloaded_at": _utc_to_jst_iso(row.downloaded_at),
                "role": "downloaded_by_user" if row.downloader == username else "uploaded_by_user",
            }
            for row in downloads
        ],
    })


# ============================================================
# DSTT 共通メール送信基盤（管理者）
# ============================================================

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_recipients(raw) -> tuple[list[str], list[str]]:
    """カンマ/空白/改行/セミコロン区切りの宛先文字列を分解する。

    戻り値は (正常なアドレス一覧, 形式不正なアドレス一覧)。重複は除去する。
    """
    text = str(raw or "")
    tokens = [t.strip() for t in re.split(r"[\s,;]+", text) if t.strip()]
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        (valid if _EMAIL_RE.match(token) else invalid).append(token)
    return valid, invalid


def _clean_address_list(raw) -> str | None:
    """CC/BCC など複数アドレス入力を正規化してカンマ連結で返す（無ければ None）。"""
    valid, _ = _parse_recipients(raw)
    return ", ".join(valid) if valid else None


@user_management_bp.route("/api/mail/status", methods=["GET"])
@login_required
def mail_status():
    """SMTP 設定状況とスケジューラ設定を返す（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    settings = mail_service.mail_settings()
    return jsonify({
        "configured": mail_service.is_mail_configured(),
        "host": settings["host"],
        "port": settings["port"],
        "security": settings["security"],
        "from_address": settings["from_address"],
        "from_name": settings["from_name"],
        "auth_user": settings["user"],
    })


@user_management_bp.route("/api/mail/messages", methods=["GET"])
@login_required
def mail_messages():
    """直近のメールキューを返す（送信状況のモニタ用、管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    status = (request.args.get("status") or "").strip()
    query = MailMessage.query
    if status:
        query = query.filter(MailMessage.status == status)
    rows = query.order_by(MailMessage.created_at.desc()).limit(limit).all()
    return jsonify({"messages": [m.to_dict() for m in rows]})


@user_management_bp.route("/api/mail/send", methods=["POST"])
@login_required
def mail_send():
    """管理者が任意の宛先へメールを送信（または予約キュー投入）する。

    宛先は複数指定可能。1宛先につき :class:`MailMessage` を1件キューに積み、
    各宛先ごとに送達状況を追跡できるようにする。
    """
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.get_json(silent=True) or {}
    recipients, invalid = _parse_recipients(data.get("to", ""))
    if invalid:
        return jsonify({"error": "宛先の形式が正しくありません: " + ", ".join(invalid)}), 400
    if not recipients:
        return jsonify({"error": "宛先メールアドレスを入力してください"}), 400
    if len(recipients) > 200:
        return jsonify({"error": "一度に送信できる宛先は200件までです"}), 400

    # 明示的な JSON null を文字列 "None" 化しないよう `or ""` で先に潰してから str 化する。
    subject = str(data.get("subject") or "").strip()
    if not subject:
        return jsonify({"error": "件名を入力してください"}), 400
    body_text = str(data.get("body_text") or data.get("body") or "")
    body_html = (data.get("body_html") or "").strip() or None
    if not body_text.strip() and not body_html:
        return jsonify({"error": "本文を入力してください"}), 400

    cc = _clean_address_list(data.get("cc"))
    bcc = _clean_address_list(data.get("bcc"))
    reply_to = _clean_address_list(data.get("reply_to"))
    send_now = bool(data.get("send_now", False))
    configured = mail_service.is_mail_configured()
    actor = getattr(current_user, "username", None)

    queued = 0
    sent = 0
    results = []
    for addr in recipients:
        message = mail_service.queue_mail(
            addr,
            subject,
            body_text,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            category="admin_manual",
            created_by=actor,
            send_now=send_now and configured,
        )
        if message is None:
            continue
        queued += 1
        if message.status == mail_service.STATUS_SENT:
            sent += 1
        results.append(message.to_dict())

    return jsonify({
        "success": True,
        "configured": configured,
        "queued": queued,
        "sent": sent,
        "send_now": send_now,
        "messages": results,
    })


@user_management_bp.route("/api/mail/messages/<int:message_id>/resend", methods=["POST"])
@login_required
def mail_resend(message_id):
    """失敗/保留/キャンセル済みメッセージを再送対象として queued に戻す（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    message = db.session.get(MailMessage, message_id)
    if message is None:
        return jsonify({"error": "メッセージが見つかりません"}), 404
    data = request.get_json(silent=True) or {}
    send_now = bool(data.get("send_now", True)) and mail_service.is_mail_configured()
    mail_service.requeue_message(message, send_now=send_now)
    return jsonify({"success": True, "message": message.to_dict()})


# ============================================================
# DSTT 共通メール基盤・受信トレイ（Webメーラー / 管理者）
# ============================================================

@user_management_bp.route("/api/mail/inbox/status", methods=["GET"])
@login_required
def mail_inbox_status():
    """IMAP 受信設定の状況と未読件数を返す（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    settings = mail_inbox.inbox_settings()
    unread = InboundMail.query.filter_by(is_read=False).count()
    total = InboundMail.query.count()
    return jsonify({
        "configured": mail_inbox.is_inbox_configured(),
        "host": settings["host"],
        "port": settings["port"],
        "security": settings["security"],
        "user": settings["user"],
        "mailbox": settings["mailbox"],
        "unread": unread,
        "total": total,
    })


@user_management_bp.route("/api/mail/inbox", methods=["GET"])
@login_required
def mail_inbox_list():
    """受信トレイの一覧（本文なし）を返す（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    query = InboundMail.query
    if (request.args.get("unread") or "").strip() in {"1", "true", "yes"}:
        query = query.filter_by(is_read=False)
    search = (request.args.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                InboundMail.subject.ilike(like),
                InboundMail.from_address.ilike(like),
                InboundMail.from_name.ilike(like),
            )
        )
    rows = query.order_by(InboundMail.received_at.desc()).limit(limit).all()
    return jsonify({"messages": [m.to_dict() for m in rows]})


@user_management_bp.route("/api/mail/inbox/<int:mail_id>", methods=["GET"])
@login_required
def mail_inbox_detail(mail_id):
    """受信メール本文を返す。既読化する（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    row = db.session.get(InboundMail, mail_id)
    if row is None:
        return jsonify({"error": "メールが見つかりません"}), 404
    if not row.is_read:
        row.is_read = True
        db.session.commit()
    return jsonify({"message": row.to_dict(include_body=True)})


@user_management_bp.route("/api/mail/inbox/<int:mail_id>/read", methods=["POST"])
@login_required
def mail_inbox_mark_read(mail_id):
    """既読/未読を切り替える（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    row = db.session.get(InboundMail, mail_id)
    if row is None:
        return jsonify({"error": "メールが見つかりません"}), 404
    data = request.get_json(silent=True) or {}
    row.is_read = bool(data.get("read", True))
    db.session.commit()
    return jsonify({"success": True, "is_read": row.is_read})


@user_management_bp.route("/api/mail/inbox/<int:mail_id>", methods=["DELETE"])
@login_required
def mail_inbox_delete(mail_id):
    """受信メールを削除する（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    row = db.session.get(InboundMail, mail_id)
    if row is None:
        return jsonify({"error": "メールが見つかりません"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@user_management_bp.route("/api/mail/inbox/poll", methods=["POST"])
@login_required
def mail_inbox_poll():
    """IMAP から今すぐ新着を取り込む（管理者のみ）。"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    if not mail_inbox.is_inbox_configured():
        return jsonify({"error": "IMAP が未設定です（DSTT_IMAP_HOST / DSTT_IMAP_USER / DSTT_IMAP_PASSWORD を設定してください）。"}), 400
    summary = mail_inbox.fetch_new_messages()
    return jsonify({"success": True, "summary": summary})


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
