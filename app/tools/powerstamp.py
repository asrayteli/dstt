from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
import os
import json
from datetime import datetime

powerstamp_bp = Blueprint("powerstamp", __name__, url_prefix="/tools/powerstamp")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_STORE_PATH = os.path.join(BASE_DIR, "..", "data", "powerstamp_templates.json")
MAX_TEMPLATES = 20


def _ensure_store_dir():
    os.makedirs(os.path.dirname(TEMPLATE_STORE_PATH), exist_ok=True)


def _load_templates():
    _ensure_store_dir()
    if not os.path.exists(TEMPLATE_STORE_PATH):
        return []
    try:
        with open(TEMPLATE_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def _save_templates(templates):
    _ensure_store_dir()
    with open(TEMPLATE_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


@powerstamp_bp.route("/", methods=["GET"])
@login_required
def powerstamp():
    return render_template("powerstamp.html")


@powerstamp_bp.route("/api/templates", methods=["GET"])
@login_required
def list_templates():
    templates = _load_templates()
    # 一覧表示用に軽量情報だけ返す
    summary = [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "owner": t.get("owner"),
        }
        for t in templates
    ]
    # 新しい順
    summary.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return jsonify({"templates": summary, "max": MAX_TEMPLATES, "count": len(summary)})


@powerstamp_bp.route("/api/templates/<template_id>", methods=["GET"])
@login_required
def get_template(template_id):
    templates = _load_templates()
    for t in templates:
        if t.get("id") == template_id:
            return jsonify({"template": t})
    return jsonify({"error": "テンプレートが見つかりません"}), 404


@powerstamp_bp.route("/api/templates", methods=["POST"])
@login_required
def save_template():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    data = payload.get("data")

    if not name:
        return jsonify({"error": "テンプレート名を入力してください"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "テンプレートデータが不正です"}), 400

    templates = _load_templates()
    now = datetime.utcnow().isoformat()
    owner = getattr(current_user, "username", "unknown")

    # 同名は上書き
    existing = next((t for t in templates if t.get("name") == name), None)
    if existing:
        existing["data"] = data
        existing["updated_at"] = now
        existing["owner"] = owner
        _save_templates(templates)
        return jsonify({"ok": True, "mode": "updated", "id": existing.get("id")})

    if len(templates) >= MAX_TEMPLATES:
        return jsonify({"error": f"テンプレート保存上限は{MAX_TEMPLATES}件です"}), 400

    template_id = f"tpl_{int(datetime.utcnow().timestamp() * 1000)}"
    record = {
        "id": template_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "owner": owner,
        "data": data,
    }
    templates.append(record)
    _save_templates(templates)
    return jsonify({"ok": True, "mode": "created", "id": template_id})


@powerstamp_bp.route("/api/templates/<template_id>", methods=["DELETE"])
@login_required
def delete_template(template_id):
    templates = _load_templates()
    before = len(templates)
    templates = [t for t in templates if t.get("id") != template_id]
    if len(templates) == before:
        return jsonify({"error": "テンプレートが見つかりません"}), 404
    _save_templates(templates)
    return jsonify({"ok": True})


@powerstamp_bp.route("/preview", methods=["GET"])
@login_required
def powerstamp_preview():
    return render_template("powerstamp_preview.html")


@powerstamp_bp.route("/verify", methods=["GET"])
@login_required
def powerstamp_verify():
    return render_template("powerstamp_verify.html")
