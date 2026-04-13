from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
import csv
import os
import re
import traceback
from datetime import datetime

from app.models import SiteContractMaster
from app.site_contract_master import VALID_SEGMENTS, ensure_contract_master_synced, manager_ids_match


subject_analysis_tool_bp = Blueprint("subject_analysis_tool", __name__, url_prefix="/tools/subject_analysis_tool")


_DECIMAL_INTEGER_PATTERN = re.compile(r"^\d+\.0+$")
_DIGITS_PATTERN = re.compile(r"^\d+$")


def normalize_contract_code(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(",", "").replace(" ", "").replace("\u3000", "")
    if _DECIMAL_INTEGER_PATTERN.match(text):
        text = text.split(".", 1)[0]
    if _DIGITS_PATTERN.match(text) and len(text) < 8:
        text = text.zfill(8)
    return text


def get_upload_folder():
    """一時アップロードフォルダを返す。"""
    folder = os.path.join(current_app.root_path, "static", "subject_analysis_tool", "uploads")
    os.makedirs(folder, exist_ok=True)
    return folder


def load_site_mapping_from_master():
    """現場リストPLUSの最新マスタから 契約コード -> セグメント を返す。"""
    valid_segments = {"役員", "一般", "旅客"}
    rows = (
        SiteContractMaster.query
        .filter(SiteContractMaster.is_active.is_(True))
        .order_by(SiteContractMaster.contract_code.asc())
        .all()
    )

    site_dict = {}
    for row in rows:
        contract_code = normalize_contract_code(row.contract_code)
        segment = str(row.segment or "").strip()
        if not contract_code or segment not in valid_segments:
            continue
        site_dict[contract_code] = segment
    return site_dict


def load_site_mapping_from_master():
    ensure_contract_master_synced()

    rows = (
        SiteContractMaster.query
        .filter(SiteContractMaster.is_active.is_(True))
        .order_by(SiteContractMaster.contract_code.asc())
        .all()
    )

    site_dict = {}
    site_master = {}
    warnings = []
    matched_rows = 0

    for row in rows:
        matched_rows += 1
        contract_code = normalize_contract_code(row.contract_code)
        segment = str(row.segment or "").strip()
        if not contract_code:
            continue

        site_master[contract_code] = {
            "contract_code": contract_code,
            "segment": segment if segment in VALID_SEGMENTS else "",
            "site_manager_id": str(row.site_manager_id or "").strip(),
            "site_manager_name": str(row.site_manager_name or "").strip(),
            "site_name": str(row.site_name or "").strip(),
        }

        if segment not in VALID_SEGMENTS:
            warnings.append(f"segment missing: {contract_code} - {row.site_name}")
            continue
        site_dict[contract_code] = segment
    if matched_rows == 0:
        raise ValueError("現場リストPLUSの契約マスタがまだ作成されていません。現場リストPLUSを開いて同期してください")
    if matched_rows > 0 and not site_dict:
        raise ValueError("現場リストPLUSの現場はありますが、セグメントが未登録です。現場表を取り込んでください")
    return site_dict, site_master, warnings


@subject_analysis_tool_bp.route("/")
@login_required
def index():
    return render_template("subject_analysis_tool.html")


@subject_analysis_tool_bp.route("/api/upload", methods=["POST"])
@login_required
def upload_files():
    try:
        subject_file = request.files.get("subject_file")
        prev_year_subject_file = request.files.get("prev_year_subject_file")
        site_file = request.files.get("site_file")
        site_source = str(request.form.get("site_source", "file") or "file").strip().lower()

        if not subject_file:
            return jsonify({"error": "科目別分析表 CSV を選択してください"}), 400
        if site_source not in {"file", "db"}:
            return jsonify({"error": "site_source は file または db を指定してください"}), 400
        if site_source == "file" and (not site_file or not site_file.filename):
            return jsonify({"error": "現場表読み込みモードでは現行現場表 CSV が必要です"}), 400

        upload_folder = get_upload_folder()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        user_id = current_user.username

        subject_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_subject.csv")
        subject_file.save(subject_path)

        subject_data = read_csv_with_encoding(subject_path)
        if not subject_data:
            return jsonify({"error": "科目別分析表 CSV の読み込みに失敗しました"}), 400

        parsed_data = parse_subject_data(subject_data)

        prev_year_data = None
        if prev_year_subject_file and prev_year_subject_file.filename:
            prev_year_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_prev_subject.csv")
            prev_year_subject_file.save(prev_year_path)
            prev_year_csv = read_csv_with_encoding(prev_year_path)
            if prev_year_csv:
                prev_year_data = parse_subject_data(prev_year_csv)
            try:
                os.remove(prev_year_path)
            except OSError:
                pass

        warnings = []
        if site_source == "db":
            site_data, site_master, warnings = load_site_mapping_from_master()
        else:
            site_data = {}
            site_master = {}
            site_path = os.path.join(upload_folder, f"{user_id}_{timestamp}_site.csv")
            site_file.save(site_path)
            site_csv = read_csv_with_encoding(site_path)
            if site_csv:
                site_data = parse_site_data(site_csv)
            try:
                os.remove(site_path)
            except OSError:
                pass

        try:
            os.remove(subject_path)
        except OSError:
            pass

        return jsonify(
            {
                "success": True,
                "data": {
                    "current_year": parsed_data,
                    "prev_year": prev_year_data,
                    "site_mapping": site_data,
                    "site_master": site_master,
                },
                "metadata": {
                    "current_year_count": len(parsed_data),
                    "prev_year_count": len(prev_year_data) if prev_year_data else 0,
                    "site_count": len(site_data) if site_data else 0,
                    "site_source": site_source,
                    "warnings": warnings,
                },
            }
        )

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"処理中にエラーが発生しました: {exc}"}), 500


@subject_analysis_tool_bp.route("/api/manager_contracts", methods=["GET"])
@login_required
def manager_contracts():
    try:
        manager_id = str(request.args.get("manager_id", "") or "").strip()
        if not manager_id:
            return jsonify({"error": "manager_id を指定してください"}), 400

        ensure_contract_master_synced()
        rows = (
            SiteContractMaster.query
            .filter(SiteContractMaster.is_active.is_(True))
            .order_by(SiteContractMaster.contract_code.asc())
            .all()
        )

        contract_codes = []
        for row in rows:
            if not manager_ids_match(row.site_manager_id, manager_id):
                continue
            contract_code = normalize_contract_code(row.contract_code)
            if not contract_code:
                continue
            contract_codes.append(contract_code)

        return jsonify(
            {
                "manager_id": manager_id,
                "contract_codes": sorted(set(contract_codes)),
                "count": len(set(contract_codes)),
            }
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"処理中にエラーが発生しました: {exc}"}), 500


def read_csv_with_encoding(file_path):
    """複数エンコーディングで CSV を読み込む。"""
    encodings = ["utf-8", "shift_jis", "cp932", "utf-8-sig"]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as file:
                reader = csv.reader(file)
                return list(reader)
        except Exception:
            continue

    return None


def parse_subject_data(csv_data):
    """科目別分析表 CSV を解析して明細配列に変換する。"""
    parsed = []
    indirect_cost_codes = {"間接原価", "関節原価"}
    revenue_names = {"基本請負料", "その他請負料"}

    for index, row in enumerate(csv_data):
        if index == 0:
            continue
        if len(row) < 13:
            continue

        contract_code = normalize_contract_code(row[8] if len(row) > 8 else "")
        corp_name = row[9].strip() if len(row) > 9 else ""
        site_name = row[10].strip() if len(row) > 10 else ""
        subject_code = row[11].strip() if len(row) > 11 else ""
        subject_name = row[12].strip() if len(row) > 12 else ""
        contract_type = row[5].strip() if len(row) > 5 else ""

        normalized_subject_code = subject_code.replace(" ", "").replace("　", "")
        is_indirect_cost = normalized_subject_code in indirect_cost_codes

        if not subject_name and not is_indirect_cost:
            continue
        if not contract_code:
            continue

        display_subject_name = subject_name
        if not display_subject_name and is_indirect_cost:
            display_subject_name = "間接原価"

        if subject_name == "自動車売上":
            if contract_type in revenue_names:
                display_subject_name = contract_type
            elif contract_type:
                display_subject_name = f"自動車売上({contract_type})"

        amounts = []
        for col_index in range(13, len(row)):
            try:
                amounts.append(float(row[col_index]) if row[col_index] else 0)
            except Exception:
                amounts.append(0)

        while len(amounts) < 12:
            amounts.append(0)

        is_revenue = display_subject_name in revenue_names
        if not is_revenue:
            amounts = [-amount for amount in amounts]

        parsed.append(
            {
                "contract_code": contract_code,
                "corp_name": corp_name,
                "site_name": site_name,
                "subject_code": subject_code,
                "subject_name": display_subject_name,
                "original_subject_name": subject_name,
                "contract_type": contract_type,
                "is_revenue": is_revenue,
                "amounts": amounts[:12],
            }
        )

    return parsed


def parse_site_data(csv_data):
    """現場表 CSV を解析して 契約コード -> セグメント に変換する。"""
    site_dict = {}

    for index, row in enumerate(csv_data):
        if index == 0:
            continue
        if len(row) < 2:
            continue

        contract_code = normalize_contract_code(row[0])
        segment = row[1].strip()
        if not contract_code:
            continue

        site_dict[contract_code] = segment

    return site_dict
