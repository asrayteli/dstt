from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from flask_login import login_required, current_user
import os
import csv
import io
from datetime import datetime
from openpyxl import load_workbook
import traceback

monthly_generator_bp = Blueprint("monthly_generator", __name__, url_prefix="/tools/monthly_generator")

# 経費対照表（システムに組み込み）
EXPENSE_MAPPING = {
    "自動車売上": ["基本売上", "その他売上"],
    "外注費": "材料",
    "消耗品費": "材料",
    "保健衛生費": "材料",
    "燃料費": "材料",
    "タイヤ費": "材料",
    "被服費": "材料",
    "修繕費": "材料",
    "賃借料": "材料",
    "保険料": "材料",
    "家賃地代": "材料",
    "減価償却費": "材料",
    "減価償却費（ﾘｰｽ資産）": "材料",
    "租税公課": "材料",
    "支払手数料": "材料",
    "給料": "労務",
    "賞与": "労務",
    "賞与引当金繰入額": "労務",
    "退職給付費用": "労務",
    "法定福利費": "労務",
    "福利厚生費": "労務",
    "通勤費": "労務",
    "関係会社外注費": "労務",
    "労務費振替": "労務",
    "広告宣伝費": "経費",
    "募集費": "経費",
    "旅費交通費": "経費",
    "新聞図書費": "経費",
    "水道光熱費": "経費",
    "傭車費": "経費",
    "運賃": "経費",
    "通信費": "経費",
    "リース料": "経費",
    "交際接待費": "経費",
    "文化教育費": "経費",
    "事故負担金": "経費",
    "寄付金": "経費",
    "会費": "経費",
    "雑費": "経費",
}

# セル位置マッピング
CELL_MAPPING = {
    "材料": {"役員": "F9", "一般": "F17", "旅客": "F25", "役員合算": "N9", "一般合算": "N17", "旅客合算": "N25"},
    "労務": {"役員": "F10", "一般": "F18", "旅客": "F26", "役員合算": "N10", "一般合算": "N18", "旅客合算": "N26"},
    "経費": {"役員": "F11", "一般": "F19", "旅客": "F27", "役員合算": "N11", "一般合算": "N19", "旅客合算": "N27"},
    "基本売上": {"役員": "F6", "一般": "F14", "旅客": "F22", "役員合算": "N6", "一般合算": "N14", "旅客合算": "N22"},
    "その他売上": {"役員": "F7", "一般": "F15", "旅客": "F23", "役員合算": "N7", "一般合算": "N15", "旅客合算": "N23"},
}

def get_upload_folder():
    """一時アップロードフォルダのパスを取得"""
    folder = os.path.join(current_app.root_path, 'static', 'monthly_generator', 'uploads')
    os.makedirs(folder, exist_ok=True)
    return folder


@monthly_generator_bp.route("/")
@login_required
def index():
    """メインページ"""
    return render_template("monthly_generator.html")


@monthly_generator_bp.route("/api/process", methods=["POST"])
@login_required
def process_files():
    """ファイル処理のメインエンドポイント"""
    try:
        # ファイル取得
        subject_file = request.files.get('subject_file')  # 科目別推移表
        site_file = request.files.get('site_file')  # 現場表
        report_file = request.files.get('report_file')  # 月次報告書

        # パラメータ取得
        target_month = int(request.form.get('target_month'))  # 対象月（1-12）
        sheet_name = request.form.get('sheet_name')  # シート名

        if not all([subject_file, site_file, report_file, target_month, sheet_name]):
            return jsonify({"error": "必要なファイルまたはパラメータが不足しています"}), 400

        # ファイル保存
        upload_folder = get_upload_folder()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        user_id = current_user.username

        subject_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_subject.csv")
        site_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_site.csv")
        report_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_report.xlsx")

        subject_file.save(subject_path)
        site_file.save(site_path)
        report_file.save(report_path)

        # 処理実行
        result = process_monthly_data(subject_path, site_path, report_path, target_month, sheet_name)

        if result.get("error"):
            return jsonify(result), 400

        # 一時ファイル削除
        try:
            os.remove(subject_path)
            os.remove(site_path)
        except:
            pass

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"処理中にエラーが発生しました: {str(e)}"}), 500


def process_monthly_data(subject_path, site_path, report_path, target_month, sheet_name):
    """月次データ処理のメインロジック"""
    errors = []

    # ステップ1: CSVファイル読み込み
    try:
        # 科目別推移表読み込み（UTF-8, Shift-JIS, CP932を試行）
        subject_data = read_csv_with_encoding(subject_path)
        if not subject_data:
            return {"error": "科目別推移表の読み込みに失敗しました"}

        # 現場表読み込み
        site_data = read_csv_with_encoding(site_path)
        if not site_data:
            return {"error": "現場表の読み込みに失敗しました"}

    except Exception as e:
        return {"error": f"CSVファイルの読み込みエラー: {str(e)}"}

    # ステップ2: 現場表の解析
    site_list = []
    for row in site_data:
        if len(row) >= 3:
            site_list.append({
                "法人名称": row[0].strip(),
                "現場名称": row[1].strip(),
                "セグメント": row[2].strip()
            })

    # ステップ3: 科目別推移表から対象現場のデータを抽出
    extracted_data = []
    found_sites = set()

    for row in subject_data:
        if len(row) < 13:
            continue

        corp_name = row[9].strip() if len(row) > 9 else ""
        site_name = row[10].strip() if len(row) > 10 else ""
        subject_name = row[12].strip() if len(row) > 12 else ""

        # 現場表と照合
        matching_site = None
        for site in site_list:
            if site["法人名称"] == corp_name and site["現場名称"] == site_name:
                matching_site = site
                found_sites.add(f"{corp_name}|{site_name}")
                break

        if matching_site:
            # 金額データ抽出（列13以降が各月のデータ）
            amounts = []
            for i in range(13, len(row)):
                try:
                    amount = float(row[i]) if row[i] else 0
                    amounts.append(amount)
                except:
                    amounts.append(0)

            # 科目コード取得（間接原価判定用）
            subject_code = row[11].strip() if len(row) > 11 else ""

            extracted_data.append({
                "法人名称": corp_name,
                "現場名称": site_name,
                "セグメント": matching_site["セグメント"],
                "科目名称": subject_name,
                "科目コード": subject_code,
                "金額データ": amounts
            })

    # ステップ4: エラーチェック - 現場表にあるが科目別推移表に見つからない現場
    for site in site_list:
        site_key = f"{site['法人名称']}|{site['現場名称']}"
        if site_key not in found_sites:
            errors.append(f"現場が科目別推移表に見つかりません: {site['法人名称']} - {site['現場名称']}")

    # ステップ5: 経費ジャンル分けと合算
    aggregated = {
        "役員": {"材料": 0, "労務": 0, "経費": 0, "基本売上": 0, "その他売上": 0, "材料合算": 0, "労務合算": 0, "経費合算": 0, "基本売上合算": 0, "その他売上合算": 0},
        "一般": {"材料": 0, "労務": 0, "経費": 0, "基本売上": 0, "その他売上": 0, "材料合算": 0, "労務合算": 0, "経費合算": 0, "基本売上合算": 0, "その他売上合算": 0},
        "旅客": {"材料": 0, "労務": 0, "経費": 0, "基本売上": 0, "その他売上": 0, "材料合算": 0, "労務合算": 0, "経費合算": 0, "基本売上合算": 0, "その他売上合算": 0},
    }

    unknown_subjects = set()

    for item in extracted_data:
        segment = item["セグメント"]
        subject = item["科目名称"]
        amounts = item["金額データ"]
        subject_code = item["科目コード"]

        if segment not in aggregated:
            continue

        # 当月金額（target_month - 1はインデックスなので）
        month_amount = amounts[target_month - 1] if target_month - 1 < len(amounts) else 0

        # 累計金額（1月からtarget_monthまで）
        cumulative_amount = sum(amounts[:target_month])

        # 間接原価の場合は労務に合算
        if subject_code == "間接原価":
            aggregated[segment]["労務"] += month_amount
            aggregated[segment]["労務合算"] += cumulative_amount
            continue

        # 経費対照表から原価科目を取得
        expense_category = EXPENSE_MAPPING.get(subject)

        if not expense_category:
            unknown_subjects.add(subject)
            continue

        # 自動車売上は特殊（基本売上とその他売上の両方）
        if isinstance(expense_category, list):
            # 基本売上とその他売上に振り分け（ここでは均等に分割）
            for cat in expense_category:
                aggregated[segment][cat] += month_amount / len(expense_category)
                aggregated[segment][f"{cat}合算"] += cumulative_amount / len(expense_category)
        else:
            aggregated[segment][expense_category] += month_amount
            aggregated[segment][f"{expense_category}合算"] += cumulative_amount

    # エラーチェック - 経費対照表に見つからない科目
    for subject in unknown_subjects:
        errors.append(f"科目が経費対照表に見つかりません: {subject}")

    if errors:
        return {"error": "データ検証エラー", "details": errors}

    # ステップ6: Excelファイルへの書き込み
    try:
        wb = load_workbook(report_path)

        if sheet_name not in wb.sheetnames:
            return {"error": f"指定されたシート '{sheet_name}' が見つかりません"}

        ws = wb[sheet_name]

        # セルに値を書き込み
        for expense_type, cells in CELL_MAPPING.items():
            for segment in ["役員", "一般", "旅客"]:
                # 当月データ
                cell_addr = cells[segment]
                value = aggregated[segment][expense_type]
                ws[cell_addr] = value

                # 累計データ
                cell_addr_cumulative = cells[f"{segment}合算"]
                value_cumulative = aggregated[segment][f"{expense_type}合算"]
                ws[cell_addr_cumulative] = value_cumulative

        # 保存
        output_path = report_path.replace('.xlsx', '_output.xlsx')
        wb.save(output_path)
        wb.close()

        return {
            "success": True,
            "output_file": output_path,
            "data": aggregated
        }

    except Exception as e:
        traceback.print_exc()
        return {"error": f"Excelファイルの処理エラー: {str(e)}"}


def read_csv_with_encoding(file_path):
    """複数のエンコーディングでCSVを読み込む"""
    encodings = ['utf-8', 'shift_jis', 'cp932', 'utf-8-sig']

    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                reader = csv.reader(f)
                data = list(reader)
                return data
        except:
            continue

    return None


@monthly_generator_bp.route("/api/download/<filename>")
@login_required
def download_file(filename):
    """処理済みファイルのダウンロード"""
    try:
        upload_folder = get_upload_folder()
        file_path = os.path.join(upload_folder, filename)

        if not os.path.exists(file_path):
            return jsonify({"error": "ファイルが見つかりません"}), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name="月次報告書_出力.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500
