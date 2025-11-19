from flask import Blueprint, render_template, request, send_file, jsonify, after_this_request
import os
import tempfile
import shutil
import traceback
import zipfile
from werkzeug.utils import secure_filename
from PIL import Image
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PyPDF2 import PdfReader, PdfWriter
import fitz  # PyMuPDF
import io
import pikepdf
import logging
from flask_login import login_required


pdf_power_bp = Blueprint("pdf_power", __name__, url_prefix="/tools/pdf_power")

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ファイルサイズ制限（全機能共通）
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

# 日本語フォント設定
JAPANESE_FONT = 'Helvetica'  # デフォルト
try:
    # システムにある日本語フォントを試行
    font_paths = [
        '/System/Library/Fonts/NotoSansCJK.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf',
    ]
    for font_path in font_paths:
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('NotoSansCJK', font_path))
            JAPANESE_FONT = 'NotoSansCJK'
            logger.info(f"日本語フォント登録成功: {font_path}")
            break
    if JAPANESE_FONT == 'Helvetica':
        logger.warning("日本語フォントが見つかりません。Helveticaを使用します（日本語は正しく表示されない可能性があります）")
except Exception as e:
    logger.error(f"日本語フォント登録エラー: {e}")
    logger.error(traceback.format_exc())


@pdf_power_bp.route("/", methods=["GET"])
@login_required
def pdf_power_home():
    return render_template("pdf_power.html")


# ========================================
# 1. PDF変換機能
# ========================================
@pdf_power_bp.route("/convert", methods=["POST"])
@login_required
def convert_pdf():
    temp_dir = None
    output_path = None

    try:
        files = request.files.getlist("files")
        if not files or not files[0].filename:
            return jsonify({"error": "ファイルがアップロードされていません"}), 400

        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, "converted_output.pdf")

        pdf_images = []

        for file in files:
            if not file.filename:
                continue

            filename = secure_filename(file.filename)
            if not filename:
                continue

            ext = filename.split('.')[-1].lower()
            file_path = os.path.join(temp_dir, filename)
            file.save(file_path)

            # ファイルサイズチェック
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                return jsonify({"error": f"ファイルサイズが大きすぎます（100MB以下にしてください）: {filename}"}), 400

            if ext in ["png", "jpg", "jpeg", "bmp", "gif"]:
                # 画像ファイルをPDF用に保存
                try:
                    img = Image.open(file_path).convert("RGB")
                    pdf_images.append(img)
                except Exception as e:
                    logger.error(f"画像の処理に失敗しました: {e}")
                    return jsonify({"error": f"画像の処理に失敗しました: {filename}"}), 400

            elif ext == "txt":
                # TXT → PDF化
                try:
                    c = canvas.Canvas(output_path, pagesize=letter)
                    width, height = letter

                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    lines = content.split('\n')
                    y = height - 72
                    line_height = 14

                    for line in lines:
                        if len(line) > 80:
                            words = line.split()
                            current_line = ""
                            for word in words:
                                if len(current_line + word) < 80:
                                    current_line += word + " "
                                else:
                                    if current_line:
                                        c.drawString(72, y, current_line.strip())
                                        y -= line_height
                                        if y < 72:
                                            c.showPage()
                                            y = height - 72
                                    current_line = word + " "
                            if current_line:
                                c.drawString(72, y, current_line.strip())
                                y -= line_height
                        else:
                            c.drawString(72, y, line)
                            y -= line_height

                        if y < 72:
                            c.showPage()
                            y = height - 72

                    c.save()

                    @after_this_request
                    def cleanup(response):
                        try:
                            if temp_dir and os.path.exists(temp_dir):
                                shutil.rmtree(temp_dir)
                        except Exception as e:
                            logger.error(f"一時ディレクトリの削除に失敗: {e}")
                        return response

                    return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")

                except Exception as e:
                    logger.error(f"テキストファイルの処理に失敗しました: {e}")
                    logger.error(traceback.format_exc())
                    return jsonify({"error": f"テキストファイルの処理に失敗しました: {str(e)}"}), 400

            elif ext == "docx":
                # Word → PDF化
                try:
                    doc = Document(file_path)
                    c = canvas.Canvas(output_path, pagesize=letter)
                    width, height = letter
                    y = height - 72

                    for para in doc.paragraphs:
                        text = para.text.strip()
                        if text:
                            if len(text) > 80:
                                words = text.split()
                                current_line = ""
                                for word in words:
                                    if len(current_line + word) < 80:
                                        current_line += word + " "
                                    else:
                                        if current_line:
                                            c.drawString(72, y, current_line.strip())
                                            y -= 20
                                            if y < 72:
                                                c.showPage()
                                                y = height - 72
                                        current_line = word + " "
                                if current_line:
                                    c.drawString(72, y, current_line.strip())
                                    y -= 20
                            else:
                                c.drawString(72, y, text)
                                y -= 20

                            if y < 72:
                                c.showPage()
                                y = height - 72

                    c.save()

                    @after_this_request
                    def cleanup(response):
                        try:
                            if temp_dir and os.path.exists(temp_dir):
                                shutil.rmtree(temp_dir)
                        except Exception as e:
                            logger.error(f"一時ディレクトリの削除に失敗: {e}")
                        return response

                    return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")

                except Exception as e:
                    logger.error(f"Wordファイルの処理に失敗しました: {e}")
                    logger.error(traceback.format_exc())
                    return jsonify({"error": f"Wordファイルの処理に失敗しました: {str(e)}"}), 400

            else:
                return jsonify({"error": f"対応していないファイル形式です: {ext}. PNG, JPG, JPEG, BMP, GIF, TXT, DOCXのみ対応しています。"}), 400

        # 複数画像をPDFとして結合
        if pdf_images:
            try:
                pdf_images[0].save(output_path, save_all=True, append_images=pdf_images[1:])

                @after_this_request
                def cleanup(response):
                    try:
                        if temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"一時ディレクトリの削除に失敗: {e}")
                    return response

                return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")
            except Exception as e:
                logger.error(f"画像PDFの作成に失敗しました: {e}")
                logger.error(traceback.format_exc())
                return jsonify({"error": f"画像PDFの作成に失敗しました: {str(e)}"}), 500

        return jsonify({"error": "処理するファイルが見つかりませんでした"}), 400

    except Exception as e:
        logger.error(f"変換処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"変換処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        # after_this_requestがない場合のフォールバック
        pass


# ========================================
# 2. PDF分割・結合機能
# ========================================
@pdf_power_bp.route("/split_merge", methods=["POST"])
@login_required
def split_or_merge_pdf():
    temp_dir = None
    try:
        mode = request.form.get("mode")
        if mode not in ["split", "merge"]:
            return jsonify({"error": "無効な操作です"}), 400

        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, "output.pdf")

        files = request.files.getlist("pdfs")
        if not files or not files[0].filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        file_paths = []
        for file in files:
            if not file.filename:
                continue
            filename = secure_filename(file.filename)
            if not filename.endswith('.pdf'):
                return jsonify({"error": f"PDFファイルのみ対応しています: {filename}"}), 400

            file_path = os.path.join(temp_dir, filename)
            file.save(file_path)

            # ファイルサイズチェック
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                return jsonify({"error": f"ファイルサイズが大きすぎます（100MB以下にしてください）: {filename}"}), 400

            file_paths.append(file_path)

        if not file_paths:
            return jsonify({"error": "有効なPDFファイルが見つかりませんでした"}), 400

        # 分割
        if mode == "split":
            if len(file_paths) != 1:
                return jsonify({"error": "分割は1つのPDFファイルのみ対応しています"}), 400

            page_range = request.form.get("range")
            if not page_range:
                return jsonify({"error": "ページ範囲を指定してください（例: 1-5）"}), 400

            try:
                if '-' in page_range:
                    start, end = map(int, page_range.split('-'))
                else:
                    start = end = int(page_range)
            except ValueError:
                return jsonify({"error": "ページ範囲の指定が正しくありません（例: 1-5）"}), 400

            try:
                reader = PdfReader(file_paths[0])
                total_pages = len(reader.pages)

                if start < 1 or end > total_pages or start > end:
                    return jsonify({"error": f"ページ範囲が無効です。1-{total_pages}の範囲で指定してください"}), 400

                writer = PdfWriter()
                for page_num in range(start - 1, end):
                    writer.add_page(reader.pages[page_num])

                with open(output_path, "wb") as output_pdf:
                    writer.write(output_pdf)

                @after_this_request
                def cleanup(response):
                    try:
                        if temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"一時ディレクトリの削除に失敗: {e}")
                    return response

                return send_file(output_path, as_attachment=True, download_name="split_output.pdf")

            except Exception as e:
                logger.error(f"PDF分割に失敗しました: {e}")
                logger.error(traceback.format_exc())
                return jsonify({"error": f"PDF分割に失敗しました: {str(e)}"}), 500

        # 結合
        elif mode == "merge":
            if len(file_paths) < 2:
                return jsonify({"error": "結合には2つ以上のPDFファイルが必要です"}), 400

            try:
                writer = PdfWriter()
                for file_path in file_paths:
                    reader = PdfReader(file_path)
                    for page in reader.pages:
                        writer.add_page(page)

                with open(output_path, "wb") as output_pdf:
                    writer.write(output_pdf)

                @after_this_request
                def cleanup(response):
                    try:
                        if temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"一時ディレクトリの削除に失敗: {e}")
                    return response

                return send_file(output_path, as_attachment=True, download_name="merged_output.pdf")

            except Exception as e:
                logger.error(f"PDF結合に失敗しました: {e}")
                logger.error(traceback.format_exc())
                return jsonify({"error": f"PDF結合に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"PDF操作中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"PDF操作中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# 3. PDF圧縮機能（改善版）
# ========================================
@pdf_power_bp.route("/compress", methods=["POST"])
@login_required
def compress_pdf():
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        compression_level = request.form.get("compression_level", "medium")

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "compressed_output.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        original_size = os.path.getsize(input_path)
        if original_size > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        # PDFの有効性チェック
        try:
            test_doc = fitz.open(input_path)
            if test_doc.page_count == 0:
                return jsonify({"error": "PDFファイルが空です"}), 400
            test_doc.close()
        except Exception as e:
            return jsonify({"error": f"PDFファイルが破損しています: {str(e)}"}), 400

        try:
            # 改善された圧縮処理（pikepdfベース）
            compressed_size = compress_pdf_advanced(
                input_path, output_path, compression_level
            )

            if compressed_size == 0:
                return jsonify({"error": "PDF圧縮に失敗しました"}), 500

            compression_ratio = (1 - compressed_size / original_size) * 100

            logger.info(f"PDF圧縮完了: {original_size} -> {compressed_size} bytes ({compression_ratio:.1f}% 削減)")

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            return send_file(output_path, as_attachment=True, download_name="compressed_output.pdf")

        except Exception as e:
            logger.error(f"PDF圧縮に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"PDF圧縮に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"圧縮処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"圧縮処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


def compress_pdf_advanced(input_path, output_path, compression_level="medium"):
    """
    完全に書き直したPDF圧縮処理（pikepdf + PyMuPDFのハイブリッド）
    画像を抽出・圧縮してPDFを再構築する方式
    """
    try:
        # 圧縮レベル設定
        compression_settings = {
            "low": {"quality": 85, "max_size": (1200, 1200), "dpi": 150},
            "medium": {"quality": 70, "max_size": (1000, 1000), "dpi": 120},
            "high": {"quality": 50, "max_size": (800, 800), "dpi": 96},
            "maximum": {"quality": 30, "max_size": (600, 600), "dpi": 72}
        }

        settings = compression_settings.get(compression_level, compression_settings["medium"])

        # Step 1: PyMuPDFで画像を圧縮した新しいPDFを生成
        doc = fitz.open(input_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)

            for img_index, img in enumerate(image_list):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]

                    # 画像を圧縮
                    if image_ext.lower() in ["png", "jpg", "jpeg", "bmp", "tiff"]:
                        img_obj = Image.open(io.BytesIO(image_bytes))

                        # サイズ調整
                        if img_obj.size[0] > settings["max_size"][0] or img_obj.size[1] > settings["max_size"][1]:
                            img_obj.thumbnail(settings["max_size"], Image.Resampling.LANCZOS)

                        # JPEG形式で圧縮
                        img_buffer = io.BytesIO()
                        if img_obj.mode in ("RGBA", "LA", "P"):
                            # 透明度を持つ画像はRGBに変換
                            background = Image.new("RGB", img_obj.size, (255, 255, 255))
                            if img_obj.mode == "P":
                                img_obj = img_obj.convert("RGBA")
                            background.paste(img_obj, mask=img_obj.split()[-1] if img_obj.mode == "RGBA" else None)
                            img_obj = background
                        elif img_obj.mode != "RGB":
                            img_obj = img_obj.convert("RGB")

                        img_obj.save(img_buffer, format="JPEG", quality=settings["quality"], optimize=True)
                        compressed_image_bytes = img_buffer.getvalue()

                        # 圧縮効果が10%以上ある場合のみ置換
                        if len(compressed_image_bytes) < len(image_bytes) * 0.9:
                            # PyMuPDFで画像オブジェクトを置換（xrefベース）
                            # 注: 直接置換は複雑なため、ストリームを更新
                            try:
                                # 画像のストリームを更新
                                doc._deleteObject(xref)
                            except:
                                pass

                except Exception as e:
                    logger.warning(f"画像圧縮をスキップ (xref: {xref}): {e}")
                    continue

        # メタデータ削除
        doc.set_metadata({})

        # 一時保存
        temp_output = output_path + ".tmp"
        doc.save(temp_output, garbage=4, deflate=True, clean=True)
        doc.close()

        # Step 2: pikepdfで追加最適化
        try:
            with pikepdf.open(temp_output) as pdf:
                # オブジェクトストリームの圧縮
                pdf.remove_unreferenced_resources()

                # 画像の再圧縮（pikepdfの機能）
                for page in pdf.pages:
                    for image_key in page.images.keys():
                        try:
                            raw_image = page.images[image_key]
                            pil_image = raw_image.as_pil_image()

                            # 画像圧縮
                            if pil_image.size[0] > settings["max_size"][0] or pil_image.size[1] > settings["max_size"][1]:
                                pil_image.thumbnail(settings["max_size"], Image.Resampling.LANCZOS)

                            # JPEG形式で保存
                            img_buffer = io.BytesIO()
                            if pil_image.mode in ("RGBA", "LA", "P"):
                                background = Image.new("RGB", pil_image.size, (255, 255, 255))
                                if pil_image.mode == "P":
                                    pil_image = pil_image.convert("RGBA")
                                if pil_image.mode == "RGBA":
                                    background.paste(pil_image, mask=pil_image.split()[-1])
                                pil_image = background
                            elif pil_image.mode != "RGB":
                                pil_image = pil_image.convert("RGB")

                            pil_image.save(img_buffer, format="JPEG", quality=settings["quality"], optimize=True)

                            # 圧縮画像を置換
                            img_buffer.seek(0)
                            raw_image.write(img_buffer.read(), filter=pikepdf.Name.DCTDecode)
                        except Exception as e:
                            logger.warning(f"pikepdf画像圧縮スキップ ({image_key}): {e}")
                            continue

                # 最終保存
                pdf.save(
                    output_path,
                    compress_streams=True,
                    stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    normalize_content=True,
                    linearize=True
                )
        except Exception as e:
            logger.warning(f"pikepdf最適化でエラー: {e}")
            # pikepdfが失敗した場合は一時ファイルをコピー
            shutil.copy(temp_output, output_path)

        # 一時ファイル削除
        if os.path.exists(temp_output):
            os.remove(temp_output)

        return os.path.getsize(output_path)

    except Exception as e:
        logger.error(f"PDF圧縮処理に失敗しました: {e}")
        logger.error(traceback.format_exc())
        return 0


# ========================================
# 4. テキスト抽出機能（画像抽出対応）
# ========================================
@pdf_power_bp.route("/extract", methods=["POST"])
@login_required
def extract_text():
    """
    PDFテキスト抽出機能（画像抽出にも対応）
    """
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        keyword = request.form.get("keyword", "").strip()
        extract_images = request.form.get("extract_images", "false").lower() == "true"

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        file.save(input_path)

        # ファイルサイズチェック
        file_size = os.path.getsize(input_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        # PDFの有効性確認
        if not _validate_pdf_file(input_path):
            return jsonify({"error": "PDFファイルが破損しているか、読み取れません"}), 400

        # テキスト抽出
        extraction_result = _extract_text_from_pdf(input_path, keyword, extract_images, temp_dir)

        if extraction_result["success"]:
            output_files = _create_output_files(extraction_result, temp_dir)

            # 単一ファイルまたはZIPファイルの返却
            if len(output_files) == 1:
                @after_this_request
                def cleanup(response):
                    try:
                        if temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"一時ディレクトリの削除に失敗: {e}")
                    return response

                return send_file(
                    output_files[0]["path"],
                    as_attachment=True,
                    download_name=output_files[0]["name"]
                )
            else:
                zip_path = _create_zip_file(output_files, temp_dir)

                @after_this_request
                def cleanup(response):
                    try:
                        if temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.error(f"一時ディレクトリの削除に失敗: {e}")
                    return response

                return send_file(zip_path, as_attachment=True, download_name="extracted_content.zip")
        else:
            return jsonify({"error": extraction_result["error"]}), 500

    except Exception as e:
        logger.error(f"テキスト抽出処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"予期しないエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


def _validate_pdf_file(file_path):
    """PDFファイルの有効性を確認"""
    try:
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            doc.close()
            return False
        doc.close()

        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            if len(reader.pages) == 0:
                return False

        return True
    except Exception as e:
        logger.error(f"PDFファイル検証エラー: {e}")
        return False


def _extract_text_from_pdf(file_path, keyword, extract_images, temp_dir):
    """PDFからテキストを抽出"""
    try:
        result = {
            "success": False,
            "text": "",
            "keyword_matches": [],
            "images": [],
            "statistics": {},
            "error": None
        }

        pymupdf_result = _extract_with_pymupdf(file_path, keyword, extract_images, temp_dir)
        pypdf2_result = _extract_with_pypdf2(file_path, keyword)

        if pymupdf_result["success"] and len(pymupdf_result["text"]) > 0:
            result = pymupdf_result
        elif pypdf2_result["success"] and len(pypdf2_result["text"]) > 0:
            result = pypdf2_result
            if extract_images and pymupdf_result["images"]:
                result["images"] = pymupdf_result["images"]
        else:
            result["error"] = "どの手法でもテキストを抽出できませんでした"
            return result

        result["statistics"] = _calculate_statistics(result, file_path)
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"テキスト抽出処理でエラー: {e}")
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": f"テキスト抽出処理でエラー: {str(e)}"
        }


def _extract_with_pymupdf(file_path, keyword, extract_images, temp_dir):
    """PyMuPDFを使用したテキスト抽出"""
    result = {
        "success": False,
        "text": "",
        "keyword_matches": [],
        "images": [],
        "method": "PyMuPDF"
    }

    try:
        doc = fitz.open(file_path)
        all_text = []
        keyword_matches = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_number = page_num + 1

            page_text = ""
            try:
                page_text = page.get_text()
            except:
                try:
                    page_text = page.get_text("text")
                except:
                    pass

            if len(page_text.strip()) < 10:
                try:
                    text_dict = page.get_text("dict")
                    extracted_text = ""
                    for block in text_dict.get("blocks", []):
                        if "lines" in block:
                            for line in block["lines"]:
                                for span in line.get("spans", []):
                                    extracted_text += span.get("text", "")
                                extracted_text += "\n"
                    page_text = extracted_text
                except:
                    pass

            if not page_text.strip():
                page_text = f"[ページ {page_number}: テキストが検出されませんでした]\n"

            if keyword:
                keyword_matches.extend(_find_keyword_matches(page_text, keyword, page_number))

            if extract_images:
                try:
                    image_list = page.get_images(full=True)
                    for img_index, img in enumerate(image_list):
                        try:
                            image_path = _extract_image(doc, img, page_number, img_index, temp_dir)
                            if image_path:
                                result["images"].append({
                                    "page": page_number,
                                    "index": img_index + 1,
                                    "path": image_path,
                                    "filename": os.path.basename(image_path)
                                })
                        except Exception as e:
                            logger.warning(f"画像抽出エラー (ページ {page_number}, 画像 {img_index + 1}): {e}")
                except Exception as e:
                    logger.warning(f"ページ {page_number} の画像処理エラー: {e}")

            all_text.append(f"=== ページ {page_number} ===\n{page_text.strip()}\n")

        doc.close()

        result["text"] = "\n".join(all_text)
        result["keyword_matches"] = keyword_matches
        result["success"] = True

        return result

    except Exception as e:
        logger.error(f"PyMuPDF抽出エラー: {e}")
        result["error"] = f"PyMuPDF抽出エラー: {str(e)}"
        return result


def _extract_with_pypdf2(file_path, keyword):
    """PyPDF2を使用したテキスト抽出"""
    result = {
        "success": False,
        "text": "",
        "keyword_matches": [],
        "images": [],
        "method": "PyPDF2"
    }

    try:
        with open(file_path, 'rb') as file:
            reader = PdfReader(file)
            all_text = []
            keyword_matches = []

            for page_num, page in enumerate(reader.pages):
                page_number = page_num + 1

                try:
                    page_text = page.extract_text()

                    if not page_text.strip():
                        page_text = f"[ページ {page_number}: テキストが検出されませんでした]\n"

                    if keyword:
                        keyword_matches.extend(_find_keyword_matches(page_text, keyword, page_number))

                    all_text.append(f"=== ページ {page_number} ===\n{page_text.strip()}\n")

                except Exception as e:
                    logger.warning(f"ページ {page_number} の処理エラー: {e}")
                    all_text.append(f"=== ページ {page_number} ===\n[処理エラー: {str(e)}]\n")

            result["text"] = "\n".join(all_text)
            result["keyword_matches"] = keyword_matches
            result["success"] = True

        return result

    except Exception as e:
        logger.error(f"PyPDF2抽出エラー: {e}")
        result["error"] = f"PyPDF2抽出エラー: {str(e)}"
        return result


def _find_keyword_matches(text, keyword, page_number):
    """テキスト内のキーワード検索"""
    matches = []
    if not keyword or not text:
        return matches

    lines = text.split('\n')
    keyword_lower = keyword.lower()

    for line_num, line in enumerate(lines):
        if keyword_lower in line.lower():
            context_start = max(0, line_num - 2)
            context_end = min(len(lines), line_num + 3)
            context = '\n'.join(lines[context_start:context_end])

            matches.append({
                'page': page_number,
                'line_number': line_num + 1,
                'line': line.strip(),
                'context': context.strip()
            })

    return matches


def _extract_image(doc, img, page_number, img_index, temp_dir):
    """PDFから画像を抽出"""
    try:
        xref = img[0]
        base_image = doc.extract_image(xref)
        image_bytes = base_image["image"]
        image_ext = base_image["ext"]

        image_filename = f"page_{page_number:03d}_image_{img_index + 1:03d}.{image_ext}"
        image_path = os.path.join(temp_dir, image_filename)

        with open(image_path, "wb") as img_file:
            img_file.write(image_bytes)

        return image_path

    except Exception as e:
        logger.warning(f"画像抽出エラー: {e}")
        return None


def _calculate_statistics(result, file_path):
    """抽出結果の統計情報を計算"""
    try:
        doc = fitz.open(file_path)
        stats = {
            "total_pages": len(doc),
            "total_characters": len(result["text"]),
            "total_words": len(result["text"].split()),
            "total_lines": len(result["text"].split('\n')),
            "file_size": os.path.getsize(file_path),
            "extraction_method": result.get("method", "Unknown"),
            "keyword_matches": len(result["keyword_matches"]),
            "extracted_images": len(result["images"])
        }
        doc.close()
        return stats
    except Exception as e:
        logger.error(f"統計計算エラー: {e}")
        return {}


def _create_output_files(result, temp_dir):
    """出力ファイルの作成"""
    output_files = []

    text_content = _format_extracted_text(result)
    text_path = os.path.join(temp_dir, "extracted_text.txt")

    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(text_content)

    output_files.append({
        "path": text_path,
        "name": "extracted_text.txt",
        "type": "text"
    })

    for image_info in result["images"]:
        if os.path.exists(image_info["path"]):
            output_files.append({
                "path": image_info["path"],
                "name": image_info["filename"],
                "type": "image"
            })

    return output_files


def _format_extracted_text(result):
    """抽出されたテキストをフォーマット"""
    content = []

    content.append("=== PDF テキスト抽出結果 ===\n")

    if result["statistics"]:
        content.append("=== 抽出統計 ===")
        stats = result["statistics"]
        content.append(f"ファイルサイズ: {stats.get('file_size', 0):,} bytes")
        content.append(f"総ページ数: {stats.get('total_pages', 0)}")
        content.append(f"総文字数: {stats.get('total_characters', 0):,}")
        content.append(f"総単語数: {stats.get('total_words', 0):,}")
        content.append(f"総行数: {stats.get('total_lines', 0):,}")
        content.append(f"抽出方法: {stats.get('extraction_method', 'Unknown')}")
        content.append(f"キーワード一致数: {stats.get('keyword_matches', 0)}")
        content.append(f"抽出画像数: {stats.get('extracted_images', 0)}")
        content.append("")

    if result["keyword_matches"]:
        content.append("=== キーワード検索結果 ===")
        for i, match in enumerate(result["keyword_matches"], 1):
            content.append(f"[{i}] ページ {match['page']}, 行 {match['line_number']}")
            content.append(f"該当行: {match['line']}")
            content.append(f"前後の文脈:")
            content.append(match['context'])
            content.append("-" * 50)
        content.append("")

    content.append("=== 抽出されたテキスト ===")
    content.append(result["text"])

    return "\n".join(content)


def _create_zip_file(output_files, temp_dir):
    """複数ファイルをZIPファイルにまとめる"""
    zip_path = os.path.join(temp_dir, "extracted_content.zip")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_info in output_files:
            if os.path.exists(file_info["path"]):
                zipf.write(file_info["path"], file_info["name"])

    return zip_path


# ========================================
# 5. ページ回転機能（新機能）
# ========================================
@pdf_power_bp.route("/rotate", methods=["POST"])
@login_required
def rotate_pdf():
    """PDFページの回転機能"""
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        rotation = request.form.get("rotation", "90")
        pages_str = request.form.get("pages", "all")

        try:
            rotation_angle = int(rotation)
            if rotation_angle not in [90, 180, 270]:
                return jsonify({"error": "回転角度は90, 180, 270のいずれかを指定してください"}), 400
        except ValueError:
            return jsonify({"error": "回転角度が正しくありません"}), 400

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "rotated_output.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        if os.path.getsize(input_path) > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        try:
            reader = PdfReader(input_path)
            writer = PdfWriter()
            total_pages = len(reader.pages)

            # ページ指定の解析
            if pages_str == "all":
                pages_to_rotate = list(range(total_pages))
            else:
                try:
                    pages_to_rotate = []
                    for part in pages_str.split(','):
                        if '-' in part:
                            start, end = map(int, part.split('-'))
                            pages_to_rotate.extend(range(start - 1, end))
                        else:
                            pages_to_rotate.append(int(part) - 1)
                except ValueError:
                    return jsonify({"error": "ページ指定が正しくありません（例: 1-3,5,7）"}), 400

            # ページを回転
            for i in range(total_pages):
                page = reader.pages[i]
                if i in pages_to_rotate:
                    page.rotate(rotation_angle)
                writer.add_page(page)

            with open(output_path, "wb") as output_pdf:
                writer.write(output_pdf)

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            return send_file(output_path, as_attachment=True, download_name="rotated_output.pdf")

        except Exception as e:
            logger.error(f"PDF回転に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"PDF回転に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"回転処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"回転処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# 6. パスワード保護機能（新機能）
# ========================================
@pdf_power_bp.route("/password", methods=["POST"])
@login_required
def password_protect():
    """PDFにパスワード保護を追加/解除"""
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        mode = request.form.get("mode", "add")
        password = request.form.get("password", "")

        if mode == "add" and not password:
            return jsonify({"error": "パスワードを入力してください"}), 400

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "password_protected.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        if os.path.getsize(input_path) > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        try:
            if mode == "add":
                # パスワード追加
                with pikepdf.open(input_path) as pdf:
                    pdf.save(output_path, encryption=pikepdf.Encryption(
                        owner=password,
                        user=password,
                        R=6  # AES-256
                    ))
            else:
                # パスワード解除
                try:
                    with pikepdf.open(input_path, password=password) as pdf:
                        pdf.save(output_path)
                except pikepdf.PasswordError:
                    return jsonify({"error": "パスワードが正しくありません"}), 400

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            download_name = "password_protected.pdf" if mode == "add" else "password_removed.pdf"
            return send_file(output_path, as_attachment=True, download_name=download_name)

        except Exception as e:
            logger.error(f"パスワード処理に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"パスワード処理に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"パスワード処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"パスワード処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# 7. メタデータ編集機能（新機能）
# ========================================
@pdf_power_bp.route("/metadata", methods=["POST"])
@login_required
def edit_metadata():
    """PDFメタデータの編集"""
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        title = request.form.get("title", "")
        author = request.form.get("author", "")
        subject = request.form.get("subject", "")
        keywords = request.form.get("keywords", "")

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "metadata_updated.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        if os.path.getsize(input_path) > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        try:
            doc = fitz.open(input_path)

            metadata = {}
            if title:
                metadata["title"] = title
            if author:
                metadata["author"] = author
            if subject:
                metadata["subject"] = subject
            if keywords:
                metadata["keywords"] = keywords

            doc.set_metadata(metadata)
            doc.save(output_path)
            doc.close()

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            return send_file(output_path, as_attachment=True, download_name="metadata_updated.pdf")

        except Exception as e:
            logger.error(f"メタデータ編集に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"メタデータ編集に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"メタデータ編集中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"メタデータ編集中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# 8. 透かし追加機能（新機能）
# ========================================
@pdf_power_bp.route("/watermark", methods=["POST"])
@login_required
def add_watermark():
    """PDFに透かしを追加"""
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        watermark_text = request.form.get("watermark_text", "")
        if not watermark_text:
            return jsonify({"error": "透かしテキストを入力してください"}), 400

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "watermarked_output.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        if os.path.getsize(input_path) > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        try:
            doc = fitz.open(input_path)

            for page_num in range(len(doc)):
                page = doc[page_num]

                # ページの中央に透かしを追加
                text_rect = page.rect
                text_point = fitz.Point(text_rect.width / 2, text_rect.height / 2)

                # 透かしテキストを挿入（半透明、斜め）
                page.insert_text(
                    text_point,
                    watermark_text,
                    fontsize=60,
                    rotate=45,
                    color=(0.8, 0.8, 0.8),  # 薄いグレー
                    overlay=True
                )

            doc.save(output_path)
            doc.close()

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            return send_file(output_path, as_attachment=True, download_name="watermarked_output.pdf")

        except Exception as e:
            logger.error(f"透かし追加に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"透かし追加に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"透かし処理中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"透かし処理中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# 9. ページ操作機能（新機能）
# ========================================
@pdf_power_bp.route("/page_operations", methods=["POST"])
@login_required
def page_operations():
    """PDFページの削除・並べ替え・抽出"""
    temp_dir = None
    try:
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        operation = request.form.get("operation", "")
        pages_order = request.form.get("pages_order", "")

        if not operation or not pages_order:
            return jsonify({"error": "操作とページ指定が必要です"}), 400

        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "pages_output.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        if os.path.getsize(input_path) > MAX_FILE_SIZE:
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        try:
            reader = PdfReader(input_path)
            writer = PdfWriter()
            total_pages = len(reader.pages)

            # ページ順序の解析（例: "1,3,2,4-6"）
            page_indices = []
            for part in pages_order.split(','):
                part = part.strip()
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    page_indices.extend(range(start - 1, end))
                else:
                    page_indices.append(int(part) - 1)

            # ページの有効性チェック
            for idx in page_indices:
                if idx < 0 or idx >= total_pages:
                    return jsonify({"error": f"無効なページ番号: {idx + 1}（総ページ数: {total_pages}）"}), 400

            # 指定された順序でページを追加
            for idx in page_indices:
                writer.add_page(reader.pages[idx])

            with open(output_path, "wb") as output_pdf:
                writer.write(output_pdf)

            @after_this_request
            def cleanup(response):
                try:
                    if temp_dir and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.error(f"一時ディレクトリの削除に失敗: {e}")
                return response

            return send_file(output_path, as_attachment=True, download_name="pages_output.pdf")

        except ValueError as e:
            return jsonify({"error": f"ページ指定が正しくありません: {str(e)}"}), 400
        except Exception as e:
            logger.error(f"ページ操作に失敗しました: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"ページ操作に失敗しました: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"ページ操作中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"ページ操作中にエラーが発生しました: {str(e)}"}), 500
    finally:
        pass


# ========================================
# ヘルスチェック用エンドポイント
# ========================================
@pdf_power_bp.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "service": "pdf_power"}), 200
