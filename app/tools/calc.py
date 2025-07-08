from flask import Blueprint, render_template, request
from flask_login import login_required
calc_bp = Blueprint("calc", __name__, url_prefix="/tools/calc")



@calc_bp.route("/", methods=["GET", "POST"])
@login_required
def calc():
    result = None
    if request.method == "POST":
        # ここにロジックを追加
        pass
    return render_template("calc.html", result=result)
