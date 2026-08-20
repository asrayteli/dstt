"""他DSTTツール → ToBell へのフック関数群。

各ツールはこのモジュールの関数を呼び、ユーザー個人ごとの連携許可が
ONになっているユーザーにのみ通知/タスクを生成する。例外は飲み込んで
呼び出し元の動作を妨げない（連携は補助機能）。
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Iterable

from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import IntegrityError

from app.models import ToBellNotification, ToBellTask, db
from app.services.local_time import local_now
from app.services.to_bell_integrations import enabled_users, get_health_check_notify, is_enabled

logger = logging.getLogger(__name__)


def _create_task_for(
    *,
    target_user: str,
    title: str,
    description: str = "",
    source_tool: str,
    source_ref_type: str | None = None,
    source_ref_id: str | None = None,
    href: str | None = None,
    severity: str = "warning",
    priority: str = "normal",
    notify_event: str = "external",
) -> ToBellTask | None:
    """既存の同一参照タスクがあればスキップし、ToBellタスクと通知を1件作る。"""
    if not target_user:
        return None
    if source_ref_id and source_tool:
        existing = ToBellTask.query.filter_by(
            source_tool=source_tool,
            source_ref_type=source_ref_type,
            source_ref_id=str(source_ref_id),
        ).first()
        if existing:
            return existing
    task = ToBellTask(
        title=title[:240],
        description=description[:2000] if description else "",
        status="todo",
        priority=priority,
        created_by=target_user,
        assigned_to=target_user,
        source_tool=source_tool,
        source_ref_type=source_ref_type,
        source_ref_id=str(source_ref_id) if source_ref_id else None,
    )
    try:
        # 一意制約(uq_to_bell_task_source)で守られているため、別プロセス/二重送信が
        # 同一参照タスクを同時生成すると flush で IntegrityError になる。セーブポイントで
        # 隔離し、衝突時は相手が作った既存タスクを採用する（呼び出し元の他処理は守る）。
        # add も begin_nested 内で行う必要がある（外に出すと savepoint 巻き戻しで
        # セッションが復旧できず PendingRollbackError になる）。
        with db.session.begin_nested():
            db.session.add(task)
            db.session.flush()
    except IntegrityError:
        if source_ref_id and source_tool:
            return ToBellTask.query.filter_by(
                source_tool=source_tool,
                source_ref_type=source_ref_type,
                source_ref_id=str(source_ref_id),
            ).first()
        raise
    db.session.add(
        ToBellNotification(
            user_id=target_user,
            task=task,
            source_tool=source_tool,
            event_type=notify_event,
            title=title[:240],
            body=description[:400] if description else "",
            href=href or "/tools/to_bell",
            severity=severity,
        )
    )
    return task


def _commit_safely() -> None:
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("ToBell連携のコミットに失敗しました")
        db.session.rollback()


def on_cloudshift_leave_change_request(
    *,
    request_payload: dict[str, Any],
    project_title: str,
    project_id: str,
    creator_username: str | None = None,
    audience: Iterable[str] | None = None,
) -> None:
    """休暇種別変更申請が起きたとき、連携を許可しているユーザーへ即時タスク化。

    audience が指定されなければ、許可している全ユーザーに作成する。
    """
    integration_key = "cloudshift.leave_change_request"
    try:
        targets = list(audience) if audience is not None else enabled_users(integration_key)
        if creator_username and is_enabled(creator_username, integration_key):
            if creator_username not in targets:
                targets.append(creator_username)
        if not targets:
            return
        day = request_payload.get("day")
        old = request_payload.get("old_leave_type") or request_payload.get("old_option_key") or ""
        new = request_payload.get("requested_leave_type") or request_payload.get("requested_option_key") or ""
        request_id = str(request_payload.get("id") or "")
        month_key = str(request_payload.get("month_key") or "")
        title = f"[CloudShift] 休暇種別変更申請: {project_title} {month_key} {day}日"
        body = f"{old} → {new}\n申請コメント: {request_payload.get('request_comment') or '(なし)'}"
        href = f"/tools/shiftersync/cloudshift?project={project_id}&month={month_key}"
        for user in dict.fromkeys(targets):
            if not is_enabled(user, integration_key):
                continue
            _create_task_for(
                target_user=user,
                title=title,
                description=body,
                source_tool="cloudshift",
                source_ref_type="leave_change_request",
                source_ref_id=f"{project_id}:{request_id}",
                href=href,
                severity="warning",
                priority="high",
                notify_event="leave_change_request",
            )
        _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("CloudShift leave_change_request の ToBell 連携で失敗")
        db.session.rollback()


_ACTION_LABEL_JP = {"added": "追加", "modified": "更新", "removed": "削除"}


def _format_substitute_change_lines(changes: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    """changesを「5月15日: 追加: 山田太郎 → 現場A [A]」形式の行に整形する。"""
    lines: list[str] = []
    for ch in changes[:limit]:
        mk = str(ch.get("month_key") or "")
        dk = str(ch.get("day_key") or "")
        action = _ACTION_LABEL_JP.get(str(ch.get("action") or ""), "更新")
        label = str(ch.get("label") or "")
        month_part = ""
        if mk:
            try:
                _, m = mk.split("-")
                month_part = f"{int(m)}月"
            except Exception:
                month_part = f"{mk} "
        day_part = f"{int(dk)}日" if dk.isdigit() else (f"{dk}日" if dk else "")
        prefix = f"{month_part}{day_part}".strip()
        if prefix:
            lines.append(f"・{prefix}: {action}: {label}")
        else:
            lines.append(f"・{action}: {label}")
    if len(changes) > limit:
        lines.append(f"・他 {len(changes) - limit} 件")
    return lines


def on_cloudshift_substitute_updated(
    *,
    substitute_project_id: str,
    substitute_project_title: str,
    changes: list[dict[str, Any]] | None = None,
    month_key: str = "",  # 後方互換のため残す
    day_key: str = "",
    action: str = "updated",
) -> None:
    """要代務シフト帳が更新されたとき、連携許可ユーザー全員に確認タスクを追加し、
    即時プッシュ通知も送信する。

    タスクは「ユーザー:プロジェクトID:YYYYMMDD」単位でdedupする（1日1タスク）。
    既存タスクがある場合は本文（メモ）に最新の変更を追記する。
    プッシュ通知はdedupせず、変更があるたびに送る。
    """
    integration_key = "cloudshift.shift_update"
    try:
        targets = enabled_users(integration_key)
        if not targets:
            return

        change_list = changes or []
        # フォールバック: changes が無い場合は旧来の month_key/day_key/action で1件の擬似変更を作る
        if not change_list and (month_key or day_key or action):
            change_list = [{
                "action": action or "updated",
                "month_key": month_key,
                "day_key": day_key,
                "label": "",
            }]

        change_lines = _format_substitute_change_lines(change_list)
        body_lines = [f"{substitute_project_title} が更新されました。"] + change_lines + ["", "ToBellで開いて確認してください。"]
        body = "\n".join(body_lines)
        title = f"[CloudShift] 要代務シフト帳が更新されました: {substitute_project_title}"
        href = f"/tools/shiftersync/cloudshift?project={substitute_project_id}"

        # 「当日」は日本のローカル日付で判定する（UTCだと日付の境界がずれる）。
        today_key = local_now().strftime("%Y%m%d")
        for user in dict.fromkeys(targets):
            _upsert_substitute_task_for_user(
                user=user,
                substitute_project_id=substitute_project_id,
                today_key=today_key,
                title=title,
                body=body,
                href=href,
                change_lines=change_lines,
            )
        _commit_safely()

        # 即時プッシュ送信（変更がある度に毎回送る）
        push_body = "\n".join(change_lines) if change_lines else f"{substitute_project_title} が更新されました。"
        for user in dict.fromkeys(targets):
            _send_immediate_push(user, title=title, body=push_body, url=href)
    except Exception:  # noqa: BLE001
        logger.exception("CloudShift substitute_update の ToBell 連携で失敗")
        db.session.rollback()


def _upsert_substitute_task_for_user(
    *,
    user: str,
    substitute_project_id: str,
    today_key: str,
    title: str,
    body: str,
    href: str,
    change_lines: list[str],
) -> None:
    """同日に既存タスクがあれば説明文に追記、なければ新規作成する。"""
    source_ref_id = f"{user}:{substitute_project_id}:{today_key}"
    existing = ToBellTask.query.filter_by(
        source_tool="cloudshift",
        source_ref_type="substitute_update",
        source_ref_id=source_ref_id,
    ).first()
    if existing:
        # 既存メモ末尾に、今回の変更行のみを追記する（重複行は付け足さない）
        prev_body = existing.description or ""
        addition_lines = [line for line in change_lines if line and line not in prev_body]
        if addition_lines:
            existing.description = (prev_body + "\n" + "\n".join(addition_lines))[:4000]
        # 通知は新たに1件足す（既読フラグの再アピール）
        db.session.add(
            ToBellNotification(
                user_id=user,
                task=existing,
                source_tool="cloudshift",
                event_type="substitute_update",
                title=title[:240],
                body=("\n".join(addition_lines) or body)[:400],
                href=href,
                severity="warning",
            )
        )
        return
    _create_task_for(
        target_user=user,
        title=title,
        description=body,
        source_tool="cloudshift",
        source_ref_type="substitute_update",
        source_ref_id=source_ref_id,
        href=href,
        severity="warning",
        priority="normal",
        notify_event="substitute_update",
    )


def _send_immediate_push(user: str, *, title: str, body: str, url: str) -> None:
    """Webプッシュを即時送信する。失敗は無視。"""
    try:
        from app.services.to_bell_push import send_push_to_user, ToBellPushUnavailable

        try:
            send_push_to_user(user, title=title[:120], body=body[:400], url=url)
        except ToBellPushUnavailable:
            return
    except Exception:  # noqa: BLE001
        logger.exception("CloudShift substitute_update プッシュ送信失敗")


# ============================================================
# 健診PLUS（health_check）→ ToBell リマインダー
# ============================================================

HEALTH_CHECK_INTEGRATION_KEY = "health_check.linkage"
HEALTH_CHECK_DEFAULT_LEAD_DAYS = 3
_HEALTH_CHECK_HREF = "/tools/health_check"

# 通知タイミング：対象日の前日にタスク化し、当日 09:00 にアラートを流す。
HEALTH_CHECK_REMINDER_LEAD_DAYS = 1

# 通知ジャンル（source_ref_type → 表示名）。
# 本文・タイトルは「{氏名}さんの{ジャンル}になりました。」で統一する。
HEALTH_CHECK_REMINDER_GENRE = {
    "reservation": "健康診断予約日",
    "night_second": "受診日②",
    "secondary_exam": "二次検査受診推奨日",
}


def _hc_due_at(basis_date) -> datetime:
    """アラート時刻＝対象日の 09:00。"""
    return datetime.combine(basis_date, time(9, 0))


def _hc_should_materialize(basis_date, now: datetime) -> bool:
    """タスク化の判定。対象日の前日（以降）になったら作成する。"""
    return now.date() >= basis_date - timedelta(days=HEALTH_CHECK_REMINDER_LEAD_DAYS)


def _hc_night_basis(record) -> "Any":
    """深夜2回目リマインドの基準日。未設定時は受診日＋6か月を既定とする。"""
    if record.exam_date_2_target:
        return record.exam_date_2_target
    if record.exam_date:
        return record.exam_date + relativedelta(months=6)
    return None


def _hc_notifications_suppressed(record) -> bool:
    """このレコードは通知の対象外か。

    退職者（`is_retired`）と受診非対象者（`is_exempt`）は、一覧・ダッシュボード・
    エクスポートのいずれでも集計対象外として扱われる（設計書 §10.3 / §10.4）。
    リマインドだけが飛び続けると、担当者に「もう追いかけようのない対象者」の
    通知が届き続けるため、同じ基準で通知も止める。
    """
    return bool(getattr(record, "is_retired", False)) or bool(getattr(record, "is_exempt", False))


def _hc_recipients(record) -> list[str]:
    """このレコードの通知宛先（username）を重複排除して返す。

    既定の宛先 = 管理担当者（manager_user）＋ 営業所の健康診断担当。
    さらにレコード個別の追加宛先（extra_notify_users）を上乗せする。
    実際に通知されるのは、各宛先が連携をオプトインしている場合のみ（呼び出し側で判定）。
    """
    recipients: list[str] = []
    seen: set[str] = set()

    def _add(user) -> None:
        user = (user or "").strip()
        if user and user not in seen:
            seen.add(user)
            recipients.append(user)

    _add(getattr(record, "manager_user", None))
    try:
        from app.tools.health_check import get_office_health_officers
        for user in get_office_health_officers(getattr(record, "office_code", None)):
            _add(user)
    except Exception:  # noqa: BLE001
        logger.exception("健康診断担当の取得に失敗")
    try:
        for user in record.extra_notify_users_list():
            _add(user)
    except Exception:  # noqa: BLE001
        logger.exception("追加通知先の取得に失敗")
    return recipients


def _hc_employee_mail_contact(record) -> tuple[str, str]:
    """本人あてメールに載せる問い合わせ先（営業所名・担当者名）を解決する。

    社員はDSTTを見られないため、困ったときの連絡先を本文に書く必要がある。
    """
    office_name = ""
    try:
        from app.tools.health_check import get_office_display_name
        office_name = get_office_display_name(getattr(record, "office_code", None))
    except Exception:  # noqa: BLE001
        logger.exception("営業所名の解決に失敗")
    contact = (getattr(record, "manager_name", "") or "").strip()
    return office_name, contact


def _hc_queue_employee_mail(record, *, now: datetime, suppressed: bool) -> bool:
    """対象日に達した通知について、社員本人あてメールをキューに積む。

    例外は握りつぶす。メールが積めないことで健診レコードの保存や
    ToBell リマインドを止めてはならない。
    """
    try:
        from app.tools.health_check import office_mail_enabled
        from app.services.health_check_mail import (
            queue_notification,
            can_send_to,
            cancel_pending_notifications,
            dedupe_key,
        )

        keep_keys: set[str] = set()
        ok, _reason = can_send_to(record)

        office_code = getattr(record, "office_code", None)
        plans = (
            ("reservation", record.reservation_date,
             record.reservation_date is not None and record.exam_date is None),
            ("night_second", _hc_night_basis(record),
             bool(record.is_night_worker) and record.exam_date_2 is None),
            ("secondary_exam", record.secondary_recommended_date,
             bool(record.needs_recheck)
             and record.secondary_recommended_date is not None
             and record.secondary_exam_date is None),
        )
        office_name, contact = _hc_employee_mail_contact(record)
        if not suppressed and ok:
            for kind, basis, condition in plans:
                if not condition or basis is None:
                    continue
                if not _hc_should_materialize(basis, now):
                    continue
                if not office_mail_enabled(office_code, kind):
                    continue
                queue_notification(
                    record, kind, basis,
                    office_name=office_name, contact_name=contact,
                    created_by="health_check", commit=False,
                )
                keep_keys.add(dedupe_key(record.id, kind, basis))

        # 予定変更・受診完了・配信停止などで現在の条件から外れた未送信分を止める。
        cancel_pending_notifications(record.id, keep_keys=keep_keys, commit=False)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("健診PLUS 本人あてメールのキュー投入に失敗")
        return False


def _ensure_reminder_task(
    *,
    manager_user: str,
    title: str,
    description: str,
    source_ref_type: str,
    source_ref_id: str,
    due_at: datetime,
    priority: str = "normal",
) -> ToBellTask | None:
    """健診リマインドのタスクを「無ければ作成／あれば期日・宛先を更新」する。"""
    if not manager_user:
        return None
    existing = ToBellTask.query.filter_by(
        source_tool="health_check",
        source_ref_type=source_ref_type,
        source_ref_id=str(source_ref_id),
    ).first()
    if existing:
        existing.title = title[:240]
        existing.description = description[:2000] if description else ""
        existing.due_at = due_at
        existing.assigned_to = manager_user
        existing.priority = priority
        if existing.status in ("done", "archived"):
            existing.status = "todo"
            existing.completed_at = None
        return existing
    task = ToBellTask(
        title=title[:240],
        description=description[:2000] if description else "",
        status="todo",
        priority=priority,
        due_at=due_at,
        created_by=manager_user,
        assigned_to=manager_user,
        source_tool="health_check",
        source_ref_type=source_ref_type,
        source_ref_id=str(source_ref_id),
    )
    try:
        # 一意制約(uq_to_bell_task_source)による同時生成の衝突をセーブポイントで隔離。
        # add も begin_nested 内で行う（外に出すと巻き戻し後に復旧できない）。
        with db.session.begin_nested():
            db.session.add(task)
            db.session.flush()
    except IntegrityError:
        return ToBellTask.query.filter_by(
            source_tool="health_check",
            source_ref_type=source_ref_type,
            source_ref_id=str(source_ref_id),
        ).first()
    db.session.add(
        ToBellNotification(
            user_id=manager_user,
            task=task,
            source_tool="health_check",
            event_type="health_check_reminder",
            title=title[:240],
            body=description[:400] if description else "",
            href=_HEALTH_CHECK_HREF,
            severity="warning",
        )
    )
    return task


def _close_reminder_tasks_for_record(source_ref_type: str, record_id: int,
                                     keep_ref_ids: "set[str] | None" = None) -> None:
    """指定レコード・種別の健診リマインドのうち、keep_ref_ids に無いものをアーカイブする。

    宛先ごとに `source_ref_id = "{record_id}:{username}"` でタスクを持つため、
    宛先の増減・オプトアウト・条件解除に追従してクローズできる。
    旧形式（`source_ref_id == str(record_id)`）も対象に含める。
    """
    keep = keep_ref_ids or set()
    prefix = f"{record_id}:"
    candidates = ToBellTask.query.filter(
        ToBellTask.source_tool == "health_check",
        ToBellTask.source_ref_type == source_ref_type,
        db.or_(
            ToBellTask.source_ref_id == str(record_id),
            ToBellTask.source_ref_id.like(prefix + "%"),
        ),
    ).all()
    for task in candidates:
        if task.source_ref_id in keep:
            continue
        if task.status not in ("done", "archived"):
            task.status = "archived"


def ensure_health_check_reminders(
    record,
    *,
    global_lead_days: int = HEALTH_CHECK_DEFAULT_LEAD_DAYS,
    now: datetime | None = None,
    commit: bool = True,
) -> bool:
    """健診レコードの状態から、3種のリマインドを同期する。

    対象日（健康診断予約日／深夜従事者の受診日②／二次検査受診推奨日）の
    **前日にタスク化**し、対象日 **09:00** にアラート（push）を流す。
    タスク・通知の本文は「{氏名}さんの{ジャンル}になりました。」で統一する。

    通知は宛先（管理担当者＋営業所の健康診断担当＋レコード個別の追加宛先）のうち、
    連携をオプトインしている人ごとに 1 タスクずつ起票する（`source_ref_id="{record_id}:{username}"`）。
    例外は飲み込み、健診側の保存処理を妨げない。
    （`global_lead_days` は後方互換のため残すが、本リマインドでは使用しない。）
    """
    now = now or local_now()
    suppressed = _hc_notifications_suppressed(record)
    recipients = [] if suppressed else _hc_recipients(record)
    # 本人あてメールは ToBell と同じ発火条件（対象日の前日に確定）で積む。
    # 担当者のオプトインとは独立で、営業所ごとの送信設定だけで決まる
    # （社員はDSTTアカウントを持たないためオプトインの仕組みが無い）。
    mail_ok = _hc_queue_employee_mail(record, now=now, suppressed=suppressed)

    def _sync(ref_type: str, basis, condition: bool, priority: str) -> "ToBellTask | None":
        """対象日 basis の前日以降になったら、オプトイン済みの各宛先にタスク化する。
        条件を満たさない・まだ前日に達していない宛先のタスクはクローズする。
        戻り値は代表タスク（*_task_id 後方互換のため）。"""
        materialize = (
            not suppressed
            and condition
            and basis is not None
            and _hc_should_materialize(basis, now)
        )
        keep_ref_ids: set[str] = set()
        first_task: "ToBellTask | None" = None
        if materialize:
            message = f"{record.employee_name}さんの{HEALTH_CHECK_REMINDER_GENRE[ref_type]}になりました。"
            due_at = _hc_due_at(basis)
            for user in recipients:
                if not is_enabled(user, HEALTH_CHECK_INTEGRATION_KEY):
                    continue
                if not get_health_check_notify(user).get(ref_type, True):
                    continue
                ref_id = f"{record.id}:{user}"
                task = _ensure_reminder_task(
                    manager_user=user,
                    title=message,
                    description=message,
                    source_ref_type=ref_type,
                    source_ref_id=ref_id,
                    due_at=due_at,
                    priority=priority,
                )
                if task is not None:
                    keep_ref_ids.add(ref_id)
                    if first_task is None:
                        first_task = task
        # 宛先から外れた／オプトアウトした／条件解除された分はクローズ
        _close_reminder_tasks_for_record(ref_type, record.id, keep_ref_ids)
        return first_task

    try:
        # リマインド同期はセーブポイント内で行う。ここで失敗しても巻き戻るのは
        # リマインド分だけで、呼び出し元（健診レコードの保存）の未コミット変更は
        # そのまま残す。以前は except で db.session.rollback() していたため、
        # 同期の失敗が利用者の入力ごと破棄していた（保存APIは success を返すため
        # 利用者は気づけなかった）。
        with db.session.begin_nested():
            # --- 健康診断予約日 ---
            _sync(
                "reservation",
                record.reservation_date,
                record.reservation_date is not None and record.exam_date is None,
                "normal",
            )
            # --- 深夜従事者 年2回目（受診日②） ---
            night_task = _sync(
                "night_second",
                _hc_night_basis(record),
                bool(record.is_night_worker) and record.exam_date_2 is None,
                "normal",
            )
            record.night2_task_id = night_task.id if night_task is not None else None
            # --- 二次検査 受診推奨日 ---
            secondary_task = _sync(
                "secondary_exam",
                record.secondary_recommended_date,
                bool(record.needs_recheck)
                and record.secondary_recommended_date is not None
                and record.secondary_exam_date is None,
                "high",
            )
            record.secondary_task_id = secondary_task.id if secondary_task is not None else None

        if commit:
            _commit_safely()
        return mail_ok
    except Exception:  # noqa: BLE001
        # セーブポイントは with を抜ける時点で巻き戻り済み。ここで
        # db.session.rollback() を呼んではならない（呼び出し元の保存が消える）。
        logger.exception("health_check リマインダーの同期で失敗")
        return False


def close_health_check_reminders(record_id: int, *, commit: bool = True) -> None:
    """レコード削除時などに、紐付くリマインド（全宛先・全種別）をアーカイブする。"""
    try:
        for ref_type in ("reservation", "night_second", "secondary_exam"):
            _close_reminder_tasks_for_record(ref_type, record_id, set())
        from app.services.health_check_mail import cancel_pending_notifications
        cancel_pending_notifications(record_id, include_manual=True, commit=False)
        if commit:
            _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("health_check リマインダーのクローズで失敗")
        db.session.rollback()


def sweep_health_check_reminders(*, now: datetime | None = None) -> dict[str, int]:
    """全レコードのリマインドを再同期する（日次実行で due_at を当日通知へ繰り上げる）。"""
    now = now or local_now()
    processed = 0
    failed = 0
    try:
        from app.models import HealthCheckRecord
        from app.tools.health_check import (
            get_global_lead_days, _sync_linked_record, _prefetch_employees,
        )

        global_lead = get_global_lead_days()
        records = HealthCheckRecord.query.filter(
            db.or_(
                HealthCheckRecord.secondary_task_id.isnot(None),
                HealthCheckRecord.night2_task_id.isnot(None),
                db.and_(
                    HealthCheckRecord.reservation_date.isnot(None),
                    HealthCheckRecord.exam_date.is_(None),
                ),
                db.and_(
                    HealthCheckRecord.needs_recheck.is_(True),
                    HealthCheckRecord.secondary_recommended_date.isnot(None),
                    HealthCheckRecord.secondary_exam_date.is_(None),
                ),
                db.and_(
                    HealthCheckRecord.is_night_worker.is_(True),
                    HealthCheckRecord.exam_date_2.is_(None),
                ),
            )
        ).all()
        # 名簿を一括で読み込む（戻り値は identity map の弱参照対策で保持する）
        _keep_alive = _prefetch_employees(records)  # noqa: F841
        for record in records:
            # 名簿（Employee）から先に再同期する。これをしないと、社員が転属・退職
            # しても DB に残った古い office_code / manager_user のまま宛先が解決され、
            # 旧営業所の健康診断担当に通知が飛び続ける。従来は画面を開いたときの
            # _check_record_access 経由でしか同期されなかった。
            try:
                _sync_linked_record(record)
            except Exception:  # noqa: BLE001
                logger.exception("health_check 名簿同期に失敗 (record_id=%s)", record.id)
                failed += 1
            if not ensure_health_check_reminders(
                record, global_lead_days=global_lead, now=now, commit=False
            ):
                failed += 1
            processed += 1
        _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("health_check リマインダーの日次スイープで失敗")
        db.session.rollback()
        failed += 1
    return {"processed": processed, "failed": failed}


def resync_health_check_recipient(username: str, *, now: datetime | None = None) -> dict[str, int]:
    """個人のToBell連携設定変更を、該当する健診タスクへ即時反映する。

    前日に作成済みのタスクがある状態で利用者が通知をOFFにした場合、日次
    スイープを待つと当日9時のpushに間に合わない可能性があるため、設定保存時に
    その人が宛先となるアクティブなレコードだけを再同期する。
    """
    now = now or local_now()
    processed = 0
    failed = 0
    try:
        from app.models import HealthCheckRecord
        candidates = HealthCheckRecord.query.filter(db.or_(
            db.and_(
                HealthCheckRecord.reservation_date.isnot(None),
                HealthCheckRecord.exam_date.is_(None),
            ),
            db.and_(
                HealthCheckRecord.needs_recheck.is_(True),
                HealthCheckRecord.secondary_recommended_date.isnot(None),
                HealthCheckRecord.secondary_exam_date.is_(None),
            ),
            db.and_(
                HealthCheckRecord.is_night_worker.is_(True),
                HealthCheckRecord.exam_date_2.is_(None),
            ),
        )).all()
        for record in candidates:
            if username not in _hc_recipients(record):
                continue
            if not ensure_health_check_reminders(record, now=now, commit=False):
                failed += 1
            processed += 1
        _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("health_check 個人通知設定の再同期に失敗 (username=%s)", username)
        db.session.rollback()
        failed += 1
    return {"processed": processed, "failed": failed}
