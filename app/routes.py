from flask import Blueprint, abort, render_template
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

    return render_template(
        "index.html",
        user_name=user_name,
        is_admin=is_admin,
        announcements_to_show=unread,
    )


@main.route("/manual/<tool_key>")
@login_required
def tool_manual(tool_key):
    manual = get_manual(tool_key)
    if manual is None:
        abort(404)
    if not user_has_tool_access(tool_key):
        abort(403)
    return render_template("manual.html", manual=manual, page_title=f"DSTT - {manual['title']}")
