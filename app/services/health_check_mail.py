"""健診PLUS → 社員本人あて メール通知。

社員は DSTT のアカウントを持たない（DSTT を使うのは社員を管理する立場の人だけ）。
そのため本人あてのメールは **それだけで完結** させる。

  - DSTT へのリンク・ログインを前提とした案内は書かない
  - 「システムで確認してください」といった、本人が到達できない導線を作らない
  - 差出人・本文に DSTT というシステム名を出さない（社員から見れば会社からの連絡）

本文に載せるのは「通知の種別」と「日付」、および問い合わせ先まで。
検査項目・所見といった健康状態の詳細は載せない（宛先の多くが携帯キャリアメールで、
暗号化されない経路を通り、本人の端末に残るため）。

送信可否は次の3段で決まる。すべて満たしたときだけ送る。
  1. 営業所ごとの送信設定（settings.json の ``mail_notify``）が、その種別で有効
  2. レコードが ``email_opt_out`` でない、かつ退職者・受診非対象者でない
  3. 宛先メールアドレスが存在する
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from app.models import MailMessage, db
from app.services.mail_service import queue_mail

logger = logging.getLogger(__name__)

# 通知種別。ToBell 側の source_ref_type と一致させる。
MAIL_KINDS = ("reservation", "night_second", "secondary_exam")

# 件名・本文で使う種別ごとの文言。社員本人が読む前提のやさしい日本語にする。
_KIND_TEXT = {
    "reservation": {
        "subject": "健康診断のご予約日のお知らせ",
        "lead": "健康診断のご予約日をお知らせします。",
        "label": "健康診断の予約日",
    },
    "night_second": {
        "subject": "健康診断（2回目）のお知らせ",
        "lead": "深夜業に従事されている方は年2回の健康診断が必要です。2回目の受診時期をお知らせします。",
        "label": "2回目の受診予定日",
    },
    "secondary_exam": {
        "subject": "二次検査のご案内",
        "lead": "先の健康診断の結果、二次検査の受診をおすすめする時期になりました。",
        "label": "二次検査の受診推奨日",
    },
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str | None) -> bool:
    """送信先として使える形式か（厳密なRFC検証はしない）。"""
    text = (value or "").strip()
    return bool(text) and len(text) <= 255 and bool(_EMAIL_RE.match(text))


def format_jp_date(value: date | None) -> str:
    """2026年6月1日（月）の形式。曜日まで出すと本人が予定を立てやすい。"""
    if not value:
        return ""
    week = "月火水木金土日"[value.weekday()]
    return f"{value.year}年{value.month}月{value.day}日（{week}）"


def build_message(record, kind: str, basis_date: date, *, office_name: str = "",
                  contact_name: str = "", contact_note: str = "") -> tuple[str, str]:
    """(件名, 本文) を組み立てる。

    本文は本人が読んで **そのまま行動できる** ことを最優先にする。
    DSTT の名前・URL・ログイン導線は入れない。
    """
    text = _KIND_TEXT.get(kind)
    if text is None:
        raise ValueError(f"未知の通知種別: {kind}")

    name = (getattr(record, "employee_name", "") or "").strip() or "ご担当"
    lines = [
        f"{name} 様",
        "",
        text["lead"],
        "",
        f"　{text['label']}：{format_jp_date(basis_date)}",
    ]

    institution = (getattr(record, "medical_institution", "") or "").strip()
    if institution and kind != "secondary_exam":
        # 受診先が決まっている場合のみ。二次検査は受診先が未定のことが多い。
        lines.append(f"　受診先　　　：{institution}")

    lines += ["", "ご都合が合わない場合や、すでに受診済みの場合は、", "お手数ですが下記までご連絡ください。", ""]

    contact_lines = [v for v in (office_name, contact_name, contact_note) if (v or "").strip()]
    lines.append("　" + "　".join(contact_lines) if contact_lines else "　（担当者までご連絡ください）")
    lines += ["", "※このメールは送信専用です。ご返信いただいてもお答えできません。"]
    return text["subject"], "\n".join(lines)


def dedupe_key(record_id: int, kind: str, basis_date: date) -> str:
    """同じ対象日について二重送信しないための鍵。

    対象日が変わったら別の鍵になるので、予約日が変更されれば送り直される。
    """
    return f"health_check:{record_id}:{kind}:{basis_date.isoformat()}"


def cancel_pending_notifications(record_id: int, *, keep_keys: set[str] | None = None,
                                 include_manual: bool = False, commit: bool = True) -> int:
    """条件から外れた本人あてメールを送信前にキャンセルする。

    自動通知は対象日の前日にキューへ積むため、その後に受診完了・日付変更・
    送信停止・レコード削除が起きる可能性がある。送信スケジューラが拾う前に
    ``canceled`` にして、古い予定のメールが本人へ届かないようにする。
    """
    keep = keep_keys or set()
    query = MailMessage.query.filter_by(
        category="health_check",
        related_type="health_check_record",
        related_key=str(record_id),
    ).filter(MailMessage.status.in_(("queued", "failed", "skipped")))
    if not include_manual:
        query = query.filter(MailMessage.dedupe_key.like(f"health_check:{record_id}:%"))
    canceled = 0
    for message in query.all():
        if message.dedupe_key in keep:
            continue
        message.status = "canceled"
        message.last_error = "健診情報または通知設定の変更により送信を取り消しました。"
        canceled += 1
    if commit and canceled:
        db.session.commit()
    return canceled


def can_send_to(record) -> tuple[bool, str]:
    """このレコードに本人あてメールを送ってよいか。(可否, 理由) を返す。"""
    if getattr(record, "email_opt_out", False):
        return False, "本人希望により送信停止"
    if getattr(record, "is_retired", False):
        return False, "退職者"
    if getattr(record, "is_exempt", False):
        return False, "受診非対象者"
    address = (getattr(record, "employee_email", "") or "").strip()
    if not address:
        return False, "メールアドレス未登録"
    if not is_valid_email(address):
        return False, "メールアドレスの形式が不正"
    return True, ""


def queue_notification(record, kind: str, basis_date: date, *, office_name: str = "",
                       contact_name: str = "", contact_note: str = "",
                       created_by: str | None = None, commit: bool = True):
    """1件分の通知メールをキューに積む。送れない場合は None を返す。

    実際の送信は共通基盤（mail_service）のスケジューラが行うため、
    SMTP 未設定でも例外にはならず保留されるだけ。
    """
    ok, _reason = can_send_to(record)
    if not ok or basis_date is None:
        return None
    subject, body = build_message(
        record, kind, basis_date,
        office_name=office_name, contact_name=contact_name, contact_note=contact_note,
    )
    return queue_mail(
        (record.employee_email or "").strip(),
        subject,
        body,
        to_name=(record.employee_name or "").strip() or None,
        category="health_check",
        dedupe_key=dedupe_key(record.id, kind, basis_date),
        related_type="health_check_record",
        related_key=str(record.id),
        # ToBell と同じく対象日9:00に通知する。前日にはキューへ積むだけで、
        # 共通メールスケジューラはこの時刻まで実送信しない。
        scheduled_at=datetime.combine(basis_date, time(9, 0)),
        created_by=created_by,
        commit=commit,
    )
