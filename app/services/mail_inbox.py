"""DSTT 共通メール基盤の受信側（IMAP 取り込み）。

SMTP 送信基盤（:mod:`app.services.mail_service`）の対になる受信基盤。
IMAP サーバーを定期ポーリングして新着メールをパースし、``inbound_mails``
テーブルへ保存する。管理者ページの Webメーラーから閲覧・返信できる。

設計方針:
  - 取り込みは IMAP UID で冪等化（同じ UID は二重保存しない）。
  - サーバー側の既読フラグは変更せず、既読状態はアプリ内（is_read）で持つ。
  - 本文は上限付きで保存し、添付はメタ情報（名前/サイズ/種別）のみ保持する。
  - IMAP 未設定でもクラッシュせず、ポーリングは何もしない（送信側と同じ思想）。

設定（環境変数 / app.config どちらでも可。app.config が優先）:
  DSTT_IMAP_HOST                IMAP サーバーホスト名（未設定なら受信無効）
  DSTT_IMAP_PORT                ポート（既定: security に応じ 993/143）
  DSTT_IMAP_USER                ログインユーザー
  DSTT_IMAP_PASSWORD            ログインパスワード
  DSTT_IMAP_SECURITY            ssl | starttls | none （既定: ssl）
  DSTT_IMAP_MAILBOX             取り込むメールボックス（既定: INBOX）
  DSTT_IMAP_TIMEOUT             IMAP タイムアウト秒（既定: 20）
  DSTT_MAIL_INBOX_SCHEDULER     受信ポーリングの有効/無効（既定: 有効）
  DSTT_IMAP_POLL_INTERVAL_SECONDS  ポーリング間隔秒（既定: 300, 最小30）
  DSTT_IMAP_FETCH_LIMIT         1回の取り込み最大件数（既定: 50）
  DSTT_IMAP_RETENTION           受信トレイ保持件数（既定: 1000, 0で無制限）
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import threading
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from flask import current_app

from app.models import InboundMail, db
from app.services import mail_service
from app.services.local_time import local_now

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover
    BackgroundScheduler = None

# 1通あたりの本文保存上限（肥大化防止）。
MAX_BODY_CHARS = 1_000_000


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


def inbox_settings() -> dict[str, Any]:
    """現在の IMAP 受信設定を解決して返す。"""
    security = str(_config("IMAP_SECURITY", "DSTT_IMAP_SECURITY", "ssl")).strip().lower()
    if security not in {"none", "starttls", "ssl"}:
        security = "ssl"
    default_port = 993 if security == "ssl" else 143
    try:
        port = int(_config("IMAP_PORT", "DSTT_IMAP_PORT", default_port))
    except (TypeError, ValueError):
        port = default_port
    try:
        timeout = int(_config("IMAP_TIMEOUT", "DSTT_IMAP_TIMEOUT", 20))
    except (TypeError, ValueError):
        timeout = 20
    return {
        "host": (_config("IMAP_HOST", "DSTT_IMAP_HOST", "") or "").strip(),
        "port": port,
        "user": _config("IMAP_USER", "DSTT_IMAP_USER", "") or "",
        "password": _config("IMAP_PASSWORD", "DSTT_IMAP_PASSWORD", "") or "",
        "security": security,
        "mailbox": (_config("IMAP_MAILBOX", "DSTT_IMAP_MAILBOX", "INBOX") or "INBOX").strip(),
        "timeout": timeout,
    }


def is_inbox_configured() -> bool:
    """IMAP ホストと認証情報が揃っていれば受信可能とみなす。"""
    s = inbox_settings()
    return bool(s["host"] and s["user"] and s["password"])


def _decode(value) -> str:
    """MIME エンコードされたヘッダ値をデコードする。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def _fetch_limit() -> int:
    try:
        return max(1, int(_config("IMAP_FETCH_LIMIT", "DSTT_IMAP_FETCH_LIMIT", 50)))
    except (TypeError, ValueError):
        return 50


def _retention() -> int:
    try:
        return max(0, int(_config("IMAP_RETENTION", "DSTT_IMAP_RETENTION", 1000)))
    except (TypeError, ValueError):
        return 1000


def _extract_bodies(msg) -> tuple[str, str, list[dict]]:
    """email.message.Message から (text, html, attachments) を取り出す。"""
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()
            if filename or "attachment" in disposition.lower():
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "name": _decode(filename) or "(無名の添付)",
                    "size": len(payload),
                    "content_type": content_type,
                })
                continue
            if content_type == "text/plain":
                text_parts.append(_part_text(part))
            elif content_type == "text/html":
                html_parts.append(_part_text(part))
    else:
        if msg.get_content_type() == "text/html":
            html_parts.append(_part_text(msg))
        else:
            text_parts.append(_part_text(msg))

    text = "\n".join(p for p in text_parts if p)[:MAX_BODY_CHARS]
    html = "\n".join(p for p in html_parts if p)[:MAX_BODY_CHARS]
    return text, html, attachments


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def _parse_message(raw_bytes: bytes, uid: str, mailbox: str) -> InboundMail:
    msg = email.message_from_bytes(raw_bytes)
    from_name, from_addr = parseaddr(_decode(msg.get("From")))
    sent_at = None
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt is not None:
            sent_at = dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        sent_at = None
    text, html, attachments = _extract_bodies(msg)
    return InboundMail(
        mailbox=mailbox,
        imap_uid=str(uid),
        message_id=(_decode(msg.get("Message-ID")) or None),
        from_address=(from_addr or "")[:255] or None,
        from_name=(from_name or "")[:255] or None,
        to_address=(_decode(msg.get("To")) or "")[:1000] or None,
        cc=(_decode(msg.get("Cc")) or "")[:1000] or None,
        subject=(_decode(msg.get("Subject")) or "")[:500] or None,
        body_text=text or None,
        body_html=html or None,
        size_bytes=len(raw_bytes),
        has_attachments=bool(attachments),
        attachments=json.dumps(attachments, ensure_ascii=False) if attachments else None,
        is_read=False,
        sent_at=sent_at,
        received_at=local_now(),
    )


def _connect(settings: dict[str, Any]):
    host, port, timeout = settings["host"], settings["port"], settings["timeout"]
    if settings["security"] == "ssl":
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        if settings["security"] == "starttls":
            conn.starttls()
    conn.login(settings["user"], settings["password"])
    return conn


def fetch_new_messages(limit: int | None = None) -> dict[str, int]:
    """IMAP から新着メールを取り込み、``inbound_mails`` へ保存する。

    既存 UID は冪等にスキップする。戻り値は取り込みサマリ。
    """
    summary = {"fetched": 0, "stored": 0, "skipped": 0, "pruned": 0}
    settings = inbox_settings()
    if not is_inbox_configured():
        return summary

    mailbox = settings["mailbox"]
    limit = limit or _fetch_limit()

    conn = None
    try:
        conn = _connect(settings)
        status, _ = conn.select(mailbox, readonly=True)
        if status != "OK":
            logger.warning("IMAP: メールボックス %s を開けませんでした。", mailbox)
            return summary

        status, data = conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return summary
        all_uids = data[0].split()

        existing = {
            row.imap_uid
            for row in InboundMail.query.filter_by(mailbox=mailbox)
            .with_entities(InboundMail.imap_uid)
            .all()
        }
        new_uids = [u for u in all_uids if u.decode() not in existing]
        # 新しい（末尾）から limit 件だけ取り込む。
        new_uids = new_uids[-limit:]
        summary["skipped"] = len(all_uids) - len(new_uids)

        for raw_uid in new_uids:
            uid = raw_uid.decode()
            status, msg_data = conn.uid("fetch", raw_uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_bytes = msg_data[0][1]
            if not isinstance(raw_bytes, (bytes, bytearray)):
                continue
            summary["fetched"] += 1
            record = _parse_message(bytes(raw_bytes), uid, mailbox)
            db.session.add(record)
            try:
                db.session.commit()
                summary["stored"] += 1
            except Exception:  # noqa: BLE001 - 競合等は1通スキップして継続
                db.session.rollback()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.warning("IMAP 受信に失敗しました: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    summary["pruned"] = _prune(mailbox)
    if summary["stored"]:
        logger.info("IMAP 受信: %s", summary)
    return summary


def _prune(mailbox: str) -> int:
    """保持件数を超えた古い受信メールを削除する。0 件なら何もしない。"""
    keep = _retention()
    if keep <= 0:
        return 0
    total = InboundMail.query.filter_by(mailbox=mailbox).count()
    if total <= keep:
        return 0
    overflow = (
        InboundMail.query.filter_by(mailbox=mailbox)
        .order_by(InboundMail.received_at.asc())
        .limit(total - keep)
        .all()
    )
    for row in overflow:
        db.session.delete(row)
    db.session.commit()
    return len(overflow)


# --------------------------------------------------------------------------- #
# スケジューラ
# --------------------------------------------------------------------------- #
def _run_poll_job(app) -> None:
    def _job():
        with app.app_context():
            try:
                fetch_new_messages()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("IMAP 受信ポーリングが失敗しました: %s", exc)
    mail_service._run_singleton(app, "mail_inbox_poll.lock", _job)


def init_inbox_scheduler(app) -> None:
    """IMAP 受信を定期ポーリングするバックグラウンドスケジューラを起動する。"""
    if app.config.get("TESTING"):
        return
    if not mail_service._config_bool_app(app, "MAIL_INBOX_SCHEDULER_ENABLED", "DSTT_MAIL_INBOX_SCHEDULER", True):
        return
    if BackgroundScheduler is None:
        logger.warning("APScheduler が無いため IMAP 受信スケジューラは無効です。")
        return
    try:
        interval = int(
            app.config.get("MAIL_INBOX_POLL_INTERVAL_SECONDS")
            or os.environ.get("DSTT_IMAP_POLL_INTERVAL_SECONDS")
            or 300
        )
    except (TypeError, ValueError):
        interval = 300
    interval = max(30, interval)

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=lambda: _run_poll_job(app),
            trigger="interval",
            seconds=interval,
            id="dstt_mail_inbox_poll",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("DSTT メール受信ポーリングを起動しました（%s秒間隔）。", interval)
