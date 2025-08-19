from flask import Blueprint, render_template, request, send_file, jsonify
import os
import tempfile
import shutil
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

# 日本語フォント設定（必要に応じて）
try:
    # システムにある日本語フォントを使用（環境に応じて調整）
    pdfmetrics.registerFont(TTFont('NotoSansCJK', '/System/Library/Fonts/NotoSansCJK.ttc'))
    JAPANESE_FONT = 'NotoSansCJK'
except:
    # フォントが見つからない場合はHelveticaを使用
    JAPANESE_FONT = 'Helvetica'


@pdf_power_bp.route("/", methods=["GET"])
@login_required
def pdf_power_home():
    return render_template("pdf_power.html")


@pdf_power_bp.route("/convert", methods=["POST"])
@login_required
def convert_pdf():
    temp_dir = None
    try:
        files = request.files.getlist("files")
        if not files or not files[0].filename:
            return "ファイルがアップロードされていません", 400
        
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

            if ext in ["png", "jpg", "jpeg", "bmp", "gif"]:
                # 画像ファイルをPDF用に保存
                try:
                    img = Image.open(file_path).convert("RGB")
                    pdf_images.append(img)
                except Exception as e:
                    logger.error(f"画像の処理に失敗しました: {e}")
                    return f"画像の処理に失敗しました: {filename}", 400

            elif ext == "txt":
                # TXT → PDF化（改善版）
                try:
                    c = canvas.Canvas(output_path, pagesize=letter)
                    width, height = letter
                    
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # テキストを行に分割
                    lines = content.split('\n')
                    y = height - 72  # 上マージン
                    line_height = 14
                    
                    for line in lines:
                        # 長い行を分割
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
                    return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")
                    
                except Exception as e:
                    logger.error(f"テキストファイルの処理に失敗しました: {e}")
                    return f"テキストファイルの処理に失敗しました: {e}", 400

            elif ext == "docx":
                # Word → PDF化（改善版）
                try:
                    doc = Document(file_path)
                    c = canvas.Canvas(output_path, pagesize=letter)
                    width, height = letter
                    y = height - 72
                    
                    for para in doc.paragraphs:
                        text = para.text.strip()
                        if text:
                            # 長いテキストを分割
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
                    return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")
                    
                except Exception as e:
                    logger.error(f"Wordファイルの処理に失敗しました: {e}")
                    return f"Wordファイルの処理に失敗しました: {e}", 400

            else:
                return f"対応していないファイル形式です: {ext}. PNG, JPG, JPEG, BMP, GIF, TXT, DOCXのみ対応しています。", 400

        # 複数画像をPDFとして結合
        if pdf_images:
            try:
                pdf_images[0].save(output_path, save_all=True, append_images=pdf_images[1:])
                return send_file(output_path, as_attachment=True, download_name="converted_output.pdf")
            except Exception as e:
                logger.error(f"画像PDFの作成に失敗しました: {e}")
                return f"画像PDFの作成に失敗しました: {e}", 500

        return "処理するファイルが見つかりませんでした", 400
        
    except Exception as e:
        logger.error(f"変換処理中にエラーが発生しました: {e}")
        return f"変換処理中にエラーが発生しました: {e}", 500
    finally:
        # 一時ディレクトリのクリーンアップ
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"一時ディレクトリの削除に失敗しました: {e}")


@pdf_power_bp.route("/split_merge", methods=["POST"])
@login_required
def split_or_merge_pdf():
    temp_dir = None
    try:
        mode = request.form.get("mode")
        if mode not in ["split", "merge"]:
            return "無効な操作です", 400
        
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, "output.pdf")
        
        # ファイルアップロード
        files = request.files.getlist("pdfs")
        if not files or not files[0].filename:
            return "PDFファイルがアップロードされていません", 400
        
        file_paths = []
        for file in files:
            if not file.filename:
                continue
            filename = secure_filename(file.filename)
            if not filename.endswith('.pdf'):
                return f"PDFファイルのみ対応しています: {filename}", 400
            
            file_path = os.path.join(temp_dir, filename)
            file.save(file_path)
            file_paths.append(file_path)

        if not file_paths:
            return "有効なPDFファイルが見つかりませんでした", 400

        # 分割
        if mode == "split":
            if len(file_paths) != 1:
                return "分割は1つのPDFファイルのみ対応しています", 400
            
            page_range = request.form.get("range")
            if not page_range:
                return "ページ範囲を指定してください（例: 1-5）", 400
            
            try:
                if '-' in page_range:
                    start, end = map(int, page_range.split('-'))
                else:
                    start = end = int(page_range)
            except ValueError:
                return "ページ範囲の指定が正しくありません（例: 1-5）", 400

            try:
                reader = PdfReader(file_paths[0])
                total_pages = len(reader.pages)
                
                if start < 1 or end > total_pages or start > end:
                    return f"ページ範囲が無効です。1-{total_pages}の範囲で指定してください", 400
                
                writer = PdfWriter()
                for page_num in range(start - 1, end):
                    writer.add_page(reader.pages[page_num])

                with open(output_path, "wb") as output_pdf:
                    writer.write(output_pdf)
                
                return send_file(output_path, as_attachment=True, download_name="split_output.pdf")
                
            except Exception as e:
                logger.error(f"PDF分割に失敗しました: {e}")
                return f"PDF分割に失敗しました: {e}", 500

        # 結合
        elif mode == "merge":
            if len(file_paths) < 2:
                return "結合には2つ以上のPDFファイルが必要です", 400
            
            try:
                writer = PdfWriter()
                for file_path in file_paths:
                    reader = PdfReader(file_path)
                    for page in reader.pages:
                        writer.add_page(page)

                with open(output_path, "wb") as output_pdf:
                    writer.write(output_pdf)
                
                return send_file(output_path, as_attachment=True, download_name="merged_output.pdf")
                
            except Exception as e:
                logger.error(f"PDF結合に失敗しました: {e}")
                return f"PDF結合に失敗しました: {e}", 500

    except Exception as e:
        logger.error(f"PDF操作中にエラーが発生しました: {e}")
        return f"PDF操作中にエラーが発生しました: {e}", 500
    finally:
        # 一時ディレクトリのクリーンアップ
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"一時ディレクトリの削除に失敗しました: {e}")

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

        # 圧縮レベルの取得（デフォルト: medium）
        compression_level = request.form.get("compression_level", "medium")
        
        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "compressed_output.pdf")
        file.save(input_path)

        # ファイルサイズチェック
        original_size = os.path.getsize(input_path)
        if original_size > 100 * 1024 * 1024:  # 100MB制限
            return jsonify({"error": "ファイルサイズが大きすぎます（100MB以下にしてください）"}), 400

        # ファイルの有効性チェック
        try:
            test_doc = fitz.open(input_path)
            if test_doc.page_count == 0:
                return jsonify({"error": "PDFファイルが空です"}), 400
            test_doc.close()
        except Exception as e:
            return jsonify({"error": f"PDFファイルが破損しています: {str(e)}"}), 400

        try:
            # 改善された圧縮処理
            compressed_size = improved_pdf_compression(
                input_path, output_path, compression_level
            )
            
            if compressed_size == 0:
                return jsonify({"error": "PDF圧縮に失敗しました"}), 500
            
            compression_ratio = (1 - compressed_size / original_size) * 100
            
            logger.info(f"PDF圧縮完了: {original_size} -> {compressed_size} bytes ({compression_ratio:.1f}% 削減)")
            
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
        # 一時ディレクトリのクリーンアップ
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"一時ディレクトリの削除に失敗しました: {e}")


def improved_pdf_compression(input_path, output_path, compression_level="medium"):
    """
    改善されたPDF圧縮処理
    """
    try:
        # 圧縮レベルに応じた設定
        if compression_level == "low":
            image_quality = 85
            max_image_size = (1200, 1200)
        elif compression_level == "high":
            image_quality = 50
            max_image_size = (800, 800)
        elif compression_level == "maximum":
            image_quality = 30
            max_image_size = (600, 600)
        else:  # medium
            image_quality = 70
            max_image_size = (1000, 1000)

        # Step 1: PyMuPDFを使用した基本的な圧縮
        doc = fitz.open(input_path)
        
        # 画像圧縮処理
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # 画像を抽出して圧縮
            image_list = page.get_images(full=True)
            
            for img_index, img in enumerate(image_list):
                try:
                    # 画像データを取得
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    # 画像を圧縮
                    if image_ext.lower() in ["png", "jpg", "jpeg"]:
                        img_obj = Image.open(io.BytesIO(image_bytes))
                        
                        # 画像サイズを調整
                        if img_obj.size[0] > max_image_size[0] or img_obj.size[1] > max_image_size[1]:
                            img_obj.thumbnail(max_image_size, Image.Resampling.LANCZOS)
                        
                        # JPEG形式で圧縮
                        img_buffer = io.BytesIO()
                        if img_obj.mode in ("RGBA", "LA", "P"):
                            img_obj = img_obj.convert("RGB")
                        
                        img_obj.save(img_buffer, format="JPEG", quality=image_quality, optimize=True)
                        compressed_image_bytes = img_buffer.getvalue()
                        
                        # 圧縮効果があった場合のみ置換
                        if len(compressed_image_bytes) < len(image_bytes) * 0.9:
                            # 新しい画像オブジェクトを作成
                            new_img = fitz.open("jpeg", compressed_image_bytes)
                            new_img_page = new_img[0]
                            
                            # 元の画像と置換
                            page.insert_image(
                                page.get_image_bbox(xref),
                                stream=compressed_image_bytes
                            )
                            
                            new_img.close()
                            
                except Exception as e:
                    logger.warning(f"画像圧縮をスキップしました (xref: {xref}): {e}")
                    continue
        
        # メタデータの削除
        doc.set_metadata({})
        
        # 一時保存（garbage collection, deflate, clean適用）
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        
        # Step 2: pikepdfを使用した追加最適化
        try:
            with pikepdf.open(output_path) as pdf:
                # 重複オブジェクトの除去
                pdf.remove_unreferenced_resources()
                
                # 最終保存（さらなる最適化）
                pdf.save(
                    output_path,
                    compress_streams=True,
                    normalize_content=True,
                    linearize=True
                )
        except Exception as e:
            logger.warning(f"pikepdf最適化をスキップしました: {e}")
        
        return os.path.getsize(output_path)
        
    except Exception as e:
        logger.error(f"PDF圧縮処理に失敗しました: {e}")
        logger.error(traceback.format_exc())
        return 0


@pdf_power_bp.route("/extract", methods=["POST"])
@login_required
def extract_text():
    """
    改善されたPDFテキスト抽出機能
    複数のライブラリを使用してテキスト抽出の成功率を向上
    """
    temp_dir = None
    try:
        # リクエストの検証
        file = request.files.get("pdf")
        if not file or not file.filename:
            return jsonify({"error": "PDFファイルがアップロードされていません"}), 400

        filename = secure_filename(file.filename)
        if not filename.endswith('.pdf'):
            return jsonify({"error": "PDFファイルのみ対応しています"}), 400

        # パラメータの取得
        keyword = request.form.get("keyword", "").strip()
        extract_images = request.form.get("extract_images", "false").lower() == "true"
        
        # 一時ディレクトリの作成
        temp_dir = tempfile.mkdtemp()
        input_path = os.path.join(temp_dir, filename)
        file.save(input_path)

        # ファイルサイズと有効性の検証
        file_size = os.path.getsize(input_path)
        if file_size > 50 * 1024 * 1024:  # 50MB制限
            return jsonify({"error": "ファイルサイズが大きすぎます（50MB以下にしてください）"}), 400

        # PDFの有効性確認
        if not _validate_pdf_file(input_path):
            return jsonify({"error": "PDFファイルが破損しているか、読み取れません"}), 400

        # テキスト抽出の実行
        extraction_result = _extract_text_from_pdf(input_path, keyword, extract_images, temp_dir)
        
        if extraction_result["success"]:
            # 結果ファイルの作成
            output_files = _create_output_files(extraction_result, temp_dir)
            
            # 単一ファイルまたはZIPファイルの返却
            if len(output_files) == 1:
                return send_file(
                    output_files[0]["path"], 
                    as_attachment=True, 
                    download_name=output_files[0]["name"]
                )
            else:
                zip_path = _create_zip_file(output_files, temp_dir)
                return send_file(zip_path, as_attachment=True, download_name="extracted_content.zip")
        else:
            return jsonify({"error": extraction_result["error"]}), 500

    except Exception as e:
        logger.error(f"テキスト抽出処理中にエラーが発生しました: {e}")
        return jsonify({"error": f"予期しないエラーが発生しました: {str(e)}"}), 500
    finally:
        # 一時ディレクトリのクリーンアップ
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"一時ディレクトリの削除に失敗しました: {e}")


def _validate_pdf_file(file_path):
    """
    PDFファイルの有効性を確認
    """
    try:
        # PyMuPDFでの確認
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            doc.close()
            return False
        doc.close()
        
        # PyPDF2での確認
        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            if len(reader.pages) == 0:
                return False
        
        return True
    except Exception as e:
        logger.error(f"PDFファイル検証エラー: {e}")
        return False


def _extract_text_from_pdf(file_path, keyword, extract_images, temp_dir):
    """
    PDFからテキストを抽出（複数手法を試行）
    """
    try:
        # 抽出結果の初期化
        result = {
            "success": False,
            "text": "",
            "keyword_matches": [],
            "images": [],
            "statistics": {},
            "error": None
        }
        
        # 方法1: PyMuPDFを使用した抽出
        pymupdf_result = _extract_with_pymupdf(file_path, keyword, extract_images, temp_dir)
        
        # 方法2: PyPDF2を使用した抽出（バックアップ）
        pypdf2_result = _extract_with_pypdf2(file_path, keyword)
        
        # 最良の結果を選択
        if pymupdf_result["success"] and len(pymupdf_result["text"]) > 0:
            result = pymupdf_result
        elif pypdf2_result["success"] and len(pypdf2_result["text"]) > 0:
            result = pypdf2_result
            # PyPDF2では画像抽出はできないため、PyMuPDFの画像結果を使用
            if extract_images and pymupdf_result["images"]:
                result["images"] = pymupdf_result["images"]
        else:
            result["error"] = "どの手法でもテキストを抽出できませんでした"
            return result
        
        # 統計情報の計算
        result["statistics"] = _calculate_statistics(result, file_path)
        result["success"] = True
        
        return result
        
    except Exception as e:
        logger.error(f"テキスト抽出処理でエラー: {e}")
        return {
            "success": False,
            "error": f"テキスト抽出処理でエラー: {str(e)}"
        }


def _extract_with_pymupdf(file_path, keyword, extract_images, temp_dir):
    """
    PyMuPDFを使用したテキスト抽出
    """
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
        image_count = 0
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_number = page_num + 1
            
            # テキスト抽出（複数の方法を試行）
            page_text = ""
            
            # 方法1: 標準的なテキスト抽出
            try:
                page_text = page.get_text()
            except:
                pass
            
            # 方法2: より詳細なテキスト抽出
            if len(page_text.strip()) < 10:
                try:
                    page_text = page.get_text("text")
                except:
                    pass
            
            # 方法3: 辞書形式での抽出
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
            
            # テキストが空の場合の処理
            if not page_text.strip():
                page_text = f"[ページ {page_number}: テキストが検出されませんでした]\n"
            
            # キーワード検索
            if keyword:
                keyword_matches.extend(_find_keyword_matches(page_text, keyword, page_number))
            
            # 画像抽出
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
                                image_count += 1
                        except Exception as e:
                            logger.warning(f"画像抽出エラー (ページ {page_number}, 画像 {img_index + 1}): {e}")
                except Exception as e:
                    logger.warning(f"ページ {page_number} の画像処理エラー: {e}")
            
            # ページテキストを追加
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
    """
    PyPDF2を使用したテキスト抽出（バックアップ手法）
    """
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
                    # テキスト抽出
                    page_text = page.extract_text()
                    
                    if not page_text.strip():
                        page_text = f"[ページ {page_number}: テキストが検出されませんでした]\n"
                    
                    # キーワード検索
                    if keyword:
                        keyword_matches.extend(_find_keyword_matches(page_text, keyword, page_number))
                    
                    # ページテキストを追加
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
    """
    テキスト内のキーワード検索
    """
    matches = []
    if not keyword or not text:
        return matches
    
    lines = text.split('\n')
    keyword_lower = keyword.lower()
    
    for line_num, line in enumerate(lines):
        if keyword_lower in line.lower():
            # 前後の文脈を取得
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
    """
    PDFから画像を抽出
    """
    try:
        xref = img[0]
        base_image = doc.extract_image(xref)
        image_bytes = base_image["image"]
        image_ext = base_image["ext"]
        
        # 画像ファイル名の生成
        image_filename = f"page_{page_number:03d}_image_{img_index + 1:03d}.{image_ext}"
        image_path = os.path.join(temp_dir, image_filename)
        
        # 画像の保存
        with open(image_path, "wb") as img_file:
            img_file.write(image_bytes)
        
        return image_path
        
    except Exception as e:
        logger.warning(f"画像抽出エラー: {e}")
        return None


def _calculate_statistics(result, file_path):
    """
    抽出結果の統計情報を計算
    """
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
    """
    出力ファイルの作成
    """
    output_files = []
    
    # テキストファイルの作成
    text_content = _format_extracted_text(result)
    text_path = os.path.join(temp_dir, "extracted_text.txt")
    
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(text_content)
    
    output_files.append({
        "path": text_path,
        "name": "extracted_text.txt",
        "type": "text"
    })
    
    # 画像ファイルがある場合は追加
    for image_info in result["images"]:
        if os.path.exists(image_info["path"]):
            output_files.append({
                "path": image_info["path"],
                "name": image_info["filename"],
                "type": "image"
            })
    
    return output_files


def _format_extracted_text(result):
    """
    抽出されたテキストをフォーマット
    """
    content = []
    
    # ヘッダー情報
    content.append("=== PDF テキスト抽出結果 ===\n")
    
    # 統計情報
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
    
    # キーワード検索結果
    if result["keyword_matches"]:
        content.append("=== キーワード検索結果 ===")
        for i, match in enumerate(result["keyword_matches"], 1):
            content.append(f"[{i}] ページ {match['page']}, 行 {match['line_number']}")
            content.append(f"該当行: {match['line']}")
            content.append(f"前後の文脈:")
            content.append(match['context'])
            content.append("-" * 50)
        content.append("")
    
    # 抽出されたテキスト
    content.append("=== 抽出されたテキスト ===")
    content.append(result["text"])
    
    return "\n".join(content)


def _create_zip_file(output_files, temp_dir):
    """
    複数ファイルをZIPファイルにまとめる
    """
    import zipfile
    
    zip_path = os.path.join(temp_dir, "extracted_content.zip")
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_info in output_files:
            if os.path.exists(file_info["path"]):
                zipf.write(file_info["path"], file_info["name"])
    
    return zip_path

# ヘルスチェック用エンドポイント
@pdf_power_bp.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "service": "pdf_power"}), 200