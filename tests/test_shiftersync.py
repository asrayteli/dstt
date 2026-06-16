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
from app.tools.shiftersync_format import entry_display_text, parse_csv_text, serialize_csv_text


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


def test_check_numbered_vehicle_conflicts_only_with_same_number(tmp_path):
    _, client = _build_client(tmp_path)
    csv_a = "scene,2026,4,Team A\n譌･莉・迴ｾ蝣ｴ\n1,!N1!Alice,!N2!Alice,!N1!Alice\n"
    csv_b = "scene,2026,4,Team B\n譌･莉・迴ｾ蝣ｴ\n1,!N3!Alice\n"
    csv_c = "scene,2026,4,Team C\n譌･莉・迴ｾ蝣ｴ\n1,!N1!Alice\n"

    response = client.post(
        "/tools/shiftersync/check",
        data={
            "csv_files": [
                (io.BytesIO(csv_a.encode("utf-8-sig")), "a.csv"),
                (io.BytesIO(csv_b.encode("utf-8-sig")), "b.csv"),
                (io.BytesIO(csv_c.encode("utf-8-sig")), "c.csv"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert sorted(item["entry"] for item in payload["same_site_conflicts"]) == ["!N1!Alice"]
    assert sorted(item["entry"] for item in payload["conflicts"]) == ["!N1!Alice"]


def test_check_temporary_option_conflicts_only_within_same_site(tmp_path):
    _, client = _build_client(tmp_path)
    csv_a = "scene,2026,4,Team A\n譌･莉・迴ｾ蝣ｴ\n1,!TEMP!Alice,!TEMP!Alice\n"
    csv_b = "scene,2026,4,Team B\n譌･莉・迴ｾ蝣ｴ\n1,!TEMP!Alice\n"

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
    assert payload["option_mappings"]["TEMP"] == "臨時便"
    assert sorted(item["entry"] for item in payload["same_site_conflicts"]) == ["!TEMP!Alice"]
    assert payload["conflicts"] == []


def test_entry_display_text_places_name_before_option():
    assert entry_display_text({"value": "!A!Alice", "comment": ""}) == "Alice 午前"


def test_save_entry_modal_does_not_open_day_detail_popup():
    script = (ROOT / "app" / "static" / "shiftersync" / "js" / "ss_common.js").read_text(encoding="utf-8")
    start = script.index("function saveEntryFromModal()")
    end = script.index("function updateDayEmployeeSelectionNote", start)
    save_entry_block = script[start:end]
    assert "closeModal('entry');" in save_entry_block
    assert "openDayDetail(day);" not in save_entry_block


def test_upload_parser_preserves_extended_csv_metadata_rows():
    script = (ROOT / "app" / "static" / "shiftersync" / "js" / "ss_upload.js").read_text(encoding="utf-8")
    assert "const supportedModes = ['scene', 'person', 'master', 'substitute'];" in script
    assert "!['scene', 'person'].includes(mode)" not in script
    for prefix in [
        "#second_option",
        "#employee_name",
        "#site_row_id",
        "#site_id",
        "#site_name",
        "#site_branch_row_id",
        "#site_branch",
        "#substitute_request_type",
        "#substitute_resolved",
        "#substitute_source_entry_id",
    ]:
        assert prefix in script


def test_frontend_csv_export_preserves_substitute_metadata_rows():
    script = (ROOT / "app" / "static" / "shiftersync" / "js" / "ss_common.js").read_text(encoding="utf-8")
    start = script.index("function buildCSV()")
    end = script.index("function setState", start)
    build_csv_block = script[start:end]
    assert "substituteMetadataRows" in build_csv_block
    assert ".concat(substituteRows)" in build_csv_block


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


def test_csv_roundtrip_preserves_site_branch_metadata():
    csv_text = serialize_csv_text(
        "scene",
        2026,
        4,
        "Tokyo Team",
        0,
        {
            "1": [
                {
                    "id": "entry-1",
                    "value": "!O!Alice",
                    "comment": "大型担当",
                    "employee_name": "Alice",
                    "employee_number": "9001",
                    "site_branch_row_id": "12",
                    "site_branch": "001",
                }
            ]
        },
    )

    parsed = parse_csv_text(csv_text)
    entry = parsed["entries_per_day"]["1"][0]
    assert entry["employee_name"] == "Alice"
    assert entry["employee_number"] == "9001"
    assert entry["site_branch_row_id"] == "12"
    assert entry["site_branch"] == "001"


def test_csv_roundtrip_preserves_person_site_link_metadata():
    csv_text = serialize_csv_text(
        "person",
        2026,
        4,
        "Worker One",
        0,
        {
            "2": [
                {
                    "id": "entry-2",
                    "value": "!N1!Linked Master Site",
                    "comment": "linked site",
                    "site_row_id": "88",
                    "site_id": "24680",
                    "site_name": "Linked Master Site",
                }
            ]
        },
        "3001",
    )

    parsed = parse_csv_text(csv_text)
    entry = parsed["entries_per_day"]["2"][0]
    assert entry["site_row_id"] == "88"
    assert entry["site_id"] == "24680"
    assert entry["site_name"] == "Linked Master Site"
    assert parsed["employee_number"] == "3001"
