from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.services.to_bell_service import (
    ToBellInputError,
    add_attachment,
    add_comment,
    add_subtask,
    bulk_assign_tasks_to_project,
    complete_task,
    create_blank_template,
    create_project,
    create_task,
    create_template_from_task,
    delete_attachment,
    delete_project,
    delete_subtask,
    delete_task,
    delete_template,
    get_attachment_for_user,
    get_project_for_user,
    get_share_token,
    get_task_for_user,
    get_template_for_user,
    instantiate_template,
    issue_share_token,
    list_assignable_tasks,
    list_notifications,
    list_projects,
    list_tasks,
    list_due_notification_tasks,
    list_templates,
    mark_all_notifications_read,
    mark_notification_read,
    notification_summary,
    notify_project,
    office_user_options,
    purge_task,
    reopen_task,
    reorder_projects,
    resolve_notification,
    revoke_share_token,
    serialize_task,
    serialize_tasks,
    update_project,
    update_subtask,
    update_task,
    update_template,
)
from app.services.to_bell_push import (
    ToBellPushUnavailable,
    deactivate_subscription,
    delete_subscription,
    list_subscriptions,
    save_subscription,
    send_test_push,
    unsubscribe,
    update_subscription_label,
    vapid_public_key,
)


to_bell_bp = Blueprint("to_bell", __name__, url_prefix="/tools/to_bell")

SHARE_COOKIE_NAME = "tb_share"
SHARE_COOKIE_PATH = "/tools/to_bell"
SHARE_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180日


def _is_share_session() -> bool:
    return bool(getattr(g, "tobell_via_share_token", False))


def _block_share_session() -> None:
    """共有トークンで入ったセッションには共有リンクの管理を許さない。"""
    if _is_share_session():
        abort(403)


@to_bell_bp.route("/")
@login_required
def index():
    users = office_user_options(current_user.username)
    return render_template("to_bell.html", users=users, share_session=_is_share_session())


@to_bell_bp.route("/pwa")
@login_required
def pwa():
    users = office_user_options(current_user.username)
    return render_template("to_bell_pwa.html", users=users, share_session=_is_share_session())


@to_bell_bp.route("/s/<token>")
def open_share(token: str):
    """共有URL。ログイン不要で、Cookie にトークンを保存して ToBell に入る。"""
    from app.services.to_bell_service import resolve_share_token

    if resolve_share_token(token) is None:
        abort(404)
    response = make_response(redirect(url_for("to_bell.index")))
    response.set_cookie(
        SHARE_COOKIE_NAME,
        token,
        max_age=SHARE_COOKIE_MAX_AGE,
        path=SHARE_COOKIE_PATH,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return response


@to_bell_bp.route("/pwa/<token>")
def open_pwa_share(token: str):
    """PWA dedicated share URL."""
    from app.services.to_bell_service import resolve_share_token

    if resolve_share_token(token) is None:
        abort(404)
    response = make_response(redirect(url_for("to_bell.pwa")))
    response.set_cookie(
        SHARE_COOKIE_NAME,
        token,
        max_age=SHARE_COOKIE_MAX_AGE,
        path=SHARE_COOKIE_PATH,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return response


@to_bell_bp.route("/api/share", methods=["GET"])
@login_required
def api_share_status():
    _block_share_session()
    return jsonify(_share_payload(get_share_token(current_user.username)))


@to_bell_bp.route("/api/share/issue", methods=["POST"])
@login_required
def api_share_issue():
    _block_share_session()
    return jsonify(_share_payload(issue_share_token(current_user.username)))


@to_bell_bp.route("/api/share/revoke", methods=["POST"])
@login_required
def api_share_revoke():
    _block_share_session()
    revoke_share_token(current_user.username)
    return jsonify(_share_payload(get_share_token(current_user.username)))


def _share_payload(token_row) -> dict:
    active = bool(token_row and token_row.is_usable())
    url = ""
    pwa_url = ""
    if active:
        url = url_for("to_bell.open_share", token=token_row.token, _external=True)
        pwa_url = url_for("to_bell.open_pwa_share", token=token_row.token, _external=True)
    return {"active": active, "url": url, "mobile_url": url, "pwa_url": pwa_url}


@to_bell_bp.route("/notifier")
@login_required
def notifier():
    return render_template("to_bell_notifier.html", share_session=_is_share_session())


@to_bell_bp.route("/api/tasks", methods=["GET"])
@login_required
def api_tasks():
    username = current_user.username
    tasks = list_tasks(
        username,
        filter_name=request.args.get("filter", "today"),
        search=request.args.get("q", ""),
        project_id=request.args.get("project_id"),
        view=request.args.get("view", "list"),
    )
    return jsonify(
        {
            "tasks": serialize_tasks(tasks, username),
            "summary": notification_summary(username),
        }
    )


@to_bell_bp.route("/api/tasks", methods=["POST"])
@login_required
def api_create_task():
    def action():
        task = create_task(current_user.username, _payload())
        return serialize_task(task, current_user.username), 201

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["GET"])
@login_required
def api_task_detail(task_id: int):
    return _json_endpoint(
        lambda: serialize_task(get_task_for_user(task_id, current_user.username), current_user.username)
    )


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
@login_required
def api_update_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return serialize_task(update_task(task, _payload(), current_user.username), current_user.username)

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
        return serialize_task(complete_task(task), current_user.username)

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def api_reopen_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return serialize_task(reopen_task(task), current_user.username)

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


# ----- プロジェクト -----

@to_bell_bp.route("/api/projects", methods=["GET", "POST"])
@login_required
def api_projects():
    if request.method == "POST":
        return _json_endpoint(lambda: (create_project(current_user.username, _payload()).to_dict(), 201))
    include_archived = request.args.get("archived", "").lower() in ("1", "true", "yes")
    return jsonify({"projects": list_projects(current_user.username, include_archived=include_archived)})


@to_bell_bp.route("/api/projects/<int:project_id>", methods=["PUT", "DELETE"])
@login_required
def api_project(project_id: int):
    def action():
        project = get_project_for_user(project_id, current_user.username)
        if request.method == "DELETE":
            delete_project(project, current_user.username)
            return {"ok": True}
        return update_project(project, current_user.username, _payload()).to_dict()

    return _json_endpoint(action)


@to_bell_bp.route("/api/projects/reorder", methods=["POST"])
@login_required
def api_project_reorder():
    def action():
        payload = _payload()
        ordered = payload.get("ordered_ids") or payload.get("order") or []
        updated = reorder_projects(current_user.username, ordered)
        return {"updated": updated}

    return _json_endpoint(action)


@to_bell_bp.route("/api/projects/<int:project_id>/notify", methods=["POST"])
@login_required
def api_project_notify(project_id: int):
    def action():
        project = get_project_for_user(project_id, current_user.username)
        sent = notify_project(project, current_user.username, _payload())
        return {"sent": sent}

    return _json_endpoint(action)


@to_bell_bp.route("/api/projects/<int:project_id>/assignable-tasks", methods=["GET"])
@login_required
def api_project_assignable_tasks(project_id: int):
    def action():
        project = get_project_for_user(project_id, current_user.username)
        tasks = list_assignable_tasks(project, current_user.username, search=request.args.get("q", ""))
        return {"tasks": serialize_tasks(tasks, current_user.username)}

    return _json_endpoint(action)


@to_bell_bp.route("/api/projects/<int:project_id>/assign-tasks", methods=["POST"])
@login_required
def api_project_assign_tasks(project_id: int):
    def action():
        project = get_project_for_user(project_id, current_user.username)
        payload = _payload()
        task_ids = payload.get("task_ids") or []
        updated = bulk_assign_tasks_to_project(project, current_user.username, task_ids)
        return {"updated": updated}

    return _json_endpoint(action)


# ----- 添付ファイル -----

@to_bell_bp.route("/api/tasks/<int:task_id>/attachments", methods=["POST"])
@login_required
def api_add_attachment(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        attachment = add_attachment(task, current_user.username, request.files.get("file"))
        return attachment.to_dict(), 201

    return _json_endpoint(action)


@to_bell_bp.route("/api/attachments/<int:attachment_id>", methods=["GET"])
@login_required
def api_download_attachment(attachment_id: int):
    try:
        attachment = get_attachment_for_user(attachment_id, current_user.username)
    except ToBellInputError:
        abort(404)
    return send_file(
        attachment.stored_path,
        as_attachment=True,
        download_name=attachment.file_name,
        mimetype=attachment.mime_type or None,
    )


@to_bell_bp.route("/api/attachments/<int:attachment_id>", methods=["DELETE"])
@login_required
def api_delete_attachment(attachment_id: int):
    def action():
        delete_attachment(attachment_id, current_user.username)
        return {"ok": True}

    return _json_endpoint(action)


# ----- テンプレート -----

@to_bell_bp.route("/api/templates", methods=["GET", "POST"])
@login_required
def api_templates():
    if request.method == "POST":
        return _json_endpoint(lambda: (create_blank_template(current_user.username, _payload()).to_dict(), 201))
    return jsonify({"templates": list_templates(current_user.username)})


@to_bell_bp.route("/api/templates/<int:template_id>", methods=["PUT", "DELETE"])
@login_required
def api_template(template_id: int):
    def action():
        template = get_template_for_user(template_id, current_user.username)
        if request.method == "DELETE":
            delete_template(template, current_user.username)
            return {"ok": True}
        return update_template(template, current_user.username, _payload()).to_dict()

    return _json_endpoint(action)


@to_bell_bp.route("/api/templates/<int:template_id>/instantiate", methods=["POST"])
@login_required
def api_instantiate_template(template_id: int):
    def action():
        template = get_template_for_user(template_id, current_user.username)
        task = instantiate_template(template, current_user.username, _payload())
        return serialize_task(task, current_user.username), 201

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/template", methods=["POST"])
@login_required
def api_template_from_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        return create_template_from_task(task, current_user.username, _payload()).to_dict(), 201

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


@to_bell_bp.route("/api/push/subscriptions", methods=["GET"])
@login_required
def api_push_subscriptions():
    return jsonify({"subscriptions": list_subscriptions(current_user.username)})


@to_bell_bp.route("/api/push/subscriptions/<int:subscription_id>", methods=["PUT", "DELETE"])
@login_required
def api_push_subscription(subscription_id: int):
    if _is_mobile_request():
        return jsonify({"status": "error", "message": "通知先の編集はPC版からのみ利用できます。"}), 403
    try:
        if request.method == "DELETE":
            hard = request.args.get("hard", "").lower() in ("1", "true", "yes")
            if hard:
                return jsonify({"status": "ok", "deleted": delete_subscription(current_user.username, subscription_id)})
            return jsonify({"status": "ok", "updated": deactivate_subscription(current_user.username, subscription_id)})
        row = update_subscription_label(
            current_user.username,
            subscription_id,
            str(_payload().get("device_label") or ""),
        )
        return jsonify({"status": "ok", "subscription": row})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404


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


def _is_mobile_request() -> bool:
    ua = (request.headers.get("User-Agent", "") or "").lower()
    return any(token in ua for token in ("iphone", "ipad", "android", "mobile"))
