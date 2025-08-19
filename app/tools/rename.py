from flask import Blueprint, render_template, request, send_file
import zipfile, os, tempfile
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_login import login_required

rename_bp = Blueprint("rename", __name__, url_prefix="/tools/rename")

@rename_bp.route("/", methods=["GET", "POST"])
@login_required
def rename_tool():
    if request.method == "GET":
        return render_template("rename.html")

    files = request.files.getlist("files")
    mode = request.form.get("mode")
    prefix = request.form.get("prefix", "file_")
    search = request.form.get("search", "")
    replace = request.form.get("replace", "")
    date_prefix = datetime.now().strftime("%Y%m%d")

    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "renamed_files.zip")

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for i, file in enumerate(files, 1):
            filename = secure_filename(file.filename)
            name, ext = os.path.splitext(filename)

            if mode == "sequential":
                new_name = f"{prefix}{i:03d}{ext}"
            elif mode == "date":
                new_name = f"{date_prefix}_{i:03d}{ext}"
            elif mode == "replace":
                new_name = filename.replace(search, replace)
            elif mode == "append-sequential":
                new_name = f"{name}_{i:03d}{ext}"
            elif mode == "append-date":
                new_name = f"{name}_{date_prefix}{ext}"
            else:
                new_name = filename

            temp_file_path = os.path.join(temp_dir, new_name)
            file.save(temp_file_path)
            zipf.write(temp_file_path, arcname=new_name)

    return send_file(zip_path, as_attachment=True, download_name="renamed_files.zip")
