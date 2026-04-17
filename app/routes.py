from flask import Blueprint, render_template
from flask_login import login_required, current_user

from .access_control import is_admin_user
from .announcement_store import (
    get_unread_announcements_for_user,
    mark_announcements_read,
)

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
