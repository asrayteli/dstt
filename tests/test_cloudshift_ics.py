"""ViewPWA の iCalendar 購読フィード（/pwa/<token>/calendar.ics）のテスト。

PWA URL ひとつで「通知」と「カレンダー購読」の両方を提供する。
認可はリンク保持（PWA トークン）で、他の PWA API と同じモデル。
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "cloudshift.db"
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


def _create_owner(app_ctx, username: str = "owner"):
    from app.models import User, db
    from app.access_control import grant_tool_access

    with app_ctx.app_context():
        user = User(username=username, password_hash="hash", name="Owner")
        db.session.add(user)
        db.session.commit()
        grant_tool_access(user.id, ["shiftersync"], "system")


def _login(client, username: str = "owner"):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _token_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _create_project(app_ctx, *, title: str = "カレンダーテスト現場") -> tuple[str, dict[str, str]]:
    _create_owner(app_ctx)
    client = app_ctx.test_client()
    _login(client)
    resp = client.post(
        "/tools/shiftersync/cloudshift/api/create",
        data={"title": title, "mode": "scene", "year": "2026", "month": "5"},
    )
    assert resp.status_code == 200
    project = resp.get_json()["project"]["project"]
    return project["id"], project["urls"]


def _save_entries(app_ctx, project_id: str, entries_per_day: dict) -> None:
    client = app_ctx.test_client()
    _login(client)
    resp = client.put(
        f"/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/5",
        json={
            "base_month": {
                "year": 2026,
                "month": 5,
                "revision": 1,
                "required_capacity": 0,
                "entries_per_day": {},
            },
            "required_capacity": 0,
            "entries_per_day": entries_per_day,
        },
    )
    assert resp.status_code == 200


def _fetch_ics(app_ctx, pwa_token: str):
    anon = app_ctx.test_client()
    return anon.get(f"/tools/shiftersync/cloudshift/pwa/{pwa_token}/calendar.ics")


def test_ics_feed_accessible_without_login(app_ctx):
    _, urls = _create_project(app_ctx)
    token = _token_from_url(urls["pwa_url"])
    resp = _fetch_ics(app_ctx, token)
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type", "").startswith("text/calendar")
    body = resp.get_data(as_text=True)
    assert "BEGIN:VCALENDAR" in body
    assert "END:VCALENDAR" in body
    assert "X-WR-CALNAME:カレンダーテスト現場" in body


def test_ics_feed_contains_all_day_events(app_ctx):
    project_id, urls = _create_project(app_ctx)
    _save_entries(
        app_ctx,
        project_id,
        {
            "1": [{"id": "e1", "value": "!A!山田", "employee_number": "E001"}],
            # 月末エントリの DTEND は翌月1日へ繰り上がる
            "31": [{"id": "e2", "value": "佐藤", "comment": "備考A, 改行\nあり; セミコロン"}],
        },
    )
    token = _token_from_url(urls["pwa_url"])
    body = _fetch_ics(app_ctx, token).get_data(as_text=True)

    assert body.count("BEGIN:VEVENT") == 2
    assert "DTSTART;VALUE=DATE:20260501" in body
    assert "DTEND;VALUE=DATE:20260502" in body
    assert "DTSTART;VALUE=DATE:20260531" in body
    assert "DTEND;VALUE=DATE:20260601" in body
    # SUMMARY はオプションラベル付きの表示テキスト
    assert "SUMMARY:山田 午前" in body
    # TEXT 値のエスケープ（, ; 改行）
    assert "DESCRIPTION:備考A\\, 改行\\nあり\\; セミコロン" in body


def test_ics_feed_uid_stable_across_fetches(app_ctx):
    project_id, urls = _create_project(app_ctx)
    _save_entries(app_ctx, project_id, {"5": [{"id": "stable-entry", "value": "!P!山田"}]})
    token = _token_from_url(urls["pwa_url"])

    def uids(text: str) -> list[str]:
        return sorted(
            line for line in text.replace("\r\n ", "").splitlines() if line.startswith("UID:")
        )

    first = uids(_fetch_ics(app_ctx, token).get_data(as_text=True))
    second = uids(_fetch_ics(app_ctx, token).get_data(as_text=True))
    assert first and first == second
    assert any("stable-entry" in uid for uid in first)


def test_ics_feed_rejects_unknown_and_non_pwa_tokens(app_ctx):
    _, urls = _create_project(app_ctx)
    # 存在しないトークン
    assert _fetch_ics(app_ctx, "no-such-token").status_code == 404
    # 閲覧トークン・編集トークンでは購読できない（PWA トークン専用）
    for key in ("view_url", "edit_url"):
        token = _token_from_url(urls[key])
        assert _fetch_ics(app_ctx, token).status_code == 404


def test_ics_feed_uids_unique_even_with_duplicate_entry_ids(app_ctx):
    """同一日に同じ entry id が並んでも UID は一意になる。

    通常の API 保存はマージ（id 単位）で同一 id を1件へ畳むため到達しないが、
    レガシー JSON 由来など保存経路を通らないデータへの防御として、
    重複 UID（カレンダーアプリ側で片方が黙って消える）を出さないことを
    生成関数の単体で検証する。
    """
    from app.tools.cloudshift import _ics_text_for_project

    project = {
        "id": "proj-x",
        "title": "重複IDテスト",
        "mode": "scene",
        "months": {
            "2026-05": {
                "year": 2026,
                "month": 5,
                "revision": 1,
                "updated_at": "2026-05-01T09:00:00+09:00",
                "entries_per_day": {
                    "7": [
                        {"id": "dup-id", "value": "!A!山田"},
                        {"id": "dup-id", "value": "!P!佐藤"},
                    ]
                },
            }
        },
    }
    with app_ctx.app_context():
        body = _ics_text_for_project(project)
    uids = [
        line for line in body.replace("\r\n ", "").splitlines() if line.startswith("UID:")
    ]
    assert len(uids) == 2
    assert len(set(uids)) == 2


def test_ics_lines_folded_within_75_octets(app_ctx):
    project_id, urls = _create_project(app_ctx)
    long_comment = "とても長いコメント" * 20
    _save_entries(
        app_ctx,
        project_id,
        {"10": [{"id": "e-long", "value": "!E!長い名前のスタッフテスト", "comment": long_comment}]},
    )
    token = _token_from_url(urls["pwa_url"])
    body = _fetch_ics(app_ctx, token).get_data(as_text=True)
    for line in body.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"folded line too long: {line[:40]}..."
