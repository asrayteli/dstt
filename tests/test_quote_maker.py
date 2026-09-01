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


def _create_user(app_ctx, username="alice", name="Alice", is_admin=False):
    from app.models import User, db

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name=name, is_admin=is_admin)
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


# --- 基本テンプレート（会社共通・管理者のみ管理） -------------------------------

def test_admin_can_create_list_and_delete_base_template(app_ctx):
    admin = _create_user(app_ctx, "boss", "Boss", is_admin=True)
    client = app_ctx.test_client()
    _login(client, admin)

    resp = client.post(
        "/tools/quote_maker/api/templates",
        json={"name": "標準の運行見積", "document": _sample_document()},
    )
    assert resp.status_code == 201
    tid = resp.get_json()["id"]

    resp = client.get("/tools/quote_maker/api/templates")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["can_manage"] is True
    assert len(body["templates"]) == 1
    assert body["templates"][0]["name"] == "標準の運行見積"

    # 全ユーザーが本文を取得できる
    resp = client.get(f"/tools/quote_maker/api/templates/{tid}")
    assert resp.status_code == 200
    assert resp.get_json()["document"]["page"]["marginTop"] == 15

    resp = client.delete(f"/tools/quote_maker/api/templates/{tid}")
    assert resp.status_code == 200
    assert client.get("/tools/quote_maker/api/templates").get_json()["templates"] == []


def test_non_admin_can_view_but_not_manage_base_templates(app_ctx):
    admin = _create_user(app_ctx, "boss", "Boss", is_admin=True)
    staff = _create_user(app_ctx, "staff", "Staff", is_admin=False)

    admin_client = app_ctx.test_client()
    _login(admin_client, admin)
    tid = admin_client.post(
        "/tools/quote_maker/api/templates",
        json={"name": "会社標準", "document": _sample_document()},
    ).get_json()["id"]

    staff_client = app_ctx.test_client()
    _login(staff_client, staff)

    # 閲覧・利用は可能
    listing = staff_client.get("/tools/quote_maker/api/templates").get_json()
    assert listing["can_manage"] is False
    assert len(listing["templates"]) == 1
    assert staff_client.get(f"/tools/quote_maker/api/templates/{tid}").status_code == 200

    # 追加・更新・削除は 403
    assert staff_client.post(
        "/tools/quote_maker/api/templates",
        json={"name": "x", "document": {"blocks": []}},
    ).status_code == 403
    assert staff_client.put(
        f"/tools/quote_maker/api/templates/{tid}", json={"name": "x"}
    ).status_code == 403
    assert staff_client.delete(f"/tools/quote_maker/api/templates/{tid}").status_code == 403

    # 管理者が作ったテンプレートは残っている
    assert len(admin_client.get("/tools/quote_maker/api/templates").get_json()["templates"]) == 1


# --- 段組みの入れ子・行列形式の項目表 ------------------------------------------

def _nested_document():
    """2段組の左に明細表、右に差出人テキストを入れ、行列形式の項目表を持つ見積書。"""
    return {
        "page": {"paper": "A4"},
        "blocks": [
            {
                "id": "c1",
                "type": "columns",
                "style": {},
                "content": {
                    "gap": 16,
                    "cols": [
                        {
                            "width": 60,
                            "align": "left",
                            "blocks": [
                                {
                                    "id": "n1",
                                    "type": "items",
                                    "style": {},
                                    "content": {"rows": [{"name": "貸切バス", "qty": "2", "price": "50000"}]},
                                }
                            ],
                        },
                        {
                            "width": 40,
                            "align": "right",
                            "blocks": [
                                {"id": "n2", "type": "text", "style": {}, "content": {"html": "大進道路<br>TEL 000"}}
                            ],
                        },
                    ],
                },
            },
            {
                "id": "t1",
                "type": "table",
                "style": {},
                "content": {
                    "columns": [{"width": 30}, {"width": 40}, {"width": 30}],
                    "rows": [
                        {"cells": [
                            {"html": "見出し", "cs": 3, "rs": 1, "shade": True},
                            {"html": "", "cs": 1, "rs": 1, "covered": True},
                            {"html": "", "cs": 1, "rs": 1, "covered": True},
                        ]},
                        {"cells": [
                            {"html": "契約期間", "cs": 1, "rs": 2},
                            {"html": "7月", "cs": 1, "rs": 1},
                            {"html": "備考", "cs": 1, "rs": 1},
                        ]},
                        {"cells": [
                            {"html": "", "cs": 1, "rs": 1, "covered": True},
                            {"html": "8月", "cs": 1, "rs": 1},
                            {"html": "", "cs": 1, "rs": 1},
                        ]},
                    ],
                },
            },
        ],
    }


def test_nested_column_blocks_and_merged_table_round_trip(app_ctx):
    """段組みの列に入れたブロックと、結合したセルがそのまま保存・復元される。"""
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    created = client.post(
        "/tools/quote_maker/api/quotes",
        json={"title": "段組み見積", "document": _nested_document()},
    )
    assert created.status_code == 201
    qid = created.get_json()["id"]

    doc = client.get(f"/tools/quote_maker/api/quotes/{qid}").get_json()["document"]
    cols = doc["blocks"][0]["content"]["cols"]
    assert cols[0]["blocks"][0]["type"] == "items"
    assert cols[1]["blocks"][0]["content"]["html"] == "大進道路<br>TEL 000"

    table = doc["blocks"][1]["content"]
    assert len(table["columns"]) == 3
    assert table["rows"][0]["cells"][0]["cs"] == 3          # 横方向の結合
    assert table["rows"][1]["cells"][0]["rs"] == 2          # 縦方向の結合
    assert table["rows"][0]["cells"][1]["covered"] is True  # 結合で隠れたセル

    # 複製しても入れ子の中身ごとコピーされる
    clone = client.post(f"/tools/quote_maker/api/quotes/{qid}/duplicate").get_json()
    clone_cols = clone["document"]["blocks"][0]["content"]["cols"]
    assert clone_cols[0]["blocks"][0]["type"] == "items"


def test_nested_document_survives_update(app_ctx):
    """列の中のブロックを増やす更新も保存できる。"""
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    qid = client.post(
        "/tools/quote_maker/api/quotes",
        json={"title": "段組み見積", "document": _nested_document()},
    ).get_json()["id"]

    doc = _nested_document()
    doc["blocks"][0]["content"]["cols"][1]["blocks"].append(
        {"id": "n3", "type": "table", "style": {}, "content": {
            "columns": [{"width": 100}], "rows": [{"cells": [{"html": "追加", "cs": 1, "rs": 1}]}]}}
    )
    resp = client.put(f"/tools/quote_maker/api/quotes/{qid}", json={"document": doc})
    assert resp.status_code == 200
    saved = resp.get_json()["document"]["blocks"][0]["content"]["cols"][1]["blocks"]
    assert [b["type"] for b in saved] == ["text", "table"]


def test_index_page_exposes_new_editor_features(app_ctx):
    """画面に列追加・セル結合・自由項目の入口が含まれている。"""
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)
    html = client.get("/tools/quote_maker/").get_data(as_text=True)

    # 純粋ロジック（Node で単体テストしている部分）が読み込まれている
    assert "QM_CORE_BEGIN" in html and "window.QM_CORE" in html
    # 段組みの列へブロックを入れる導線
    assert 'data-act' in html and "col-add" in html
    assert "qm-colblocks" in html
    # 項目表の行・列・結合の操作
    for act in ("tbl-addrow", "tbl-addcol", "tbl-merge", "tbl-split", "tbl-delrow", "tbl-delcol"):
        assert act in html, act
    # 差出人の自由項目
    assert "defaultIssuerFields" in html and "qm-if-row" in html


def test_base_template_rejects_invalid_document(app_ctx):
    admin = _create_user(app_ctx, "boss", "Boss", is_admin=True)
    client = app_ctx.test_client()
    _login(client, admin)
    assert client.post(
        "/tools/quote_maker/api/templates",
        json={"name": "bad", "document": {"blocks": "notalist"}},
    ).status_code == 400
