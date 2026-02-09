from flask import Blueprint, render_template, request, send_file, jsonify
import zipfile, os, tempfile, re
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_login import login_required

rename_bp = Blueprint("rename", __name__, url_prefix="/tools/rename")


def apply_rename(filename, mode, opts, index):
    """Apply rename rule to a single filename and return the new name."""
    name, ext = os.path.splitext(filename)
    num = opts.get("start_num", 1) + index
    zero_pad = opts.get("zero_pad", 3)
    num_str = str(num).zfill(zero_pad)
    date_str = datetime.now().strftime(opts.get("date_format", "%Y%m%d"))

    if mode == "sequential":
        prefix = opts.get("prefix", "file_")
        return f"{prefix}{num_str}{ext}"

    elif mode == "date":
        return f"{date_str}_{num_str}{ext}"

    elif mode == "replace":
        search = opts.get("search", "")
        replace = opts.get("replace", "")
        if not search:
            return filename
        return filename.replace(search, replace)

    elif mode == "regex":
        pattern = opts.get("regex_pattern", "")
        replacement = opts.get("regex_replace", "")
        if not pattern:
            return filename
        try:
            return re.sub(pattern, replacement, filename)
        except re.error:
            return filename

    elif mode == "append-sequential":
        return f"{name}_{num_str}{ext}"

    elif mode == "append-date":
        return f"{name}_{date_str}{ext}"

    elif mode == "case":
        case_type = opts.get("case_type", "lower")
        if case_type == "lower":
            return name.lower() + ext.lower()
        elif case_type == "upper":
            return name.upper() + ext.upper()
        elif case_type == "title":
            return name.replace("_", " ").replace("-", " ").title().replace(" ", "_") + ext
        elif case_type == "ext_lower":
            return name + ext.lower()
        elif case_type == "ext_upper":
            return name + ext.upper()
        return filename

    elif mode == "insert":
        position = opts.get("insert_pos", 0)
        text = opts.get("insert_text", "")
        if position < 0:
            position = max(0, len(name) + position + 1)
        position = min(position, len(name))
        return name[:position] + text + name[position:] + ext

    elif mode == "delete":
        start = opts.get("delete_start", 0)
        count = opts.get("delete_count", 0)
        start = max(0, min(start, len(name)))
        end = min(start + count, len(name))
        return name[:start] + name[end:] + ext

    elif mode == "custom":
        pattern = opts.get("custom_pattern", "{name}{ext}")
        try:
            result = pattern.replace("{name}", name)
            result = result.replace("{ext}", ext)
            result = result.replace("{num}", num_str)
            result = result.replace("{date}", date_str)
            result = result.replace("{original}", filename)
            result = result.replace("{upper}", name.upper())
            result = result.replace("{lower}", name.lower())
            result = result.replace("{i}", str(index))
            return result
        except Exception:
            return filename

    return filename


@rename_bp.route("/", methods=["GET", "POST"])
@login_required
def rename_tool():
    if request.method == "GET":
        return render_template("rename.html")

    files = request.files.getlist("files")
    mode = request.form.get("mode", "sequential")
    opts = {
        "prefix": request.form.get("prefix", "file_"),
        "search": request.form.get("search", ""),
        "replace": request.form.get("replace", ""),
        "regex_pattern": request.form.get("regex_pattern", ""),
        "regex_replace": request.form.get("regex_replace", ""),
        "case_type": request.form.get("case_type", "lower"),
        "insert_pos": int(request.form.get("insert_pos", 0)),
        "insert_text": request.form.get("insert_text", ""),
        "delete_start": int(request.form.get("delete_start", 0)),
        "delete_count": int(request.form.get("delete_count", 0)),
        "custom_pattern": request.form.get("custom_pattern", "{name}{ext}"),
        "start_num": int(request.form.get("start_num", 1)),
        "zero_pad": int(request.form.get("zero_pad", 3)),
        "date_format": request.form.get("date_format", "%Y%m%d"),
    }

    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "renamed_files.zip")

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for i, file in enumerate(files):
            filename = secure_filename(file.filename)
            new_name = apply_rename(filename, mode, opts, i)
            new_name = secure_filename(new_name) if new_name else filename

            temp_file_path = os.path.join(temp_dir, new_name)
            file.save(temp_file_path)
            zipf.write(temp_file_path, arcname=new_name)

    return send_file(zip_path, as_attachment=True, download_name="renamed_files.zip")


@rename_bp.route("/preview", methods=["POST"])
@login_required
def preview():
    """Return a JSON preview of renames without processing files."""
    data = request.get_json()
    filenames = data.get("filenames", [])
    mode = data.get("mode", "sequential")
    opts = data.get("opts", {})
    opts.setdefault("prefix", "file_")
    opts.setdefault("start_num", 1)
    opts.setdefault("zero_pad", 3)
    opts.setdefault("date_format", "%Y%m%d")

    results = []
    for i, fname in enumerate(filenames):
        safe = secure_filename(fname)
        new_name = apply_rename(safe, mode, opts, i)
        new_name = secure_filename(new_name) if new_name else safe
        results.append({"original": fname, "safe": safe, "renamed": new_name})

    return jsonify(results)
