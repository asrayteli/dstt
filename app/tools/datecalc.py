from flask import Blueprint, render_template, request
from datetime import datetime, timedelta
from flask_login import login_required

datecalc_bp = Blueprint("datecalc", __name__, url_prefix="/tools/datecalc")

@datecalc_bp.route("/", methods=["GET", "POST"])
@login_required
def datecalc():
    result = None
    if request.method == "POST":
        mode = request.form.get("mode")
        if mode == "addsub":
            base_dt = request.form.get("base_datetime")
            days = int(request.form.get("days") or 0)
            hours = int(request.form.get("hours") or 0)
            minutes = int(request.form.get("minutes") or 0)
            operation = request.form.get("operation")

            try:
                dt = datetime.strptime(base_dt, "%Y-%m-%dT%H:%M")
                delta = timedelta(days=days, hours=hours, minutes=minutes)
                if operation == "add":
                    result = dt + delta
                elif operation == "sub":
                    result = dt - delta
                result = result.strftime("%Y-%m-%d %H:%M")
            except Exception as e:
                result = f"エラー: {str(e)}"

        elif mode == "diff":
            dt1 = request.form.get("datetime1")
            dt2 = request.form.get("datetime2")
            try:
                t1 = datetime.strptime(dt1, "%Y-%m-%dT%H:%M")
                t2 = datetime.strptime(dt2, "%Y-%m-%dT%H:%M")
                diff = abs(t1 - t2)
                result = f"{diff.days}日と {diff.seconds // 3600}時間{(diff.seconds % 3600) // 60}分"
            except Exception as e:
                result = f"エラー: {str(e)}"

    return render_template("datecalc.html", result=result)
