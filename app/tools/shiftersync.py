from __future__ import annotations

import os
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename

try:
    from .shiftersync_format import (
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        parse_csv_text,
    )
    from .japan_holidays import JAPAN_HOLIDAYS
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        LEAVE_OPTION_MAPPINGS,
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        parse_csv_text,
    )
    from app.tools.japan_holidays import JAPAN_HOLIDAYS  # type: ignore


shiftersync_bp = Blueprint("shiftersync", __name__, url_prefix="/tools/shiftersync")

ARTIFACT_SESSION_KEY = "shiftersync_calendar_artifacts"
NUMBER_CAR_OPTIONS = {"N1", "N2", "N3", "N4", "N5"}
TEMPORARY_OPTION = "TEMP"
TIME_CONFLICT_RULES = {
    ("A", "P"): False,
    ("A", "E"): True,
    ("A", "L"): False,
    ("P", "E"): False,
    ("P", "L"): True,
    ("E", "L"): False,
}
VEHICLE_OPTIONS = {"M", "C", "O", "W", "V"}
LEAVE_OPTION_KEYS = set(LEAVE_OPTION_MAPPINGS.keys())
JAPAN_HOLIDAYS_SET = set(JAPAN_HOLIDAYS)
JST = timezone(timedelta(hours=9))


def _calendar_issued_text() -> str:
    now = datetime.now(JST)
    return f"発行日時: {now.strftime('%Y年%m月%d日 %H:%M:%S')} (JST)"


def _calendar_output_dir() -> Path:
    configured = current_app.config.get("SHIFTERSYNC_OUTPUT_DIR") or os.environ.get("SHIFTERSYNC_OUTPUT_DIR")
    base = Path(configured) if configured else Path(current_app.instance_path) / "shiftersync" / "calendar_outputs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _decode_csv_bytes(raw: bytes) -> str:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"CSV の読み込みに失敗しました: {last_error}")


def _safe_download_name(title: str, year: int, month: int, extension: str) -> str:
    safe_title = secure_filename(title) or "calendar"
    return f"{year}-{str(month).zfill(2)}_calendar_{safe_title}.{extension}"


def _register_artifact(path: Path, download_name: str, mimetype: str) -> str:
    token = secrets.token_urlsafe(24)
    artifacts = dict(session.get(ARTIFACT_SESSION_KEY) or {})
    artifacts[token] = {
        "path": str(path),
        "download_name": download_name,
        "mimetype": mimetype,
    }
    session[ARTIFACT_SESSION_KEY] = artifacts
    session.modified = True
    return token


@shiftersync_bp.route("/", methods=["GET", "POST"])
def shiftersync():
    result = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            return redirect(url_for("shiftersync.create"))
        if action == "upload":
            return redirect(url_for("shiftersync.upload"))
        if action == "check":
            return redirect(url_for("shiftersync.check"))
        if action == "calendar":
            return redirect(url_for("shiftersync.calendar_view"))
        if action == "cloudshift":
            return redirect(url_for("cloudshift.index"))
    return render_template("shiftersync.html", result=result)


@shiftersync_bp.route("/create", methods=["GET", "POST"])
def create():
    return render_template("ss_create.html", shiftersync_holidays=sorted(JAPAN_HOLIDAYS_SET))


@shiftersync_bp.route("/upload", methods=["GET", "POST"])
def upload():
    return render_template("ss_upload.html", shiftersync_holidays=sorted(JAPAN_HOLIDAYS_SET))


def _is_duplicate_by_rules(
    option1: str | None, option2: str | None, *, same_site: bool = False
) -> bool:
    if option1 in LEAVE_OPTION_KEYS or option2 in LEAVE_OPTION_KEYS:
        return False

    if option1 == TEMPORARY_OPTION or option2 == TEMPORARY_OPTION:
        return same_site and option1 == option2 == TEMPORARY_OPTION

    if option1 is None or option2 is None:
        return True

    if option1 in NUMBER_CAR_OPTIONS or option2 in NUMBER_CAR_OPTIONS:
        return option1 in NUMBER_CAR_OPTIONS and option1 == option2

    if option1 in {"A", "P", "E", "L"} or option2 in {"A", "P", "E", "L"}:
        if option1 not in {"A", "P", "E", "L"} or option2 not in {"A", "P", "E", "L"}:
            return False
        key = tuple(sorted((option1, option2)))
        return TIME_CONFLICT_RULES.get(key, option1 == option2)

    if option1 in VEHICLE_OPTIONS and option2 in VEHICLE_OPTIONS:
        if option1 == "V" or option2 == "V":
            return True
        if option1 == option2:
            return True
        return False

    return option1 == option2


@shiftersync_bp.route("/check", methods=["GET", "POST"])
def check():
    if request.method == "GET":
        return render_template("ss_check.html")

    files = request.files.getlist("csv_files")
    if not files:
        return jsonify({"error": "CSV ファイルをアップロードしてください"})
    if len(files) > 50:
        return jsonify({"error": "CSV は 50 ファイルまで比較できます"})

    mode = None
    year = None
    month = None
    file_targets: list[str] = []
    file_capacities: list[int | None] = []
    shift_data = defaultdict(lambda: [[] for _ in range(len(files))])

    for file_index, file in enumerate(files):
        filename = secure_filename(file.filename or f"file_{file_index + 1}.csv")
        try:
            payload = parse_csv_text(_decode_csv_bytes(file.read()))
        except Exception as exc:
            return jsonify({"error": f"{filename} の読み込みに失敗しました: {exc}"})

        file_mode = payload["mode"]
        file_year = payload["year"]
        file_month = payload["month"]
        if mode is None:
            mode, year, month = file_mode, file_year, file_month
        elif (mode, year, month) != (file_mode, file_year, file_month):
            return jsonify({"error": f"{filename} は他の CSV とモードまたは年月が一致していません"})

        file_targets.append(payload["title"])
        file_capacities.append(payload["required_capacity"] or None)

        for day_key, entries in payload["entries_per_day"].items():
            day = int(day_key)
            normalized_entries = []
            for entry in entries:
                option_key, name, comment = entry_option_and_name(entry)
                normalized_entries.append(
                    {
                        "original": entry["value"],
                        "display": entry_display_text(entry),
                        "comparison": entry_name_for_comparison(entry),
                        "option": option_key,
                        "name": name,
                        "comment": comment,
                    }
                )
            shift_data[day][file_index].extend(normalized_entries)

    if mode is None or year is None or month is None:
        return jsonify({"error": "比較できる CSV がありません"})

    conflicts = []
    same_site_conflicts = []

    for day, per_file_entries in shift_data.items():
        for file_index, entries in enumerate(per_file_entries):
            same_site_grouped = defaultdict(list)
            for entry in entries:
                if entry["name"]:
                    same_site_grouped[entry["name"]].append(entry)
            for items in same_site_grouped.values():
                if len(items) < 2:
                    continue
                for left_index, left in enumerate(items):
                    for right in items[left_index + 1 :]:
                        if _is_duplicate_by_rules(
                            left["option"], right["option"], same_site=True
                        ):
                            same_site_conflicts.append(
                                {"date": day, "entry": left["original"], "file_index": file_index}
                            )
                            same_site_conflicts.append(
                                {"date": day, "entry": right["original"], "file_index": file_index}
                            )

        grouped = defaultdict(list)
        for file_index, entries in enumerate(per_file_entries):
            for entry in entries:
                if entry["name"]:
                    grouped[entry["name"]].append({"file_index": file_index, "entry": entry})

        for items in grouped.values():
            if len(items) < 2:
                continue
            for left_index, left in enumerate(items):
                for right in items[left_index + 1 :]:
                    if left["file_index"] == right["file_index"]:
                        continue
                    if _is_duplicate_by_rules(left["entry"]["option"], right["entry"]["option"]):
                        conflicts.append({"date": day, "entry": left["entry"]["original"]})
                        conflicts.append({"date": day, "entry": right["entry"]["original"]})

    conflicts = list({f'{item["date"]}-{item["entry"]}': item for item in conflicts}.values())
    same_site_conflicts = list(
        {
            f'{item["date"]}-{item["entry"]}-{item["file_index"]}': item
            for item in same_site_conflicts
        }.values()
    )

    all_dates = list(range(1, __import__("calendar").monthrange(year, month)[1] + 1))
    matrix = {
        day: shift_data.get(day, [[] for _ in range(len(files))])
        for day in all_dates
    }

    return jsonify(
        {
            "mode": mode,
            "year": year,
            "month": month,
            "targets": file_targets,
            "capacities": file_capacities,
            "dates": all_dates,
            "matrix": matrix,
            "conflicts": conflicts,
            "same_site_conflicts": same_site_conflicts,
            "option_mappings": OPTION_MAPPINGS,
            "total_files": len(files),
        }
    )


@shiftersync_bp.route("/calendar", methods=["GET", "POST"])
def calendar_view():
    if request.method != "POST":
        return render_template("ss_calendar.html")

    file = request.files.get("csvfile")
    format_type = request.form.get("format")
    if not file or not format_type:
        flash("CSV ファイルと出力形式を選択してください")
        return render_template("ss_calendar.html")

    try:
        payload = parse_csv_text(_decode_csv_bytes(file.read()))

        day_map = {
            int(day): [
                {
                    "title": entry_display_text(entry),
                    "comment": entry.get("comment", ""),
                }
                for entry in entries
            ]
            for day, entries in payload["entries_per_day"].items()
        }

        output_filename = (
            f'{payload["year"]}-{str(payload["month"]).zfill(2)}_calendar_'
            f'{secure_filename(payload["title"]) or "calendar"}_{secrets.token_hex(6)}.{format_type}'
        )
        output_path = _calendar_output_dir() / output_filename

        if format_type == "pdf":
            generate_pdf_calendar(
                output_path,
                payload["year"],
                payload["month"],
                payload["mode"],
                payload["title"],
                day_map,
                payload["required_capacity"] or None,
            )
            mimetype = "application/pdf"
        elif format_type == "png":
            generate_png_calendar(
                output_path,
                payload["year"],
                payload["month"],
                payload["mode"],
                payload["title"],
                day_map,
                payload["required_capacity"] or None,
            )
            mimetype = "image/png"
        else:
            flash("未対応の出力形式です")
            return render_template("ss_calendar.html")

        artifact_token = _register_artifact(
            output_path,
            _safe_download_name(payload["title"], payload["year"], payload["month"], format_type),
            mimetype,
        )
        artifact_url = url_for("shiftersync.download", token=artifact_token)
        return render_template(
            "ss_calendar.html",
            image_url=artifact_url if format_type == "png" else None,
            pdf_url=artifact_url if format_type == "pdf" else None,
            download_url=f"{artifact_url}?download=1",
        )
    except Exception as exc:
        flash(f"カレンダー出力に失敗しました: {exc}")
        return render_template("ss_calendar.html")
def _register_pdf_font() -> str:
    try:
        font_path = "./app/static/fonts/NotoSansJP-VariableFont_wght.ttf"
        pdfmetrics.registerFont(TTFont("Noto", font_path))
        return "Noto"
    except Exception:
        return "Helvetica"


def _load_image_font(size: int):
    try:
        return ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _calendar_weekdays() -> list[str]:
    return ["月", "火", "水", "木", "金", "土", "日"]


def _comment_badge(comment: str) -> str:
    return ""


def _calendar_day_kind(year: int, month: int, day: int) -> str:
    iso_date = f"{year:04d}-{month:02d}-{day:02d}"
    if iso_date in JAPAN_HOLIDAYS_SET:
        return "holiday"

    weekday = date(year, month, day).weekday()
    if weekday == 6:
        return "sunday"
    if weekday == 5:
        return "saturday"
    return "weekday"


def _png_calendar_palette(kind: str) -> dict[str, str]:
    if kind == "holiday":
        return {
            "cell_background": "#fde7ec",
            "cell_border": "#cf94a1",
            "badge_background": "#f1ccd4",
            "badge_text": "#111111",
        }
    if kind == "sunday":
        return {
            "cell_background": "#fde7ec",
            "cell_border": "#cf94a1",
            "badge_background": "#f1ccd4",
            "badge_text": "#111111",
        }
    if kind == "saturday":
        return {
            "cell_background": "#e4f0ff",
            "cell_border": "#86a9d2",
            "badge_background": "#cadcf3",
            "badge_text": "#111111",
        }
    return {
        "cell_background": "#ffffff",
        "cell_border": "#7898bd",
        "badge_background": "#cddff2",
        "badge_text": "#111111",
    }


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _ellipsize_calendar_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    value = str(text or "")
    if not value:
        return ""
    if _text_width(draw, value, font) <= max_width:
        return value

    ellipsis = "..."
    ellipsis_width = _text_width(draw, ellipsis, font)
    if ellipsis_width >= max_width:
        return ellipsis

    trimmed = value
    while trimmed and _text_width(draw, trimmed, font) + ellipsis_width > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed}{ellipsis}" if trimmed else ellipsis


def _fit_calendar_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int | None,
) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if max_lines is not None and max_lines <= 0:
        return []

    paragraphs = normalized.split("\n")
    lines: list[str] = []
    truncated = False
    unlimited = max_lines is None

    for paragraph_index, paragraph in enumerate(paragraphs):
        current = ""
        text_value = paragraph.strip()

        if not text_value:
            if lines and (unlimited or len(lines) < max_lines):
                lines.append("")
            elif paragraph_index < len(paragraphs) - 1:
                truncated = True
                break
            continue

        for character in text_value:
            candidate = f"{current}{character}"
            if current and _text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = character
                if not unlimited and len(lines) >= max_lines:
                    truncated = True
                    break
            else:
                current = candidate

        if truncated:
            break

        if current:
            lines.append(current)
            if not unlimited and len(lines) > max_lines:
                lines = lines[:max_lines]
                truncated = True
                break

        if not unlimited and len(lines) >= max_lines and paragraph_index < len(paragraphs) - 1:
            truncated = True
            break

    if truncated and lines:
        lines[-1] = _ellipsize_calendar_text(draw, lines[-1], font, max_width)

    return lines if unlimited else lines[:max_lines]


def _layout_png_entry_block(
    draw: ImageDraw.ImageDraw,
    entry: dict[str, Any],
    title_font: ImageFont.ImageFont,
    comment_font: ImageFont.ImageFont,
    title_width: int,
    comment_width: int,
) -> dict[str, Any]:
    title_lines = _fit_calendar_lines(
        draw,
        f"{entry['title']}{_comment_badge(entry['comment'])}",
        title_font,
        title_width,
        1,
    )
    comment_lines = _fit_calendar_lines(
        draw,
        entry["comment"],
        comment_font,
        comment_width,
        None,
    )

    block_height = 14
    if title_lines:
        block_height += len(title_lines) * 18
    if comment_lines:
        block_height += 4 + len(comment_lines) * 15

    return {
        "title_lines": title_lines,
        "comment_lines": comment_lines,
        "block_height": block_height,
    }


def _draw_bold_calendar_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    *,
    fill: str,
    font: ImageFont.ImageFont,
) -> None:
    draw.text(position, text, fill=fill, font=font)
    draw.text((position[0] + 0.45, position[1]), text, fill=fill, font=font)


def generate_pdf_calendar(path, year, month, mode, title, day_map, capacity=None):
    pdf = canvas.Canvas(path, pagesize=landscape(A4))
    width, height = landscape(A4)
    font_name = _register_pdf_font()

    pdf.setFillColor(HexColor("#4f5a54"))
    pdf.rect(0, height - 72, width, 72, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(font_name, 22)
    header_text = f"{year}年{month}月 {title}"
    if capacity:
        header_text += f" / 必要人数 {capacity}"
    pdf.drawCentredString(width / 2, height - 32, header_text)
    pdf.setFont(font_name, 9)
    pdf.drawCentredString(width / 2, height - 60, _calendar_issued_text())

    start_x = 34
    start_y = height - 118
    cell_w = (width - 68) / 7
    cell_h = 88

    pdf.setFont(font_name, 12)
    for column, day_name in enumerate(_calendar_weekdays()):
        x = start_x + column * cell_w
        pdf.setFillColor(HexColor("#efefe8"))
        pdf.rect(x, start_y, cell_w, 26, fill=1)
        pdf.setFillColor(HexColor("#43463f"))
        pdf.drawCentredString(x + cell_w / 2, start_y + 8, day_name)

    calendar_module = __import__("calendar")
    weeks = calendar_module.Calendar(firstweekday=calendar_module.MONDAY).monthdayscalendar(year, month)
    for week_index, week in enumerate(weeks):
        for column, day in enumerate(week):
            x = start_x + column * cell_w
            y = start_y - 28 - week_index * cell_h
            day_kind = _calendar_day_kind(year, month, day) if day else "weekday"
            if not day:
                fill_color = HexColor("#ececec")
                stroke_color = HexColor("#c4c4c4")
                badge_fill = HexColor("#dddddd")
            elif day_kind == "saturday":
                fill_color = HexColor("#edf5ff")
                stroke_color = HexColor("#8eadd0")
                badge_fill = HexColor("#dbe9f8")
            elif day_kind in {"sunday", "holiday"}:
                fill_color = HexColor("#fdf0f2")
                stroke_color = HexColor("#d4a0aa")
                badge_fill = HexColor("#f6dfe3")
            else:
                fill_color = HexColor("#fbfbf8")
                stroke_color = HexColor("#7898bd")
                badge_fill = HexColor("#dbe9f7")
            pdf.setFillColor(fill_color)
            pdf.rect(x, y, cell_w, cell_h, fill=1)
            pdf.setStrokeColor(stroke_color)
            pdf.rect(x, y, cell_w, cell_h, fill=0)
            if day == 0:
                continue

            entries = day_map.get(day, [])
            if capacity and len(entries) < capacity:
                pdf.setStrokeColor(HexColor("#c79a62"))
                pdf.rect(x + 2, y + 2, cell_w - 4, cell_h - 4, fill=0)

            pdf.setFillColor(badge_fill)
            pdf.rect(x + 6, y + cell_h - 21, 22, 14, fill=1, stroke=0)
            pdf.setFillColor(HexColor("#111111"))
            pdf.setFont(font_name, 11)
            pdf.drawString(x + 6, y + cell_h - 16, str(day))

            for line_index, entry in enumerate(entries[:4]):
                line_y = y + cell_h - 30 - line_index * 14
                title_text = entry["title"]
                if len(title_text) > 15:
                    title_text = f"{title_text[:15]}..."
                pdf.setFont(font_name, 8.5)
                pdf.setFillColor(HexColor("#111111"))
                pdf.drawString(x + 6, line_y, f"{title_text}{_comment_badge(entry['comment'])}")
                if entry["comment"]:
                    comment_text = entry["comment"]
                    if len(comment_text) > 16:
                        comment_text = f"{comment_text[:16]}..."
                    pdf.setFont(font_name, 7.5)
                    pdf.setFillColor(HexColor("#111111"))
                    pdf.drawString(x + 10, line_y - 9, comment_text)

    pdf.save()


def generate_png_calendar(path, year, month, mode, title, day_map, capacity=None):
    width = 1600
    min_height = 1180
    page_background = "#eef5fc"
    header_background = "#5f86b7"
    header_text = "#ffffff"
    weekday_background = "#d4e4f5"
    weekday_text = "#111111"
    empty_cell_background = "#ececec"
    border_color = "#7898bd"
    title_text_color = "#111111"
    comment_text_color = "#111111"
    entry_card_background = "#f5f9fe"
    entry_card_border = "#a8c0dc"
    footer_text_color = "#111111"
    shortage_border = "#d59f58"
    cell_w = (width - 80) // 7
    minimum_bottom_space = 58
    start_x = 40
    start_y = 150
    weekday_header_height = 42
    header_height = 116
    calendar_top = start_y + 52
    content_left_padding = 10
    content_right_padding = 10
    content_inner_padding = 10
    content_top_offset = 42
    content_bottom_padding = 12
    entry_gap = 6
    title_width = cell_w - content_left_padding - content_right_padding - content_inner_padding
    comment_width = title_width - 10

    title_font = _load_image_font(36)
    header_font = _load_image_font(24)
    day_font = _load_image_font(20)
    text_font = _load_image_font(16)
    comment_font = _load_image_font(14)
    footer_font = _load_image_font(12)
    issued_font = _load_image_font(16)

    calendar_module = __import__("calendar")
    weeks = calendar_module.Calendar(firstweekday=calendar_module.MONDAY).monthdayscalendar(year, month)
    week_count = max(len(weeks), 1)
    base_cell_height = max(148, (min_height - calendar_top - minimum_bottom_space) // week_count)

    measurement = Image.new("RGB", (width, min_height), page_background)
    measurement_draw = ImageDraw.Draw(measurement)
    entry_layouts_by_day: dict[int, list[dict[str, Any]]] = {}
    row_heights: list[int] = []

    for week in weeks:
        row_height = base_cell_height
        for day in week:
            if day == 0:
                continue
            layouts = [
                _layout_png_entry_block(
                    measurement_draw,
                    entry,
                    text_font,
                    comment_font,
                    title_width,
                    comment_width,
                )
                for entry in day_map.get(day, [])
            ]
            entry_layouts_by_day[day] = layouts
            content_height = 0
            for index, layout in enumerate(layouts):
                if index > 0:
                    content_height += entry_gap
                content_height += layout["block_height"]
            required_height = content_top_offset + content_bottom_padding + content_height
            row_height = max(row_height, required_height)
        row_heights.append(row_height)

    height = max(min_height, calendar_top + sum(row_heights) + minimum_bottom_space)
    img = Image.new("RGB", (width, height), page_background)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, width, header_height], fill=header_background)
    title_text = f"{year}年{month}月 {title}"
    if capacity:
        title_text += f" / 必要人数 {capacity}"
    title_box = draw.textbbox((0, 0), title_text, font=title_font)
    _draw_bold_calendar_text(
        draw,
        ((width - (title_box[2] - title_box[0])) // 2, 18),
        title_text,
        fill=header_text,
        font=title_font,
    )
    issued_text = _calendar_issued_text()
    issued_box = draw.textbbox((0, 0), issued_text, font=issued_font)
    _draw_bold_calendar_text(
        draw,
        ((width - (issued_box[2] - issued_box[0])) // 2, 76),
        issued_text,
        fill=header_text,
        font=issued_font,
    )

    for column, day_name in enumerate(_calendar_weekdays()):
        x = start_x + column * cell_w
        draw.rectangle(
            [x, start_y, x + cell_w, start_y + weekday_header_height],
            fill=weekday_background,
            outline=border_color,
            width=2,
        )
        box = draw.textbbox((0, 0), day_name, font=header_font)
        _draw_bold_calendar_text(
            draw,
            (x + (cell_w - (box[2] - box[0])) / 2, start_y + 7),
            day_name,
            fill=weekday_text,
            font=header_font,
        )

    current_y = calendar_top
    for week_index, week in enumerate(weeks):
        row_height = row_heights[week_index]
        for column, day in enumerate(week):
            x = start_x + column * cell_w
            y = current_y
            if day:
                day_palette = _png_calendar_palette(_calendar_day_kind(year, month, day))
                fill = day_palette["cell_background"]
                cell_outline = day_palette["cell_border"]
            else:
                day_palette = None
                fill = empty_cell_background
                cell_outline = border_color
            draw.rectangle([x, y, x + cell_w, y + row_height], fill=fill, outline=cell_outline, width=2)
            if day == 0:
                continue

            entries = day_map.get(day, [])
            if capacity and len(entries) < capacity:
                draw.rectangle(
                    [x + 3, y + 3, x + cell_w - 3, y + row_height - 3],
                    outline=shortage_border,
                    width=2,
                )

            draw.rectangle(
                [x + 8, y + 8, x + 48, y + 36],
                fill=day_palette["badge_background"],
                outline=None,
            )
            day_text = str(day)
            day_box = draw.textbbox((0, 0), day_text, font=day_font)
            _draw_bold_calendar_text(
                draw,
                (x + 28 - (day_box[2] - day_box[0]) / 2, y + 10),
                day_text,
                fill=day_palette["badge_text"],
                font=day_font,
            )

            content_left = x + content_left_padding
            content_right = x + cell_w - content_right_padding
            cursor_y = y + content_top_offset

            for layout in entry_layouts_by_day.get(day, []):
                block_height = layout["block_height"]

                draw.rectangle(
                    [content_left, cursor_y, content_right, cursor_y + block_height],
                    fill=entry_card_background,
                    outline=entry_card_border,
                    width=1,
                )

                line_top = cursor_y + 8
                for line in layout["title_lines"]:
                    _draw_bold_calendar_text(
                        draw,
                        (content_left + 8, line_top),
                        line,
                        fill=title_text_color,
                        font=text_font,
                    )
                    line_top += 18
                if layout["comment_lines"]:
                    line_top += 2
                    for line in layout["comment_lines"]:
                        _draw_bold_calendar_text(
                            draw,
                            (content_left + 14, line_top),
                            line,
                            fill=comment_text_color,
                            font=comment_font,
                        )
                        line_top += 15

                cursor_y += block_height + entry_gap

        current_y += row_height

    footer = f"Generated by DSTT Shifter-Sync / {year}-{str(month).zfill(2)}"
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    _draw_bold_calendar_text(
        draw,
        ((width - (footer_box[2] - footer_box[0])) // 2, height - 24),
        footer,
        fill=footer_text_color,
        font=footer_font,
    )
    img.save(path, "PNG", optimize=True, quality=95)


@shiftersync_bp.route("/download/<token>", methods=["GET"])
def download(token):
    artifacts = session.get(ARTIFACT_SESSION_KEY) or {}
    artifact = artifacts.get(token)
    if not artifact:
        abort(404)

    path = Path(artifact.get("path", ""))
    if not path.exists() or not path.is_file():
        abort(404)

    return send_file(
        path,
        as_attachment=request.args.get("download") == "1",
        download_name=artifact.get("download_name") or path.name,
        mimetype=artifact.get("mimetype"),
    )
