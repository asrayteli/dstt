import io
from typing import Sequence, Tuple

import fitz
import numpy as np
import pikepdf
from PIL import Image

Color = Tuple[int, int, int]
Region = Tuple[float, float, float, float]


def detect_pdf_bytes(file_bytes: bytes) -> bool:
    header = file_bytes[:1024]
    return b"%PDF" in header


def normalize_pdf_bytes(file_bytes: bytes) -> bytes:
    """Validate/repair PDF bytes when possible and return readable bytes."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        doc.close()
        return file_bytes
    except Exception:
        pass

    try:
        with pikepdf.open(io.BytesIO(file_bytes), allow_overwriting_input=True) as pdf:
            out = io.BytesIO()
            pdf.save(out, linearize=True)
            repaired = out.getvalue()
        doc = fitz.open(stream=repaired, filetype="pdf")
        doc.close()
        return repaired
    except Exception as exc:
        raise ValueError("PDFとして読み込めない、または破損している可能性があります。") from exc


def parse_hex_color(value: str) -> Color:
    text = (value or "").strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        raise ValueError(f"Invalid HEX color: {value}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"Invalid HEX color: {value}") from exc


def _mask_for_colors(rgb_array: np.ndarray, target_colors: Sequence[Color], tolerance: int) -> np.ndarray:
    if not target_colors:
        raise ValueError("target_colors is empty")

    rgb = rgb_array.astype(np.int32)
    threshold2 = int(tolerance) * int(tolerance)
    mask = np.zeros(rgb.shape[:2], dtype=bool)

    for color in target_colors:
        tr, tg, tb = color
        diff = rgb - np.array([tr, tg, tb], dtype=np.int32)
        dist2 = np.sum(diff * diff, axis=2)
        mask |= dist2 <= threshold2

    return mask


def _build_region_mask(height: int, width: int, region: Region | None) -> np.ndarray | None:
    if region is None:
        return None

    rx, ry, rw, rh = region
    x0 = max(0, min(width, int(round(rx * width))))
    y0 = max(0, min(height, int(round(ry * height))))
    x1 = max(0, min(width, int(round((rx + rw) * width))))
    y1 = max(0, min(height, int(round((ry + rh) * height))))

    if x1 <= x0 or y1 <= y0:
        raise ValueError("処理範囲が不正です。")

    region_mask = np.zeros((height, width), dtype=bool)
    region_mask[y0:y1, x0:x1] = True
    return region_mask


def apply_color_operation(
    image: Image.Image,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    region: Region | None = None,
) -> Image.Image:
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    h, w = rgb.shape[:2]

    color_mask = _mask_for_colors(rgb, target_colors, tolerance)
    region_mask = _build_region_mask(h, w, region)
    effective_mask = color_mask if region_mask is None else (color_mask & region_mask)

    if mode == "extract":
        if region_mask is None:
            if transparent_output:
                alpha[~effective_mask] = 0
            else:
                rgb[~effective_mask] = [255, 255, 255]
        else:
            within_region_nonmatch = region_mask & ~effective_mask
            if transparent_output:
                alpha[within_region_nonmatch] = 0
            else:
                rgb[within_region_nonmatch] = [255, 255, 255]
    elif mode == "exclude":
        if transparent_output:
            alpha[effective_mask] = 0
        else:
            rgb[effective_mask] = [255, 255, 255]
    elif mode == "replace":
        if replacement_color is None:
            raise ValueError("replacement_color is required for replace mode")
        rgb[effective_mask] = np.array(replacement_color, dtype=np.uint8)
    else:
        raise ValueError("Unsupported mode")

    result = np.dstack([rgb, alpha])
    return Image.fromarray(result.astype(np.uint8), mode="RGBA")


def _pixmap_to_pil(pix: fitz.Pixmap) -> Image.Image:
    mode = "RGB" if pix.n < 4 else "RGBA"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _pil_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def process_pdf_bytes(
    pdf_bytes: bytes,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    zoom: float = 2.0,
    region: Region | None = None,
) -> bytes:
    source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        for page in source_doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = _pixmap_to_pil(pix)
            processed = apply_color_operation(
                image=image,
                mode=mode,
                target_colors=target_colors,
                tolerance=tolerance,
                transparent_output=transparent_output,
                replacement_color=replacement_color,
                region=region,
            )
            png_bytes = _pil_to_png_bytes(processed)
            width = processed.width
            height = processed.height

            new_page = out_doc.new_page(width=width, height=height)
            new_page.insert_image(fitz.Rect(0, 0, width, height), stream=png_bytes)

        return out_doc.tobytes(deflate=True)
    finally:
        source_doc.close()
        out_doc.close()


def render_pdf_first_page_preview(
    pdf_bytes: bytes,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    zoom: float = 1.5,
    region: Region | None = None,
) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if len(doc) == 0:
            raise ValueError("PDF has no pages")
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = _pixmap_to_pil(pix)
        processed = apply_color_operation(
            image=image,
            mode=mode,
            target_colors=target_colors,
            tolerance=tolerance,
            transparent_output=transparent_output,
            replacement_color=replacement_color,
            region=region,
        )
        return _pil_to_png_bytes(processed)
    finally:
        doc.close()
