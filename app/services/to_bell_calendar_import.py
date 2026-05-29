"""Google Calendar → ToBell 取り込み（片方向）。

方針（確定仕様）:
- ユーザー個人ごとのオプトイン（連携キー ``google.calendar_import`` がON）。
- 取り込み範囲は3モードから選択:
    - ``tb_suffix`` … 予定名の末尾が「TB」の予定のみ（既定）。
    - ``organizer``  … 自分が主催の予定のみ。
    - ``all``        … メインカレンダーの全予定。
- 取り込み間隔は ``manual / 15m / 1h / 3h / 6h / 12h / 1d`` から選択（既定15分）。
  スケジューラは15分間隔で1度だけ起床し、各ユーザーの間隔に達した人だけを
  差分取得（syncToken）で処理する。サーバー負荷を抑えるための設計。
- 予定の変更・キャンセルにも追従する（更新／完了）。
- ToBell→Calendar で送った自前イベント（source.title == "ToBell"）は取り込まない（ループ防止）。

接続/トークン管理は ``to_bell_calendar`` を再利用する（OAuthスコープ calendar.events は読取も含む）。
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import requests

from app.models import ToBellGoogleAccount, ToBellNotification, ToBellTask, ToBellUserSettings, db
from app.services import to_bell_calendar
from app.services.to_bell_calendar import ToBellCalendarError
from app.services.to_bell_integrations import enabled_users, is_enabled

logger = logging.getLogger(__name__)

INTEGRATION_KEY = "google.calendar_import"
SOURCE_TOOL = "google"
SOURCE_REF_TYPE = "calendar_event"
_HTTP_TIMEOUT = 30

# 取り込みモード
MODE_TB_SUFFIX = "tb_suffix"
MODE_ORGANIZER = "organizer"
MODE_ALL = "all"
VALID_MODES = {MODE_TB_SUFFIX, MODE_ORGANIZER, MODE_ALL}
DEFAULT_MODE = MODE_TB_SUFFIX

# 取り込み間隔（分）。0 = 手動のみ（自動取り込みしない）。
INTERVAL_MINUTES = {
    "manual": 0,
    "15m": 15,
    "1h": 60,
    "3h": 180,
    "6h": 360,
    "12h": 720,
    "1d": 1440,
}
DEFAULT_INTERVAL = "15m"

# 初回（syncToken なし）の取り込み窓。
_INITIAL_PAST_DAYS = 7
_INITIAL_FUTURE_DAYS = 90

# 末尾「TB」マーカー。予定名の末尾が TB（スペース有無は問わない）なら取り込む。
_TB_SUFFIX_RE = re.compile(r"TB$", re.IGNORECASE)
_TB_STRIP_RE = re.compile(r"\s*TB\s*$", re.IGNORECASE)


class _SyncTokenExpired(RuntimeError):
    """syncToken が失効（HTTP 410）。フル再同期が必要。"""


# ----- タイムゾーン -----

def _timezone() -> str:
    return (os.environ.get("DSTT_TIMEZONE") or "Asia/Tokyo").strip() or "Asia/Tokyo"


# ----- ユーザー設定（preferences JSON に保存） -----

def _settings_row(username: str) -> Optional[ToBellUserSettings]:
    return ToBellUserSettings.query.filter_by(username=username).first()


def get_import_mode(username: str) -> str:
    row = _settings_row(username)
    prefs = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    value = str(prefs.get("gcal_import_mode") or "")
    return value if value in VALID_MODES else DEFAULT_MODE


def get_import_interval(username: str) -> str:
    row = _settings_row(username)
    prefs = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    value = str(prefs.get("gcal_import_interval") or "")
    return value if value in INTERVAL_MINUTES else DEFAULT_INTERVAL


def _save_pref(username: str, key: str, value: str) -> None:
    row = _settings_row(username)
    if row is None:
        row = ToBellUserSettings(username=username, integrations={}, preferences={})
        db.session.add(row)
        db.session.flush()
    prefs = dict(row.preferences or {})
    prefs[key] = value
    row.preferences = prefs
    db.session.commit()


def set_import_mode(username: str, mode: Any) -> str:
    value = str(mode or "").strip()
    if value not in VALID_MODES:
        raise ToBellCalendarError("取り込み範囲の指定が不正です。")
    _save_pref(username, "gcal_import_mode", value)
    return value


def set_import_interval(username: str, interval: Any) -> str:
    value = str(interval or "").strip()
    if value not in INTERVAL_MINUTES:
        raise ToBellCalendarError("取り込み間隔の指定が不正です。")
    _save_pref(username, "gcal_import_interval", value)
    return value


def import_status(username: str) -> dict[str, Any]:
    account = to_bell_calendar.get_account(username)
    return {
        "import_enabled": is_enabled(username, INTEGRATION_KEY),
        "import_mode": get_import_mode(username),
        "import_interval": get_import_interval(username),
        "last_import_at": (
            account.last_import_at.isoformat() if (account and account.last_import_at) else None
        ),
    }


# ----- Calendar API（読み取り） -----

def _rfc3339_utc(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _events_get(account: ToBellGoogleAccount, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{to_bell_calendar.CALENDAR_API_BASE}/calendars/{to_bell_calendar.PRIMARY_CALENDAR}/events"

    def _do(bearer: str) -> requests.Response:
        return requests.get(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            params=params,
            timeout=_HTTP_TIMEOUT,
        )

    token = to_bell_calendar.valid_access_token(account)
    try:
        resp = _do(token)
        if resp.status_code == 401:  # 失効 → 1回だけ強制リフレッシュ
            resp = _do(to_bell_calendar._refresh(account))
    except requests.RequestException as exc:
        raise ToBellCalendarError(f"Googleカレンダーへの接続に失敗しました: {exc}") from exc

    if resp.status_code == 410:  # syncToken 失効
        raise _SyncTokenExpired()
    if resp.status_code == 200:
        return resp.json()
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    raise ToBellCalendarError(f"Googleカレンダーの取得に失敗しました: {detail or resp.status_code}")


def _fetch_events(account: ToBellGoogleAccount, sync_token: Optional[str]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """全ページを取得し (events, next_sync_token) を返す。"""
    events: list[dict[str, Any]] = []
    next_sync_token: Optional[str] = None
    page_token: Optional[str] = None
    # 無限ループ防止に上限ページ数を設ける。
    for _ in range(40):
        params: dict[str, Any] = {"maxResults": 250, "singleEvents": "true"}
        if sync_token:
            params["syncToken"] = sync_token
            params["showDeleted"] = "true"  # キャンセル(status=cancelled)も受け取る
        else:
            now = datetime.utcnow()
            params["timeMin"] = _rfc3339_utc(now - timedelta(days=_INITIAL_PAST_DAYS))
            params["timeMax"] = _rfc3339_utc(now + timedelta(days=_INITIAL_FUTURE_DAYS))
            params["showDeleted"] = "false"
            params["orderBy"] = "startTime"
        if page_token:
            params["pageToken"] = page_token
        data = _events_get(account, params)
        events.extend(data.get("items", []) or [])
        next_sync_token = data.get("nextSyncToken") or next_sync_token
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return events, next_sync_token


# ----- イベント解釈 -----

def _parse_rfc3339(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo

            dt = dt.astimezone(ZoneInfo(_timezone()))
        except Exception:  # noqa: BLE001 - zoneinfo 未導入等はシステムローカルへ
            dt = dt.astimezone()
        dt = dt.replace(tzinfo=None)
    return dt.replace(microsecond=0)


def _event_due_at(event: dict[str, Any]) -> Optional[datetime]:
    start = event.get("start") or {}
    if start.get("date"):  # 終日イベント → 時刻なし扱い（23:59:59 = 明示時刻なし）
        try:
            day = date.fromisoformat(str(start["date"]))
        except ValueError:
            return None
        return datetime.combine(day, time(23, 59, 59))
    if start.get("dateTime"):
        return _parse_rfc3339(str(start["dateTime"]))
    return None


def _is_tobell_origin(event: dict[str, Any]) -> bool:
    """ToBell→Calendar で送った自前イベントは取り込まない（ループ防止）。"""
    source = event.get("source") or {}
    return source.get("title") == "ToBell"


def _matches_mode(event: dict[str, Any], mode: str, summary_text: str) -> bool:
    if mode == MODE_ALL:
        return True
    if mode == MODE_ORGANIZER:
        organizer = event.get("organizer") or {}
        creator = event.get("creator") or {}
        return bool(organizer.get("self") or creator.get("self"))
    # MODE_TB_SUFFIX
    return bool(_TB_SUFFIX_RE.search(summary_text.rstrip()))


def _title_for(event: dict[str, Any], mode: str, summary_text: str) -> str:
    title = summary_text
    if mode == MODE_TB_SUFFIX:
        stripped = _TB_STRIP_RE.sub("", summary_text).strip()
        title = stripped or summary_text
    title = title.strip()
    return (title or "(無題の予定)")[:240]


def _description_for(event: dict[str, Any]) -> str:
    parts: list[str] = []
    if event.get("description"):
        parts.append(str(event["description"]))
    if event.get("location"):
        parts.append(f"場所: {event['location']}")
    if event.get("htmlLink"):
        parts.append(f"Googleカレンダーで開く: {event['htmlLink']}")
    return "\n".join(parts).strip()[:2000]


# ----- タスク upsert -----

def _find_task(ref_id: str) -> Optional[ToBellTask]:
    return ToBellTask.query.filter_by(
        source_tool=SOURCE_TOOL,
        source_ref_type=SOURCE_REF_TYPE,
        source_ref_id=ref_id,
    ).first()


def _process_event(username: str, event: dict[str, Any], mode: str, summary: dict[str, int]) -> None:
    event_id = event.get("id")
    if not event_id:
        summary["skipped"] += 1
        return
    ref_id = f"{username}:{event_id}"
    existing = _find_task(ref_id)

    # キャンセル/削除 → 取り込み済みタスクを完了に追従。
    if event.get("status") == "cancelled":
        if existing and existing.status not in ("done", "archived"):
            existing.status = "done"
            existing.completed_at = datetime.utcnow()
            existing.updated_at = datetime.utcnow()
            for note in existing.notifications:
                note.is_resolved = True
                note.resolved_at = datetime.utcnow()
            summary["completed"] += 1
        else:
            summary["skipped"] += 1
        return

    if _is_tobell_origin(event):  # 自前イベントは無視
        summary["skipped"] += 1
        return

    summary_text = str(event.get("summary") or "")
    if not _matches_mode(event, mode, summary_text):
        summary["skipped"] += 1
        return

    due_at = _event_due_at(event)
    title = _title_for(event, mode, summary_text)
    description = _description_for(event)

    if existing is not None:
        if existing.status == "archived":  # ユーザーが削除済み → 復活させない
            summary["skipped"] += 1
            return
        changed = False
        if existing.title != title:
            existing.title = title
            changed = True
        if (existing.description or "") != description:
            existing.description = description
            changed = True
        if existing.due_at != due_at:
            existing.due_at = due_at
            changed = True
        if changed:
            existing.updated_at = datetime.utcnow()
            summary["updated"] += 1
        else:
            summary["skipped"] += 1
        return

    task = ToBellTask(
        title=title,
        description=description,
        status="todo",
        priority="normal",
        due_at=due_at,
        created_by=username,
        assigned_to=username,
        source_tool=SOURCE_TOOL,
        source_ref_type=SOURCE_REF_TYPE,
        source_ref_id=ref_id,
    )
    db.session.add(task)
    db.session.flush()
    db.session.add(
        ToBellNotification(
            user_id=username,
            task=task,
            source_tool=SOURCE_TOOL,
            event_type="calendar_import",
            title=title,
            body=(description[:400] if description else ""),
            href=str(event.get("htmlLink") or "/tools/to_bell")[:500],
            severity="info",
        )
    )
    summary["created"] += 1


# ----- 公開API -----

def import_for_user(username: str) -> dict[str, int]:
    """1ユーザー分の取り込みを実行する（ユーザー操作・スケジューラ共通）。"""
    if not is_enabled(username, INTEGRATION_KEY):
        raise ToBellCalendarError("カレンダー取り込み連携が無効です。設定で有効化してください。")
    account = to_bell_calendar.get_account(username)
    if account is None or not account.refresh_token:
        raise ToBellCalendarError("Googleアカウントが未接続です。設定から接続してください。")

    mode = get_import_mode(username)
    sync_token = account.calendar_sync_token
    try:
        events, next_token = _fetch_events(account, sync_token)
    except _SyncTokenExpired:
        account.calendar_sync_token = None
        db.session.commit()
        events, next_token = _fetch_events(account, None)

    summary = {"created": 0, "updated": 0, "completed": 0, "skipped": 0}
    for event in events:
        _process_event(username, event, mode, summary)

    if next_token:
        account.calendar_sync_token = next_token
    account.last_import_at = datetime.utcnow()
    db.session.commit()
    return summary


def run_due_imports(*, now: datetime | None = None) -> dict[str, int]:
    """スケジューラから呼ぶ。間隔に達したユーザーだけを取り込む。"""
    now = now or datetime.utcnow()
    stats = {"users": 0, "ran": 0, "failed": 0}
    for username in enabled_users(INTEGRATION_KEY):
        stats["users"] += 1
        try:
            account = to_bell_calendar.get_account(username)
            if account is None or not account.refresh_token:
                continue
            minutes = INTERVAL_MINUTES.get(get_import_interval(username), 0)
            if minutes <= 0:  # 手動のみ
                continue
            last = account.last_import_at
            # tick(15分)由来のドリフトで取りこぼさないよう60秒の余裕を持たせる。
            if last is not None and (now - last) < timedelta(minutes=minutes) - timedelta(seconds=60):
                continue
            import_for_user(username)
            stats["ran"] += 1
        except Exception:  # noqa: BLE001 - 1ユーザーの失敗を他へ波及させない
            stats["failed"] += 1
            logger.warning("Calendar→ToBell 取り込みに失敗しました（user=%s）", username, exc_info=True)
            db.session.rollback()
    return stats
