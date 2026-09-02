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
- 個別付与には強さ（scope）がある。
  * ``full`` : 従来どおりの通常許可。ツール本体のUI・全APIを使える。
  * ``api``  : ツール間API専用許可。提供元ツール本体は開けず、``app.tool_api``
               に登録されたツール間APIからの参照だけを許す。単独では何も見られず、
               利用側ツール（例: ShifterSync）の通常許可との AND で初めて効く。
               ナビゲーション表示・マニュアル・一斉通知の宛先には一切含めない。
"""

from __future__ import annotations

from functools import wraps
from typing import Iterable

from flask import abort, current_app, g, jsonify, redirect, request, url_for
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
from .tool_api import (
    TOOL_SCOPE_API,
    TOOL_SCOPE_FULL,
    TOOL_SCOPES,
    VALID_TOOL_API_ENDPOINTS,
    ToolApiEndpoint,
    api_consumers_for,
    api_provider_tool_keys,
    normalize_tool_scope,
)


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
    "quote_maker": "public",
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
    # 固定IDだけで管理者になれる挙動は、IDを知る攻撃者がそのアカウントを
    # 作成・奪取した場合の権限昇格になる。移行期間だけ明示的に opt-in する。
    enabled = bool(current_app.config.get("ENABLE_LEGACY_ADMIN", False))
    return enabled and str(username or "").strip() == LEGACY_ADMIN_USERNAME


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
    """明示的な移行期間だけ旧管理者IDを ``is_admin=True`` にする。

    この関数は起動時に毎回呼ばれるため、設定を確認せずに昇格すると、通常権限で
    作成された同名アカウントが次回起動時に管理者になる。既存環境の移行が必要な
    場合に限り ``DSTT_ENABLE_LEGACY_ADMIN=1`` を設定する。
    """
    if not current_app.config.get("ENABLE_LEGACY_ADMIN", False):
        return
    user = User.query.filter_by(username=LEGACY_ADMIN_USERNAME).first()
    if user and not user.is_admin:
        user.is_admin = True
        db.session.commit()


# ------------------------------------------------------------------
# ツールアクセス判定
# ------------------------------------------------------------------

_USER_TOOL_SCOPE_CACHE_ATTR = "_dstt_user_tool_scopes"


def _user_tool_scope_cache() -> dict | None:
    """個別付与のリクエスト内メモ化。

    ツール間APIの判定は「提供元の付与」「利用側の付与」を続けて見るため、
    素直に書くと1リクエストで同じ全件取得が何度も走る（社員候補サーチは
    入力のたびに飛ぶ）。``_group_tool_rules()`` と同じくリクエスト境界で持つ。
    """
    try:
        from flask import has_app_context

        if not has_app_context():
            return None
        cache = getattr(g, _USER_TOOL_SCOPE_CACHE_ATTR, None)
        if cache is None:
            cache = {}
            setattr(g, _USER_TOOL_SCOPE_CACHE_ATTR, cache)
        return cache
    except Exception:  # noqa: BLE001
        return None


def _invalidate_user_tool_scope_cache(user_id: int | None = None) -> None:
    cache = _user_tool_scope_cache()
    if cache is None:
        return
    if user_id is None:
        cache.clear()
    else:
        cache.pop(int(user_id), None)


def _user_tool_scopes(user) -> dict[str, str]:
    """個別付与を ``{tool_key: scope}`` で返す。scope は 'full' / 'api'。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    user_id = getattr(user, "id", None)
    if not user_id:
        return {}
    cache = _user_tool_scope_cache()
    if cache is not None and user_id in cache:
        return cache[user_id]
    rows = UserToolPermission.query.filter_by(user_id=user_id).all()
    scopes = {row.tool_key: normalize_tool_scope(row.scope) for row in rows}
    if cache is not None:
        cache[user_id] = scopes
    return scopes


def _user_granted_tool_keys(user) -> set[str]:
    """通常許可（scope='full'）で個別付与されたツールキー。

    ツール間API専用許可（scope='api'）はここに含めない。含めてしまうと
    ナビゲーション表示・マニュアル・Blueprintガードなど、既存の判定すべてが
    「ツール本体を使ってよい」と誤解する。
    """
    return {
        key
        for key, scope in _user_tool_scopes(user).items()
        if scope == TOOL_SCOPE_FULL
    }


def _user_api_granted_tool_keys(user) -> set[str]:
    """ツール間API専用許可（scope='api'）が付与されたツールキー。"""
    return {
        key
        for key, scope in _user_tool_scopes(user).items()
        if scope == TOOL_SCOPE_API
    }


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


def _group_tool_rules() -> list:
    """グループ付与ルール一覧。リクエスト内で使い回す。

    ``enabled_users()`` のように多人数ぶんの権限をまとめて判定する経路では、
    人数ぶんだけ全件スキャンが走ってしまうため、リクエスト境界でメモ化する。
    """
    try:
        from flask import g, has_app_context

        if not has_app_context():
            return GroupToolPermission.query.all()
        rules = getattr(g, "_dstt_group_tool_rules", None)
        if rules is None:
            rules = GroupToolPermission.query.all()
            g._dstt_group_tool_rules = rules
        return rules
    except Exception:  # noqa: BLE001
        return GroupToolPermission.query.all()


def _group_granted_tool_keys(user) -> set[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    branch_ids = user_branch_ids(user)
    office_ids = user_office_ids(user)
    dept_ids = user_department_ids(user)
    rules = _group_tool_rules()
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


# ------------------------------------------------------------------
# ツール間API（tool-to-tool API）判定
# ------------------------------------------------------------------

# 「このリクエストはツール間API専用許可で通した」ことを提供元エンドポイントへ
# 伝えるためのリクエストローカルなフラグ。値は返してよい項目の集合
# （``None`` は項目制限なし）。設定されるのは Blueprint ガードだけで、
# 通常許可・管理者で通ったリクエストでは設定されない。
_TOOL_API_FIELDS_ATTR = "_dstt_tool_api_fields"


def tool_api_endpoint_spec(endpoint_name: str | None) -> ToolApiEndpoint | None:
    """登録済み かつ 宣言に矛盾の無いツール間APIエンドポイントの定義。"""
    if not endpoint_name:
        return None
    return VALID_TOOL_API_ENDPOINTS.get(endpoint_name)


def _api_accessible_consumers(spec: ToolApiEndpoint, user) -> list[str]:
    """このエンドポイントの利用側ツールのうち、ユーザーが通常許可を持つもの。"""
    declared = api_consumers_for(spec.provider)
    return [
        consumer
        for consumer in spec.consumers
        if consumer in declared and user_has_tool_access(consumer, user)
    ]


def user_has_tool_api_access(provider: str, consumer: str | None = None, user=None) -> bool:
    """提供元ツールへ「ツール間API経由で」到達してよいか。

    True になるのは次のいずれか。
      * 管理者
      * 提供元ツールの通常アクセス権を持っている（従来どおり）
      * 提供元ツールのAPI専用許可を持ち、かつ利用側ツールの通常アクセス権も持つ

    ``consumer`` を省略した場合は、レジストリが宣言する利用側ツールのいずれかに
    アクセスできれば True。
    """
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    declared = api_consumers_for(provider)
    if not declared:
        # ツール間APIを持たない提供元は、API専用許可の対象外。
        return False
    if consumer is not None and consumer not in declared:
        return False
    if is_admin_user(user):
        return True
    # 提供元ツールが非公開設定なら、ツール間API経由でも触らせない
    # （従来の user_has_tool_access と同じ扱いにする）。
    if not _is_tool_visible(provider):
        return False
    if user_has_tool_access(provider, user):
        return True
    if provider not in _user_api_granted_tool_keys(user):
        return False
    candidates = (consumer,) if consumer is not None else tuple(declared)
    return any(user_has_tool_access(key, user) for key in candidates)


def tool_api_endpoint_allowed(endpoint_name: str, user=None) -> bool:
    """レジストリ登録済みエンドポイントへのアクセス可否。

    未登録のエンドポイントは常に False（＝従来どおり提供元ツールの権限が必要）。
    """
    spec = tool_api_endpoint_spec(endpoint_name)
    if spec is None:
        return False
    user = user if user is not None else current_user
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin_user(user):
        return True
    if not _is_tool_visible(spec.provider):
        return False
    if user_has_tool_access(spec.provider, user):
        return True
    if spec.provider not in _user_api_granted_tool_keys(user):
        return False
    return bool(_api_accessible_consumers(spec, user))


def tool_api_allowed_fields(endpoint_name: str, user=None) -> frozenset[str] | None:
    """API専用許可で返してよいレスポンス項目。``None`` は項目制限なし。

    提供元ツールの通常アクセス権（または管理者）があれば従来どおり全項目。
    """
    spec = tool_api_endpoint_spec(endpoint_name)
    if spec is None:
        return None
    user = user if user is not None else current_user
    if is_admin_user(user):
        return None
    if user_has_tool_access(spec.provider, user):
        return None
    if spec.provider not in _user_api_granted_tool_keys(user):
        # ここに来るのは想定外（ガードを通っていない）。安全側＝何も返さない。
        return frozenset()
    declared = api_consumers_for(spec.provider)
    fields: set[str] = set()
    for consumer in _api_accessible_consumers(spec, user):
        limit = declared[consumer]
        if limit is None:
            return None
        fields |= set(limit)
    return frozenset(fields)


def mark_tool_api_request(fields: frozenset[str] | None) -> None:
    """このリクエストがツール間API専用許可で通ったことを記録する。"""
    try:
        setattr(g, _TOOL_API_FIELDS_ATTR, fields)
    except RuntimeError:  # アプリケーションコンテキスト外
        pass


def tool_api_response_fields() -> frozenset[str] | None:
    """現在のリクエストで返してよい項目。``None`` は項目制限なし。

    Blueprint ガードが「API専用許可で通した」と記録した場合だけ集合が返る。
    通常許可・管理者のリクエストでは何も記録されないため ``None`` になる。
    """
    try:
        return getattr(g, _TOOL_API_FIELDS_ATTR, None)
    except RuntimeError:
        return None


def restrict_tool_api_rows(rows: Iterable[dict]) -> list[dict]:
    """レスポンス行を、このリクエストで返してよい項目だけに絞る。"""
    allowed = tool_api_response_fields()
    if allowed is None:
        return list(rows)
    return [{k: v for k, v in row.items() if k in allowed} for row in rows]


def api_grantable_tool_keys() -> set[str]:
    """ツール間API専用許可を付与できるツールキー。

    レジストリに提供元として載っていて、かつ現在の設定で ``sensitive``
    （＝通常許可が必要）なツールに限る。公開ツールに API 専用許可を付けても
    意味がないため候補から外す。
    """
    return {key for key in api_provider_tool_keys() if tool_requires_permission(key)}


def username_has_tool_access(username: str, tool_key: str, *, via_tool: str | None = None) -> bool:
    """username 指定でツールアクセス権を判定する。

    リクエスト外（スケジューラ・バックグラウンドジョブ）からも呼べるようにするため、
    ``current_user`` ではなく username から User を引く。

    ``via_tool`` を渡すと「そのツールの機能としてツール間API経由で参照する」判定になり、
    ``tool_key`` のAPI専用許可 + ``via_tool`` の通常許可でも True になる。
    """
    name = (username or "").strip()
    if not name:
        return False
    user = User.query.filter_by(username=name).first()
    if user is None:
        return False
    # user_has_tool_access は is_authenticated を見るため、DB から引いた User でも
    # 判定できるよう UserMixin の既定値（True）に頼る。
    if via_tool and via_tool in api_consumers_for(tool_key):
        return user_has_tool_api_access(tool_key, via_tool, user)
    return user_has_tool_access(tool_key, user)


def usernames_with_tool_access(usernames, tool_key: str, *, via_tool: str | None = None) -> set[str]:
    """複数ユーザーのツールアクセス権をまとめて判定する。

    1人ずつ ``username_has_tool_access()`` を呼ぶと、User の取得・個別付与の照会・
    グループ規則の評価が人数ぶん走る（連携の一斉通知では保存リクエストの中で
    数十〜数百クエリになる）。ツール単位の判定は1回で済ませ、個別付与は
    まとめて引き、グループ規則は必要な人にだけ評価する。

    ``via_tool`` は :func:`username_has_tool_access` と同じ意味。指定した利用側ツールの
    通常許可を持つ人に限り、``tool_key`` のツール間API専用許可も許可として数える。
    """
    names = {str(name).strip() for name in usernames if str(name or "").strip()}
    if not names:
        return set()
    users = User.query.filter(User.username.in_(names)).all()
    if not users:
        return set()

    admins = {user.username for user in users if is_admin_user(user)}
    # 以降はツール単位の判定。人数に関係なく1回だけ行う。
    if not _is_tool_visible(tool_key):
        return admins
    if not tool_requires_permission(tool_key):
        return {user.username for user in users}

    api_ok = bool(via_tool) and via_tool in api_consumers_for(tool_key)

    allowed = set(admins)
    undecided = [user for user in users if user.username not in allowed]
    # API専用許可の候補（ツール間API経由の判定のときだけ意味を持つ）
    api_scoped_ids: set[int] = set()
    if undecided:
        granted_user_ids: set[int] = set()
        for row in UserToolPermission.query.filter(
            UserToolPermission.tool_key == tool_key,
            UserToolPermission.user_id.in_([user.id for user in undecided]),
        ).all():
            if normalize_tool_scope(row.scope) == TOOL_SCOPE_API:
                api_scoped_ids.add(row.user_id)
            else:
                granted_user_ids.add(row.user_id)
        allowed.update(user.username for user in undecided if user.id in granted_user_ids)
        undecided = [user for user in undecided if user.username not in allowed]

    # グループ付与にこのツールの規則が無ければ、所属の解決自体が不要。
    if undecided and any(rule.tool_key == tool_key for rule in _group_tool_rules()):
        for user in undecided:
            if tool_key in _group_granted_tool_keys(user):
                allowed.add(user.username)
        undecided = [user for user in undecided if user.username not in allowed]

    # ツール間API経由の判定のときだけ、API専用許可 + 利用側ツールの通常許可を数える。
    if api_ok and undecided:
        for user in undecided:
            if user.id in api_scoped_ids and user_has_tool_access(via_tool, user):
                allowed.add(user.username)
    return allowed


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


def get_categorized_nav_items(user=None, items: list[dict] | None = None) -> list[dict]:
    """カテゴリ別にグループ化されたナビゲーション一覧を返す。

    items に get_accessible_nav_items() の結果を渡すと再計算しない
    （テンプレート毎レンダリングでの権限クエリ重複を避けるため）。

    Returns:
        [
            {"category": {"id": 1, "name": "大新東ツール"}, "tools": [nav_item, ...]},
            {"category": {"id": 2, "name": "ファイル操作"}, "tools": [nav_item, ...]},
            ...
            {"category": None, "tools": [uncategorized_items...]},
        ]
    """
    if items is None:
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

def _validated_scope(scope: str) -> str:
    if scope not in TOOL_SCOPES:
        raise ValueError(f"不正なツール許可スコープです: {scope!r}")
    return scope


def grant_tool_access(
    user_id: int,
    tool_keys: Iterable[str],
    granted_by: str,
    scope: str = TOOL_SCOPE_FULL,
) -> None:
    """指定スコープの許可を追加する（既存行のスコープは変更しない）。"""
    scope = _validated_scope(scope)
    existing = {
        row.tool_key
        for row in UserToolPermission.query.filter_by(user_id=user_id).all()
    }
    added = False
    for key in tool_keys:
        if key in existing:
            continue
        db.session.add(
            UserToolPermission(
                user_id=user_id, tool_key=key, scope=scope, granted_by=granted_by
            )
        )
        existing.add(key)
        added = True
    if added:
        db.session.commit()
        _invalidate_user_tool_scope_cache(user_id)


def revoke_tool_access(user_id: int, tool_keys: Iterable[str]) -> None:
    keys = list(tool_keys)
    if not keys:
        return
    UserToolPermission.query.filter(
        UserToolPermission.user_id == user_id,
        UserToolPermission.tool_key.in_(keys),
    ).delete(synchronize_session=False)
    db.session.commit()
    _invalidate_user_tool_scope_cache(user_id)


def set_tool_access_scopes(
    user_id: int,
    scopes: dict[str, str],
    granted_by: str,
    *,
    managed_scopes: Iterable[str] = TOOL_SCOPES,
) -> None:
    """ユーザーのツール許可を ``{tool_key: scope}`` と一致するよう同期する。

    ``managed_scopes`` に含まれるスコープの既存行だけを削除対象にする。
    たとえば ``managed_scopes=('full',)`` なら、通常許可の付け外しだけを行い、
    ツール間API専用許可の行はそのまま残す（権限テンプレートの適用など、
    通常許可だけを扱う経路が API 専用許可を巻き添えで消さないようにするため）。
    ただし ``scopes`` で明示されたツールは、既存行のスコープが何であっても
    指定どおりに揃える（(user_id, tool_key) は一意のため）。
    """
    desired = {key: _validated_scope(scope) for key, scope in scopes.items()}
    managed = {_validated_scope(scope) for scope in managed_scopes}

    existing_rows = UserToolPermission.query.filter_by(user_id=user_id).all()
    existing = {row.tool_key: row for row in existing_rows}

    changed = False
    for key, scope in desired.items():
        row = existing.get(key)
        if row is None:
            db.session.add(
                UserToolPermission(
                    user_id=user_id, tool_key=key, scope=scope, granted_by=granted_by
                )
            )
            changed = True
            continue
        if normalize_tool_scope(row.scope) != scope:
            row.scope = scope
            row.granted_by = granted_by
            changed = True

    for key, row in existing.items():
        if key in desired:
            continue
        if normalize_tool_scope(row.scope) not in managed:
            continue
        db.session.delete(row)
        changed = True

    if changed:
        db.session.commit()
        _invalidate_user_tool_scope_cache(user_id)


def set_tool_access(
    user_id: int,
    tool_keys: Iterable[str],
    granted_by: str,
    scope: str = TOOL_SCOPE_FULL,
) -> None:
    """指定スコープの許可を、指定セットと完全一致するよう同期する。

    既定では通常許可（full）だけを同期し、ツール間API専用許可には触れない。
    """
    scope = _validated_scope(scope)
    set_tool_access_scopes(
        user_id,
        {key: scope for key in tool_keys},
        granted_by,
        managed_scopes=(scope,),
    )
