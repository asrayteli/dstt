from flask import Blueprint, render_template
from flask_login import login_required


power_flow_bp = Blueprint("power_flow", __name__, url_prefix="/tools/power_flow")


@power_flow_bp.route("/", methods=["GET"])
@login_required
def power_flow():
    return render_template("power_flow.html", page_title="DSTT - Power Flow")
