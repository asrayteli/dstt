from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.services.worktime_engine import calculate, request_from_json, result_to_dict


worktime_bp = Blueprint("worktime", __name__, url_prefix="/tools/worktime")


@worktime_bp.route("/api/calculate", methods=["POST"])
def api_calculate():
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "ログインが必要です"}), 401
    try:
        worktime_request = request_from_json(request.get_json(silent=True))
        result = calculate(worktime_request)
    except (TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "result": result_to_dict(result)})
