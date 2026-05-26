from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, or_

from app.models import (
    ToBellComment,
    ToBellNotification,
    ToBellShareToken,
    ToBellSubtask,
    ToBellTag,
    ToBellTask,
    User,
    db,
)


VALID_STATUSES = {"todo", "doing", "blocked", "review", "returned", "done", "archived"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


class ToBellInputError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


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
        due_date = date.today().isoformat()
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


def list_tasks(username: str, *, filter_name: str = "today", search: str = "") -> list[ToBellTask]:
    today = date.today()
    query = ToBellTask.query.filter(visible_task_filter(username))
    if filter_name == "inbox":
        query = query.filter(ToBellTask.due_at.is_(None), ToBellTask.status.in_(["todo", "doing", "blocked", "review", "returned"]))
    elif filter_name == "assigned":
        query = query.filter(ToBellTask.assigned_to == username, ToBellTask.status != "done")
    elif filter_name == "overdue":
        query = query.filter(ToBellTask.due_at < datetime.combine(today, time.min), ToBellTask.status != "done")
    elif filter_name == "done":
        query = query.filter(ToBellTask.status == "done")
    elif filter_name == "attention":
        query = query.filter(
            ToBellTask.status != "done",
            or_(
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
                ToBellTask.due_at <= datetime.combine(today, time.max.replace(microsecond=0)),
            ),
        )
    else:
        query = query.filter(
            ToBellTask.status != "archived",
            or_(
                ToBellTask.due_at <= datetime.combine(today, time.max.replace(microsecond=0)),
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
            ),
        )
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(ToBellTask.title.ilike(like), ToBellTask.description.ilike(like)))
    return query.order_by(
        ToBellTask.status == "done",
        ToBellTask.due_at.is_(None),
        ToBellTask.due_at.asc(),
        ToBellTask.updated_at.desc(),
    ).all()


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
        title=title[:240],
        description=str(payload.get("description") or "").strip(),
        status=status,
        priority=priority,
        due_at=resolve_due_at(payload),
        start_at=parse_datetime(payload.get("start_at"), "start_at"),
        created_by=username,
        assigned_to=assigned_to,
        reviewer_id=reviewer_id,
        manual_progress=_int_between(payload.get("manual_progress"), 0, 100, 0),
        source_tool=str(payload.get("source_tool") or "").strip() or None,
        source_ref_type=str(payload.get("source_ref_type") or "").strip() or None,
        source_ref_id=str(payload.get("source_ref_id") or "").strip() or None,
    )
    db.session.add(task)
    db.session.flush()
    _sync_tags(task, payload.get("tags"), username)
    _create_assignment_notification(task, username)
    db.session.commit()
    return task


def update_task(task: ToBellTask, payload: dict[str, Any], actor: str) -> ToBellTask:
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ToBellInputError("title", "タスク名を入力してください。")
        task.title = title[:240]
    if "description" in payload:
        task.description = str(payload.get("description") or "").strip()
    if "status" in payload:
        task.status = _choice(payload.get("status"), VALID_STATUSES, task.status)
        if task.status == "done" and task.completed_at is None:
            task.completed_at = datetime.utcnow()
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
    _sync_tags(task, payload.get("tags"), actor)
    task.updated_at = datetime.utcnow()
    db.session.commit()
    return task


def complete_task(task: ToBellTask) -> ToBellTask:
    task.status = "done"
    task.completed_at = datetime.utcnow()
    for notification in task.notifications:
        notification.is_resolved = True
        notification.resolved_at = datetime.utcnow()
    db.session.commit()
    return task


def reopen_task(task: ToBellTask) -> ToBellTask:
    task.status = "todo"
    task.completed_at = None
    db.session.commit()
    return task


def delete_task(task: ToBellTask) -> None:
    task.status = "archived"
    task.updated_at = datetime.utcnow()
    db.session.commit()


def purge_task(task: ToBellTask) -> None:
    """サブタスク・コメント・通知ごとタスクを完全に削除する（元に戻せない）。"""
    db.session.delete(task)
    db.session.commit()


def add_subtask(task: ToBellTask, payload: dict[str, Any]) -> ToBellSubtask:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ToBellInputError("title", "サブタスク名を入力してください。")
    sort_order = len(task.subtasks)
    subtask = ToBellSubtask(task=task, title=title[:240], sort_order=sort_order)
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
        subtask.title = title[:240]
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
                body=body[:300],
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
    notification.read_at = datetime.utcnow()
    db.session.commit()
    return notification


def resolve_notification(notification_id: int, username: str) -> ToBellNotification:
    notification = _get_notification(notification_id, username)
    notification.is_read = True
    notification.read_at = notification.read_at or datetime.utcnow()
    notification.is_resolved = True
    notification.resolved_at = datetime.utcnow()
    db.session.commit()
    return notification


def mark_all_notifications_read(username: str) -> int:
    rows = ToBellNotification.query.filter_by(user_id=username, is_read=False).all()
    for row in rows:
        row.is_read = True
        row.read_at = datetime.utcnow()
    db.session.commit()
    return len(rows)


def notification_summary(username: str) -> dict[str, Any]:
    today_end = datetime.combine(date.today(), time.max.replace(microsecond=0))
    unread_count = ToBellNotification.query.filter_by(user_id=username, is_read=False).count()
    active_query = ToBellTask.query.filter(
        visible_task_filter(username),
        ToBellTask.status.notin_(["done", "archived"]),
    )
    due_action_count = active_query.filter(
        or_(ToBellTask.assigned_to == username, ToBellTask.reviewer_id == username, ToBellTask.due_at <= today_end),
    ).count()
    unresolved_notifications = ToBellNotification.query.filter_by(user_id=username, is_resolved=False).count()
    action_count = max(due_action_count, unresolved_notifications)
    todo_count = active_query.filter(ToBellTask.status == "todo").count()
    doing_count = active_query.filter(ToBellTask.status.in_(["doing", "blocked", "review", "returned"])).count()
    urgent_count = active_query.filter(ToBellTask.priority == "urgent").count()
    overdue_count = active_query.filter(ToBellTask.due_at < datetime.combine(date.today(), time.min)).count()
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
    now = now or datetime.now()
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


def cleanup_expired_records(*, now: datetime | None = None, retention_days: int = 60) -> dict[str, int]:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=retention_days)
    task_query = ToBellTask.query.filter(
        or_(
            and_(ToBellTask.status == "done", ToBellTask.completed_at.isnot(None), ToBellTask.completed_at <= cutoff),
            and_(ToBellTask.status == "archived", ToBellTask.updated_at <= cutoff),
            and_(ToBellTask.status.notin_(["done", "archived"]), ToBellTask.due_at.isnot(None), ToBellTask.due_at <= cutoff),
        )
    )
    tasks = task_query.all()
    task_ids = [task.id for task in tasks]
    for task in tasks:
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
    return {"tasks": len(tasks), "notifications": len(notifications)}


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
    row.created_at = datetime.utcnow()
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
    row.last_used_at = datetime.utcnow()
    db.session.commit()
    return user


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


def _create_assignment_notification(task: ToBellTask, actor: str) -> None:
    target = task.assigned_to or ""
    if not target or target == actor:
        return
    if target not in _same_office_usernames(actor):
        return
    db.session.add(
        ToBellNotification(
            user_id=target,
            task=task,
            source_tool="to_bell",
            event_type="assigned",
            title=f"担当タスク: {task.title}",
            body=task.description[:300],
            href=f"/tools/to_bell?task={task.id}",
            severity="warning",
        )
    )
