from __future__ import annotations

import os
import secrets
from collections import Counter, defaultdict
from pathlib import Path

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
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        parse_csv_text,
    )
except ImportError:
    from app.tools.shiftersync_format import (  # type: ignore
        OPTION_MAPPINGS,
        entry_display_text,
        entry_name_for_comparison,
        entry_option_and_name,
        parse_csv_text,
    )


shiftersync_bp = Blueprint("shiftersync", __name__, url_prefix="/tools/shiftersync")

ARTIFACT_SESSION_KEY = "shiftersync_calendar_artifacts"
NUMBER_CAR_OPTIONS = {"N1", "N2", "N3", "N4", "N5"}
TIME_CONFLICT_RULES = {
    ("A", "P"): False,
    ("A", "E"): True,
    ("A", "L"): False,
    ("P", "E"): False,
    ("P", "L"): True,
    ("E", "L"): False,
}
VEHICLE_OPTIONS = {"M", "C", "O", "W", "V"}


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
    return render_template("ss_create.html")


@shiftersync_bp.route("/upload", methods=["GET", "POST"])
def upload():
    return render_template("ss_upload.html")


def _is_duplicate_by_rules(option1: str | None, option2: str | None) -> bool:
    if option1 is None or option2 is None:
        return True

    if option1 in NUMBER_CAR_OPTIONS or option2 in NUMBER_CAR_OPTIONS:
        return True

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
            name_count = Counter(entry["name"] for entry in entries if entry["name"])
            for entry in entries:
                if entry["name"] and name_count[entry["name"]] > 1:
                    same_site_conflicts.append(
                        {"date": day, "entry": entry["original"], "file_index": file_index}
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
    return " *" if comment else ""


def generate_pdf_calendar(path, year, month, mode, title, day_map, capacity=None):
    pdf = canvas.Canvas(path, pagesize=landscape(A4))
    width, height = landscape(A4)
    font_name = _register_pdf_font()

    pdf.setFillColor(HexColor("#4f5a54"))
    pdf.rect(0, height - 62, width, 62, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(font_name, 22)
    header_text = f"{year}年{month}月 {title}"
    if capacity:
        header_text += f" / 必要人数 {capacity}"
    pdf.drawCentredString(width / 2, height - 40, header_text)

    start_x = 34
    start_y = height - 108
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
            pdf.setFillColor(HexColor("#fbfbf8") if day else HexColor("#f4f4f1"))
            pdf.rect(x, y, cell_w, cell_h, fill=1)
            pdf.setStrokeColor(HexColor("#d7d7d1"))
            pdf.rect(x, y, cell_w, cell_h, fill=0)
            if day == 0:
                continue

            entries = day_map.get(day, [])
            if capacity and len(entries) < capacity:
                pdf.setStrokeColor(HexColor("#c79a62"))
                pdf.rect(x + 2, y + 2, cell_w - 4, cell_h - 4, fill=0)

            pdf.setFillColor(HexColor("#2f312d"))
            pdf.setFont(font_name, 11)
            pdf.drawString(x + 6, y + cell_h - 16, str(day))

            for line_index, entry in enumerate(entries[:4]):
                line_y = y + cell_h - 30 - line_index * 14
                title_text = entry["title"]
                if len(title_text) > 15:
                    title_text = f"{title_text[:15]}..."
                pdf.setFont(font_name, 8.5)
                pdf.setFillColor(HexColor("#384039"))
                pdf.drawString(x + 6, line_y, f"{title_text}{_comment_badge(entry['comment'])}")
                if entry["comment"]:
                    comment_text = entry["comment"]
                    if len(comment_text) > 16:
                        comment_text = f"{comment_text[:16]}..."
                    pdf.setFont(font_name, 7.5)
                    pdf.setFillColor(HexColor("#6a6d66"))
                    pdf.drawString(x + 10, line_y - 9, comment_text)

    pdf.save()


def generate_png_calendar(path, year, month, mode, title, day_map, capacity=None):
    width = 1600
    height = 1180
    cell_w = (width - 80) // 7
    cell_h = 140

    img = Image.new("RGB", (width, height), "#f4f4f1")
    draw = ImageDraw.Draw(img)
    title_font = _load_image_font(34)
    header_font = _load_image_font(24)
    day_font = _load_image_font(20)
    text_font = _load_image_font(16)
    comment_font = _load_image_font(14)
    footer_font = _load_image_font(12)

    draw.rectangle([0, 0, width, 92], fill="#4f5a54")
    title_text = f"{year}年{month}月 {title}"
    if capacity:
        title_text += f" / 必要人数 {capacity}"
    title_box = draw.textbbox((0, 0), title_text, font=title_font)
    draw.text(((width - (title_box[2] - title_box[0])) // 2, 26), title_text, fill="white", font=title_font)

    start_x = 40
    start_y = 126
    for column, day_name in enumerate(_calendar_weekdays()):
        x = start_x + column * cell_w
        draw.rectangle([x, start_y, x + cell_w, start_y + 42], fill="#efefe8", outline="#d7d7d1")
        box = draw.textbbox((0, 0), day_name, font=header_font)
        draw.text((x + (cell_w - (box[2] - box[0])) / 2, start_y + 7), day_name, fill="#43463f", font=header_font)

    calendar_module = __import__("calendar")
    weeks = calendar_module.Calendar(firstweekday=calendar_module.MONDAY).monthdayscalendar(year, month)
    for week_index, week in enumerate(weeks):
        for column, day in enumerate(week):
            x = start_x + column * cell_w
            y = start_y + 52 + week_index * cell_h
            fill = "#fbfbf8" if day else "#f4f4f1"
            draw.rectangle([x, y, x + cell_w, y + cell_h], fill=fill, outline="#d7d7d1", width=2)
            if day == 0:
                continue

            entries = day_map.get(day, [])
            if capacity and len(entries) < capacity:
                draw.rectangle([x + 3, y + 3, x + cell_w - 3, y + cell_h - 3], outline="#c79a62", width=2)

            draw.rectangle([x + 8, y + 8, x + 46, y + 34], fill="#ecece7")
            day_text = str(day)
            day_box = draw.textbbox((0, 0), day_text, font=day_font)
            draw.text((x + 27 - (day_box[2] - day_box[0]) / 2, y + 10), day_text, fill="#2f312d", font=day_font)

            for line_index, entry in enumerate(entries[:4]):
                top = y + 42 + line_index * 24
                title_text = entry["title"]
                if len(title_text) > 15:
                    title_text = f"{title_text[:15]}..."
                draw.text((x + 10, top), f"{title_text}{_comment_badge(entry['comment'])}", fill="#384039", font=text_font)
                if entry["comment"]:
                    comment_text = entry["comment"]
                    if len(comment_text) > 18:
                        comment_text = f"{comment_text[:18]}..."
                    draw.text((x + 18, top + 12), comment_text, fill="#6a6d66", font=comment_font)

    footer = f"Generated by Shifter-Sync / {year}-{str(month).zfill(2)}"
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(((width - (footer_box[2] - footer_box[0])) // 2, height - 28), footer, fill="#777973", font=footer_font)
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
