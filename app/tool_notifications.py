from __future__ import annotations

from typing import Any, Callable

from .models import CloudShiftProject
from .services.to_bell_service import notification_summary as to_bell_notification_summary


NotificationProvider = Callable[[Any], dict[str, Any]]
_PROVIDERS: dict[str, NotificationProvider] = {}


def register_tool_notification_provider(tool_key: str, provider: NotificationProvider) -> None:
    _PROVIDERS[tool_key] = provider


def get_tool_notification_summary(user) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tool_key, provider in _PROVIDERS.items():
        try:
            summary = provider(user)
        except Exception:
            summary = {}
        normalized = _normalize_summary(summary)
        if normalized["unread_count"] or normalized["action_count"] or normalized["label"]:
            result[tool_key] = normalized
    return result


def _normalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    unread = _to_int(summary.get("unread_count"))
    action = _to_int(summary.get("action_count"))
    label = str(summary.get("label") or "").strip()
    severity = str(summary.get("severity") or "info").strip()
    if severity not in {"info", "warning", "danger"}:
        severity = "info"
    return {
        "unread_count": unread,
        "action_count": action,
        "label": label,
        "severity": severity,
        "href": str(summary.get("href") or "").strip(),
    }


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_bell_provider(user) -> dict[str, Any]:
    username = str(getattr(user, "username", "") or "")
    if not username:
        return {}
    return to_bell_notification_summary(username)


def _shiftersync_provider(user) -> dict[str, Any]:
    username = str(getattr(user, "username", "") or "")
    if not username:
        return {}
    pending = 0
    unviewed = 0
    rows = CloudShiftProject.query.filter_by(owner_user_id=username).all()
    for row in rows:
        extra = row.extra_data if isinstance(row.extra_data, dict) else {}
        for item in extra.get("leave_change_requests") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "pending") != "pending":
                continue
            pending += 1
            if not item.get("owner_viewed_at"):
                unviewed += 1
    if not pending and not unviewed:
        return {}
    return {
        "unread_count": unviewed,
        "action_count": pending,
        "label": f"申請 {pending}" if pending else "未読あり",
        "severity": "warning",
        "href": "/tools/shiftersync/cloudshift",
    }


register_tool_notification_provider("to_bell", _to_bell_provider)
register_tool_notification_provider("shiftersync", _shiftersync_provider)
