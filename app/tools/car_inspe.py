from flask import Blueprint, render_template, request, send_file
from werkzeug.utils import secure_filename
from flask_login import login_required
import os, tempfile, csv, re, shutil, platform
import pytesseract
from PIL import Image
import fitz  # PyMuPDF
from datetime import datetime
from io import BytesIO
from PIL import ImageDraw, ImageFont

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")

# OCRプリセット定義
COORD_PRESETS = {
    "電子PDF": {
        "dpi": 300,  # ← 高DPIに変更
        "size": (2480, 3509),
        "regions": {
            "reg_number": (345, 305, 1500, 410),  # ← 少し広めに調整済み
            "expiry_date": (1900, 550, 2360, 645),
        }
    },
    # 追加プリセットはここに記述
}

def setup_tesseract():
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    system = platform.system().lower()

    if system == "windows":
        tesseract_path = os.path.join(app_root, "binaries", "windows", "tesseract.exe")
        if os.path.exists(tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        else:
            return False
    else:
        # Linuxなどでは明示的に絶対パスを指定
        pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
        if not os.path.exists(pytesseract.pytesseract.tesseract_cmd):
            return False

    # 共通処理（tessdata_dirがあるなら優先して指定）
    tessdata_dir = os.path.join(app_root, "tessdata")
    if os.path.exists(tessdata_dir):
        os.environ['TESSDATA_PREFIX'] = tessdata_dir

    return True


def send_text_file(text, filename="error.txt"):
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, 'w', encoding="utf-8") as tmp:
        tmp.write(text)
    return send_file(path, as_attachment=True, download_name=filename)

def extract_with_preset(pdf_path, preset_name):
    if preset_name not in COORD_PRESETS:
        raise ValueError(f"プリセット「{preset_name}」が未定義です。")
    
    preset = COORD_PRESETS[preset_name]
    dpi = preset["dpi"]
    regions = preset["regions"]

    # PDF → 画像化
    doc = fitz.open(pdf_path)
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)

    # PILで読み込み
    img_bytes = BytesIO(pix.tobytes("png"))
    img = Image.open(img_bytes).convert("RGB")  # ← RGBモードで描画可能に
    
    # ↓ 以下の描画関連は削除 or コメントアウト
    # draw = ImageDraw.Draw(img)

    result = {}

    # ↓ フォント読み込みは描画のためのものなので不要
    # try:
    #     font = ImageFont.truetype("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", 24)
    # except:
    #     font = None

    for key, box in regions.items():
        region = img.crop(box).convert("L")
        bw = region.point(lambda x: 0 if x < 160 else 255, '1')
        text = pytesseract.image_to_string(bw, lang='jpn', config="--psm 7").strip()
        result[key] = text

        # ↓ OCR領域の赤枠・文字描画はすべて無効化
        # draw.rectangle(box, outline="red", width=3)
        # if text:
        #     draw.text((box[0], box[1] - 30), f"{key}: {text}", fill="red", font=font)

    # ↓ 画像保存処理を削除
    # debug_path = os.path.join(tempfile.gettempdir(), "debug_ocr_area.png")
    # img.save(debug_path)
    # print(f"[DEBUG] OCR領域の可視化画像を保存: {debug_path}")

    return result


def extract_suffix_digits(text, length=4):
    digits_reversed = []
    for char in reversed(text):
        if char.isdigit():
            digits_reversed.append(char)
        else:
            break
    digits = ''.join(reversed(digits_reversed))
    return digits.zfill(length)

@car_inspe_bp.route("/", methods=["GET", "POST"])
@login_required
def car_inspection():
    error = None
    temp_dir = None

    if request.method == "POST":
        try:
            if not setup_tesseract():
                return send_text_file("Tesseractが利用できません。")

            pdf_files = request.files.getlist("pdf_files")  # ← 複数取得
            csv_file = request.files.get("csv_file")
            preset = request.form.get("preset", "電子PDF")

            if not pdf_files or not csv_file:
                return send_text_file("PDFとCSVの両方をアップロードしてください。")

            temp_dir = tempfile.mkdtemp()
            csv_path = os.path.join(temp_dir, secure_filename(csv_file.filename))
            csv_file.save(csv_path)

            # CSV読み込み（全ファイルに使う）
            matching_data = {}
            encodings = ['utf-8', 'shift_jis', 'cp932', 'utf-8-sig']
            for encoding in encodings:
                try:
                    with open(csv_path, newline='', encoding=encoding) as f:
                        reader = csv.reader(f)
                        for row in reader:
                            if len(row) < 3:
                                continue
                            csv_id = row[0].strip()
                            csv_suffix = row[1].strip().zfill(4)
                            csv_location = row[2].strip()
                            matching_data[csv_suffix] = {
                                'id': csv_id,
                                'location': csv_location
                            }
                    break
                except UnicodeDecodeError:
                    continue

            success_files = []
            fail_logs = []

            for pdf_file in pdf_files:
                pdf_filename = secure_filename(pdf_file.filename)
                pdf_path = os.path.join(temp_dir, pdf_filename)
                pdf_file.save(pdf_path)

                try:
                    ocr_result = extract_with_preset(pdf_path, preset)
                    reg_number = ocr_result.get("reg_number", "").replace("　", " ").replace(" ", "")
                    raw_date = ocr_result.get("expiry_date", "")
                    if not reg_number or not raw_date:
                        fail_logs.append(f"{pdf_filename}: 登録番号または満了日が読み取れません")
                        continue

                    # 満了日整形（和暦対応）
                    wareki_match = re.search(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", raw_date)
                    if wareki_match:
                        year = 2018 + int(wareki_match.group(1))
                        month = int(wareki_match.group(2))
                        day = int(wareki_match.group(3))
                        formatted_date = datetime(year, month, day).strftime("%Y%m%d")
                    else:
                        fail_logs.append(f"{pdf_filename}: 満了日の形式が不明")
                        continue

                    suffix = extract_suffix_digits(reg_number)
                    if suffix not in matching_data:
                        fail_logs.append(f"{pdf_filename}: 登録番号末尾「{suffix}」がCSVに存在しません")
                        continue

                    info = matching_data[suffix]
                    new_filename = f"{formatted_date}_{info['id']}_{info['location']}_{reg_number}.pdf"
                    new_filename = re.sub(r'[<>:"/\\|?*]', '_', new_filename)
                    renamed_path = os.path.join(temp_dir, new_filename)
                    os.rename(pdf_path, renamed_path)
                    success_files.append(renamed_path)

                except Exception as e:
                    fail_logs.append(f"{pdf_filename}: 例外発生 - {str(e)}")

            if not success_files:
                return send_text_file("すべてのファイルで処理に失敗しました。\n\n" + "\n".join(fail_logs), filename="failure_log.txt")

            elif len(success_files) == 1:
                return send_file(success_files[0], as_attachment=True, download_name=os.path.basename(success_files[0]))

            else:
                zip_path = os.path.join(temp_dir, "renamed_files.zip")
                shutil.make_archive(zip_path[:-4], 'zip', root_dir=temp_dir, base_dir='.')
                log_txt_path = os.path.join(temp_dir, "処理ログ.txt")
                with open(log_txt_path, "w", encoding="utf-8") as logf:
                    logf.write("\n".join(fail_logs))
                with zipfile.ZipFile(zip_path, 'a') as zipf:
                    zipf.write(log_txt_path, arcname="処理ログ.txt")
                return send_file(zip_path, as_attachment=True, download_name="renamed_files.zip")

        except Exception as e:
            return send_text_file(f"処理中にエラーが発生しました:\n{str(e)}", filename="system_error.txt")

        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass

    return render_template("car_inspe.html", error=error)
