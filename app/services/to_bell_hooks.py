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
