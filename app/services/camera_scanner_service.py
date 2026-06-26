"""カメラスキャナーの画像処理パイプライン。

スマートフォンで撮影した「ただの写真」を、プリンターでスキャンしたような
正面化・切り取り済みの書類画像へ補正し、最終的に表裏をまとめた1つのPDFを
生成する。肝となるのは以下の2点で、すべて CPU（OpenCV）だけで完結する。

1. 角度の自動補正＋切り取り
   グレースケール化 → エッジ抽出 → 最大の四角形（カード外形）検出 →
   射影変換（4点ワープ）で正面化し、運転免許証の規格比（ISO/IEC 7810 ID-1,
   85.6mm×54mm ≒ 1.585:1）へ正規化する。

2. スキャン風の仕上げ
   照明ムラの平準化（CLAHE）、簡易ホワイトバランス、軽いシャープ化で、
   色を保ったままスキャナー出力のような均一で締まった見た目にする。

自動検出が外れた場合に備え、利用者が四隅をドラッグして指定する手動補正
（``process_with_points``）と、90度単位の回転（``rotate_image``）も提供する。
"""
from __future__ import annotations

import io
import logging
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# 運転免許証（ID-1）の縦横比。出力はこの比率の横長へ正規化する。
ID1_RATIO = 85.6 / 54.0

# 射影変換後の出力サイズ（300dpi 相当の横長）。長辺/短辺で固定し、
# どのカードも同じ寸法で揃うようにする。
OUTPUT_LONG_EDGE = 1012   # 85.6mm @ 300dpi
OUTPUT_SHORT_EDGE = int(round(OUTPUT_LONG_EDGE / ID1_RATIO))  # ≒ 638

# 輪郭検出を行う際の作業解像度（長辺）。大きな写真はここまで縮小して高速化する。
DETECT_MAX_EDGE = 1600

# カード外形とみなすために必要な、画面に占める最小面積比。
MIN_QUAD_AREA_RATIO = 0.18

JPEG_QUALITY = 90

# ID-1（運転免許証）の角丸半径。長辺に対する比（3.18mm / 85.6mm）。
CORNER_RADIUS_RATIO = 3.18 / 85.6


class ScanError(ValueError):
    """画像のデコード・処理に失敗したときに送出する。"""


# --------------------------------------------------------------------------- #
# 入出力
# --------------------------------------------------------------------------- #
def decode_image(data: bytes) -> np.ndarray:
    """アップロードされた画像バイト列を BGR の ndarray へ変換する。

    スマートフォンの写真は EXIF に回転情報を持つことが多いため、PIL の
    ``exif_transpose`` で見た目どおりの向きへ正規化してから OpenCV へ渡す。
    """
    if not data:
        raise ScanError("画像データが空です。")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            rgb = img.convert("RGB")
            arr = np.asarray(rgb)
    except Exception as exc:  # noqa: BLE001 - 破損/非対応画像をまとめて扱う
        raise ScanError("画像を読み込めませんでした。") from exc
    if arr.size == 0:
        raise ScanError("画像を読み込めませんでした。")
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def encode_jpeg(bgr: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    """BGR 画像を JPEG バイト列へエンコードする。"""
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ScanError("画像のエンコードに失敗しました。")
    return buf.tobytes()


def encode_png(img: np.ndarray) -> bytes:
    """画像（BGR または 角丸の透明付き BGRA）を PNG バイト列へエンコードする。"""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ScanError("画像のエンコードに失敗しました。")
    return buf.tobytes()


def decode_unchanged(data: bytes) -> np.ndarray:
    """保存済み画像をアルファ込み（BGRA可）でそのまま読み込む（回転のやり直し用）。"""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ScanError("画像を読み込めませんでした。")
    return img


# --------------------------------------------------------------------------- #
# 四角形（カード外形）の検出
# --------------------------------------------------------------------------- #
def _order_points(pts: np.ndarray) -> np.ndarray:
    """4点を 左上・右上・右下・左下 の順へ並べ替える。

    座標の和が最小＝左上、最大＝右下。差(x-y)が最小＝右上、最大＝左下。
    撮影時の回転に関わらず一貫した巻き方向で返す。
    """
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    ordered[0] = pts[np.argmin(s)]  # 左上
    ordered[2] = pts[np.argmax(s)]  # 右下
    ordered[1] = pts[np.argmin(d)]  # 右上
    ordered[3] = pts[np.argmax(d)]  # 左下
    return ordered


def _largest_card_contour(work: np.ndarray):
    """カード外形とみられる最大の輪郭を返す。複数の前処理を併用して頑健にする。

    実写真では背景・影・低コントラストで Canny だけだと外形を取りこぼすことが
    あるため、Canny に加えて Otsu 二値化（明るいカードと背景の境界）も試し、
    妥当な面積の最大輪郭を選ぶ。
    """
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    work_area = float(work.shape[0] * work.shape[1])

    contours = []
    # 方法A: 中央値ベースの自動しきい値 Canny（エッジで外形を捉える）
    median = float(np.median(gray))
    lower = int(max(0, 0.66 * median))
    upper = int(min(255, 1.33 * median))
    edged = cv2.Canny(gray, lower, upper)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)
    edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    ca, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours.extend(ca)
    # 方法B: Otsu 二値化（明暗どちらの背景でも拾えるよう正負両方）
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    for binimg in (th, cv2.bitwise_not(th)):
        cb, _ = cv2.findContours(binimg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(cb)

    best = None
    best_area = 0.0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        # 小さすぎ（誤検出）・大きすぎ（画像全体の枠）は除外。
        if area < MIN_QUAD_AREA_RATIO * work_area or area > 0.985 * work_area:
            continue
        if area > best_area:
            best = cnt
            best_area = area
    return best


def _quad_from_contour(cnt: np.ndarray) -> np.ndarray:
    """輪郭から4隅を得る。まず4角形近似、ダメなら最小外接の回転矩形で代用する。

    回転矩形（``minAreaRect``）を使うことで、斜めに撮られたカードでも淵に沿った
    傾きを検出でき、射影変換で垂直・平行へ（自動回転して）正面化できる。
    """
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        return approx.reshape(4, 2).astype("float32")
    # 4角形に近似できない（背景が複雑・淵が一部欠ける等）ときは、淵に最もよく
    # 沿う回転矩形の4隅を使う。これによりカードの傾きを検出して水平化できる。
    box = cv2.boxPoints(cv2.minAreaRect(cnt))
    return box.astype("float32")


def _auto_detect_quad(bgr: np.ndarray) -> Optional[np.ndarray]:
    """画像中のカード外形を検出して4隅を返す。

    見つからなければ ``None``。座標は入力 ``bgr`` のピクセル座標系で返す。
    """
    h, w = bgr.shape[:2]
    scale = min(1.0, DETECT_MAX_EDGE / float(max(h, w)))
    work = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else bgr

    cnt = _largest_card_contour(work)
    if cnt is None:
        return None
    quad = _quad_from_contour(cnt)
    if scale < 1.0:
        quad = quad / scale
    return _order_points(quad)


# --------------------------------------------------------------------------- #
# 射影変換（正面化＋切り取り）と仕上げ
# --------------------------------------------------------------------------- #
def _warp_to_card(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """順序付き4点でカードを正面化し、ID-1 比の固定サイズへ整える。

    四角形の長辺を横に取り、運転免許証として自然な横長で出力する。元写真で
    カードが縦向きに写っていた場合も、長辺=横として正規化する。
    """
    tl, tr, br, bl = quad
    width = max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))
    height = max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))

    if width >= height:
        out_w, out_h = OUTPUT_LONG_EDGE, OUTPUT_SHORT_EDGE
        dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype="float32")
    else:
        # 写真上は縦長＝カードが90度寝ている。縦長の出力面へワープしてから横へ起こす。
        out_w, out_h = OUTPUT_SHORT_EDGE, OUTPUT_LONG_EDGE
        dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype="float32")

    matrix = cv2.getPerspectiveTransform(quad.astype("float32"), dst)
    warped = cv2.warpPerspective(bgr, matrix, (out_w, out_h))
    if width < height:
        warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
    return warped


def enhance_document(bgr: np.ndarray) -> np.ndarray:
    """色を保ったまま、スキャナー出力風に照明を平準化して締める。

    - 簡易ホワイトバランス（gray-world）で色かぶりを除く。
    - LAB の L チャンネルへ CLAHE をかけ、影や照明ムラを平準化。
    - 軽いアンシャープマスクで輪郭を立てる。
    """
    img = bgr.astype(np.float32)

    # gray-world ホワイトバランス
    means = img.reshape(-1, 3).mean(axis=0)
    gray_mean = float(means.mean())
    for c in range(3):
        if means[c] > 1e-6:
            img[:, :, c] *= gray_mean / means[c]
    img = np.clip(img, 0, 255).astype(np.uint8)

    # CLAHE で照明ムラを平準化
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    # アンシャープマスク（軽め）
    blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.0)
    img = cv2.addWeighted(img, 1.4, blur, -0.4, 0)
    return img


def _fine_deskew(bgr: np.ndarray, max_angle: float = 7.0) -> np.ndarray:
    """粗く正面化したカードの残存傾きを、直線検出でさらに厳格に補正する。

    画像内の支配的な直線（カードの縁・帯・文字行）を Hough 変換で拾い、軸からの
    ずれ角の中央値だけ逆回転して縁を厳密に水平・垂直へ揃える。傾きが大きすぎる
    （誤検出）/検出できない場合は何もしない。
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLines(edges, 1, np.pi / 180.0, 110)
    if lines is None:
        return bgr
    devs = []
    for rho_theta in lines[:80]:
        theta_deg = float(np.degrees(rho_theta[0][1]))
        # 90 で割った余りで、水平/垂直どちらの線も「軸からのずれ」に正規化（±45度）。
        a = theta_deg % 90.0
        dev = a if a <= 45.0 else a - 90.0
        if abs(dev) <= max_angle:
            devs.append(dev)
    if not devs:
        return bgr
    angle = float(np.median(devs))
    if abs(angle) < 0.1:
        return bgr
    h, w = bgr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(
        bgr, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _round_corners(bgr: np.ndarray) -> np.ndarray:
    """ID-1（運転免許証）の角丸に合わせ、四隅を透明にした BGRA を返す。"""
    if bgr.ndim == 3 and bgr.shape[2] == 4:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_BGRA2BGR)
    h, w = bgr.shape[:2]
    r = max(1, int(round(CORNER_RADIUS_RATIO * max(w, h))))
    alpha = np.zeros((h, w), np.uint8)
    cv2.rectangle(alpha, (r, 0), (w - r, h), 255, -1)
    cv2.rectangle(alpha, (0, r), (w, h - r), 255, -1)
    for cx, cy in ((r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)):
        cv2.circle(alpha, (cx, cy), r, 255, -1)
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def _finish_card(warped: np.ndarray) -> np.ndarray:
    """正面化済みカードを 厳格水平化 → 仕上げ → 角丸（透明）して BGRA で返す。"""
    leveled = _fine_deskew(warped)
    enhanced = enhance_document(leveled)
    return _round_corners(enhanced)


def rotate_image(bgr: np.ndarray, degrees: int) -> np.ndarray:
    """90度単位で回転する（時計回り）。0/90/180/270 以外は90度に丸める。"""
    deg = int(degrees) % 360
    if deg == 0:
        return bgr
    if deg == 90:
        return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(bgr, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)


# --------------------------------------------------------------------------- #
# 高レベル API（ルートから呼ぶ）
# --------------------------------------------------------------------------- #
def _quad_to_normalized(quad: Optional[np.ndarray], shape: Tuple[int, int]) -> Optional[List[dict]]:
    """ピクセル座標の四角形を、画像サイズに対する 0〜1 の割合へ変換する。"""
    if quad is None:
        return None
    h, w = shape[:2]
    if w <= 0 or h <= 0:
        return None
    return [{"x": float(x) / w, "y": float(y) / h} for (x, y) in quad]


def process_auto(data: bytes) -> dict:
    """自動でカード検出→正面化→仕上げを行う（1回のデコードで完結）。

    Returns dict:
        original  : EXIF補正済みの原画像 BGR（保存・手動補正のやり直し用）。
        processed : 処理済み画像 BGR。
        detected  : カード外形を自動検出できたか。
        quad      : 検出した四隅の正規化座標（0〜1, 手動補正の初期値）。失敗時 None。

    検出に失敗したときは全体を仕上げた画像と ``detected=False`` を返し、
    呼び出し側で手動補正へ誘導する。
    """
    bgr = decode_image(data)
    quad = _auto_detect_quad(bgr)
    if quad is None:
        # 検出失敗時は全体を仕上げただけ（角丸なし）を返し、手動補正へ誘導する。
        return {"original": bgr, "processed": enhance_document(bgr), "detected": False, "quad": None}
    warped = _warp_to_card(bgr, quad)
    return {
        "original": bgr,
        "processed": _finish_card(warped),  # 厳格水平化＋角丸（BGRA）
        "detected": True,
        "quad": _quad_to_normalized(quad, bgr.shape),
    }


def process_with_points(data: bytes, points: Sequence[dict]) -> np.ndarray:
    """利用者が指定した四隅（正規化座標 0〜1）でワープして仕上げる。

    ``points`` は ``[{"x":..,"y":..}, ...]`` の4要素。並びは任意でよく、内部で
    左上・右上・右下・左下へ並べ替える。
    """
    bgr = decode_image(data)
    h, w = bgr.shape[:2]
    if not points or len(points) != 4:
        raise ScanError("四隅の座標は4点必要です。")
    try:
        pts = np.array(
            [[float(p["x"]) * w, float(p["y"]) * h] for p in points],
            dtype="float32",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ScanError("四隅の座標が不正です。") from exc
    quad = _order_points(pts)
    warped = _warp_to_card(bgr, quad)
    return _finish_card(warped)  # 厳格水平化＋角丸（BGRA）


# --------------------------------------------------------------------------- #
# PDF 生成（表裏をまとめて1枚のPDFへ）
# --------------------------------------------------------------------------- #
def build_pdf(
    image_paths: Sequence[str],
    output_path: str,
    *,
    card_size_mm: Tuple[float, float] = (85.6, 54.0),
) -> str:
    """処理済み画像（表・裏…）を実寸サイズで A4 1ページに配置して保存する。

    免許証の実サイズ（既定 ID-1: 85.6mm×54mm）でA4へ、上から順（表→裏）に
    横中央寄せで積む。印刷すると実物と同じ大きさで出力される。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    paths = [p for p in image_paths if p]
    if not paths:
        raise ScanError("PDF化する画像がありません。")

    page_w, page_h = A4
    card_w = float(card_size_mm[0]) * mm
    card_h = float(card_size_mm[1]) * mm
    gap = 12 * mm           # 表裏の間隔
    top_margin = 25 * mm    # 上端からの余白

    c = canvas.Canvas(output_path, pagesize=A4)
    x = (page_w - card_w) / 2.0          # 横中央寄せ
    y_top = page_h - top_margin          # 上端から下へ積む
    for i, path in enumerate(paths):
        # 角丸PNGの透明部分を白へ合成（印刷時、角丸が紙の白に溶け込む）。
        with Image.open(path) as im:
            rgba = im.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            flat.alpha_composite(rgba)
            reader = ImageReader(flat.convert("RGB"))
        y = y_top - card_h - i * (card_h + gap)
        c.drawImage(
            reader, x, y, width=card_w, height=card_h,
            preserveAspectRatio=True, anchor="c",
        )
    c.showPage()
    c.save()
    return output_path
