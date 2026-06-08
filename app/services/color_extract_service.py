import io
from typing import Sequence, Tuple

import fitz
import numpy as np
import pikepdf
from PIL import Image, ImageDraw
from scipy import ndimage

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
        raise ValueError("PDF could not be parsed or repaired.") from exc


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


def _rgb_to_lab(rgb_array: np.ndarray) -> np.ndarray:
    rgb = rgb_array.astype(np.float32) / 255.0
    linear = np.where(
        rgb <= 0.04045,
        rgb / 12.92,
        ((rgb + 0.055) / 1.055) ** 2.4,
    )

    x = linear[:, :, 0] * 0.4124564 + linear[:, :, 1] * 0.3575761 + linear[:, :, 2] * 0.1804375
    y = linear[:, :, 0] * 0.2126729 + linear[:, :, 1] * 0.7151522 + linear[:, :, 2] * 0.0721750
    z = linear[:, :, 0] * 0.0193339 + linear[:, :, 1] * 0.1191920 + linear[:, :, 2] * 0.9503041

    x /= 0.95047
    z /= 1.08883

    epsilon = 216 / 24389
    kappa = 24389 / 27

    fx = np.where(x > epsilon, np.cbrt(x), (kappa * x + 16.0) / 116.0)
    fy = np.where(y > epsilon, np.cbrt(y), (kappa * y + 16.0) / 116.0)
    fz = np.where(z > epsilon, np.cbrt(z), (kappa * z + 16.0) / 116.0)

    l = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return np.dstack([l, a, b])


def _delta_e_ciede2000(lab_image: np.ndarray, lab_target: np.ndarray) -> np.ndarray:
    l1 = lab_image[:, :, 0]
    a1 = lab_image[:, :, 1]
    b1 = lab_image[:, :, 2]
    l2 = float(lab_target[0])
    a2 = float(lab_target[1])
    b2 = float(lab_target[2])

    c1 = np.sqrt(a1 * a1 + b1 * b1)
    c2 = np.sqrt(a2 * a2 + b2 * b2)
    c_bar = (c1 + c2) / 2.0

    c_bar7 = c_bar**7
    g = 0.5 * (1.0 - np.sqrt(c_bar7 / (c_bar7 + 25.0**7 + 1e-12)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = np.sqrt(a1p * a1p + b1 * b1)
    c2p = np.sqrt(a2p * a2p + b2 * b2)

    h1p = (np.degrees(np.arctan2(b1, a1p)) + 360.0) % 360.0
    h2p = (np.degrees(np.arctan2(b2, a2p)) + 360.0) % 360.0

    delta_lp = l2 - l1
    delta_cp = c2p - c1p

    hdiff = h2p - h1p
    hdiff = np.where(hdiff > 180.0, hdiff - 360.0, hdiff)
    hdiff = np.where(hdiff < -180.0, hdiff + 360.0, hdiff)
    hdiff = np.where((c1p * c2p) == 0.0, 0.0, hdiff)
    delta_hp = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(hdiff / 2.0))

    l_bar_p = (l1 + l2) / 2.0
    c_bar_p = (c1p + c2p) / 2.0

    hsum = h1p + h2p
    h_bar_p = np.where(
        (c1p * c2p) == 0.0,
        hsum,
        np.where(
            np.abs(h1p - h2p) > 180.0,
            np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0),
            hsum / 2.0,
        ),
    )

    t = (
        1.0
        - 0.17 * np.cos(np.radians(h_bar_p - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * h_bar_p))
        + 0.32 * np.cos(np.radians(3.0 * h_bar_p + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * h_bar_p - 63.0))
    )

    delta_theta = 30.0 * np.exp(-(((h_bar_p - 275.0) / 25.0) ** 2))
    r_c = 2.0 * np.sqrt((c_bar_p**7) / (c_bar_p**7 + 25.0**7 + 1e-12))

    s_l = 1.0 + (0.015 * ((l_bar_p - 50.0) ** 2)) / np.sqrt(20.0 + ((l_bar_p - 50.0) ** 2))
    s_c = 1.0 + 0.045 * c_bar_p
    s_h = 1.0 + 0.015 * c_bar_p * t

    r_t = -np.sin(np.radians(2.0 * delta_theta)) * r_c

    dl = delta_lp / s_l
    dc = delta_cp / s_c
    dh = delta_hp / s_h
    return np.sqrt(dl * dl + dc * dc + dh * dh + r_t * dc * dh)


def _rgb_to_hsv_saturation(rgb_array: np.ndarray) -> np.ndarray:
    rgb = rgb_array.astype(np.float32) / 255.0
    cmax = np.max(rgb, axis=2)
    cmin = np.min(rgb, axis=2)
    delta = cmax - cmin
    sat = np.where(cmax <= 0.0, 0.0, delta / cmax)
    return sat * 255.0


def _mask_for_colors(
    rgb_array: np.ndarray,
    target_colors: Sequence[Color],
    tolerance: int,
    *,
    color_metric: str,
    saturation_min: int,
    lightness_min: int,
    lightness_max: int,
) -> tuple[np.ndarray, np.ndarray]:
    if not target_colors:
        raise ValueError("target_colors is empty")

    if color_metric not in {"deltae", "rgb"}:
        raise ValueError("color_metric must be deltae or rgb")

    if color_metric == "rgb":
        rgb = rgb_array.astype(np.int32)
        threshold2 = int(tolerance) * int(tolerance)
        min_dist = None
        for color in target_colors:
            tr, tg, tb = color
            diff = rgb - np.array([tr, tg, tb], dtype=np.int32)
            dist2 = np.sum(diff * diff, axis=2)
            dist = np.sqrt(dist2.astype(np.float32))
            min_dist = dist if min_dist is None else np.minimum(min_dist, dist)
    else:
        lab = _rgb_to_lab(rgb_array)
        min_dist = None
        for color in target_colors:
            target_lab = _rgb_to_lab(np.array([[color]], dtype=np.uint8))[0, 0]
            dist = _delta_e_ciede2000(lab, target_lab).astype(np.float32)
            min_dist = dist if min_dist is None else np.minimum(min_dist, dist)

    mask = min_dist <= float(tolerance)

    if saturation_min > 0:
        sat = _rgb_to_hsv_saturation(rgb_array)
        mask &= sat >= float(saturation_min)

    if lightness_min > 0 or lightness_max < 255:
        lstar = _rgb_to_lab(rgb_array)[:, :, 0] * 2.55
        mask &= (lstar >= float(lightness_min)) & (lstar <= float(lightness_max))

    return mask, min_dist


def _build_region_mask(height: int, width: int, region: Region | None) -> np.ndarray | None:
    if region is None:
        return None

    rx, ry, rw, rh = region
    x0 = max(0, min(width, int(round(rx * width))))
    y0 = max(0, min(height, int(round(ry * height))))
    x1 = max(0, min(width, int(round((rx + rw) * width))))
    y1 = max(0, min(height, int(round((ry + rh) * height))))

    if x1 <= x0 or y1 <= y0:
        raise ValueError("Invalid process region.")

    region_mask = np.zeros((height, width), dtype=bool)
    region_mask[y0:y1, x0:x1] = True
    return region_mask


def _apply_preset_options(
    preset: str,
    *,
    enable_preprocess: bool,
    enable_postprocess: bool,
    saturation_min: int,
    lightness_min: int,
    lightness_max: int,
    min_component_area: int,
) -> tuple[bool, bool, int, int, int, int]:
    if preset == "none":
        return (
            enable_preprocess,
            enable_postprocess,
            saturation_min,
            lightness_min,
            lightness_max,
            min_component_area,
        )
    if preset == "stamp_red":
        return (
            True,
            True,
            max(saturation_min, 35),
            max(lightness_min, 20),
            min(lightness_max, 240),
            max(min_component_area, 30),
        )
    if preset == "blue_pen":
        return (
            True,
            True,
            max(saturation_min, 30),
            max(lightness_min, 15),
            min(lightness_max, 245),
            max(min_component_area, 20),
        )
    if preset == "highlighter":
        return (
            True,
            True,
            max(saturation_min, 25),
            max(lightness_min, 110),
            lightness_max,
            max(min_component_area, 20),
        )
    if preset == "keep_black_text":
        return (
            enable_preprocess,
            True,
            saturation_min,
            lightness_min,
            min(lightness_max, 120),
            max(min_component_area, 8),
        )
    if preset == "whiten_background":
        return (
            enable_preprocess,
            True,
            saturation_min,
            max(lightness_min, 170),
            lightness_max,
            min_component_area,
        )
    raise ValueError("Unsupported preset")


def _preprocess_rgb(rgb: np.ndarray, enabled: bool) -> np.ndarray:
    if not enabled:
        return rgb

    arr = rgb.astype(np.float32)
    channel_means = arr.reshape(-1, 3).mean(axis=0)
    mean_gray = float(np.mean(channel_means))
    gains = mean_gray / np.maximum(channel_means, 1e-6)
    arr *= gains.reshape(1, 1, 3)
    arr = np.clip(arr, 0, 255)

    arr = ndimage.median_filter(arr, size=(3, 3, 1))
    return arr.astype(np.uint8)


def _postprocess_mask(mask: np.ndarray, enabled: bool, min_component_area: int) -> np.ndarray:
    if not enabled:
        return mask

    structure = np.ones((3, 3), dtype=bool)
    cleaned = ndimage.binary_opening(mask, structure=structure)
    cleaned = ndimage.binary_closing(cleaned, structure=structure)

    if min_component_area > 0:
        labels, num = ndimage.label(cleaned)
        if num > 0:
            counts = np.bincount(labels.ravel())
            remove = counts < int(min_component_area)
            remove[0] = False
            cleaned = cleaned & (~remove[labels])

    return cleaned


def _prepare_effective_mask(
    rgb: np.ndarray,
    target_colors: Sequence[Color],
    tolerance: int,
    region: Region | None,
    *,
    color_metric: str,
    preset: str,
    enable_preprocess: bool,
    enable_postprocess: bool,
    saturation_min: int,
    lightness_min: int,
    lightness_max: int,
    min_component_area: int,
) -> tuple[np.ndarray, np.ndarray]:
    (
        enable_preprocess,
        enable_postprocess,
        saturation_min,
        lightness_min,
        lightness_max,
        min_component_area,
    ) = _apply_preset_options(
        preset,
        enable_preprocess=enable_preprocess,
        enable_postprocess=enable_postprocess,
        saturation_min=saturation_min,
        lightness_min=lightness_min,
        lightness_max=lightness_max,
        min_component_area=min_component_area,
    )

    rgb_for_mask = _preprocess_rgb(rgb, enable_preprocess)
    color_mask, min_dist = _mask_for_colors(
        rgb_for_mask,
        target_colors,
        tolerance,
        color_metric=color_metric,
        saturation_min=saturation_min,
        lightness_min=lightness_min,
        lightness_max=lightness_max,
    )
    color_mask = _postprocess_mask(color_mask, enable_postprocess, min_component_area)

    h, w = rgb.shape[:2]
    region_mask = _build_region_mask(h, w, region)
    effective_mask = color_mask if region_mask is None else (color_mask & region_mask)
    return effective_mask, min_dist


def apply_color_operation(
    image: Image.Image,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    region: Region | None = None,
    color_metric: str = "deltae",
    preset: str = "none",
    enable_preprocess: bool = True,
    enable_postprocess: bool = True,
    saturation_min: int = 0,
    lightness_min: int = 0,
    lightness_max: int = 255,
    min_component_area: int = 0,
) -> Image.Image:
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    effective_mask, _ = _prepare_effective_mask(
        rgb,
        target_colors,
        tolerance,
        region,
        color_metric=color_metric,
        preset=preset,
        enable_preprocess=enable_preprocess,
        enable_postprocess=enable_postprocess,
        saturation_min=saturation_min,
        lightness_min=lightness_min,
        lightness_max=lightness_max,
        min_component_area=min_component_area,
    )
    region_mask = _build_region_mask(rgb.shape[0], rgb.shape[1], region)

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


def crop_image_to_region(image: Image.Image, region: Region) -> Image.Image:
    width, height = image.size
    rx, ry, rw, rh = region
    x0 = max(0, min(width, int(round(rx * width))))
    y0 = max(0, min(height, int(round(ry * height))))
    x1 = max(0, min(width, int(round((rx + rw) * width))))
    y1 = max(0, min(height, int(round((ry + rh) * height))))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Invalid process region.")
    return image.crop((x0, y0, x1, y1))

def parse_hex_color_rgba(value: str) -> tuple[int, int, int, int]:
    text = (value or "").strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 6:
        text += "ff"
    if len(text) != 8:
        raise ValueError(f"Invalid RGBA color: {value}")
    try:
        return (
            int(text[0:2], 16),
            int(text[2:4], 16),
            int(text[4:6], 16),
            int(text[6:8], 16),
        )
    except ValueError as exc:
        raise ValueError(f"Invalid RGBA color: {value}") from exc
def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))
def _norm_to_px(width: int, height: int, x: float, y: float) -> tuple[int, int]:
    px = int(round(_clamp01(x) * width))
    py = int(round(_clamp01(y) * height))
    return max(0, min(width - 1, px)), max(0, min(height - 1, py))
def apply_paint_actions(
    image: Image.Image,
    paint_actions: list[dict] | None,
    *,
    page_number: int | None = None,
) -> Image.Image:
    if not paint_actions:
        return image
    base = image.convert("RGBA")
    width, height = base.size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    erase_mask = Image.new("L", (width, height), 0)
    draw_overlay = ImageDraw.Draw(overlay, "RGBA")
    draw_erase = ImageDraw.Draw(erase_mask, "L")
    for action in paint_actions:
        if not isinstance(action, dict):
            continue
        action_page = action.get("page")
        if page_number is not None and action_page is not None:
            try:
                if int(action_page) != int(page_number):
                    continue
            except Exception:
                continue
        action_type = str(action.get("type", "")).strip().lower()
        color = parse_hex_color_rgba(str(action.get("color", "#000000ff")))
        alpha = color[3]
        brush_size = max(1, int(action.get("size", 8)))
        if action_type == "rect_fill":
            x0, y0 = _norm_to_px(width, height, action.get("x", 0.0), action.get("y", 0.0))
            x1, y1 = _norm_to_px(
                width,
                height,
                float(action.get("x", 0.0)) + float(action.get("w", 0.0)),
                float(action.get("y", 0.0)) + float(action.get("h", 0.0)),
            )
            left, right = sorted([x0, x1])
            top, bottom = sorted([y0, y1])
            if right <= left or bottom <= top:
                continue
            if alpha == 0:
                draw_erase.rectangle([left, top, right, bottom], fill=255)
            else:
                draw_overlay.rectangle([left, top, right, bottom], fill=color)
            continue
        if action_type == "free_draw":
            points = action.get("points") or []
            if not isinstance(points, list) or len(points) == 0:
                continue
            line_points = []
            for p in points:
                if not isinstance(p, dict):
                    continue
                line_points.append(_norm_to_px(width, height, p.get("x", 0.0), p.get("y", 0.0)))
            if len(line_points) < 1:
                continue
            if len(line_points) == 1:
                x, y = line_points[0]
                r = max(1, brush_size // 2)
                if alpha == 0:
                    draw_erase.ellipse([x - r, y - r, x + r, y + r], fill=255)
                else:
                    draw_overlay.ellipse([x - r, y - r, x + r, y + r], fill=color)
                continue
            if alpha == 0:
                draw_erase.line(line_points, fill=255, width=brush_size, joint="curve")
            else:
                draw_overlay.line(line_points, fill=color, width=brush_size, joint="curve")
    merged = Image.alpha_composite(base, overlay)
    erase_arr = np.array(erase_mask, dtype=np.uint8)
    if np.any(erase_arr > 0):
        arr = np.array(merged, dtype=np.uint8)
        arr[:, :, 3][erase_arr > 0] = 0
        merged = Image.fromarray(arr, mode="RGBA")
    return merged


def render_pdf_page_preview(
    pdf_bytes: bytes,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    zoom: float = 1.5,
    region: Region | None = None,
    page_number: int = 1,
    color_metric: str = "deltae",
    preset: str = "none",
    enable_preprocess: bool = True,
    enable_postprocess: bool = True,
    saturation_min: int = 0,
    lightness_min: int = 0,
    lightness_max: int = 255,
    min_component_area: int = 0,
    paint_actions: list[dict] | None = None,
) -> tuple[bytes, int, int]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError("PDF has no pages")
        safe_page = max(1, min(total_pages, int(page_number)))
        page = doc[safe_page - 1]
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
            color_metric=color_metric,
            preset=preset,
            enable_preprocess=enable_preprocess,
            enable_postprocess=enable_postprocess,
            saturation_min=saturation_min,
            lightness_min=lightness_min,
            lightness_max=lightness_max,
            min_component_area=min_component_area,
        )
        processed = apply_paint_actions(processed, paint_actions, page_number=safe_page)
        return _pil_to_png_bytes(processed), total_pages, safe_page
    finally:
        doc.close()


def process_pdf_bytes(
    pdf_bytes: bytes,
    mode: str,
    target_colors: Sequence[Color],
    tolerance: int,
    transparent_output: bool,
    replacement_color: Color | None = None,
    zoom: float = 2.0,
    region: Region | None = None,
    color_metric: str = "deltae",
    preset: str = "none",
    enable_preprocess: bool = True,
    enable_postprocess: bool = True,
    saturation_min: int = 0,
    lightness_min: int = 0,
    lightness_max: int = 255,
    min_component_area: int = 0,
    paint_actions: list[dict] | None = None,
) -> bytes:
    source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        for page in source_doc:
            current_page = page.number + 1
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
                color_metric=color_metric,
                preset=preset,
                enable_preprocess=enable_preprocess,
                enable_postprocess=enable_postprocess,
                saturation_min=saturation_min,
                lightness_min=lightness_min,
                lightness_max=lightness_max,
                min_component_area=min_component_area,
            )
            processed = apply_paint_actions(processed, paint_actions, page_number=current_page)
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
    color_metric: str = "deltae",
    preset: str = "none",
    enable_preprocess: bool = True,
    enable_postprocess: bool = True,
    saturation_min: int = 0,
    lightness_min: int = 0,
    lightness_max: int = 255,
    min_component_area: int = 0,
    paint_actions: list[dict] | None = None,
) -> bytes:
    png_bytes, _, _ = render_pdf_page_preview(
        pdf_bytes=pdf_bytes,
        mode=mode,
        target_colors=target_colors,
        tolerance=tolerance,
        transparent_output=transparent_output,
        replacement_color=replacement_color,
        zoom=zoom,
        region=region,
        page_number=1,
        color_metric=color_metric,
        preset=preset,
        enable_preprocess=enable_preprocess,
        enable_postprocess=enable_postprocess,
        saturation_min=saturation_min,
        lightness_min=lightness_min,
        lightness_max=lightness_max,
        min_component_area=min_component_area,
    )
    return png_bytes


def suggest_tolerance(
    image: Image.Image,
    target_colors: Sequence[Color],
    region: Region | None = None,
    *,
    color_metric: str = "deltae",
    preset: str = "none",
    enable_preprocess: bool = True,
    saturation_min: int = 0,
    lightness_min: int = 0,
    lightness_max: int = 255,
) -> dict:
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]

    (
        enable_preprocess,
        _,
        saturation_min,
        lightness_min,
        lightness_max,
        _,
    ) = _apply_preset_options(
        preset,
        enable_preprocess=enable_preprocess,
        enable_postprocess=False,
        saturation_min=saturation_min,
        lightness_min=lightness_min,
        lightness_max=lightness_max,
        min_component_area=0,
    )

    rgb_for_mask = _preprocess_rgb(rgb, enable_preprocess)
    _, min_dist = _mask_for_colors(
        rgb_for_mask,
        target_colors,
        tolerance=9999,
        color_metric=color_metric,
        saturation_min=0,
        lightness_min=0,
        lightness_max=255,
    )
    region_mask = _build_region_mask(rgb.shape[0], rgb.shape[1], region)

    sat_map = _rgb_to_hsv_saturation(rgb_for_mask)
    lstar_map = _rgb_to_lab(rgb_for_mask)[:, :, 0] * 2.55
    quality_mask = (
        (sat_map >= float(saturation_min))
        & (lstar_map >= float(lightness_min))
        & (lstar_map <= float(lightness_max))
    )
    if region_mask is not None:
        quality_mask &= region_mask

    candidates = min_dist[quality_mask]
    if candidates.size < 200:
        fallback = min_dist[region_mask] if region_mask is not None else min_dist.ravel()
        candidates = fallback

    if candidates.size < 50:
        raise ValueError("Not enough pixels for auto-tuning. Adjust region or colors.")

    p5, p10, p20 = np.percentile(candidates, [5, 10, 20])
    suggested_tolerance = int(round(np.clip(p10 + (p20 - p10) * 0.8, 3, 120)))

    near_mask = min_dist <= p20
    if region_mask is not None:
        near_mask &= region_mask
    near_sat = sat_map[near_mask]
    suggested_sat = int(round(np.clip(np.percentile(near_sat, 25) if near_sat.size else 20, 0, 255)))

    return {
        "suggested_tolerance": suggested_tolerance,
        "suggested_saturation_min": suggested_sat,
        "debug": {
            "samples": int(candidates.size),
            "p5": float(p5),
            "p10": float(p10),
            "p20": float(p20),
        },
    }





