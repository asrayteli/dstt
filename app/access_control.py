"""DSTT全体で共有するアクセス権管理ロジック。

設計概要
------
- ツールごとにアクセス制御カテゴリを定義する（`TOOL_ACCESS_CATEGORIES`）。
  * ``public``   : ログイン済みなら誰でも利用可。
  * ``sensitive``: 個人情報を扱う／他の機密ツールと連携するため、
                    個別の付与または管理者権限が必要。
- ユーザーには「支店 → 営業所 → 担当」の3階層所属と、ツール毎の個別付与
  (`UserToolPermission`) を持たせ、管理者ページから設定できるようにする。
- ダッシュボードのナビゲーションは、アクセスできるツールのみ表示する。
- 個別のツール側では `require_tool_access` デコレータで強制的にガードする。
"""

from __future__ import annotations

from functools import wraps
from typing import Iterable

from flask import abort, jsonify, redirect, request, url_for
from flask_login import current_user

from .models import (
    AccessBranch,
    AccessOffice,
    GroupToolPermission,
    ToolCategory,
    ToolSettings,
    User,
    UserAccessibleOffice,
    UserToolPermission,
    db,
)
from .navigation import NAV_ITEMS


# 初期管理者ID（旧実装の互換性のためハードコーディング）
LEGACY_ADMIN_USERNAME = "3243012"


# ツールカテゴリ定義。`nav_key` は NAV_ITEMS の href 末尾 (= Blueprint url_prefix の末尾)
# と一致させる。
TOOL_ACCESS_CATEGORIES: dict[str, str] = {
    # 機密情報を扱うツール：個別付与が必要
    "leave_mgr": "sensitive",            # 有休共有ツール
    "pluslist": "sensitive",             # 社員名簿PLUS
    "siteplus": "sensitive",             # 現場リストPLUS
    "shiftersync": "sensitive",          # ShifterSync（社員名簿/現場リストと連携、CloudShiftも包含）
    "subject_analysis_tool": "sensitive",  # 科目別分析ツール（現場リストPLUSを利用）
    "bus_pricing": "sensitive",          # 貸切料金計算ツール
    "health_check": "sensitive",         # 健診PLUS（健康情報＝要配慮個人情報）
    "camera_scanner": "sensitive",       # カメラスキャナー（免許証等の本人確認書類＝個人情報）

    # 以下は公開（ログインで誰でも利用可）
    "datecalc": "public",
    "calc": "public",
    "rename": "public",
    "compress": "public",
    "csvtool": "public",
    "password_tool": "public",
    "workday": "public",
    "pdf_power": "public",
    "color_extract": "public",
    "powerstamp": "public",
    "powervote": "public",
    "power_flow": "public",
    "power_imager": "sensitive",
    "share": "public",
    "car_inspe": "sensitive",
    "monthly_generator": "public",
    "to_bell": "public",
}


def tool_category(tool_key: str) -> str:
    ts = db.session.get(ToolSettings, tool_key)
    if ts:
        return ts.access_type
    return TOOL_ACCESS_CATEGORIES.get(tool_key, "public")


def tool_requires_permission(tool_key: str) -> bool:
    return tool_category(tool_key) == "sensitive"


# ------------------------------------------------------------------
# 管理者判定
# ------------------------------------------------------------------

def is_legacy_admin_username(username) -> bool:
    return str(username or "").strip() == LEGACY_ADMIN_USERNAME


def is_admin_user(user=None) -> bool:
    """現在のユーザー（または指定ユーザー）が管理者かどうか。"""
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    # レガシー互換：従来の固定管理者ID
    if is_legacy_admin_username(getattr(user, "username", None)):
        return True
    return bool(getattr(user, "is_admin", False))


def ensure_legacy_admin_flag() -> None:
    """既存DBの `3243012` を `is_admin=True` に自動昇格させる。"""
    user = User.query.filter_by(username=LEGACY_ADMIN_USERNAME).first()
    if user and not user.is_admin:
        user.is_admin = True
        db.session.commit()


# ------------------------------------------------------------------
# ツールアクセス判定
# ------------------------------------------------------------------

def _user_granted_tool_keys(user) -> set[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    user_id = getattr(user, "id", None)
    if not user_id:
        return set()
    rows = UserToolPermission.query.filter_by(user_id=user_id).all()
    return {row.tool_key for row in rows}


# ------------------------------------------------------------------
# 所属（支店/営業所/担当）の解決
# ------------------------------------------------------------------

def user_office_ids(user=None) -> set[int]:
    """ユーザーがアクセスできる営業所IDの集合（主営業所 + 追加付与）。

    特例: 主営業所が未設定で支店のみ登録されているユーザーは、
    その支店配下の全営業所を自動的にアクセス対象とする。
    """
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    ids: set[int] = set()
    primary_office_id = getattr(user, "office_id", None)
    if primary_office_id:
        ids.add(int(primary_office_id))
    user_id = getattr(user, "id", None)
    if user_id:
        rows = UserAccessibleOffice.query.filter_by(user_id=user_id).all()
        for row in rows:
            if row.office_id is not None:
                ids.add(int(row.office_id))
    branch_id = getattr(user, "branch_id", None)
    if branch_id and not primary_office_id:
        rows = AccessOffice.query.filter_by(branch_id=int(branch_id)).all()
        for row in rows:
            ids.add(int(row.id))
    return ids


def user_branch_ids(user=None) -> set[int]:
    """ユーザーがアクセスできる支店IDの集合。
    主支店 + アクセス可能な営業所から導出される支店。"""
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    ids: set[int] = set()
    if getattr(user, "branch_id", None):
        ids.add(int(user.branch_id))
    office_ids = user_office_ids(user)
    if office_ids:
        rows = AccessOffice.query.filter(AccessOffice.id.in_(office_ids)).all()
        for row in rows:
            if row.branch_id is not None:
                ids.add(int(row.branch_id))
    return ids


def user_department_ids(user=None) -> set[int]:
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    ids: set[int] = set()
    if getattr(user, "department_id", None):
        ids.add(int(user.department_id))
    return ids


def user_office_codes(user=None) -> set[str]:
    """データフィルタ用。ユーザーがアクセスできる営業所の「コード」集合。
    コード未設定の営業所は無視する。"""
    office_ids = user_office_ids(user)
    if not office_ids:
        return set()
    rows = AccessOffice.query.filter(AccessOffice.id.in_(office_ids)).all()
    return {r.code for r in rows if r.code}


def user_branch_codes(user=None) -> set[str]:
    branch_ids = user_branch_ids(user)
    if not branch_ids:
        return set()
    rows = AccessBranch.query.filter(AccessBranch.id.in_(branch_ids)).all()
    return {r.code for r in rows if r.code}


def _user_satisfies_group_rule(
    user,
    rule: GroupToolPermission,
    branch_ids: set[int] | None = None,
    office_ids: set[int] | None = None,
    dept_ids: set[int] | None = None,
) -> bool:
    """グループ付与ルールがユーザーの所属範囲を満たすか判定。"""
    if branch_ids is None:
        branch_ids = user_branch_ids(user)
    if office_ids is None:
        office_ids = user_office_ids(user)
    if dept_ids is None:
        dept_ids = user_department_ids(user)

    if rule.branch_id is not None and int(rule.branch_id) not in branch_ids:
        return False
    if rule.office_id is not None and int(rule.office_id) not in office_ids:
        return False
    if rule.department_id is not None and int(rule.department_id) not in dept_ids:
        return False
    # 全てのスコープが未設定だと全ユーザーに許可になってしまうので、
    # 少なくとも 1 つは設定されていることを要件とする。
    if rule.branch_id is None and rule.office_id is None and rule.department_id is None:
        return False
    return True


def _group_granted_tool_keys(user) -> set[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    branch_ids = user_branch_ids(user)
    office_ids = user_office_ids(user)
    dept_ids = user_department_ids(user)
    rules = GroupToolPermission.query.all()
    keys: set[str] = set()
    for rule in rules:
        if _user_satisfies_group_rule(user, rule, branch_ids, office_ids, dept_ids):
            keys.add(rule.tool_key)
    return keys


def _is_tool_visible(tool_key: str) -> bool:
    ts = db.session.get(ToolSettings, tool_key)
    return ts.is_visible if ts else True


def user_has_tool_access(tool_key: str, user=None) -> bool:
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin_user(user):
        return True
    if not _is_tool_visible(tool_key):
        return False
    if not tool_requires_permission(tool_key):
        return True
    if tool_key in _user_granted_tool_keys(user):
        return True
    if tool_key in _group_granted_tool_keys(user):
        return True
    return False


def _tool_visibility_map() -> dict[str, bool]:
    """ToolSettingsからツールの公開/非公開マップを返す。未登録は公開扱い。"""
    rows = ToolSettings.query.all()
    return {row.tool_key: row.is_visible for row in rows}


def get_accessible_nav_items(user=None) -> list[dict]:
    """ユーザーがアクセスできるツールだけに絞ったナビゲーション一覧を返す。"""
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    admin = is_admin_user(user)
    granted: set[str] | None = None
    if not admin:
        granted = set(_user_granted_tool_keys(user)) | set(_group_granted_tool_keys(user))

    visibility = _tool_visibility_map()

    visible: list[dict] = []
    for item in NAV_ITEMS:
        tool_key = _nav_tool_key(item)
        if not admin and not visibility.get(tool_key, True):
            continue
        if admin:
            visible.append(item)
            continue
        if not tool_requires_permission(tool_key):
            visible.append(item)
            continue
        if granted is not None and tool_key in granted:
            visible.append(item)
    return visible


def get_categorized_nav_items(user=None) -> list[dict]:
    """カテゴリ別にグループ化されたナビゲーション一覧を返す。

    Returns:
        [
            {"category": {"id": 1, "name": "大新東ツール"}, "tools": [nav_item, ...]},
            {"category": {"id": 2, "name": "ファイル操作"}, "tools": [nav_item, ...]},
            ...
            {"category": None, "tools": [uncategorized_items...]},
        ]
    """
    items = get_accessible_nav_items(user)
    if not items:
        return []

    accessible_keys = {_nav_tool_key(item) for item in items}
    item_map = {_nav_tool_key(item): item for item in items}

    categories = ToolCategory.query.order_by(ToolCategory.sort_order, ToolCategory.id).all()
    settings = {ts.tool_key: ts for ts in ToolSettings.query.all()}

    result = []
    placed_keys: set[str] = set()

    for cat in categories:
        cat_tools = []
        cat_settings = sorted(
            [ts for ts in settings.values() if ts.category_id == cat.id],
            key=lambda ts: ts.sort_order,
        )
        for ts in cat_settings:
            if ts.tool_key in accessible_keys:
                cat_tools.append(item_map[ts.tool_key])
                placed_keys.add(ts.tool_key)
        if cat_tools:
            result.append({
                "category": {"id": cat.id, "name": cat.name},
                "tools": cat_tools,
            })

    uncategorized = [item_map[k] for k in accessible_keys if k not in placed_keys]
    if uncategorized:
        ordered = [item for item in items if _nav_tool_key(item) in {_nav_tool_key(u) for u in uncategorized}]
        result.append({"category": None, "tools": ordered})

    return result


def user_can_access_office_code(office_code: str | None, user=None) -> bool:
    """データに割り当てられた office_code に対するアクセス判定。
    - 管理者は常に可。
    - コード未設定（None/空）のデータは管理者のみ可（誤公開防止）。
    - それ以外は、ユーザーのアクセス可能な営業所コード集合に含まれていれば可。"""
    user = user if user is not None else current_user
    if is_admin_user(user):
        return True
    code = (office_code or "").strip()
    if not code:
        return False
    return code in user_office_codes(user)


def _nav_tool_key(item: dict) -> str:
    key = str(item.get("key", "")).strip()
    if key:
        return key
    href = str(item.get("href", "")).strip("/")
    # 例: "tools/leave_mgr" -> "leave_mgr"
    if "/" in href:
        return href.rsplit("/", 1)[-1]
    return href


def all_tool_keys_in_order() -> list[str]:
    return [_nav_tool_key(item) for item in NAV_ITEMS]


# ------------------------------------------------------------------
# デコレータ
# ------------------------------------------------------------------

def enforce_tool_access(tool_key: str):
    """Blueprintの `before_request` 用ヘルパー。
    未ログイン/権限不足なら適切なレスポンスを返す。"""
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not user_has_tool_access(tool_key):
        if _wants_json():
            return jsonify({"error": "このツールへのアクセス権限がありません"}), 403
        abort(403)
    return None


def require_tool_access(tool_key: str):
    """特定ツールへのアクセスを要求するFlaskデコレータ。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if not user_has_tool_access(tool_key):
                if _wants_json():
                    return jsonify({"error": "このツールへのアクセス権限がありません"}), 403
                abort(403)
            return fn(*args, **kwargs)
        return wrapper

    return decorator


def require_admin(fn):
    """管理者専用エンドポイント用デコレータ。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not is_admin_user():
            if _wants_json():
                return jsonify({"error": "管理者権限が必要です"}), 403
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def _wants_json() -> bool:
    path = request.path or ""
    if "/api/" in path or path.endswith("/api"):
        return True
    accept = request.headers.get("Accept", "") or ""
    if "application/json" in accept and "text/html" not in accept:
        return True
    if request.is_json:
        return True
    return False


# ------------------------------------------------------------------
# 一括付与/剥奪ユーティリティ
# ------------------------------------------------------------------

def grant_tool_access(user_id: int, tool_keys: Iterable[str], granted_by: str) -> None:
    existing = {
        row.tool_key
        for row in UserToolPermission.query.filter_by(user_id=user_id).all()
    }
    added = False
    for key in tool_keys:
        if key in existing:
            continue
        db.session.add(
            UserToolPermission(user_id=user_id, tool_key=key, granted_by=granted_by)
        )
        existing.add(key)
        added = True
    if added:
        db.session.commit()


def revoke_tool_access(user_id: int, tool_keys: Iterable[str]) -> None:
    keys = list(tool_keys)
    if not keys:
        return
    UserToolPermission.query.filter(
        UserToolPermission.user_id == user_id,
        UserToolPermission.tool_key.in_(keys),
    ).delete(synchronize_session=False)
    db.session.commit()


def set_tool_access(user_id: int, tool_keys: Iterable[str], granted_by: str) -> None:
    """ユーザーのツール許可を、指定セットと完全一致するよう同期する。"""
    desired = set(tool_keys)
    existing_rows = UserToolPermission.query.filter_by(user_id=user_id).all()
    existing = {row.tool_key: row for row in existing_rows}

    to_add = desired - set(existing.keys())
    to_remove = set(existing.keys()) - desired

    for key in to_add:
        db.session.add(
            UserToolPermission(user_id=user_id, tool_key=key, granted_by=granted_by)
        )
    for key in to_remove:
        db.session.delete(existing[key])

    if to_add or to_remove:
        db.session.commit()
