from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "powervote.db"
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


def test_powervote_creator_can_create_publish_and_collect_response(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    create_response = client.post("/tools/powervote/api/forms", json={"title": "参加確認"})
    assert create_response.status_code == 201
    form = create_response.get_json()["form"]

    form["status"] = "open"
    form["identity_mode"] = "required"
    form["is_anonymous"] = False
    form["questions"] = [
        {
            "id": None,
            "type": "single_choice",
            "title": "参加できますか？",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [{"id": "yes", "label": "参加する"}, {"id": "no", "label": "参加しない"}],
            "settings": {},
        },
        {
            "id": None,
            "type": "consent",
            "title": "内容に同意します",
            "description": "",
            "required": True,
            "sort_order": 1,
            "options": [],
            "settings": {},
        },
    ]
    update_response = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)
    assert update_response.status_code == 200
    form = update_response.get_json()["form"]

    public_page = client.get(f"/vote/{form['public_token']}")
    assert public_page.status_code == 200
    assert "PowerVote" in public_page.get_data(as_text=True)

    answers = {str(form["questions"][0]["id"]): "yes", str(form["questions"][1]["id"]): True}
    submit_response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"respondent": {"name": "山田"}, "answers": answers},
    )
    assert submit_response.status_code == 201
    assert submit_response.headers.get("Set-Cookie")

    duplicate_response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"respondent": {"name": "山田"}, "answers": answers},
    )
    assert duplicate_response.status_code == 409

    results_response = client.get(f"/tools/powervote/api/forms/{form['id']}/results")
    assert results_response.status_code == 200
    results = results_response.get_json()
    assert len(results["responses"]) == 1
    assert results["responses"][0]["respondent_name"] == "山田"
    assert results["summary"][0]["counts"]["参加する"] == 1


def test_powervote_public_rejects_draft_form(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    create_response = client.post("/tools/powervote/api/forms", json={"title": "下書き"})
    form = create_response.get_json()["form"]

    submit_response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {}},
    )

    assert submit_response.status_code == 403


def test_powervote_accepts_many_questions_and_new_input_types(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    create_response = client.post("/tools/powervote/api/forms", json={"title": "自由フォーム"})
    form = create_response.get_json()["form"]
    form["questions"] = [
        {
            "id": None,
            "type": "email" if index == 0 else "time" if index == 1 else "url" if index == 2 else "short_text",
            "title": f"質問{index + 1}",
            "description": "",
            "required": False,
            "sort_order": index,
            "options": [],
            "settings": {},
        }
        for index in range(160)
    ]

    update_response = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)

    assert update_response.status_code == 200
    updated = update_response.get_json()["form"]
    assert len(updated["questions"]) == 160
    assert updated["questions"][0]["type"] == "email"
    assert updated["questions"][1]["type"] == "time"
    assert updated["questions"][2]["type"] == "url"


def test_powervote_new_form_starts_empty(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    create_response = client.post("/tools/powervote/api/forms", json={"title": "空のフォーム"})

    assert create_response.status_code == 201
    assert create_response.get_json()["form"]["questions"] == []


def test_powervote_branching_validates_only_reachable_required_questions(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "分岐"}).get_json()["form"]
    form["status"] = "open"
    form["questions"] = [
        {
            "id": None,
            "type": "single_choice",
            "title": "参加できますか？",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [{"id": "yes", "label": "参加"}, {"id": "no", "label": "不参加"}],
            "settings": {},
        },
        {
            "id": None,
            "type": "short_text",
            "title": "参加者名",
            "description": "",
            "required": True,
            "sort_order": 1,
            "options": [],
            "settings": {},
        },
    ]
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]
    first_id = str(form["questions"][0]["id"])
    second_id = str(form["questions"][1]["id"])
    form["questions"][0]["settings"] = {"branches": {"no": "__end__"}}
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]

    skipped = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {first_id: "no"}},
    )
    assert skipped.status_code == 201

    fresh_client = app_ctx.test_client()
    missing_reachable = fresh_client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {first_id: "yes", second_id: ""}},
    )
    assert missing_reachable.status_code == 400


def test_powervote_manual_flow_uses_explicit_next_links(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Manual flow"}).get_json()["form"]
    form["status"] = "open"
    form["settings"] = {"flow_mode": "manual", "flow_start": "flow-q1"}
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "First",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [],
            "settings": {"flow": {"key": "flow-q1"}, "default_next": "flow-q3"},
        },
        {
            "id": None,
            "type": "short_text",
            "title": "Skipped",
            "description": "",
            "required": True,
            "sort_order": 1,
            "options": [],
            "settings": {"flow": {"key": "flow-q2"}},
        },
        {
            "id": None,
            "type": "short_text",
            "title": "Third",
            "description": "",
            "required": True,
            "sort_order": 2,
            "options": [],
            "settings": {"flow": {"key": "flow-q3"}, "default_next": "__end__"},
        },
    ]
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]
    q1_id = str(form["questions"][0]["id"])
    q3_id = str(form["questions"][2]["id"])

    response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {q1_id: "hello", q3_id: "done"}},
    )

    assert response.status_code == 201


def test_powervote_flow_created_question_is_reachable_by_flow_key(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Flow created"}).get_json()["form"]
    form["status"] = "open"
    form["settings"] = {"flow_mode": "manual", "flow_start": "flow-created"}
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "Created in flow",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [],
            "settings": {"flow": {"key": "flow-created"}, "default_next": "__end__"},
        }
    ]

    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]
    question_id = str(form["questions"][0]["id"])

    submit_response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {question_id: "reflected"}},
    )

    assert submit_response.status_code == 201


def test_powervote_partition_does_not_require_answer(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Partition"}).get_json()["form"]
    form["status"] = "open"
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "First",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [],
            "settings": {},
        },
        {
            "id": None,
            "type": "partition",
            "title": "Next page",
            "description": "",
            "required": False,
            "sort_order": 1,
            "options": [],
            "settings": {},
        },
        {
            "id": None,
            "type": "short_text",
            "title": "Second",
            "description": "",
            "required": True,
            "sort_order": 2,
            "options": [],
            "settings": {},
        },
    ]
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]

    response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={
            "answers": {
                str(form["questions"][0]["id"]): "one",
                str(form["questions"][2]["id"]): "two",
            }
        },
    )

    assert response.status_code == 201


def test_powervote_api_coerces_string_booleans_and_bad_sort_order(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Coercion"}).get_json()["form"]
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "Optional",
            "description": "",
            "required": "false",
            "sort_order": "not-a-number",
            "options": [],
            "settings": {},
        }
    ]

    response = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)

    assert response.status_code == 200
    question = response.get_json()["form"]["questions"][0]
    assert question["required"] is False
    assert question["sort_order"] == 0


def test_powervote_bad_numeric_settings_do_not_crash_submission(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Bad settings"}).get_json()["form"]
    form["status"] = "open"
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "Text",
            "description": "",
            "required": False,
            "sort_order": 0,
            "options": [],
            "settings": {"max_length": "bad"},
        },
        {
            "id": None,
            "type": "number",
            "title": "Number",
            "description": "",
            "required": False,
            "sort_order": 1,
            "options": [],
            "settings": {"min": "bad", "max": "also-bad"},
        },
        {
            "id": None,
            "type": "rating",
            "title": "Rating",
            "description": "",
            "required": False,
            "sort_order": 2,
            "options": [],
            "settings": {"min": "bad", "max": "also-bad"},
        },
    ]
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]

    response = client.post(
        f"/vote/{form['public_token']}/submit",
        json={
            "answers": {
                str(form["questions"][0]["id"]): "ok",
                str(form["questions"][1]["id"]): "10",
                str(form["questions"][2]["id"]): "4",
            }
        },
    )

    assert response.status_code == 201


def test_powervote_text_and_number_limits_are_enforced(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Limits"}).get_json()["form"]
    form["status"] = "open"
    form["questions"] = [
        {
            "id": None,
            "type": "short_text",
            "title": "Code",
            "description": "",
            "required": True,
            "sort_order": 0,
            "options": [],
            "settings": {"min_length": 3, "max_length": 5},
        },
        {
            "id": None,
            "type": "number",
            "title": "Count",
            "description": "",
            "required": True,
            "sort_order": 1,
            "options": [],
            "settings": {"min": 1, "max": 10, "integer_only": True},
        },
    ]
    form = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form).get_json()["form"]
    text_id = str(form["questions"][0]["id"])
    number_id = str(form["questions"][1]["id"])

    too_short = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {text_id: "ab", number_id: "3"}},
    )
    assert too_short.status_code == 400

    decimal = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {text_id: "abcd", number_id: "3.5"}},
    )
    assert decimal.status_code == 400

    ok = client.post(
        f"/vote/{form['public_token']}/submit",
        json={"answers": {text_id: "abcd", number_id: "3"}},
    )
    assert ok.status_code == 201


def test_powervote_shared_editor_can_open_and_update_form(app_ctx):
    owner = _create_user(app_ctx, "owner", "Owner User")
    editor = _create_user(app_ctx, "editor", "編集 太郎")
    client = app_ctx.test_client()
    _login(client, owner)

    form = client.post("/tools/powervote/api/forms", json={"title": "Shared"}).get_json()["form"]
    form["settings"] = {"shared_editors": [editor]}
    saved = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)
    assert saved.status_code == 200

    editor_client = app_ctx.test_client()
    _login(editor_client, editor)
    listed = editor_client.get("/tools/powervote/api/forms")
    assert listed.status_code == 200
    listed_form = listed.get_json()["forms"][0]
    assert listed_form["id"] == form["id"]
    assert listed_form["is_shared_to_me"] is True
    assert listed_form["owner_display_name"] == "Owner User"

    shared_form = editor_client.get(f"/tools/powervote/api/forms/{form['id']}").get_json()["form"]
    shared_form["title"] = "Edited by shared user"
    updated = editor_client.put(f"/tools/powervote/api/forms/{form['id']}", json=shared_form)
    assert updated.status_code == 200
    assert updated.get_json()["form"]["title"] == "Edited by shared user"


def test_powervote_user_search_uses_display_name(app_ctx):
    owner = _create_user(app_ctx, "owner", "Owner User")
    _create_user(app_ctx, "editor-login", "共有 花子")
    client = app_ctx.test_client()
    _login(client, owner)

    response = client.get("/tools/powervote/api/users?query=花子")

    assert response.status_code == 200
    users = response.get_json()["users"]
    assert users == [{"username": "editor-login", "name": "共有 花子"}]


def test_powervote_description_preserves_newlines(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Description"}).get_json()["form"]
    form["description"] = "line 1\nline 2"
    form["questions"] = [
        {
            "id": None,
            "type": "info",
            "title": "Info",
            "description": "first\nsecond",
            "required": False,
            "sort_order": 0,
            "options": [],
            "settings": {"description_format": {"bold": True, "color": "#123456"}},
        }
    ]

    updated = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)
    assert updated.status_code == 200
    payload = updated.get_json()["form"]
    assert payload["description"] == "line 1\nline 2"
    assert payload["questions"][0]["description"] == "first\nsecond"
    assert payload["questions"][0]["settings"]["description_format"]["bold"] is True


def test_powervote_description_html_is_sanitized(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Rich"}).get_json()["form"]
    form["settings"] = {
        "description_html": (
            '<div style="text-align: center"><span style="color: rgb(255, 0, 0)">赤</span>'
            '<script>alert(1)</script><strong>太字</strong><u>下線</u>'
            '<a href="https://example.com">リンク</a><ul><li>項目</li></ul></div>'
        )
    }
    form["questions"] = [
        {
            "id": None,
            "type": "info",
            "title": "Info",
            "description": "plain",
            "required": False,
            "sort_order": 0,
            "options": [],
            "settings": {
                "description_html": '<font color="#0000ff">青</font><img src=x onerror=alert(1)>'
            },
        }
    ]

    response = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)

    assert response.status_code == 200
    payload = response.get_json()["form"]
    assert "<script" not in payload["settings"]["description_html"]
    assert 'style="color: #ff0000"' in payload["settings"]["description_html"]
    assert "<u>下線</u>" in payload["settings"]["description_html"]
    assert '<a href="https://example.com" target="_blank" rel="noopener noreferrer">リンク</a>' in payload["settings"]["description_html"]
    assert "<ul" not in payload["settings"]["description_html"]
    assert "<li" not in payload["settings"]["description_html"]
    assert "項目" in payload["settings"]["description_html"]
    assert 'style="text-align: center"' in payload["settings"]["description_html"]
    assert "<img" not in payload["questions"][0]["settings"]["description_html"]
    assert 'style="color: #0000ff"' in payload["questions"][0]["settings"]["description_html"]


def test_powervote_theme_sanitizes_base_text_color(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Theme"}).get_json()["form"]
    assert form["theme"]["base_text"] == "#0f172a"
    form["theme"] = {"accent": "#123456", "base_text": "rgb(12, 34, 56)"}

    response = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)

    assert response.status_code == 200
    assert response.get_json()["form"]["theme"] == {
        "accent": "#123456",
        "base_text": "#0c2238",
    }


def test_powervote_image_upload_is_served_and_deleted_with_form(app_ctx):
    username = _create_user(app_ctx)
    client = app_ctx.test_client()
    _login(client, username)

    form = client.post("/tools/powervote/api/forms", json={"title": "Images"}).get_json()["form"]
    upload = client.post(
        f"/tools/powervote/api/forms/{form['id']}/images",
        data={"image": (io.BytesIO(b"fake-png"), "sample.png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    url = upload.get_json()["url"]
    assert url.startswith(f"/tools/powervote/uploads/{form['id']}/")
    assert client.get(url).status_code == 200

    form["settings"] = {
        "description_html": f'<img src="{url}" alt="sample" data-width="55" data-align="center">'
    }
    updated = client.put(f"/tools/powervote/api/forms/{form['id']}", json=form)
    assert updated.status_code == 200
    image_html = updated.get_json()["form"]["settings"]["description_html"]
    assert f'<img src="{url}" alt="sample"' in image_html
    assert 'data-width="55"' in image_html
    assert 'data-align="center"' in image_html
    assert "width: 55%" in image_html
    assert "margin-left: auto" in image_html
    assert "margin-right: auto" in image_html

    upload_dir = Path(app_ctx.instance_path) / "powervote_uploads" / f"form_{form['id']}"
    assert upload_dir.exists()

    delete = client.delete(f"/tools/powervote/api/forms/{form['id']}")
    assert delete.status_code == 200
    assert not upload_dir.exists()
