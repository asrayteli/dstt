"""DSTT 共通メール送信基盤。

車検証ツールに限らず DSTT 全体から再利用できる汎用のメール送信基盤。

設計方針:
  - 各ツールは :func:`queue_mail` でメールを ``mail_messages`` テーブルに積む。
  - スケジューラ（:func:`init_mail_scheduler`）が一定間隔で :func:`dispatch_pending`
    を呼び、キューを SMTP で実送信する。``send_now=True`` なら即時送信も可能。
  - ``dedupe_key`` を指定すると同一通知の二重送信を防げる。
  - SMTP 未設定の場合はキューに保留し、ログ警告のみでクラッシュしない
    （基盤先行構築のため、運用設定が後追いでも壊れない）。

設定（環境変数 / app.config どちらでも可。app.config が優先）:
  DSTT_SMTP_HOST              SMTP サーバーホスト名（未設定なら送信無効）
  DSTT_SMTP_PORT              ポート（既定: security に応じ 465/587/25）
  DSTT_SMTP_USER              認証ユーザー（任意）
  DSTT_SMTP_PASSWORD          認証パスワード（任意）
  DSTT_SMTP_SECURITY          none | starttls | ssl （既定: starttls）
  DSTT_MAIL_FROM              送信元アドレス（既定: SMTP_USER）
  DSTT_MAIL_FROM_NAME         送信元表示名（任意）
  DSTT_MAIL_TIMEOUT           SMTP タイムアウト秒（既定: 20）
  DSTT_MAIL_SCHEDULER         スケジューラ有効/無効（既定: 有効）
  DSTT_MAIL_DISPATCH_INTERVAL_SECONDS  送信間隔秒（既定: 60）
"""
from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import MailMessage, db
from app.services.local_time import local_now

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover
    BackgroundScheduler = None

try:
    import fcntl  # Linux/Unix のみ。プロセス間ロックに使用。
except Exception:  # pragma: no cover - Windows 等
    fcntl = None

# 1回のスケジューラ実行で送信を試みる最大件数。
DEFAULT_DISPATCH_LIMIT = 50
# ステータスの定数。
STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELED = "canceled"
# 再送対象とみなすステータス。
RETRYABLE_STATUSES = (STATUS_QUEUED, STATUS_FAILED)


def _app():
    return current_app._get_current_object()


def _config(key: str, env_key: str, default: Any = None) -> Any:
    """app.config を優先し、無ければ環境変数、最後に default を返す。"""
    try:
        value = _app().config.get(key)
    except Exception:
        value = None
    if value in (None, ""):
        value = os.environ.get(env_key)
    if value in (None, ""):
        return default
    return value


def _config_bool(key: str, env_key: str, default: bool = True) -> bool:
    value = _config(key, env_key, None)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def mail_settings() -> dict[str, Any]:
    """現在のメール送信設定を解決して返す。"""
    security = str(_config("MAIL_SECURITY", "DSTT_SMTP_SECURITY", "starttls")).strip().lower()
    if security not in {"none", "starttls", "ssl"}:
        security = "starttls"
    default_port = {"ssl": 465, "starttls": 587, "none": 25}[security]
    try:
        port = int(_config("MAIL_PORT", "DSTT_SMTP_PORT", default_port))
    except (TypeError, ValueError):
        port = default_port
    user = _config("MAIL_USER", "DSTT_SMTP_USER", "") or ""
    sender = _config("MAIL_FROM", "DSTT_MAIL_FROM", user) or user
    try:
        timeout = int(_config("MAIL_TIMEOUT", "DSTT_MAIL_TIMEOUT", 20))
    except (TypeError, ValueError):
        timeout = 20
    return {
        "host": (_config("MAIL_HOST", "DSTT_SMTP_HOST", "") or "").strip(),
        "port": port,
        "user": user,
        "password": _config("MAIL_PASSWORD", "DSTT_SMTP_PASSWORD", "") or "",
        "security": security,
        "from_address": (sender or "").strip(),
        "from_name": (_config("MAIL_FROM_NAME", "DSTT_MAIL_FROM_NAME", "") or "").strip(),
        "timeout": timeout,
    }


def is_mail_configured() -> bool:
    """SMTP ホストと送信元が揃っていれば送信可能とみなす。"""
    settings = mail_settings()
    return bool(settings["host"] and settings["from_address"])


def _normalize_address(value: str) -> str:
    return str(value or "").strip()


def queue_mail(
    to_address: str,
    subject: str,
    body_text: str,
    *,
    body_html: str | None = None,
    to_name: str | None = None,
    category: str = "general",
    dedupe_key: str | None = None,
    related_type: str | None = None,
    related_key: str | None = None,
    scheduled_at=None,
    cc: str | None = None,
    bcc: str | None = None,
    reply_to: str | None = None,
    created_by: str | None = None,
    max_attempts: int = 5,
    send_now: bool = False,
    commit: bool = True,
) -> MailMessage | None:
    """メールをキューに積む。``dedupe_key`` が既存（未失敗）なら積まずに既存を返す。

    返り値は作成/既存の :class:`MailMessage`。宛先が空なら ``None``。
    ``send_now=True`` の場合はキュー投入後すぐ送信を試みる。
    """
    to_address = _normalize_address(to_address)
    if not to_address:
        logger.warning("queue_mail: 宛先が空のため送信をスキップします (category=%s)", category)
        return None

    if dedupe_key:
        existing = MailMessage.query.filter_by(dedupe_key=dedupe_key).first()
        if existing is not None:
            if existing.status in (STATUS_QUEUED, STATUS_SENT) or existing.status == "sending":
                # まだ送信予定 or 送信済みなら二重送信しない。
                return existing
            # 失敗/スキップ/キャンセル済みは再キューとして同じ行を使い回す。
            existing.status = STATUS_QUEUED
            existing.attempts = 0
            existing.last_error = None
            existing.subject = subject
            existing.body_text = body_text or ""
            existing.body_html = body_html
            existing.scheduled_at = scheduled_at
            if commit:
                db.session.commit()
            if send_now:
                _dispatch_one(existing)
                if commit:
                    db.session.commit()
            return existing

    message = MailMessage(
        category=category or "general",
        to_address=to_address,
        to_name=(to_name or "").strip() or None,
        cc=_normalize_address(cc) or None,
        bcc=_normalize_address(bcc) or None,
        reply_to=_normalize_address(reply_to) or None,
        subject=subject or "(件名なし)",
        body_text=body_text or "",
        body_html=body_html,
        status=STATUS_QUEUED,
        max_attempts=max(1, int(max_attempts or 1)),
        dedupe_key=dedupe_key or None,
        related_type=related_type,
        related_key=related_key,
        scheduled_at=scheduled_at,
        created_by=created_by,
    )
    db.session.add(message)
    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except IntegrityError:
        # dedupe_key の競合（並列実行）。既存を取り直して返す。
        db.session.rollback()
        if dedupe_key:
            return MailMessage.query.filter_by(dedupe_key=dedupe_key).first()
        raise

    if send_now:
        _dispatch_one(message)
        if commit:
            db.session.commit()
    return message


def send_mail_now(to_address: str, subject: str, body_text: str, **kwargs) -> MailMessage | None:
    """即時送信のショートカット。queue_mail(..., send_now=True) と同じ。"""
    kwargs["send_now"] = True
    return queue_mail(to_address, subject, body_text, **kwargs)


def requeue_message(message: MailMessage, *, send_now: bool = False, commit: bool = True) -> MailMessage:
    """既存メッセージ（failed/skipped/canceled 等）を再送対象として queued に戻す。

    試行回数・エラー・予約時刻をリセットし、``send_now=True`` なら即時送信を試みる。
    管理画面からの「再送」操作や、SMTP 設定後の手動リカバリで利用する。
    """
    message.status = STATUS_QUEUED
    message.attempts = 0
    message.last_error = None
    message.scheduled_at = None
    if commit:
        db.session.commit()
    if send_now:
        _dispatch_one(message)
        if commit:
            db.session.commit()
    return message


def _build_email(message: MailMessage, settings: dict[str, Any]) -> EmailMessage:
    email = EmailMessage()
    from_address = settings["from_address"]
    if settings["from_name"]:
        email["From"] = formataddr((settings["from_name"], from_address))
    else:
        email["From"] = from_address
    if message.to_name:
        email["To"] = formataddr((message.to_name, message.to_address))
    else:
        email["To"] = message.to_address
    if message.cc:
        email["Cc"] = message.cc
    if message.reply_to:
        email["Reply-To"] = message.reply_to
    email["Subject"] = message.subject
    email.set_content(message.body_text or "")
    if message.body_html:
        email.add_alternative(message.body_html, subtype="html")
    return email


def _recipients(message: MailMessage) -> list[str]:
    recipients = [message.to_address]
    for bucket in (message.cc, message.bcc):
        if bucket:
            recipients.extend(addr.strip() for addr in bucket.split(",") if addr.strip())
    return recipients


def _send_smtp(message: MailMessage, settings: dict[str, Any]) -> None:
    """1通を SMTP で送信する。失敗時は例外を送出する。"""
    email = _build_email(message, settings)
    recipients = _recipients(message)
    host, port, timeout = settings["host"], settings["port"], settings["timeout"]
    security = settings["security"]

    if security == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls()
            smtp.ehlo()
        if settings["user"]:
            smtp.login(settings["user"], settings["password"])
        smtp.send_message(email, from_addr=settings["from_address"], to_addrs=recipients)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def _dispatch_one(message: MailMessage, settings: dict[str, Any] | None = None) -> bool:
    """1通の送信を試み、ステータスを更新する。送信成功で True。

    呼び出し側で commit すること（バッチ送信のため）。
    """
    if settings is None:
        settings = mail_settings()
    if not (settings["host"] and settings["from_address"]):
        # SMTP 未設定時は "安全な保留"。skipped（再送不可）にして失うのではなく、
        # queued のまま据え置く。即時送信(send_now)経路でも、設定が後追いで入れば
        # スケジューラ（dispatch_pending）が自動送信する。両経路で挙動を一致させる。
        message.status = STATUS_QUEUED
        message.last_error = "SMTP 未設定のため保留中です（DSTT_SMTP_HOST / DSTT_MAIL_FROM を設定してください）。"
        logger.warning("メール送信を保留（SMTP未設定）: to=%s subject=%s", message.to_address, message.subject)
        return False

    message.attempts = (message.attempts or 0) + 1
    try:
        _send_smtp(message, settings)
    except Exception as exc:  # noqa: BLE001
        message.last_error = f"{type(exc).__name__}: {exc}"
        if message.attempts >= (message.max_attempts or 5):
            message.status = STATUS_FAILED
        else:
            message.status = STATUS_QUEUED  # 次回tickで再試行
        logger.warning(
            "メール送信失敗 (attempt %s/%s) to=%s: %s",
            message.attempts, message.max_attempts, message.to_address, exc,
        )
        return False

    message.status = STATUS_SENT
    message.sent_at = local_now()
    message.last_error = None
    logger.info("メール送信成功: to=%s subject=%s category=%s", message.to_address, message.subject, message.category)
    return True


def dispatch_pending(limit: int = DEFAULT_DISPATCH_LIMIT, now=None) -> dict[str, int]:
    """送信待ち（queued/再試行可能なfailed）かつ予約時刻到来済みのメールを送信する。"""
    now = now or local_now()
    settings = mail_settings()
    summary = {"selected": 0, "sent": 0, "failed": 0, "skipped": 0, "held": 0}

    # SMTP 未設定時は「安全な保留」: 送信待ちメールに触れず queued のまま据え置く。
    # こうしておけば、運用設定（DSTT_SMTP_*）が後追いで入った時点で自動的に送信
    # される（skipped にして失わない）。基盤先行構築の運用を壊さないための要。
    if not (settings["host"] and settings["from_address"]):
        held = (
            MailMessage.query
            .filter(MailMessage.status.in_(RETRYABLE_STATUSES))
            .filter(MailMessage.attempts < MailMessage.max_attempts)
            .filter((MailMessage.scheduled_at.is_(None)) | (MailMessage.scheduled_at <= now))
            .count()
        )
        summary["held"] = held
        if held:
            logger.warning(
                "SMTP 未設定のためメール %s 件を保留中です（DSTT_SMTP_HOST / DSTT_MAIL_FROM を設定してください）。",
                held,
            )
        return summary

    query = (
        MailMessage.query
        .filter(MailMessage.status.in_(RETRYABLE_STATUSES))
        .filter(MailMessage.attempts < MailMessage.max_attempts)
        .filter((MailMessage.scheduled_at.is_(None)) | (MailMessage.scheduled_at <= now))
        .order_by(MailMessage.created_at.asc())
        .limit(max(1, int(limit or 1)))
    )
    messages = query.all()
    summary["selected"] = len(messages)
    if not messages:
        return summary

    for message in messages:
        ok = _dispatch_one(message, settings)
        if ok:
            summary["sent"] += 1
        elif message.status == STATUS_SKIPPED:
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
    db.session.commit()
    return summary


# --------------------------------------------------------------------------- #
# スケジューラ
# --------------------------------------------------------------------------- #
def _run_singleton(app, lock_filename: str, job) -> None:
    """gunicorn の複数ワーカーで同一ジョブが多重実行されないよう flock で制御する。"""
    if fcntl is None:
        job()
        return
    lock_path = os.path.join(app.instance_path, lock_filename)
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        handle = open(lock_path, "w")
    except OSError:
        job()
        return
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # 他プロセスが実行中 → このtickはスキップ
        job()
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:
            pass
        handle.close()


def _run_dispatch_job(app) -> None:
    def _job():
        with app.app_context():
            try:
                dispatch_pending()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("メール送信スケジューラが失敗しました: %s", exc)
    _run_singleton(app, "mail_dispatch.lock", _job)


def init_mail_scheduler(app) -> None:
    """メールキューを定期送信するバックグラウンドスケジューラを起動する。"""
    if app.config.get("TESTING"):
        return
    if not _config_bool_app(app, "MAIL_SCHEDULER_ENABLED", "DSTT_MAIL_SCHEDULER", True):
        return
    if BackgroundScheduler is None:
        logger.warning("APScheduler が無いためメール送信スケジューラは無効です。")
        return
    try:
        interval = int(
            app.config.get("MAIL_DISPATCH_INTERVAL_SECONDS")
            or os.environ.get("DSTT_MAIL_DISPATCH_INTERVAL_SECONDS")
            or 60
        )
    except (TypeError, ValueError):
        interval = 60
    interval = max(15, interval)

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=lambda: _run_dispatch_job(app),
            trigger="interval",
            seconds=interval,
            id="dstt_mail_dispatch",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("DSTT メール送信スケジューラを起動しました（%s秒間隔）。", interval)


def _config_bool_app(app, key: str, env_key: str, default: bool = True) -> bool:
    value = app.config.get(key)
    if value is None:
        value = os.environ.get(env_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
