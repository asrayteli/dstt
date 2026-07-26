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
    return {
        "integrations": {key: bool(integrations.get(key, False)) for key in INTEGRATION_KEYS},
        "preferences": preferences,
        "catalog": [
            {"key": key, **meta}
            for key, meta in INTEGRATION_KEYS.items()
        ],
    }


def update_integrations(username: str, payload: dict[str, Any]) -> dict[str, Any]:
    incoming = payload.get("integrations") if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        incoming = {}
    row = _ensure_settings(username)
    current = dict(row.integrations or {})
    for key, value in incoming.items():
        if key in INTEGRATION_KEYS:
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
        if not cache:
            return
        if username is None:
            cache.clear()
        else:
            cache.pop(username, None)
    except Exception:
        pass


def is_enabled(username: str, integration_key: str) -> bool:
    """ユーザー個人の連携許可フラグ。未設定は常にFalse。"""
    if integration_key not in INTEGRATION_KEYS:
        return False
    row = _settings_row_cached(username)
    if row is None or not isinstance(row.integrations, dict):
        return False
    return bool(row.integrations.get(integration_key, False))


def enabled_users(integration_key: str) -> list[str]:
    """指定の連携機能を許可しているユーザーのusername一覧。"""
    if integration_key not in INTEGRATION_KEYS:
        return []
    rows = ToBellUserSettings.query.all()
    result: list[str] = []
    for row in rows:
        integrations = row.integrations if isinstance(row.integrations, dict) else {}
        if integrations.get(integration_key):
            result.append(row.username)
    return result


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
