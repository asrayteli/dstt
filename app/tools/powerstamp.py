from flask import Blueprint, render_template
from flask_login import login_required

powerstamp_bp = Blueprint("powerstamp", __name__, url_prefix="/tools/powerstamp")


@powerstamp_bp.route("/", methods=["GET"])
@login_required
def powerstamp():
    return render_template("powerstamp.html")
