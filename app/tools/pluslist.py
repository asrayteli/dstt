from flask import Blueprint, render_template, request

pluslist_bp = Blueprint("pluslist", __name__, url_prefix="/tools/pluslist")

@pluslist_bp.route("/", methods=["GET", "POST"])
def pluslist():
    result = None
    if request.method == "POST":
        # ここにロジックを追加
        pass
    return render_template("pluslist.html", result=result)
