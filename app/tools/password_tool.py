from __future__ import annotations

import base64
import binascii
import secrets
from typing import Any

from flask import Blueprint, jsonify, make_response, render_template, request, session
from flask_login import current_user, login_required

from app.models import PasswordVault, PasswordVaultItem, db


password_tool_bp = Blueprint("password_tool", __name__, url_prefix="/tools/password_tool")

DEFAULT_KDF_ITERATIONS = 600_000
MIN_KDF_ITERATIONS = 200_000
MAX_KDF_ITERATIONS = 2_000_000
MAX_BLOB_BYTES = 768 * 1024
MAX_ITEMS_PER_VAULT = 5000
CSRF_SESSION_KEY = "password_tool_csrf_token"
HTML_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "img-src 'self' data:; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


def _json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _serialize_vault(vault: PasswordVault | None) -> dict[str, Any] | None:
    if vault is None:
        return None
    return {
        "id": vault.id,
        "kdf_name": vault.kdf_name,
        "kdf_iterations": vault.kdf_iterations,
        "salt_b64": vault.salt_b64,
        "check_nonce_b64": vault.check_nonce_b64,
        "check_ciphertext_b64": vault.check_ciphertext_b64,
        "created_at": vault.created_at.isoformat() if vault.created_at else None,
        "updated_at": vault.updated_at.isoformat() if vault.updated_at else None,
    }


def _serialize_item(item: PasswordVaultItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "schema_version": item.schema_version,
        "nonce_b64": item.nonce_b64,
        "ciphertext_b64": item.ciphertext_b64,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _get_or_create_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
        session.modified = True
    return token


def _require_csrf() -> str | None:
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return "セキュリティトークンの検証に失敗しました。画面を再読み込みしてください。"
    return None


def _decode_b64(value: Any, *, field_name: str, min_bytes: int = 1, max_bytes: int = MAX_BLOB_BYTES) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} が空です。")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{field_name} の形式が不正です。") from exc
    if len(raw) < min_bytes:
        raise ValueError(f"{field_name} が短すぎます。")
    if len(raw) > max_bytes:
        raise ValueError(f"{field_name} が大きすぎます。")
    return value


def _require_json_dict() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("JSON データが不正です。")
    return payload


def _require_vault() -> PasswordVault | None:
    return PasswordVault.query.filter_by(user_id=current_user.id).first()


def _validate_vault_payload(payload: dict[str, Any]) -> dict[str, Any]:
    kdf_name = str(payload.get("kdf_name") or "").strip()
    if kdf_name != "PBKDF2-SHA-256":
        raise ValueError("現在対応している鍵導出方式は PBKDF2-SHA-256 のみです。")

    try:
        kdf_iterations = int(payload.get("kdf_iterations", DEFAULT_KDF_ITERATIONS))
    except (TypeError, ValueError) as exc:
        raise ValueError("鍵導出回数が不正です。") from exc
    if kdf_iterations < MIN_KDF_ITERATIONS or kdf_iterations > MAX_KDF_ITERATIONS:
        raise ValueError(
            f"鍵導出回数は {MIN_KDF_ITERATIONS:,} から {MAX_KDF_ITERATIONS:,} の範囲で指定してください。"
        )

    return {
        "kdf_name": kdf_name,
        "kdf_iterations": kdf_iterations,
        "salt_b64": _decode_b64(payload.get("salt_b64"), field_name="salt_b64", min_bytes=16, max_bytes=64),
        "check_nonce_b64": _decode_b64(
            payload.get("check_nonce_b64"),
            field_name="check_nonce_b64",
            min_bytes=12,
            max_bytes=32,
        ),
        "check_ciphertext_b64": _decode_b64(
            payload.get("check_ciphertext_b64"),
            field_name="check_ciphertext_b64",
            min_bytes=16,
        ),
    }


def _validate_item_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        schema_version = int(payload.get("schema_version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version が不正です。") from exc
    if schema_version < 1 or schema_version > 10:
        raise ValueError("schema_version が範囲外です。")

    return {
        "schema_version": schema_version,
        "nonce_b64": _decode_b64(payload.get("nonce_b64"), field_name="nonce_b64", min_bytes=12, max_bytes=32),
        "ciphertext_b64": _decode_b64(payload.get("ciphertext_b64"), field_name="ciphertext_b64", min_bytes=16),
    }


def _password_tool_payload(vault: PasswordVault | None) -> dict[str, Any]:
    items = vault.items if vault else []
    return {
        "ok": True,
        "csrf_token": _get_or_create_csrf_token(),
        "user": {
            "username": getattr(current_user, "username", ""),
            "display_name": getattr(current_user, "name", "") or getattr(current_user, "username", ""),
        },
        "vault": _serialize_vault(vault),
        "items": [_serialize_item(item) for item in items],
    }


@password_tool_bp.before_request
def _protect_mutating_requests():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if not request.path.startswith(f"{password_tool_bp.url_prefix}/api/"):
        return None
    error = _require_csrf()
    if error:
        return _json_error(error, 403)
    return None


@password_tool_bp.after_request
def _harden_password_tool_response(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = HTML_CSP
    return response


@password_tool_bp.route("/", methods=["GET"])
@login_required
def password_home():
    _get_or_create_csrf_token()
    return make_response(render_template("password_tool.html"))


@password_tool_bp.route("/api/vault", methods=["GET"])
@login_required
def api_vault_state():
    return jsonify(_password_tool_payload(_require_vault()))


@password_tool_bp.route("/api/vault", methods=["POST"])
@login_required
def api_create_vault():
    if _require_vault() is not None:
        return _json_error("このユーザーの vault はすでに作成されています。", 409)

    try:
        payload = _validate_vault_payload(_require_json_dict())
    except ValueError as exc:
        return _json_error(str(exc), 400)

    vault = PasswordVault(user_id=current_user.id, **payload)
    db.session.add(vault)
    db.session.commit()
    return jsonify(_password_tool_payload(vault)), 201


@password_tool_bp.route("/api/vault/items", methods=["POST"])
@login_required
def api_create_item():
    vault = _require_vault()
    if vault is None:
        return _json_error("先に vault を作成してください。", 404)
    if len(vault.items) >= MAX_ITEMS_PER_VAULT:
        return _json_error(f"保存上限 {MAX_ITEMS_PER_VAULT} 件に達しています。", 400)

    try:
        payload = _validate_item_payload(_require_json_dict())
    except ValueError as exc:
        return _json_error(str(exc), 400)

    item = PasswordVaultItem(vault_id=vault.id, **payload)
    db.session.add(item)
    db.session.commit()
    return jsonify({"ok": True, "item": _serialize_item(item)}), 201


@password_tool_bp.route("/api/vault/items/<int:item_id>", methods=["PUT"])
@login_required
def api_update_item(item_id: int):
    vault = _require_vault()
    if vault is None:
        return _json_error("vault が見つかりません。", 404)

    item = PasswordVaultItem.query.filter_by(id=item_id, vault_id=vault.id).first()
    if item is None:
        return _json_error("対象の項目が見つかりません。", 404)

    try:
        payload = _validate_item_payload(_require_json_dict())
    except ValueError as exc:
        return _json_error(str(exc), 400)

    item.schema_version = payload["schema_version"]
    item.nonce_b64 = payload["nonce_b64"]
    item.ciphertext_b64 = payload["ciphertext_b64"]
    db.session.commit()
    return jsonify({"ok": True, "item": _serialize_item(item)})


@password_tool_bp.route("/api/vault/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_item(item_id: int):
    vault = _require_vault()
    if vault is None:
        return _json_error("vault が見つかりません。", 404)

    item = PasswordVaultItem.query.filter_by(id=item_id, vault_id=vault.id).first()
    if item is None:
        return _json_error("対象の項目が見つかりません。", 404)

    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True, "deleted_id": item_id})


@password_tool_bp.route("/api/vault/rotate", methods=["POST"])
@login_required
def api_rotate_vault():
    vault = _require_vault()
    if vault is None:
        return _json_error("vault が見つかりません。", 404)

    try:
        payload = _require_json_dict()
        vault_update = _validate_vault_payload(payload)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("items は配列で指定してください。")
        if len(raw_items) != len(vault.items):
            raise ValueError("マスターパスワード変更時は全項目を再暗号化してください。")
    except ValueError as exc:
        return _json_error(str(exc), 400)

    existing_items = {item.id: item for item in vault.items}
    submitted_ids: set[int] = set()
    validated_items: list[tuple[PasswordVaultItem, dict[str, Any]]] = []

    try:
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise ValueError("items 内のデータ形式が不正です。")
            try:
                item_id = int(raw_item.get("id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("items 内の id が不正です。") from exc
            if item_id in submitted_ids:
                raise ValueError("同じ項目が重複して送信されました。")
            if item_id not in existing_items:
                raise ValueError("存在しない項目が含まれています。")
            submitted_ids.add(item_id)
            validated_items.append((existing_items[item_id], _validate_item_payload(raw_item)))
    except ValueError as exc:
        return _json_error(str(exc), 400)

    if submitted_ids != set(existing_items):
        return _json_error("全項目の再暗号化データが揃っていません。", 400)

    vault.kdf_name = vault_update["kdf_name"]
    vault.kdf_iterations = vault_update["kdf_iterations"]
    vault.salt_b64 = vault_update["salt_b64"]
    vault.check_nonce_b64 = vault_update["check_nonce_b64"]
    vault.check_ciphertext_b64 = vault_update["check_ciphertext_b64"]

    for item, validated in validated_items:
        item.schema_version = validated["schema_version"]
        item.nonce_b64 = validated["nonce_b64"]
        item.ciphertext_b64 = validated["ciphertext_b64"]

    db.session.commit()
    return jsonify(_password_tool_payload(vault))
