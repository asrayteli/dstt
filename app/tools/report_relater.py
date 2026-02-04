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
import json

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
        return False

    # まず現在の設定でテスト
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        pass

    # Windows: よくあるインストール先を探索
    if os.name == 'nt':
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Tesseract検出: {path}")
                try:
                    pytesseract.get_tesseract_version()
                    return True
                except Exception:
                    continue

    logger.error(
        "Tesseractが見つかりません。"
        "Windowsの場合: https://github.com/UB-Mannheim/tesseract/wiki から"
        "インストーラーをダウンロードし、日本語言語パックも選択してインストールしてください。"
    )
    return False


TESSERACT_CONFIGURED = _configure_tesseract()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
PDF_RENDER_DPI = 300  # OCR用レンダリング解像度


# ============================================================
# 日報フィールド定義（正規化後の相対座標）
# 形式: (x%, y%, w%, h%) — 正規化フォーム画像に対する割合
# これらは初期推定値で、実データで調整が必要な場合があります
# ============================================================
# 日報は横向き(landscape)で読む前提
# 注意: 実際のスキャンデータに合わせて座標を調整してください
DAILY_REPORT_FIELDS = {
    "date": {
        "label": "日付",
        "region": (0.78, 0.01, 0.20, 0.06),
        "ocr_config": "--psm 7 -l jpn",
        "type": "text",
    },
    "employee_id": {
        "label": "社員番号",
        "region": (0.78, 0.07, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789",
        "type": "numeric",
    },
    "start_time": {
        "label": "始業時刻",
        "region": (0.86, 0.22, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789:",
        "type": "time",
    },
    "end_time": {
        "label": "終業時刻",
        "region": (0.86, 0.28, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789:",
        "type": "time",
    },
    "meter_out": {
        "label": "出庫メーター",
        "region": (0.86, 0.34, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789,.",
        "type": "numeric",
    },
    "meter_in": {
        "label": "入庫メーター",
        "region": (0.86, 0.40, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789,.",
        "type": "numeric",
    },
    "mileage": {
        "label": "粁数",
        "region": (0.86, 0.46, 0.12, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789",
        "type": "numeric",
    },
    "vehicle_code": {
        "label": "車両番号(6ケタ)",
        "region": (0.78, 0.13, 0.20, 0.05),
        "ocr_config": "--psm 7 -c tessedit_char_whitelist=0123456789-",
        "type": "vehicle_code",
    },
}

# 比較対象フィールドのマッピング
# attendance_key → daily_report_key
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
# ルート定義
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
        return jsonify({"error": "OpenCVがインストールされていません。pip install opencv-python-headless を実行してください。"}), 500
    if not TESSERACT_AVAILABLE:
        return jsonify({"error": "pytesseractがインストールされていません。pip install pytesseract を実行してください。"}), 500
    if not TESSERACT_CONFIGURED:
        return jsonify({"error": "Tesseract OCRエンジンが見つかりません。https://github.com/UB-Mannheim/tesseract/wiki からインストールし、日本語言語パック(jpn)も選択してください。インストール後にサーバーを再起動してください。"}), 500

    temp_dir = None
    try:
        attendance_file = request.files.get("attendance_pdf")
        report_file = request.files.get("report_pdf")

        if not attendance_file or not attendance_file.filename:
            return jsonify({"error": "勤怠データPDFがアップロードされていません"}), 400
        if not report_file or not report_file.filename:
            return jsonify({"error": "日報PDFがアップロードされていません"}), 400

        temp_dir = tempfile.mkdtemp()

        # 勤怠データPDF保存
        att_path = os.path.join(temp_dir, "attendance.pdf")
        attendance_file.save(att_path)
        if os.path.getsize(att_path) > MAX_FILE_SIZE:
            return jsonify({"error": "勤怠データPDFが大きすぎます(100MB以下)"}), 400

        # 日報PDF保存
        rep_path = os.path.join(temp_dir, "report.pdf")
        report_file.save(rep_path)
        if os.path.getsize(rep_path) > MAX_FILE_SIZE:
            return jsonify({"error": "日報PDFが大きすぎます(100MB以下)"}), 400

        # 勤怠データOCR
        attendance_result = process_attendance_pdf(att_path, temp_dir)

        # 日報OCR
        report_result = process_daily_report_pdf(rep_path, temp_dir)

        result = {
            "attendance": attendance_result,
            "daily_reports": report_result,
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f"アップロード処理エラー: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


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
        logger.error(traceback.format_exc())
        return jsonify({"error": f"照合処理中にエラーが発生しました: {str(e)}"}), 500


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

        # ヘッダー
        writer.writerow([
            "日付",
            "ステータス",
            "項目",
            "勤怠データ値",
            "日報値",
            "一致",
        ])

        for row in comparison_rows:
            date = row.get("date", "")
            status = row.get("status", "")
            details = row.get("details", [])

            if not details:
                writer.writerow([date, status, "", "", "", ""])
            else:
                for detail in details:
                    writer.writerow([
                        date,
                        status,
                        detail.get("field_label", ""),
                        detail.get("attendance_value", ""),
                        detail.get("report_value", ""),
                        "O" if detail.get("match") else "X",
                    ])

        output.seek(0)
        mem = io.BytesIO()
        # BOM付きUTF-8でExcel対応
        mem.write(b"\xef\xbb\xbf")
        mem.write(output.getvalue().encode("utf-8"))
        mem.seek(0)

        return send_file(
            mem,
            as_attachment=True,
            download_name="comparison_result.csv",
            mimetype="text/csv; charset=utf-8",
        )

    except Exception as e:
        logger.error(f"CSVダウンロードエラー: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"CSV生成エラー: {str(e)}"}), 500


# ============================================================
# 勤怠データ OCR処理
# ============================================================


def process_attendance_pdf(pdf_path, temp_dir):
    """勤怠データPDFをOCRして構造化データを返す"""
    result = {
        "employee_id": "",
        "employee_name": "",
        "period": "",
        "rows": [],
        "page_images": [],
    }

    try:
        doc = fitz.open(pdf_path)

        all_rows = []
        for page_num in range(len(doc)):
            page = doc[page_num]

            # 高解像度で画像化
            zoom = PDF_RENDER_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            # PIL Image → numpy → OpenCV
            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data))
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

            # ページ画像をbase64で返す（表示用、縮小版）
            thumb = pil_img.copy()
            thumb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format="PNG")
            page_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            result["page_images"].append(page_b64)

            # テーブル行抽出
            rows = extract_attendance_table(cv_img, page_num)

            # 1ページ目からヘッダー情報を取得
            if page_num == 0:
                header_info = extract_attendance_header(cv_img)
                result["employee_id"] = header_info.get("employee_id", "")
                result["employee_name"] = header_info.get("employee_name", "")
                result["period"] = header_info.get("period", "")

            all_rows.extend(rows)

        doc.close()
        result["rows"] = all_rows

    except Exception as e:
        logger.error(f"勤怠データOCRエラー: {e}")
        logger.error(traceback.format_exc())
        result["error"] = str(e)

    return result


def extract_attendance_header(cv_img):
    """勤怠データの先頭部分から社員番号・名前・期間を抽出"""
    info = {"employee_id": "", "employee_name": "", "period": ""}
    try:
        h, w = cv_img.shape[:2]
        # ヘッダー領域（上部15%程度）
        header_region = cv_img[0 : int(h * 0.12), :]

        gray = cv2.cvtColor(header_region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        text = pytesseract.image_to_string(binary, lang="jpn", config="--psm 6")

        # 社員番号を検出 (7桁数字パターン)
        emp_match = re.search(r"(\d{7})", text)
        if emp_match:
            info["employee_id"] = emp_match.group(1)

        # 期間を検出
        period_match = re.search(
            r"(\d{4}/\d{2}/\d{2})\s*[~～〜]\s*(\d{4}/\d{2}/\d{2})", text
        )
        if period_match:
            info["period"] = f"{period_match.group(1)} ~ {period_match.group(2)}"

        # 名前は社員番号の近くにあるはず
        name_match = re.search(r"([^\d\s/~～]{2,6})\s+([^\d\s/~～]{1,4})", text)
        if name_match:
            info["employee_name"] = f"{name_match.group(1)} {name_match.group(2)}"

    except Exception as e:
        logger.warning(f"ヘッダー抽出警告: {e}")

    return info


def extract_attendance_table(cv_img, page_num):
    """勤怠データの表を解析して行データを返す"""
    rows = []
    try:
        h, w = cv_img.shape[:2]
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

        # 表領域を検出（上部ヘッダーを除外）
        table_region = gray[int(h * 0.12) :, :]
        table_color = cv_img[int(h * 0.12) :, :]
        table_h, table_w = table_region.shape[:2]

        # 二値化
        _, binary = cv2.threshold(
            table_region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

        # 水平線検出
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (table_w // 5, 1))
        horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)

        # 垂直線検出
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, table_h // 10))
        vert_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel)

        # 格子を合成
        grid = cv2.add(horiz_lines, vert_lines)

        # 輪郭検出でセルを見つける
        contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        # セルの境界を取得
        cells = []
        min_cell_area = (table_w * table_h) * 0.0001  # 最小セルサイズ
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            if area > min_cell_area and cw > 10 and ch > 10:
                cells.append((x, y, cw, ch))

        if not cells:
            # セル検出できなかった場合は全体OCR
            return extract_attendance_fullpage(cv_img, page_num)

        # セルをY座標でグループ化（行として）
        cells.sort(key=lambda c: (c[1], c[0]))

        # 行をグループ化
        row_groups = []
        current_row = [cells[0]]
        for cell in cells[1:]:
            # 同じ行（Y座標が近い）
            if abs(cell[1] - current_row[0][1]) < 15:
                current_row.append(cell)
            else:
                row_groups.append(sorted(current_row, key=lambda c: c[0]))
                current_row = [cell]
        row_groups.append(sorted(current_row, key=lambda c: c[0]))

        # ヘッダー行をスキップし、データ行を処理
        # 最初の2-3行はヘッダー（期間・列名等）
        data_start = min(3, len(row_groups) - 1)

        for row_idx, row_cells in enumerate(row_groups[data_start:]):
            row_data = extract_attendance_row_from_cells(
                table_region, row_cells, row_idx
            )
            if row_data and row_data.get("date"):
                rows.append(row_data)

    except Exception as e:
        logger.warning(f"テーブル抽出警告 (ページ{page_num + 1}): {e}")
        # フォールバック: 全体OCR
        rows = extract_attendance_fullpage(cv_img, page_num)

    return rows


def extract_attendance_row_from_cells(table_img, cells, row_idx):
    """セルからOCRして1行分のデータを返す"""
    row = {
        "date": "",
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

    # セルの数に基づいてカラムをマッピング
    # 勤怠データの主要カラム位置（セル数に応じて調整）
    cell_texts = []
    for x, y, w, h in cells:
        cell_img = table_img[y : y + h, x : x + w]
        text = ocr_cell(cell_img, "general")
        cell_texts.append(text.strip())

    if len(cell_texts) < 5:
        return None

    # カラム位置の推定（勤怠データフォーマットに基づく）
    # 典型的な配列: 日付, 曜日, 勤務種別, 予定始業, 予定終業,
    #               実績始業, 実績終業, 実績休憩, 法定内, 法定外, 深夜,
    #               休日, 休暇, メーター出庫, メーター入庫, 走行,
    #               営業泊, 現場名, 契約, 車両コード
    try:
        if len(cell_texts) >= 3:
            row["date"] = cell_texts[0]
            row["day_of_week"] = cell_texts[1] if len(cell_texts) > 1 else ""
            row["work_type"] = cell_texts[2] if len(cell_texts) > 2 else ""

        if len(cell_texts) >= 8:
            row["actual_start"] = cell_texts[5] if len(cell_texts) > 5 else ""
            row["actual_end"] = cell_texts[6] if len(cell_texts) > 6 else ""

        if len(cell_texts) >= 16:
            row["meter_out"] = cell_texts[13] if len(cell_texts) > 13 else ""
            row["meter_in"] = cell_texts[14] if len(cell_texts) > 14 else ""
            row["mileage"] = cell_texts[15] if len(cell_texts) > 15 else ""

        if len(cell_texts) >= 20:
            row["site_name"] = cell_texts[17] if len(cell_texts) > 17 else ""
            row["vehicle_code"] = cell_texts[19] if len(cell_texts) > 19 else ""
        elif len(cell_texts) >= 18:
            row["vehicle_code"] = cell_texts[-1]

    except (IndexError, Exception) as e:
        logger.warning(f"行データ解析警告 (行{row_idx}): {e}")

    return row


def extract_attendance_fullpage(cv_img, page_num):
    """全体OCRで勤怠データを抽出するフォールバック"""
    rows = []
    try:
        h, w = cv_img.shape[:2]
        # データ部分のみ（ヘッダー除外）
        data_region = cv_img[int(h * 0.15) :, :]

        gray = cv2.cvtColor(data_region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        text = pytesseract.image_to_string(binary, lang="jpn", config="--psm 6")

        # 日付パターンで行を分割
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 日付パターン検索（YYYY/MM/DD or MM/DD）
            date_match = re.search(r"(\d{4}/\d{2}/\d{2})", line)
            if not date_match:
                continue

            row = parse_attendance_line(line, date_match.group(1))
            if row:
                rows.append(row)

    except Exception as e:
        logger.warning(f"全体OCR警告 (ページ{page_num + 1}): {e}")

    return rows


def parse_attendance_line(line, date_str):
    """OCRテキスト行から勤怠データを解析"""
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
        # 予定始業, 予定終業, 実績始業, 実績終業 の順
        row["actual_start"] = times[2]
        row["actual_end"] = times[3]
    elif len(times) >= 2:
        row["actual_start"] = times[0]
        row["actual_end"] = times[1]

    # 曜日検出
    dow_match = re.search(r"[月火水木金土日]", line)
    if dow_match:
        row["day_of_week"] = dow_match.group(0)

    # 勤務種別
    for wt in ["出勤", "休日", "休日出勤", "有休", "欠勤"]:
        if wt in line:
            row["work_type"] = wt
            break

    # 大きな数値（メーター）を検索
    numbers = re.findall(r"(\d{2,6}[,.]?\d{0,3})", line)
    large_numbers = [n for n in numbers if len(n.replace(",", "").replace(".", "")) >= 3]
    if len(large_numbers) >= 2:
        row["meter_out"] = large_numbers[-2]
        row["meter_in"] = large_numbers[-1]

    # 6桁数値（車両番号）
    vehicle_match = re.search(r"(\d{6})", line)
    if vehicle_match:
        row["vehicle_code"] = vehicle_match.group(1)

    return row


# ============================================================
# 日報 OCR処理
# ============================================================


def process_daily_report_pdf(pdf_path, temp_dir):
    """日報PDFをOCRして構造化データを返す"""
    result = {
        "reports": [],
        "page_images": [],
    }

    try:
        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]

            # 高解像度で画像化
            zoom = PDF_RENDER_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data))
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

            # ページ画像base64（表示用）
            thumb = pil_img.copy()
            thumb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format="PNG")
            page_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            result["page_images"].append(page_b64)

            # 日報ページを処理
            report_data = process_daily_report_page(cv_img, page_num)
            report_data["page_number"] = page_num + 1
            result["reports"].append(report_data)

        doc.close()

    except Exception as e:
        logger.error(f"日報OCRエラー: {e}")
        logger.error(traceback.format_exc())
        result["error"] = str(e)

    return result


def process_daily_report_page(cv_img, page_num):
    """日報1ページを処理してフィールドデータを返す"""
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

        # 縦長(portrait)なら横向きに回転（日報は横フォーマット）
        if h > w:
            cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
            h, w = cv_img.shape[:2]

        # フォーム境界を検出して正規化
        normalized = normalize_form_image(cv_img)
        if normalized is None:
            normalized = cv_img

        # 各フィールド領域を抽出してOCR
        for field_name, field_def in DAILY_REPORT_FIELDS.items():
            try:
                rx, ry, rw, rh = field_def["region"]
                fh, fw = normalized.shape[:2]

                x = max(0, int(rx * fw))
                y = max(0, int(ry * fh))
                region_w = min(int(rw * fw), fw - x)
                region_h = min(int(rh * fh), fh - y)

                if region_w <= 0 or region_h <= 0:
                    continue

                region = normalized[y : y + region_h, x : x + region_w]

                # 前処理
                processed = preprocess_field_image(region, field_def["type"])

                # OCR
                ocr_config = field_def["ocr_config"]
                text = pytesseract.image_to_string(processed, config=ocr_config)
                text = text.strip()

                # 後処理
                text = postprocess_field_text(text, field_def["type"])

                fields[field_name] = text

            except Exception as e:
                logger.warning(
                    f"フィールド{field_name}のOCRエラー (ページ{page_num + 1}): {e}"
                )

    except Exception as e:
        logger.error(f"日報ページ処理エラー (ページ{page_num + 1}): {e}")
        logger.error(traceback.format_exc())

    return fields


def normalize_form_image(cv_img):
    """フォーム画像の傾き補正と境界検出"""
    try:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        # エッジ検出
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)

        # 膨張処理で線を太くする
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        # 最大の矩形輪郭を検出
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if not contours:
            return deskew_image(cv_img)

        # 面積が最大の輪郭
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)

        # 画像面積の30%以上のみ処理
        if area < (h * w * 0.3):
            return deskew_image(cv_img)

        # 輪郭を近似して4点を取得
        epsilon = 0.02 * cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, epsilon, True)

        if len(approx) == 4:
            # 透視変換
            pts = order_points(approx.reshape(4, 2))
            dst_w, dst_h = compute_output_dimensions(pts)

            if dst_w > 100 and dst_h > 100:
                dst = np.array(
                    [[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]],
                    dtype=np.float32,
                )
                M = cv2.getPerspectiveTransform(pts, dst)
                warped = cv2.warpPerspective(cv_img, M, (int(dst_w), int(dst_h)))
                return warped

        # 4点取得できなかった場合はデスキューのみ
        return deskew_image(cv_img)

    except Exception as e:
        logger.warning(f"フォーム正規化警告: {e}")
        return deskew_image(cv_img)


def deskew_image(cv_img):
    """画像の傾きを検出して補正する"""
    try:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)

        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10
        )

        if lines is None:
            return cv_img

        # 水平に近い線の角度を集める
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if abs(x2 - x1) > 0:
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if abs(angle) < 10:  # ±10度以内のみ
                    angles.append(angle)

        if not angles:
            return cv_img

        median_angle = np.median(angles)

        if abs(median_angle) < 0.1:
            return cv_img

        h, w = cv_img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
        rotated = cv2.warpAffine(
            cv_img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
        )

        return rotated

    except Exception as e:
        logger.warning(f"デスキュー警告: {e}")
        return cv_img


def order_points(pts):
    """4点を[左上, 右上, 右下, 左下]の順序に並べる"""
    rect = np.zeros((4, 2), dtype=np.float32)

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # 左上（x+yが最小）
    rect[2] = pts[np.argmax(s)]  # 右下（x+yが最大）

    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]  # 右上（x-yが最小）
    rect[3] = pts[np.argmax(d)]  # 左下（x-yが最大）

    return rect


def compute_output_dimensions(pts):
    """4点から出力画像のサイズを計算"""
    (tl, tr, br, bl) = pts

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b))

    return max_width, max_height


def preprocess_field_image(region, field_type):
    """フィールド画像の前処理"""
    try:
        # グレースケール化
        if len(region.shape) == 3:
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        else:
            gray = region.copy()

        # リサイズ（小さすぎる場合は拡大）
        h, w = gray.shape[:2]
        if h < 30:
            scale = 30.0 / h
            gray = cv2.resize(
                gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )

        # ノイズ除去
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # 適応的二値化
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 10
        )

        # 数値フィールドの場合は追加処理
        if field_type in ("numeric", "time", "vehicle_code"):
            # モルフォロジー処理でノイズ除去
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        return binary

    except Exception as e:
        logger.warning(f"前処理警告: {e}")
        return region


def postprocess_field_text(text, field_type):
    """OCR結果の後処理"""
    if not text:
        return ""

    # 共通: 不要な空白・改行を除去
    text = text.replace("\n", "").replace("\r", "").strip()

    if field_type == "numeric":
        # 数値のみ残す（カンマ・ピリオドは保持）
        text = re.sub(r"[^\d,.]", "", text)

    elif field_type == "time":
        # 時刻形式に整形
        text = re.sub(r"[^\d:]", "", text)
        # コロンがない場合は補完（例: "0730" → "07:30"）
        if ":" not in text and len(text) >= 3:
            text = text[:-2] + ":" + text[-2:]

    elif field_type == "vehicle_code":
        # ハイフン・スペース除去、数字のみ
        text = re.sub(r"[^\d]", "", text)
        # 6桁にパディング
        if text and len(text) <= 6:
            text = text.zfill(6)

    return text


def ocr_cell(cell_img, cell_type="general"):
    """単一セルをOCRする"""
    try:
        if len(cell_img.shape) == 3:
            gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = cell_img

        # リサイズ（小さい場合）
        h, w = gray.shape[:2]
        if h < 20 or w < 20:
            return ""

        if h < 30:
            scale = 30.0 / h
            gray = cv2.resize(
                gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )

        # 二値化
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        if cell_type == "numeric":
            config = "--psm 7 -c tessedit_char_whitelist=0123456789,."
        elif cell_type == "time":
            config = "--psm 7 -c tessedit_char_whitelist=0123456789:"
        else:
            config = "--psm 7 -l jpn"

        text = pytesseract.image_to_string(binary, config=config)
        return text.strip()

    except Exception as e:
        return ""


# ============================================================
# 照合処理
# ============================================================


def perform_comparison(attendance_rows, report_rows, employee_id_attendance):
    """勤怠データと日報を照合する"""
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

    # 日報を日付でインデックス化（1日に複数枚の可能性あり）
    report_by_date = {}
    for report in report_rows:
        date = normalize_date(report.get("date", ""))
        if date:
            if date not in report_by_date:
                report_by_date[date] = []
            report_by_date[date].append(report)

    # 勤怠データを日付でインデックス化（1日に複数行の可能性あり）
    attendance_by_date = {}
    for att_row in attendance_rows:
        date = normalize_date(att_row.get("date", ""))
        if date:
            if date not in attendance_by_date:
                attendance_by_date[date] = []
            attendance_by_date[date].append(att_row)

    # 全日付を収集
    all_dates = sorted(set(list(attendance_by_date.keys()) + list(report_by_date.keys())))

    for date in all_dates:
        att_list = attendance_by_date.get(date, [])
        rep_list = report_by_date.get(date, [])

        if att_list and not rep_list:
            # 勤怠データのみ（日報なし）
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
            # 日報のみ（勤怠データなし）
            for rep in rep_list:
                results["comparison"].append({
                    "date": date,
                    "status": "report_only",
                    "status_label": "日報のみ（勤怠データなし）",
                    "attendance": None,
                    "report": rep,
                    "details": [],
                })
                results["report_only"] += 1

        else:
            # 両方あり → 照合
            # 同日の複数行をペアリング（順番で対応）
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
                        status_label = "不一致あり"
                    else:
                        results["matched"] += 1
                        status = "match"
                        status_label = "一致"

                    results["comparison"].append({
                        "date": date,
                        "status": status,
                        "status_label": status_label,
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

        match = att_val == rep_val

        details.append({
            "field": att_key,
            "field_label": COMPARISON_FIELD_LABELS.get(att_key, att_key),
            "attendance_value": attendance.get(att_key, ""),
            "report_value": report.get(rep_key, ""),
            "normalized_attendance": att_val,
            "normalized_report": rep_val,
            "match": match,
        })

    return details


def normalize_value(value, field_key):
    """比較用に値を正規化する"""
    if not value:
        return ""

    value = str(value).strip()

    if field_key in ("meter_out", "meter_in", "mileage"):
        # カンマ・ピリオド・スペース除去、純粋な数値に
        value = re.sub(r"[,.\s]", "", value)

    elif field_key in ("actual_start", "actual_end", "start_time", "end_time"):
        # 時刻形式の正規化 → HH:MM
        value = re.sub(r"[^\d:]", "", value)
        if ":" not in value and len(value) >= 3:
            value = value[:-2] + ":" + value[-2:]
        # 先頭0パディング
        parts = value.split(":")
        if len(parts) == 2:
            value = f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    elif field_key == "vehicle_code":
        # ハイフン等除去、6桁に
        value = re.sub(r"[^\d]", "", value)
        if value:
            value = value.zfill(6)

    return value


def normalize_date(date_str):
    """日付文字列を正規化（YYYY/MM/DD形式に）"""
    if not date_str:
        return ""

    date_str = date_str.strip()

    # YYYY/MM/DD
    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_str)
    if match:
        return f"{match.group(1)}/{int(match.group(2)):02d}/{int(match.group(3)):02d}"

    # 和暦パターン（令和X年M月D日 等）
    match = re.search(r"(\d{1,2})年(\d{1,2})月(\d{1,2})日", date_str)
    if match:
        # 年の部分は正確でない可能性があるのでMM/DDだけ使用
        return f"__/{int(match.group(2)):02d}/{int(match.group(3)):02d}"

    # MM/DD のみ
    match = re.search(r"(\d{1,2})/(\d{1,2})", date_str)
    if match:
        return f"__/{int(match.group(1)):02d}/{int(match.group(2)):02d}"

    return date_str
