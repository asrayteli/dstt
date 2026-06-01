from flask import Blueprint, abort, current_app, jsonify, render_template, send_from_directory
from flask_login import login_required, current_user

from .access_control import is_admin_user, user_has_tool_access
from .announcement_store import (
    get_unread_announcements_for_user,
    mark_announcements_read,
)
from .manuals import get_manual

main = Blueprint("main", __name__)


@main.route("/")
@login_required
def index():
    user_name = current_user.name if current_user.name and current_user.name != "unknown" else "ゲスト"
    is_admin = is_admin_user()

    unread = get_unread_announcements_for_user(current_user.username)
    if unread:
        mark_announcements_read(current_user.username, [a["id"] for a in unread if "id" in a])

    from .tool_notifications import get_tool_notification_summary
    tool_notifications = get_tool_notification_summary(current_user)

    return render_template(
        "index.html",
        user_name=user_name,
        is_admin=is_admin,
        announcements_to_show=unread,
        tool_notifications=tool_notifications,
    )


@main.route("/manual/<tool_key>")
@login_required
def tool_manual(tool_key):
    manual = get_manual(tool_key)
    if manual is None:
        abort(404)
    if not user_has_tool_access(tool_key):
        abort(403)
    template = manual.get("template", "manual.html")
    return render_template(template, manual=manual, page_title=f"DSTT - {manual['title']}")


@main.route("/api/tool-notifications")
@login_required
def tool_notifications():
    from .tool_notifications import get_tool_notification_summary

    return jsonify(get_tool_notification_summary(current_user))


@main.route("/service-worker.js")
def service_worker():
    return send_from_directory(
        current_app.static_folder,
        "to_bell/service-worker.js",
        mimetype="application/javascript",
    )


@main.route("/cloudshift-service-worker.js")
def cloudshift_service_worker():
    # ルート直下で配信することで /tools/shiftersync/cloudshift/ 配下を scope に取れる。
    response = send_from_directory(
        current_app.static_folder,
        "cloudshift/service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/tools/shiftersync/cloudshift/"
    return response
