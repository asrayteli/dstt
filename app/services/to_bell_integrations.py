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
    "google.calendar": {
        "tool": "google",
        "label": "重要タスクをGoogleカレンダーに送る",
        "description": "選んだタスクの期限をGoogleカレンダー（メイン）にイベントとして追加します。片方向（ToBell→カレンダー）で、完了時は完了印が付きます。",
    },
}


FILEPOST_OVERFLOW_THRESHOLD = 25 * 1024 * 1024


def _ensure_settings(username: str) -> ToBellUserSettings:
    row = ToBellUserSettings.query.filter_by(username=username).first()
    if row is None:
        row = ToBellUserSettings(username=username, integrations={}, preferences={})
        db.session.add(row)
        db.session.flush()
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
    return get_settings(username)


def is_enabled(username: str, integration_key: str) -> bool:
    """ユーザー個人の連携許可フラグ。未設定は常にFalse。"""
    if integration_key not in INTEGRATION_KEYS:
        return False
    row = ToBellUserSettings.query.filter_by(username=username).first()
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
