"""CloudShift の ViewPWA 共有先向け Web Push 配信。

ToBell の to_bell_push を参考にした、CloudShift 専用の独立実装。
VAPID 鍵・購読テーブル・配信ロジックを ToBell と分離して持つことで、
ToBell 側の挙動に影響を与えずに運用できる。

購読の重複防止（同端末・同シフト帳で 1 件、最新で上書き）は
CloudShiftPwaSubscription の (project_id, device_id) 一意制約で担保する。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import CloudShiftPwaSubscription, db

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


class CloudShiftPushUnavailable(RuntimeError):
    pass


def push_available() -> bool:
    return webpush is not None and Vapid is not None


def vapid_public_key() -> str:
    vapid = _load_or_create_vapid()
    if serialization is None or b64urlencode is None:
        raise CloudShiftPushUnavailable("Web Push ライブラリが利用できません。")
    public_bytes = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(public_bytes)


def save_subscription(
    project_id: str,
    device_id: str,
    payload: dict[str, Any],
    *,
    user_agent: str = "",
) -> CloudShiftPwaSubscription:
    project_id = str(project_id or "").strip()
    device_id = str(device_id or "").strip()[:64]
    if not project_id or not device_id:
        raise ValueError("購読情報が不正です。")
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("購読情報が不正です。")

    # (project_id, device_id) で 1 件に保ち、最も直近のリクエストで上書きする。
    row = CloudShiftPwaSubscription.query.filter_by(
        project_id=project_id,
        device_id=device_id,
    ).first()
    if row is None:
        row = CloudShiftPwaSubscription(project_id=project_id, device_id=device_id)
        db.session.add(row)
    row.endpoint = endpoint
    row.p256dh = p256dh
    row.auth = auth
    row.user_agent = (user_agent or "")[:500]
    row.device_label = _device_label(user_agent)
    row.is_active = True
    try:
        db.session.commit()
    except IntegrityError:
        # 別ワーカーが同じ (project_id, device_id) を同時に作った場合は読み直して上書き。
        db.session.rollback()
        row = CloudShiftPwaSubscription.query.filter_by(
            project_id=project_id,
            device_id=device_id,
        ).first()
        if row is None:
            raise
        row.endpoint = endpoint
        row.p256dh = p256dh
        row.auth = auth
        row.user_agent = (user_agent or "")[:500]
        row.device_label = _device_label(user_agent)
        row.is_active = True
        db.session.commit()
    return row


def list_project_subscriptions(project_id: str) -> list[dict[str, Any]]:
    """シフト帳に登録された通知先（ViewPWA 購読）の一覧を返す。"""
    rows = (
        CloudShiftPwaSubscription.query.filter_by(project_id=str(project_id or "").strip())
        .order_by(
            CloudShiftPwaSubscription.is_active.desc(),
            CloudShiftPwaSubscription.updated_at.desc(),
        )
        .all()
    )
    return [_subscription_payload(row) for row in rows]


def update_project_subscription_label(
    project_id: str, subscription_id: int, label: str
) -> dict[str, Any]:
    row = _find_project_subscription(project_id, subscription_id)
    if row is None:
        raise ValueError("通知先が見つかりません。")
    row.device_label = str(label or "").strip()[:120] or _device_label(row.user_agent)
    db.session.commit()
    return _subscription_payload(row)


def deactivate_project_subscription(project_id: str, subscription_id: int) -> bool:
    row = _find_project_subscription(project_id, subscription_id)
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def delete_project_subscription(project_id: str, subscription_id: int) -> bool:
    row = _find_project_subscription(project_id, subscription_id)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def _find_project_subscription(project_id: str, subscription_id: int):
    return CloudShiftPwaSubscription.query.filter_by(
        id=subscription_id,
        project_id=str(project_id or "").strip(),
    ).first()


def _subscription_payload(row: CloudShiftPwaSubscription) -> dict[str, Any]:
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


def unsubscribe(project_id: str, device_id: str) -> bool:
    row = CloudShiftPwaSubscription.query.filter_by(
        project_id=str(project_id or "").strip(),
        device_id=str(device_id or "").strip(),
    ).first()
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def send_push_to_project(
    project_id: str,
    *,
    title: str,
    body: str,
    url: str,
    latest_only: bool = False,
) -> dict[str, int]:
    """ViewPWA 購読者へ通知を送る。

    現場シフトは複数人が閲覧するため、有効な購読すべてに送る（既定）。
    個人シフトは基本的に一人しか見ないため、``latest_only`` を指定すると
    「最も直近に通知を有効化した端末」1 件だけに送る。
    """
    if webpush is None:
        raise CloudShiftPushUnavailable("pywebpush がインストールされていません。")
    private_key_path = _vapid_private_key_path()
    if not private_key_path.exists():
        _load_or_create_vapid()
    query = CloudShiftPwaSubscription.query.filter_by(
        project_id=str(project_id or "").strip(),
        is_active=True,
    )
    if latest_only:
        # 「有効化ボタンを押した最も直近のデバイス」のみを対象にする。
        query = query.order_by(CloudShiftPwaSubscription.updated_at.desc()).limit(1)
    subscriptions = query.all()
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
        # pywebpush は claims を破壊的に書き換えるため、購読ごとに新しい dict を渡す
        # （ToBell push と同様。複数の push サービス宛で aud が混ざるのを防ぐ）。
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
            logger.warning("CloudShift push failed for project %s: %s", project_id, exc)
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            failed += 1
            logger.warning("CloudShift push error for project %s: %s", project_id, exc)
    return {"sent": sent, "failed": failed}


def send_push_to_project_async(
    app,
    project_id: str,
    *,
    title: str,
    body: str,
    url: str,
    latest_only: bool = False,
) -> None:
    """保存リクエストの応答を遅らせないよう、別スレッドで送信する。"""

    def _worker() -> None:
        with app.app_context():
            try:
                send_push_to_project(
                    project_id,
                    title=title,
                    body=body,
                    url=url,
                    latest_only=latest_only,
                )
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("CloudShift async push failed for project %s: %s", project_id, exc)

    thread = threading.Thread(target=_worker, name="cloudshift-push", daemon=True)
    thread.start()


def _load_or_create_vapid():
    if Vapid is None:
        raise CloudShiftPushUnavailable("Web Push ライブラリが利用できません。")
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
    configured = current_app.config.get("CLOUDSHIFT_VAPID_PRIVATE_KEY_PATH") or os.environ.get(
        "DSTT_CLOUDSHIFT_VAPID_PRIVATE_KEY_PATH"
    )
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "cloudshift_vapid_private.pem"


def _vapid_public_key_path() -> Path:
    configured = current_app.config.get("CLOUDSHIFT_VAPID_PUBLIC_KEY_PATH") or os.environ.get(
        "DSTT_CLOUDSHIFT_VAPID_PUBLIC_KEY_PATH"
    )
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "cloudshift_vapid_public.pem"


def _vapid_subject() -> str:
    return (
        current_app.config.get("CLOUDSHIFT_VAPID_SUBJECT")
        or os.environ.get("DSTT_CLOUDSHIFT_VAPID_SUBJECT")
        or current_app.config.get("TO_BELL_VAPID_SUBJECT")
        or os.environ.get("DSTT_TO_BELL_VAPID_SUBJECT")
        or "mailto:admin@example.com"
    )


def _device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "android" in ua:
        return "Android"
    if "windows" in ua:
        return "Windows"
    return "Web Push"
