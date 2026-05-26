from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import ToBellPushDelivery, ToBellPushSubscription, ToBellTask, db
from app.services.to_bell_service import has_explicit_notification_time, task_notification_targets

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


def list_subscriptions(user_id: str) -> list[dict[str, Any]]:
    rows = ToBellPushSubscription.query.filter_by(user_id=user_id).order_by(
        ToBellPushSubscription.is_active.desc(),
        ToBellPushSubscription.updated_at.desc(),
    ).all()
    return [_subscription_payload(row) for row in rows]


def update_subscription_label(user_id: str, subscription_id: int, label: str) -> dict[str, Any]:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        raise ValueError("通知先が見つかりません。")
    row.device_label = str(label or "").strip()[:120] or _device_label(row.user_agent)
    db.session.commit()
    return _subscription_payload(row)


def deactivate_subscription(user_id: str, subscription_id: int) -> bool:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def delete_subscription(user_id: str, subscription_id: int) -> bool:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def send_test_push(user_id: str) -> dict[str, int]:
    return send_push_to_user(
        user_id,
        title="ToBell テスト通知 from DSTT",
        body="この端末のプッシュ通知は有効です。",
        url="/tools/to_bell",
    )


def send_due_task_pushes(*, now: datetime | None = None) -> dict[str, int]:
    # due_at は利用者が入力したローカル時刻のナイーブな値として保存されるため、
    # 比較も utcnow() ではなくローカルの now() を使う（時差で通知がずれる不具合を防ぐ）。
    now = now or datetime.now()
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
        if not has_explicit_notification_time(task):
            skipped += 1
            continue
        due_key = task.due_at.isoformat(timespec="minutes") if task.due_at else ""
        for user_id in task_notification_targets(task):
            delivery = ToBellPushDelivery.query.filter_by(
                task_id=task.id,
                user_id=user_id,
                due_at_key=due_key,
            ).first()
            # 配信済み（sent）・送信先なし（skipped）は確定状態なので再送しない。
            # 送信失敗（failed）は一時的な障害の可能性があるため再送対象とする。
            if delivery is not None and delivery.status != "failed":
                skipped += 1
                continue
            result = send_push_to_user(
                user_id,
                title=f"ToBell {task.title} from DSTT",
                body=task.description or "",
                url=f"/tools/to_bell?task={task.id}",
            )
            sent += result["sent"]
            failed += result["failed"]
            if result["sent"]:
                status = "sent"
            elif result["failed"]:
                # 全件失敗。配信済みとして記録せず、次回スケジュールで再送する。
                status = "failed"
            else:
                # 有効な購読が無い。後から購読した端末へ過去分が一斉配信
                # されるのを防ぐため確定状態として記録する。
                status = "skipped"
            if delivery is None:
                delivery = ToBellPushDelivery(
                    task_id=task.id,
                    user_id=user_id,
                    due_at_key=due_key,
                    status=status,
                )
                db.session.add(delivery)
            else:
                delivery.status = status
            try:
                db.session.commit()
            except IntegrityError:
                # 複数ワーカーが同時に同じ配信を記録した場合のレース。
                # 既に他ワーカーが確定済みなので無視してよい。
                db.session.rollback()
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
    subject = _vapid_subject()
    for subscription in subscriptions:
        # pywebpush は渡した claims を破壊的に書き換える（配信先の origin を aud、
        # 期限を exp として追記する）。dict を使い回すと 2 件目以降の aud が
        # 1 件目の配信先のままになり、別の push サービス（例: iPhone は
        # web.push.apple.com、PC は fcm.googleapis.com）で署名が一致せず失敗する。
        # そのため購読ごとに新しい claims を渡す。
        claims = {"sub": subject}
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
        except Exception as exc:  # noqa: BLE001 - 1件の配信失敗で全体を止めない
            failed += 1
            logger.warning("To Bell push error for %s: %s", user_id, exc)
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


def _subscription_payload(row: ToBellPushSubscription) -> dict[str, Any]:
    endpoint = row.endpoint or ""
    return {
        "id": row.id,
        "device_label": row.device_label or _device_label(row.user_agent),
        "user_agent": row.user_agent or "",
        "endpoint_tail": endpoint[-18:] if endpoint else "",
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _config_bool(app, key: str, env_key: str, default: bool = True) -> bool:
    value = app.config.get(key)
    if value is None:
        value = os.environ.get(env_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
