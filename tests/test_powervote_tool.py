from __future__ import annotations

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
