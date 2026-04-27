from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
from flask_login import current_user, login_required
import os, tempfile, csv, re, shutil, platform, logging, uuid, json, time, base64
from app.access_control import is_admin_user
try:
    import pytesseract
except ImportError:  # pragma: no cover - production dependency guard
    pytesseract = None
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageOps
import fitz  # PyMuPDF
from datetime import datetime
from io import BytesIO
import unicodedata
import zipfile
from zipfile import ZipInfo
import chardet

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")
logger = logging.getLogger(__name__)
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAR_INSPE_WORK_DIR = os.path.join(APP_ROOT, "..", "var", "car_inspe")
PRESET_STORE_PATH = os.path.join(CAR_INSPE_WORK_DIR, "presets.json")
SESSION_TTL_SECONDS = 60 * 60 * 6

COORD_PRESETS = {}
DEFAULT_MANUAL_DPI = 300
DEFAULT_FILENAME_TEMPLATE = "{expiry}_{vehicle_id}_{location}_{registration}.pdf"
CSV_FIELD_ALIASES = {
    "vehicle_id": ("契約コード", "契約CD", "契約ID", "id", "ID", "車両ID", "管理ID", "管理番号", "車番ID", "車両番号ID"),
    "registration": ("登録番号", "車両番号", "ナンバー", "自動車登録番号", "標識番号"),
    "suffix": ("下4桁", "末尾4桁", "登録番号末尾4桁", "車番末尾", "番号下4桁"),
    "location": ("現場名", "保管場所", "拠点", "拠点名", "営業所", "営業所名", "駐車場", "場所"),
}


def ensure_work_dir():
    os.makedirs(CAR_INSPE_WORK_DIR, exist_ok=True)


def cleanup_old_sessions():
    ensure_work_dir()
    now = time.time()
    for name in os.listdir(CAR_INSPE_WORK_DIR):
        path = os.path.join(CAR_INSPE_WORK_DIR, name)
        if not os.path.isdir(path) or not (name.startswith("session_") or name.startswith("preview_")):
            continue
        try:
            if now - os.path.getmtime(path) > SESSION_TTL_SECONDS:
                shutil.rmtree(path)
        except OSError:
            logger.debug("古い車検証セッションの削除に失敗: %s", path, exc_info=True)


def user_session_dir(session_id):
    ensure_work_dir()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", session_id or "")
    if not safe_id:
        raise ValueError("セッションIDが不正です。")
    return os.path.join(CAR_INSPE_WORK_DIR, f"session_{safe_id}")


def preview_session_dir(preview_id):
    ensure_work_dir()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", preview_id or "")
    if not safe_id:
        raise ValueError("プレビューIDが不正です。")
    return os.path.join(CAR_INSPE_WORK_DIR, f"preview_{safe_id}")


def read_preset_store():
    ensure_work_dir()
    if not os.path.exists(PRESET_STORE_PATH):
        return {}
    try:
        with open(PRESET_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("車検証プリセットの読み込みに失敗しました。", exc_info=True)
        return {}


def write_preset_store(data):
    ensure_work_dir()
    with open(PRESET_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def all_presets():
    presets = dict(COORD_PRESETS)
    for name, value in read_preset_store().items():
        if isinstance(value, dict) and "regions" in value and "dpi" in value:
            presets[name] = value
    return presets


def first_preset_name():
    presets = all_presets()
    return next(iter(presets), "")

def setup_tesseract():
    if pytesseract is None:
        return False
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

    tessdata_candidates = [
        os.path.join(app_root, "binaries", "windows", "tessdata"),
        os.path.join(app_root, "tessdata"),
    ]
    for tessdata_dir in tessdata_candidates:
        if os.path.exists(tessdata_dir):
            os.environ["TESSDATA_PREFIX"] = tessdata_dir
            break
    return True

def is_valid_reg_number(reg_number):
    """
    登録番号が、末尾最大4桁の数字＋その前にひらがながある形式であるか判定。
    """
    reg_number = clean_registration_number(reg_number)
    digits = ''
    for i in range(1, len(reg_number) + 1):
        c = reg_number[-i]
        if c.isdigit():
            digits = c + digits
            if len(digits) >= 4:
                break
        else:
            break

    if len(digits) < 2:
        return False

    idx = len(reg_number) - len(digits) - 1
    if idx < 0:
        return False

    return any(is_hiragana(char) for char in reg_number[: len(reg_number) - len(digits)])

def is_hiragana(char):
    """
    Unicodeカテゴリでひらがな判定を行う
    """
    return 'HIRAGANA' in unicodedata.name(char, '')

def normalize_ocr_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", text)


def correct_ocr_text(text, field=None):
    text = normalize_ocr_text(text)
    common_replacements = {
        "O": "0", "o": "0", "D": "0", "Q": "0",
        "I": "1", "l": "1", "|": "1",
        "S": "8", "B": "8",
    }
    date_replacements = {
        "m": "日", "n": "日", "」": "月", "』": "月", "'": "月",
        "牛": "年", "干": "年", "于": "年", "午": "年",
        "合": "令", "命": "令", "禾口": "和", "利": "和",
    }
    replacements = dict(common_replacements)
    if field == "expiry_date":
        replacements.update(date_replacements)
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    return text


def clean_registration_number(text: str) -> str:
    text = correct_ocr_text(text, "reg_number")
    text = text.replace("-", "").replace("ー", "").replace("－", "")
    text = re.sub(r"[^\wぁ-んァ-ン一-龥]", "", text)

    # OCRが前後の見出しまで拾った場合でも、末尾数字を持つ最もそれらしい断片を使う。
    candidates = re.findall(r"[一-龥]{1,6}\d{2,4}[ぁ-ん][\d-]{2,5}", text)
    if candidates:
        return candidates[-1].replace("-", "")
    return text


def parse_expiry_date(raw_text: str):
    raw_text = correct_ocr_text(raw_text, "expiry_date")
    raw_text = raw_text.replace("有効期間の満了する日", "").replace("満了する日", "")

    def valid_date(year: int, month: int, day: int):
        try:
            datetime(year, month, day)
            return f"{year:04}{month:02}{day:02}", None
        except ValueError:
            return None, f"日付として不正です: {year}/{month}/{day}"

    compact = re.search(r"(20\d{2})(\d{2})(\d{2})", raw_text)
    if compact:
        return valid_date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))

    seireki = re.search(r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", raw_text)
    if seireki:
        return valid_date(int(seireki.group(1)), int(seireki.group(2)), int(seireki.group(3)))

    era_offsets = {
        "令和": 2018,
        "R": 2018,
        "平成": 1988,
        "H": 1988,
        "昭和": 1925,
        "S": 1925,
    }
    era_pattern = r"(令和|平成|昭和|R|H|S)(元|\d{1,2})年?(\d{1,2})月?(\d{1,2})日?"
    era_match = re.search(era_pattern, raw_text)
    if era_match:
        era, year_raw, month_raw, day_raw = era_match.groups()
        era_year = 1 if year_raw == "元" else int(year_raw)
        year = era_offsets[era] + era_year
        return valid_date(year, int(month_raw), int(day_raw))

    wareki_match = re.search(r"(令和|平成|昭和|R|H|S)(元|\d{1,2})", raw_text)
    if not wareki_match:
        return None, "和暦または西暦の日付が見つかりません"
    era, year_raw = wareki_match.groups()
    era_year = 1 if year_raw == "元" else int(year_raw)
    year = era_offsets[era] + era_year
    remain = raw_text[wareki_match.end():]
    number_match = re.findall(r"\d{1,2}", remain)
    if len(number_match) < 2:
        return None, f"月・日が正しく見つかりません（検出数: {len(number_match)}）"
    try:
        month = int(number_match[0])
        day = int(number_match[1])
        return valid_date(year, month, day)
    except ValueError:
        return None, "月日を数値として解釈できません"

def _resize_image(image, scale):
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    return image.resize((width, height), resampling)


def _prepare_region_variants(region):
    gray = ImageOps.grayscale(region)
    gray = ImageOps.autocontrast(gray)
    variants = []

    for scale in (2.0, 3.0):
        base = _resize_image(gray, scale)
        base = ImageEnhance.Contrast(base).enhance(2.2)
        base = ImageEnhance.Sharpness(base).enhance(1.8)
        variants.append(base)
        variants.append(base.filter(ImageFilter.MedianFilter(size=3)))

        for threshold in (145, 165, 185, 205):
            binary = base.point(lambda px, t=threshold: 0 if px < t else 255, "1").convert("L")
            variants.append(binary)

    padded = []
    for image in variants:
        padded.append(ImageOps.expand(image, border=18, fill=255))
    return padded


def _ocr_image_variants(region, field):
    if field == "expiry_date":
        configs = [
            "--oem 1 --psm 7",
            "--oem 1 --psm 6",
            "--oem 1 --psm 11",
            "--oem 1 --psm 13",
        ]
    else:
        configs = [
            "--oem 1 --psm 7",
            "--oem 1 --psm 8",
            "--oem 1 --psm 6",
            "--oem 1 --psm 13",
        ]

    texts = []
    for image in _prepare_region_variants(region):
        for config in configs:
            try:
                text = pytesseract.image_to_string(image, lang="jpn+eng", config=config).strip()
            except Exception as exc:
                logger.debug("OCR failed for %s with %s: %s", field, config, exc)
                continue
            if text:
                texts.append(correct_ocr_text(text, field))
    return texts


def _score_registration_candidate(text):
    cleaned = clean_registration_number(text)
    if not cleaned:
        return -100, cleaned
    trailing_digits = re.search(r"(\d{2,4})$", cleaned)
    if not trailing_digits:
        return -50, cleaned

    score = len(trailing_digits.group(1)) * 10
    if any(is_hiragana(char) for char in cleaned):
        score += 40
    if re.search(r"[一-龥]{1,6}\d{2,4}[ぁ-ん]\d{2,4}$", cleaned):
        score += 40
    if 6 <= len(cleaned) <= 16:
        score += 10
    if len(cleaned) > 24:
        score -= 20
    return score, cleaned


def _score_date_candidate(text):
    formatted, _err = parse_expiry_date(text)
    if not formatted:
        return -100, None
    score = 80
    normalized = correct_ocr_text(text, "expiry_date")
    if "令和" in normalized or "平成" in normalized or re.search(r"20\d{2}", normalized):
        score += 20
    if "年" in normalized and "月" in normalized:
        score += 10
    return score, formatted


def _best_candidate(texts, field):
    best_score = -999
    best_value = ""
    raw_value = ""
    for text in texts:
        if field == "reg_number":
            score, value = _score_registration_candidate(text)
        else:
            score, value = _score_date_candidate(text)
        if value and score > best_score:
            best_score = score
            best_value = value
            raw_value = text
    return best_value, raw_value, best_score


def _render_first_page(pdf_path, dpi):
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
    img_bytes = BytesIO(pix.tobytes("png"))
    return Image.open(img_bytes).convert("RGB")


def _load_image(image_path):
    return Image.open(image_path).convert("RGB")


def _save_pdf_first_page_image(pdf_path, dpi, image_path):
    image = _render_first_page(pdf_path, dpi)
    image.save(image_path, format="PNG", optimize=True)
    return image


def _clip_box(box, image_size):
    max_width, max_height = image_size
    left, top, right, bottom = [int(round(float(value))) for value in box]
    clipped = (
        max(0, min(max_width, left)),
        max(0, min(max_height, top)),
        max(0, min(max_width, right)),
        max(0, min(max_height, bottom)),
    )
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        raise ValueError("OCR範囲が画像の範囲外です。")
    return clipped


def image_to_data_url(image, max_width=1100):
    preview = image.copy()
    if preview.width > max_width:
        preview = _resize_image(preview, max_width / preview.width)
    output = BytesIO()
    preview.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def image_to_preview_payload(image, max_width=1100):
    source_width, source_height = image.size
    preview = image.copy()
    scale = 1.0
    if preview.width > max_width:
        scale = max_width / preview.width
        preview = _resize_image(preview, scale)
    output = BytesIO()
    preview.save(output, format="PNG", optimize=True)
    return {
        "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
        "source_width": source_width,
        "source_height": source_height,
        "preview_width": preview.width,
        "preview_height": preview.height,
        "scale": scale,
    }


def dpi_for_preset(preset_name):
    preset = all_presets().get(preset_name or "")
    try:
        return int((preset or {}).get("dpi", DEFAULT_MANUAL_DPI))
    except (TypeError, ValueError):
        return DEFAULT_MANUAL_DPI


def draw_preview_with_boxes(pdf_path, preset, boxes=None, image_path=None):
    preset = preset or {"dpi": DEFAULT_MANUAL_DPI, "regions": {}}
    dpi = int(preset.get("dpi", DEFAULT_MANUAL_DPI))
    img = _load_image(image_path) if image_path and os.path.exists(image_path) else _render_first_page(pdf_path, dpi)
    draw = ImageDraw.Draw(img)
    regions = boxes or preset.get("regions", {})
    colors = {"reg_number": "#2563eb", "expiry_date": "#dc2626"}
    for key, box in regions.items():
        clipped_box = _clip_box(box, img.size)
        draw.rectangle(clipped_box, outline=colors.get(key, "#f59e0b"), width=5)
        draw.text((clipped_box[0], max(0, clipped_box[1] - 34)), key, fill=colors.get(key, "#f59e0b"))
    return image_to_data_url(img)


def preview_pdf_payload(pdf_path, preset_name, image_path=None):
    presets = all_presets()
    if preset_name and preset_name not in presets:
        raise ValueError("存在しないOCRプリセットです。")
    preset = presets.get(preset_name) or {"dpi": DEFAULT_MANUAL_DPI, "regions": {}}
    dpi = int(preset.get("dpi", DEFAULT_MANUAL_DPI))
    image = _load_image(image_path) if image_path and os.path.exists(image_path) else _render_first_page(pdf_path, dpi)
    payload = image_to_preview_payload(image)
    payload.update({
        "preset": preset_name,
        "dpi": dpi,
        "regions": preset.get("regions", {}),
    })
    return payload


def extract_with_preset(pdf_path, preset_name, custom_regions=None, image_path=None):
    presets = all_presets()
    if preset_name and preset_name not in presets:
        raise ValueError(f"プリセット「{preset_name}」が未定義です。")
    preset = presets.get(preset_name) or {"dpi": DEFAULT_MANUAL_DPI, "regions": {}}
    dpi = int(preset.get("dpi", DEFAULT_MANUAL_DPI))
    regions = custom_regions or preset.get("regions") or {}
    if set(regions) != {"reg_number", "expiry_date"}:
        raise ValueError("登録番号と満了日のOCR範囲を指定してください。")
    img = _load_image(image_path) if image_path and os.path.exists(image_path) else _render_first_page(pdf_path, dpi)
    draw = ImageDraw.Draw(img)
    result = {}
    raw_result = {}
    scores = {}
    candidates_by_field = {}
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", 24)
    except:
        font = None
    for key, box in regions.items():
        clipped_box = _clip_box(box, img.size)
        region = img.crop(clipped_box)
        candidates = _ocr_image_variants(region, key)
        text, raw_text, score = _best_candidate(candidates, key)
        result[key] = text
        raw_result[key] = raw_text
        scores[key] = score
        candidates_by_field[key] = sorted(set(candidates), key=lambda item: _best_candidate([item], key)[2], reverse=True)[:5]
        logger.info("車検証OCR %s preset=%s score=%s raw=%s result=%s", key, preset_name, score, raw_text, text)
        draw.rectangle(clipped_box, outline="red", width=3)
        if text:
            draw.text((clipped_box[0], max(0, clipped_box[1] - 30)), f"{key}: {text}", fill="red", font=font)
    debug_path = os.path.join(tempfile.gettempdir(), "debug_ocr_area.png")
    img.save(debug_path)
    logger.info("OCR領域の可視化画像を保存: %s", debug_path)
    result["_raw"] = raw_result
    result["_scores"] = scores
    result["_candidates"] = candidates_by_field
    result["_regions"] = {key: list(value) for key, value in regions.items()}
    result["_preview"] = image_to_data_url(img)
    return result

def try_all_presets(pdf_path, user_preset, custom_regions=None, image_path=None):
    presets = all_presets()
    if custom_regions and (not user_preset or user_preset not in presets):
        preset_order = [""]
    else:
        preset_order = [user_preset] + [p for p in presets if p != user_preset]
    best_attempt = None

    for preset_name in preset_order:
        try:
            logger.info("車検証OCRプリセット試行: %s", preset_name)
            ocr_result = extract_with_preset(
                pdf_path,
                preset_name,
                custom_regions if preset_name == user_preset or not preset_name else None,
                image_path=image_path,
            )

            reg_number = clean_registration_number(ocr_result.get("reg_number", ""))
            raw_date = ocr_result.get("expiry_date", "")
            formatted_date, err = parse_expiry_date(raw_date)

            attempt_score = 0
            if is_valid_reg_number(reg_number):
                attempt_score += 100
            if formatted_date:
                attempt_score += 100
            if best_attempt is None or attempt_score > best_attempt[0]:
                best_attempt = (attempt_score, ocr_result, formatted_date, preset_name, err)

            if not is_valid_reg_number(reg_number):
                logger.info("登録番号の形式が無効: %s", reg_number)
                continue

            if not formatted_date:
                logger.info("満了日の解釈に失敗: %s", err)
                continue

            return ocr_result, formatted_date, preset_name

        except Exception as e:
            logger.exception("プリセット %s でOCRエラー", preset_name)
            continue

    if best_attempt and best_attempt[0] >= 200:
        _score, ocr_result, formatted_date, preset_name, _err = best_attempt
        if formatted_date:
            return ocr_result, formatted_date, preset_name
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


def detect_csv_encoding(file_bytes):
    detected = chardet.detect(file_bytes)
    candidates = [detected.get("encoding"), "utf-8-sig", "utf-8", "cp932", "shift_jis"]
    seen = set()
    for enc in candidates:
        if not enc or enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            return file_bytes.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError("CSVの文字コードを判定できません。")


def normalize_header(value):
    return normalize_ocr_text(value).lower().replace("_", "").replace("-", "")


def looks_like_header(row):
    joined = ",".join(row)
    return any(alias in joined for aliases in CSV_FIELD_ALIASES.values() for alias in aliases)


def find_column(headers, field):
    normalized_headers = [normalize_header(header) for header in headers]
    for alias in CSV_FIELD_ALIASES[field]:
        normalized_alias = normalize_header(alias)
        for idx, header in enumerate(normalized_headers):
            if normalized_alias and normalized_alias in header:
                return idx
    return None


def parse_vehicle_csv(file_bytes):
    decoded, encoding = detect_csv_encoding(file_bytes)
    sample = decoded[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(decoded.splitlines(), dialect) if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("CSVにデータがありません。")

    has_header = looks_like_header(rows[0])
    headers = rows[0] if has_header else ["契約コード", "登録番号末尾4桁", "現場名"]
    data_rows = rows[1:] if has_header else rows
    columns = {
        "vehicle_id": find_column(headers, "vehicle_id"),
        "registration": find_column(headers, "registration"),
        "suffix": find_column(headers, "suffix"),
        "location": find_column(headers, "location"),
    }
    if not has_header:
        columns.update({"vehicle_id": 0, "suffix": 1, "location": 2})

    if columns["vehicle_id"] is None:
        columns["vehicle_id"] = 0
    if columns["location"] is None and len(headers) >= 3:
        columns["location"] = 2
    if columns["suffix"] is None and columns["registration"] is None:
        columns["suffix"] = 1 if len(headers) >= 2 else None

    entries = []
    for index, row in enumerate(data_rows, 1):
        def get(col):
            return row[col].strip() if col is not None and col < len(row) else ""

        registration = clean_registration_number(get(columns["registration"]))
        suffix = re.sub(r"\D", "", get(columns["suffix"]))
        if registration and not suffix:
            suffix = extract_suffix_digits(registration)
        if suffix:
            suffix = suffix[-4:].zfill(4)
        entry = {
            "row": index + (1 if has_header else 0),
            "vehicle_id": get(columns["vehicle_id"]),
            "registration": registration,
            "suffix": suffix,
            "location": get(columns["location"]),
            "raw": row,
        }
        if entry["vehicle_id"] or entry["registration"] or entry["suffix"] or entry["location"]:
            entries.append(entry)

    return {
        "encoding": encoding,
        "has_header": has_header,
        "headers": headers,
        "columns": columns,
        "entries": entries,
    }


def match_vehicle(registration, csv_entries):
    registration = clean_registration_number(registration)
    suffix = extract_suffix_digits(registration)
    matches = []
    for entry in csv_entries:
        score = 0
        if entry.get("registration") and clean_registration_number(entry["registration"]) == registration:
            score += 120
        if entry.get("suffix") and entry["suffix"] == suffix:
            score += 80
        if entry.get("registration") and entry["registration"] and entry["registration"] in registration:
            score += 20
        if score:
            matches.append({**entry, "score": score})
    matches.sort(key=lambda item: item["score"], reverse=True)
    best = matches[0] if matches else None
    return {
        "suffix": suffix,
        "best": best,
        "candidates": matches[:8],
        "status": "matched" if best else "unmatched",
    }


def sanitize_filename_part(value, fallback="unknown"):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"_+", "_", text).strip(" ._")
    return text or fallback


def normalize_expiry_for_filename(value):
    parsed, _err = parse_expiry_date(str(value or ""))
    if parsed:
        return parsed
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(value or "")))
    return digits[:8] if len(digits) >= 8 else ""


def build_output_filename(row, template=DEFAULT_FILENAME_TEMPLATE):
    expiry = normalize_expiry_for_filename(row.get("expiry_date"))
    registration = clean_registration_number(row.get("registration_number", ""))
    values = {
        "expiry": sanitize_filename_part(expiry, "満了日未確認"),
        "vehicle_id": sanitize_filename_part(row.get("vehicle_id"), "契約コード未確認"),
        "location": sanitize_filename_part(row.get("location"), "現場名未確認"),
        "registration": sanitize_filename_part(registration, "登録番号未確認"),
        "original": sanitize_filename_part(os.path.splitext(row.get("original_name", ""))[0], "source"),
        "today": datetime.now().strftime("%Y%m%d"),
    }
    template = (template or DEFAULT_FILENAME_TEMPLATE).strip()
    try:
        filename = template.format(**values)
    except (KeyError, ValueError):
        filename = DEFAULT_FILENAME_TEMPLATE.format(**values)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    filename = sanitize_filename_part(filename, "車検証.pdf")
    return filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"


def session_manifest_path(session_dir):
    return os.path.join(session_dir, "manifest.json")


def save_manifest(session_dir, manifest):
    with open(session_manifest_path(session_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def load_manifest(session_id):
    session_dir = user_session_dir(session_id)
    path = session_manifest_path(session_dir)
    if not os.path.exists(path):
        raise ValueError("解析セッションが見つかりません。もう一度アップロードしてください。")
    with open(path, "r", encoding="utf-8") as f:
        return session_dir, json.load(f)


def preview_manifest_path(preview_dir):
    return os.path.join(preview_dir, "manifest.json")


def save_preview_manifest(preview_dir, manifest):
    with open(preview_manifest_path(preview_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def load_preview_manifest(preview_id):
    preview_dir = preview_session_dir(preview_id)
    path = preview_manifest_path(preview_dir)
    if not os.path.exists(path):
        raise ValueError("プレビューセッションが見つかりません。")
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    owner = manifest.get("owner", "")
    current_owner = getattr(current_user, "username", "")
    if owner and owner != current_owner:
        raise ValueError("このプレビューにはアクセスできません。")
    return preview_dir, manifest


def remove_preview_session(preview_id):
    if not preview_id:
        return
    try:
        shutil.rmtree(preview_session_dir(preview_id), ignore_errors=True)
    except ValueError:
        return


def send_file_and_cleanup(path, session_dir, **kwargs):
    response = send_file(path, **kwargs)

    @response.call_on_close
    def _cleanup_session():
        shutil.rmtree(session_dir, ignore_errors=True)

    return response


def parse_regions_payload(payload):
    regions = {}
    source = payload or {}
    for key in ("reg_number", "expiry_date"):
        box = source.get(key)
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            coords = [int(float(value)) for value in box]
        except (TypeError, ValueError):
            continue
        if coords[2] > coords[0] and coords[3] > coords[1]:
            regions[key] = coords
    return regions or None


def analyze_pdf_to_row(pdf_path, original_name, preset_name, csv_entries, custom_regions=None, image_path=None):
    ocr_result, formatted_date, used_preset = try_all_presets(pdf_path, preset_name, custom_regions, image_path=image_path)
    presets = all_presets()
    preset = presets.get(used_preset or preset_name, presets.get(preset_name))
    if not ocr_result:
        preview_regions = custom_regions or (preset.get("regions") if preset else {})
        preview = draw_preview_with_boxes(pdf_path, preset, boxes=preview_regions, image_path=image_path) if preview_regions or image_path else ""
        return {
            "file_id": os.path.basename(pdf_path),
            "original_name": original_name,
            "status": "needs_review",
            "registration_number": "",
            "expiry_date": "",
            "vehicle_id": "",
            "location": "",
            "match_status": "unmatched",
            "match_candidates": [],
            "preset": used_preset or preset_name,
            "regions": custom_regions or (preset.get("regions") if preset else {}),
            "preview": preview,
            "candidate_texts": {},
            "confidence": 0,
            "message": "OCRで登録番号または満了日を読み取れませんでした。",
        }

    registration = clean_registration_number(ocr_result.get("reg_number", ""))
    match = match_vehicle(registration, csv_entries)
    best = match["best"] or {}
    row = {
        "file_id": os.path.basename(pdf_path),
        "original_name": original_name,
        "status": "ready" if registration and formatted_date and best else "needs_review",
        "registration_number": registration,
        "expiry_date": formatted_date or "",
        "vehicle_id": best.get("vehicle_id", ""),
        "location": best.get("location", ""),
        "match_status": match["status"],
        "match_suffix": match["suffix"],
        "match_candidates": match["candidates"],
        "preset": used_preset,
        "regions": ocr_result.get("_regions", {}),
        "preview": ocr_result.get("_preview", ""),
        "candidate_texts": ocr_result.get("_candidates", {}),
        "raw_text": ocr_result.get("_raw", {}),
        "confidence": min(100, max(0, int((ocr_result.get("_scores", {}).get("reg_number", 0) + ocr_result.get("_scores", {}).get("expiry_date", 0)) / 2))),
        "message": "",
    }
    row["output_name"] = build_output_filename(row)
    return row


@car_inspe_bp.route("/api/presets", methods=["GET"])
@login_required
def api_presets():
    presets = all_presets()
    return jsonify({
        "presets": [
            {
                "name": name,
                "dpi": preset.get("dpi", DEFAULT_MANUAL_DPI),
                "regions": preset.get("regions", {}),
                "custom": True,
            }
            for name, preset in presets.items()
        ],
        "default": first_preset_name(),
        "can_manage": is_admin_user(),
    })


@car_inspe_bp.route("/api/presets", methods=["POST"])
@login_required
def api_save_preset():
    if not is_admin_user():
        return jsonify({"error": "プリセットを保存できるのは管理者のみです。"}), 403
    payload = request.get_json(silent=True) or {}
    name = normalize_ocr_text(payload.get("name", ""))
    if not name:
        return jsonify({"error": "プリセット名を入力してください。"}), 400
    regions = parse_regions_payload(payload.get("regions"))
    if not regions or set(regions) != {"reg_number", "expiry_date"}:
        return jsonify({"error": "登録番号と満了日の範囲を両方指定してください。"}), 400
    try:
        dpi = int(payload.get("dpi") or DEFAULT_MANUAL_DPI)
    except (TypeError, ValueError):
        dpi = DEFAULT_MANUAL_DPI
    data = read_preset_store()
    data[name] = {"dpi": dpi, "regions": regions}
    write_preset_store(data)
    return jsonify({"success": True, "name": name, "preset": data[name]})


@car_inspe_bp.route("/api/preview", methods=["POST"])
@login_required
def api_preview_pdf():
    pdf_file = request.files.get("pdf_file")
    preset = request.form.get("preset", "")
    if not pdf_file or not pdf_file.filename:
        return jsonify({"error": "プレビューするPDFを選択してください。"}), 400
    if preset and preset not in all_presets():
        return jsonify({"error": "存在しないOCRプリセットです。"}), 400

    cleanup_old_sessions()
    preview_id = uuid.uuid4().hex
    preview_dir = preview_session_dir(preview_id)
    os.makedirs(preview_dir, exist_ok=True)
    original_name = pdf_file.filename
    safe_name = secure_filename(original_name) or "preview.pdf"
    preview_path = os.path.join(preview_dir, safe_name)
    image_path = os.path.join(preview_dir, "page_001.png")
    try:
        pdf_file.save(preview_path)
        dpi = dpi_for_preset(preset)
        image = _save_pdf_first_page_image(preview_path, dpi, image_path)
        save_preview_manifest(preview_dir, {
            "preview_id": preview_id,
            "created_at": time.time(),
            "owner": getattr(current_user, "username", ""),
            "original_name": original_name,
            "pdf_path": preview_path,
            "image_path": image_path,
            "preset": preset,
            "dpi": dpi,
        })
        payload = image_to_preview_payload(image)
        payload.update({
            "preview_id": preview_id,
            "preset": preset,
            "dpi": dpi,
            "regions": (all_presets().get(preset) or {}).get("regions", {}),
        })
        return jsonify(payload)
    except Exception as exc:
        logger.exception("車検証PDFプレビュー生成に失敗しました。")
        shutil.rmtree(preview_dir, ignore_errors=True)
        return jsonify({"error": f"PDFプレビューを生成できませんでした: {exc}"}), 400


@car_inspe_bp.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    cleanup_old_sessions()
    if not setup_tesseract():
        return jsonify({"error": "Tesseractが利用できません。"}), 500

    pdf_files = [f for f in request.files.getlist("pdf_files") if f and f.filename]
    csv_file = request.files.get("csv_file")
    preset = request.form.get("preset", "")
    if not pdf_files:
        return jsonify({"error": "PDFを選択してください。"}), 400
    if not csv_file:
        return jsonify({"error": "CSVを選択してください。"}), 400
    if preset and preset not in all_presets():
        return jsonify({"error": "存在しないOCRプリセットです。"}), 400
    try:
        custom_regions = parse_regions_payload(json.loads(request.form.get("regions") or "{}"))
        file_regions_payload = json.loads(request.form.get("file_regions") or "[]")
        preview_ids = json.loads(request.form.get("preview_ids") or "[]")
    except json.JSONDecodeError:
        return jsonify({"error": "OCR範囲指定の形式が不正です。"}), 400
    region_mode = request.form.get("region_mode", "all")
    if region_mode not in {"all", "per_file"}:
        return jsonify({"error": "OCR範囲の適用方法が不正です。"}), 400
    if not isinstance(file_regions_payload, list):
        return jsonify({"error": "PDFごとのOCR範囲指定の形式が不正です。"}), 400
    if not isinstance(preview_ids, list):
        return jsonify({"error": "プレビュー指定の形式が不正です。"}), 400
    if region_mode == "per_file" and not preset:
        missing_indexes = [
            str(index + 1)
            for index in range(len(pdf_files))
            if index >= len(file_regions_payload) or not parse_regions_payload(file_regions_payload[index])
        ]
        if missing_indexes:
            return jsonify({"error": f"{'、'.join(missing_indexes)}件目の登録番号と満了日のOCR範囲を指定してください。"}), 400
    elif not preset and not custom_regions:
        return jsonify({"error": "プリセットを選ぶか、PDF上でOCR範囲を指定してください。"}), 400

    try:
        csv_info = parse_vehicle_csv(csv_file.read())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    session_id = uuid.uuid4().hex
    session_dir = user_session_dir(session_id)
    os.makedirs(session_dir, exist_ok=True)

    rows = []
    files = {}
    used_preview_ids = []
    for index, pdf_file in enumerate(pdf_files):
        original_name = pdf_file.filename
        safe_name = secure_filename(original_name) or f"vehicle_{index + 1}.pdf"
        file_id = f"{index + 1:03d}_{uuid.uuid4().hex[:8]}_{safe_name}"
        pdf_path = os.path.join(session_dir, file_id)
        image_path = os.path.join(session_dir, f"{os.path.splitext(file_id)[0]}.png")
        preview_manifest = None
        preview_id = preview_ids[index] if index < len(preview_ids) else None
        if preview_id:
            try:
                _preview_dir, preview_manifest = load_preview_manifest(preview_id)
            except ValueError:
                preview_manifest = None

        if preview_manifest and os.path.exists(preview_manifest.get("pdf_path", "")) and os.path.exists(preview_manifest.get("image_path", "")):
            original_name = preview_manifest.get("original_name") or original_name
            shutil.copyfile(preview_manifest["pdf_path"], pdf_path)
            shutil.copyfile(preview_manifest["image_path"], image_path)
            used_preview_ids.append(preview_id)
        else:
            pdf_file.save(pdf_path)
            _save_pdf_first_page_image(pdf_path, dpi_for_preset(preset), image_path)

        files[file_id] = {"original_name": original_name, "path": pdf_path, "image_path": image_path, "dpi": dpi_for_preset(preset)}
        per_file_regions = None
        if region_mode == "per_file" and index < len(file_regions_payload):
            per_file_regions = parse_regions_payload(file_regions_payload[index])
        active_regions = per_file_regions or custom_regions
        try:
            row = analyze_pdf_to_row(pdf_path, original_name, preset, csv_info["entries"], active_regions, image_path=image_path)
        except Exception as exc:
            logger.exception("車検証PDF解析に失敗: %s", original_name)
            row = {
                "file_id": file_id,
                "original_name": original_name,
                "status": "needs_review",
                "registration_number": "",
                "expiry_date": "",
                "vehicle_id": "",
                "location": "",
                "match_status": "unmatched",
                "match_candidates": [],
                "preset": preset,
                "regions": active_regions or (all_presets().get(preset, {}).get("regions", {})),
                "preview": "",
                "candidate_texts": {},
                "confidence": 0,
                "message": f"解析中にエラーが発生しました: {exc}",
        }
        rows.append(row)

    manifest = {
        "session_id": session_id,
        "created_at": time.time(),
        "owner": getattr(current_user, "username", ""),
        "preset": preset,
        "files": files,
        "csv": csv_info,
    }
    save_manifest(session_dir, manifest)
    for preview_id in set(used_preview_ids):
        remove_preview_session(preview_id)

    return jsonify({
        "session_id": session_id,
        "rows": rows,
        "csv": {
            "encoding": csv_info["encoding"],
            "has_header": csv_info["has_header"],
            "headers": csv_info["headers"],
            "columns": csv_info["columns"],
            "count": len(csv_info["entries"]),
        },
        "filename_template": DEFAULT_FILENAME_TEMPLATE,
    })


@car_inspe_bp.route("/api/reanalyze", methods=["POST"])
@login_required
def api_reanalyze():
    payload = request.get_json(silent=True) or {}
    try:
        session_dir, manifest = load_manifest(payload.get("session_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    file_id = os.path.basename(payload.get("file_id", ""))
    file_info = manifest.get("files", {}).get(file_id)
    if not file_info:
        return jsonify({"error": "対象PDFが見つかりません。"}), 404
    preset = payload.get("preset") or manifest.get("preset") or ""
    if preset and preset not in all_presets():
        return jsonify({"error": "存在しないOCRプリセットです。"}), 400
    custom_regions = parse_regions_payload(payload.get("regions"))
    if not preset and not custom_regions:
        return jsonify({"error": "プリセットを選ぶか、OCR範囲を指定してください。"}), 400
    if not setup_tesseract():
        return jsonify({"error": "Tesseractが利用できません。"}), 500
    row = analyze_pdf_to_row(
        file_info["path"],
        file_info["original_name"],
        preset,
        manifest.get("csv", {}).get("entries", []),
        custom_regions,
        image_path=file_info.get("image_path"),
    )
    return jsonify({"row": row})


@car_inspe_bp.route("/api/match", methods=["POST"])
@login_required
def api_match():
    payload = request.get_json(silent=True) or {}
    try:
        _session_dir, manifest = load_manifest(payload.get("session_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    registration = payload.get("registration_number", "")
    match = match_vehicle(registration, manifest.get("csv", {}).get("entries", []))
    return jsonify({"match": match})


@car_inspe_bp.route("/api/finalize", methods=["POST"])
@login_required
def api_finalize():
    payload = request.get_json(silent=True) or {}
    try:
        session_dir, manifest = load_manifest(payload.get("session_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    rows = payload.get("rows") or []
    if not rows:
        return jsonify({"error": "出力対象がありません。"}), 400
    template = payload.get("filename_template") or DEFAULT_FILENAME_TEMPLATE
    selected_rows = [row for row in rows if row.get("selected", True)]
    if not selected_rows:
        return jsonify({"error": "出力対象が選択されていません。"}), 400

    output_items = []
    for row in selected_rows:
        file_id = os.path.basename(row.get("file_id", ""))
        file_info = manifest.get("files", {}).get(file_id)
        if not file_info or not os.path.exists(file_info["path"]):
            return jsonify({"error": f"{row.get('original_name', file_id)} の元PDFが見つかりません。"}), 404
        normalized_row = {
            **row,
            "registration_number": clean_registration_number(row.get("registration_number", "")),
            "expiry_date": normalize_ocr_text(row.get("expiry_date", "")),
            "vehicle_id": row.get("vehicle_id", ""),
            "location": row.get("location", ""),
        }
        filename = build_output_filename(normalized_row, template)
        output_items.append((file_info["path"], filename))

    if len(output_items) == 1:
        source_path, filename = output_items[0]
        return send_file_and_cleanup(source_path, session_dir, as_attachment=True, download_name=filename)

    zip_path = os.path.join(session_dir, "vehicle_inspection_output.zip")
    used_names = {}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for source_path, filename in output_items:
            arcname = filename
            if arcname in used_names:
                used_names[arcname] += 1
                root, ext = os.path.splitext(filename)
                arcname = f"{root}_{used_names[filename]}{ext}"
            else:
                used_names[arcname] = 1
            info = ZipInfo(arcname)
            info.flag_bits |= 0x800
            info.compress_type = zipfile.ZIP_DEFLATED
            info.date_time = datetime.now().timetuple()[:6]
            with open(source_path, "rb") as f:
                zipf.writestr(info, f.read())
    return send_file_and_cleanup(zip_path, session_dir, as_attachment=True, download_name="vehicle_inspection_output.zip")


@car_inspe_bp.route("/", methods=["GET"])
@login_required
def car_inspection():
    return render_template("car_inspe.html", error=None)
