from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.services.to_bell_service import (
    ToBellInputError,
    add_comment,
    add_subtask,
    complete_task,
    create_task,
    delete_subtask,
    delete_task,
    get_task_for_user,
    list_notifications,
    list_tasks,
    list_due_notification_tasks,
    mark_all_notifications_read,
    mark_notification_read,
    notification_summary,
    office_user_options,
    purge_task,
    reopen_task,
    resolve_notification,
    update_subtask,
    update_task,
)
from app.services.to_bell_push import (
    ToBellPushUnavailable,
    save_subscription,
    send_test_push,
    unsubscribe,
    vapid_public_key,
)


to_bell_bp = Blueprint("to_bell", __name__, url_prefix="/tools/to_bell")


@to_bell_bp.route("/")
@login_required
def index():
    users = office_user_options(current_user.username)
    return render_template("to_bell.html", users=users)


@to_bell_bp.route("/notifier")
@login_required
def notifier():
    return render_template("to_bell_notifier.html")


@to_bell_bp.route("/api/tasks", methods=["GET"])
@login_required
def api_tasks():
    tasks = list_tasks(
        current_user.username,
        filter_name=request.args.get("filter", "today"),
        search=request.args.get("q", ""),
    )
    return jsonify(
        {
            "tasks": [task.to_dict() for task in tasks],
            "summary": notification_summary(current_user.username),
        }
    )


@to_bell_bp.route("/api/tasks", methods=["POST"])
@login_required
def api_create_task():
    return _json_endpoint(lambda: (create_task(current_user.username, _payload()).to_dict(), 201))


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["GET"])
@login_required
def api_task_detail(task_id: int):
    return _json_endpoint(lambda: get_task_for_user(task_id, current_user.username).to_dict())


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
@login_required
def api_update_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return update_task(task, _payload(), current_user.username).to_dict()

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: int):
    hard = request.args.get("hard", "").lower() in ("1", "true", "yes")

    def action():
        task = get_task_for_user(task_id, current_user.username)
        if hard:
            purge_task(task)
        else:
            delete_task(task)
        return {"ok": True}

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def api_complete_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return complete_task(task).to_dict()

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def api_reopen_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return reopen_task(task).to_dict()

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/subtasks", methods=["POST"])
@login_required
def api_add_subtask(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return add_subtask(task, _payload()).to_dict(), 201

    return _json_endpoint(action)


@to_bell_bp.route("/api/subtasks/<int:subtask_id>", methods=["PUT"])
@login_required
def api_update_subtask(subtask_id: int):
    return _json_endpoint(lambda: update_subtask(subtask_id, current_user.username, _payload()).to_dict())


@to_bell_bp.route("/api/subtasks/<int:subtask_id>", methods=["DELETE"])
@login_required
def api_delete_subtask(subtask_id: int):
    def action():
        delete_subtask(subtask_id, current_user.username)
        return {"ok": True}

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/comments", methods=["GET", "POST"])
@login_required
def api_comments(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        if request.method == "POST":
            return add_comment(task, current_user.username, _payload()).to_dict(), 201
        return {"comments": [comment.to_dict() for comment in task.comments]}

    return _json_endpoint(action)


@to_bell_bp.route("/api/notifications", methods=["GET"])
@login_required
def api_notifications():
    rows = list_notifications(current_user.username)
    return jsonify({"notifications": [row.to_dict() for row in rows]})


@to_bell_bp.route("/api/notifications/due-tasks", methods=["GET"])
@login_required
def api_due_task_notifications():
    rows = list_due_notification_tasks(current_user.username)
    return jsonify({"tasks": [row.to_dict(include_detail=False) for row in rows]})


@to_bell_bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def api_read_notification(notification_id: int):
    return _json_endpoint(lambda: mark_notification_read(notification_id, current_user.username).to_dict())


@to_bell_bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_read_all_notifications():
    return _json_endpoint(lambda: {"updated": mark_all_notifications_read(current_user.username)})


@to_bell_bp.route("/api/notifications/<int:notification_id>/resolve", methods=["POST"])
@login_required
def api_resolve_notification(notification_id: int):
    return _json_endpoint(lambda: resolve_notification(notification_id, current_user.username).to_dict())


@to_bell_bp.route("/api/push/public-key")
@login_required
def api_push_public_key():
    try:
        return jsonify({"status": "ok", "public_key": vapid_public_key()})
    except ToBellPushUnavailable as exc:
        return jsonify({"status": "unavailable", "public_key": "", "message": str(exc)}), 503


@to_bell_bp.route("/api/push/subscribe", methods=["POST"])
@login_required
def api_push_subscribe():
    try:
        payload = _payload()
        subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else payload
        row = save_subscription(
            current_user.username,
            subscription,
            user_agent=request.headers.get("User-Agent", ""),
        )
        return jsonify({"status": "ok", "device_label": row.device_label})
    except (ValueError, ToBellPushUnavailable) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@to_bell_bp.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def api_push_unsubscribe():
    endpoint = str(_payload().get("endpoint") or "").strip()
    return jsonify({"status": "ok", "updated": unsubscribe(current_user.username, endpoint)})


@to_bell_bp.route("/api/push/test", methods=["POST"])
@login_required
def api_push_test():
    try:
        return jsonify({"status": "ok", **send_test_push(current_user.username)})
    except ToBellPushUnavailable as exc:
        return jsonify({"status": "unavailable", "message": str(exc)}), 503


def _payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _json_endpoint(action):
    try:
        result = action()
        status = 200
        if isinstance(result, tuple):
            result, status = result
        return jsonify(result), status
    except ToBellInputError as exc:
        return jsonify({"error": exc.message, "field": exc.field}), 400
