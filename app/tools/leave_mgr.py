from flask import Blueprint, render_template, request

leave_mgr_bp = Blueprint("leave_mgr", __name__, url_prefix="/tools/leave_mgr")

@leave_mgr_bp.route("/", methods=["GET", "POST"])
def leave_mgr():
    result = None
    if request.method == "POST":
        # ここにロジックを追加
        pass
    return render_template("leave_mgr.html", result=result)
