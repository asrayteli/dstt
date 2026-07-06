from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "quote_maker.db"
    monkeypatch.chdir(tmp_path)

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

    # app_context を保持したままにすると全クライアント要求が同一 g を共有し、
    # Flask-Login が最初のユーザーをキャッシュして権限分離テストが誤検知する。
    # 本番同様に「1リクエスト=1コンテキスト」とするため context の外で yield する。
    yield app


def _create_user(app_ctx, username="alice", name="Alice"):
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name=name)
        db.session.add(user)
        db.session.commit()
        return user.username


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _sample_document():
    return {
        "page": {"paper": "A4", "marginTop": 15},
        "blocks": [
            {"id": "b1", "type": "text", "style": {"align": "center"}, "content": {"html": "お見積書"}},
            {"id": "b2", "type": "table", "style": {}, "content": {"rows": [{"label": "契約期間", "value": "7月"}]}},
        ],
    }


def test_requires_login(app_ctx):
    client = app_ctx.test_client()
    # 未ログインは一覧APIにアクセスできない（ログインへリダイレクト）
    resp = client.get("/tools/quote_maker/api/quotes")
    assert resp.status_code in (301, 302, 401)


def test_create_list_get_update_delete_cycle(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    # create
    resp = client.post(
        "/tools/quote_maker/api/quotes",
        json={"title": "株式会社テスト 御中", "document": _sample_document()},
    )
    assert resp.status_code == 201
    created = resp.get_json()
    qid = created["id"]
    assert created["title"] == "株式会社テスト 御中"
    assert len(created["document"]["blocks"]) == 2

    # list
    resp = client.get("/tools/quote_maker/api/quotes")
    assert resp.status_code == 200
    quotes = resp.get_json()["quotes"]
    assert len(quotes) == 1
    assert quotes[0]["id"] == qid
    assert "document" not in quotes[0]  # 一覧は要約のみ

    # get detail
    resp = client.get(f"/tools/quote_maker/api/quotes/{qid}")
    assert resp.status_code == 200
    assert resp.get_json()["document"]["page"]["marginTop"] == 15

    # update
    new_doc = _sample_document()
    new_doc["blocks"].append({"id": "b3", "type": "box", "style": {}, "content": {"title": "【特記】"}})
    resp = client.put(
        f"/tools/quote_maker/api/quotes/{qid}",
        json={"title": "更新後タイトル", "document": new_doc},
    )
    assert resp.status_code == 200
    updated = resp.get_json()
    assert updated["title"] == "更新後タイトル"
    assert len(updated["document"]["blocks"]) == 3

    # delete
    resp = client.delete(f"/tools/quote_maker/api/quotes/{qid}")
    assert resp.status_code == 200
    resp = client.get("/tools/quote_maker/api/quotes")
    assert resp.get_json()["quotes"] == []


def test_duplicate_creates_independent_copy(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    qid = client.post(
        "/tools/quote_maker/api/quotes",
        json={"title": "原本", "document": _sample_document()},
    ).get_json()["id"]

    resp = client.post(f"/tools/quote_maker/api/quotes/{qid}/duplicate")
    assert resp.status_code == 201
    clone = resp.get_json()
    assert clone["id"] != qid
    assert clone["title"] == "原本（コピー）"

    # 複製後にコピー側を編集しても原本は不変
    edited = _sample_document()
    edited["blocks"] = []
    client.put(f"/tools/quote_maker/api/quotes/{clone['id']}", json={"document": edited})
    original = client.get(f"/tools/quote_maker/api/quotes/{qid}").get_json()
    assert len(original["document"]["blocks"]) == 2


def test_owner_isolation(app_ctx):
    alice = _create_user(app_ctx, "alice", "Alice")
    bob = _create_user(app_ctx, "bob", "Bob")

    alice_client = app_ctx.test_client()
    _login(alice_client, alice)
    qid = alice_client.post(
        "/tools/quote_maker/api/quotes",
        json={"title": "アリスの見積", "document": _sample_document()},
    ).get_json()["id"]

    bob_client = app_ctx.test_client()
    _login(bob_client, bob)
    # 他人の見積は取得・更新・削除・複製いずれも 404
    assert bob_client.get(f"/tools/quote_maker/api/quotes/{qid}").status_code == 404
    assert bob_client.put(f"/tools/quote_maker/api/quotes/{qid}", json={"title": "x"}).status_code == 404
    assert bob_client.post(f"/tools/quote_maker/api/quotes/{qid}/duplicate").status_code == 404
    assert bob_client.delete(f"/tools/quote_maker/api/quotes/{qid}").status_code == 404
    # ボブの一覧にアリスの見積は出ない
    assert bob_client.get("/tools/quote_maker/api/quotes").get_json()["quotes"] == []


def test_invalid_document_rejected(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    # document がオブジェクトでない
    assert client.post("/tools/quote_maker/api/quotes", json={"document": "bad"}).status_code == 400
    # blocks が配列でない
    assert client.post(
        "/tools/quote_maker/api/quotes", json={"document": {"blocks": {}}}
    ).status_code == 400


def test_oversized_document_rejected(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    huge = {"blocks": [{"id": "b", "type": "text", "content": {"html": "あ" * 7_000_000}}]}
    resp = client.post("/tools/quote_maker/api/quotes", json={"title": "big", "document": huge})
    assert resp.status_code == 400


def test_empty_title_defaults(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    resp = client.post("/tools/quote_maker/api/quotes", json={"title": "  ", "document": {"blocks": []}})
    assert resp.status_code == 201
    assert resp.get_json()["title"] == "無題の見積書"


def test_index_page_renders(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)
    resp = client.get("/tools/quote_maker/")
    assert resp.status_code == 200
    assert "見積書" in resp.get_data(as_text=True)
