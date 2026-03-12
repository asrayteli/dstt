from flask import Blueprint, render_template, request, jsonify, current_app, has_app_context
from flask_login import login_required, current_user
import os
import json
from datetime import datetime
import shutil

powerstamp_bp = Blueprint("powerstamp", __name__, url_prefix="/tools/powerstamp")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LEGACY_STORE_PATH = os.path.join(BASE_DIR, "..", "data", "powerstamp_templates.json")
SEED_STORE_PATH = os.path.join(BASE_DIR, "..", "data", "powerstamp_templates.seed.json")
MAX_TEMPLATES = 20


def _runtime_store_path() -> str:
    # 追跡対象(app/data)ではなく、実行時データ(instance)へ保存する
    if has_app_context() and current_app:
        return os.path.join(current_app.instance_path, "powerstamp_templates.json")
    # フォールバック（通常は使わない）
    return os.path.join(BASE_DIR, "..", "..", "instance", "powerstamp_templates.json")


def _ensure_runtime_store_initialized() -> str:
    store_path = os.path.abspath(_runtime_store_path())
    os.makedirs(os.path.dirname(store_path), exist_ok=True)

    if os.path.exists(store_path):
        return store_path

    # 既存環境移行: legacyファイルがあれば最優先で移す
    if os.path.exists(LEGACY_STORE_PATH):
        try:
            shutil.copy2(LEGACY_STORE_PATH, store_path)
            return store_path
        except Exception:
            pass

    # 初期化: seedがあればseedで初期化
    if os.path.exists(SEED_STORE_PATH):
        try:
            shutil.copy2(SEED_STORE_PATH, store_path)
            return store_path
        except Exception:
            pass

    # どちらもなければ空配列
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

    return store_path


def _load_templates():
    store_path = _ensure_runtime_store_initialized()
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def _save_templates(templates):
    store_path = _ensure_runtime_store_initialized()
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


@powerstamp_bp.route("/", methods=["GET"])
@login_required
def powerstamp():
    return render_template("powerstamp.html")


@powerstamp_bp.route("/api/templates", methods=["GET"])
@login_required
def list_templates():
    templates = _load_templates()
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
