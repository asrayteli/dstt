from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import io
import zipfile
from types import SimpleNamespace

from flask import Flask


def _point_share_store(monkeypatch, tmp_path):
    from app.tools import share

    monkeypatch.setattr(share, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(share, "META_PATH", str(tmp_path / "meta.json"))
    monkeypatch.setattr(share, "_CLEANUP_LOCK_PATH", str(tmp_path / ".cleanup.lock"))
    return share


def _share_client(share, monkeypatch, username="tester01"):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["LOGIN_DISABLED"] = True
    app.register_blueprint(share.share_bp)
    monkeypatch.setattr(
        share,
        "current_user",
        SimpleNamespace(is_authenticated=True, username=username, name="Test User"),
    )
    return app.test_client()


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


def test_chunk_upload_combines_files_and_uses_custom_zip_name(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    monkeypatch.setattr(share, "CHUNK_SIZE", 5)
    monkeypatch.setattr(share, "MAX_CHUNK_SIZE", 1024)
    monkeypatch.setattr(share, "_make_qr_data", lambda url: "qr")
    client = _share_client(share, monkeypatch)

    first = b"hello-world"
    second = b"abc123"
    init = client.post(
        "/tools/share/api/chunk/init",
        json={
            "title": "A社提出資料",
            "memo": "確認お願いします",
            "zip_name": "bundle.zip",
            "expires_mode": "3d",
            "files": [
                {"name": "first.txt", "size": len(first), "type": "text/plain"},
                {"name": "second.txt", "size": len(second), "type": "text/plain"},
            ],
        },
    )
    assert init.status_code == 200
    upload_id = init.get_json()["upload_id"]

    for file_index, content in enumerate([first, second]):
        chunks = [content[i : i + share.CHUNK_SIZE] for i in range(0, len(content), share.CHUNK_SIZE)]
        for chunk_index, chunk in enumerate(chunks):
            response = client.post(
                "/tools/share/api/chunk/upload",
                data={
                    "upload_id": upload_id,
                    "file_index": str(file_index),
                    "chunk_index": str(chunk_index),
                    "chunk": (io.BytesIO(chunk), f"{file_index}-{chunk_index}.part"),
                },
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            assert response.get_json()["success"] is True

    complete = client.post("/tools/share/api/chunk/complete", json={"upload_id": upload_id})
    assert complete.status_code == 200
    payload = complete.get_json()
    assert payload["success"] is True

    meta = share.load_meta()
    assert list(meta.keys()) == [payload["uid"]]
    record = meta[payload["uid"]]
    assert record["title"] == "A社提出資料"
    assert record["zip_name"] == "bundle.zip"
    assert record["memo"] == "確認お願いします"
    assert record["expires_mode"] == "3d"
    assert len(record["files"]) == 2

    stored_first = tmp_path / record["files"][0]["stored_name"]
    stored_second = tmp_path / record["files"][1]["stored_name"]
    assert stored_first.read_bytes() == first
    assert stored_second.read_bytes() == second
    assert not (tmp_path / ".chunks" / upload_id).exists()

    download = client.get(f"/tools/share/download/{payload['uid']}")
    assert download.status_code == 200
    assert "bundle.zip" in download.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
        assert archive.read("first.txt") == first
        assert archive.read("second.txt") == second


def test_chunk_complete_keeps_session_when_chunk_is_missing(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    monkeypatch.setattr(share, "CHUNK_SIZE", 4)
    monkeypatch.setattr(share, "MAX_CHUNK_SIZE", 1024)
    client = _share_client(share, monkeypatch)

    content = b"missing-last"
    init = client.post(
        "/tools/share/api/chunk/init",
        json={"files": [{"name": "missing.txt", "size": len(content), "type": "text/plain"}]},
    )
    upload_id = init.get_json()["upload_id"]
    response = client.post(
        "/tools/share/api/chunk/upload",
        data={
            "upload_id": upload_id,
            "file_index": "0",
            "chunk_index": "0",
            "chunk": (io.BytesIO(content[:4]), "0.part"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    complete = client.post("/tools/share/api/chunk/complete", json={"upload_id": upload_id})
    assert complete.status_code == 400
    assert "未送信チャンク" in complete.get_json()["error"]
    assert (tmp_path / ".chunks" / upload_id / "session.json").exists()
    assert share.load_meta() == {}


def test_chunk_upload_rejects_chunks_over_50mb_limit(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    monkeypatch.setattr(share, "CHUNK_SIZE", 4)
    monkeypatch.setattr(share, "MAX_CHUNK_SIZE", 1024)
    client = _share_client(share, monkeypatch)

    init = client.post(
        "/tools/share/api/chunk/init",
        json={"files": [{"name": "large.txt", "size": 5, "type": "text/plain"}]},
    )
    upload_id = init.get_json()["upload_id"]
    response = client.post(
        "/tools/share/api/chunk/upload",
        data={
            "upload_id": upload_id,
            "file_index": "0",
            "chunk_index": "0",
            "chunk": (io.BytesIO(b"12345"), "too-large.part"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert "50MB" in response.get_json()["error"]


def test_list_and_preview_support_images_and_first_pdf_page(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    client = _share_client(share, monkeypatch, username="owner01")

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
        b"\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    image_path = tmp_path / "image.png"
    image_path.write_bytes(png_bytes)

    import fitz

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page(width=120, height=80)
    doc.new_page(width=240, height=160)
    doc.save(pdf_path)
    doc.close()

    share.save_meta(
        {
            "abc123": {
                "display_id": "ABCDE",
                "title": "Preview files",
                "uploader": "owner01",
                "password_hash": None,
                "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "created_at": datetime.utcnow().isoformat(),
                "files": [
                    {
                        "original_name": "image.png",
                        "stored_name": image_path.name,
                        "size": len(png_bytes),
                        "mime_type": "image/png",
                    },
                    {
                        "original_name": "doc.pdf",
                        "stored_name": pdf_path.name,
                        "size": pdf_path.stat().st_size,
                        "mime_type": "application/pdf",
                    },
                ],
            }
        }
    )

    listed = client.get("/tools/share/api/list")
    assert listed.status_code == 200
    files = listed.get_json()[0]["files"]
    assert files[0]["preview_kind"] == "image"
    assert files[0]["preview_url"] == "/tools/share/preview/abc123/0"
    assert files[1]["preview_kind"] == "pdf"
    assert files[1]["preview_url"] == "/tools/share/preview/abc123/1"

    image_preview = client.get("/tools/share/preview/abc123/0")
    assert image_preview.status_code == 200
    assert image_preview.mimetype == "image/png"
    assert image_preview.data == png_bytes

    pdf_preview = client.get("/tools/share/preview/abc123/1")
    assert pdf_preview.status_code == 200
    assert pdf_preview.mimetype == "image/png"
    assert pdf_preview.data.startswith(b"\x89PNG")


def test_password_protected_preview_is_hidden_until_verified(tmp_path, monkeypatch):
    share = _point_share_store(monkeypatch, tmp_path)
    client = _share_client(share, monkeypatch, username="viewer01")
    image_path = tmp_path / "secret.png"
    image_path.write_bytes(b"not-a-real-image-but-served-as-preview")
    password_hash = hashlib.sha256("secret".encode()).hexdigest()
    share.save_meta(
        {
            "secret1": {
                "display_id": "LOCK1",
                "uploader": "owner01",
                "password_hash": password_hash,
                "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                "created_at": datetime.utcnow().isoformat(),
                "files": [
                    {
                        "original_name": "secret.png",
                        "stored_name": image_path.name,
                        "size": image_path.stat().st_size,
                        "mime_type": "image/png",
                    }
                ],
            }
        }
    )

    listed = client.get("/tools/share/api/list").get_json()
    assert listed[0]["files"][0]["preview_url"] == ""
    blocked = client.get("/tools/share/preview/secret1/0")
    assert blocked.status_code == 403

    verified = client.post("/tools/share/api/verify_password/secret1", json={"password": "secret"})
    assert verified.status_code == 200
    allowed = client.get("/tools/share/preview/secret1/0")
    assert allowed.status_code == 200
