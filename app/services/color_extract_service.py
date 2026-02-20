import io
from typing import Iterable, List, Sequence, Tuple

import fitz
import numpy as np
from PIL import Image


Color = Tuple[int, int, int]


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


def apply_color_operation(
    image: Image.Image,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
) -> Image.Image:
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]

    mask = _mask_for_colors(rgb, target_colors, tolerance)

    if mode == "extract":
        if transparent_output:
            alpha[~mask] = 0
        else:
            rgb[~mask] = [255, 255, 255]
    elif mode == "exclude":
        if transparent_output:
            alpha[mask] = 0
        else:
            rgb[mask] = [255, 255, 255]
    elif mode == "replace":
        if replacement_color is None:
            raise ValueError("replacement_color is required for replace mode")
        rgb[mask] = np.array(replacement_color, dtype=np.uint8)
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
        )
        return _pil_to_png_bytes(processed)
    finally:
        doc.close()
