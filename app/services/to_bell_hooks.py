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
    db.session.add(task)
    try:
        # 一意制約(uq_to_bell_task_source)で守られているため、別プロセス/二重送信が
        # 同一参照タスクを同時生成すると flush で IntegrityError になる。セーブポイントで
        # 隔離し、衝突時は相手が作った既存タスクを採用する（呼び出し元の他処理は守る）。
        with db.session.begin_nested():
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

        today_key = datetime.utcnow().strftime("%Y%m%d")
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
    db.session.add(task)
    try:
        # 一意制約(uq_to_bell_task_source)による同時生成の衝突をセーブポイントで隔離。
        with db.session.begin_nested():
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


def _close_reminder_task(source_ref_type: str, source_ref_id: str) -> None:
    """条件を満たさなくなった健診リマインドをアーカイブする。"""
    existing = ToBellTask.query.filter_by(
        source_tool="health_check",
        source_ref_type=source_ref_type,
        source_ref_id=str(source_ref_id),
    ).first()
    if existing and existing.status not in ("done", "archived"):
        existing.status = "archived"


def ensure_health_check_reminders(
    record,
    *,
    global_lead_days: int = HEALTH_CHECK_DEFAULT_LEAD_DAYS,
    now: datetime | None = None,
    commit: bool = True,
) -> None:
    """健診レコードの状態から、3種のリマインドを同期する。

    対象日（健康診断予約日／深夜従事者の受診日②／二次検査受診推奨日）の
    **前日にタスク化**し、対象日 **09:00** にアラート（push）を流す。
    タスク・通知の本文は「{氏名}さんの{ジャンル}になりました。」で統一する。

    通知は担当者（manager_user）が連携をオプトインしている場合のみ起票する。
    例外は飲み込み、健診側の保存処理を妨げない。
    （`global_lead_days` は後方互換のため残すが、本リマインドでは使用しない。）
    """
    now = now or datetime.now()
    manager = (record.manager_user or "").strip()
    opted_in = bool(manager) and is_enabled(manager, HEALTH_CHECK_INTEGRATION_KEY)
    # 担当者ごとの種別別ON/OFF（未設定は通知する）。
    notify = get_health_check_notify(manager) if opted_in else {}

    def _sync(ref_type: str, basis, condition: bool, priority: str) -> "ToBellTask | None":
        """対象日 basis の前日以降になったらタスク化（無ければ作成／あれば更新）する。
        条件を満たさない・まだ前日に達していない場合は既存タスクをクローズする。"""
        active = (
            opted_in
            and notify.get(ref_type, True)
            and condition
            and basis is not None
            and _hc_should_materialize(basis, now)
        )
        if not active:
            _close_reminder_task(ref_type, str(record.id))
            return None
        message = f"{record.employee_name}さんの{HEALTH_CHECK_REMINDER_GENRE[ref_type]}になりました。"
        return _ensure_reminder_task(
            manager_user=manager,
            title=message,
            description=message,
            source_ref_type=ref_type,
            source_ref_id=str(record.id),
            due_at=_hc_due_at(basis),
            priority=priority,
        )

    try:
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
    except Exception:  # noqa: BLE001
        logger.exception("health_check リマインダーの同期で失敗")
        db.session.rollback()


def close_health_check_reminders(record_id: int, *, commit: bool = True) -> None:
    """レコード削除時などに、紐付くリマインドをアーカイブする。"""
    try:
        _close_reminder_task("secondary_exam", str(record_id))
        _close_reminder_task("night_second", str(record_id))
        if commit:
            _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("health_check リマインダーのクローズで失敗")
        db.session.rollback()


def sweep_health_check_reminders(*, now: datetime | None = None) -> dict[str, int]:
    """全レコードのリマインドを再同期する（日次実行で due_at を当日通知へ繰り上げる）。"""
    now = now or datetime.now()
    processed = 0
    try:
        from app.models import HealthCheckRecord
        from app.tools.health_check import get_global_lead_days

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
        for record in records:
            ensure_health_check_reminders(
                record, global_lead_days=global_lead, now=now, commit=False
            )
            processed += 1
        _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("health_check リマインダーの日次スイープで失敗")
        db.session.rollback()
    return {"processed": processed}
