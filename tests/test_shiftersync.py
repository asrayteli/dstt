import io
import re
import sys
from pathlib import Path

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _build_client(tmp_path):
    from app.tools.shiftersync import shiftersync_bp

    app = Flask(
        __name__,
        instance_path=str(tmp_path / "instance"),
        template_folder=str(ROOT / "app" / "templates"),
        static_folder=str(ROOT / "app" / "static"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.register_blueprint(shiftersync_bp)
    return app, app.test_client()


def test_calendar_outputs_are_session_scoped(tmp_path):
    app, client = _build_client(tmp_path)
    csv_text = "scene,2026,4,Tokyo Team\n日付,現場\n1,!A!Alice\n"

    response = client.post(
        "/tools/shiftersync/calendar",
        data={
            "csvfile": (io.BytesIO(csv_text.encode("utf-8-sig")), "calendar.csv"),
            "format": "png",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    match = re.search(r"/tools/shiftersync/download/[^\"? ]+\?download=1", html)
    assert match, html
    download_path = match.group(0)

    inline_path = download_path.replace("?download=1", "")
    inline_response = client.get(inline_path)
    assert inline_response.status_code == 200
    assert inline_response.mimetype == "image/png"

    download_response = client.get(download_path)
    assert download_response.status_code == 200
    assert "attachment" in download_response.headers.get("Content-Disposition", "")

    other_client = app.test_client()
    assert other_client.get(inline_path).status_code == 404


def test_calendar_accepts_shift_jis_csv(tmp_path):
    _, client = _build_client(tmp_path)
    csv_text = "scene,2026,4,東京チーム\n日付,現場\n1,!A!山田\n"

    response = client.post(
        "/tools/shiftersync/calendar",
        data={
            "csvfile": (io.BytesIO(csv_text.encode("cp932")), "calendar.csv"),
            "format": "png",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/tools/shiftersync/download/" in html
