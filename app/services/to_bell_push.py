from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app

from app.models import ToBellPushDelivery, ToBellPushSubscription, ToBellTask, db
from app.services.to_bell_service import task_notification_targets

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover
    BackgroundScheduler = None

try:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover
    Vapid = None
    WebPushException = Exception
    b64urlencode = None
    serialization = None
    webpush = None


logger = logging.getLogger(__name__)
_scheduler = None
_scheduler_lock = threading.Lock()


class ToBellPushUnavailable(RuntimeError):
    pass


def vapid_public_key() -> str:
    vapid = _load_or_create_vapid()
    if serialization is None or b64urlencode is None:
        raise ToBellPushUnavailable("Web Push ライブラリが利用できません。")
    public_bytes = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(public_bytes)


def save_subscription(user_id: str, payload: dict[str, Any], *, user_agent: str = "") -> ToBellPushSubscription:
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("購読情報が不正です。")
    row = ToBellPushSubscription.query.filter_by(endpoint=endpoint).first()
    if row is None:
        row = ToBellPushSubscription(endpoint=endpoint)
        db.session.add(row)
    row.user_id = user_id
    row.p256dh = p256dh
    row.auth = auth
    row.user_agent = user_agent[:500]
    row.device_label = _device_label(user_agent)
    row.is_active = True
    db.session.commit()
    return row


def unsubscribe(user_id: str, endpoint: str) -> bool:
    row = ToBellPushSubscription.query.filter_by(endpoint=endpoint, user_id=user_id).first()
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def send_test_push(user_id: str) -> dict[str, int]:
    return send_push_to_user(
        user_id,
        title="To Bell テスト通知",
        body="iPhone/PCのプッシュ通知が有効です。",
        url="/tools/to_bell",
    )


def send_due_task_pushes(*, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=60)
    tasks = ToBellTask.query.filter(
        ToBellTask.status.notin_(["done", "archived"]),
        ToBellTask.due_at.isnot(None),
        ToBellTask.due_at <= now,
        ToBellTask.due_at >= cutoff,
    ).all()
    sent = 0
    skipped = 0
    failed = 0
    for task in tasks:
        due_key = task.due_at.isoformat(timespec="minutes") if task.due_at else ""
        for user_id in task_notification_targets(task):
            existing = ToBellPushDelivery.query.filter_by(
                task_id=task.id,
                user_id=user_id,
                due_at_key=due_key,
            ).first()
            if existing:
                skipped += 1
                continue
            result = send_push_to_user(
                user_id,
                title=f"To Bell: {task.title}",
                body=f"通知日時になりました: {task.due_at.strftime('%Y-%m-%d %H:%M') if task.due_at else ''}",
                url=f"/tools/to_bell?task={task.id}",
            )
            status = "sent" if result["sent"] else "failed"
            sent += result["sent"]
            failed += result["failed"]
            db.session.add(
                ToBellPushDelivery(
                    task_id=task.id,
                    user_id=user_id,
                    due_at_key=due_key,
                    status=status,
                )
            )
            db.session.commit()
    return {"sent": sent, "skipped": skipped, "failed": failed}


def send_push_to_user(user_id: str, *, title: str, body: str, url: str) -> dict[str, int]:
    if webpush is None:
        raise ToBellPushUnavailable("pywebpush がインストールされていません。")
    private_key_path = _vapid_private_key_path()
    if not private_key_path.exists():
        _load_or_create_vapid()
    subscriptions = ToBellPushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    sent = 0
    failed = 0
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
            "icon": "/static/img/android-chrome-192x192.png",
            "badge": "/static/img/apple-touch-icon.png",
        },
        ensure_ascii=False,
    )
    claims = {"sub": _vapid_subject()}
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription.to_subscription_info(),
                data=payload,
                vapid_private_key=str(private_key_path),
                vapid_claims=claims,
                ttl=3600,
                timeout=8,
            )
            sent += 1
        except WebPushException as exc:
            failed += 1
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                subscription.is_active = False
                db.session.commit()
            logger.warning("To Bell push failed for %s: %s", user_id, exc)
    return {"sent": sent, "failed": failed}


def init_to_bell_push_scheduler(app) -> None:
    if app.config.get("TESTING"):
        return
    if not _config_bool(app, "TO_BELL_PUSH_SCHEDULER_ENABLED", "DSTT_TO_BELL_PUSH_SCHEDULER", True):
        return
    if BackgroundScheduler is None:
        logger.warning("APScheduler is not installed; To Bell push scheduler is disabled")
        return

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=lambda: _run_due_push_job(app),
            trigger="interval",
            minutes=1,
            id="to_bell_due_pushes",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        _scheduler.start()


def _run_due_push_job(app) -> None:
    with app.app_context():
        try:
            send_due_task_pushes()
        except Exception as exc:
            db.session.rollback()
            logger.warning("To Bell push scheduler failed: %s", exc)


def _load_or_create_vapid():
    if Vapid is None:
        raise ToBellPushUnavailable("Web Push ライブラリが利用できません。")
    private_path = _vapid_private_key_path()
    public_path = _vapid_public_key_path()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    vapid = Vapid()
    if private_path.exists():
        return vapid.from_pem(private_path.read_bytes())
    vapid.generate_keys()
    private_path.write_bytes(vapid.private_pem())
    public_path.write_bytes(vapid.public_pem())
    return vapid


def _vapid_private_key_path() -> Path:
    configured = current_app.config.get("TO_BELL_VAPID_PRIVATE_KEY_PATH") or os.environ.get("DSTT_TO_BELL_VAPID_PRIVATE_KEY_PATH")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "to_bell_vapid_private.pem"


def _vapid_public_key_path() -> Path:
    configured = current_app.config.get("TO_BELL_VAPID_PUBLIC_KEY_PATH") or os.environ.get("DSTT_TO_BELL_VAPID_PUBLIC_KEY_PATH")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "to_bell_vapid_public.pem"


def _vapid_subject() -> str:
    return (
        current_app.config.get("TO_BELL_VAPID_SUBJECT")
        or os.environ.get("DSTT_TO_BELL_VAPID_SUBJECT")
        or "mailto:admin@example.com"
    )


def _device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "windows" in ua:
        return "Windows"
    return "Web Push"


def _config_bool(app, key: str, env_key: str, default: bool = True) -> bool:
    value = app.config.get(key)
    if value is None:
        value = os.environ.get(env_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
