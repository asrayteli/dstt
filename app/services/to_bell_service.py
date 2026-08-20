from __future__ import annotations

import os
import secrets
import uuid
from datetime import date, datetime, time, timedelta

from app.services.local_time import local_now
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import selectinload
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
    ToBellUserSettings,
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

# 共有トークンの last_used_at を書き戻す最小間隔。
SHARE_TOKEN_TOUCH_INTERVAL = timedelta(hours=1)

# 「終日タスク」の目印。時刻を指定していないタスクは、その日の 23:59:59 として保存する。
# has_explicit_notification_time() がこの値を「時刻未指定」と判定し、プッシュを送らない。
END_OF_DAY = time.max.replace(microsecond=0)  # 23:59:59

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


def end_of_day(day: date) -> datetime:
    """指定日の「終日タスク」用日時（23:59:59）。"""
    return datetime.combine(day, END_OF_DAY)


def default_due_at() -> datetime:
    """期日を指定せずに追加したタスクの既定期日＝追加した日の終日。

    カレンダー／期限ビューに必ず並ぶようにするための既定値。時刻は付けないので
    プッシュ通知は飛ばない（has_explicit_notification_time() を参照）。
    """
    return end_of_day(local_today())


def _escape_like(value: str) -> str:
    """LIKE のメタ文字をエスケープする（`%` や `_` を「そのままの文字」として扱う）。

    エスケープしないと、一括削除で `%` を入力しただけで全件一致してしまう。
    """
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _like_pattern(value: str) -> str:
    return f"%{_escape_like(value)}%"


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
                return end_of_day(parsed.date())
            return parsed
        except ValueError:
            continue
    raise ToBellInputError(field, "日付の形式が正しくありません。")


def resolve_due_at(payload: dict[str, Any]) -> datetime | None:
    """入力から期日を組み立てる。

    ``due_all_day`` が真なら、時刻部分を捨てて「その日の終日（23:59:59）」に丸める。
    詳細パネルの ``datetime-local`` は秒を持てないため、終日タスクを開いて
    そのまま保存すると 23:59:59 → 23:59:00 になり「時刻指定あり」に化けてしまう。
    終日フラグを一緒に送ることで、無変更保存でも終日のままにする。
    """
    all_day = _truthy(payload.get("due_all_day"))
    resolved = _resolve_due_at_raw(payload)
    if all_day and resolved is not None:
        return end_of_day(resolved.date())
    return resolved


def _resolve_due_at_raw(payload: dict[str, Any]) -> datetime | None:
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


def resolve_due_at_for_new_task(payload: dict[str, Any]) -> tuple[datetime, bool]:
    """新規タスクの期日と「自動で付けたか」を返す。

    期日なしタスクはカレンダー／期限ビューに一切現れないため、未指定なら
    「追加した日の終日」を入れる。ただしそれは利用者が決めた締切ではないので
    ``due_auto=True`` の印を付け、「期限切れ」の判定からは外す。
    """
    resolved = resolve_due_at(payload)
    if resolved is not None:
        return resolved, False
    return default_due_at(), True


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
    now = local_now()
    base_query = ToBellTask.query.options(
        selectinload(ToBellTask.subtasks),
        selectinload(ToBellTask.comments),
        selectinload(ToBellTask.attachments),
        selectinload(ToBellTask.tags),
    ).filter(visible_task_filter(username))
    pid = _safe_int(project_id)
    is_integration_filter = filter_name == "integrations"
    if is_integration_filter:
        # 専用フィルタ: 連携（自動生成）タスクのみを表示する。
        base_query = base_query.filter(_integration_task_clause())
    elif view == "calendar":
        # カレンダーは「その日に何があるか」を見る画面なので、Googleカレンダー取り込みや
        # CloudShift/健診PLUS 由来の予定も一緒に並べる（リスト・カンバンでは従来どおり除外）。
        pass
    else:
        # 通常フィルタ/ビューでは連携タスクを除外する（専用フィルタにのみ出す）。
        base_query = base_query.filter(_manual_task_clause())
    if view != "calendar" and not pid and not is_integration_filter:
        base_query = base_query.filter(_visible_project_clause())
    if search:
        like = _like_pattern(search.strip())
        base_query = base_query.filter(
            or_(ToBellTask.title.ilike(like, escape="\\"), ToBellTask.description.ilike(like, escape="\\"))
        )
    if pid:
        base_query = base_query.filter(ToBellTask.project_id == pid)

    # ピン留めはフィルタ条件に関わらず常に先頭に出す（アーカイブ済みは除く）。
    # ただし「アーカイブ」フィルタ自体はアーカイブ済みだけを見る画面なので混ぜない。
    if filter_name == "archived":
        pinned_tasks: list[ToBellTask] = []
        query = base_query
    else:
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
    elif filter_name == "archived":
        # アーカイブしたタスクの置き場（「戻す」で復帰できるようにするため）。
        query = query.filter(ToBellTask.status == "archived")
    elif filter_name == "inbox":
        # 「受信箱」は完了・アーカイブ以外のすべてのタスク置き場。
        # 期日を指定せず追加したタスクにも日付が入るようになったため、
        # 「期日なし」を条件にすると常に空になってしまう。
        query = query.filter(ToBellTask.status.notin_(["done", "archived"]))
    elif filter_name == "assigned":
        query = query.filter(ToBellTask.assigned_to == username, ToBellTask.status.notin_(["done", "archived"]))
    elif filter_name == "overdue":
        # 「期限切れ」は現在時刻を過ぎたもの。終日タスク(23:59:59)は当日中は期限切れにしない。
        # 期日を指定せずに追加したタスクへ自動で入れた日付（due_auto）は、利用者が決めた
        # 締切ではないので数えない。触って期日を変えた時点で対象になる。
        query = query.filter(
            ToBellTask.due_at < now,
            ToBellTask.due_auto.is_(False),
            ToBellTask.status.notin_(["done", "archived"]),
        )
    elif filter_name == "done":
        query = query.filter(ToBellTask.status == "done")
    elif filter_name == "attention":
        query = query.filter(
            ToBellTask.status.notin_(["done", "archived"]),
            or_(
                ToBellTask.assigned_to == username,
                ToBellTask.reviewer_id == username,
                ToBellTask.due_at <= end_of_day(today),
            ),
        )
    else:
        query = query.filter(
            ToBellTask.status.notin_(["done", "archived"]),
            or_(
                ToBellTask.due_at <= end_of_day(today),
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


def _visible_project_clause():
    """カレンダー専用プロジェクトのタスクを除く条件。

    リスト・カンバン・サマリー件数で共通に使う。以前はサマリーだけが除外しておらず、
    「要対応 1件」と出るのに一覧は空、という食い違いが起きていた。
    """
    hidden_subq = db.session.query(ToBellProject.id).filter(ToBellProject.calendar_only.is_(True))
    return or_(ToBellTask.project_id.is_(None), ToBellTask.project_id.notin_(hidden_subq))


def office_user_options(username: str) -> list[User]:
    actor = User.query.filter_by(username=username).first()
    if actor is None or actor.office_id is None:
        return User.query.filter_by(username=username).all()
    return User.query.filter_by(office_id=actor.office_id).order_by(User.name, User.username).all()


def create_task(username: str, payload: dict[str, Any], *, allow_source: bool = False) -> ToBellTask:
    """タスクを新規作成する。

    ``allow_source`` は他ツール連携（CloudShift / 健診PLUS / Googleカレンダー）から
    呼ぶときだけ True にする。利用者のリクエスト経由で ``source_tool`` を受け付けると、
    連携タスクを詐称して「通常フィルタから消え、一定期間で自動削除される」タスクを
    作れてしまうため、既定では無視する。
    """
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ToBellInputError("title", "タスク名を入力してください。")

    status = _choice(payload.get("status"), VALID_STATUSES, "todo")
    priority = _choice(payload.get("priority"), VALID_PRIORITIES, "normal")
    assigned_to = _coerce_same_office_user(username, payload.get("assigned_to") or username, "assigned_to")
    reviewer_id = _coerce_same_office_user(username, payload.get("reviewer_id"), "reviewer_id")

    due_at, due_is_auto = resolve_due_at_for_new_task(payload)

    task = ToBellTask(
        title=title[:MAX_TITLE_LEN],
        description=str(payload.get("description") or "").strip(),
        status=status,
        priority=priority,
        # 期日未指定なら「追加した日の終日」。カレンダーに必ず並ぶようにするため。
        due_at=due_at,
        due_auto=due_is_auto,
        start_at=parse_datetime(payload.get("start_at"), "start_at"),
        created_by=username,
        assigned_to=assigned_to,
        reviewer_id=reviewer_id,
        manual_progress=_int_between(payload.get("manual_progress"), 0, 100, 0),
        project_id=_coerce_project(username, payload.get("project_id")),
        source_tool=(str(payload.get("source_tool") or "").strip() or None) if allow_source else None,
        source_ref_type=(str(payload.get("source_ref_type") or "").strip() or None) if allow_source else None,
        source_ref_id=(str(payload.get("source_ref_id") or "").strip() or None) if allow_source else None,
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
    if "due_at" in payload or "due_date" in payload or "due_time" in payload:
        previous_due_at = task.due_at
        task.due_at = _resolve_due_at_for_update(task, payload)
        if task.due_at != previous_due_at:
            # 利用者が期日を触った＝本人が決めた締切。以後は期限切れの対象になる。
            # 値が変わっていない（詳細を開いてそのまま保存した）場合は印を維持する。
            task.due_auto = False
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


def _resolve_due_at_for_update(task: ToBellTask, payload: dict[str, Any]) -> datetime | None:
    """更新時の期日。終日タスクが「時刻指定あり」に化けないよう保護する。

    画面は終日チェックの状態を ``due_all_day`` として必ず送る。ただし古いキャッシュの
    JS など、フラグを送らないクライアントも起こりうる。その場合に限り、
    「同じ日の 23:59（datetime-local が 23:59:59 を表現できずに落ちた形）」を
    元の終日として扱い、意図しない通知が発生しないようにする。
    """
    resolved = resolve_due_at(payload)
    if "due_all_day" in payload or resolved is None:
        return resolved
    was_all_day = task.is_all_day()
    looks_truncated = (
        resolved.hour == 23
        and resolved.minute == 59
        and resolved.second == 0
        and task.due_at is not None
        and resolved.date() == task.due_at.date()
    )
    if was_all_day and looks_truncated:
        return end_of_day(resolved.date())
    return resolved


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
    remove_attachment_files(task)
    db.session.delete(task)
    db.session.commit()


def remove_attachment_files(task: ToBellTask) -> int:
    """タスクに紐づく添付ファイルの実体をディスクから削除する。

    DB 行は cascade で消えるが、実体は残る。タスクの完全削除・一括削除・日次
    クリーンアップのいずれからも呼び、孤児ファイルが溜まらないようにする。
    """
    removed = 0
    for attachment in list(task.attachments or []):
        if not attachment.stored_path:
            continue
        try:
            path = Path(attachment.stored_path)
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _match_tasks_by_name(username: str, name: str, match: str):
    """名前でタスクを検索する。操作可能（可視）なタスクのみを対象にする。"""
    keyword = (name or "").strip()
    if not keyword:
        raise ToBellInputError("name", "削除するタスク名を入力してください。")
    query = ToBellTask.query.filter(visible_task_filter(username))
    if match == "contains":
        # `%` や `_` は LIKE のワイルドカード。エスケープしないと "%" の1文字入力で
        # 操作可能な全タスクが一致し、そのまま完全削除されてしまう。
        like = _like_pattern(keyword)
        query = query.filter(ToBellTask.title.ilike(like, escape="\\"))
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
        remove_attachment_files(task)
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


def _mark_read(notification: ToBellNotification, now: datetime) -> None:
    notification.is_read = True
    notification.read_at = notification.read_at or now
    # タスクに紐づかない通知（プロジェクト通知など）は「完了させる対象」が無いため、
    # 既読になった時点で解決済みにする。そうしないと要対応バッジを消す手段が無く、
    # 60日の自動整理まで「要対応 1件」が出続けてしまう。
    if notification.task_id is None and not notification.is_resolved:
        notification.is_resolved = True
        notification.resolved_at = now


def mark_notification_read(notification_id: int, username: str) -> ToBellNotification:
    notification = _get_notification(notification_id, username)
    _mark_read(notification, utc_now())
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
    now = utc_now()
    rows = ToBellNotification.query.filter(
        ToBellNotification.user_id == username,
        or_(ToBellNotification.is_read.is_(False), ToBellNotification.is_resolved.is_(False)),
    ).all()
    updated = 0
    for row in rows:
        if row.is_read and row.is_resolved:
            continue
        was_read = row.is_read
        _mark_read(row, now)
        if not was_read:
            updated += 1
    db.session.commit()
    return updated


def notification_summary(username: str) -> dict[str, Any]:
    today = local_today()
    now = local_now()
    today_end = end_of_day(today)
    unread_count = ToBellNotification.query.filter_by(user_id=username, is_read=False).count()
    # 連携（自動生成）タスクとカレンダー専用プロジェクトのタスクは通常フィルタに
    # 出さないため、サマリーの件数からも除外する（バッジ件数と画面に見えるタスクを
    # 一致させる。以前はカレンダー専用分だけがバッジに乗り、一覧は空になっていた）。
    active_query = ToBellTask.query.filter(
        visible_task_filter(username),
        ToBellTask.status.notin_(["done", "archived"]),
        _manual_task_clause(),
        _visible_project_clause(),
    )
    due_action_count = active_query.filter(
        or_(ToBellTask.assigned_to == username, ToBellTask.reviewer_id == username, ToBellTask.due_at <= today_end),
    ).count()
    unresolved_notifications = ToBellNotification.query.filter_by(user_id=username, is_resolved=False).count()
    action_count = max(due_action_count, unresolved_notifications)
    todo_count = active_query.filter(ToBellTask.status == "todo").count()
    doing_count = active_query.filter(ToBellTask.status.in_(["doing", "blocked", "review", "returned"])).count()
    urgent_count = active_query.filter(ToBellTask.priority == "urgent").count()
    overdue_count = active_query.filter(
        ToBellTask.due_at < now, ToBellTask.due_auto.is_(False)
    ).count()
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
    return not task.is_all_day()


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
    # created_at / completed_at / updated_at はナイーブUTC、due_at は設定タイムゾーンの
    # ウォールクロック。「まだ先の予定か」を UTC の現在時刻で判定すると、UTCより西の
    # タイムゾーンでは当日ぶんを未来のまま消してしまう。due_at の比較だけ基準を合わせる。
    local_reference = local_now() + (now - utc_now())

    task_query = ToBellTask.query.filter(
        or_(
            and_(ToBellTask.status == "done", ToBellTask.completed_at.isnot(None), ToBellTask.completed_at <= cutoff),
            and_(ToBellTask.status == "archived", ToBellTask.updated_at <= cutoff),
            # 期日を過ぎたまま放置された未完了タスク。ただし「期日を指定せずに追加したので
            # 自動で入った日付」は締切ではないので、これを根拠に消してはいけない。
            # （期日なしタスクは従来ずっと残っていた。既定で日付が入るようになった結果、
            #   放置したメモが60日で完全削除される、という事故になる）
            and_(
                ToBellTask.status.notin_(["done", "archived"]),
                ToBellTask.due_at.isnot(None),
                ToBellTask.due_auto.is_(False),
                ToBellTask.due_at <= cutoff,
            ),
        )
    )
    # 連携（CloudShift / 健診PLUS / Googleカレンダー）から自動追加されたタスクは、
    # 追加(created_at)から一定期間（既定1か月）を過ぎたら削除する。
    # ただし「まだ先の予定」は消さない。Googleカレンダー取り込みは syncToken による
    # 差分同期なので、一度消すと予定に変更が入らない限り二度と復活せず、2か月先の
    # 予定が当日の1か月前に黙って消える、という事故になっていた。
    # ここでの削除は ToBell の DB 行を消すだけで、Googleカレンダー側の予定には触れない
    # （取り込みタスクは gcal_event_id を持たず、ToBell→カレンダー送信もしていないため）。
    integration_query = ToBellTask.query.filter(
        _integration_task_clause(),
        ToBellTask.created_at.isnot(None),
        ToBellTask.created_at <= integration_cutoff,
        or_(ToBellTask.due_at.is_(None), ToBellTask.due_at <= local_reference),
    )

    tasks: dict[int, ToBellTask] = {task.id: task for task in task_query.all()}
    integration_count = 0
    for task in integration_query.all():
        if task.id not in tasks:
            integration_count += 1
        tasks[task.id] = task
    task_ids = list(tasks.keys())
    for task in tasks.values():
        remove_attachment_files(task)
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
    # last_used_at は「最後に使われた日」が分かれば十分。毎リクエスト書くと
    # ToBell 配下の全API呼び出しが DB への UPDATE+COMMIT を伴ってしまうため、
    # 前回更新から一定時間空いたときだけ書き込む。
    now = utc_now()
    if row.last_used_at is None or (now - row.last_used_at) >= SHARE_TOKEN_TOUCH_INTERVAL:
        row.last_used_at = now
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

PROJECT_ORDER_PREF_KEY = "project_order"


def get_project_order(username: str) -> list[int]:
    """利用者ごとのプロジェクト並び順（プロジェクトIDの並び）。

    ``sort_order`` は全員で共有するカラムなので、そこに書き込むと一人の並べ替えが
    同じ営業所の全員の画面を変えてしまう。並び替えは利用者個人の設定として持つ。
    """
    row = ToBellUserSettings.query.filter_by(username=username).first()
    prefs = (row.preferences if row and isinstance(row.preferences, dict) else {}) or {}
    raw = prefs.get(PROJECT_ORDER_PREF_KEY)
    if not isinstance(raw, list):
        return []
    order: list[int] = []
    for item in raw:
        pid = _safe_int(item)
        if pid and pid not in order:
            order.append(pid)
    return order


def _project_sort_key(username_order: dict[int, int]):
    def key(project: ToBellProject):
        personal = username_order.get(project.id)
        return (
            0 if project.pinned else 1,
            PROJECT_STATUS_ORDER.get(project.status, 9),
            # 個人の並び順があるものを先に、その並びで。無いものは共有の sort_order で後ろへ。
            (0, personal) if personal is not None else (1, int(project.sort_order or 0)),
            (project.name or "").lower(),
            project.id,
        )

    return key


def list_projects(username: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    office_id = _user_office_id(username)
    query = ToBellProject.query.filter(_project_visible_filter(username, office_id))
    if not include_archived:
        query = query.filter(ToBellProject.status != "archived")
    personal_order = {pid: index for index, pid in enumerate(get_project_order(username))}
    projects = sorted(query.all(), key=_project_sort_key(personal_order))
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
    """プロジェクトの並び順を、その利用者個人の設定として保存する。

    以前は共有カラム ``sort_order`` を書き換えていたため、所属内の誰かが並べ替えると
    他の人の画面の並びまで変わっていた。個人設定に持つことで、作成者でなくても自分の
    サイドバーを自由に並べ替えられ、かつ他人には影響しない。
    """
    if not isinstance(ordered_ids, (list, tuple)):
        raise ToBellInputError("ordered_ids", "並び替えるプロジェクトを指定してください。")
    office_id = _user_office_id(username)
    visible = {
        p.id
        for p in ToBellProject.query.filter(_project_visible_filter(username, office_id)).all()
    }
    order: list[int] = []
    for raw in ordered_ids:
        pid = _safe_int(raw)
        # 見えないプロジェクト・重複は落とす（消えたプロジェクトのIDも溜めない）。
        if pid and pid in visible and pid not in order:
            order.append(pid)
    if not order:
        raise ToBellInputError("ordered_ids", "並び替えるプロジェクトを指定してください。")
    if get_project_order(username) == order:
        return 0
    row = ToBellUserSettings.query.filter_by(username=username).first()
    if row is None:
        row = ToBellUserSettings(username=username, integrations={}, preferences={})
        db.session.add(row)
        db.session.flush()
    prefs = dict(row.preferences or {})
    prefs[PROJECT_ORDER_PREF_KEY] = order
    row.preferences = prefs
    db.session.commit()
    return len(order)


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
        like = _like_pattern(search.strip())
        query = query.filter(
            or_(ToBellTask.title.ilike(like, escape="\\"), ToBellTask.description.ilike(like, escape="\\"))
        )
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
    # 期日未指定のため自動で入った日付は、テンプレートの明示的な期限として
    # 引き継がない。引き継ぐと生成タスクの due_auto が False になり、利用者が
    # 期限を決めていないのに翌日から「期限切れ」扱いになってしまう。
    if task.due_at is not None and not task.due_auto:
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
        due_at = end_of_day(local_today() + timedelta(days=int(tpl.get("due_in_days") or 0)))
    due_is_auto = False
    if due_at is None:
        # 新規タスクと同じ既定（追加した日の終日／期限切れには数えない）にそろえる。
        due_at = default_due_at()
        due_is_auto = True
    task = ToBellTask(
        title=title[:MAX_TITLE_LEN],
        description=str(tpl.get("description") or "").strip(),
        status="todo",
        priority=_choice(tpl.get("priority"), VALID_PRIORITIES, "normal"),
        due_at=due_at,
        due_auto=due_is_auto,
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
    from app.runtime_paths import runtime_path

    override = current_app.config.get("TO_BELL_ATTACHMENT_DIR")
    if override:
        return Path(override)
    legacy = Path(__file__).resolve().parents[2] / "var" / "to_bell" / "attachments"
    return runtime_path("tools", "to_bell", "attachments", legacy=legacy)


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
