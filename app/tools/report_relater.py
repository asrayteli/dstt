from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required
import os
import tempfile
import shutil
import traceback
import logging
import io
import base64
import csv
import re

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


report_relater_bp = Blueprint(
    "report_relater", __name__, url_prefix="/tools/report_relater"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# Tesseract パス自動検出（Windows対応）
# ============================================================
def _configure_tesseract():
    """Windowsでtesseractのパスを自動検出して設定する"""
    if not TESSERACT_AVAILABLE:
        logger.error("pytesseractモジュールがインストールされていません")
        return False

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]

    if os.name == 'nt':
        candidates.extend([
            os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ])

    path_tesseract = shutil.which("tesseract")
    if path_tesseract:
        candidates.insert(0, path_tesseract)

    for path in candidates:
        if path and os.path.isfile(path):
            try:
                pytesseract.pytesseract.tesseract_cmd = path
                version = pytesseract.get_tesseract_version()
                logger.info(f"Tesseract検出成功: {path} (version: {version})")
                return True
            except Exception as e:
                logger.warning(f"Tesseract候補 {path} は使用不可: {e}")
                continue

    logger.error("Tesseractが見つかりません。")
    return False


TESSERACT_CONFIGURED = _configure_tesseract()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
PDF_RENDER_DPI = 300


# ============================================================
# 日報フィールド定義（実際の画像から計測した座標）
# フォーマット: (x%, y%, w%, h%) - フォーム右側に集中
# ============================================================
DAILY_REPORT_FIELDS = {
    "employee_id": {
        "label": "社員番号",
        "region": (0.80, 0.00, 0.20, 0.06),
    },
    "date": {
        "label": "日付",
        "region": (0.80, 0.05, 0.20, 0.06),
    },
    "vehicle_code": {
        "label": "車両番号(6ケタ)",
        "region": (0.80, 0.10, 0.20, 0.05),
    },
    "start_time": {
        "label": "始業時刻",
        "region": (0.88, 0.17, 0.12, 0.05),
    },
    "end_time": {
        "label": "終業時刻",
        "region": (0.88, 0.21, 0.12, 0.05),
    },
    "meter_out": {
        "label": "出庫メーター",
        "region": (0.80, 0.25, 0.20, 0.05),
    },
    "meter_in": {
        "label": "入庫メーター",
        "region": (0.80, 0.30, 0.20, 0.05),
    },
    "mileage": {
        "label": "走行粁",
        "region": (0.80, 0.35, 0.20, 0.05),
    },
}

COMPARISON_FIELDS = {
    "actual_start": "start_time",
    "actual_end": "end_time",
    "meter_out": "meter_out",
    "meter_in": "meter_in",
    "mileage": "mileage",
    "vehicle_code": "vehicle_code",
}

COMPARISON_FIELD_LABELS = {
    "actual_start": "始業時刻",
    "actual_end": "終業時刻",
    "meter_out": "出庫メーター",
    "meter_in": "入庫メーター",
    "mileage": "走行距離",
    "vehicle_code": "車両番号",
}


# ============================================================
# ルート
# ============================================================

@report_relater_bp.route("/", methods=["GET"])
@login_required
def report_relater():
    return render_template("report_relater.html")


@report_relater_bp.route("/upload", methods=["POST"])
@login_required
def upload_and_ocr():
    """勤怠データPDFと日報PDFをアップロードし、OCR処理を行う"""
    if not CV2_AVAILABLE:
        return jsonify({"error": "OpenCVがインストールされていません"}), 500
    if not TESSERACT_AVAILABLE:
        return jsonify({"error": "pytesseractがインストールされていません"}), 500
    if not TESSERACT_CONFIGURED:
        return jsonify({"error": "Tesseractが見つかりません。インストール後にサーバーを再起動してください。"}), 500

    temp_dir = None
    try:
        attendance_file = request.files.get("attendance_pdf")
        report_file = request.files.get("report_pdf")

        if not attendance_file or not attendance_file.filename:
            return jsonify({"error": "勤怠データPDFがアップロードされていません"}), 400
        if not report_file or not report_file.filename:
            return jsonify({"error": "日報PDFがアップロードされていません"}), 400

        temp_dir = tempfile.mkdtemp()

        att_path = os.path.join(temp_dir, "attendance.pdf")
        attendance_file.save(att_path)
        if os.path.getsize(att_path) > MAX_FILE_SIZE:
            return jsonify({"error": "勤怠データPDFが大きすぎます"}), 400

        rep_path = os.path.join(temp_dir, "report.pdf")
        report_file.save(rep_path)
        if os.path.getsize(rep_path) > MAX_FILE_SIZE:
            return jsonify({"error": "日報PDFが大きすぎます"}), 400

        attendance_result = process_attendance_pdf(att_path)
        report_result = process_daily_report_pdf(rep_path)

        return jsonify({
            "attendance": attendance_result,
            "daily_reports": report_result,
        })

    except Exception as e:
        logger.error(f"アップロード処理エラー: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@report_relater_bp.route("/compare", methods=["POST"])
@login_required
def compare_data():
    """ユーザーが修正したOCR結果を照合する"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが送信されていません"}), 400

        attendance_rows = data.get("attendance", [])
        report_rows = data.get("daily_reports", [])
        employee_id_attendance = data.get("employee_id_attendance", "")

        results = perform_comparison(attendance_rows, report_rows, employee_id_attendance)
        return jsonify(results)

    except Exception as e:
        logger.error(f"照合処理エラー: {e}")
        return jsonify({"error": str(e)}), 500


@report_relater_bp.route("/download_csv", methods=["POST"])
@login_required
def download_csv():
    """照合結果をCSVでダウンロード"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが送信されていません"}), 400

        comparison_rows = data.get("comparison", [])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["日付", "ステータス", "項目", "勤怠データ値", "日報値", "一致"])

        for row in comparison_rows:
            date = row.get("date", "")
            status = row.get("status", "")
            details = row.get("details", [])

            if not details:
                writer.writerow([date, status, "", "", "", ""])
            else:
                for detail in details:
                    writer.writerow([
                        date, status,
                        detail.get("field_label", ""),
                        detail.get("attendance_value", ""),
                        detail.get("report_value", ""),
                        "O" if detail.get("match") else "X",
                    ])

        output.seek(0)
        mem = io.BytesIO()
        mem.write(b"\xef\xbb\xbf")  # BOM for Excel
        mem.write(output.getvalue().encode("utf-8"))
        mem.seek(0)

        return send_file(mem, as_attachment=True, download_name="comparison_result.csv",
                         mimetype="text/csv; charset=utf-8")

    except Exception as e:
        logger.error(f"CSVダウンロードエラー: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 勤怠データ OCR処理（シンプル版）
# ============================================================

def process_attendance_pdf(pdf_path):
    """勤怠データPDFを処理"""
    result = {
        "employee_id": "",
        "employee_name": "",
        "period": "",
        "rows": [],
        "page_images": [],
    }

    try:
        doc = fitz.open(pdf_path)
        all_text = ""

        for page_num in range(len(doc)):
            page = doc[page_num]
            zoom = PDF_RENDER_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data))
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

            # サムネイル
            thumb = pil_img.copy()
            thumb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format="PNG")
            result["page_images"].append(base64.b64encode(buf.getvalue()).decode("utf-8"))

            # 全ページOCR（数字と基本記号のみ）
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # まず数字だけのOCRを試す
            text = pytesseract.image_to_string(
                binary,
                config="--psm 6 -c tessedit_char_whitelist=0123456789/:.-,| "
            )
            all_text += text + "\n"

            # 1ページ目でヘッダー情報を取得
            if page_num == 0:
                header_info = extract_header_info(binary)
                result["employee_id"] = header_info.get("employee_id", "")
                result["period"] = header_info.get("period", "")

        doc.close()

        # テキストから行データを抽出
        result["rows"] = parse_attendance_text(all_text)

    except Exception as e:
        logger.error(f"勤怠データOCRエラー: {e}")
        logger.error(traceback.format_exc())

    return result


def extract_header_info(binary_img):
    """ヘッダーから社員番号・期間を抽出"""
    info = {"employee_id": "", "period": ""}
    try:
        h, w = binary_img.shape[:2]
        header = binary_img[0:int(h * 0.15), :]

        text = pytesseract.image_to_string(
            header,
            config="--psm 6 -c tessedit_char_whitelist=0123456789/~- "
        )

        # 7桁の社員番号
        emp_match = re.search(r"(\d{7})", text)
        if emp_match:
            info["employee_id"] = emp_match.group(1)

        # 期間
        period_match = re.search(r"(\d{4}/\d{2}/\d{2})\s*[~\-]\s*(\d{4}/\d{2}/\d{2})", text)
        if period_match:
            info["period"] = f"{period_match.group(1)} ~ {period_match.group(2)}"

    except Exception as e:
        logger.warning(f"ヘッダー抽出エラー: {e}")

    return info


def parse_attendance_text(text):
    """OCRテキストから勤怠行データを抽出"""
    rows = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 日付パターン（YYYY/MM/DD）を探す
        date_match = re.search(r"(\d{4}/\d{2}/\d{2})", line)
        if not date_match:
            continue

        date_str = date_match.group(1)

        # 行データを初期化
        row = {
            "date": date_str,
            "day_of_week": "",
            "work_type": "",
            "actual_start": "",
            "actual_end": "",
            "meter_out": "",
            "meter_in": "",
            "mileage": "",
            "vehicle_code": "",
            "site_name": "",
        }

        # 時刻パターン（HH:MM）
        times = re.findall(r"(\d{1,2}:\d{2})", line)
        if len(times) >= 4:
            row["actual_start"] = times[2]  # 実績始業
            row["actual_end"] = times[3]    # 実績終業
        elif len(times) >= 2:
            row["actual_start"] = times[0]
            row["actual_end"] = times[1]

        # 大きな数値を抽出（メーター読み取り用）
        # 3桁以上の数字を見つける
        numbers = re.findall(r"\b(\d{3,6})\b", line)

        # メーター（6桁程度の大きな数値）
        large_nums = [n for n in numbers if len(n) >= 5]
        if len(large_nums) >= 2:
            row["meter_out"] = large_nums[0]
            row["meter_in"] = large_nums[1]

        # 走行距離（2-3桁の小さな数値）
        small_nums = [n for n in numbers if 1 <= len(n) <= 3]
        if small_nums:
            # 走行距離は通常最後の方にある小さな数値
            row["mileage"] = small_nums[-1] if small_nums else ""

        # 車両番号（6桁）
        vehicle_match = re.search(r"\b(\d{6})\b", line)
        if vehicle_match:
            row["vehicle_code"] = vehicle_match.group(1)

        rows.append(row)

    return rows


# ============================================================
# 日報 OCR処理（シンプル版 - 数字のみ）
# ============================================================

def process_daily_report_pdf(pdf_path):
    """日報PDFを処理"""
    result = {
        "reports": [],
        "page_images": [],
    }

    try:
        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            zoom = PDF_RENDER_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data))
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

            # サムネイル
            thumb = pil_img.copy()
            thumb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format="PNG")
            result["page_images"].append(base64.b64encode(buf.getvalue()).decode("utf-8"))

            # 日報ページを処理
            report_data = process_daily_report_page(cv_img, page_num)
            report_data["page_number"] = page_num + 1
            result["reports"].append(report_data)

        doc.close()

    except Exception as e:
        logger.error(f"日報OCRエラー: {e}")
        logger.error(traceback.format_exc())

    return result


def process_daily_report_page(cv_img, page_num):
    """日報1ページを処理"""
    fields = {
        "date": "",
        "employee_id": "",
        "start_time": "",
        "end_time": "",
        "meter_out": "",
        "meter_in": "",
        "mileage": "",
        "vehicle_code": "",
    }

    try:
        h, w = cv_img.shape[:2]

        # 縦長なら回転（日報は横向き）
        if h > w:
            cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
            h, w = cv_img.shape[:2]

        # 右側25%だけを処理（数字フィールドが集中）
        right_region = cv_img[:, int(w * 0.75):]

        # グレースケール・二値化
        gray = cv2.cvtColor(right_region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 数字のみOCR
        text = pytesseract.image_to_string(
            binary,
            config="--psm 6 -c tessedit_char_whitelist=0123456789/:.-"
        )

        logger.info(f"日報ページ{page_num + 1} OCR結果: {text[:200]}...")

        # パターンマッチングで各フィールドを抽出
        fields = extract_daily_report_fields(text)

        # 各フィールドを個別領域からも試す（より正確に）
        fields = extract_fields_by_region(cv_img, fields)

    except Exception as e:
        logger.error(f"日報ページ処理エラー (ページ{page_num + 1}): {e}")
        logger.error(traceback.format_exc())

    return fields


def extract_daily_report_fields(text):
    """OCRテキストから日報フィールドを抽出"""
    fields = {
        "date": "",
        "employee_id": "",
        "start_time": "",
        "end_time": "",
        "meter_out": "",
        "meter_in": "",
        "mileage": "",
        "vehicle_code": "",
    }

    lines = text.split("\n")
    all_numbers = []

    for line in lines:
        # 数字を抽出
        nums = re.findall(r"\d+", line)
        all_numbers.extend(nums)

    # 7桁 = 社員番号
    for num in all_numbers:
        if len(num) == 7:
            fields["employee_id"] = num
            break

    # 6桁 = 車両番号（ハイフンがあったものを結合）
    vehicle_match = re.search(r"(\d{4})[.\-]?(\d{2})", text)
    if vehicle_match:
        fields["vehicle_code"] = vehicle_match.group(1) + vehicle_match.group(2)
    else:
        for num in all_numbers:
            if len(num) == 6:
                fields["vehicle_code"] = num
                break

    # 5桁 = メーター読み取り
    five_digit = [n for n in all_numbers if len(n) == 5]
    if len(five_digit) >= 2:
        fields["meter_out"] = five_digit[0]
        fields["meter_in"] = five_digit[1]
    elif len(five_digit) == 1:
        fields["meter_out"] = five_digit[0]

    # 時刻パターン
    time_matches = re.findall(r"(\d{1,2}):(\d{2})", text)
    if len(time_matches) >= 2:
        fields["start_time"] = f"{time_matches[0][0]}:{time_matches[0][1]}"
        fields["end_time"] = f"{time_matches[1][0]}:{time_matches[1][1]}"

    # 1-3桁 = 走行距離（最後に出てくるもの）
    small_nums = [n for n in all_numbers if 1 <= len(n) <= 3]
    if small_nums:
        fields["mileage"] = small_nums[-1]

    # 日付
    date_match = re.search(r"(\d{4})[./](\d{1,2})[./](\d{1,2})", text)
    if date_match:
        fields["date"] = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}"

    return fields


def extract_fields_by_region(cv_img, fields):
    """定義された領域から各フィールドを抽出"""
    h, w = cv_img.shape[:2]

    for field_name, field_def in DAILY_REPORT_FIELDS.items():
        try:
            rx, ry, rw, rh = field_def["region"]
            x = int(rx * w)
            y = int(ry * h)
            region_w = int(rw * w)
            region_h = int(rh * h)

            # 境界チェック
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            region_w = min(region_w, w - x)
            region_h = min(region_h, h - y)

            if region_w <= 10 or region_h <= 10:
                continue

            region = cv_img[y:y + region_h, x:x + region_w]

            # 前処理
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

            # コントラスト強調
            gray = cv2.equalizeHist(gray)

            # 二値化
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # 小さい場合は拡大
            h_r, w_r = binary.shape[:2]
            if h_r < 30:
                scale = 30.0 / h_r
                binary = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

            # OCR（数字のみ）
            text = pytesseract.image_to_string(
                binary,
                config="--psm 7 -c tessedit_char_whitelist=0123456789/:.-"
            ).strip()

            # 結果があれば更新
            if text and not fields.get(field_name):
                # 後処理
                text = re.sub(r"[^0123456789/:.-]", "", text)

                if field_name == "vehicle_code":
                    text = re.sub(r"[^\d]", "", text)
                    if len(text) == 6:
                        fields[field_name] = text
                elif field_name in ("meter_out", "meter_in"):
                    text = re.sub(r"[^\d]", "", text)
                    if len(text) >= 4:
                        fields[field_name] = text
                elif field_name == "mileage":
                    text = re.sub(r"[^\d]", "", text)
                    if text:
                        fields[field_name] = text
                elif field_name == "employee_id":
                    text = re.sub(r"[^\d]", "", text)
                    if len(text) == 7:
                        fields[field_name] = text
                elif field_name in ("start_time", "end_time"):
                    if ":" in text or len(text) >= 3:
                        fields[field_name] = text
                else:
                    fields[field_name] = text

        except Exception as e:
            logger.warning(f"フィールド {field_name} 抽出エラー: {e}")

    return fields


# ============================================================
# 照合処理
# ============================================================

def perform_comparison(attendance_rows, report_rows, employee_id_attendance):
    """勤怠データと日報を照合"""
    results = {
        "employee_id": employee_id_attendance,
        "total_attendance": len(attendance_rows),
        "total_reports": len(report_rows),
        "matched": 0,
        "mismatched": 0,
        "no_report": 0,
        "report_only": 0,
        "comparison": [],
    }

    # 日付でインデックス化
    report_by_date = {}
    for report in report_rows:
        date = normalize_date(report.get("date", ""))
        if date:
            if date not in report_by_date:
                report_by_date[date] = []
            report_by_date[date].append(report)

    attendance_by_date = {}
    for att in attendance_rows:
        date = normalize_date(att.get("date", ""))
        if date:
            if date not in attendance_by_date:
                attendance_by_date[date] = []
            attendance_by_date[date].append(att)

    all_dates = sorted(set(list(attendance_by_date.keys()) + list(report_by_date.keys())))

    for date in all_dates:
        att_list = attendance_by_date.get(date, [])
        rep_list = report_by_date.get(date, [])

        if att_list and not rep_list:
            for att in att_list:
                results["comparison"].append({
                    "date": date,
                    "status": "no_report",
                    "status_label": "日報なし",
                    "attendance": att,
                    "report": None,
                    "details": [],
                })
                results["no_report"] += 1

        elif rep_list and not att_list:
            for rep in rep_list:
                results["comparison"].append({
                    "date": date,
                    "status": "report_only",
                    "status_label": "日報のみ",
                    "attendance": None,
                    "report": rep,
                    "details": [],
                })
                results["report_only"] += 1

        else:
            max_count = max(len(att_list), len(rep_list))
            for i in range(max_count):
                att = att_list[i] if i < len(att_list) else None
                rep = rep_list[i] if i < len(rep_list) else None

                if att and rep:
                    details = compare_single_pair(att, rep)
                    has_mismatch = any(not d["match"] for d in details)

                    if has_mismatch:
                        results["mismatched"] += 1
                        status = "mismatch"
                    else:
                        results["matched"] += 1
                        status = "match"

                    results["comparison"].append({
                        "date": date,
                        "status": status,
                        "status_label": "不一致あり" if has_mismatch else "一致",
                        "attendance": att,
                        "report": rep,
                        "details": details,
                    })
                elif att:
                    results["comparison"].append({
                        "date": date,
                        "status": "no_report",
                        "status_label": "日報なし",
                        "attendance": att,
                        "report": None,
                        "details": [],
                    })
                    results["no_report"] += 1
                elif rep:
                    results["comparison"].append({
                        "date": date,
                        "status": "report_only",
                        "status_label": "日報のみ",
                        "attendance": None,
                        "report": rep,
                        "details": [],
                    })
                    results["report_only"] += 1

    return results


def compare_single_pair(attendance, report):
    """勤怠1行と日報1枚を比較"""
    details = []

    for att_key, rep_key in COMPARISON_FIELDS.items():
        att_val = normalize_value(attendance.get(att_key, ""), att_key)
        rep_val = normalize_value(report.get(rep_key, ""), rep_key)

        details.append({
            "field": att_key,
            "field_label": COMPARISON_FIELD_LABELS.get(att_key, att_key),
            "attendance_value": attendance.get(att_key, ""),
            "report_value": report.get(rep_key, ""),
            "match": att_val == rep_val,
        })

    return details


def normalize_value(value, field_key):
    """比較用に値を正規化"""
    if not value:
        return ""

    value = str(value).strip()

    if field_key in ("meter_out", "meter_in", "mileage"):
        value = re.sub(r"[^\d]", "", value)

    elif field_key in ("actual_start", "actual_end", "start_time", "end_time"):
        value = re.sub(r"[^\d:]", "", value)
        if ":" not in value and len(value) >= 3:
            value = value[:-2] + ":" + value[-2:]
        parts = value.split(":")
        if len(parts) == 2:
            try:
                value = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            except:
                pass

    elif field_key == "vehicle_code":
        value = re.sub(r"[^\d]", "", value)
        if value:
            value = value.zfill(6)

    return value


def normalize_date(date_str):
    """日付を正規化"""
    if not date_str:
        return ""

    date_str = str(date_str).strip()

    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_str)
    if match:
        return f"{match.group(1)}/{int(match.group(2)):02d}/{int(match.group(3)):02d}"

    match = re.search(r"(\d{1,2})/(\d{1,2})", date_str)
    if match:
        return f"__/{int(match.group(1)):02d}/{int(match.group(2)):02d}"

    return date_str
