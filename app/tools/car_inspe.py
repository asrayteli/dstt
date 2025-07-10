from flask import Blueprint, render_template, request, send_file
from werkzeug.utils import secure_filename
from flask_login import login_required
import os, tempfile, csv, re
import pytesseract
from PIL import Image
import fitz  # PyMuPDF
from datetime import datetime

car_inspe_bp = Blueprint("car_inspe", __name__, url_prefix="/tools/car_inspe")

@car_inspe_bp.route("/", methods=["GET", "POST"])
@login_required
def car_inspection():
    error = None
    renamed_path = None

    if request.method == "POST":
        pdf_file = request.files.get("pdf")
        csv_file = request.files.get("csv")

        if not pdf_file or not csv_file:
            error = "PDFとCSVの両方をアップロードしてください。"
            return render_template("car_inspe.html", error=error)

        temp_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(temp_dir, secure_filename(pdf_file.filename))
        csv_path = os.path.join(temp_dir, secure_filename(csv_file.filename))
        pdf_file.save(pdf_path)
        csv_file.save(csv_path)

        # PDFを画像に変換（1ページのみ）
        doc = fitz.open(pdf_path)
        if len(doc) != 1:
            error = "1ページのPDFのみ対応しています。複数ページのPDFは使用できません。"
            return render_template("car_inspe.html", error=error)

        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=300)
        image_path = os.path.join(temp_dir, "page.png")
        pix.save(image_path)

        # OCRで画像からテキスト抽出
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='jpn')

        # 登録番号の抽出（例：千葉200い123）
        reg_match = re.search(r'([\u4e00-\u9fa5]{1,2}\s*\d{3,4}\s*[\u3040-\u309F]\s*\d{1,4})', text)
        # 車検満了日の抽出（例：2025年07月01日 or 2025/07/01）
        date_match = re.search(r'(20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2})', text)

        if not reg_match or not date_match:
            error = "OCRで登録番号または車検満了日が検出できませんでした。画像の品質を確認してください。"
            return render_template("car_inspe.html", error=error)

        reg_number = reg_match.group(1).replace(" ", "")
        raw_date = date_match.group(1)

        # 満了日を整形（例：20250701）
        try:
            parsed_date = datetime.strptime(re.sub(r'[年月日/.-]', '-', raw_date), "%Y-%m-%d")
            formatted_date = parsed_date.strftime("%Y%m%d")
        except:
            error = "車検満了日の形式が認識できません。"
            return render_template("car_inspe.html", error=error)

        # 登録番号末尾4桁（数字のみ）を抽出・ゼロ埋め
        digits = re.findall(r'\d', reg_number)
        suffix = ("0" * 4 + "".join(digits))[-4:]

        # CSVから保管場所を探す
        location = None
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2 and row[1].zfill(4) == suffix:
                    location = row[0]
                    break

        if not location:
            error = f"CSVに登録番号末尾 {suffix} が見つかりません。"
            return render_template("car_inspe.html", error=error)

        # ファイル名変更して保存
        new_filename = f"{formatted_date}_{location}_{reg_number}.pdf"
        renamed_path = os.path.join(temp_dir, new_filename)
        os.rename(pdf_path, renamed_path)

        return send_file(renamed_path, as_attachment=True, download_name=new_filename)

    return render_template("car_inspe.html", error=error)
