from __future__ import annotations

import secrets

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
    session,
    url_for,
)
from flask_login import current_user, login_required

from app.services.to_bell_service import (
    ToBellInputError,
    add_attachment,
    add_comment,
    add_subtask,
    bulk_assign_tasks_to_project,
    bulk_delete_tasks_by_name,
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
    preview_tasks_by_name,
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
from app.services.to_bell_integrations import (
    FILEPOST_OVERFLOW_THRESHOLD,
    get_settings as get_integration_settings,
    has_tool_access as integration_tool_access,
    is_enabled as integration_is_enabled,
    update_integrations,
)
from app.services import google_oauth
from app.services import to_bell_calendar
from app.services import to_bell_calendar_import
from app.services.to_bell_calendar import ToBellCalendarError
from app.services.to_bell_links import (
    add_employee_link,
    add_external_file,
    add_site_link,
    get_employee_link,
    get_external_file,
    get_site_link,
    list_employee_links,
    list_external_files,
    list_site_links,
    remove_employee_link,
    remove_external_file,
    remove_site_link,
    search_employees,
    search_sites,
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


# 共有トークン（ログイン不要リンク）で入ったセッションに許さない操作。
#
# 共有リンクは「本人としてToBellだけを操作できる」という約束で配る。したがって、
#   * ToBell の外（社員名簿・現場リスト・FILEPOST・Googleカレンダー）へ手が届く操作
#   * 取り返しがつかない操作（完全削除・一括削除）
#   * 他人へ影響する操作（所属全員への通知）
#   * 本人の設定・接続情報を書き換える操作
# はブロックする。UI 側でボタンを隠すだけでは、URL を直接叩けば通ってしまう。
_SHARE_BLOCKED_ENDPOINTS = frozenset({
    # 共有リンク自体の管理
    "to_bell.api_share_status",
    "to_bell.api_share_issue",
    "to_bell.api_share_revoke",
    # 取り返しのつかない削除
    "to_bell.api_bulk_delete_tasks",
    # 他人へ影響する
    "to_bell.api_project_notify",
    # 通知先（端末）の管理
    "to_bell.api_push_subscribe",
    "to_bell.api_push_unsubscribe",
    "to_bell.api_push_subscriptions",
    "to_bell.api_push_subscription",
    "to_bell.api_push_test",
    # 設定・外部接続
    "to_bell.api_settings_integrations",
    "to_bell.api_google_connect",
    "to_bell.api_google_callback",
    "to_bell.api_google_disconnect",
    "to_bell.api_google_reminders",
    "to_bell.api_google_import_settings",
    "to_bell.api_google_import_now",
    "to_bell.api_task_calendar_enable",
    "to_bell.api_task_calendar_disable",
    # pluslist / siteplus / FILEPOST への到達
    "to_bell.api_employee_links",
    "to_bell.api_employee_link_delete",
    "to_bell.api_employee_search",
    "to_bell.api_site_links",
    "to_bell.api_site_link_delete",
    "to_bell.api_site_search",
    "to_bell.api_filepost_threshold",
    "to_bell.api_filepost_upload",
    "to_bell.api_filepost_files_list",
    "to_bell.api_filepost_files_delete",
})


def _is_share_session() -> bool:
    return bool(getattr(g, "tobell_via_share_token", False))


def _block_share_session() -> None:
    """共有トークンで入ったセッションには共有リンクの管理を許さない。"""
    if _is_share_session():
        abort(403)


@to_bell_bp.before_request
def _guard_share_session():
    """共有セッションからの越境操作をまとめて弾く。

    個々のビューに ``_block_share_session()`` を書き足す方式だと、エンドポイントを
    追加したときに書き忘れが起きる。ここで一括して落とす。
    """
    # current_user は遅延解決される。共有トークンを読む request_loader は
    # current_user に触れて初めて動くため、ここで一度参照して g のフラグを確定させる。
    # （これを忘れると before_request の時点ではフラグが未設定で、ガードが素通りする）
    current_user.is_authenticated  # noqa: B018 - 遅延ロードの発火が目的
    if not _is_share_session():
        return None
    if request.endpoint in _SHARE_BLOCKED_ENDPOINTS:
        abort(403, description="共有リンクではこの操作を利用できません。")
    # 完全削除（?hard=1）は共有リンクからは許可しない（アーカイブは可）。
    if request.endpoint == "to_bell.api_delete_task" and _wants_hard_delete():
        abort(403, description="共有リンクからタスクを完全削除することはできません。")
    return None


def _wants_hard_delete() -> bool:
    return request.args.get("hard", "").lower() in ("1", "true", "yes")


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


@to_bell_bp.route("/api/tasks/bulk-delete", methods=["GET", "POST"])
@login_required
def api_bulk_delete_tasks():
    """タスク名による一括削除（設定から使用）。

    GET  … 事前確認。一致件数とサンプルを返す（削除しない）。
    POST … 実際に削除する。
    """
    _block_share_session()

    def _match_mode(value: str) -> str:
        return "contains" if str(value or "").strip().lower() == "contains" else "exact"

    if request.method == "GET":
        name = request.args.get("name", "")
        match = _match_mode(request.args.get("match"))
        return _json_endpoint(lambda: preview_tasks_by_name(current_user.username, name, match))

    payload = _payload()
    name = payload.get("name", "")
    match = _match_mode(payload.get("match"))
    return _json_endpoint(
        lambda: {"deleted": bulk_delete_tasks_by_name(current_user.username, name, match)}
    )


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
        task = update_task(task, _payload(), current_user.username)
        to_bell_calendar.on_task_changed(task)
        return serialize_task(task, current_user.username)

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: int):
    hard = _wants_hard_delete()

    def action():
        task = get_task_for_user(task_id, current_user.username)
        to_bell_calendar.on_task_deleted(task)
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
        task = complete_task(task)
        to_bell_calendar.on_task_changed(task)
        return serialize_task(task, current_user.username)

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def api_reopen_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        task = reopen_task(task)
        to_bell_calendar.on_task_changed(task)
        return serialize_task(task, current_user.username)

    return _json_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/pin", methods=["POST"])
@login_required
def api_pin_task(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        payload = _payload()
        desired = payload.get("pinned")
        if desired is None:
            task = update_task(task, {"pinned": not bool(task.pinned)}, current_user.username)
        else:
            task = update_task(task, {"pinned": desired}, current_user.username)
        return serialize_task(task, current_user.username)

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
    _block_share_session()
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
    _block_share_session()
    endpoint = str(_payload().get("endpoint") or "").strip()
    return jsonify({"status": "ok", "updated": unsubscribe(current_user.username, endpoint)})


@to_bell_bp.route("/api/push/subscriptions", methods=["GET"])
@login_required
def api_push_subscriptions():
    return jsonify({"subscriptions": list_subscriptions(current_user.username)})


@to_bell_bp.route("/api/push/subscriptions/<int:subscription_id>", methods=["PUT", "DELETE"])
@login_required
def api_push_subscription(subscription_id: int):
    _block_share_session()
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
    _block_share_session()
    try:
        return jsonify({"status": "ok", **send_test_push(current_user.username)})
    except ToBellPushUnavailable as exc:
        return jsonify({"status": "unavailable", "message": str(exc)}), 503


# ----- 設定 / DSTT連携 -----

@to_bell_bp.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    return jsonify(get_integration_settings(current_user.username))


@to_bell_bp.route("/api/settings/integrations", methods=["PUT"])
@login_required
def api_settings_integrations():
    # アクセス権のない連携キーは update_integrations 側で無視される。
    return jsonify(update_integrations(current_user.username, _payload()))


# ----- Google Calendar 連携 -----

_GCAL_STATE_KEY = "tb_gcal_oauth_state"


def _google_callback_uri() -> str:
    return url_for("to_bell.api_google_callback", _external=True)


@to_bell_bp.route("/api/google/status", methods=["GET"])
@login_required
def api_google_status():
    status = to_bell_calendar.connection_status(current_user.username)
    status.update(to_bell_calendar_import.import_status(current_user.username))
    return jsonify(status)


@to_bell_bp.route("/api/google/connect", methods=["GET"])
@login_required
def api_google_connect():
    """同意画面へリダイレクトする。"""
    _block_share_session()
    if not google_oauth.is_configured():
        abort(503, description="Google連携が未設定です（環境変数 DSTT_GOOGLE_CLIENT_ID / SECRET）。")
    state = secrets.token_urlsafe(24)
    session[_GCAL_STATE_KEY] = state
    return redirect(google_oauth.build_auth_url(state, _google_callback_uri()))


@to_bell_bp.route("/api/google/callback", methods=["GET"])
@login_required
def api_google_callback():
    """Google からのコールバック。state 検証 → token 交換 → 保存。"""
    _block_share_session()
    expected = session.pop(_GCAL_STATE_KEY, None)
    received = request.args.get("state")
    error = request.args.get("error")
    base_url = url_for("to_bell.index")
    if error:
        return redirect(f"{base_url}?google_error={error}")
    if not expected or not received or not secrets.compare_digest(str(expected), str(received)):
        return redirect(f"{base_url}?google_error=state_mismatch")
    code = request.args.get("code")
    if not code:
        return redirect(f"{base_url}?google_error=no_code")
    try:
        token_response = google_oauth.exchange_code(code, _google_callback_uri())
        to_bell_calendar.store_connection(current_user.username, token_response)
    except google_oauth.GoogleOAuthError:
        return redirect(f"{base_url}?google_error=token_exchange")
    return redirect(f"{base_url}?google=connected")


@to_bell_bp.route("/api/google/disconnect", methods=["POST"])
@login_required
def api_google_disconnect():
    _block_share_session()
    to_bell_calendar.disconnect(current_user.username)
    return jsonify(to_bell_calendar.connection_status(current_user.username))


@to_bell_bp.route("/api/google/reminders", methods=["PUT"])
@login_required
def api_google_reminders():
    _block_share_session()

    def action():
        reminders = _payload().get("reminders")
        saved = to_bell_calendar.set_default_reminders(current_user.username, reminders)
        return {"default_reminders": saved}

    return _calendar_endpoint(action)


@to_bell_bp.route("/api/google/import-settings", methods=["PUT"])
@login_required
def api_google_import_settings():
    _block_share_session()

    def action():
        payload = _payload()
        if "mode" in payload:
            to_bell_calendar_import.set_import_mode(current_user.username, payload.get("mode"))
        if "interval" in payload:
            to_bell_calendar_import.set_import_interval(current_user.username, payload.get("interval"))
        return to_bell_calendar_import.import_status(current_user.username)

    return _calendar_endpoint(action)


@to_bell_bp.route("/api/google/import", methods=["POST"])
@login_required
def api_google_import_now():
    """「今すぐ取り込み」。間隔に関係なく即時実行する。"""
    _block_share_session()

    def action():
        result = to_bell_calendar_import.import_for_user(current_user.username)
        return {"result": result, "status": {
            **to_bell_calendar.connection_status(current_user.username),
            **to_bell_calendar_import.import_status(current_user.username),
        }}

    return _calendar_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/calendar", methods=["POST"])
@login_required
def api_task_calendar_enable(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        reminder_override = _payload().get("reminders")
        result = to_bell_calendar.enable_for_task(task, current_user.username, reminder_override)
        return {**result, "task": serialize_task(task, current_user.username)}

    return _calendar_endpoint(action)


@to_bell_bp.route("/api/tasks/<int:task_id>/calendar", methods=["DELETE"])
@login_required
def api_task_calendar_disable(task_id: int):
    def action():
        task = get_task_for_user(task_id, current_user.username)
        result = to_bell_calendar.disable_for_task(task, current_user.username)
        return {**result, "task": serialize_task(task, current_user.username)}

    return _calendar_endpoint(action)


def _calendar_endpoint(action):
    try:
        result = action()
        status = 200
        if isinstance(result, tuple):
            result, status = result
        return jsonify(result), status
    except ToBellInputError as exc:
        return jsonify({"error": exc.message, "field": exc.field}), 400
    except ToBellCalendarError as exc:
        return jsonify({"error": str(exc)}), 400


# ----- 紐付け (pluslist 社員 / siteplus 現場) -----


def _require_link_permission(integration_key: str) -> None:
    """連携機能を使える条件。

    ``integration_is_enabled`` は「個人トグルON」かつ「連携先ツールのアクセス権あり」
    を判定する（app/services/to_bell_integrations.py）。トグルは利用者が自分で押せる
    ため、トグルだけを条件にすると pluslist / siteplus（sensitive＝個別付与が必要）の
    名簿を権限のない利用者が ToBell 経由で全件検索できてしまう。

    「アクセス権あり」には、連携先ツールの通常許可のほかに、ツール間API専用許可
    （scope='api'）も含む。ToBell の紐付けはツール間APIそのものであり、扱う項目も
    社員番号・氏名・営業所名などの識別情報に限られるため。
    """
    if not integration_tool_access(current_user.username, integration_key):
        abort(403, description="この連携先ツールへのアクセス権がありません。管理者に付与を依頼してください。")
    if not integration_is_enabled(current_user.username, integration_key):
        abort(403, description="連携が無効です。ToBellの設定で有効化してください。")


def _ensure_target_exists(target_type: str, target_id: int) -> None:
    try:
        if target_type == "project":
            get_project_for_user(target_id, current_user.username)
        elif target_type == "task":
            get_task_for_user(target_id, current_user.username)
        else:
            abort(404)
    except ToBellInputError:
        abort(404)


def _arg_int(value, default: int = 0) -> int:
    """クエリ/フォームの数値パラメータを安全に int 化する。不正値は 400。"""
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        abort(400)


def _ensure_link_owned(row) -> None:
    """紐付け行の対象（タスク/プロジェクト）を現在ユーザーが操作可能か検証する。

    link_id だけで他人の紐付けを削除できる IDOR を防ぐため、対象の可視性を確認する。
    """
    if row is None:
        abort(404)
    _ensure_target_exists(row.target_type, row.target_id)


@to_bell_bp.route("/api/links/employees", methods=["GET", "POST"])
@login_required
def api_employee_links():
    _require_link_permission("pluslist.linkage")
    if request.method == "GET":
        target_type = request.args.get("target_type", "")
        target_id = _arg_int(request.args.get("target_id"))
        _ensure_target_exists(target_type, target_id)
        return jsonify({"links": list_employee_links(target_type, target_id)})

    payload = _payload()
    target_type = str(payload.get("target_type") or "")
    target_id = _arg_int(payload.get("target_id"))
    employee_id = _arg_int(payload.get("employee_id"))
    _ensure_target_exists(target_type, target_id)
    try:
        row = add_employee_link(target_type, target_id, employee_id, current_user.username)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"link_id": row.id}), 201


@to_bell_bp.route("/api/links/employees/<int:link_id>", methods=["DELETE"])
@login_required
def api_employee_link_delete(link_id: int):
    _require_link_permission("pluslist.linkage")
    _ensure_link_owned(get_employee_link(link_id))
    return jsonify({"ok": remove_employee_link(link_id)})


@to_bell_bp.route("/api/links/employees/search", methods=["GET"])
@login_required
def api_employee_search():
    _require_link_permission("pluslist.linkage")
    return jsonify({"results": search_employees(request.args.get("q", ""))})


@to_bell_bp.route("/api/links/sites", methods=["GET", "POST"])
@login_required
def api_site_links():
    _require_link_permission("siteplus.linkage")
    if request.method == "GET":
        target_type = request.args.get("target_type", "")
        target_id = _arg_int(request.args.get("target_id"))
        _ensure_target_exists(target_type, target_id)
        return jsonify({"links": list_site_links(target_type, target_id)})

    payload = _payload()
    target_type = str(payload.get("target_type") or "")
    target_id = _arg_int(payload.get("target_id"))
    site_row_id = _arg_int(payload.get("site_row_id"))
    _ensure_target_exists(target_type, target_id)
    try:
        row = add_site_link(target_type, target_id, site_row_id, current_user.username)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"link_id": row.id}), 201


@to_bell_bp.route("/api/links/sites/<int:link_id>", methods=["DELETE"])
@login_required
def api_site_link_delete(link_id: int):
    _require_link_permission("siteplus.linkage")
    _ensure_link_owned(get_site_link(link_id))
    return jsonify({"ok": remove_site_link(link_id)})


@to_bell_bp.route("/api/links/sites/search", methods=["GET"])
@login_required
def api_site_search():
    _require_link_permission("siteplus.linkage")
    return jsonify({"results": search_sites(request.args.get("q", ""))})


# ----- FILEPOST 連携 -----


@to_bell_bp.route("/api/integrations/filepost/threshold", methods=["GET"])
@login_required
def api_filepost_threshold():
    return jsonify(
        {
            "threshold_bytes": FILEPOST_OVERFLOW_THRESHOLD,
            "overflow_enabled": integration_is_enabled(
                current_user.username, "filepost.attachment_overflow"
            ),
            "project_files_enabled": integration_is_enabled(
                current_user.username, "filepost.project_files"
            ),
        }
    )


@to_bell_bp.route("/api/integrations/filepost/upload", methods=["POST"])
@login_required
def api_filepost_upload():
    """ToBellタスク/プロジェクトに紐付けるためのFILEPOST非公開アップロード。"""
    target_type = str(request.form.get("target_type") or "")
    target_id = _arg_int(request.form.get("target_id"))

    try:
        if target_type == "task":
            if not integration_is_enabled(current_user.username, "filepost.attachment_overflow"):
                abort(403, description="添付オーバー連携が無効です。")
            get_task_for_user(target_id, current_user.username)
        elif target_type == "project":
            if not integration_is_enabled(current_user.username, "filepost.project_files"):
                abort(403, description="プロジェクトFILEPOST連携が無効です。")
            get_project_for_user(target_id, current_user.username)
        else:
            abort(400)
    except ToBellInputError:
        abort(404)

    file_storage = request.files.get("file")
    if file_storage is None or not file_storage.filename:
        return jsonify({"error": "ファイルが指定されていません"}), 400

    from app.tools.share import create_internal_share

    try:
        share_result = create_internal_share(
            file_storage=file_storage,
            uploader=current_user.username,
            title=file_storage.filename,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    note = str(request.form.get("note") or "")
    row = add_external_file(
        target_type=target_type,
        target_id=target_id,
        share_id=share_result["uid"],
        file_name=share_result["file_name"],
        file_size=share_result["file_size"],
        mime_type=share_result["mime_type"],
        private_url=share_result["download_url"],
        uploaded_by=current_user.username,
        note=note,
    )
    return jsonify(row.to_dict()), 201


@to_bell_bp.route("/api/integrations/filepost/files", methods=["GET"])
@login_required
def api_filepost_files_list():
    target_type = request.args.get("target_type", "")
    target_id = _arg_int(request.args.get("target_id"))
    try:
        if target_type == "task":
            get_task_for_user(target_id, current_user.username)
        elif target_type == "project":
            get_project_for_user(target_id, current_user.username)
        else:
            abort(400)
    except ToBellInputError:
        abort(404)
    return jsonify({"files": list_external_files(target_type, target_id)})


@to_bell_bp.route("/api/integrations/filepost/files/<int:file_id>", methods=["DELETE"])
@login_required
def api_filepost_files_delete(file_id: int):
    _ensure_link_owned(get_external_file(file_id))
    return jsonify({"ok": remove_external_file(file_id)})


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
