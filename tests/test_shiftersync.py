import io
import re
import sys
from pathlib import Path

from flask import Flask
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tools.shiftersync import _calendar_day_kind, _fit_calendar_lines, _load_image_font, generate_png_calendar
from app.tools.shiftersync_format import entry_display_text


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


def test_check_ignores_leave_entries_in_all_conflict_checks(tmp_path):
    _, client = _build_client(tmp_path)
    csv_a = "person,2026,4,Team A\n日付,現場\n1,!PAID!Alice,!PAID!Alice\n"
    csv_b = "person,2026,4,Team B\n日付,現場\n1,!COMP!Alice\n"

    response = client.post(
        "/tools/shiftersync/check",
        data={
            "csv_files": [
                (io.BytesIO(csv_a.encode("utf-8-sig")), "a.csv"),
                (io.BytesIO(csv_b.encode("utf-8-sig")), "b.csv"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["conflicts"] == []
    assert payload["same_site_conflicts"] == []


def test_entry_display_text_places_name_before_option():
    assert entry_display_text({"value": "!A!Alice", "comment": ""}) == "Alice 午前"


def test_calendar_day_kind_distinguishes_saturday_sunday_and_holiday():
    assert _calendar_day_kind(2026, 3, 20) == "holiday"
    assert _calendar_day_kind(2026, 3, 21) == "saturday"
    assert _calendar_day_kind(2026, 3, 22) == "sunday"
    assert _calendar_day_kind(2026, 3, 18) == "weekday"


def test_fit_calendar_lines_allows_more_than_two_comment_lines():
    image = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(image)
    font = _load_image_font(14)

    lines = _fit_calendar_lines(
        draw,
        "1行目コメント\n2行目コメント\n3行目コメント\n4行目コメント",
        font,
        120,
        None,
    )

    assert len(lines) >= 4


def test_generate_png_calendar_uses_lower_space_for_six_week_month(tmp_path):
    path = tmp_path / "calendar_six_weeks.png"

    generate_png_calendar(path, 2026, 3, "scene", "Tokyo Team", {}, None)

    with Image.open(path) as image:
        assert image.size[1] >= 1180
        assert image.getpixel((50, 1090)) != (238, 245, 252)


def test_generate_png_calendar_expands_for_long_multiline_comment(tmp_path):
    path = tmp_path / "calendar_long_comment.png"
    long_comment = "\n".join([f"コメント{i}" for i in range(1, 13)])

    generate_png_calendar(
        path,
        2026,
        4,
        "scene",
        "Tokyo Team",
        {1: [{"title": "Alice 午前", "comment": long_comment}]},
        None,
    )

    with Image.open(path) as image:
        assert image.size[1] > 1180
