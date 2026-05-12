from flask import Blueprint, render_template
from flask_login import login_required


power_imager_bp = Blueprint("power_imager", __name__, url_prefix="/tools/power_imager")


@power_imager_bp.route("/", methods=["GET"])
@login_required
def power_imager_home():
    return render_template("power_imager.html", page_title="DSTT - PowerImager")


@power_imager_bp.route("/editor", methods=["GET"])
@login_required
def power_imager_editor():
    return render_template("power_imager_editor.html")
