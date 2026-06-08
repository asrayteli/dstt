"""リネームツールの正常系・上限・一時ファイル後始末を検証する。"""

import glob
import io
import tempfile
import zipfile

import pytest

from app import create_app
import app.tools.rename as rename_mod


@pytest.fixture()
def client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "LOGIN_DISABLED": True,
    })
    return app.test_client()


def _upload(client, files, **form):
    data = {"mode": form.pop("mode", "sequential")}
    data.update(form)
    data["files"] = files
    return client.post("/tools/rename/", data=data, content_type="multipart/form-data")


def test_sequential_rename_returns_zip_with_renamed_entries(client):
    files = [
        (io.BytesIO(b"a"), "first.txt"),
        (io.BytesIO(b"bb"), "second.txt"),
    ]
    resp = _upload(client, files, mode="sequential", prefix="doc_", zero_pad="2", start_num="1")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert sorted(zf.namelist()) == ["doc_01.txt", "doc_02.txt"]


def test_empty_upload_is_rejected(client):
    resp = client.post("/tools/rename/", data={"mode": "sequential"},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_too_many_files_rejected(client):
    files = [(io.BytesIO(b"x"), f"f{i}.txt") for i in range(rename_mod.MAX_RENAME_FILES + 1)]
    resp = _upload(client, files, mode="sequential")
    # アプリの上限(400)または Werkzeug のフォームパート上限(413)で拒否されること。
    assert resp.status_code in (400, 413)


def test_no_temp_directory_left_on_disk(client):
    # メモリ上で ZIP を生成するため、処理後にディスク上の一時ディレクトリが
    # 増えていないこと（リークしないこと）を確認する。
    import glob
    import os
    pattern = os.path.join(tempfile.gettempdir(), "tmp*")
    before = set(glob.glob(pattern))
    files = [(io.BytesIO(b"hello"), "a.txt"), (io.BytesIO(b"world"), "b.txt")]
    resp = _upload(client, files, mode="sequential")
    assert resp.status_code == 200
    resp.close()
    leaked_dirs = [p for p in (set(glob.glob(pattern)) - before) if os.path.isdir(p)]
    assert leaked_dirs == [], f"leaked temp dirs: {leaked_dirs}"
