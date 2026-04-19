from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "vault.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret",
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _create_user(app_ctx, username="alice"):
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name="Alice")
        db.session.add(user)
        db.session.commit()
        return user.username


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _csrf(client):
    response = client.get("/tools/password_tool/api/vault")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["csrf_token"]


def test_password_tool_page_uses_hardened_headers(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    response = client.get("/tools/password_tool/")

    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert b"unpkg.com" not in response.data
    assert b"cdn.tailwindcss.com" not in response.data


def test_password_vault_create_and_store_encrypted_items(app_ctx):
    from app.models import PasswordVault, PasswordVaultItem

    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)
    csrf_token = _csrf(client)

    create_response = client.post(
        "/tools/password_tool/api/vault",
        json={
            "kdf_name": "PBKDF2-SHA-256",
            "kdf_iterations": 600000,
            "salt_b64": _b64(b"s" * 16),
            "check_nonce_b64": _b64(b"n" * 12),
            "check_ciphertext_b64": _b64(b"cipher-check" * 4),
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert create_response.status_code == 201

    item_response = client.post(
        "/tools/password_tool/api/vault/items",
        json={
            "schema_version": 1,
            "nonce_b64": _b64(b"i" * 12),
            "ciphertext_b64": _b64(b"\x01\x02encrypted-entry\x03\x04"),
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert item_response.status_code == 201

    state_response = client.get("/tools/password_tool/api/vault")
    payload = state_response.get_json()
    assert payload["vault"]["kdf_iterations"] == 600000
    assert len(payload["items"]) == 1
    assert payload["items"][0]["ciphertext_b64"] == _b64(b"\x01\x02encrypted-entry\x03\x04")

    html_response = client.get("/tools/password_tool/")
    assert b"encrypted-entry" not in html_response.data

    with app_ctx.app_context():
        vault = PasswordVault.query.one()
        item = PasswordVaultItem.query.one()
        assert vault.salt_b64 == _b64(b"s" * 16)
        assert item.ciphertext_b64 == _b64(b"\x01\x02encrypted-entry\x03\x04")


def test_password_vault_requires_csrf_for_mutations(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    response = client.post(
        "/tools/password_tool/api/vault",
        json={
            "kdf_name": "PBKDF2-SHA-256",
            "kdf_iterations": 600000,
            "salt_b64": _b64(b"s" * 16),
            "check_nonce_b64": _b64(b"n" * 12),
            "check_ciphertext_b64": _b64(b"cipher-check" * 4),
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"]


def test_password_vault_rotation_reencrypts_all_items(app_ctx):
    from app.models import PasswordVaultItem

    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)
    csrf_token = _csrf(client)

    client.post(
        "/tools/password_tool/api/vault",
        json={
            "kdf_name": "PBKDF2-SHA-256",
            "kdf_iterations": 600000,
            "salt_b64": _b64(b"a" * 16),
            "check_nonce_b64": _b64(b"b" * 12),
            "check_ciphertext_b64": _b64(b"c" * 24),
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    first = client.post(
        "/tools/password_tool/api/vault/items",
        json={
            "schema_version": 1,
            "nonce_b64": _b64(b"d" * 12),
            "ciphertext_b64": _b64(b"entry-one-ciphertext"),
        },
        headers={"X-CSRF-Token": csrf_token},
    ).get_json()["item"]
    second = client.post(
        "/tools/password_tool/api/vault/items",
        json={
            "schema_version": 1,
            "nonce_b64": _b64(b"e" * 12),
            "ciphertext_b64": _b64(b"entry-two-ciphertext"),
        },
        headers={"X-CSRF-Token": csrf_token},
    ).get_json()["item"]

    partial_rotate = client.post(
        "/tools/password_tool/api/vault/rotate",
        json={
            "kdf_name": "PBKDF2-SHA-256",
            "kdf_iterations": 700000,
            "salt_b64": _b64(b"z" * 16),
            "check_nonce_b64": _b64(b"y" * 12),
            "check_ciphertext_b64": _b64(b"x" * 24),
            "items": [
                {
                    "id": first["id"],
                    "schema_version": 1,
                    "nonce_b64": _b64(b"r" * 12),
                    "ciphertext_b64": _b64(b"rotated-one-ciphertext"),
                }
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert partial_rotate.status_code == 400

    rotate_response = client.post(
        "/tools/password_tool/api/vault/rotate",
        json={
            "kdf_name": "PBKDF2-SHA-256",
            "kdf_iterations": 700000,
            "salt_b64": _b64(b"z" * 16),
            "check_nonce_b64": _b64(b"y" * 12),
            "check_ciphertext_b64": _b64(b"x" * 24),
            "items": [
                {
                    "id": first["id"],
                    "schema_version": 1,
                    "nonce_b64": _b64(b"r" * 12),
                    "ciphertext_b64": _b64(b"rotated-one-ciphertext"),
                },
                {
                    "id": second["id"],
                    "schema_version": 1,
                    "nonce_b64": _b64(b"s" * 12),
                    "ciphertext_b64": _b64(b"rotated-two-ciphertext"),
                },
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )

    assert rotate_response.status_code == 200
    payload = rotate_response.get_json()
    assert payload["vault"]["kdf_iterations"] == 700000

    with app_ctx.app_context():
        stored_items = PasswordVaultItem.query.order_by(PasswordVaultItem.id.asc()).all()
        assert stored_items[0].ciphertext_b64 == _b64(b"rotated-one-ciphertext")
        assert stored_items[1].ciphertext_b64 == _b64(b"rotated-two-ciphertext")
