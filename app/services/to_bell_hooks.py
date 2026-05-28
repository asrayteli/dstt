"""他DSTTツール → ToBell へのフック関数群。

各ツールはこのモジュールの関数を呼び、ユーザー個人ごとの連携許可が
ONになっているユーザーにのみ通知/タスクを生成する。例外は飲み込んで
呼び出し元の動作を妨げない（連携は補助機能）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

from app.models import ToBellNotification, ToBellTask, db
from app.services.to_bell_integrations import enabled_users, is_enabled

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
    db.session.flush()
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


def on_cloudshift_substitute_updated(
    *,
    substitute_project_id: str,
    substitute_project_title: str,
    month_key: str = "",
    day_key: str = "",
    action: str = "updated",
) -> None:
    """要代務シフト帳が更新されたとき、連携許可ユーザー全員に確認タスクを追加。

    同一営業日の同一プロジェクトに対しては1ユーザー1タスクのみ（dedupする）。
    """
    integration_key = "cloudshift.shift_update"
    try:
        targets = enabled_users(integration_key)
        if not targets:
            return
        # 日次dedup: 1日に何度更新されても、同じプロジェクトでは1人1タスク
        today_key = datetime.utcnow().strftime("%Y%m%d")
        title = f"[CloudShift] 要代務シフト帳が更新されました: {substitute_project_title}"
        if month_key or day_key:
            body_lines = [f"更新箇所: {month_key} {day_key}日 ({action})"]
        else:
            body_lines = [f"更新内容: {action}"]
        body_lines.append("ToBellで開いて確認してください。")
        body = "\n".join(body_lines)
        href = f"/tools/shiftersync/cloudshift?project={substitute_project_id}"
        if month_key:
            href += f"&month={month_key}"
        for user in targets:
            _create_task_for(
                target_user=user,
                title=title,
                description=body,
                source_tool="cloudshift",
                source_ref_type="substitute_update",
                source_ref_id=f"{user}:{substitute_project_id}:{today_key}",
                href=href,
                severity="info",
                priority="normal",
                notify_event="substitute_update",
            )
        _commit_safely()
    except Exception:  # noqa: BLE001
        logger.exception("CloudShift substitute_update の ToBell 連携で失敗")
        db.session.rollback()
