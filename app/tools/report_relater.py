from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user
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
from datetime import datetime

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
# プリセット管理
# ============================================================
PRESETS_FILE = os.path.join(os.path.dirname(__file__), "report_relater_presets.json")


def load_presets():
    """プリセットをファイルから読み込む"""
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"プリセット読み込みエラー: {e}")
    return {"presets": []}


def save_presets(data):
    """プリセットをファイルに保存"""
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"プリセット保存エラー: {e}")
        return False


def get_preset_by_id(preset_id):
    """IDでプリセットを取得"""
    data = load_presets()
    for preset in data.get("presets", []):
        if preset.get("id") == preset_id:
            return preset
    return None


def get_default_fields():
    """デフォルトのフィールド定義を返す"""
    return {
        "attendance": {
            # テーブル形式設定
            "table_config": {
                "header_region": [0, 0, 1.0, 0.08],  # ヘッダー領域（社員番号・期間）
                "data_start_y": 0.12,  # データ行の開始Y位置
                "row_height": 0.022,   # 1行の高さ
                "max_rows": 31,        # 最大行数（1ヶ月分）
            },
            # カラム定義（X位置と幅）
            "columns": [
                {"name": "date", "label": "月日", "x": 0.02, "width": 0.04},
                {"name": "day_of_week", "label": "曜", "x": 0.06, "width": 0.02},
                {"name": "actual_start", "label": "実績始業", "x": 0.22, "width": 0.04},
                {"name": "actual_end", "label": "実績終業", "x": 0.26, "width": 0.04},
                {"name": "meter_out", "label": "出庫メーター", "x": 0.60, "width": 0.05},
                {"name": "meter_in", "label": "入庫メーター", "x": 0.65, "width": 0.05},
                {"name": "mileage", "label": "走行キロ", "x": 0.70, "width": 0.04},
                {"name": "vehicle_code", "label": "車両番号", "x": 0.92, "width": 0.06},
            ]
        },
        "daily_report": {
            "main_region": [0.75, 0, 0.25, 1.0],
            "fields": [
                {"name": "employee_id", "label": "社員番号", "region": [0.80, 0.00, 0.20, 0.06]},
                {"name": "date", "label": "日付", "region": [0.80, 0.05, 0.20, 0.06]},
                {"name": "vehicle_code", "label": "車両番号", "region": [0.80, 0.10, 0.20, 0.05]},
                {"name": "start_time", "label": "始業時刻", "region": [0.88, 0.17, 0.12, 0.05]},
                {"name": "end_time", "label": "終業時刻", "region": [0.88, 0.21, 0.12, 0.05]},
                {"name": "meter_out", "label": "出庫メーター", "region": [0.80, 0.25, 0.20, 0.05]},
                {"name": "meter_in", "label": "入庫メーター", "region": [0.80, 0.30, 0.20, 0.05]},
                {"name": "mileage", "label": "走行粁", "region": [0.80, 0.35, 0.20, 0.05]},
            ]
        }
    }


# ============================================================
# 日報フィールド定義（デフォルト - プリセット未使用時）
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
    presets = load_presets().get("presets", [])
    return render_template("report_relater.html", presets=presets)


# ============================================================
# プリセット管理ルート
# ============================================================

@report_relater_bp.route("/presets", methods=["GET"])
@login_required
def presets_page():
    """プリセット設定ページ"""
    return render_template("report_relater_presets.html")


@report_relater_bp.route("/api/presets", methods=["GET"])
@login_required
def get_presets():
    """プリセット一覧を取得"""
    data = load_presets()
    return jsonify(data)


@report_relater_bp.route("/api/presets", methods=["POST"])
@login_required
def create_preset():
    """新規プリセットを作成"""
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"error": "データが送信されていません"}), 400

        name = req_data.get("name", "").strip()
        if not name:
            return jsonify({"error": "プリセット名を入力してください"}), 400

        data = load_presets()
        presets = data.get("presets", [])

        # 新しいIDを生成
        max_id = 0
        for p in presets:
            try:
                pid = int(p.get("id", 0))
                if pid > max_id:
                    max_id = pid
            except:
                pass

        new_preset = {
            "id": str(max_id + 1),
            "name": name,
            "created_by": getattr(current_user, 'username', 'unknown'),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "attendance": req_data.get("attendance", get_default_fields()["attendance"]),
            "daily_report": req_data.get("daily_report", get_default_fields()["daily_report"]),
        }

        presets.append(new_preset)
        data["presets"] = presets

        if save_presets(data):
            return jsonify({"success": True, "preset": new_preset})
        else:
            return jsonify({"error": "保存に失敗しました"}), 500

    except Exception as e:
        logger.error(f"プリセット作成エラー: {e}")
        return jsonify({"error": str(e)}), 500


@report_relater_bp.route("/api/presets/<preset_id>", methods=["GET"])
@login_required
def get_preset(preset_id):
    """特定のプリセットを取得"""
    preset = get_preset_by_id(preset_id)
    if preset:
        return jsonify(preset)
    return jsonify({"error": "プリセットが見つかりません"}), 404


@report_relater_bp.route("/api/presets/<preset_id>", methods=["PUT"])
@login_required
def update_preset(preset_id):
    """プリセットを更新"""
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"error": "データが送信されていません"}), 400

        data = load_presets()
        presets = data.get("presets", [])

        for i, preset in enumerate(presets):
            if preset.get("id") == preset_id:
                presets[i]["name"] = req_data.get("name", preset.get("name"))
                presets[i]["updated_at"] = datetime.now().isoformat()
                presets[i]["updated_by"] = getattr(current_user, 'username', 'unknown')
                if "attendance" in req_data:
                    presets[i]["attendance"] = req_data["attendance"]
                if "daily_report" in req_data:
                    presets[i]["daily_report"] = req_data["daily_report"]

                data["presets"] = presets
                if save_presets(data):
                    return jsonify({"success": True, "preset": presets[i]})
                else:
                    return jsonify({"error": "保存に失敗しました"}), 500

        return jsonify({"error": "プリセットが見つかりません"}), 404

    except Exception as e:
        logger.error(f"プリセット更新エラー: {e}")
        return jsonify({"error": str(e)}), 500


@report_relater_bp.route("/api/presets/<preset_id>", methods=["DELETE"])
@login_required
def delete_preset(preset_id):
    """プリセットを削除"""
    try:
        data = load_presets()
        presets = data.get("presets", [])

        new_presets = [p for p in presets if p.get("id") != preset_id]

        if len(new_presets) == len(presets):
            return jsonify({"error": "プリセットが見つかりません"}), 404

        data["presets"] = new_presets
        if save_presets(data):
            return jsonify({"success": True})
        else:
            return jsonify({"error": "保存に失敗しました"}), 500

    except Exception as e:
        logger.error(f"プリセット削除エラー: {e}")
        return jsonify({"error": str(e)}), 500


@report_relater_bp.route("/api/preview_image", methods=["POST"])
@login_required
def preview_image():
    """PDFの最初のページを画像として返す（プリセット設定用）"""
    try:
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "ファイルがアップロードされていません"}), 400

        temp_dir = tempfile.mkdtemp()
        try:
            file_path = os.path.join(temp_dir, "temp.pdf")
            file.save(file_path)

            doc = fitz.open(file_path)
            page = doc[0]
            zoom = PDF_RENDER_DPI / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data))

            # サムネイル作成
            pil_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            doc.close()

            return jsonify({
                "image": img_base64,
                "width": pil_img.width,
                "height": pil_img.height,
            })

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"プレビュー画像エラー: {e}")
        return jsonify({"error": str(e)}), 500


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
        preset_id = request.form.get("preset_id", "")

        # プリセット取得
        preset = None
        if preset_id:
            preset = get_preset_by_id(preset_id)
            if not preset:
                return jsonify({"error": f"プリセット (ID: {preset_id}) が見つかりません"}), 400

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

        # プリセット設定を渡す
        attendance_result = process_attendance_pdf(att_path, preset)
        report_result = process_daily_report_pdf(rep_path, preset)

        return jsonify({
            "attendance": attendance_result,
            "daily_reports": report_result,
            "preset_used": preset.get("name") if preset else None,
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

def process_attendance_pdf(pdf_path, preset=None):
    """勤怠データPDFを処理（テーブル形式対応）"""
    result = {
        "employee_id": "",
        "employee_name": "",
        "period": "",
        "rows": [],
        "page_images": [],
    }

    # プリセットからテーブル設定とカラム定義を取得
    default_attendance = get_default_fields()["attendance"]
    table_config = default_attendance.get("table_config", {})
    columns = default_attendance.get("columns", [])

    if preset and "attendance" in preset:
        att_preset = preset["attendance"]
        if "table_config" in att_preset:
            table_config = att_preset["table_config"]
        if "columns" in att_preset and att_preset["columns"]:
            columns = att_preset["columns"]

    # テーブル設定パラメータ
    header_region = table_config.get("header_region", [0, 0, 1.0, 0.08])
    data_start_y = table_config.get("data_start_y", 0.12)
    row_height = table_config.get("row_height", 0.022)
    max_rows = table_config.get("max_rows", 31)

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

            h, w = cv_img.shape[:2]

            # OCR領域を収集（可視化用）
            ocr_regions = []

            # 1ページ目でヘッダー情報を取得
            if page_num == 0:
                hx, hy, hw, hh = header_region
                header_h_px = int(hh * h)
                header_y_px = int(hy * h)
                header_x_px = int(hx * w)
                header_w_px = int(hw * w)
                ocr_regions.append({
                    "rect": (header_x_px, header_y_px, header_w_px, header_h_px),
                    "label": "Header",
                    "color": (0, 255, 0)  # 緑
                })

                gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
                _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                header_info = extract_header_info(binary, header_region)
                result["employee_id"] = header_info.get("employee_id", "")
                result["period"] = header_info.get("period", "")

            # テーブル行をOCR
            page_rows = extract_attendance_table_rows(
                cv_img, columns, data_start_y, row_height, max_rows, ocr_regions
            )
            result["rows"].extend(page_rows)

            # OCR領域を描画
            annotated = draw_ocr_regions_attendance(cv_img, ocr_regions)

            # 赤枠付き画像をサムネイルとして保存
            annotated_pil = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
            annotated_pil.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            annotated_pil.save(buf, format="PNG")
            result["page_images"].append(base64.b64encode(buf.getvalue()).decode("utf-8"))

        doc.close()

    except Exception as e:
        logger.error(f"勤怠データOCRエラー: {e}")
        logger.error(traceback.format_exc())

    return result


def extract_attendance_table_rows(cv_img, columns, data_start_y, row_height, max_rows, ocr_regions):
    """テーブル形式の勤怠データから行を抽出"""
    h, w = cv_img.shape[:2]
    rows = []
    empty_row_count = 0

    for row_idx in range(max_rows):
        # 行のY位置を計算
        row_y = data_start_y + (row_idx * row_height)
        if row_y + row_height > 1.0:
            break  # 画像範囲外

        row_data = {
            "date": "",
            "day_of_week": "",
            "actual_start": "",
            "actual_end": "",
            "meter_out": "",
            "meter_in": "",
            "mileage": "",
            "vehicle_code": "",
        }

        has_data = False

        # 各カラムを処理
        for col in columns:
            col_name = col.get("name", "")
            col_x = col.get("x", 0)
            col_width = col.get("width", 0.05)

            # ピクセル座標に変換
            x = int(col_x * w)
            y = int(row_y * h)
            region_w = int(col_width * w)
            region_h = int(row_height * h)

            # 境界チェック
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            region_w = min(region_w, w - x)
            region_h = min(region_h, h - y)

            if region_w <= 5 or region_h <= 5:
                continue

            # 最初の行のみOCR領域を可視化
            if row_idx < 3:
                ocr_regions.append({
                    "rect": (x, y, region_w, region_h),
                    "label": col.get("label", col_name),
                    "color": (255, 0, 0)  # 青
                })

            # 領域を切り出してOCR
            region = cv_img[y:y + region_h, x:x + region_w]
            text = ocr_region_improved(region, col_name)

            if text and col_name in row_data:
                row_data[col_name] = text
                if text.strip():
                    has_data = True

        # 日付データがあれば行として追加
        if row_data["date"] or has_data:
            rows.append(row_data)
            empty_row_count = 0
        else:
            empty_row_count += 1
            # 3行連続で空なら終了
            if empty_row_count >= 3:
                break

    return rows


def extract_attendance_fields_by_region(cv_img, fields):
    """勤怠データのフィールドを領域単位でOCR"""
    h, w = cv_img.shape[:2]
    rows = []

    # フィールドを縦位置でグループ化
    field_by_y = {}
    for field in fields:
        if "region" not in field:
            continue
        _, fy, _, fh = field["region"]
        y_center = fy + fh / 2
        # 同じ行のフィールドをグループ化（Y座標が近いもの）
        found_group = False
        for group_y in field_by_y.keys():
            if abs(group_y - y_center) < 0.02:  # 2%以内なら同じ行
                field_by_y[group_y].append(field)
                found_group = True
                break
        if not found_group:
            field_by_y[y_center] = [field]

    # 各行を処理
    for y_center in sorted(field_by_y.keys()):
        row_fields = field_by_y[y_center]
        row_data = {
            "date": "",
            "day_of_week": "",
            "actual_start": "",
            "actual_end": "",
            "meter_out": "",
            "meter_in": "",
            "mileage": "",
            "vehicle_code": "",
        }

        for field in row_fields:
            field_name = field.get("name", "")
            if field_name not in row_data:
                continue

            fx, fy, fw, fh = field["region"]
            x = int(fx * w)
            y = int(fy * h)
            region_w = int(fw * w)
            region_h = int(fh * h)

            # 境界チェック
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            region_w = min(region_w, w - x)
            region_h = min(region_h, h - y)

            if region_w <= 5 or region_h <= 5:
                continue

            region = cv_img[y:y + region_h, x:x + region_w]

            # 前処理を改善
            text = ocr_region_improved(region, field_name)

            if text:
                row_data[field_name] = text

        # 日付があれば行として追加
        if row_data["date"]:
            rows.append(row_data)

    return rows


def ocr_region_improved(region, field_name):
    """改善されたOCR処理"""
    try:
        # グレースケール変換
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

        # ノイズ除去
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # コントラスト強調 (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        # 適応的二値化
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # 小さい領域は拡大
        h_r, w_r = binary.shape[:2]
        if h_r < 40:
            scale = 40.0 / h_r
            binary = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # パディングを追加
        binary = cv2.copyMakeBorder(binary, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)

        # フィールドタイプに応じたOCR設定
        if field_name in ("actual_start", "actual_end", "start_time", "end_time"):
            # 時刻用
            config = "--psm 7 -c tessedit_char_whitelist=0123456789:"
        elif field_name in ("meter_out", "meter_in", "mileage", "vehicle_code", "employee_id"):
            # 数字のみ
            config = "--psm 7 -c tessedit_char_whitelist=0123456789"
        elif field_name == "date":
            # 日付用
            config = "--psm 7 -c tessedit_char_whitelist=0123456789/"
        elif field_name == "day_of_week":
            # 曜日（日本語）
            config = "--psm 7 -l jpn"
        else:
            config = "--psm 7 -c tessedit_char_whitelist=0123456789/:.-"

        text = pytesseract.image_to_string(binary, config=config).strip()

        # 後処理
        text = post_process_ocr_text(text, field_name)

        return text

    except Exception as e:
        logger.warning(f"OCRエラー ({field_name}): {e}")
        return ""


def post_process_ocr_text(text, field_name):
    """OCR結果の後処理"""
    if not text:
        return ""

    # 基本的なクリーンアップ
    text = text.strip()
    text = re.sub(r"\s+", "", text)  # 空白除去

    if field_name in ("actual_start", "actual_end", "start_time", "end_time"):
        # 時刻形式に整形
        text = re.sub(r"[^\d:]", "", text)
        if ":" not in text and len(text) >= 3:
            text = text[:-2] + ":" + text[-2:]

    elif field_name in ("meter_out", "meter_in"):
        # メーター: 数字のみ
        text = re.sub(r"[^\d]", "", text)

    elif field_name == "mileage":
        # 走行距離: 数字のみ
        text = re.sub(r"[^\d]", "", text)

    elif field_name == "vehicle_code":
        # 車両番号: 数字のみ、6桁
        text = re.sub(r"[^\d]", "", text)

    elif field_name == "employee_id":
        # 社員番号: 数字のみ、7桁
        text = re.sub(r"[^\d]", "", text)

    elif field_name == "date":
        # 日付形式
        text = re.sub(r"[^\d/]", "", text)

    return text


def draw_ocr_regions_attendance(cv_img, regions):
    """勤怠データのOCR対象領域を描画"""
    annotated = cv_img.copy()

    for region in regions:
        x, y, w, h = region["rect"]
        label = region.get("label", "")
        color = region.get("color", (0, 0, 255))  # デフォルト赤
        thickness = 3

        # 矩形を描画
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)

        # ラベルを描画（背景付き）
        if label:
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.8
            (text_w, text_h), _ = cv2.getTextSize(label, font, font_scale, 2)

            # ラベル背景
            cv2.rectangle(annotated, (x, y - text_h - 10), (x + text_w + 4, y), color, -1)
            # ラベルテキスト
            cv2.putText(annotated, label, (x + 2, y - 5), font, font_scale, (255, 255, 255), 2)

    return annotated


def extract_header_info(binary_img, header_region=None):
    """ヘッダーから社員番号・期間を抽出"""
    info = {"employee_id": "", "period": ""}
    try:
        h, w = binary_img.shape[:2]

        # プリセットのheader_regionを使用
        if header_region:
            hx, hy, hw, hh = header_region
            y1 = int(hy * h)
            y2 = int((hy + hh) * h)
            x1 = int(hx * w)
            x2 = int((hx + hw) * w)
            header = binary_img[y1:y2, x1:x2]
        else:
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

def process_daily_report_pdf(pdf_path, preset=None):
    """日報PDFを処理"""
    result = {
        "reports": [],
        "page_images": [],
    }

    # プリセットから日報設定を取得
    daily_report_preset = None
    if preset and "daily_report" in preset:
        daily_report_preset = preset["daily_report"]

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

            # 日報ページを処理（OCR領域情報も取得）
            report_data, ocr_regions = process_daily_report_page(cv_img, page_num, daily_report_preset)
            report_data["page_number"] = page_num + 1
            result["reports"].append(report_data)

            # OCR領域を赤枠で描画
            annotated_img = draw_ocr_regions(cv_img, ocr_regions)

            # 赤枠付き画像をサムネイルとして保存
            annotated_pil = Image.fromarray(cv2.cvtColor(annotated_img, cv2.COLOR_BGR2RGB))
            annotated_pil.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            annotated_pil.save(buf, format="PNG")
            result["page_images"].append(base64.b64encode(buf.getvalue()).decode("utf-8"))

        doc.close()

    except Exception as e:
        logger.error(f"日報OCRエラー: {e}")
        logger.error(traceback.format_exc())

    return result


def draw_ocr_regions(cv_img, regions):
    """OCR対象領域を赤枠で描画"""
    annotated = cv_img.copy()

    for region in regions:
        x, y, w, h = region["rect"]
        label = region.get("label", "")
        color = (0, 0, 255)  # 赤 (BGR)
        thickness = 3

        # 矩形を描画
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)

        # ラベルを描画（背景付き）
        if label:
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.6
            (text_w, text_h), _ = cv2.getTextSize(label, font, font_scale, 1)

            # ラベル背景
            cv2.rectangle(annotated, (x, y - text_h - 10), (x + text_w + 4, y), (0, 0, 255), -1)
            # ラベルテキスト
            cv2.putText(annotated, label, (x + 2, y - 5), font, font_scale, (255, 255, 255), 1)

    return annotated


def process_daily_report_page(cv_img, page_num, preset=None):
    """日報1ページを処理。フィールド値とOCR領域情報を返す"""
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
    ocr_regions = []

    # プリセットからメイン領域とフィールド定義を取得
    main_region = [0.75, 0, 0.25, 1.0]  # デフォルト: 右25%
    preset_fields = None
    if preset:
        if "main_region" in preset:
            main_region = preset["main_region"]
        if "fields" in preset:
            preset_fields = preset["fields"]

    try:
        h, w = cv_img.shape[:2]

        # 縦長なら回転（日報は横向き）
        if h > w:
            cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
            h, w = cv_img.shape[:2]

        # メイン領域を処理（プリセットから）
        mx, my, mw, mh = main_region
        main_x = int(mx * w)
        main_y = int(my * h)
        main_w = int(mw * w)
        main_h = int(mh * h)

        main_region_img = cv_img[main_y:main_y + main_h, main_x:main_x + main_w]

        # メイン領域を赤枠で表示
        ocr_regions.append({
            "rect": (main_x, main_y, main_w, main_h),
            "label": "Main Region"
        })

        # グレースケール・二値化
        gray = cv2.cvtColor(main_region_img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 数字のみOCR
        text = pytesseract.image_to_string(
            binary,
            config="--psm 6 -c tessedit_char_whitelist=0123456789/:.-"
        )

        logger.info(f"日報ページ{page_num + 1} OCR結果: {text[:200]}...")

        # パターンマッチングで各フィールドを抽出
        fields = extract_daily_report_fields(text)

        # 各フィールドを個別領域からも試す（プリセットのフィールドを使用）
        fields, field_regions = extract_fields_by_region(cv_img, fields, preset_fields)
        ocr_regions.extend(field_regions)

    except Exception as e:
        logger.error(f"日報ページ処理エラー (ページ{page_num + 1}): {e}")
        logger.error(traceback.format_exc())

    return fields, ocr_regions


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


def extract_fields_by_region(cv_img, fields, preset_fields=None):
    """定義された領域から各フィールドを抽出。フィールド値とOCR領域情報を返す"""
    h, w = cv_img.shape[:2]
    ocr_regions = []

    # プリセットのフィールドがある場合はそれを使用、なければデフォルト
    if preset_fields:
        field_defs = {f["name"]: f for f in preset_fields}
    else:
        field_defs = DAILY_REPORT_FIELDS

    for field_name, field_def in field_defs.items():
        try:
            # プリセット形式とデフォルト形式の両方に対応
            if "region" in field_def:
                region_data = field_def["region"]
                if isinstance(region_data, (list, tuple)):
                    rx, ry, rw, rh = region_data
                else:
                    continue
            else:
                continue

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

            # OCR領域情報を記録
            ocr_regions.append({
                "rect": (x, y, region_w, region_h),
                "label": field_def.get("label", field_name)
            })

            region = cv_img[y:y + region_h, x:x + region_w]

            # 改善されたOCR処理を使用
            text = ocr_region_improved(region, field_name)

            # 結果があれば更新
            if text and not fields.get(field_name):
                fields[field_name] = text

        except Exception as e:
            logger.warning(f"フィールド {field_name} 抽出エラー: {e}")

    return fields, ocr_regions


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
