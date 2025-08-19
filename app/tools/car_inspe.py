from flask import Blueprint, render_template, request, send_file
from werkzeug.utils import secure_filename
from flask_login import login_required
import os, tempfile, csv, re, shutil, platform
import pytesseract
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import fitz  # PyMuPDF
from datetime import datetime
from io import BytesIO
import unicodedata
import zipfile
from zipfile import ZipInfo
import chardet

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")

COORD_PRESETS = {
    "旧車検証_電子PDF": {
        "dpi": 300,
        "size": (2480, 3509),
        "regions": {
            "reg_number": (345, 305, 1500, 410),
            "expiry_date": (1880, 530, 2300, 675),
        }
    },
    "新車検証_電子PDF": {
        "dpi": 600,
        "size": (4960, 7018),
        "regions": {
            "reg_number": (1320, 715, 3000, 895),
            "expiry_date": (3800, 1110, 4700, 1300),
        }
    },
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
        pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
        if not os.path.exists(pytesseract.pytesseract.tesseract_cmd):
            return False
    tessdata_dir = os.path.join(app_root, "tessdata")
    if os.path.exists(tessdata_dir):
        os.environ['TESSDATA_PREFIX'] = tessdata_dir
    return True

def is_valid_reg_number(reg_number):
    """
    登録番号が、末尾最大4桁の数字＋その前にひらがながある形式であるか判定。
    """
    reg_number = reg_number.strip()
    digits = ''
    for i in range(1, len(reg_number) + 1):
        c = reg_number[-i]
        if c.isdigit():
            digits = c + digits
            if len(digits) >= 4:
                break
        else:
            break

    if not digits:
        return False

    idx = len(reg_number) - len(digits) - 1
    if idx < 0:
        return False

    prev_char = reg_number[idx]
    return is_hiragana(prev_char)

def is_hiragana(char):
    """
    Unicodeカテゴリでひらがな判定を行う
    """
    return 'HIRAGANA' in unicodedata.name(char, '')

def correct_ocr_text(text):
    replacements = {
        "S": "8", "O": "0", "I": "1", "l": "1", "B": "8", "D": "0",
        "m": "日", "n": "日", "」": "月", "』": "月", "'": "月",
        "牛": "年", "于": "千", "干": "千",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    return text

def parse_expiry_date(raw_text: str):
    raw_text = raw_text.replace(" ", "").replace("　", "")
    wareki_match = re.search(r"(令和\s*\d+年)", raw_text)
    if not wareki_match:
        return None, "和暦（例: 令和7年）が見つかりません"
    wareki_part = wareki_match.group(1)
    wareki_num = re.search(r"令和\s*(\d+)", wareki_part)
    if not wareki_num:
        return None, "和暦の年数が読み取れません"
    year = 2018 + int(wareki_num.group(1))
    remain = raw_text[wareki_match.end():]
    number_match = re.findall(r"(\d{1,2})[^\d]{0,3}", remain)
    if len(number_match) < 2:
        return None, f"月・日が正しく見つかりません（検出数: {len(number_match)}）"
    try:
        month = int(number_match[0])
        day = int(number_match[1])
        date_str = f"{year:04}{month:02}{day:02}"
        return date_str, None
    except:
        return None, "月日を数値として解釈できません"

def send_text_file(text, filename="error.txt"):
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, 'w', encoding="utf-8") as tmp:
        tmp.write(text)
    return send_file(path, as_attachment=True, download_name=filename)

def create_zip_with_logs(zip_path, file_paths, log_lines):
    print(f"[DEBUG] ZIPファイル作成: {zip_path}")
    print(f"[DEBUG] ファイル数: {file_paths}")
    print(f"[DEBUG] ログ行数: {len(log_lines)}")
    """
    日本語ファイル名対応のZIPファイルを作成する（ファイルベース）
    """
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
        # PDFファイルを追加
        for file_path in file_paths:
            arcname = os.path.basename(file_path)
            print(f"[DEBUG] アーカイブ名: {arcname}")
            
            zip_info = zipfile.ZipInfo(arcname)
            zip_info.compress_type = zipfile.ZIP_DEFLATED
            
            # ファイルのタイムスタンプを設定
            stat = os.stat(file_path)
            zip_info.date_time = datetime.fromtimestamp(stat.st_mtime).timetuple()[:6]
            
            with open(file_path, 'rb') as f:
                zipf.writestr(zip_info, f.read())

        # ログファイルを追加
        if log_lines:
            log_name = "処理ログ.txt"
            log_info = zipfile.ZipInfo(log_name)
            log_info.flag_bits |= 0x800  # UTF-8フラグを設定
            log_info.compress_type = zipfile.ZIP_DEFLATED
            log_info.date_time = datetime.now().timetuple()[:6]
            
            log_content = "\n".join(log_lines).encode("utf-8")
            zipf.writestr(log_info, log_content)

def extract_with_preset(pdf_path, preset_name):
    if preset_name not in COORD_PRESETS:
        raise ValueError(f"プリセット「{preset_name}」が未定義です。")
    preset = COORD_PRESETS[preset_name]
    dpi = preset["dpi"]
    regions = preset["regions"]
    doc = fitz.open(pdf_path)
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = BytesIO(pix.tobytes("png"))
    img = Image.open(img_bytes).convert("RGB")
    draw = ImageDraw.Draw(img)
    result = {}
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", 24)
    except:
        font = None
    for key, box in regions.items():
        region = img.crop(box).convert("L")
        region = ImageEnhance.Contrast(region).enhance(2.0)
        bw = region.point(lambda x: 0 if x < 190 else 255, '1')
        text = pytesseract.image_to_string(bw, lang='jpn', config="--psm 7").strip()
        original = text
        text = correct_ocr_text(text)
        print(f"[補正前] {original} → [補正後] {text}")
        result[key] = text
        draw.rectangle(box, outline="red", width=3)
        if text:
            draw.text((box[0], box[1] - 30), f"{key}: {text}", fill="red", font=font)
    debug_path = os.path.join(tempfile.gettempdir(), "debug_ocr_area.png")
    img.save(debug_path)
    print(f"[DEBUG] OCR領域の可視化画像を保存: {debug_path}")
    return result

def try_all_presets(pdf_path, user_preset):
    preset_order = [user_preset] + [p for p in COORD_PRESETS if p != user_preset]

    for preset_name in preset_order:
        try:
            print(f"[DEBUG] 試行中プリセット: {preset_name}")
            ocr_result = extract_with_preset(pdf_path, preset_name)

            reg_number = ocr_result.get("reg_number", "").replace("　", " ").replace(" ", "")
            raw_date = ocr_result.get("expiry_date", "")

            if not is_valid_reg_number(reg_number):
                print(f"[DEBUG] 登録番号の形式が無効: {reg_number}")
                continue

            formatted_date, err = parse_expiry_date(raw_date)
            if not formatted_date:
                print(f"[DEBUG] 満了日の解釈に失敗: {err}")
                continue

            return ocr_result, formatted_date, preset_name

        except Exception as e:
            print(f"[DEBUG] プリセット {preset_name} でエラー: {e}")
            continue

    return None, None, None

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
            pdf_files = request.files.getlist("pdf_files")
            csv_file = request.files.get("csv_file")
            preset = request.form.get("preset", "旧車検証_電子PDF")
            if not pdf_files or not csv_file:
                return send_text_file("PDFとCSVの両方をアップロードしてください。")
            temp_dir = tempfile.mkdtemp()
            csv_path = os.path.join(temp_dir, secure_filename(csv_file.filename))
            csv_file.save(csv_path)
            matching_data = {}
            for enc in ['utf-8', 'shift_jis', 'cp932', 'utf-8-sig']:
                try:
                    with open(csv_path, newline='', encoding=enc) as f:
                        reader = csv.reader(f)
                        for row in reader:
                            if len(row) < 3:
                                continue
                            csv_id = row[0].strip()
                            csv_suffix = row[1].strip().zfill(4)
                            csv_location = row[2].strip()
                            matching_data[csv_suffix] = {'id': csv_id, 'location': csv_location}
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
                    # ここでユーザーが選んだプリセットを渡す
                    ocr_result, formatted_date, used_preset = try_all_presets(pdf_path, preset)
                    if not ocr_result:
                        fail_logs.append(f"{pdf_filename}: すべてのプリセットで読み取りに失敗")
                        continue

                    print(f"[DEBUG] 使用されたプリセット: {used_preset}")  # デバッグ用にログを出力

                    reg_number = ocr_result.get("reg_number", "").replace("　", " ").replace(" ", "")
                    raw_date = ocr_result.get("expiry_date", "")
                    if not reg_number or not raw_date:
                        fail_logs.append(f"{pdf_filename}: 登録番号または満了日が読み取れません")
                        continue

                    formatted_date, err = parse_expiry_date(raw_date)
                    if not formatted_date:
                        fail_logs.append(f"{pdf_filename}: 満了日が解釈できません - {err}")
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
                print("[DEBUG] 成功したファイル数:", len(success_files))
                print("[DEBUG] 失敗ログ数:", len(fail_logs))
                zip_path = os.path.join(temp_dir, "renamed_files.zip")
                create_zip_with_logs(zip_path, success_files, fail_logs)
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