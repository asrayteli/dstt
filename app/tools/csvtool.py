from flask import Blueprint, render_template, request, session, send_file, jsonify
import csv, os, tempfile
import chardet
from io import StringIO
from flask_login import login_required

csvtool_bp = Blueprint("csvtool", __name__, url_prefix="/tools/csvtool")


@csvtool_bp.route("/")
@login_required
def csvtool():
    return render_template("csvtool.html")


@csvtool_bp.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"error": "ファイルが空です"}), 400

    detected = chardet.detect(file_bytes)
    encoding = detected.get("encoding") or "utf-8"
    confidence = detected.get("confidence", 0)

    decoded = None
    used_encoding = encoding

    encodings_to_try = [encoding, "utf-8", "utf-8-sig", "shift_jis", "cp932",
                        "euc-jp", "iso-2022-jp", "latin-1"]
    seen = set()
    for enc in encodings_to_try:
        if enc and enc.lower() not in seen:
            seen.add(enc.lower())
            try:
                decoded = file_bytes.decode(enc)
                used_encoding = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

    if decoded is None:
        return jsonify({"error": "エンコーディングの検出に失敗しました"}), 400

    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]

    try:
        dialect = csv.Sniffer().sniff(decoded[:8192])
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(StringIO(decoded), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        return jsonify({"error": "CSVにデータがありません"}), 400

    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return jsonify({"error": "CSVにデータがありません"}), 400

    headers = rows[0]
    data = rows[1:]
    col_count = len(headers)

    normalized = []
    for row in data:
        if len(row) < col_count:
            row = row + [""] * (col_count - len(row))
        elif len(row) > col_count:
            row = row[:col_count]
        normalized.append(row)

    return jsonify({
        "headers": headers,
        "data": normalized,
        "encoding": used_encoding,
        "confidence": round(confidence * 100, 1),
        "delimiter": delimiter,
        "totalRows": len(normalized),
        "totalColumns": len(headers),
        "filename": file.filename,
    })


@csvtool_bp.route("/api/download", methods=["POST"])
@login_required
def api_download():
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "データがありません"}), 400

    headers = payload.get("headers", [])
    data = payload.get("data", [])
    encoding = payload.get("encoding", "utf-8-sig")
    delimiter = payload.get("delimiter", ",")
    filename = payload.get("filename", "power_csv_export.csv")

    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, "export.csv")

    with open(output_path, "w", newline="", encoding=encoding) as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(data)

    return send_file(output_path, as_attachment=True, download_name=filename)


@csvtool_bp.route("/api/validate", methods=["POST"])
@login_required
def api_validate():
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "データがありません"}), 400

    headers = payload.get("headers", [])
    data = payload.get("data", [])
    issues = []

    col_stats = {}
    for col_idx, header in enumerate(headers):
        col_values = [row[col_idx] if col_idx < len(row) else "" for row in data]
        non_empty = [v for v in col_values if v.strip()]
        unique_values = set(non_empty)
        empty_count = len(col_values) - len(non_empty)
        col_stats[header] = {
            "index": col_idx,
            "total": len(col_values),
            "empty": empty_count,
            "unique": len(unique_values),
            "fillRate": round((len(non_empty) / max(len(col_values), 1)) * 100, 1),
        }

    empty_cells = 0
    for i, row in enumerate(data):
        for j, cell in enumerate(row):
            if j < len(headers) and cell.strip() == "":
                issues.append({
                    "type": "missing",
                    "severity": "warning",
                    "row": i,
                    "col": j,
                    "message": f"{i + 2}行目「{headers[j]}」が空欄",
                })
                empty_cells += 1

    seen = {}
    duplicate_count = 0
    for i, row in enumerate(data):
        key = tuple(row)
        if key in seen:
            issues.append({
                "type": "duplicate",
                "severity": "error",
                "row": i,
                "col": -1,
                "originalRow": seen[key],
                "message": f"{i + 2}行目 → {seen[key] + 2}行目と重複",
            })
            duplicate_count += 1
        else:
            seen[key] = i

    return jsonify({
        "issues": issues,
        "summary": {
            "totalRows": len(data),
            "totalColumns": len(headers),
            "emptyCells": empty_cells,
            "duplicateRows": duplicate_count,
            "issueCount": len(issues),
        },
        "columnStats": col_stats,
    })
