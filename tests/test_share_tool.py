from __future__ import annotations

from datetime import datetime, timedelta
import hashlib

from flask import Flask


def _point_share_store(monkeypatch, tmp_path):
    from app.tools import share

    monkeypatch.setattr(share, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(share, "META_PATH", str(tmp_path / "meta.json"))
    monkeypatch.setattr(share, "_CLEANUP_LOCK_PATH", str(tmp_path / ".cleanup.lock"))
    return share


def test_share_cleanup_keeps_fresh_metadata_when_removing_expired_files(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    expired_file = tmp_path / "expired.txt"
    fresh_file = tmp_path / "fresh.txt"
    expired_file.write_text("old", encoding="utf-8")
    fresh_file.write_text("new", encoding="utf-8")

    share.save_meta(
        {
            "expired": {
                "expires_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
                "files": [{"stored_name": expired_file.name, "original_name": "expired.txt"}],
            },
            "fresh": {
                "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "files": [{"stored_name": fresh_file.name, "original_name": "fresh.txt"}],
            },
        }
    )

    share.cleanup_expired_files()

    meta = share.load_meta()
    assert "expired" not in meta
    assert "fresh" in meta
    assert not expired_file.exists()
    assert fresh_file.exists()


def test_share_password_verify_issues_token_without_debug_output(tmp_path, monkeypatch, capsys):
    share = _point_share_store(monkeypatch, tmp_path)
    password_hash = hashlib.sha256("secret".encode()).hexdigest()
    share.save_meta(
        {
            "abc123": {
                "password_hash": password_hash,
                "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "files": [],
            }
        }
    )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(share.share_bp)

    response = app.test_client().post(
        "/tools/share/api/verify_password/abc123",
        json={"password": "secret"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["token"]
    assert "[DEBUG]" not in capsys.readouterr().out
