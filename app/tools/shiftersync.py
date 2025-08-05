from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import os
import csv
import io
from collections import defaultdict, Counter
from werkzeug.utils import secure_filename

from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageDraw, ImageFont
import calendar


shiftersync_bp = Blueprint("shiftersync", __name__, url_prefix="/tools/shiftersync")

nowdir = os.getcwd()

# トップページ：新規作成かアップロード選択
@shiftersync_bp.route("/", methods=["GET", "POST"])
def shiftersync():
    result = None
    if request.method == "POST":
        # 新規作成 or CSVアップロードを選択する処理
        action = request.form.get("action")
        if action == "create":
            return redirect(url_for("shiftersync.create"))
        elif action == "upload":
            return redirect(url_for("shiftersync.upload"))
        elif action == "check":
            return redirect(url_for("shiftersync.check"))
        elif action == "calendar":
            return redirect(url_for("shiftersync.calendar_view"))
    return render_template("shiftersync.html", result=result)

# 新規シフト作成ページ
@shiftersync_bp.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        # 新規シフト作成処理
        year = request.form["year"]
        month = request.form["month"]
        mode = request.form["mode"]
        name = request.form["name"]
        # シフトデータを生成または保存する処理
        return redirect(url_for("shiftersync.create"))
    return render_template("ss_create.html")

@shiftersync_bp.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".csv"):
            # ファイルを保存
            file_path = os.path.join("app", "storage", "shiftersync", file.filename)
            file.save(file_path)
            
            # ファイルをUTF-8で読み込む
            data = []
            try:
                with open(file_path, mode="r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        data.append(row)
            except UnicodeDecodeError:
                return "文字コードエラー: このアプリで作成されたファイルのみ対応しています", 400

            return render_template("ss_upload.html", data=data)
    return render_template("ss_upload.html")


@shiftersync_bp.route("/check", methods=["GET", "POST"])
def check():
    if request.method == "GET":
        return render_template("ss_check.html")

    files = request.files.getlist('csv_files')
    if not files or len(files) > 6:
        return jsonify({"error": "1～6件のCSVファイルをアップロードしてください"})

    mode, year, month = None, None, None
    file_targets = []  # 各ファイルの対象名
    shift_data = defaultdict(lambda: [[] for _ in range(len(files))])  # {日: [[人A,人B],[],[]]}

    for file_index, file in enumerate(files):
        filename = secure_filename(file.filename)

        try:
            content = file.read().decode('utf-8-sig')
        except Exception:
            return jsonify({"error": f"{filename} の読み込みに失敗しました"})

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        if len(rows) < 2:
            return jsonify({"error": f"{filename} の行数が不足しています"})

        header = rows[0]
        if len(header) < 4:
            return jsonify({"error": f"{filename} のヘッダーが不正です（4列未満）"})

        f_mode = header[0].strip().lower().lstrip('\ufeff')
        if f_mode not in ("scene", "person"):
            return jsonify({"error": f"{filename} のモードが不正です（scene または person）"})

        try:
            f_year = int(header[1])
            f_month = int(header[2])
        except ValueError:
            return jsonify({"error": f"{filename} の年月が整数ではありません"})

        f_target = header[3].strip()

        if mode is None:
            mode, year, month = f_mode, f_year, f_month
        elif (f_mode, f_year, f_month) != (mode, year, month):
            return jsonify({"error": f"{filename} は他のファイルとモード・年月が一致していません"})

        file_targets.append(f_target)

        for row in rows[2:]:
            if not row or not row[0].strip().isdigit():
                continue
            try:
                day = int(row[0].strip())
            except ValueError:
                continue

            entries = [e.strip() for e in row[1:] if e.strip()]
            shift_data[day][file_index].extend(entries)

    all_dates = sorted(shift_data.keys())
    result_matrix = {d: shift_data[d] for d in all_dates}

    # 重複検出：同日に同じ名前が複数ファイルに出現
    conflict_set = set()
    for day in all_dates:
        name_to_files = defaultdict(set)
        for f_index, names in enumerate(shift_data[day]):
            for name in names:
                name_to_files[name].add(f_index)

        for name, f_indexes in name_to_files.items():
            if len(f_indexes) > 1:
                conflict_set.add((day, name))

    conflicts = [{"date": d, "name": n} for (d, n) in conflict_set]

    return jsonify({
        "mode": mode,
        "year": year,
        "month": month,
        "targets": file_targets,
        "dates": all_dates,
        "matrix": result_matrix,
        "conflicts": conflicts
    })


UPLOAD_FOLDER = 'app/static/calendar_outputs'
@shiftersync_bp.route('/calendar', methods=['GET', 'POST'])
def calendar_view():
    if request.method == 'POST':
        file = request.files.get('csvfile')
        format_type = request.form.get('format')  # 'pdf' or 'png'

        if not file or not format_type:
            flash("ファイルと出力形式を選択してください")
            return render_template('ss_calendar.html')

        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        # === CSVパースと情報抽出 ===
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            try:
                # 1行目：メタ情報（モード、年、月、対象名）
                header = next(reader)
                mode = header[0].strip().lower()
                year = int(header[1])
                month = int(header[2])
                target_name = header[3]

                # 2行目：カラムヘッダー（例：日付, 内容）をスキップ
                next(reader)

                # 残りのデータ処理
                day_map = {}  # day(int): content(list)
                for row in reader:
                    if len(row) >= 2:
                        try:
                            day = int(row[0])
                            value = [v.strip() for v in row[1:] if v.strip()]
                            day_map[day] = value
                        except ValueError:
                            continue  # 無効な行はスキップ（例：空行や誤入力）

            except Exception as e:
                flash(f"CSVの読み込み中にエラーが発生しました: {e}")
                return render_template('ss_calendar.html')

        # === 出力ファイル名生成 ===
        output_filename = f"{year}-{str(month).zfill(2)}_カレンダー_{mode}.{format_type}"
        output_path = os.path.join(UPLOAD_FOLDER, output_filename)

        try:
            if format_type == 'pdf':
                generate_pdf_calendar(output_path, year, month, mode, target_name, day_map)
            elif format_type == 'png':
                generate_png_calendar(output_path, year, month, mode, target_name, day_map)
        except Exception as e:
            flash(f"カレンダー出力時にエラーが発生しました: {e}")
            return render_template('ss_calendar.html')

        return render_template(
            'ss_calendar.html',
            image_file=output_filename if format_type == 'png' else None,
            pdf_file=output_filename if format_type == 'pdf' else None
        )

    return render_template('ss_calendar.html')



def generate_pdf_calendar(path, year, month, mode, title, day_map):
    c = canvas.Canvas(path, pagesize=landscape(A4))
    width, height = landscape(A4)

    # 日本語フォント設定 (reportlabで日本語を使うためにはカスタムフォントが必要)
    try:
        font = "./app/static/fonts/NotoSansJP-VariableFont_wght.ttf"
        pdfmetrics.registerFont(TTFont('Noto', font))
        font_size = 20
        c.setFont('Noto', font_size)
    except:
        print("フォントの読み込みに失敗しました。デフォルトフォントを使用します。")

    c.drawCentredString(width / 2, height - 40, f"{year}年{month}月 {title} シフト表")

    start_x = 40
    start_y = height - 100
    cell_w = (width - 80) / 7
    cell_h = 100

    # 曜日ラベル（月曜始まり）
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    for i, day in enumerate(weekdays):
        color = colors.black
        if i == 5:  # 土曜日
            color = colors.blue
        elif i == 6:  # 日曜日
            color = colors.red
        c.setFillColor(color)
        c.drawString(start_x + i * cell_w + 5, start_y + 10, day)

    c.setFillColor(colors.black)

    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    weeks = cal.monthdayscalendar(year, month)

    for w, week in enumerate(weeks):
        for d, day in enumerate(week):
            x = start_x + d * cell_w
            y = start_y - ((w + 1) * cell_h)
            if day != 0:
                # セル背景色（土日）
                if d == 5:  # 土曜日
                    c.setFillColor(HexColor("#87cefa"))
                    c.rect(x, y, cell_w, cell_h, fill=1)
                elif d == 6:  # 日曜日
                    c.setFillColor(HexColor("#ffa07a"))
                    c.rect(x, y, cell_w, cell_h, fill=1)
                else:
                    c.setFillColor(colors.white)
                    c.rect(x, y, cell_w, cell_h, fill=1)

                # 日付番号
                c.setFillColor(colors.black)
                c.drawString(x + 4, y + cell_h - 15, str(day))

                # 内容
                content_lines = day_map.get(day, [])
                for i, line in enumerate(content_lines):
                    text = line.strip()
                    c.setFont('Noto', 15)
                    if i < 3:
                        text_x = x + 5
                        text_y = y + cell_h - 33 - i * 15
                    else:
                        text_x = x + cell_w / 2 + 5
                        text_y = y + cell_h - 33 - (i - 3) * 15
                    c.drawString(text_x, text_y, text)


    c.save()

def generate_png_calendar(path, year, month, mode, title, day_map):
    width = 1123
    height = 794
    cell_w = width // 7
    cell_h = (height - 160) // 6

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # 日本語フォント設定
    try:
        font_title = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 36)
        font_day = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 24)
        font_text = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 18)
    except IOError:
        print("フォントの読み込みに失敗しました。デフォルトフォントを使用します。")
        font_title = ImageFont.load_default()
        font_day = ImageFont.load_default()
        font_text = ImageFont.load_default()

    draw.text((width // 2 - 200, 30), f"{year}年{month}月 {title} シフト表", fill="black", font=font_title)

    # 曜日ラベル（月曜始まり）
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    for i, day in enumerate(weekdays):
        color = "black"
        if i == 5:  # 土曜日
            color = "blue"
        elif i == 6:  # 日曜日
            color = "red"
        draw.text((i * cell_w + 10, 90), day, fill=color, font=font_day)

    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    weeks = cal.monthdayscalendar(year, month)

    for w, week in enumerate(weeks):
        for d, day in enumerate(week):
            x = d * cell_w
            y = 120 + w * cell_h
            if day != 0:
                # セル背景色（土日）
                if d == 5:  # 土曜日
                    draw.rectangle([x, y, x + cell_w, y + cell_h], fill="#87cefa")
                elif d == 6:  # 日曜日
                    draw.rectangle([x, y, x + cell_w, y + cell_h], fill="#ffa07a")
                else:
                    draw.rectangle([x, y, x + cell_w, y + cell_h], fill="white")

                # --- 枠線は常に描画 ---
                draw.rectangle([x, y, x + cell_w, y + cell_h], outline="black", width=2)

                # 日付番号
                draw.text((x + 5, y + 5), str(day), fill="black", font=font_day)

                # 内容
                content_lines = day_map.get(day, [])
                for i, line in enumerate(content_lines):
                    text = line.strip()
                    if i < 3:
                        text_x = x + 10
                        text_y = y + 30 + i * 18
                    else:
                        text_x = x + cell_w // 2 + 10
                        text_y = y + 30 + (i - 3) * 18
                    draw.text((text_x, text_y), text, fill="black", font=font_text)


    img.save(path)



# CSVダウンロードページ
@shiftersync_bp.route("/download/<filename>", methods=["GET"])
def download(filename):
    directory = os.path.join("app", "storage", "shiftersync")
    return send_from_directory(directory, filename)
