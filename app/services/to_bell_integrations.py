"""ToBell ⇄ 他DSTTツール連携の共通レイヤ。

ユーザー個人ごとの「連携許可」設定（デフォルトOFF）と、
連携機能のキー定義、許可判定ヘルパーをまとめる。
"""
from __future__ import annotations

from typing import Any

from app.models import ToBellUserSettings, db


INTEGRATION_KEYS = {
    "cloudshift.leave_change_request": {
        "tool": "cloudshift",
        "label": "休暇種別変更申請を自動タスク化",
        "description": "CloudShiftで休暇種別変更申請が起きると、即時通知付きで処理タスクをToBellに追加します。",
    },
    "cloudshift.shift_update": {
        "tool": "cloudshift",
        "label": "要代務シフト帳の更新を確認タスク化",
        "description": "要代務シフト帳が更新されたら、確認を促すタスクをToBellに追加します。",
    },
    "filepost.attachment_overflow": {
        "tool": "filepost",
        "label": "25MB超の添付を自動でFILEPOSTへ",
        "description": "ToBellタスクへの添付がしきい値（25MB）を超えた場合、FILEPOSTの仕組みでアップロードして紐付けます。",
    },
    "filepost.project_files": {
        "tool": "filepost",
        "label": "プロジェクトのFILEPOSTファイル管理",
        "description": "FILEPOSTでアップロードしたファイルの非公開URLを、ToBellのプロジェクトに紐付けて管理します。",
    },
    "pluslist.linkage": {
        "tool": "pluslist",
        "label": "プロジェクト/タスクを社員に紐付け",
        "description": "pluslistの社員と、ToBellのプロジェクトやタスクを多対多で紐付けます。",
    },
    "siteplus.linkage": {
        "tool": "siteplus",
        "label": "プロジェクト/タスクを現場に紐付け",
        "description": "siteplusの現場と、ToBellのプロジェクトやタスクを多対多で紐付けます。",
    },
    "health_check.linkage": {
        "tool": "health_check",
        "label": "健診の予約・二次検査・深夜2回目をリマインド",
        "description": "健診PLUSで自分が担当する対象者の健康診断予約日・二次検査受診推奨日・深夜従事者の年2回目受診を、対象日の当日朝にToBellのタスク／通知でお知らせします。",
    },
    "google.calendar": {
        "tool": "google",
        "label": "重要タスクをGoogleカレンダーに送る",
        "description": "選んだタスクの期限をGoogleカレンダー（メイン）にイベントとして追加します。片方向（ToBell→カレンダー）で、完了時は完了印が付きます。",
    },
    "google.calendar_import": {
        "tool": "google",
        "label": "Googleカレンダーの予定をタスクに取り込む",
        "description": "Googleカレンダー（メイン）の予定を定期的に取り込み、ToBellタスクを自動作成します。片方向（カレンダー→ToBell）。取り込む範囲（末尾TBの予定のみ／自分が主催／全予定）と間隔（手動／15分〜1日）は設定で選べます。予定の変更・キャンセルにも追従します。",
    },
}


FILEPOST_OVERFLOW_THRESHOLD = 25 * 1024 * 1024


# 連携キー → その連携が読み書きする DSTT ツール。
#
# ここに載るツールは access_control 上 sensitive（個別付与が必要）なので、
# 「ToBell の個人トグルがON」だけを条件にすると権限の抜け道になる。たとえば
#   * pluslist / siteplus … ToBell の検索APIから名簿・現場マスタを全件引ける
#   * shiftersync (CloudShift) … 休暇種別変更申請・要代務シフト帳の更新が、
#     オプトインした「全ユーザー」へブロードキャストされる（宛先が自己選択できる）
# トグルは利用者が自分で押せるため、ツール自体のアクセス権も必須にする。
#
# 健診PLUS（health_check.linkage）はここに入れない。宛先は健診レコード側
# （管理担当者＋営業所の健康診断担当＋レコード個別の追加宛先）で決まり、
# 利用者が自分で宛先に入り込むことはできない。トグルは「自分宛の通知を受け取るか」
# を切り替えるだけなので、ここでゲートすると担当者への通知を止めるだけになる。
INTEGRATION_TOOL_REQUIREMENTS = {
    "cloudshift.leave_change_request": "shiftersync",
    "cloudshift.shift_update": "shiftersync",
    "pluslist.linkage": "pluslist",
    "siteplus.linkage": "siteplus",
}


# ToBell から見た「利用側ツールキー」。ツール間API専用許可の判定に使う
# （app/tool_api.py の TOOL_API_CONSUMERS に to_bell として宣言してある）。
INTEGRATION_CONSUMER_TOOL = "to_bell"


def has_tool_access(username: str, integration_key: str) -> bool:
    """連携先ツールへのアクセス権があるか。ツール指定の無い連携は常に True。

    連携先ツール本体の通常許可に加えて、「ツール間API専用許可」でも許可する。
    ToBell の紐付けは提供元ツールのマスタを ToBell 経由で参照する典型的な
    ツール間APIであり、返す項目も社員番号・氏名・営業所名などの識別情報に限られる。
    """
    tool_key = INTEGRATION_TOOL_REQUIREMENTS.get(integration_key)
    if tool_key is None:
        return True
    cache = _tool_access_cache()
    cache_key = (username, tool_key)
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        from app.access_control import username_has_tool_access

        allowed = username_has_tool_access(
            username, tool_key, via_tool=INTEGRATION_CONSUMER_TOOL
        )
    except Exception:  # noqa: BLE001 - 判定できないときは安全側（不許可）
        allowed = False
    if cache is not None:
        cache[cache_key] = allowed
    return allowed


def _tool_access_cache():
    """リクエスト内でのアクセス権判定のメモ化（健診の日次スイープ対策）。"""
    try:
        from flask import g, has_app_context

        if not has_app_context():
            return None
        cache = getattr(g, "_tb_tool_access_cache", None)
        if cache is None:
            cache = {}
            g._tb_tool_access_cache = cache
        return cache
    except Exception:  # noqa: BLE001
        return None


def allowed_integration_keys(username: str) -> set[str]:
    """この利用者が使ってよい連携キー。"""
    return {key for key in INTEGRATION_KEYS if has_tool_access(username, key)}


def _ensure_settings(username: str) -> ToBellUserSettings:
    row = ToBellUserSettings.query.filter_by(username=username).first()
    if row is None:
        row = ToBellUserSettings(username=username, integrations={}, preferences={})
        db.session.add(row)
        db.session.flush()
        invalidate_settings_cache(username)
    return row


def get_settings(username: str) -> dict[str, Any]:
    row = ToBellUserSettings.query.filter_by(username=username).first()
    integrations = (row.integrations if row and isinstance(row.integrations, dict) else {}) or {}
    preferences = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    allowed = allowed_integration_keys(username)
    return {
        # 連携先ツールへのアクセス権が無いものは、フラグが立っていても「無効」として返す。
        # （権限を外されたあともフラグだけ残っている、という状態を画面に出さない）
        "integrations": {
            key: bool(integrations.get(key, False)) and key in allowed for key in INTEGRATION_KEYS
        },
        "preferences": preferences,
        "catalog": [
            {
                "key": key,
                **meta,
                "available": key in allowed,
                "requires_tool": INTEGRATION_TOOL_REQUIREMENTS.get(key),
            }
            for key, meta in INTEGRATION_KEYS.items()
        ],
    }


def update_integrations(username: str, payload: dict[str, Any]) -> dict[str, Any]:
    incoming = payload.get("integrations") if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        incoming = {}
    row = _ensure_settings(username)
    current = dict(row.integrations or {})
    allowed = allowed_integration_keys(username)
    for key, value in incoming.items():
        if key in INTEGRATION_KEYS and key in allowed:
            current[key] = bool(value)
    row.integrations = current
    db.session.commit()
    invalidate_settings_cache(username)
    return get_settings(username)


def _settings_row_cached(username: str):
    """username → ToBellUserSettings をリクエスト内でメモ化して返す。

    健診PLUSの日次スイープは、レコード×宛先ごとに連携フラグと通知種別を
    引くため、素直にクエリすると数千本のSELECTになる。
    リクエスト境界を越えないので設定変更の反映は遅れない。
    """
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return ToBellUserSettings.query.filter_by(username=username).first()
        cache = getattr(g, "_tb_settings_cache", None)
        if cache is None:
            cache = {}
            g._tb_settings_cache = cache
    except Exception:
        return ToBellUserSettings.query.filter_by(username=username).first()
    if username not in cache:
        cache[username] = ToBellUserSettings.query.filter_by(username=username).first()
    return cache[username]


def invalidate_settings_cache(username: str | None = None) -> None:
    """設定を書き換えた直後にメモ化を捨てる。"""
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return
        cache = getattr(g, "_tb_settings_cache", None)
        if cache:
            if username is None:
                cache.clear()
            else:
                cache.pop(username, None)
        access_cache = getattr(g, "_tb_tool_access_cache", None)
        if access_cache:
            if username is None:
                access_cache.clear()
            else:
                for key in [k for k in access_cache if k[0] == username]:
                    access_cache.pop(key, None)
    except Exception:
        pass


def is_enabled(username: str, integration_key: str) -> bool:
    """ユーザー個人の連携許可フラグ。未設定は常にFalse。

    連携先ツールが sensitive な場合は、そのツールへのアクセス権も必須にする。
    フラグだけを見ると、利用者が自分でトグルを押すだけで sensitive ツールの
    データに到達できてしまうため、判定はここに一本化している。
    """
    if integration_key not in INTEGRATION_KEYS:
        return False
    row = _settings_row_cached(username)
    if row is None or not isinstance(row.integrations, dict):
        return False
    if not bool(row.integrations.get(integration_key, False)):
        return False
    return has_tool_access(username, integration_key)


def enabled_users(integration_key: str) -> list[str]:
    """指定の連携機能を許可しているユーザーのusername一覧。"""
    if integration_key not in INTEGRATION_KEYS:
        return []
    rows = ToBellUserSettings.query.all()
    candidates = [
        row.username
        for row in rows
        if (row.integrations if isinstance(row.integrations, dict) else {}).get(integration_key)
    ]
    if not candidates:
        return []
    tool_key = INTEGRATION_TOOL_REQUIREMENTS.get(integration_key)
    if tool_key is None:
        return candidates
    # アクセス権判定はユーザーごとに所属・付与を引くため、対象者を一度にまとめて解決する
    # （1人ずつ判定すると、CloudShift の一斉通知が人数ぶんの全件スキャンを伴う）。
    from app.access_control import usernames_with_tool_access

    # has_tool_access と同じ基準で判定する（キャッシュを共有するため、
    # ここだけ基準がずれると同じキーに別意味の結果が入ってしまう）。
    allowed = usernames_with_tool_access(
        candidates, tool_key, via_tool=INTEGRATION_CONSUMER_TOOL
    )
    cache = _tool_access_cache()
    if cache is not None:
        for username in candidates:
            cache[(username, tool_key)] = username in allowed
    return [username for username in candidates if username in allowed]


# 健診PLUSの通知種別（source_ref_type と一致）。
# ユーザーごとに個別ON/OFFでき、未設定は通知する（デフォルトON）。
HEALTH_CHECK_NOTIFY_KINDS = ("reservation", "night_second", "secondary_exam")


def get_health_check_notify(username: str) -> dict[str, bool]:
    """健診通知の種別別ON/OFF。未設定の種別は True（通知する）。"""
    row = _settings_row_cached(username)
    prefs = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    notify = prefs.get("health_check_notify")
    if not isinstance(notify, dict):
        notify = {}
    return {kind: bool(notify.get(kind, True)) for kind in HEALTH_CHECK_NOTIFY_KINDS}


def set_health_check_notify(username: str, kinds: Any) -> dict[str, bool]:
    """健診通知の種別別ON/OFFを保存する。渡された種別だけ更新する。"""
    row = _ensure_settings(username)
    prefs = dict(row.preferences or {})
    current = dict(prefs.get("health_check_notify") or {})
    if isinstance(kinds, dict):
        for kind in HEALTH_CHECK_NOTIFY_KINDS:
            if kind in kinds:
                current[kind] = bool(kinds[kind])
    prefs["health_check_notify"] = current
    row.preferences = prefs
    db.session.commit()
    invalidate_settings_cache(username)
    return {kind: bool(current.get(kind, True)) for kind in HEALTH_CHECK_NOTIFY_KINDS}


def health_check_notify_enabled(username: str, kind: str) -> bool:
    """マスターのオプトイン かつ その種別がONのときだけ True。"""
    if not is_enabled(username, "health_check.linkage"):
        return False
    return get_health_check_notify(username).get(kind, True)
