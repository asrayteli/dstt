from __future__ import annotations

import os
import secrets
import uuid
from datetime import date, datetime, time, timedelta

from app.services.local_time import local_now
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_
from werkzeug.utils import secure_filename

from app.models import (
    utc_now,
    ToBellAttachment,
    ToBellComment,
    ToBellNotification,
    ToBellProject,
    ToBellProjectMember,
    ToBellShareToken,
    ToBellSubtask,
    ToBellTag,
    ToBellTask,
    ToBellTemplate,
    User,
    db,
)


VALID_STATUSES = {"todo", "doing", "blocked", "review", "returned", "done", "archived"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_PROJECT_STATUSES = {"active", "done", "archived"}
VALID_SCOPES = {"private", "office", "members"}
PROJECT_STATUS_ORDER = {"active": 0, "done": 1, "archived": 2}

# 他ツール（CloudShift / 健診PLUS / Googleカレンダー等）から自動生成されたタスクは
# source_tool が設定される。これらは通常のフィルタには出さず、専用の「連携」フィルタ
# にのみ表示し、追加から一定期間で自動削除する。
INTEGRATION_TASK_RETENTION_DAYS = 30


def _integration_task_clause():
    """連携（自動生成）タスクを表す条件。source_tool が設定されているもの。"""
    return and_(ToBellTask.source_tool.isnot(None), ToBellTask.source_tool != "")


def _manual_task_clause():
    """手動作成タスクを表す条件（連携タスクの否定）。"""
    return or_(ToBellTask.source_tool.is_(None), ToBellTask.source_tool == "")


ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

# 入力テキストの最大保存長（DB カラム長に合わせた切り詰め用）
MAX_TITLE_LEN = 240
MAX_COMMENT_PREVIEW_LEN = 300
MAX_PROJECT_NAME_LEN = 160


class ToBellInputError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def local_today() -> date:
    return local_now().date()


def parse_datetime(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return datetime.combine(parsed.date(), time.max.replace(microsecond=0))
            return parsed
        except ValueError:
            continue
    raise ToBellInputError(field, "日付の形式が正しくありません。")


def resolve_due_at(payload: dict[str, Any]) -> datetime | None:
    if payload.get("due_at"):
        return parse_datetime(payload.get("due_at"), "due_at")
    due_date = str(payload.get("due_date") or "").strip()
    due_time = str(payload.get("due_time") or "").strip()
    if not due_date and not due_time:
        return None
    if not due_date:
        due_date = local_today().isoformat()
    if due_time:
        return parse_datetime(f"{due_date}T{due_time}", "due_time")
    return parse_datetime(due_date, "due_date")


def visible_task_filter(username: str):
    return or_(
        ToBellTask.created_by == username,
        ToBellTask.assigned_to == username,
        ToBellTask.reviewer_id == username,
    )


def task_visible_to(task: ToBellTask, username: str) -> bool:
    return username in {
        task.created_by,
        task.assigned_to or "",
        task.reviewer_id or "",
    }


def get_task_for_user(task_id: int, username: str) -> ToBellTask:
    task = db.session.get(ToBellTask, task_id)
    if task is None or not task_visible_to(task, username):
        raise ToBellInputError("task", "タスクが見つかりません。")
    return task


def list_tasks(
    username: str,
    *,
    filter_name: str = "today",
    search: str = "",
    project_id: Any = None,
    view: str = "list",
) -> list[ToBellTask]:
    today = local_today()
    base_query = ToBellTask.query.filter(visible_task_filter(username))
    pid = _safe_int(project_id)
    is_integration_filter = filter_name == "integrations"
    if is_integration_filter:
        # 専用フィルタ: 連携（自動生成）タスクのみを表示する。
        base_query = base_query.filter(_integration_task_clause())
    else:
        # 通常フィルタ/ビューでは連携タスクを除外する（専用フィルタにのみ出す）。
        base_query = base_query.filter(_manual_task_clause())
        if view != "calendar" and not pid:
            hidden_subq = db.session.query(ToBellProject.id).filter(ToBellProject.calendar_only.is_(True))
            base_query = base_query.filter(or_(ToBellTask.project_id.is_(None), ToBellTask.project_id.notin_(hidden_subq)))
    if search:
        like = f"%{search.strip()}%"
        base_query = base_query.filter(or_(ToBellTask.title.ilike(like), ToBellTask.description.ilike(like)))
    if pid:
        base_query = base_query.filter(ToBellTask.project_id == pid)

    # ピン留めはフィルタ条件に関わらず常に先頭に出す（アーカイブ済みは除く）。
    pinned_tasks = base_query.filter(
        ToBellTask.pinned.is_(True),
        ToBellTask.status != "archived",
    ).order_by(ToBellTask.updated_at.desc()).all()

    query = base_query.filter(ToBellTask.pinned.is_(False))
    if filter_name == "integrations":
        # 連携タスク専用フィルタ: アーカイブ以外の自動生成タスクを期限順にすべて返す。
        query = query.filter(ToBellTask.status != "archived")
    elif filter_name == "board":
        # カンバン / カレンダー用: アーカイブ以外の参加タスクをすべて返す。
        query = query.filter(ToBellTask.status != "archived")
    elif filter_name == "inbox":
        query = query.filter(ToBellTask.due_at.is_(None), ToBellTask.status.in_(["todo", "doing", "blocked", "review", "returned"]))
    elif filter_name == "assigned":
        query = query.filter(ToBellTask.assigned_to == username, ToBellTask.status.notin_(["done", "archived"]))
    elif filter_name == "overdue":
        query = query.filter(ToBellTask.due_at < datetime.combine(today, time.min), ToBellTask.status.notin_(["done", "archived"]))
    elif filter_name == "done":
        query = query.filter(ToBellTask.status == "done")
    elif filter_name == "attention":
        query = query.filter(
            ToBellTask.status.notin_(["done", "archived"]),
            or_(
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
                ToBellTask.due_at <= datetime.combine(today, time.max.replace(microsecond=0)),
            ),
        )
    else:
        query = query.filter(
            ToBellTask.status.notin_(["done", "archived"]),
            or_(
                ToBellTask.due_at <= datetime.combine(today, time.max.replace(microsecond=0)),
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
            ),
        )
    other_tasks = query.order_by(
        ToBellTask.status == "done",
        ToBellTask.due_at.is_(None),
        ToBellTask.due_at.asc(),
        ToBellTask.updated_at.desc(),
    ).all()
    return pinned_tasks + other_tasks


def office_user_options(username: str) -> list[User]:
    actor = User.query.filter_by(username=username).first()
    if actor is None or actor.office_id is None:
        return User.query.filter_by(username=username).all()
    return User.query.filter_by(office_id=actor.office_id).order_by(User.name, User.username).all()


def create_task(username: str, payload: dict[str, Any]) -> ToBellTask:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ToBellInputError("title", "タスク名を入力してください。")

    status = _choice(payload.get("status"), VALID_STATUSES, "todo")
    priority = _choice(payload.get("priority"), VALID_PRIORITIES, "normal")
    assigned_to = _coerce_same_office_user(username, payload.get("assigned_to") or username, "assigned_to")
    reviewer_id = _coerce_same_office_user(username, payload.get("reviewer_id"), "reviewer_id")

    task = ToBellTask(
        title=title[:MAX_TITLE_LEN],
        description=str(payload.get("description") or "").strip(),
        status=status,
        priority=priority,
        due_at=resolve_due_at(payload),
        start_at=parse_datetime(payload.get("start_at"), "start_at"),
        created_by=username,
        assigned_to=assigned_to,
        reviewer_id=reviewer_id,
        manual_progress=_int_between(payload.get("manual_progress"), 0, 100, 0),
        project_id=_coerce_project(username, payload.get("project_id")),
        source_tool=str(payload.get("source_tool") or "").strip() or None,
        source_ref_type=str(payload.get("source_ref_type") or "").strip() or None,
        source_ref_id=str(payload.get("source_ref_id") or "").strip() or None,
        pinned=_truthy(payload.get("pinned")),
    )
    db.session.add(task)
    db.session.flush()
    _sync_tags(task, payload.get("tags"), username)
    if task.status == "done" and task.completed_at is None:
        task.completed_at = utc_now()
    if task.status != "done":
        _create_assignment_notification(task, username)
        if task.reviewer_id:
            _create_assignment_notification(task, username, target=task.reviewer_id, event_type="reviewer", label="確認依頼")
    db.session.commit()
    return task


def update_task(task: ToBellTask, payload: dict[str, Any], actor: str) -> ToBellTask:
    old_assigned_to = task.assigned_to
    old_reviewer_id = task.reviewer_id
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ToBellInputError("title", "タスク名を入力してください。")
        task.title = title[:MAX_TITLE_LEN]
    if "description" in payload:
        task.description = str(payload.get("description") or "").strip()
    if "status" in payload:
        task.status = _choice(payload.get("status"), VALID_STATUSES, task.status)
        if task.status == "done":
            if task.completed_at is None:
                task.completed_at = utc_now()
        elif task.status != "done":
            task.completed_at = None
    if "priority" in payload:
        task.priority = _choice(payload.get("priority"), VALID_PRIORITIES, task.priority)
    if "due_at" in payload:
        task.due_at = resolve_due_at(payload)
    elif "due_date" in payload or "due_time" in payload:
        task.due_at = resolve_due_at(payload)
    if "start_at" in payload:
        task.start_at = parse_datetime(payload.get("start_at"), "start_at")
    if "assigned_to" in payload:
        task.assigned_to = _coerce_same_office_user(actor, payload.get("assigned_to"), "assigned_to")
    if "reviewer_id" in payload:
        task.reviewer_id = _coerce_same_office_user(actor, payload.get("reviewer_id"), "reviewer_id")
    if "manual_progress" in payload:
        task.manual_progress = _int_between(payload.get("manual_progress"), 0, 100, task.manual_progress)
    if "project_id" in payload:
        task.project_id = _coerce_project(actor, payload.get("project_id"))
    if "pinned" in payload:
        task.pinned = _truthy(payload.get("pinned"))
    _sync_tags(task, payload.get("tags"), actor)
    if "assigned_to" in payload and task.assigned_to != old_assigned_to:
        _create_assignment_notification(task, actor, target=task.assigned_to, event_type="assigned", label="担当タスク")
    if "reviewer_id" in payload and task.reviewer_id and task.reviewer_id != old_reviewer_id:
        _create_assignment_notification(task, actor, target=task.reviewer_id, event_type="reviewer", label="確認依頼")
    if task.status == "done":
        _resolve_task_notifications(task)
    task.updated_at = utc_now()
    db.session.commit()
    return task


def complete_task(task: ToBellTask) -> ToBellTask:
    task.status = "done"
    task.completed_at = utc_now()
    _resolve_task_notifications(task)
    db.session.commit()
    return task


def reopen_task(task: ToBellTask) -> ToBellTask:
    task.status = "todo"
    task.completed_at = None
    db.session.commit()
    return task


def delete_task(task: ToBellTask) -> None:
    task.status = "archived"
    task.updated_at = utc_now()
    db.session.commit()


def purge_task(task: ToBellTask) -> None:
    """サブタスク・コメント・通知ごとタスクを完全に削除する（元に戻せない）。"""
    db.session.delete(task)
    db.session.commit()


def _match_tasks_by_name(username: str, name: str, match: str):
    """名前でタスクを検索する。操作可能（可視）なタスクのみを対象にする。"""
    keyword = (name or "").strip()
    if not keyword:
        raise ToBellInputError("name", "削除するタスク名を入力してください。")
    query = ToBellTask.query.filter(visible_task_filter(username))
    if match == "contains":
        like = f"%{keyword}%"
        query = query.filter(ToBellTask.title.ilike(like))
    else:  # exact（既定）— 前後の空白差を吸収するため大文字小文字無視の完全一致
        query = query.filter(func.lower(ToBellTask.title) == keyword.lower())
    return query.order_by(ToBellTask.updated_at.desc()).all()


def preview_tasks_by_name(username: str, name: str, match: str = "exact") -> dict[str, Any]:
    """一括削除の事前確認。一致件数とサンプルのタイトルを返す（削除はしない）。"""
    tasks = _match_tasks_by_name(username, name, match)
    samples = [task.title for task in tasks[:10]]
    return {"count": len(tasks), "samples": samples}


def bulk_delete_tasks_by_name(username: str, name: str, match: str = "exact") -> int:
    """名前が一致する、自分が操作可能なタスクを一括で完全削除する。

    ToBell→Googleカレンダーへ送信済みのタスクは、単体削除と同様にカレンダー側の
    イベントも削除する（gcal_event_id を持つもののみ）。連携で取り込んだだけの
    タスクはイベントを持たないため、カレンダーには影響しない。
    """
    tasks = _match_tasks_by_name(username, name, match)
    if not tasks:
        return 0
    try:
        from app.services import to_bell_calendar
    except Exception:  # noqa: BLE001 - カレンダー連携が無くても削除は続行する
        to_bell_calendar = None
    deleted = 0
    for task in tasks:
        if to_bell_calendar is not None:
            try:
                to_bell_calendar.on_task_deleted(task)
            except Exception:  # noqa: BLE001 - 連携の失敗で本体削除を止めない
                pass
        db.session.delete(task)
        deleted += 1
    db.session.commit()
    return deleted


def add_subtask(task: ToBellTask, payload: dict[str, Any]) -> ToBellSubtask:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ToBellInputError("title", "サブタスク名を入力してください。")
    sort_order = len(task.subtasks)
    subtask = ToBellSubtask(task=task, title=title[:MAX_TITLE_LEN], sort_order=sort_order)
    db.session.add(subtask)
    db.session.commit()
    return subtask


def update_subtask(subtask_id: int, username: str, payload: dict[str, Any]) -> ToBellSubtask:
    subtask = db.session.get(ToBellSubtask, subtask_id)
    if subtask is None or not task_visible_to(subtask.task, username):
        raise ToBellInputError("subtask", "サブタスクが見つかりません。")
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ToBellInputError("title", "サブタスク名を入力してください。")
        subtask.title = title[:MAX_TITLE_LEN]
    if "is_done" in payload:
        subtask.is_done = bool(payload.get("is_done"))
    db.session.commit()
    return subtask


def delete_subtask(subtask_id: int, username: str) -> None:
    subtask = db.session.get(ToBellSubtask, subtask_id)
    if subtask is None or not task_visible_to(subtask.task, username):
        raise ToBellInputError("subtask", "サブタスクが見つかりません。")
    db.session.delete(subtask)
    db.session.commit()


def add_comment(task: ToBellTask, username: str, payload: dict[str, Any]) -> ToBellComment:
    body = str(payload.get("body") or "").strip()
    if not body:
        raise ToBellInputError("body", "コメントを入力してください。")
    comment = ToBellComment(task=task, body=body, created_by=username)
    db.session.add(comment)
    allowed_targets = _same_office_usernames(username)
    for target in ({task.created_by, task.assigned_to or "", task.reviewer_id or ""} - {username, ""}):
        if target not in allowed_targets:
            continue
        db.session.add(
            ToBellNotification(
                user_id=target,
                task=task,
                source_tool="to_bell",
                event_type="comment",
                title=f"コメント: {task.title}",
                body=body[:MAX_COMMENT_PREVIEW_LEN],
                href=f"/tools/to_bell?task={task.id}",
                severity="info",
            )
        )
    db.session.commit()
    return comment


def list_notifications(username: str) -> list[ToBellNotification]:
    return ToBellNotification.query.filter_by(user_id=username).order_by(ToBellNotification.created_at.desc()).limit(100).all()


def mark_notification_read(notification_id: int, username: str) -> ToBellNotification:
    notification = _get_notification(notification_id, username)
    notification.is_read = True
    notification.read_at = utc_now()
    db.session.commit()
    return notification


def resolve_notification(notification_id: int, username: str) -> ToBellNotification:
    notification = _get_notification(notification_id, username)
    notification.is_read = True
    notification.read_at = notification.read_at or utc_now()
    notification.is_resolved = True
    notification.resolved_at = utc_now()
    db.session.commit()
    return notification


def mark_all_notifications_read(username: str) -> int:
    rows = ToBellNotification.query.filter_by(user_id=username, is_read=False).all()
    for row in rows:
        row.is_read = True
        row.read_at = utc_now()
    db.session.commit()
    return len(rows)


def notification_summary(username: str) -> dict[str, Any]:
    today = local_today()
    today_end = datetime.combine(today, time.max.replace(microsecond=0))
    unread_count = ToBellNotification.query.filter_by(user_id=username, is_read=False).count()
    # 連携（自動生成）タスクは通常フィルタに出さないため、サマリーの件数からも除外する
    # （バッジ件数と画面に見えるタスクを一致させる）。
    active_query = ToBellTask.query.filter(
        visible_task_filter(username),
        ToBellTask.status.notin_(["done", "archived"]),
        _manual_task_clause(),
    )
    due_action_count = active_query.filter(
        or_(ToBellTask.assigned_to == username, ToBellTask.reviewer_id == username, ToBellTask.due_at <= today_end),
    ).count()
    unresolved_notifications = ToBellNotification.query.filter_by(user_id=username, is_resolved=False).count()
    action_count = max(due_action_count, unresolved_notifications)
    todo_count = active_query.filter(ToBellTask.status == "todo").count()
    doing_count = active_query.filter(ToBellTask.status.in_(["doing", "blocked", "review", "returned"])).count()
    urgent_count = active_query.filter(ToBellTask.priority == "urgent").count()
    overdue_count = active_query.filter(ToBellTask.due_at < datetime.combine(today, time.min)).count()
    severity = "danger" if action_count else ("warning" if unread_count else "info")
    badges = []
    if urgent_count:
        badges.append({"label": f"緊急{urgent_count}件", "severity": "danger"})
    if overdue_count:
        badges.append({"label": f"期限切れ{overdue_count}件", "severity": "danger"})
    if todo_count:
        badges.append({"label": f"未着手{todo_count}件", "severity": "info"})
    if doing_count:
        badges.append({"label": f"進行中{doing_count}件", "severity": "warning"})
    if unread_count:
        badges.append({"label": f"未読{unread_count}件", "severity": "info"})
    label = badges[0]["label"] if badges else ""
    return {
        "unread_count": unread_count,
        "action_count": action_count,
        "label": label,
        "severity": severity,
        "badges": badges[:4],
        "href": "/tools/to_bell?filter=attention" if action_count else "/tools/to_bell",
    }


def has_explicit_notification_time(task: ToBellTask) -> bool:
    """Only tasks with a user-entered time should fire push/local reminders."""
    if task.due_at is None:
        return False
    return not (
        task.due_at.hour == 23
        and task.due_at.minute == 59
        and task.due_at.second == 59
    )


def list_due_notification_tasks(username: str, *, now: datetime | None = None) -> list[ToBellTask]:
    now = now or local_now()
    cutoff = now - timedelta(days=60)
    rows = ToBellTask.query.filter(
        visible_task_filter(username),
        ToBellTask.status.notin_(["done", "archived"]),
        ToBellTask.due_at.isnot(None),
        ToBellTask.due_at <= now,
        ToBellTask.due_at >= cutoff,
    ).order_by(ToBellTask.due_at.asc()).limit(30).all()
    return [task for task in rows if has_explicit_notification_time(task)]


def task_notification_targets(task: ToBellTask) -> set[str]:
    targets = {task.created_by, task.assigned_to or "", task.reviewer_id or ""} - {""}
    creator_allowed = _same_office_usernames(task.created_by)
    return {target for target in targets if target in creator_allowed}


def cleanup_expired_records(
    *,
    now: datetime | None = None,
    retention_days: int = 60,
    integration_retention_days: int = INTEGRATION_TASK_RETENTION_DAYS,
) -> dict[str, int]:
    now = now or utc_now()
    cutoff = now - timedelta(days=retention_days)
    integration_cutoff = now - timedelta(days=integration_retention_days)

    task_query = ToBellTask.query.filter(
        or_(
            and_(ToBellTask.status == "done", ToBellTask.completed_at.isnot(None), ToBellTask.completed_at <= cutoff),
            and_(ToBellTask.status == "archived", ToBellTask.updated_at <= cutoff),
            and_(ToBellTask.status.notin_(["done", "archived"]), ToBellTask.due_at.isnot(None), ToBellTask.due_at <= cutoff),
        )
    )
    # 連携（CloudShift / 健診PLUS / Googleカレンダー）から自動追加されたタスクは、
    # 追加(created_at)から一定期間（既定1か月）を過ぎたら無条件で削除する。
    # ここでの削除は ToBell の DB 行を消すだけで、Googleカレンダー側の予定には触れない
    # （取り込みタスクは gcal_event_id を持たず、ToBell→カレンダー送信もしていないため）。
    integration_query = ToBellTask.query.filter(
        _integration_task_clause(),
        ToBellTask.created_at.isnot(None),
        ToBellTask.created_at <= integration_cutoff,
    )

    tasks: dict[int, ToBellTask] = {task.id: task for task in task_query.all()}
    integration_count = 0
    for task in integration_query.all():
        if task.id not in tasks:
            integration_count += 1
        tasks[task.id] = task
    task_ids = list(tasks.keys())
    for task in tasks.values():
        db.session.delete(task)

    notification_query = ToBellNotification.query.filter(ToBellNotification.created_at <= cutoff)
    if task_ids:
        notification_query = notification_query.filter(
            or_(
                ToBellNotification.task_id.is_(None),
                ToBellNotification.task_id.notin_(task_ids),
            )
        )
    notifications = notification_query.all()
    for notification in notifications:
        db.session.delete(notification)

    db.session.commit()
    return {
        "tasks": len(tasks),
        "integration_tasks": integration_count,
        "notifications": len(notifications),
    }


def get_share_token(username: str) -> ToBellShareToken | None:
    return ToBellShareToken.query.filter_by(user_id=username).first()


def issue_share_token(username: str) -> ToBellShareToken:
    """共有トークンを発行（既存があれば再発行して旧トークンを無効化）する。"""
    row = ToBellShareToken.query.filter_by(user_id=username).first()
    if row is None:
        row = ToBellShareToken(user_id=username)
        db.session.add(row)
    row.token = secrets.token_urlsafe(32)
    row.is_revoked = False
    row.created_at = utc_now()
    row.last_used_at = None
    db.session.commit()
    return row


def revoke_share_token(username: str) -> bool:
    row = ToBellShareToken.query.filter_by(user_id=username).first()
    if row is None or row.is_revoked:
        return False
    row.is_revoked = True
    db.session.commit()
    return True


def resolve_share_token(token: str) -> User | None:
    """共有トークンを username 経由で User に解決し、最終利用時刻を更新する。"""
    raw = (token or "").strip()
    if not raw:
        return None
    row = ToBellShareToken.query.filter_by(token=raw).first()
    if row is None or not row.is_usable():
        return None
    user = User.query.filter_by(username=row.user_id).first()
    if user is None:
        return None
    row.last_used_at = utc_now()
    db.session.commit()
    return user


# ===== タスクのシリアライズ（プロジェクト情報を注入） =====

def serialize_tasks(tasks: list[ToBellTask], username: str) -> list[dict[str, Any]]:
    project_map = _project_map({task.project_id for task in tasks}, username)
    result = []
    for task in tasks:
        data = task.to_dict(include_detail=True)
        data["project"] = _project_brief(project_map.get(task.project_id))
        result.append(data)
    return result


def serialize_task(task: ToBellTask, username: str) -> dict[str, Any]:
    data = task.to_dict(include_detail=True)
    project = None
    if task.project_id:
        project = _project_map({task.project_id}, username).get(task.project_id)
    data["project"] = _project_brief(project)
    return data


# ===== プロジェクト =====

def list_projects(username: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    office_id = _user_office_id(username)
    query = ToBellProject.query.filter(_project_visible_filter(username, office_id))
    if not include_archived:
        query = query.filter(ToBellProject.status != "archived")
    projects = sorted(
        query.all(),
        key=lambda p: (
            0 if p.pinned else 1,
            PROJECT_STATUS_ORDER.get(p.status, 9),
            int(p.sort_order or 0),
            (p.name or "").lower(),
            p.id,
        ),
    )
    project_ids = [project.id for project in projects]
    total_map: dict[int, int] = {}
    open_map: dict[int, int] = {}
    if project_ids:
        # プロジェクトごとの集計を 2 クエリにまとめる（N+1 回避）
        total_rows = (
            db.session.query(ToBellTask.project_id, func.count(ToBellTask.id))
            .filter(ToBellTask.project_id.in_(project_ids), ToBellTask.status != "archived")
            .group_by(ToBellTask.project_id)
            .all()
        )
        total_map = {pid: count for pid, count in total_rows}
        open_rows = (
            db.session.query(ToBellTask.project_id, func.count(ToBellTask.id))
            .filter(
                ToBellTask.project_id.in_(project_ids),
                ToBellTask.status.notin_(["done", "archived"]),
            )
            .group_by(ToBellTask.project_id)
            .all()
        )
        open_map = {pid: count for pid, count in open_rows}
    return [
        project.to_dict(
            task_count=total_map.get(project.id, 0),
            open_count=open_map.get(project.id, 0),
        )
        for project in projects
    ]


def get_project_for_user(project_id: int, username: str) -> ToBellProject:
    project = db.session.get(ToBellProject, project_id)
    if project is None or not project_visible_to(project, username):
        raise ToBellInputError("project", "プロジェクトが見つかりません。")
    return project


def create_project(username: str, payload: dict[str, Any]) -> ToBellProject:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ToBellInputError("name", "プロジェクト名を入力してください。")
    project = ToBellProject(
        name=name[:MAX_PROJECT_NAME_LEN],
        description=str(payload.get("description") or "").strip(),
        status=_choice(payload.get("status"), VALID_PROJECT_STATUSES, "active"),
        color=_safe_color(payload.get("color"), "#2563eb"),
        owner_id=username,
        visibility_scope=_choice(payload.get("visibility_scope"), VALID_SCOPES, "office"),
        office_id=_user_office_id(username),
        calendar_only=_truthy(payload.get("calendar_only")),
        due_at=resolve_due_at(payload),
        pinned=_truthy(payload.get("pinned")),
        sort_order=_safe_int(payload.get("sort_order")) or 0,
    )
    db.session.add(project)
    db.session.flush()
    if "members" in payload:
        _sync_project_members(project, username, payload.get("members"))
    db.session.commit()
    return project


def update_project(project: ToBellProject, username: str, payload: dict[str, Any]) -> ToBellProject:
    if project.owner_id != username and not _is_admin(username):
        raise ToBellInputError("project", "このプロジェクトを編集できるのは作成者のみです。")
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ToBellInputError("name", "プロジェクト名を入力してください。")
        project.name = name[:MAX_PROJECT_NAME_LEN]
    if "description" in payload:
        project.description = str(payload.get("description") or "").strip()
    if "status" in payload:
        project.status = _choice(payload.get("status"), VALID_PROJECT_STATUSES, project.status)
    if "color" in payload:
        project.color = _safe_color(payload.get("color"), project.color)
    if "visibility_scope" in payload:
        project.visibility_scope = _choice(payload.get("visibility_scope"), VALID_SCOPES, project.visibility_scope)
    if "calendar_only" in payload:
        project.calendar_only = _truthy(payload.get("calendar_only"))
    if "due_at" in payload or "due_date" in payload or "due_time" in payload:
        project.due_at = resolve_due_at(payload)
    if "pinned" in payload:
        project.pinned = _truthy(payload.get("pinned"))
    if "sort_order" in payload:
        project.sort_order = _safe_int(payload.get("sort_order")) or 0
    if "members" in payload:
        _sync_project_members(project, username, payload.get("members"))
    project.updated_at = utc_now()
    db.session.commit()
    return project


def reorder_projects(username: str, ordered_ids: Any) -> int:
    """利用者が見られるプロジェクトを、与えられた並び順で sort_order に反映する。"""
    if not isinstance(ordered_ids, (list, tuple)):
        raise ToBellInputError("ordered_ids", "並び替えるプロジェクトを指定してください。")
    office_id = _user_office_id(username)
    visible = {
        p.id: p
        for p in ToBellProject.query.filter(_project_visible_filter(username, office_id)).all()
    }
    updated = 0
    for index, raw in enumerate(ordered_ids):
        pid = _safe_int(raw)
        project = visible.get(pid) if pid else None
        if project is None:
            continue
        if project.sort_order != index:
            project.sort_order = index
            updated += 1
    if updated:
        db.session.commit()
    return updated


def notify_project(project: ToBellProject, actor: str, payload: dict[str, Any]) -> int:
    """プロジェクトが見える全員に通知を送る。送信者自身には送らない。"""
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title:
        raise ToBellInputError("title", "通知タイトルを入力してください。")
    severity = _choice(payload.get("severity"), {"info", "warning", "urgent"}, "info")
    recipients = sorted(_project_audience(project, actor) - {actor})
    if not recipients:
        return 0
    href = f"/tools/to_bell?project_id={project.id}"
    body_text = (body or project.name)[:1000]
    for user_id in recipients:
        db.session.add(
            ToBellNotification(
                user_id=user_id,
                source_tool="to_bell",
                event_type="project_notify",
                title=f"[{project.name}] {title}"[:MAX_TITLE_LEN],
                body=body_text,
                href=href,
                severity=severity,
            )
        )
    db.session.commit()
    return len(recipients)


def _project_audience(project: ToBellProject, actor: str) -> set[str]:
    audience: set[str] = {project.owner_id}
    if project.visibility_scope == "office" and project.office_id is not None:
        rows = User.query.filter_by(office_id=project.office_id).all()
        audience.update(row.username for row in rows if row.username)
    elif project.visibility_scope == "members":
        audience.update(member.username for member in project.members if member.username)
    return audience


def _sync_project_members(project: ToBellProject, actor: str, raw: Any) -> None:
    if project.owner_id != actor and not _is_admin(actor):
        raise ToBellInputError("members", "メンバーを変更できるのは作成者のみです。")
    if isinstance(raw, str):
        names_iter = [item.strip() for item in raw.replace("、", ",").split(",")]
    elif isinstance(raw, list):
        names_iter = [str(item).strip() for item in raw]
    else:
        names_iter = []
    allowed = _same_office_usernames(actor)
    desired: set[str] = set()
    for name in names_iter:
        if not name or name == project.owner_id:
            continue
        if name not in allowed:
            raise ToBellInputError("members", "メンバーに指定できるのは同じ営業所内のユーザーのみです。")
        desired.add(name)
    existing = {m.username: m for m in project.members}
    for username in list(existing):
        if username not in desired:
            db.session.delete(existing[username])
    for username in desired:
        if username not in existing:
            db.session.add(ToBellProjectMember(project_id=project.id, username=username))


def delete_project(project: ToBellProject, username: str) -> None:
    if project.owner_id != username and not _is_admin(username):
        raise ToBellInputError("project", "このプロジェクトを削除できるのは作成者のみです。")
    ToBellTask.query.filter_by(project_id=project.id).update({"project_id": None})
    db.session.delete(project)
    db.session.commit()


def list_assignable_tasks(project: ToBellProject, username: str, *, search: str = "") -> list[ToBellTask]:
    """このプロジェクトに紐づいていない、利用者が見られるタスク一覧。"""
    query = ToBellTask.query.filter(
        visible_task_filter(username),
        ToBellTask.status != "archived",
        or_(ToBellTask.project_id.is_(None), ToBellTask.project_id != project.id),
    )
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(ToBellTask.title.ilike(like), ToBellTask.description.ilike(like)))
    return query.order_by(
        ToBellTask.status == "done",
        ToBellTask.due_at.is_(None),
        ToBellTask.due_at.asc(),
        ToBellTask.updated_at.desc(),
    ).limit(300).all()


def bulk_assign_tasks_to_project(project: ToBellProject, username: str, task_ids: Any) -> int:
    """指定のタスクをプロジェクトに紐付け。編集権限のあるタスクだけが対象。"""
    if not isinstance(task_ids, (list, tuple)):
        raise ToBellInputError("task_ids", "対象タスクを指定してください。")
    ids = []
    for raw in task_ids:
        pid = _safe_int(raw)
        if pid:
            ids.append(pid)
    if not ids:
        raise ToBellInputError("task_ids", "対象タスクを指定してください。")
    tasks = ToBellTask.query.filter(ToBellTask.id.in_(ids)).all()
    updated = 0
    for task in tasks:
        if not task_visible_to(task, username):
            continue
        if task.project_id == project.id:
            continue
        task.project_id = project.id
        task.updated_at = utc_now()
        updated += 1
    if updated:
        db.session.commit()
    return updated


# ===== 添付ファイル =====

def add_attachment(task: ToBellTask, username: str, file_storage) -> ToBellAttachment:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ToBellInputError("file", "ファイルを選択してください。")
    raw_name = file_storage.filename
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size <= 0:
        raise ToBellInputError("file", "空のファイルは添付できません。")
    if size > ATTACHMENT_MAX_BYTES:
        raise ToBellInputError("file", "添付できるのは25MBまでです。大きいファイルはFILE POSTを利用してください。")
    safe = secure_filename(raw_name) or "attachment"
    stored_name = f"{uuid.uuid4().hex}_{safe}"
    directory = _attachment_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stored_path = directory / stored_name
    file_storage.save(str(stored_path))
    attachment = ToBellAttachment(
        task=task,
        file_name=raw_name[:255],
        stored_path=str(stored_path),
        mime_type=(getattr(file_storage, "mimetype", "") or "")[:120],
        file_size=size,
        uploaded_by=username,
    )
    db.session.add(attachment)
    db.session.commit()
    return attachment


def get_attachment_for_user(attachment_id: int, username: str) -> ToBellAttachment:
    attachment = db.session.get(ToBellAttachment, attachment_id)
    if attachment is None or not task_visible_to(attachment.task, username):
        raise ToBellInputError("attachment", "添付ファイルが見つかりません。")
    return attachment


def delete_attachment(attachment_id: int, username: str) -> None:
    attachment = get_attachment_for_user(attachment_id, username)
    try:
        path = Path(attachment.stored_path)
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    db.session.delete(attachment)
    db.session.commit()


# ===== ユーザー作成テンプレート =====

def list_templates(username: str) -> list[dict[str, Any]]:
    office_id = _user_office_id(username)
    condition = ToBellTemplate.owner_id == username
    if office_id is not None:
        condition = or_(
            condition,
            and_(
                ToBellTemplate.scope == "office",
                ToBellTemplate.office_id == office_id,
                ToBellTemplate.is_hidden.is_(False),
            ),
        )
    rows = ToBellTemplate.query.filter(condition).order_by(ToBellTemplate.name).all()
    return [row.to_dict() for row in rows]


def get_template_for_user(template_id: int, username: str) -> ToBellTemplate:
    template = db.session.get(ToBellTemplate, template_id)
    if template is None or not _template_visible_to(template, username):
        raise ToBellInputError("template", "テンプレートが見つかりません。")
    return template


def create_blank_template(username: str, payload: dict[str, Any]) -> ToBellTemplate:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ToBellInputError("name", "テンプレート名を入力してください。")
    template = ToBellTemplate(
        name=name[:MAX_PROJECT_NAME_LEN],
        description=str(payload.get("description") or "").strip(),
        owner_id=username,
        scope=_choice(payload.get("scope"), VALID_SCOPES, "private"),
        office_id=_user_office_id(username),
        payload=_normalize_template_payload(payload.get("payload") or {}),
    )
    db.session.add(template)
    db.session.commit()
    return template


def create_template_from_task(task: ToBellTask, username: str, payload: dict[str, Any]) -> ToBellTemplate:
    due_in_days = None
    if task.due_at is not None:
        due_in_days = max(0, (task.due_at.date() - local_today()).days)
    template_payload = {
        "title": task.title,
        "description": task.description or "",
        "priority": task.priority,
        "tags": [tag.name for tag in task.tags],
        "due_in_days": due_in_days,
        "subtasks": [subtask.title for subtask in task.subtasks],
    }
    name = str(payload.get("name") or "").strip() or task.title
    template = ToBellTemplate(
        name=name[:MAX_PROJECT_NAME_LEN],
        description=str(payload.get("description") or "").strip(),
        owner_id=username,
        scope=_choice(payload.get("scope"), VALID_SCOPES, "private"),
        office_id=_user_office_id(username),
        payload=template_payload,
    )
    db.session.add(template)
    db.session.commit()
    return template


def update_template(template: ToBellTemplate, username: str, payload: dict[str, Any]) -> ToBellTemplate:
    is_owner = template.owner_id == username
    if not is_owner and not _is_admin(username):
        raise ToBellInputError("template", "このテンプレートを編集できるのは作成者のみです。")
    if "name" in payload and is_owner:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ToBellInputError("name", "テンプレート名を入力してください。")
        template.name = name[:MAX_PROJECT_NAME_LEN]
    if "description" in payload and is_owner:
        template.description = str(payload.get("description") or "").strip()
    if "scope" in payload and is_owner:
        template.scope = _choice(payload.get("scope"), VALID_SCOPES, template.scope)
    if "payload" in payload and is_owner:
        template.payload = _normalize_template_payload(payload.get("payload") or {})
    if "is_hidden" in payload:
        # 所属共有テンプレートは管理者が非表示にできる。
        template.is_hidden = bool(payload.get("is_hidden"))
    template.updated_at = utc_now()
    db.session.commit()
    return template


def delete_template(template: ToBellTemplate, username: str) -> None:
    if template.owner_id != username and not _is_admin(username):
        raise ToBellInputError("template", "このテンプレートを削除できるのは作成者のみです。")
    db.session.delete(template)
    db.session.commit()


def instantiate_template(template: ToBellTemplate, username: str, payload: dict[str, Any]) -> ToBellTask:
    tpl = template.payload if isinstance(template.payload, dict) else {}
    title = str(payload.get("title") or tpl.get("title") or "").strip() or template.name
    due_at = None
    if payload.get("due_at") or payload.get("due_date") or payload.get("due_time"):
        due_at = resolve_due_at(payload)
    elif tpl.get("due_in_days") is not None:
        due_at = datetime.combine(
            local_today() + timedelta(days=int(tpl.get("due_in_days") or 0)),
            time.max.replace(microsecond=0),
        )
    task = ToBellTask(
        title=title[:MAX_TITLE_LEN],
        description=str(tpl.get("description") or "").strip(),
        status="todo",
        priority=_choice(tpl.get("priority"), VALID_PRIORITIES, "normal"),
        due_at=due_at,
        created_by=username,
        assigned_to=_coerce_same_office_user(username, payload.get("assigned_to") or username, "assigned_to"),
        project_id=_coerce_project(username, payload.get("project_id")),
    )
    db.session.add(task)
    db.session.flush()
    for index, sub_title in enumerate(tpl.get("subtasks") or []):
        clean = str(sub_title or "").strip()
        if clean:
            db.session.add(ToBellSubtask(task=task, title=clean[:MAX_TITLE_LEN], sort_order=index))
    _sync_tags(task, tpl.get("tags"), username)
    _create_assignment_notification(task, username)
    db.session.commit()
    return task


def _get_notification(notification_id: int, username: str) -> ToBellNotification:
    notification = db.session.get(ToBellNotification, notification_id)
    if notification is None or notification.user_id != username:
        raise ToBellInputError("notification", "通知が見つかりません。")
    return notification


def _choice(value: Any, choices: set[str], default: str) -> str:
    raw = str(value or "").strip()
    return raw if raw in choices else default


def _int_between(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _same_office_usernames(username: str) -> set[str]:
    actor = User.query.filter_by(username=username).first()
    if actor is None:
        return {username}
    if actor.office_id is None:
        return {username}
    return {
        user.username
        for user in User.query.filter_by(office_id=actor.office_id).all()
        if user.username
    }


def _coerce_same_office_user(actor_username: str, candidate: Any, field: str) -> str | None:
    raw = str(candidate or "").strip()
    if not raw:
        return None
    allowed = _same_office_usernames(actor_username)
    if raw not in allowed:
        raise ToBellInputError(field, "通知や担当に指定できるのは同じ営業所内のユーザーのみです。")
    return raw


def _sync_tags(task: ToBellTask, raw_tags: Any, username: str) -> None:
    if raw_tags is None:
        return
    if isinstance(raw_tags, str):
        names = [item.strip() for item in raw_tags.replace("、", ",").split(",")]
    elif isinstance(raw_tags, list):
        names = [str(item).strip() for item in raw_tags]
    else:
        names = []
    names = [name[:80] for name in names if name][:8]
    tags = []
    for name in names:
        tag = ToBellTag.query.filter(
            and_(ToBellTag.created_by == username, ToBellTag.name == name)
        ).first()
        if tag is None:
            tag = ToBellTag(name=name, created_by=username)
            db.session.add(tag)
        tags.append(tag)
    task.tags = tags


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _safe_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _safe_color(value: Any, default: str) -> str:
    raw = str(value or "").strip()
    if len(raw) == 7 and raw.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in raw[1:]):
        return raw
    return default


def _user_office_id(username: str) -> int | None:
    actor = User.query.filter_by(username=username).first()
    return actor.office_id if actor else None


def _is_admin(username: str) -> bool:
    actor = User.query.filter_by(username=username).first()
    return bool(actor and actor.is_admin)


def project_visible_to(project: ToBellProject, username: str) -> bool:
    if project.owner_id == username:
        return True
    if project.visibility_scope == "office":
        office_id = _user_office_id(username)
        return office_id is not None and office_id == project.office_id
    if project.visibility_scope == "members":
        return any(member.username == username for member in project.members)
    return False


def _project_visible_filter(username: str, office_id: int | None):
    member_subq = db.session.query(ToBellProjectMember.project_id).filter(
        ToBellProjectMember.username == username
    )
    condition = or_(
        ToBellProject.owner_id == username,
        and_(ToBellProject.visibility_scope == "members", ToBellProject.id.in_(member_subq)),
    )
    if office_id is not None:
        condition = or_(
            condition,
            and_(ToBellProject.visibility_scope == "office", ToBellProject.office_id == office_id),
        )
    return condition


def _coerce_project(username: str, value: Any) -> int | None:
    pid = _safe_int(value)
    if pid is None:
        return None
    project = db.session.get(ToBellProject, pid)
    if project is None or not project_visible_to(project, username):
        raise ToBellInputError("project_id", "指定したプロジェクトを利用できません。")
    return pid


def _project_map(project_ids, username: str) -> dict[int, ToBellProject]:
    ids = {pid for pid in project_ids if pid}
    if not ids:
        return {}
    rows = ToBellProject.query.filter(ToBellProject.id.in_(ids)).all()
    return {project.id: project for project in rows if project_visible_to(project, username)}


def _project_brief(project: ToBellProject | None) -> dict[str, Any] | None:
    if project is None:
        return None
    return {"id": project.id, "name": project.name, "color": project.color}


def _template_visible_to(template: ToBellTemplate, username: str) -> bool:
    if template.owner_id == username:
        return True
    if template.scope == "office" and not template.is_hidden:
        office_id = _user_office_id(username)
        return office_id is not None and office_id == template.office_id
    return False


def _normalize_template_payload(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    tags = data.get("tags")
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.replace("、", ",").split(",") if item.strip()]
    elif isinstance(tags, list):
        tags = [str(item).strip() for item in tags if str(item).strip()]
    else:
        tags = []
    subtasks = data.get("subtasks")
    if isinstance(subtasks, list):
        subtasks = [str(item).strip() for item in subtasks if str(item).strip()]
    else:
        subtasks = []
    due_in_days = data.get("due_in_days")
    try:
        due_in_days = int(due_in_days) if due_in_days not in (None, "") else None
    except (TypeError, ValueError):
        due_in_days = None
    return {
        "title": str(data.get("title") or "").strip()[:MAX_TITLE_LEN],
        "description": str(data.get("description") or "").strip(),
        "priority": _choice(data.get("priority"), VALID_PRIORITIES, "normal"),
        "tags": tags[:8],
        "due_in_days": due_in_days,
        "subtasks": subtasks[:20],
    }


def _attachment_dir() -> Path:
    from flask import current_app

    override = current_app.config.get("TO_BELL_ATTACHMENT_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "var" / "to_bell" / "attachments"


def _resolve_task_notifications(task: ToBellTask) -> None:
    now = utc_now()
    for notification in task.notifications:
        notification.is_resolved = True
        notification.resolved_at = now


def _create_assignment_notification(
    task: ToBellTask,
    actor: str,
    *,
    target: str | None = None,
    event_type: str = "assigned",
    label: str = "担当タスク",
) -> None:
    target = target or task.assigned_to or ""
    if not target or target == actor:
        return
    if target not in _same_office_usernames(actor):
        return
    db.session.add(
        ToBellNotification(
            user_id=target,
            task=task,
            source_tool="to_bell",
            event_type=event_type,
            title=f"{label}: {task.title}",
            body=task.description[:MAX_COMMENT_PREVIEW_LEN],
            href=f"/tools/to_bell?task={task.id}",
            severity="warning",
        )
    )
