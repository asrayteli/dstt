from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
from flask_login import current_user, login_required
import os, tempfile, csv, re, shutil, platform, logging, uuid, json, time, base64, threading
from concurrent.futures import ThreadPoolExecutor
from app.access_control import is_admin_user
from app.models import SiteContractMaster, VehicleInspectionRecord, db
from app.site_contract_master import ensure_contract_master_synced
try:
    import pytesseract
except ImportError:  # pragma: no cover - production dependency guard
    pytesseract = None
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageOps
import fitz  # PyMuPDF
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import unicodedata
import zipfile
from zipfile import ZipInfo
import chardet
import numpy as np
from sqlalchemy import or_

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")
logger = logging.getLogger(__name__)
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAR_INSPE_WORK_DIR = os.path.join(APP_ROOT, "..", "var", "car_inspe")
CAR_INSPE_STORAGE_DIR = os.path.join(CAR_INSPE_WORK_DIR, "storage")
PRESET_STORE_PATH = os.path.join(CAR_INSPE_WORK_DIR, "presets.json")
SESSION_TTL_SECONDS = 60 * 60 * 6

COORD_PRESETS = {}
DEFAULT_MANUAL_DPI = 300
DEFAULT_FILENAME_TEMPLATE = "{満了日}_{契約コード}_{現場名}_{登録番号}.pdf"

# Tesseract セットアップは初回呼び出しのみ実施し、以降はキャッシュを使う。
_TESSERACT_READY = None
_TESSERACT_LOCK = threading.Lock()
_TESSERACT_WARMED = False
# OCR 実行用の共有スレッドプール。Tesseract はサブプロセス呼び出しなので
# I/O 待ち中に GIL を解放するため ThreadPool で並列化が有効。
_OCR_WORKER_COUNT = max(2, min(8, (os.cpu_count() or 4)))
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=_OCR_WORKER_COUNT, thread_name_prefix="car_inspe_ocr")
CSV_FIELD_ALIASES = {
    "vehicle_id": ("契約コード", "契約CD", "契約ID", "id", "ID", "車両ID", "管理ID", "管理番号", "車番ID", "車両番号ID"),
    "registration": ("登録番号", "車両番号", "ナンバー", "自動車登録番号", "標識番号"),
    "suffix": ("下4桁", "末尾4桁", "登録番号末尾4桁", "車番末尾", "番号下4桁"),
    "location": ("現場名", "保管場所", "拠点", "拠点名", "営業所", "営業所名", "駐車場", "場所"),
}
EXTRA_OCR_FIELDS = ("first_registration_month", "passenger_capacity", "displacement")


def ensure_work_dir():
    os.makedirs(CAR_INSPE_WORK_DIR, exist_ok=True)
    os.makedirs(CAR_INSPE_STORAGE_DIR, exist_ok=True)


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

def _detect_tesseract():
    if pytesseract is None:
        return False, "pytesseract モジュールがインストールされていません。"
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    system = platform.system().lower()
    if system == "windows":
        tesseract_path = os.path.join(app_root, "binaries", "windows", "tesseract.exe")
        if os.path.exists(tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        else:
            return False, f"Tesseract 実行ファイルが見つかりません: {tesseract_path}"
    else:
        pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
        if not os.path.exists(pytesseract.pytesseract.tesseract_cmd):
            return False, "Tesseract 実行ファイル (/usr/bin/tesseract) が見つかりません。"

    tessdata_candidates = [
        os.path.join(app_root, "binaries", "windows", "tessdata"),
        os.path.join(app_root, "tessdata"),
    ]
    for tessdata_dir in tessdata_candidates:
        if os.path.exists(tessdata_dir):
            os.environ["TESSDATA_PREFIX"] = tessdata_dir
            break
    return True, ""


def setup_tesseract():
    global _TESSERACT_READY
    if _TESSERACT_READY is True:
        return True
    with _TESSERACT_LOCK:
        if _TESSERACT_READY is True:
            return True
        ok, reason = _detect_tesseract()
        _TESSERACT_READY = ok
        if not ok:
            logger.warning("Tesseract 初期化失敗: %s", reason)
        return ok


def setup_tesseract_with_reason():
    """初期化結果を理由付きで返す。利用者向けエラーメッセージに使う。"""
    global _TESSERACT_READY
    with _TESSERACT_LOCK:
        ok, reason = _detect_tesseract()
        _TESSERACT_READY = ok
        return ok, reason


def warmup_tesseract():
    """Tesseract バイナリと言語データを起動時に予熱する。
    初回 OCR の遅延を抑えるため、tessdata を OS のファイルキャッシュに乗せる。
    """
    global _TESSERACT_WARMED
    if _TESSERACT_WARMED or pytesseract is None:
        return
    try:
        if not setup_tesseract():
            return
        dummy = Image.new("L", (60, 24), 255)
        ImageDraw.Draw(dummy).text((4, 4), "12", fill=0)
        try:
            pytesseract.image_to_string(dummy, lang="jpn+eng", config="--oem 1 --psm 7")
        except Exception:
            logger.debug("Tesseract 予熱中の OCR で軽微なエラー", exc_info=True)
        _TESSERACT_WARMED = True
        logger.info("Tesseract の予熱が完了しました。")
    except Exception:
        logger.exception("Tesseract の予熱に失敗しました。")

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
    if field in {"first_registration_month", "passenger_capacity", "displacement"}:
        return text
    common_replacements = {
        "O": "0", "o": "0", "D": "0", "Q": "0",
        "I": "1", "l": "1", "|": "1",
        "S": "8", "B": "8",
    }
    date_replacements = {
        "m": "日", "n": "日", "」": "月", "』": "月", "'": "月",
        "牛": "年", "干": "年", "于": "年", "午": "年",
        "合": "令", "命": "令", "今": "令", "苓": "令", "禾口": "和", "利": "和",
        "平咸": "平成", "平戊": "平成", "平威": "平成",
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

def _resize_image(image, scale, resampling=None):
    if resampling is None:
        resampling = getattr(Image, "Resampling", Image).LANCZOS
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    return image.resize((width, height), resampling)


def _add_ocr_variant(variants, seen, image, *, border=18):
    padded = ImageOps.expand(image.convert("L"), border=border, fill=255)
    fingerprint = (padded.size, padded.tobytes())
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    variants.append(padded)


def _adaptive_threshold(image, block_size=35, offset=12):
    """局所平均を使った適応的二値化。影や紙面ムラに固定閾値より強い。"""
    if block_size % 2 == 0:
        block_size += 1
    gray = image.convert("L")
    source = np.asarray(gray, dtype=np.int16)
    local_mean = np.asarray(gray.filter(ImageFilter.BoxBlur(block_size // 2)), dtype=np.int16)
    binary = np.where(source < local_mean - int(offset), 0, 255).astype(np.uint8)
    return Image.fromarray(binary, mode="L")


def _suppress_form_lines(image):
    gray = image.convert("L")
    arr = np.array(gray, dtype=np.uint8)
    if arr.size == 0:
        return gray
    dark = arr < 90
    horizontal = np.where(dark.mean(axis=1) > 0.45)[0]
    vertical = np.where(dark.mean(axis=0) > 0.45)[0]
    for row in horizontal:
        start = max(0, row - 1)
        end = min(arr.shape[0], row + 2)
        arr[start:end, :] = 255
    for col in vertical:
        start = max(0, col - 1)
        end = min(arr.shape[1], col + 2)
        arr[:, start:end] = 255
    return Image.fromarray(arr, mode="L")


def _threshold_image(image, threshold):
    return image.point(lambda px, t=threshold: 0 if px < t else 255, "1").convert("L")


def _variant_thresholds_for_field(field):
    if field in {"passenger_capacity", "displacement"}:
        return (105, 120, 135, 150, 165, 180, 195, 210, 225)
    return (120, 135, 150, 165, 180, 195, 210, 225)


def _variant_offsets_for_field(field):
    if field in {"passenger_capacity", "displacement"}:
        return (6, 9, 12, 15)
    return (8, 11, 14)


def _prepare_region_variants(region, field=None, *, retry=False):
    """Build accuracy-oriented OCR images for one cropped ROI."""
    gray = _suppress_form_lines(ImageOps.grayscale(region))
    variants = []
    seen = set()
    scales = (2.0, 2.5, 3.0)
    if retry:
        scales = (2.25, 2.75, 3.25)

    thresholds = _variant_thresholds_for_field(field)
    offsets = _variant_offsets_for_field(field)

    for scale in scales:
        resized = _resize_image(gray, scale)
        autocontrast = ImageOps.autocontrast(resized, cutoff=1 if retry else 0)
        base_images = [
            resized,
            autocontrast,
            ImageEnhance.Contrast(autocontrast).enhance(1.6),
            ImageEnhance.Contrast(autocontrast).enhance(2.2 if not retry else 2.6),
            ImageEnhance.Sharpness(autocontrast).enhance(1.8),
        ]
        for base in base_images:
            denoised = base.filter(ImageFilter.MedianFilter(size=3))
            _add_ocr_variant(variants, seen, base)
            _add_ocr_variant(variants, seen, denoised)
            for threshold in thresholds:
                _add_ocr_variant(variants, seen, _threshold_image(base, threshold))
            for offset in offsets:
                _add_ocr_variant(variants, seen, _adaptive_threshold(denoised, block_size=35, offset=offset))

    return variants


def _ocr_image_variants_tesseract(region, field, configs, *, retry=False, variant_offset=0, variant_limit=None):
    """variants × configs を共有スレッドプールで並列実行する。"""
    if pytesseract is None:
        return []
    lang = _ocr_lang_for_field(field)
    variants = _prepare_region_variants(region, field, retry=retry)
    if variant_limit is None:
        variants = variants[variant_offset:]
    else:
        variants = variants[variant_offset: variant_offset + variant_limit]
    tasks = [(image, config) for image in variants for config in configs]
    if not tasks:
        return []

    def _run(task):
        image, config = task
        try:
            return pytesseract.image_to_string(image, lang=lang, config=config).strip()
        except Exception as exc:
            logger.debug("OCR failed for %s with %s: %s", field, config, exc)
            return ""

    texts = []
    # Tesseract はサブプロセス呼び出しなので待機中は GIL を解放する。
    # max_workers は共有 Executor のサイズに自動調整される。
    for raw_text in _OCR_EXECUTOR.map(_run, tasks):
        if raw_text:
            texts.append(correct_ocr_text(raw_text, field))
    return texts


def _ocr_lang_for_field(field):
    if field == "displacement":
        return "eng"
    return "jpn+eng"


def _ocr_configs_for_field(field, *, retry=False):
    if field == "expiry_date":
        configs = [
            "--oem 1 --psm 7",
        ]
        if retry:
            configs = ["--oem 1 --psm 6"]
    elif field == "first_registration_month":
        configs = [
            "--oem 1 --psm 7",
        ]
        if retry:
            configs = ["--oem 1 --psm 6"]
    elif field == "passenger_capacity":
        psm = "10" if retry else "8"
        configs = [f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789０１２３４５６７８９人入名員"]
    elif field == "displacement":
        configs = [
            "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789.LlKkWw",
        ]
        if retry:
            configs = ["--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.LlKkWw"]
    else:
        configs = [
            "--oem 1 --psm 7",
        ]
        if retry:
            configs = ["--oem 1 --psm 6"]
    extra_configs = []
    if field == "expiry_date":
        extra_configs = [
            "--oem 1 --psm 8",
            "--oem 1 --psm 6",
            "--oem 1 --psm 13",
        ]
    elif field == "first_registration_month":
        extra_configs = [
            "--oem 1 --psm 8",
            "--oem 1 --psm 6",
            "--oem 1 --psm 13",
        ]
    elif field == "passenger_capacity":
        extra_configs = [
            "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789０１２３４５６７８９人入名員",
            "--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789０１２３４５６７８９人入名員",
            "--oem 1 --psm 10 -c tessedit_char_whitelist=0123456789０１２３４５６７８９人入名員",
            "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789０１２３４５６７８９人入名員",
        ]
    elif field == "displacement":
        extra_configs = [
            "--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789.LlKkWw",
            "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.LlKkWw",
            "--oem 1 --psm 13 -c tessedit_char_whitelist=0123456789.LlKkWw",
        ]
    elif field == "reg_number":
        extra_configs = ["--oem 1 --psm 8", "--oem 1 --psm 6", "--oem 1 --psm 13"]

    for config in extra_configs:
        if config not in configs:
            configs.append(config)
    return configs


def _ocr_success_threshold(field):
    if field == "reg_number":
        return 40
    if field in {"expiry_date", "first_registration_month", "passenger_capacity", "displacement"}:
        return 80
    return 1


def _ocr_fast_variant_count(field):
    if field in {"passenger_capacity", "displacement"}:
        return 16
    return 12


def _ocr_fast_config_count(field):
    return 1


def _ocr_image_variants(region, field):
    configs = _ocr_configs_for_field(field)
    fast_variant_count = _ocr_fast_variant_count(field)
    fast_config_count = _ocr_fast_config_count(field)

    texts = _ocr_image_variants_tesseract(
        region,
        field,
        configs[:fast_config_count],
        retry=False,
        variant_limit=fast_variant_count,
    )
    _value, _raw, score = _best_candidate(texts, field)
    if score >= _ocr_success_threshold(field):
        return texts

    if len(configs) > fast_config_count:
        texts.extend(_ocr_image_variants_tesseract(
            region,
            field,
            configs[fast_config_count:],
            retry=False,
            variant_limit=fast_variant_count,
        ))
        _value, _raw, score = _best_candidate(texts, field)
        if score >= _ocr_success_threshold(field):
            return texts

    texts.extend(_ocr_image_variants_tesseract(
        region,
        field,
        configs,
        retry=False,
        variant_offset=fast_variant_count,
    ))
    _value, _raw, score = _best_candidate(texts, field)
    if score >= _ocr_success_threshold(field):
        return texts

    retry_texts = _ocr_image_variants_tesseract(region, field, _ocr_configs_for_field(field, retry=True), retry=True)
    return texts + retry_texts


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


def normalize_first_registration_month(text):
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    value = re.sub(r"\s+", "", value)
    western_match = re.search(r"(19\d{2}|20\d{2})年?(\d{1,2})月?", value)
    if western_match:
        year_int = int(western_match.group(1))
        month_int = int(western_match.group(2))
        if 1 <= month_int <= 12 and year_int <= datetime.now().year:
            return f"{year_int}年{month_int}月"
        return ""
    value = (
        value
        .replace("初度検査年月", "")
        .replace("検査年月", "")
        .replace("合", "令")
        .replace("命", "令")
        .replace("今", "令")
        .replace("苓", "令")
        .replace("禾口", "和")
        .replace("平咸", "平成")
        .replace("平戊", "平成")
        .replace("平威", "平成")
    )
    era_names = {
        "令和": "令和",
        "R": "令和",
        "平成": "平成",
        "H": "平成",
        "昭和": "昭和",
        "S": "昭和",
    }
    era_offsets = {
        "令和": 2018,
        "平成": 1988,
        "昭和": 1925,
    }
    era_limits = {
        "令和": (1, max(1, datetime.now().year - 2018)),
        "平成": (1, 31),
        "昭和": (1, 64),
    }
    match = re.search(r"(?<![A-Z0-9])(令和|平成|昭和|R|H|S)(元|\d{1,2})年?(\d{1,2})月?", value, re.I)
    if not match:
        return ""
    era, year, month = match.groups()
    era = era_names.get(era.upper(), era)
    era_year = 1 if year == "元" else int(year)
    month_int = int(month)
    if month_int < 1 or month_int > 12:
        return ""
    min_year, max_year = era_limits[era]
    if era_year < min_year or era_year > max_year:
        return ""
    western_year = era_offsets[era] + era_year
    if western_year > datetime.now().year:
        return ""
    return f"{western_year}年{month_int}月"


def normalize_passenger_capacity(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.translate(str.maketrans({
        "O": "0", "o": "0", "D": "0", "Q": "0",
        "I": "1", "l": "1", "|": "1", "!": "1",
        "S": "5", "s": "5", "B": "8",
    }))
    value = value.replace("入", "人").replace("名", "人")
    compact = re.sub(r"\s+", "", value)
    unit_match = re.search(r"(\d{1,2})(?:人|員)", compact)
    if unit_match:
        count = int(unit_match.group(1))
    else:
        numbers = [int(item) for item in re.findall(r"(?<![\d.])(\d{1,2})(?![\d.])", compact)]
        if not numbers:
            return ""
        label_index = max(compact.find("乗車定員"), compact.find("定員"))
        if label_index >= 0:
            tail_numbers = [int(item) for item in re.findall(r"(?<![\d.])(\d{1,2})(?![\d.])", compact[label_index:])]
            if tail_numbers:
                numbers = tail_numbers
        count = numbers[0]
    if count < 1 or count > 99:
        return ""
    return f"{count}人"


def normalize_displacement(text):
    value = unicodedata.normalize("NFKC", str(text or "")).upper()
    value = value.replace("リットル", "L").replace("Ｌ", "L").replace("㎤", "CC").replace("CM3", "CC")
    value = value.replace("I", "1").replace("|", "1").replace("O", "0").replace("，", ",")
    value = re.sub(r"\s+", "", value)
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(K?W|L|CC|C\.C\.)", value)
    if match:
        number, unit = match.groups()
        unit = "CC" if unit == "C.C." else unit
        number = number.replace(",", "" if unit == "CC" else ".")
    else:
        decimal_match = re.search(r"(\d+[.,]\d+)", value)
        if not decimal_match:
            return ""
        number, unit = decimal_match.group(1).replace(",", "."), "L"
    try:
        decimal_number = Decimal(number)
    except InvalidOperation:
        return ""
    if unit == "CC":
        decimal_number = decimal_number / Decimal("1000")
        unit = "L"
    has_decimal_point = "." in number
    if unit == "L":
        try:
            if has_decimal_point or decimal_number != decimal_number.to_integral_value():
                decimal_number = decimal_number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            number = format(decimal_number, "f")
        except InvalidOperation:
            return ""
        if "." in number:
            number = number.rstrip("0").rstrip(".")
    else:
        number = format(decimal_number.normalize(), "f").rstrip("0").rstrip(".")
    return f"{number}{unit}"


def _score_first_registration_month_candidate(text):
    value = normalize_first_registration_month(text)
    if not value:
        return -100, ""
    score = 100
    if "年" in value and "月" in value:
        score += 20
    return score, value


def _score_passenger_capacity_candidate(text):
    value = normalize_passenger_capacity(text)
    if not value:
        return -100, ""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    score = 110
    if "乗車定員" in normalized or "定員" in normalized:
        score += 25
    if re.search(r"\d{1,2}\s*(人|入|名|員)", normalized):
        score += 20
    if re.search(r"(kg|KG|L|リットル)", normalized):
        score -= 30
    return score, value


def _score_displacement_candidate(text):
    value = normalize_displacement(text)
    if not value:
        return -100, ""
    score = 100
    if value.endswith("L"):
        score += 15
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    if "排気量" in normalized or "定格出力" in normalized:
        score += 25
    if re.search(r"\d+(?:[.,]\d+)?\s*(L|l|W|w|kW|KW|cc|CC|リットル)", normalized):
        score += 20
    return score, value


def _best_candidate(texts, field):
    best_score = -999
    best_value = ""
    raw_value = ""
    for text in texts:
        if field == "reg_number":
            score, value = _score_registration_candidate(text)
        elif field == "expiry_date":
            score, value = _score_date_candidate(text)
        elif field == "first_registration_month":
            score, value = _score_first_registration_month_candidate(text)
        elif field == "passenger_capacity":
            score, value = _score_passenger_capacity_candidate(text)
        elif field == "displacement":
            score, value = _score_displacement_candidate(text)
        else:
            score, value = (1, normalize_ocr_text(text))
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
    if not {"reg_number", "expiry_date"}.issubset(set(regions)):
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
    if os.environ.get("DSTT_OCR_DEBUG_PREVIEW", "").strip().lower() in {"1", "true", "yes", "on"}:
        debug_path = os.path.join(tempfile.gettempdir(), "debug_ocr_area.png")
        img.save(debug_path)
        logger.info("OCR debug preview saved: %s", debug_path)
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
    if not digits:
        return ""
    return digits[-length:].zfill(length)


def detect_csv_encoding(file_bytes):
    detected = chardet.detect(file_bytes)
    candidates = ["utf-8-sig", "utf-8", "cp932", "shift_jis", detected.get("encoding")]
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


def normalize_vehicle_number(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", text)


def parse_management_date(value):
    text = normalize_ocr_text(value)
    if not text:
        return ""
    parsed, _err = parse_expiry_date(text)
    if parsed:
        return parsed
    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else text


def load_vehicle_entries_from_db():
    ensure_contract_master_synced()
    records = VehicleInspectionRecord.query.order_by(VehicleInspectionRecord.updated_at.asc()).all()
    latest_by_contract = {record.contract_code: record for record in records}
    entries = []
    rows = (
        SiteContractMaster.query
        .filter(SiteContractMaster.is_active.is_(True))
        .order_by(SiteContractMaster.contract_code.asc())
        .all()
    )
    for index, row in enumerate(rows, 1):
        record = latest_by_contract.get(row.contract_code)
        registration = clean_registration_number(record.registration_number if record else "")
        vehicle_number = normalize_vehicle_number(row.vehicle_number or (record.vehicle_number if record else ""))
        entries.append({
            "row": index,
            "vehicle_id": row.contract_code,
            "contract_code": row.contract_code,
            "vehicle_number": vehicle_number,
            "registration": registration,
            "suffix": extract_suffix_digits(registration or vehicle_number),
            "location": row.site_name or "",
            "raw": [],
        })
    return {
        "encoding": "database",
        "has_header": True,
        "headers": ["契約コード", "車両番号", "登録番号", "現場名"],
        "columns": {},
        "entries": entries,
    }


def upsert_vehicle_mapping(contract_code, vehicle_number, *, actor=""):
    contract_code = str(contract_code or "").strip()
    vehicle_number = normalize_vehicle_number(vehicle_number)
    if not contract_code or not vehicle_number:
        return None
    ensure_contract_master_synced()
    row = db.session.get(SiteContractMaster, contract_code)
    if row is None:
        return None
    row.vehicle_number = vehicle_number
    row.vehicle_number_updated_by = actor
    row.vehicle_number_updated_at = datetime.utcnow()
    return row


def siteplus_contract_for_code(contract_code):
    contract_code = str(contract_code or "").strip()
    if not contract_code:
        return None
    ensure_contract_master_synced()
    row = db.session.get(SiteContractMaster, contract_code)
    if row is None or not row.is_active:
        return None
    return row


def serialize_siteplus_contract(row):
    if row is None:
        return None
    return {
        "contract_code": row.contract_code,
        "site_id": row.site_id,
        "site_branch": row.site_branch,
        "site_name": row.site_name,
        "site_manager_id": row.site_manager_id,
        "site_manager_name": row.site_manager_name,
        "vehicle_number": row.vehicle_number or "",
        "segment": row.segment or "",
        "is_active": bool(row.is_active),
    }


def missing_siteplus_contract_response(contract_codes):
    missing = sorted({str(code or "").strip() for code in contract_codes if str(code or "").strip()})
    return jsonify({
        "error": "現場リストPLUSに存在しない契約コードです。確認して保存する場合は再実行してください。",
        "requires_confirmation": True,
        "missing_contracts": missing,
    }), 409


def enrich_vehicle_record(record):
    item = record.to_dict()
    siteplus_row = siteplus_contract_for_code(record.contract_code)
    item["siteplus_linked"] = siteplus_row is not None
    item["siteplus_contract"] = serialize_siteplus_contract(siteplus_row)
    if siteplus_row is not None:
        item["site_name"] = siteplus_row.site_name or item.get("site_name", "")
        item["siteplus_vehicle_number"] = siteplus_row.vehicle_number or ""
    else:
        item["siteplus_vehicle_number"] = ""
    return item


def match_vehicle(registration, csv_entries):
    registration = clean_registration_number(registration)
    suffix = extract_suffix_digits(registration)
    normalized_registration = normalize_vehicle_number(registration)
    matches = []
    for entry in csv_entries:
        score = 0
        entry_vehicle_number = normalize_vehicle_number(entry.get("vehicle_number", ""))
        entry_suffix = extract_suffix_digits(entry_vehicle_number)
        if entry.get("registration") and clean_registration_number(entry["registration"]) == registration:
            score += 120
        if entry_vehicle_number and normalized_registration and entry_vehicle_number == normalized_registration:
            score += 130
        if entry_suffix and suffix and entry_suffix == suffix:
            score += 90
        if entry.get("suffix") and entry["suffix"] == suffix:
            score += 80
        if entry.get("registration") and entry["registration"] and entry["registration"] in registration:
            score += 20
        if score:
            matches.append({**entry, "score": score})
    matches.sort(key=lambda item: (item["score"], bool(item.get("vehicle_number")), bool(item.get("registration"))), reverse=True)
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
    expiry_part = sanitize_filename_part(expiry, "満了日未確認")
    contract_part = sanitize_filename_part(row.get("vehicle_id"), "契約コード未確認")
    location_part = sanitize_filename_part(row.get("location"), "現場名未確認")
    registration_part = sanitize_filename_part(registration, "登録番号未確認")
    original_part = sanitize_filename_part(os.path.splitext(row.get("original_name", ""))[0], "元ファイル")
    today_part = datetime.now().strftime("%Y%m%d")
    values = {
        "満了日": expiry_part,
        "契約コード": contract_part,
        "現場名": location_part,
        "登録番号": registration_part,
        "元ファイル名": original_part,
        "今日の日付": today_part,
        "expiry": expiry_part,
        "vehicle_id": contract_part,
        "location": location_part,
        "registration": registration_part,
        "original": original_part,
        "today": today_part,
    }
    template = (template or DEFAULT_FILENAME_TEMPLATE).strip()
    try:
        filename = template.format(**values)
    except (KeyError, ValueError):
        filename = DEFAULT_FILENAME_TEMPLATE.format(**values)
    filename = re.sub(r"\{[^{}]*\}", "", filename)
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
    for key in ("reg_number", "expiry_date", *EXTRA_OCR_FIELDS):
        box = source.get(key)
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            coords = [int(float(value)) for value in box]
        except (TypeError, ValueError):
            continue
        if coords[2] > coords[0] and coords[3] > coords[1]:
            regions[key] = coords
    if not {"reg_number", "expiry_date"}.issubset(regions):
        return None
    return regions


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
            "first_registration_month": "",
            "passenger_capacity": "",
            "displacement": "",
            "vehicle_id": "",
            "vehicle_number": "",
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
        "first_registration_month": normalize_first_registration_month(ocr_result.get("first_registration_month", "")),
        "passenger_capacity": normalize_passenger_capacity(ocr_result.get("passenger_capacity", "")),
        "displacement": normalize_displacement(ocr_result.get("displacement", "")),
        "vehicle_id": best.get("vehicle_id", ""),
        "vehicle_number": best.get("vehicle_number", "") or match["suffix"],
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
    if not regions or not {"reg_number", "expiry_date"}.issubset(set(regions)):
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
    ok, reason = setup_tesseract_with_reason()
    if not ok:
        return jsonify({"error": f"OCRエンジン (Tesseract) を初期化できませんでした: {reason or '原因不明'}"}), 500

    pdf_files = [f for f in request.files.getlist("pdf_files") if f and f.filename]
    csv_file = request.files.get("csv_file")
    preset = request.form.get("preset", "")
    if not pdf_files:
        return jsonify({"error": "PDFを選択してください。"}), 400
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

    if csv_file and csv_file.filename:
        try:
            csv_info = parse_vehicle_csv(csv_file.read())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        csv_info = load_vehicle_entries_from_db()

    session_id = uuid.uuid4().hex
    session_dir = user_session_dir(session_id)
    os.makedirs(session_dir, exist_ok=True)

    files = {}
    used_preview_ids = []
    analyze_tasks = []  # (index, file_id, pdf_path, image_path, original_name, active_regions)
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

        try:
            if preview_manifest and os.path.exists(preview_manifest.get("pdf_path", "")) and os.path.exists(preview_manifest.get("image_path", "")):
                original_name = preview_manifest.get("original_name") or original_name
                shutil.copyfile(preview_manifest["pdf_path"], pdf_path)
                shutil.copyfile(preview_manifest["image_path"], image_path)
                used_preview_ids.append(preview_id)
            else:
                pdf_file.save(pdf_path)
                _save_pdf_first_page_image(pdf_path, dpi_for_preset(preset), image_path)
        except Exception as exc:
            logger.exception("PDFの保存または画像化に失敗: %s", original_name)
            return jsonify({"error": f"{original_name} を読み込めませんでした: {exc}"}), 400

        files[file_id] = {"original_name": original_name, "path": pdf_path, "image_path": image_path, "dpi": dpi_for_preset(preset)}
        per_file_regions = None
        if region_mode == "per_file" and index < len(file_regions_payload):
            per_file_regions = parse_regions_payload(file_regions_payload[index])
        active_regions = per_file_regions or custom_regions
        analyze_tasks.append((index, file_id, pdf_path, image_path, original_name, active_regions))

    def _analyze_one(task):
        idx, fid, ppath, ipath, oname, regs = task
        try:
            return idx, analyze_pdf_to_row(ppath, oname, preset, csv_info["entries"], regs, image_path=ipath)
        except Exception as exc:
            logger.exception("車検証PDF解析に失敗: %s", oname)
            fallback = {
                "file_id": fid,
                "original_name": oname,
                "status": "needs_review",
                "registration_number": "",
                "expiry_date": "",
                "first_registration_month": "",
                "passenger_capacity": "",
                "displacement": "",
                "vehicle_id": "",
                "vehicle_number": "",
                "location": "",
                "match_status": "unmatched",
                "match_candidates": [],
                "preset": preset,
                "regions": regs or (all_presets().get(preset, {}).get("regions", {})),
                "preview": "",
                "candidate_texts": {},
                "confidence": 0,
                "message": f"解析中にエラーが発生しました: {exc}",
            }
            return idx, fallback

    # PDF 単位で並列処理する。OCR 内部のスレッドプールと共有プールなので
    # トータルの並列度は _OCR_WORKER_COUNT に自然に制限される。
    rows = [None] * len(analyze_tasks)
    if len(analyze_tasks) > 1:
        max_pdf_workers = max(1, min(len(analyze_tasks), _OCR_WORKER_COUNT))
        with ThreadPoolExecutor(max_workers=max_pdf_workers, thread_name_prefix="car_inspe_pdf") as pool:
            for idx, row in pool.map(_analyze_one, analyze_tasks):
                rows[idx] = row
    elif analyze_tasks:
        idx, row = _analyze_one(analyze_tasks[0])
        rows[idx] = row

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
    ok, reason = setup_tesseract_with_reason()
    if not ok:
        return jsonify({"error": f"OCRエンジン (Tesseract) を初期化できませんでした: {reason or '原因不明'}"}), 500
    try:
        row = analyze_pdf_to_row(
            file_info["path"],
            file_info["original_name"],
            preset,
            manifest.get("csv", {}).get("entries", []),
            custom_regions,
            image_path=file_info.get("image_path"),
        )
    except Exception as exc:
        logger.exception("再OCRに失敗: %s", file_info.get("original_name"))
        return jsonify({"error": f"再OCRに失敗しました: {exc}"}), 500
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


def save_vehicle_record_from_row(row, source_path, output_filename, *, allow_unlinked_site=False):
    contract_code = str(row.get("vehicle_id") or row.get("contract_code") or "").strip()
    registration_number = clean_registration_number(row.get("registration_number", ""))
    vehicle_number = normalize_vehicle_number(row.get("vehicle_number") or "")
    if not vehicle_number:
        vehicle_number = extract_suffix_digits(registration_number)
    if not contract_code or not vehicle_number:
        return None

    actor = getattr(current_user, "username", "")
    contract_row = upsert_vehicle_mapping(contract_code, vehicle_number, actor=actor)
    if contract_row is None and not allow_unlinked_site:
        raise ValueError(f"現場リストPLUSに契約コード {contract_code} がありません")
    site_name = (contract_row.site_name if contract_row else "") or row.get("location") or ""

    record = VehicleInspectionRecord.query.filter_by(
        contract_code=contract_code,
        vehicle_number=vehicle_number,
    ).first()
    if record is None:
        record = VehicleInspectionRecord(contract_code=contract_code, vehicle_number=vehicle_number)
        db.session.add(record)
    elif record.stored_path and os.path.exists(record.stored_path):
        try:
            os.remove(record.stored_path)
        except OSError:
            logger.warning("古い車検証PDFを削除できませんでした: %s", record.stored_path, exc_info=True)

    ensure_work_dir()
    today_dir = os.path.join(CAR_INSPE_STORAGE_DIR, datetime.now().strftime("%Y%m"))
    os.makedirs(today_dir, exist_ok=True)
    stored_filename = sanitize_filename_part(
        f"{contract_code}_{vehicle_number}_{output_filename}",
        "vehicle_inspection.pdf",
    )
    stored_path = os.path.join(today_dir, stored_filename)
    shutil.copyfile(source_path, stored_path)

    record.registration_number = registration_number
    record.expiry_date = parse_management_date(row.get("expiry_date"))
    record.first_registration_month = normalize_first_registration_month(row.get("first_registration_month", ""))
    record.passenger_capacity = normalize_passenger_capacity(row.get("passenger_capacity", ""))
    record.displacement = normalize_displacement(row.get("displacement", ""))
    record.site_name = site_name
    record.original_filename = row.get("original_name", "")
    record.stored_filename = output_filename
    record.stored_path = stored_path
    record.uploaded_by = actor
    record.uploaded_at = datetime.utcnow()
    record.source = "ocr" if contract_row is not None else "ocr_unlinked"
    return record


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

    allow_unlinked_site = bool(payload.get("confirm_unlinked_sites") or payload.get("confirm_missing_site"))
    missing_contracts = []
    for row in selected_rows:
        contract_code = str(row.get("vehicle_id") or row.get("contract_code") or "").strip()
        if contract_code and siteplus_contract_for_code(contract_code) is None:
            missing_contracts.append(contract_code)
    if missing_contracts and not allow_unlinked_site:
        return missing_siteplus_contract_response(missing_contracts)

    output_items = []
    save_errors = []
    for row in selected_rows:
        file_id = os.path.basename(row.get("file_id", ""))
        file_info = manifest.get("files", {}).get(file_id)
        if not file_info or not os.path.exists(file_info["path"]):
            return jsonify({"error": f"{row.get('original_name', file_id)} の元PDFが見つかりません。"}), 404
        normalized_row = {
            **row,
            "registration_number": clean_registration_number(row.get("registration_number", "")),
            "expiry_date": normalize_ocr_text(row.get("expiry_date", "")),
            "first_registration_month": normalize_first_registration_month(row.get("first_registration_month", "")),
            "passenger_capacity": normalize_passenger_capacity(row.get("passenger_capacity", "")),
            "displacement": normalize_displacement(row.get("displacement", "")),
            "vehicle_id": row.get("vehicle_id", ""),
            "vehicle_number": row.get("vehicle_number", ""),
            "location": row.get("location", ""),
        }
        filename = build_output_filename(normalized_row, template)
        try:
            saved = save_vehicle_record_from_row(
                normalized_row,
                file_info["path"],
                filename,
                allow_unlinked_site=allow_unlinked_site,
            )
        except Exception as exc:
            logger.exception("車検証台帳の保存に失敗: %s", row.get("original_name", file_id))
            save_errors.append(f"{row.get('original_name', file_id)}: {exc}")
            continue
        if saved is None:
            save_errors.append(
                f"{row.get('original_name', file_id)}: 契約コードと車両番号が両方そろっていません。"
            )
        output_items.append((file_info["path"], filename))

    if save_errors:
        db.session.rollback()
        return jsonify({"error": "台帳保存中にエラーが発生しました。\n" + "\n".join(save_errors)}), 400

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("車検証台帳のコミットに失敗")
        return jsonify({"error": f"台帳の保存をコミットできませんでした: {exc}"}), 500

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
    return send_file_and_cleanup(zip_path, session_dir, as_attachment=True, download_name="車検証PDF.zip")


@car_inspe_bp.route("/api/records", methods=["GET"])
@login_required
def api_records():
    ensure_contract_master_synced()
    q = normalize_ocr_text(request.args.get("q", ""))
    status = str(request.args.get("status", "") or "").strip()
    query = VehicleInspectionRecord.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                VehicleInspectionRecord.contract_code.ilike(like),
                VehicleInspectionRecord.vehicle_number.ilike(like),
                VehicleInspectionRecord.registration_number.ilike(like),
                VehicleInspectionRecord.site_name.ilike(like),
            )
        )
    records = query.order_by(VehicleInspectionRecord.expiry_date.asc(), VehicleInspectionRecord.contract_code.asc()).all()
    today = datetime.now().strftime("%Y%m%d")
    enriched = []
    seen_keys = set()
    for record in records:
        item = enrich_vehicle_record(record)
        seen_keys.add((
            str(item.get("contract_code") or "").strip(),
            str(item.get("vehicle_number") or "").strip(),
        ))
        expiry = item.get("expiry_date") or ""
        item["status"] = "unknown"
        # expiry_date は正常時 "YYYYMMDD" の8桁。OCR失敗時に非8桁の文字列が
        # 入りうるため、8桁の場合のみ today との文字列比較で期限判定する
        # （非8桁を比較すると期限切れ車両を active と誤判定する恐れがある）。
        if re.fullmatch(r"\d{8}", expiry):
            item["status"] = "expired" if expiry < today else "active"
        if status and item["status"] != status:
            continue
        enriched.append(item)

    siteplus_query = SiteContractMaster.query.filter(
        SiteContractMaster.is_active.is_(True),
        SiteContractMaster.vehicle_number.isnot(None),
        SiteContractMaster.vehicle_number != "",
    )
    if q:
        like = f"%{q}%"
        siteplus_query = siteplus_query.filter(
            or_(
                SiteContractMaster.contract_code.ilike(like),
                SiteContractMaster.vehicle_number.ilike(like),
                SiteContractMaster.site_name.ilike(like),
            )
        )
    siteplus_rows = siteplus_query.order_by(SiteContractMaster.contract_code.asc()).all()
    for row in siteplus_rows:
        contract_code = str(row.contract_code or "").strip()
        vehicle_number = str(row.vehicle_number or "").strip()
        if not contract_code or not vehicle_number:
            continue
        if (contract_code, vehicle_number) in seen_keys:
            continue
        seen_keys.add((contract_code, vehicle_number))
        if status and status != "unknown":
            continue
        enriched.append({
            "id": None,
            "contract_code": contract_code,
            "vehicle_number": vehicle_number,
            "registration_number": "",
            "expiry_date": "",
            "first_registration_month": "",
            "passenger_capacity": "",
            "displacement": "",
            "site_name": row.site_name or "",
            "original_filename": "",
            "stored_filename": "",
            "has_pdf": False,
            "uploaded_by": "",
            "uploaded_at": None,
            "updated_at": None,
            "source": "siteplus",
            "notes": "",
            "siteplus_linked": True,
            "siteplus_contract": serialize_siteplus_contract(row),
            "siteplus_vehicle_number": vehicle_number,
            "status": "unknown",
            "is_pending": True,
        })

    return jsonify({
        "records": enriched,
        "count": len(enriched),
        "summary": {
            "contracts": SiteContractMaster.query.filter(SiteContractMaster.is_active.is_(True)).count(),
            "records": VehicleInspectionRecord.query.count(),
        },
    })


@car_inspe_bp.route("/api/site-contracts", methods=["GET"])
@login_required
def api_site_contracts():
    ensure_contract_master_synced()
    q = normalize_ocr_text(request.args.get("q", ""))
    try:
        limit = int(request.args.get("limit", 30) or 30)
    except (TypeError, ValueError):
        limit = 30
    limit = min(max(limit, 1), 100)
    query = SiteContractMaster.query.filter(SiteContractMaster.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                SiteContractMaster.contract_code.ilike(like),
                SiteContractMaster.site_name.ilike(like),
                SiteContractMaster.vehicle_number.ilike(like),
                SiteContractMaster.site_manager_name.ilike(like),
            )
        )
    rows = query.order_by(SiteContractMaster.contract_code.asc()).limit(limit).all()
    return jsonify({
        "contracts": [serialize_siteplus_contract(row) for row in rows],
        "count": len(rows),
    })


@car_inspe_bp.route("/api/records", methods=["POST"])
@login_required
def api_create_record():
    data = request.get_json(silent=True) or {}
    contract_code = str(data.get("contract_code", "") or "").strip()
    vehicle_number = normalize_vehicle_number(data.get("vehicle_number", ""))
    if not contract_code or not vehicle_number:
        return jsonify({"error": "契約コードと車両番号は必須です。"}), 400

    duplicate = VehicleInspectionRecord.query.filter_by(
        contract_code=contract_code,
        vehicle_number=vehicle_number,
    ).first()
    if duplicate:
        return jsonify({"error": "同じ契約コードと車両番号の台帳が既にあります。"}), 409

    siteplus_row = siteplus_contract_for_code(contract_code)
    if siteplus_row is None and not data.get("confirm_missing_site"):
        return missing_siteplus_contract_response([contract_code])

    actor = getattr(current_user, "username", "")
    if siteplus_row is not None:
        upsert_vehicle_mapping(contract_code, vehicle_number, actor=actor)

    record = VehicleInspectionRecord(
        contract_code=contract_code,
        vehicle_number=vehicle_number,
        registration_number=clean_registration_number(data.get("registration_number", "")),
        expiry_date=parse_management_date(data.get("expiry_date", "")),
        first_registration_month=normalize_first_registration_month(data.get("first_registration_month", "")),
        passenger_capacity=normalize_passenger_capacity(data.get("passenger_capacity", "")),
        displacement=normalize_displacement(data.get("displacement", "")),
        site_name=(siteplus_row.site_name if siteplus_row else str(data.get("site_name", "") or "").strip()),
        notes=str(data.get("notes", "") or "").strip(),
        uploaded_by=actor,
        source="manual" if siteplus_row is not None else "manual_unlinked",
    )
    db.session.add(record)
    db.session.commit()
    return jsonify({"success": True, "record": enrich_vehicle_record(record)})


@car_inspe_bp.route("/api/records/<int:record_id>", methods=["PUT"])
@login_required
def api_update_record(record_id):
    record = db.session.get(VehicleInspectionRecord, record_id)
    if record is None:
        return jsonify({"error": "対象データが見つかりません。"}), 404
    data = request.get_json(silent=True) or {}
    contract_code = str(data.get("contract_code", record.contract_code) or "").strip()
    vehicle_number = normalize_vehicle_number(data.get("vehicle_number", record.vehicle_number))
    if not contract_code or not vehicle_number:
        return jsonify({"error": "契約コードと車両番号は必須です。"}), 400
    duplicate = VehicleInspectionRecord.query.filter(
        VehicleInspectionRecord.contract_code == contract_code,
        VehicleInspectionRecord.vehicle_number == vehicle_number,
        VehicleInspectionRecord.id != record.id,
    ).first()
    if duplicate:
        return jsonify({"error": "同じ契約コードと車両番号の台帳が既にあります。"}), 409

    siteplus_row = siteplus_contract_for_code(contract_code)
    if siteplus_row is None and not data.get("confirm_missing_site"):
        return missing_siteplus_contract_response([contract_code])

    record.contract_code = contract_code
    record.vehicle_number = vehicle_number
    record.registration_number = clean_registration_number(data.get("registration_number", record.registration_number))
    record.expiry_date = parse_management_date(data.get("expiry_date", record.expiry_date))
    record.first_registration_month = normalize_first_registration_month(data.get("first_registration_month", record.first_registration_month))
    record.passenger_capacity = normalize_passenger_capacity(data.get("passenger_capacity", record.passenger_capacity))
    record.displacement = normalize_displacement(data.get("displacement", record.displacement))
    record.site_name = (siteplus_row.site_name if siteplus_row else str(data.get("site_name", record.site_name) or "").strip())
    record.notes = str(data.get("notes", record.notes) or "").strip()
    if siteplus_row is not None:
        upsert_vehicle_mapping(contract_code, vehicle_number, actor=getattr(current_user, "username", ""))
        if record.source and record.source.endswith("_unlinked"):
            record.source = record.source.replace("_unlinked", "")
    elif not (record.source or "").endswith("_unlinked"):
        record.source = f"{record.source or 'manual'}_unlinked"
    db.session.commit()
    return jsonify({"success": True, "record": enrich_vehicle_record(record)})


@car_inspe_bp.route("/api/records/<int:record_id>", methods=["DELETE"])
@login_required
def api_delete_record(record_id):
    record = db.session.get(VehicleInspectionRecord, record_id)
    if record is None:
        return jsonify({"error": "対象データが見つかりません。"}), 404
    stored_path = record.stored_path
    db.session.delete(record)
    db.session.commit()
    if stored_path and os.path.exists(stored_path):
        try:
            os.remove(stored_path)
        except OSError:
            logger.warning("車検証PDFを削除できませんでした: %s", stored_path, exc_info=True)
    return jsonify({"success": True})


@car_inspe_bp.route("/api/records/<int:record_id>/download", methods=["GET"])
@login_required
def api_download_record_pdf(record_id):
    record = db.session.get(VehicleInspectionRecord, record_id)
    if record is None or not record.stored_path or not os.path.exists(record.stored_path):
        return jsonify({"error": "PDFが見つかりません。"}), 404
    return send_file(record.stored_path, as_attachment=True, download_name=record.stored_filename or os.path.basename(record.stored_path))


@car_inspe_bp.route("/api/vehicle-mapping/import", methods=["POST"])
@login_required
def api_import_vehicle_mapping():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "CSVファイルを選択してください。"}), 400
    try:
        decoded, _encoding = detect_csv_encoding(upload.read())
        rows = list(csv.reader(decoded.splitlines()))
    except Exception as exc:
        return jsonify({"error": f"CSVを読み込めませんでした: {exc}"}), 400

    summary = {"total": 0, "updated": 0, "skipped": 0, "errors": []}
    actor = getattr(current_user, "username", "")
    for index, row in enumerate(rows[1:] if rows else [], start=2):
        if not row or not any(str(cell or "").strip() for cell in row):
            continue
        summary["total"] += 1
        contract_code = str(row[0] if len(row) > 0 else "").strip()
        vehicle_number = normalize_vehicle_number(row[1] if len(row) > 1 else "")
        contract_row = upsert_vehicle_mapping(contract_code, vehicle_number, actor=actor)
        if contract_row is None:
            summary["skipped"] += 1
            summary["errors"].append({"row": index, "message": f"契約コード {contract_code} を更新できません。"})
            continue
        summary["updated"] += 1
    db.session.commit()
    return jsonify({"success": True, "summary": summary})


@car_inspe_bp.route("/", methods=["GET"])
@login_required
def car_inspection():
    mode = request.args.get("mode", "")
    if mode == "read":
        return render_template("car_inspe.html", error=None)
    if mode == "manage":
        return render_template("car_inspe_manage.html", error=None)
    return render_template("car_inspe_home.html", error=None)
