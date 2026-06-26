"""カメラスキャナー。

スマートフォン（iPhone等）で運転免許証などを撮影し、サーバー側で角度補正・
切り取り・スキャン風の仕上げを行って、表裏をまとめた1つのPDFを生成・保存する
ツール。撮影はスマートフォンからのみ行い、スキャン結果の一覧やPDFはPCからも
閲覧できる。

画像処理の本体は ``app.services.camera_scanner_service`` に分離している。
アクセスは作成者（ログインユーザー）単位で絞り、DSTT管理者は全件を参照できる。
画像・PDFの実体は webルート外（``var/camera_scanner/<scan_id>/``）に保存し、
``send_file`` をアクセス制御つきで配信する。
"""
from __future__ import annotations

import logging
import os
import shutil

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from app.access_control import is_admin_user
from app.models import CameraScan, db, jst_now
from app.services import camera_scanner_service as scanner

camera_scanner_bp = Blueprint("camera_scanner", __name__, url_prefix="/tools/camera_scanner")
logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(APP_ROOT, "..", "var", "camera_scanner")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 1枚あたり上限20MB

# --------------------------------------------------------------------------- #
# プリセット定義（将来 名刺・車検証・A4書類 などを追加できる構造）
# --------------------------------------------------------------------------- #
PRESETS: dict[str, dict] = {
    "jp_driver_license": {
        "key": "jp_driver_license",
        "label": "運転免許証",
        "description": "日本の自動車運転免許証。表面と裏面を別々に撮影し、1つのPDFにまとめます。",
        "sides": [
            {"key": "front", "label": "表面", "hint": "顔写真のある面"},
            {"key": "back", "label": "裏面", "hint": "裏面（記載事項・備考）"},
        ],
        "require_all_sides": True,
        "layout": "stacked_a4",
    },
}
DEFAULT_PRESET = "jp_driver_license"
SIDES = ("front", "back")


# --------------------------------------------------------------------------- #
# 起動時スキーマ用意（新テーブル camera_scans を作成）
# --------------------------------------------------------------------------- #
@camera_scanner_bp.before_app_request
def _ensure_camera_scanner_schema():
    if current_app.extensions.get("camera_scanner_schema_ready"):
        return
    try:
        db.create_all()
    except Exception:  # noqa: BLE001 - 起動時の保険。失敗しても他機能は動かす。
        logger.warning("camera_scans テーブルの作成に失敗しました。", exc_info=True)
    current_app.extensions["camera_scanner_schema_ready"] = True


# --------------------------------------------------------------------------- #
# 共通ヘルパー
# --------------------------------------------------------------------------- #
def _owner() -> str:
    return str(getattr(current_user, "username", "") or "")


def _is_admin() -> bool:
    return is_admin_user(current_user)


def _is_mobile() -> bool:
    """スマートフォンからのアクセスかどうか（撮影UIの出し分けに使う）。

    UAは詐称可能なので、これはセキュリティ境界ではなく表示上のガード。
    認証(@login_required)と作成者スコープが本来のアクセス制御。
    """
    ua = (request.headers.get("User-Agent") or "").lower()
    return any(k in ua for k in ("iphone", "ipad", "ipod", "android", "mobile"))


def _preset_of(scan: CameraScan) -> dict:
    return PRESETS.get(scan.preset_key) or PRESETS[DEFAULT_PRESET]


def _scoped_query():
    """作成者スコープを適用した CameraScan クエリ。管理者は全件。"""
    query = CameraScan.query
    if not _is_admin():
        query = query.filter(CameraScan.created_by == _owner())
    return query


def _get_scan(scan_id):
    """スキャンを取得しつつ作成者アクセス権を検証する。"""
    scan = db.session.get(CameraScan, scan_id)
    if scan is None:
        return None, (jsonify({"error": "対象のスキャンが見つかりません。"}), 404)
    if not _is_admin() and (scan.created_by or "") != _owner():
        return None, (jsonify({"error": "このスキャンへのアクセス権限がありません。"}), 403)
    return scan, None


def _scan_dir(scan_id, *, create: bool = False) -> str:
    path = os.path.join(STORAGE_DIR, str(scan_id))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _side_path(scan_id, filename) -> str | None:
    if not filename:
        return None
    return os.path.join(_scan_dir(scan_id), filename)


def _read_upload(upload):
    """アップロードを読み込み検証する。``(bytes, None)`` か ``(None, エラー文)``。"""
    if upload is None or not upload.filename:
        return None, "画像ファイルを選択してください。"
    data = upload.read()
    if not data:
        return None, "画像ファイルが空です。"
    if len(data) > MAX_UPLOAD_BYTES:
        return None, "画像サイズが大きすぎます（上限20MB）。"
    return data, None


def _reset_pdf_if_completed(scan: CameraScan) -> None:
    """確定後に面を撮り直したら、PDFを無効化して下書きへ戻す。"""
    if scan.status == CameraScan.STATUS_COMPLETED:
        old = _side_path(scan.id, scan.output_pdf)
        if old and os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                logger.warning("旧PDFの削除に失敗: %s", old, exc_info=True)
        scan.output_pdf = None
        scan.completed_at = None
        scan.status = CameraScan.STATUS_DRAFT


# --------------------------------------------------------------------------- #
# 画面
# --------------------------------------------------------------------------- #
@camera_scanner_bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template(
        "camera_scanner.html",
        is_mobile=_is_mobile(),
        presets=list(PRESETS.values()),
        default_preset=DEFAULT_PRESET,
        is_admin=_is_admin(),
    )


# --------------------------------------------------------------------------- #
# スキャン CRUD
# --------------------------------------------------------------------------- #
@camera_scanner_bp.route("/api/scans", methods=["GET"])
@login_required
def api_list_scans():
    scans = _scoped_query().order_by(CameraScan.created_at.desc()).all()
    return jsonify({
        "scans": [s.to_dict() for s in scans],
        "count": len(scans),
        "can_admin": _is_admin(),
        "is_mobile": _is_mobile(),
    })


@camera_scanner_bp.route("/api/scans", methods=["POST"])
@login_required
def api_create_scan():
    data = request.get_json(silent=True) or {}
    preset = str(data.get("preset_key", "") or DEFAULT_PRESET).strip()
    if preset not in PRESETS:
        return jsonify({"error": "不明なプリセットです。"}), 400
    title = str(data.get("title", "") or "").strip()[:120]
    scan = CameraScan(
        created_by=_owner(),
        preset_key=preset,
        title=title,
        status=CameraScan.STATUS_DRAFT,
    )
    db.session.add(scan)
    db.session.commit()
    return jsonify({"scan": scan.to_dict()})


@camera_scanner_bp.route("/api/scans/<int:scan_id>", methods=["GET"])
@login_required
def api_get_scan(scan_id):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    return jsonify({"scan": scan.to_dict()})


@camera_scanner_bp.route("/api/scans/<int:scan_id>", methods=["PUT"])
@login_required
def api_update_scan(scan_id):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    if "title" in data:
        scan.title = str(data.get("title", "") or "").strip()[:120]
    db.session.commit()
    return jsonify({"scan": scan.to_dict()})


@camera_scanner_bp.route("/api/scans/<int:scan_id>", methods=["DELETE"])
@login_required
def api_delete_scan(scan_id):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    db.session.delete(scan)
    db.session.commit()
    try:
        shutil.rmtree(_scan_dir(scan_id), ignore_errors=True)
    except OSError:
        logger.warning("スキャンフォルダの削除に失敗: %s", scan_id, exc_info=True)
    return jsonify({"success": True})


# --------------------------------------------------------------------------- #
# 面の撮影アップロード・補正
# --------------------------------------------------------------------------- #
def _valid_side(scan: CameraScan, side: str) -> bool:
    side_keys = {s["key"] for s in _preset_of(scan)["sides"]}
    return side in side_keys and side in SIDES


@camera_scanner_bp.route("/api/scans/<int:scan_id>/sides/<side>", methods=["POST"])
@login_required
def api_upload_side(scan_id, side):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    if not _valid_side(scan, side):
        return jsonify({"error": "不明な面です。"}), 400

    upload = request.files.get("file")
    data, upload_error = _read_upload(upload)
    if upload_error:
        return jsonify({"error": upload_error}), 400

    try:
        result = scanner.process_auto(data)
    except scanner.ScanError as exc:
        return jsonify({"error": str(exc)}), 400

    scan_dir = _scan_dir(scan_id, create=True)
    original_name = f"{side}_original.jpg"
    processed_name = f"{side}_processed.jpg"
    try:
        with open(os.path.join(scan_dir, original_name), "wb") as f:
            f.write(scanner.encode_jpeg(result["original"]))
        with open(os.path.join(scan_dir, processed_name), "wb") as f:
            f.write(scanner.encode_jpeg(result["processed"]))
    except OSError as exc:
        logger.error("スキャン画像の保存に失敗: %s", exc, exc_info=True)
        return jsonify({"error": "画像の保存に失敗しました。"}), 500

    setattr(scan, f"{side}_original", original_name)
    setattr(scan, f"{side}_processed", processed_name)
    setattr(scan, f"{side}_detected", bool(result["detected"]))
    _reset_pdf_if_completed(scan)
    db.session.commit()

    return jsonify({
        "scan": scan.to_dict(),
        "side": side,
        "detected": bool(result["detected"]),
        "quad": result["quad"],
    })


@camera_scanner_bp.route("/api/scans/<int:scan_id>/sides/<side>/adjust", methods=["POST"])
@login_required
def api_adjust_side(scan_id, side):
    """四隅の手動指定（points）または90度回転（rotate）でやり直す。"""
    scan, error = _get_scan(scan_id)
    if error:
        return error
    if not _valid_side(scan, side):
        return jsonify({"error": "不明な面です。"}), 400

    original_name = getattr(scan, f"{side}_original", None)
    original_path = _side_path(scan_id, original_name)
    if not original_path or not os.path.exists(original_path):
        return jsonify({"error": "先に撮影してください。"}), 400

    body = request.get_json(silent=True) or {}
    points = body.get("points")
    rotate = body.get("rotate")
    if points is None and rotate is None:
        return jsonify({"error": "補正内容（四隅または回転）が指定されていません。"}), 400

    try:
        if points is not None:
            with open(original_path, "rb") as f:
                original_data = f.read()
            image = scanner.process_with_points(original_data, points)
        else:
            processed_path = _side_path(scan_id, getattr(scan, f"{side}_processed", None))
            if not processed_path or not os.path.exists(processed_path):
                return jsonify({"error": "先に撮影してください。"}), 400
            with open(processed_path, "rb") as f:
                image = scanner.decode_image(f.read())
        if rotate is not None:
            image = scanner.rotate_image(image, int(rotate))
    except scanner.ScanError as exc:
        return jsonify({"error": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "補正の指定が不正です。"}), 400

    processed_name = f"{side}_processed.jpg"
    try:
        with open(os.path.join(_scan_dir(scan_id, create=True), processed_name), "wb") as f:
            f.write(scanner.encode_jpeg(image))
    except OSError as exc:
        logger.error("補正画像の保存に失敗: %s", exc, exc_info=True)
        return jsonify({"error": "画像の保存に失敗しました。"}), 500

    setattr(scan, f"{side}_processed", processed_name)
    if points is not None:
        # 利用者が四隅を確定したので「検出済み」とみなす。
        setattr(scan, f"{side}_detected", True)
    _reset_pdf_if_completed(scan)
    db.session.commit()
    return jsonify({"scan": scan.to_dict(), "side": side})


# --------------------------------------------------------------------------- #
# 画像配信（補正前=手動補正用 / 補正後=プレビュー）
# --------------------------------------------------------------------------- #
@camera_scanner_bp.route("/api/scans/<int:scan_id>/sides/<side>/original", methods=["GET"])
@login_required
def api_side_original(scan_id, side):
    return _send_side_image(scan_id, side, "original")


@camera_scanner_bp.route("/api/scans/<int:scan_id>/sides/<side>/preview", methods=["GET"])
@login_required
def api_side_preview(scan_id, side):
    return _send_side_image(scan_id, side, "processed")


def _send_side_image(scan_id, side, field):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    if side not in SIDES:
        return jsonify({"error": "不明な面です。"}), 400
    path = _side_path(scan_id, getattr(scan, f"{side}_{field}", None))
    if not path or not os.path.exists(path):
        return jsonify({"error": "画像が見つかりません。"}), 404
    response = send_file(path, mimetype="image/jpeg")
    # 補正をやり直すたびに最新を表示させる。
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- #
# 確定（PDF化）とダウンロード
# --------------------------------------------------------------------------- #
@camera_scanner_bp.route("/api/scans/<int:scan_id>/finalize", methods=["POST"])
@login_required
def api_finalize(scan_id):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    preset = _preset_of(scan)
    side_keys = [s["key"] for s in preset["sides"]]

    paths = []
    missing = []
    for side in side_keys:
        path = _side_path(scan_id, getattr(scan, f"{side}_processed", None))
        if path and os.path.exists(path):
            paths.append(path)
        else:
            missing.append(side)

    if preset.get("require_all_sides") and missing:
        labels = {s["key"]: s["label"] for s in preset["sides"]}
        names = "・".join(labels.get(m, m) for m in missing)
        return jsonify({"error": f"未撮影の面があります（{names}）。"}), 400
    if not paths:
        return jsonify({"error": "撮影された面がありません。"}), 400

    output_path = os.path.join(_scan_dir(scan_id, create=True), "output.pdf")
    try:
        scanner.build_pdf(paths, output_path, layout=preset.get("layout", "stacked_a4"))
    except scanner.ScanError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF生成に失敗: %s", exc, exc_info=True)
        return jsonify({"error": "PDFの生成に失敗しました。"}), 500

    scan.output_pdf = "output.pdf"
    scan.status = CameraScan.STATUS_COMPLETED
    scan.completed_at = jst_now()
    db.session.commit()
    return jsonify({"scan": scan.to_dict()})


@camera_scanner_bp.route("/api/scans/<int:scan_id>/pdf", methods=["GET"])
@login_required
def api_download_pdf(scan_id):
    scan, error = _get_scan(scan_id)
    if error:
        return error
    path = _side_path(scan_id, scan.output_pdf)
    if not path or not os.path.exists(path):
        return jsonify({"error": "PDFがまだ作成されていません。"}), 404
    safe_title = (scan.title or "scan").strip() or "scan"
    download_name = f"{safe_title}_{scan.id}.pdf"
    return send_file(path, as_attachment=True, download_name=download_name, mimetype="application/pdf")
