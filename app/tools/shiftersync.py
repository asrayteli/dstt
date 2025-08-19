from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import os
import csv
import io
from collections import defaultdict, Counter
from werkzeug.utils import secure_filename

import re
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
    file_capacities = []  # 各ファイルの台数設定
    shift_data = defaultdict(lambda: [[] for _ in range(len(files))])  # {日: [[人A,人B],[],[]]}

    # オプションマッピング定義
    option_mappings = {
        'A': '午前',
        'P': '午後',
        '1': '1号車',
        '2': '2号車',
        'E': '早番',
        'L': '遅番'
    }

    def parse_entry_for_display(entry):
        """エントリーを表示用に変換"""
        import re
        option_match = re.match(r'^!([^!]+)!(.+)$', entry)
        if option_match:
            option_key = option_match.group(1)
            name = option_match.group(2)
            option_text = option_mappings.get(option_key, option_key)
            return f"{option_text} {name}"
        return entry

    def parse_entry_for_comparison(entry):
        """エントリーを比較用に変換（名前部分のみ取得）"""
        import re
        option_match = re.match(r'^!([^!]+)!(.+)$', entry)
        if option_match:
            return option_match.group(2)  # 名前部分のみ
        return entry

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
        
        # 台数設定の読み込み（5番目の要素があれば）
        f_capacity = None
        if len(header) >= 5:
            try:
                f_capacity = int(header[4])
            except ValueError:
                f_capacity = None

        if mode is None:
            mode, year, month = f_mode, f_year, f_month
        elif (f_mode, f_year, f_month) != (mode, year, month):
            return jsonify({"error": f"{filename} は他のファイルとモード・年月が一致していません"})

        file_targets.append(f_target)
        file_capacities.append(f_capacity)

        for row in rows[2:]:
            if not row or not row[0].strip().isdigit():
                continue
            try:
                day = int(row[0].strip())
            except ValueError:
                continue

            entries = [e.strip() for e in row[1:] if e.strip()]
            
            # エントリーを処理（表示用と比較用を両方保存）
            processed_entries = []
            for entry in entries:
                display_entry = parse_entry_for_display(entry)
                comparison_name = parse_entry_for_comparison(entry)
                processed_entries.append({
                    'original': entry,
                    'display': display_entry,
                    'comparison': comparison_name
                })
            
            shift_data[day][file_index].extend(processed_entries)

    # 重複検出：同じオプション+名前 または オプションなしとの重複
    conflict_set = set()
    for day in shift_data.keys():
        # ファイル別のエントリーを収集 {(オプション, 名前): [ファイルインデックス]}
        entry_to_files = defaultdict(set)
        
        for f_index, entries in enumerate(shift_data[day]):
            for entry in entries:
                original = entry['original']
                comparison_name = entry['comparison']
                
                # オプション部分を取得
                import re
                option_match = re.match(r'^!([^!]+)!(.+)$', original)
                if option_match:
                    option_key = option_match.group(1)
                    entry_key = (option_key, comparison_name)
                else:
                    # オプションなしの場合は特別なキー
                    entry_key = (None, comparison_name)
                
                entry_to_files[entry_key].add(f_index)

        # 重複判定
        for (option, name), f_indexes in entry_to_files.items():
            if len(f_indexes) > 1:
                # 同じオプション+名前で複数ファイルに存在
                conflict_set.add((day, option, name))
            
            # オプションなしの場合は、同じ名前の他のオプションとも競合
            if option is None:
                for other_entry_key, other_f_indexes in entry_to_files.items():
                    other_option, other_name = other_entry_key
                    if other_option is not None and other_name == name and len(other_f_indexes.intersection(f_indexes)) == 0:
                        # 異なるファイルで同じ名前のオプション付きエントリーがある
                        if len(other_f_indexes) > 0:
                            conflict_set.add((day, other_option, other_name))
                            conflict_set.add((day, None, name))

    # JavaScriptで使いやすい形式に変換
    conflicts = []
    for (day, option, name) in conflict_set:
        if option is None:
            conflicts.append({"date": day, "entry": name})
        else:
            conflicts.append({"date": day, "entry": f"!{option}!{name}"})

    # 全日付を生成（1日から月末まで）
    import calendar
    days_in_month = calendar.monthrange(year, month)[1]
    all_possible_dates = list(range(1, days_in_month + 1))
    
    # データがない日付も含めて結果マトリックスを作成
    complete_matrix = {}
    for day in all_possible_dates:
        if day in shift_data:
            complete_matrix[day] = shift_data[day]
        else:
            # データがない日は空のリストで埋める
            complete_matrix[day] = [[] for _ in range(len(files))]

    return jsonify({
        "mode": mode,
        "year": year,
        "month": month,
        "targets": file_targets,
        "capacities": file_capacities,
        "dates": all_possible_dates,
        "matrix": complete_matrix,
        "conflicts": conflicts,
        "option_mappings": option_mappings
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
                # 1行目：メタ情報（モード、年、月、対象名、台数設定）
                header = next(reader)
                mode = header[0].strip().lower()
                year = int(header[1])
                month = int(header[2])
                target_name = header[3]
                
                # 台数設定の読み込み（5番目の要素があれば）
                capacity = None
                if len(header) >= 5:
                    try:
                        capacity = int(header[4])
                    except ValueError:
                        capacity = None

                # 2行目：カラムヘッダー（例：日付, 内容）をスキップ
                next(reader)

                # オプションマッピング定義
                option_mappings = {
                    'A': '午前',
                    'P': '午後',
                    '1': '1号車',
                    '2': '2号車',
                    'E': '早番',
                    'L': '遅番'
                }

                def parse_entry_for_display(entry):
                    """エントリーを表示用に変換"""
                    import re
                    option_match = re.match(r'^!([^!]+)!(.+)$', entry)
                    if option_match:
                        option_key = option_match.group(1)
                        name = option_match.group(2)
                        option_text = option_mappings.get(option_key, option_key)
                        return f"{option_text} {name}"
                    return entry

                # 残りのデータ処理
                day_map = {}  # day(int): content(list)
                for row in reader:
                    if len(row) >= 2:
                        try:
                            day = int(row[0])
                            raw_entries = [v.strip() for v in row[1:] if v.strip()]
                            # オプション付きエントリーを表示用に変換
                            display_entries = [parse_entry_for_display(entry) for entry in raw_entries]
                            day_map[day] = display_entries
                        except ValueError:
                            continue  # 無効な行はスキップ（例：空行や誤入力）

            except Exception as e:
                flash(f"CSVの読み込み中にエラーが発生しました: {e}")
                return render_template('ss_calendar.html')

        # === 出力ファイル名生成 ===
        output_filename = f"{year}-{str(month).zfill(2)}_カレンダー_{target_name}.{format_type}"
        output_path = os.path.join(UPLOAD_FOLDER, output_filename)

        try:
            if format_type == 'pdf':
                generate_pdf_calendar(output_path, year, month, mode, target_name, day_map, capacity)
            elif format_type == 'png':
                generate_png_calendar(output_path, year, month, mode, target_name, day_map, capacity)
        except Exception as e:
            flash(f"カレンダー出力時にエラーが発生しました: {e}")
            return render_template('ss_calendar.html')

        return render_template(
            'ss_calendar.html',
            image_file=output_filename if format_type == 'png' else None,
            pdf_file=output_filename if format_type == 'pdf' else None
        )

    return render_template('ss_calendar.html')


def generate_pdf_calendar(path, year, month, mode, title, day_map, capacity=None):
    c = canvas.Canvas(path, pagesize=landscape(A4))
    width, height = landscape(A4)

    # 日本語フォント設定
    try:
        font_path = "./app/static/fonts/NotoSansJP-VariableFont_wght.ttf"
        pdfmetrics.registerFont(TTFont('Noto', font_path))
        c.setFont('Noto', 24)
    except:
        print("フォントの読み込みに失敗しました。デフォルトフォントを使用します。")
        c.setFont('Helvetica', 20)

    # === 美しいヘッダーデザイン ===
    # メインヘッダー背景（グラデーション風）
    c.setFillColor(HexColor("#3b82f6"))
    c.rect(0, height - 80, width, 80, fill=1)
    
    # ヘッダー装飾ライン
    c.setFillColor(HexColor("#1b73ff"))
    c.rect(0, height - 85, width, 5, fill=1)
    
    # タイトル
    c.setFillColor(colors.white)
    title_text = f"{year}年{month}月 {title} シフト表"
    if capacity:
        title_text += f" (必要人数: {capacity}人/日)"
    c.drawCentredString(width / 2, height - 50, title_text)

    # === カレンダーグリッド設定 ===
    start_x = 40
    start_y = height - 120
    cell_w = (width - 80) / 7
    cell_h = 90

    # 曜日ヘッダー
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    for i, day in enumerate(weekdays):
        x = start_x + i * cell_w
        y = start_y
        
        # 曜日背景（グラデーション風）
        if i == 5:  # 土曜日
            c.setFillColor(HexColor("#3b82f6"))
        elif i == 6:  # 日曜日
            c.setFillColor(HexColor("#ef4444"))
        else:
            c.setFillColor(HexColor("#cdcefa"))
        
        c.rect(x, y, cell_w, 35, fill=1)
        
        # ヘッダー下部のアクセントライン
        c.setFillColor(HexColor("#1e293b"))
        c.rect(x, y - 3, cell_w, 3, fill=1)
        
        # 曜日の枠線を追加
        c.setStrokeColor(HexColor("#1e293b"))
        c.setLineWidth(1)
        c.rect(x, y, cell_w, 35, fill=0)
        
        # 曜日テキスト
        c.setFillColor(colors.black)
        c.setFont('Noto', 18)
        c.drawCentredString(x + cell_w/2, y + 8, day)

    # === カレンダー本体 ===
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    weeks = cal.monthdayscalendar(year, month)

    for w, week in enumerate(weeks):
        for d, day in enumerate(week):
            x = start_x + d * cell_w
            y = start_y - 90 - (w * cell_h)  # -35で曜日ヘッダー分を考慮
            
            if day != 0:
                # セル背景
                if d == 5:  # 土曜日
                    bg_color = HexColor("#eff6ff")
                elif d == 6:  # 日曜日
                    bg_color = HexColor("#fef2f2")
                else:
                    bg_color = colors.white
                
                c.setFillColor(bg_color)
                c.rect(x, y, cell_w, cell_h, fill=1)
                
                # 台数不足の警告背景
                content_lines = day_map.get(day, [])
                if capacity and len(content_lines) < capacity:
                    c.setFillColor(HexColor("#ffffff"))
                    c.rect(x + 2, y + 2, cell_w - 4, cell_h - 4, fill=1)
                
                # セルの枠線とシャドウ効果
                c.setStrokeColor(HexColor("#353535"))
                c.setLineWidth(2)
                c.rect(x, y, cell_w, cell_h, fill=0)
                
                # 内側の装飾ボーダー
                c.setStrokeColor(HexColor("#f3f4f6"))
                c.setLineWidth(1)
                c.rect(x + 1, y + 1, cell_w - 2, cell_h - 2, fill=0)
                
                # 日付番号（左上角の装飾的な配置）
                c.setFillColor(HexColor("#1e293b"))
                date_bg_w = 30
                date_bg_h = 20
                c.rect(x + 3, y + cell_h - date_bg_h - 3, date_bg_w, date_bg_h, fill=1)
                
                c.setFillColor(colors.white)
                c.setFont('Noto', 14)
                c.drawCentredString(x + 3 + date_bg_w/2, y + cell_h - 17, str(day))
                
                # コンテンツ表示
                c.setFont('Noto', 11)
                for i, line in enumerate(content_lines[:5]):
                    text_x = x + 8
                    text_y = y + cell_h - 35 - (i * 13)
                    
                    # オプション別の色分け
                    if "午前" in line or "早番" in line:
                        c.setFillColor(HexColor("#059669"))
                    elif "午後" in line or "遅番" in line:
                        c.setFillColor(HexColor("#dc2626"))
                    elif "号車" in line:
                        c.setFillColor(HexColor("#7c3aed"))
                    else:
                        c.setFillColor(HexColor("#374151"))
                    
                    # テキストが長い場合は省略
                    if len(line) > 12:
                        line = line[:10] + "..."
                    
                    c.drawString(text_x, text_y, line)

    # フッター装飾
    c.setFillColor(HexColor("#f3f4f6"))
    c.rect(0, 0, width, 30, fill=1)
    c.setFillColor(HexColor("#6b7280"))
    c.setFont('Noto', 10)
    c.drawCentredString(width / 2, 10, f"Generated by Shifter-Sync | {year}/{month}")

    c.save()


def generate_png_calendar(path, year, month, mode, title, day_map, capacity=None):
    # 高解像度設定 - PDFと同じ比率に調整
    width = 1600
    height = 1200
    cell_w = (width - 80) // 7
    cell_h = 140

    img = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(img)

    # フォント設定
    try:
        font_title = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 36)
        font_day = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 24)
        font_text = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 20)
        font_header = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 28)
        font_footer = ImageFont.truetype("./app/static/fonts/NotoSansJP-VariableFont_wght.ttf", 14)
    except IOError:
        print("フォントの読み込みに失敗しました。デフォルトフォントを使用します。")
        font_title = ImageFont.load_default()
        font_day = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_footer = ImageFont.load_default()

    # === 美しいヘッダー（PDFと統一）===
    # メインヘッダー背景
    draw.rectangle([0, 0, width, 120], fill="#3b82f6")
    
    # ヘッダー装飾ライン
    draw.rectangle([0, 120, width, 127], fill="#1b73ff")
    
    # タイトル
    title_text = f"{year}年{month}月 {title} シフト表"
    if capacity:
        title_text += f" (必要人数: {capacity}人/日)"
    
    bbox = draw.textbbox((0, 0), title_text, font=font_title)
    title_width = bbox[2] - bbox[0]
    draw.text(((width - title_width) // 2, 40), title_text, fill="white", font=font_title)

    # === 曜日ヘッダー（PDFと統一）===
    start_x = 40
    start_y = 150
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    
    for i, day in enumerate(weekdays):
        x = start_x + i * cell_w
        y = start_y
        
        # 曜日背景（グラデーション風）
        if i == 5:  # 土曜日
            color = "#3b82f6"
        elif i == 6:  # 日曜日
            color = "#ef4444"
        else:
            color = "#c4c5fd"
        
        draw.rectangle([x, y, x + cell_w, y + 50], fill=color)
        
        # ヘッダー下部のアクセントライン
        draw.rectangle([x, y + 50, x + cell_w, y + 55], fill="#1e293b")
        
        # 曜日テキスト
        bbox = draw.textbbox((0, 0), day, font=font_header)
        text_width = bbox[2] - bbox[0]
        draw.text((x + (cell_w - text_width) // 2, y + 5), day, fill="black", font=font_header)

    # === カレンダー本体（PDFと統一）===
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    weeks = cal.monthdayscalendar(year, month)

    for w, week in enumerate(weeks):
        for d, day in enumerate(week):
            x = start_x + d * cell_w
            y = start_y + 55 + w * cell_h
            
            if day != 0:
                # セル背景
                if d == 5:  # 土曜日
                    bg_color = "#eff6ff"
                elif d == 6:  # 日曜日
                    bg_color = "#fef2f2"
                else:
                    bg_color = "white"
                
                draw.rectangle([x, y, x + cell_w, y + cell_h], fill=bg_color)
                
                # 台数不足の警告背景
                content_lines = day_map.get(day, [])
                if capacity and len(content_lines) < capacity:
                    draw.rectangle([x + 3, y + 3, x + cell_w - 3, y + cell_h - 3], fill="#ffffff")
                
                # セルの枠線とシャドウ効果
                draw.rectangle([x, y, x + cell_w, y + cell_h], outline="#353535", width=3)
                
                # 内側の装飾ボーダー
                draw.rectangle([x + 1, y + 1, x + cell_w - 1, y + cell_h - 1], outline="#f3f4f6", width=1)
                
                # 日付番号（左上角の装飾的な配置 - PDFと統一）
                date_bg_w = 45
                date_bg_h = 30
                draw.rectangle([x + 5, y + 5, x + 5 + date_bg_w, y + 5 + date_bg_h], fill="#1e293b")
                
                day_str = str(day)
                bbox = draw.textbbox((0, 0), day_str, font=font_day)
                day_width = bbox[2] - bbox[0]
                
                # テキストを背景の中央に正確に配置（PDFの方式を参考）
                text_x = x + 5 + date_bg_w // 2
                text_y = y - 10 + date_bg_h // 2 - 2  # 少し上にオフセット
                
                # 中央揃えでテキストを描画
                draw.text((text_x - day_width // 2, text_y), day_str, fill="white", font=font_day)
                
                # コンテンツ表示
                for i, line in enumerate(content_lines[:5]):
                    text_x = x + 12
                    text_y = y + 45 + i * 20
                    
                    # オプション別の色分け（PDFと統一）
                    text_color = "#374151"
                    if "午前" in line or "早番" in line:
                        text_color = "#059669"
                    elif "午後" in line or "遅番" in line:
                        text_color = "#dc2626"
                    elif "号車" in line:
                        text_color = "#7c3aed"
                    
                    # テキストが長い場合は省略
                    if len(line) > 15:
                        line = line[:13] + "..."
                    
                    draw.text((text_x, text_y), line, fill=text_color, font=font_text)

    # フッター装飾（PDFと統一）
    draw.rectangle([0, height - 45, width, height], fill="#f3f4f6")
    footer_text = f"Generated by Shifter-Sync | {year}/{month}"
    bbox = draw.textbbox((0, 0), footer_text, font=font_footer)
    footer_width = bbox[2] - bbox[0]
    draw.text(((width - footer_width) // 2, height - 30), footer_text, fill="#6b7280", font=font_footer)

    img.save(path, "PNG", optimize=True, quality=95)



# CSVダウンロードページ
@shiftersync_bp.route("/download/<filename>", methods=["GET"])
def download(filename):
    directory = os.path.join("app", "storage", "shiftersync")
    return send_from_directory(directory, filename)
