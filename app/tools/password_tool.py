from flask import Blueprint, render_template, request, session, send_file
import secrets, string, csv, tempfile
from flask_login import login_required

password_tool_bp = Blueprint("password_tool", __name__, url_prefix="/tools/password_tool")

def generate_password(length, upper=True, numbers=True, symbols=False):
    chars = string.ascii_lowercase
    if upper:
        chars += string.ascii_uppercase
    if numbers:
        chars += string.digits
    if symbols:
        chars += "!@#$%&*()-_=+[]{};:,.<>?/~"

    return ''.join(secrets.choice(chars) for _ in range(length))

@password_tool_bp.route("/", methods=["GET", "POST"])
@login_required
def password_home():
    if "passwords" not in session:
        session["passwords"] = []

    if request.method == "POST":
        label = request.form.get("label") or "(無名)"
        length = int(request.form.get("length", 12))
        upper = bool(request.form.get("include_upper"))
        numbers = bool(request.form.get("include_numbers"))
        symbols = bool(request.form.get("include_symbols"))

        pwd = generate_password(length, upper, numbers, symbols)

        session["passwords"].append({"label": label, "password": pwd})
        session.modified = True

    return render_template("password_tool.html", passwords=session.get("passwords", []))

@password_tool_bp.route("/download")
@login_required
def download():
    passwords = session.get("passwords", [])

    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", newline="", encoding="utf-8")
    tmpfile.write("\ufeff")  # ← UTF-8 BOMを追加
    writer = csv.writer(tmpfile)
    writer.writerow(["サービス名", "パスワード"])
    for item in passwords:
        writer.writerow([item["label"], item["password"]])
    tmpfile.close()

    return send_file(tmpfile.name, as_attachment=True, download_name="passwords.csv")

@password_tool_bp.route("/reset", methods=["POST"])
@login_required
def reset():
    session["passwords"] = []  # パスワードリストのみリセット
    session.modified = True
    return render_template("password_tool.html", passwords=[])