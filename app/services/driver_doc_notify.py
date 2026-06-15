"""運転士マイカー書類の期限切れ通知。

``driver_documents`` の有効期限を走査し、期限が近い/切れた書類について
該当運転士の登録メールアドレスへ通知メールをキュー投入する。

二重送信を防ぐため、各書類ごとに「通知ステージ（残り日数しきい値）」を記録し、
より近いステージに入ったときだけ新しい通知を送る。``mail_service`` の
``dedupe_key`` でもガードするため、プロセス再起動をまたいでも重複しない。
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime

from app.models import DriverDocument, DriverVehicleProfile, db
from app.services.local_time import local_now
from app.services import mail_service

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover
    BackgroundScheduler = None

# 通知する「残り日数しきい値」（大きい順）。0 以下は期限当日/期限切れを表す。
NOTIFY_STAGES = (60, 30, 14, 7, 3, 1, 0)

DOC_LABELS = {
    "inspection": "車検証",
    "liability_insurance": "自賠責保険証",
    "voluntary_insurance": "任意保険証",
    "license": "運転免許証",
}


def _parse_expiry(value: str) -> datetime | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None


def _format_expiry(value: str) -> str:
    parsed = _parse_expiry(value)
    if not parsed:
        return value or ""
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _applicable_stage(days_left: int) -> int | None:
    """残り日数に対応する最小のしきい値を返す。該当が無ければ None。"""
    candidates = [stage for stage in NOTIFY_STAGES if days_left <= stage]
    return min(candidates) if candidates else None


def _build_message(profile: DriverVehicleProfile, doc: DriverDocument, days_left: int):
    label = DOC_LABELS.get(doc.doc_type, "書類")
    name = profile.employee_name or profile.employee_number
    expiry_text = _format_expiry(doc.expiry_date)
    if days_left < 0:
        headline = f"{label}の有効期限が切れています（{abs(days_left)}日経過）"
        subject = f"【期限切れ】{label} の有効期限が過ぎています"
    elif days_left == 0:
        headline = f"{label}の有効期限は本日です"
        subject = f"【本日期限】{label} の有効期限のお知らせ"
    else:
        headline = f"{label}の有効期限まで残り{days_left}日です"
        subject = f"【期限のお知らせ】{label} の有効期限が近づいています（残り{days_left}日）"

    car_bits = " ".join(filter(None, [profile.car_maker, profile.car_name, profile.registration_number]))
    lines = [
        f"{name} 様",
        "",
        headline,
        "",
        f"・書類: {label}",
        f"・有効期限: {expiry_text}",
    ]
    if doc.document_number:
        lines.append(f"・番号: {doc.document_number}")
    if doc.issuer:
        lines.append(f"・発行/保険会社: {doc.issuer}")
    if car_bits:
        lines.append(f"・対象車両: {car_bits}")
    lines += [
        "",
        "更新手続きのご準備をお願いいたします。",
        "",
        "※このメールはマイカー管理ツールから自動送信されています。",
    ]
    return subject, "\n".join(lines)


def run_due_expiry_notifications(now: datetime | None = None) -> dict[str, int]:
    """期限が近い/切れた書類について通知メールをキュー投入する。"""
    now = now or local_now()
    today = datetime(now.year, now.month, now.day)
    summary = {"checked": 0, "queued": 0, "skipped": 0}

    rows = (
        db.session.query(DriverDocument, DriverVehicleProfile)
        .join(DriverVehicleProfile, DriverDocument.profile_id == DriverVehicleProfile.id)
        .filter(DriverVehicleProfile.notify_enabled.is_(True))
        .all()
    )
    for doc, profile in rows:
        expiry = _parse_expiry(doc.expiry_date)
        if expiry is None:
            continue
        summary["checked"] += 1
        email = (profile.email or "").strip()
        if not email:
            summary["skipped"] += 1
            continue

        days_left = (expiry - today).days
        stage = _applicable_stage(days_left)
        if stage is None:
            continue  # まだ通知期間に入っていない（残り日数 > 最大しきい値）

        # 既に同じか、より近いステージで通知済みなら送らない。
        if doc.last_notified_stage is not None and stage >= doc.last_notified_stage:
            continue

        subject, body = _build_message(profile, doc, days_left)
        message = mail_service.queue_mail(
            email,
            subject,
            body,
            to_name=profile.employee_name or None,
            category="driver_doc_expiry",
            dedupe_key=f"driver_doc:{doc.id}:{stage}",
            related_type="driver_document",
            related_key=str(doc.id),
            commit=False,
        )
        if message is not None:
            doc.last_notified_stage = stage
            doc.last_notified_at = now
            summary["queued"] += 1
        else:
            summary["skipped"] += 1

    db.session.commit()
    if summary["queued"]:
        logger.info("運転士マイカー期限通知をキュー投入: %s", summary)
    return summary


def _run_notify_job(app) -> None:
    def _job():
        with app.app_context():
            try:
                run_due_expiry_notifications()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("運転士マイカー期限通知ジョブが失敗しました: %s", exc)
    mail_service._run_singleton(app, "driver_doc_notify.lock", _job)


def init_driver_doc_scheduler(app) -> None:
    """運転士マイカー書類の期限通知を定期実行するスケジューラを起動する。"""
    if app.config.get("TESTING"):
        return
    if not mail_service._config_bool_app(app, "DRIVER_DOC_NOTIFY_ENABLED", "DSTT_DRIVER_DOC_NOTIFY", True):
        return
    if BackgroundScheduler is None:
        logger.warning("APScheduler が無いため運転士マイカー期限通知スケジューラは無効です。")
        return
    try:
        hours = int(
            app.config.get("DRIVER_DOC_NOTIFY_INTERVAL_HOURS")
            or os.environ.get("DSTT_DRIVER_DOC_NOTIFY_INTERVAL_HOURS")
            or 6
        )
    except (TypeError, ValueError):
        hours = 6
    hours = max(1, hours)

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=lambda: _run_notify_job(app),
            trigger="interval",
            hours=hours,
            id="dstt_driver_doc_notify",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            next_run_time=local_now(),  # 起動直後に一度走らせる
        )
        _scheduler.start()
        logger.info("運転士マイカー期限通知スケジューラを起動しました（%s時間間隔）。", hours)
