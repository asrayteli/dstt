"""ToBell → Google Calendar 片方向連携。

方針（確定仕様）:
- 連携対象は「タスクごとの手動トグル」。ユーザーが選んだタスクだけ送る。
- 送り先はユーザーのメインカレンダー(primary)。
- イベント型は due_at の時刻有無で自動判定（時刻あり=時間イベント / 日付のみ=終日）。
- タスク完了時はイベントを残し、タイトルに ✓ を付け色を変える（完了印）。
- リマインダーはユーザー共通設定＋タスク個別上書き。
- 連携はユーザー個人オプトイン（デフォルトOFF）。APIエラーは握りつぶしToBell本体を止めない。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

from app.models import ToBellGoogleAccount, ToBellTask, ToBellUserSettings, db
from app.services import google_oauth
from app.services.to_bell_integrations import is_enabled
from app.services.to_bell_service import has_explicit_notification_time

logger = logging.getLogger(__name__)

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
PRIMARY_CALENDAR = "primary"
INTEGRATION_KEY = "google.calendar"
_HTTP_TIMEOUT = 15

# 完了印に使う Google Calendar の colorId（8 = グラファイト/グレー）。
COMPLETED_COLOR_ID = "8"
DONE_PREFIX = "✓ "

# ユーザーが未設定のときに適用する既定リマインダー（期限1日前にポップアップ）。
DEFAULT_REMINDERS: list[dict[str, Any]] = [{"method": "popup", "minutes": 1440}]
_VALID_REMINDER_METHODS = {"popup", "email"}
_MAX_REMINDER_MINUTES = 40320  # Google の上限（4週間）


class ToBellCalendarError(RuntimeError):
    """ユーザーに見せてよい連携エラー。"""


# ----- タイムゾーン -----

def _timezone() -> str:
    return (os.environ.get("DSTT_TIMEZONE") or "Asia/Tokyo").strip() or "Asia/Tokyo"


# ----- アカウント接続 -----

def get_account(username: str) -> Optional[ToBellGoogleAccount]:
    return ToBellGoogleAccount.query.filter_by(username=username).first()


def is_connected(username: str) -> bool:
    account = get_account(username)
    return bool(account and account.refresh_token)


def connection_status(username: str) -> dict[str, Any]:
    account = get_account(username)
    return {
        "configured": google_oauth.is_configured(),
        "integration_enabled": is_enabled(username, INTEGRATION_KEY),
        "connected": bool(account and account.refresh_token),
        "google_email": (account.google_email if account else "") or "",
        "connected_at": account.connected_at.isoformat() if (account and account.connected_at) else None,
        "default_reminders": get_default_reminders(username),
    }


def store_connection(username: str, token_response: dict[str, Any]) -> ToBellGoogleAccount:
    """code 交換で得たトークンを保存する。"""
    access_token = str(token_response.get("access_token") or "")
    refresh_token = str(token_response.get("refresh_token") or "")
    expires_in = int(token_response.get("expires_in") or 0)
    scope = str(token_response.get("scope") or "")

    email = ""
    if access_token:
        info = google_oauth.fetch_userinfo(access_token)
        email = str(info.get("email") or "")

    account = get_account(username)
    if account is None:
        account = ToBellGoogleAccount(username=username)
        db.session.add(account)
    if refresh_token:  # 再同意時など、refresh_token が来たときだけ更新する
        account.refresh_token = refresh_token
    account.access_token = access_token or account.access_token
    account.token_expiry = datetime.utcnow() + timedelta(seconds=max(0, expires_in - 60)) if expires_in else None
    account.scope = scope or account.scope
    if email:
        account.google_email = email
    if account.connected_at is None:
        account.connected_at = datetime.utcnow()
    db.session.commit()
    return account


def disconnect(username: str) -> None:
    """トークンを失効・削除し、当該ユーザーが同期したイベント参照を外す。"""
    account = get_account(username)
    if account is not None:
        token = account.refresh_token or account.access_token
        if token:
            google_oauth.revoke(token)
        db.session.delete(account)
    # このユーザーが同期したタスクのイベント参照をクリア（UIの整合のため）
    rows = ToBellTask.query.filter_by(gcal_synced_by=username).all()
    for task in rows:
        task.gcal_event_id = None
        task.gcal_synced_by = None
    db.session.commit()


def valid_access_token(account: ToBellGoogleAccount) -> str:
    """期限切れなら refresh して有効な access_token を返す。"""
    if account is None or not account.refresh_token:
        raise ToBellCalendarError("Googleアカウントが未接続です。")
    now = datetime.utcnow()
    if account.access_token and account.token_expiry and account.token_expiry > now:
        return account.access_token
    return _refresh(account)


def _refresh(account: ToBellGoogleAccount) -> str:
    try:
        token_response = google_oauth.refresh_access_token(account.refresh_token)
    except google_oauth.GoogleOAuthError as exc:
        raise ToBellCalendarError(str(exc)) from exc
    access_token = str(token_response.get("access_token") or "")
    expires_in = int(token_response.get("expires_in") or 0)
    if not access_token:
        raise ToBellCalendarError("access_token を更新できませんでした。再接続が必要です。")
    account.access_token = access_token
    account.token_expiry = datetime.utcnow() + timedelta(seconds=max(0, expires_in - 60)) if expires_in else None
    db.session.commit()
    return access_token


# ----- リマインダー設定 -----

def normalize_reminders(value: Any) -> Optional[list[dict[str, Any]]]:
    """リマインダー入力を検証・正規化する。

    None を返すと「ユーザー既定を使う」を意味する。
    空リストは「リマインダーなし」を意味する。
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise ToBellCalendarError("リマインダーの形式が不正です。")
    out: list[dict[str, Any]] = []
    for item in value[:5]:  # Google の上限に配慮し最大5件
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "popup")
        if method not in _VALID_REMINDER_METHODS:
            method = "popup"
        try:
            minutes = int(item.get("minutes"))
        except (TypeError, ValueError):
            continue
        minutes = max(0, min(_MAX_REMINDER_MINUTES, minutes))
        out.append({"method": method, "minutes": minutes})
    return out


def get_default_reminders(username: str) -> list[dict[str, Any]]:
    row = ToBellUserSettings.query.filter_by(username=username).first()
    prefs = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    stored = prefs.get("gcal_reminders")
    normalized = normalize_reminders(stored) if stored is not None else None
    return normalized if normalized is not None else list(DEFAULT_REMINDERS)


def set_default_reminders(username: str, reminders: Any) -> list[dict[str, Any]]:
    normalized = normalize_reminders(reminders)
    if normalized is None:
        normalized = list(DEFAULT_REMINDERS)
    row = ToBellUserSettings.query.filter_by(username=username).first()
    if row is None:
        row = ToBellUserSettings(username=username, integrations={}, preferences={})
        db.session.add(row)
        db.session.flush()
    prefs = dict(row.preferences or {})
    prefs["gcal_reminders"] = normalized
    row.preferences = prefs
    db.session.commit()
    return normalized


def _reminders_for_task(task: ToBellTask, username: str) -> list[dict[str, Any]]:
    override = task.gcal_reminder_override
    if override is not None:
        normalized = normalize_reminders(override)
        if normalized is not None:
            return normalized
    return get_default_reminders(username)


# ----- イベント本文 -----

def _task_url(task: ToBellTask) -> str:
    try:
        from flask import url_for

        return url_for("to_bell.index", _external=True) + f"?task={task.id}"
    except Exception:  # noqa: BLE001 - リクエスト外（スケジューラ等）でも壊さない
        return f"/tools/to_bell?task={task.id}"


def _event_body(task: ToBellTask, username: str) -> dict[str, Any]:
    is_done = task.status == "done"
    summary = (DONE_PREFIX if is_done else "") + (task.title or "(無題)")

    description_parts = []
    if task.description:
        description_parts.append(task.description)
    description_parts.append(f"\nToBellで開く: {_task_url(task)}")
    description = "\n".join(description_parts).strip()

    body: dict[str, Any] = {
        "summary": summary[:1000],
        "description": description[:8000],
        "source": {"title": "ToBell", "url": _task_url(task)},
    }

    if has_explicit_notification_time(task):
        start = task.due_at
        end = start + timedelta(minutes=30)
        tz = _timezone()
        body["start"] = {"dateTime": start.replace(microsecond=0).isoformat(), "timeZone": tz}
        body["end"] = {"dateTime": end.replace(microsecond=0).isoformat(), "timeZone": tz}
    else:
        day = (task.due_at or datetime.utcnow()).date()
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (day + timedelta(days=1)).isoformat()}

    reminders = _reminders_for_task(task, username)
    body["reminders"] = {"useDefault": False, "overrides": reminders}

    if is_done:
        body["colorId"] = COMPLETED_COLOR_ID

    return body


# ----- Calendar API 呼び出し -----

def _calendar_request(method: str, path: str, account: ToBellGoogleAccount, *, json: Any = None) -> dict[str, Any]:
    token = valid_access_token(account)
    url = f"{CALENDAR_API_BASE}{path}"

    def _do(bearer: str) -> requests.Response:
        return requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
            json=json,
            timeout=_HTTP_TIMEOUT,
        )

    try:
        resp = _do(token)
        if resp.status_code == 401:  # アクセストークン失効 → 1回だけ強制リフレッシュ
            resp = _do(_refresh(account))
    except requests.RequestException as exc:
        raise ToBellCalendarError(f"Googleカレンダーへの接続に失敗しました: {exc}") from exc

    if resp.status_code in (200, 201):
        return resp.json()
    if resp.status_code in (204, 404, 410):
        # 204=成功(本文なし)、404/410=既に存在しない（削除は冪等扱い）
        return {}
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    raise ToBellCalendarError(f"Googleカレンダー操作に失敗しました: {detail or resp.status_code}")


def _create_event(task: ToBellTask, username: str, account: ToBellGoogleAccount) -> None:
    result = _calendar_request(
        "POST",
        f"/calendars/{PRIMARY_CALENDAR}/events",
        account,
        json=_event_body(task, username),
    )
    event_id = result.get("id")
    if not event_id:
        raise ToBellCalendarError("イベントIDを取得できませんでした。")
    task.gcal_event_id = event_id
    task.gcal_synced_by = username


def _patch_event(task: ToBellTask, account: ToBellGoogleAccount) -> None:
    username = task.gcal_synced_by or account.username
    _calendar_request(
        "PATCH",
        f"/calendars/{PRIMARY_CALENDAR}/events/{task.gcal_event_id}",
        account,
        json=_event_body(task, username),
    )


def _delete_event(task: ToBellTask, account: ToBellGoogleAccount) -> None:
    _calendar_request(
        "DELETE",
        f"/calendars/{PRIMARY_CALENDAR}/events/{task.gcal_event_id}",
        account,
    )


# ----- 公開API: トグル（ユーザー操作なのでエラーは伝える） -----

def enable_for_task(task: ToBellTask, username: str, reminder_override: Any = None) -> dict[str, Any]:
    """タスクをカレンダーに追加（既にあれば更新）。"""
    if not is_enabled(username, INTEGRATION_KEY):
        raise ToBellCalendarError("Googleカレンダー連携が無効です。設定で有効化してください。")
    # Googleカレンダーから取り込んだタスクは送り返さない。
    # 送ると同じ予定が二重に作られ、編集が分岐するため（取り込み元イベントとは別の新規イベントになる）。
    if task.source_tool == "google" and task.source_ref_type == "calendar_event":
        raise ToBellCalendarError("このタスクはGoogleカレンダーから取り込んだ予定です。カレンダーへ送り返すことはできません。")
    account = get_account(username)
    if account is None or not account.refresh_token:
        raise ToBellCalendarError("Googleアカウントが未接続です。設定から接続してください。")
    if task.due_at is None:
        raise ToBellCalendarError("期限が設定されていないタスクはカレンダーに追加できません。")

    if reminder_override == "default":
        task.gcal_reminder_override = None
    elif reminder_override is not None:
        task.gcal_reminder_override = normalize_reminders(reminder_override)

    if task.gcal_event_id and task.gcal_synced_by == username:
        _patch_event(task, account)
    else:
        _create_event(task, username, account)
    db.session.commit()
    return {"gcal_synced": True, "gcal_reminder_override": task.gcal_reminder_override}


def disable_for_task(task: ToBellTask, username: str) -> dict[str, Any]:
    """タスクをカレンダーから外す（イベント削除）。"""
    if not task.gcal_event_id:
        return {"gcal_synced": False}
    account = get_account(task.gcal_synced_by or username)
    if account is not None and account.refresh_token:
        try:
            _delete_event(task, account)
        except ToBellCalendarError:
            logger.warning("カレンダーイベント削除に失敗（参照は外します）", exc_info=True)
    task.gcal_event_id = None
    task.gcal_synced_by = None
    task.gcal_reminder_override = None
    db.session.commit()
    return {"gcal_synced": False}


# ----- 公開API: 自動同期（補助機能なのでエラーは握りつぶす） -----

def on_task_changed(task: ToBellTask) -> None:
    """タスクの更新/完了/再オープン時にイベントを追従させる。"""
    if not task.gcal_event_id or not task.gcal_synced_by:
        return
    try:
        account = get_account(task.gcal_synced_by)
        if account is None or not account.refresh_token:
            return
        _patch_event(task, account)
        db.session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("ToBell→Calendar の自動同期に失敗しました", exc_info=True)
        db.session.rollback()


def on_task_deleted(task: ToBellTask) -> None:
    """タスク削除時にイベントも削除する。"""
    if not task.gcal_event_id or not task.gcal_synced_by:
        return
    try:
        account = get_account(task.gcal_synced_by)
        if account is None or not account.refresh_token:
            return
        _delete_event(task, account)
    except Exception:  # noqa: BLE001
        logger.warning("ToBell→Calendar の削除同期に失敗しました", exc_info=True)
