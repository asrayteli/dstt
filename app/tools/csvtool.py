from flask import Blueprint, render_template, request, session, send_file, redirect, url_for
import csv, os, tempfile
import chardet
from io import BytesIO, StringIO
from flask_login import login_required

csvtool_bp = Blueprint("csvtool", __name__, url_prefix="/tools/csvtool")

@csvtool_bp.route("/", methods=["GET", "POST"])
@login_required
def csvtool():
    if request.method == "GET":
        return render_template("csvtool.html")

    file = request.files["csvfile"]
    file_bytes = file.read()
    detected_encoding = chardet.detect(file_bytes)
    encoding = detected_encoding['encoding']

    try:
        decoded = file_bytes.decode(encoding)
    except UnicodeDecodeError:
        return "エンコーディングの読み込みに失敗しました。別のファイルを試してください。"

    reader = csv.reader(StringIO(decoded))
    rows = list(reader)
    headers = rows[0]
    data = rows[1:]

    temp_dir = tempfile.mkdtemp()
    raw_path = os.path.join(temp_dir, "original.csv")
    with open(raw_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data)

    session["csvtool_raw"] = raw_path

    return render_template("csvtool.html", headers=headers, preview=data[:20])

@csvtool_bp.route("/process", methods=["POST"])
@login_required
def csvtool_process():
    raw_path = session.get("csvtool_raw")
    if not raw_path or not os.path.exists(raw_path):
        return redirect(url_for("csvtool.csvtool"))

    trim = bool(request.form.get("trim"))
    check_missing = bool(request.form.get("check_missing"))
    check_duplicate = bool(request.form.get("check_duplicate"))
    filter_str = request.form.get("filter", "").strip()
    selected_columns = request.form.getlist("selected_columns")
    headers_order = request.form.get("headers_order")

    with open(raw_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    headers = rows[0]
    data = rows[1:]
    issues = []

    if trim:
        data = [[cell.strip() for cell in row] for row in data]

    if check_missing:
        for i, row in enumerate(data, 2):
            if any(cell.strip() == "" for cell in row):
                issues.append(f"{i}行目に空欄があります")

    if check_duplicate:
        seen = set()
        for i, row in enumerate(data, 2):
            row_tuple = tuple(row)
            if row_tuple in seen:
                issues.append(f"{i}行目が重複しています")
            else:
                seen.add(row_tuple)

    if headers_order:
        import json
        try:
            order = json.loads(headers_order)
            header_index = {h: i for i, h in enumerate(headers)}
            headers = [h for h in order if h in header_index]
            data = [[row[header_index[h]] for h in headers] for row in data]
        except Exception as e:
            issues.append(f"並び替え失敗: {str(e)}")

    if selected_columns:
        header_index = {h: i for i, h in enumerate(headers)}
        headers = [h for h in headers if h in selected_columns]
        data = [[row[header_index[h]] for h in headers] for row in data]

    if filter_str:
        data = [row for row in data if any(filter_str in cell for cell in row)]

    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, "cleaned.csv")
    session["csvtool_download"] = output_path
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data)

    return render_template("csvtool.html", headers=headers, preview=data[:20], issues=issues)

@csvtool_bp.route("/download")
@login_required
def download():
    path = session.get("csvtool_download")
    if not path or not os.path.exists(path):
        return redirect(url_for("csvtool.csvtool"))

    with open(path, "r", encoding="utf-8") as infile:
        content = infile.read()

    temp_path = os.path.join(tempfile.mkdtemp(), "cleaned_with_bom.csv")
    with open(temp_path, "w", newline="", encoding="utf-8-sig") as outfile:
        outfile.write(content)

    return send_file(temp_path, as_attachment=True, download_name="cleaned.csv")
