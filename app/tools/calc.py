from flask import Blueprint, render_template
from flask_login import login_required

calc_bp = Blueprint("calc", __name__, url_prefix="/tools/calc")



@calc_bp.route("/", methods=["GET"])
@login_required
def calc():
    return render_template("calc.html")
