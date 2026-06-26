import importlib.util
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CameraScan, db


def load_module():
    module_path = ROOT / "app" / "tools" / "camera_scanner.py"
    spec = importlib.util.spec_from_file_location("camera_scanner_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _user(username="userA", is_admin=False):
    return SimpleNamespace(is_authenticated=True, username=username, name=username, is_admin=is_admin)


@pytest.fixture()
def client(tmp_path):
    module = load_module()
    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'cs.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # base.html が参照する共通コンテキスト（本番は create_app の context_processor が供給）。
    @app.context_processor
    def _ctx():
        return {
            "categorized_navigation": [],
            "app_navigation_items": [],
            "all_navigation_items": [],
            "current_user_is_admin": False,
            "current_user_id": "",
            "app_version": "test",
            "default_page_title": lambda: "DSTT",
        }

    # ストレージはテスト用 tmp に隔離（リポジトリの var/ を汚さない）。
    module.STORAGE_DIR = str(tmp_path / "storage")
    app.register_blueprint(module.camera_scanner_bp)
    module.current_user = _user()
    with app.app_context():
        db.create_all()
    app.cs_module = module
    return app, app.test_client()


def login(app, username="userA", is_admin=False):
    app.cs_module.current_user = _user(username=username, is_admin=is_admin)


def make_card_jpeg():
    """暗い背景に、傾けた明るいカード（ID-1比）を合成した検出しやすい画像。"""
    bg = np.full((1200, 1600, 3), 60, np.uint8)
    card = np.full((400, 640, 3), 240, np.uint8)
    cv2.putText(card, "LICENSE", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 160), 5)
    src = np.float32([[0, 0], [640, 0], [640, 400], [0, 400]])
    dst = np.float32([[300, 250], [1250, 180], [1300, 820], [360, 900]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    warp = cv2.warpPerspective(card, matrix, (1600, 1200))
    mask = cv2.warpPerspective(np.full((400, 640), 255, np.uint8), matrix, (1600, 1200))
    bg[mask > 0] = warp[mask > 0]
    ok, buf = cv2.imencode(".jpg", bg)
    assert ok
    return buf.tobytes()


def upload_side(http, scan_id, side, data=None):
    return http.post(
        f"/tools/camera_scanner/api/scans/{scan_id}/sides/{side}",
        data={"file": (io.BytesIO(data or make_card_jpeg()), f"{side}.jpg")},
        content_type="multipart/form-data",
    )


# --------------------------------------------------------------------------- #
# 基本フロー
# --------------------------------------------------------------------------- #
def test_create_scan_defaults_to_draft(client):
    app, http = client
    res = http.post("/tools/camera_scanner/api/scans", json={"title": "山田太郎"})
    assert res.status_code == 200
    scan = res.get_json()["scan"]
    assert scan["status"] == "draft"
    assert scan["title"] == "山田太郎"
    assert scan["preset_key"] == "jp_driver_license"
    assert scan["has_front"] is False and scan["has_back"] is False and scan["has_pdf"] is False


def test_create_rejects_unknown_preset(client):
    app, http = client
    res = http.post("/tools/camera_scanner/api/scans", json={"preset_key": "nope"})
    assert res.status_code == 400


def test_upload_side_auto_detects_and_crops(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]

    res = upload_side(http, scan_id, "front")
    assert res.status_code == 200
    body = res.get_json()
    assert body["detected"] is True
    assert body["quad"] and len(body["quad"]) == 4
    assert body["scan"]["has_front"] is True
    assert body["scan"]["front_detected"] is True

    # 補正後プレビュー（角丸のため PNG）と補正前オリジナル（JPEG）の両方が配信できる。
    prev = http.get(f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/preview")
    assert prev.status_code == 200 and prev.data[:4] == b"\x89PNG"  # PNG signature
    orig = http.get(f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/original")
    assert orig.status_code == 200 and orig.data[:2] == b"\xff\xd8"  # JPEG SOI


def test_keep_original_option_and_download(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={"keep_original": True}).get_json()["scan"]["id"]
    assert http.get(f"/tools/camera_scanner/api/scans/{scan_id}").get_json()["scan"]["keep_original"] is True

    upload_side(http, scan_id, "front")
    # 補正前の元画像をダウンロード（添付）保存できる。
    dl = http.get(f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/original?dl=1")
    assert dl.status_code == 200 and dl.data[:2] == b"\xff\xd8"
    assert "attachment" in (dl.headers.get("Content-Disposition", ""))

    # PUT で保存オプションを切り替えられる。
    http.put(f"/tools/camera_scanner/api/scans/{scan_id}", json={"keep_original": False})
    assert http.get(f"/tools/camera_scanner/api/scans/{scan_id}").get_json()["scan"]["keep_original"] is False


def test_upload_rejects_unknown_side_and_missing_file(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]

    assert upload_side(http, scan_id, "middle").status_code == 400
    no_file = http.post(
        f"/tools/camera_scanner/api/scans/{scan_id}/sides/front",
        data={}, content_type="multipart/form-data",
    )
    assert no_file.status_code == 400


def test_adjust_with_points_crops_from_original(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    upload_side(http, scan_id, "front")

    points = [
        {"x": 300 / 1600, "y": 250 / 1200},
        {"x": 1250 / 1600, "y": 180 / 1200},
        {"x": 1300 / 1600, "y": 820 / 1200},
        {"x": 360 / 1600, "y": 900 / 1200},
    ]
    res = http.post(
        f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/adjust",
        json={"points": points},
    )
    assert res.status_code == 200
    assert res.get_json()["scan"]["front_detected"] is True


def test_adjust_rotate_only(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    upload_side(http, scan_id, "front")
    res = http.post(
        f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/adjust",
        json={"rotate": 90},
    )
    assert res.status_code == 200


def test_adjust_requires_capture_first(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    res = http.post(
        f"/tools/camera_scanner/api/scans/{scan_id}/sides/front/adjust",
        json={"rotate": 90},
    )
    assert res.status_code == 400


def test_finalize_requires_both_sides(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    upload_side(http, scan_id, "front")

    early = http.post(f"/tools/camera_scanner/api/scans/{scan_id}/finalize")
    assert early.status_code == 400

    upload_side(http, scan_id, "back")
    done = http.post(f"/tools/camera_scanner/api/scans/{scan_id}/finalize")
    assert done.status_code == 200
    assert done.get_json()["scan"]["status"] == "completed"
    assert done.get_json()["scan"]["has_pdf"] is True

    pdf = http.get(f"/tools/camera_scanner/api/scans/{scan_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.data[:5] == b"%PDF-"


def test_recapture_after_finalize_resets_to_draft(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    upload_side(http, scan_id, "front")
    upload_side(http, scan_id, "back")
    http.post(f"/tools/camera_scanner/api/scans/{scan_id}/finalize")

    # 確定後に表面を撮り直すと下書きへ戻り、PDFは無効化される。
    res = upload_side(http, scan_id, "front")
    assert res.status_code == 200
    assert res.get_json()["scan"]["status"] == "draft"
    assert res.get_json()["scan"]["has_pdf"] is False
    assert http.get(f"/tools/camera_scanner/api/scans/{scan_id}/pdf").status_code == 404


def test_delete_removes_scan_and_files(client):
    app, http = client
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]
    upload_side(http, scan_id, "front")
    scan_dir = os.path.join(app.cs_module.STORAGE_DIR, str(scan_id))
    assert os.path.isdir(scan_dir)

    res = http.delete(f"/tools/camera_scanner/api/scans/{scan_id}")
    assert res.status_code == 200
    assert not os.path.exists(scan_dir)
    with app.app_context():
        assert db.session.get(CameraScan, scan_id) is None


# --------------------------------------------------------------------------- #
# 作成者スコープ
# --------------------------------------------------------------------------- #
def test_list_scoped_to_owner(client):
    app, http = client
    login(app, "userA")
    http.post("/tools/camera_scanner/api/scans", json={"title": "A's"})
    login(app, "userB")
    http.post("/tools/camera_scanner/api/scans", json={"title": "B's"})

    data = http.get("/tools/camera_scanner/api/scans").get_json()
    assert [s["title"] for s in data["scans"]] == ["B's"]
    assert data["count"] == 1


def test_admin_sees_all_scans(client):
    app, http = client
    login(app, "userA")
    http.post("/tools/camera_scanner/api/scans", json={"title": "A's"})
    login(app, "userB")
    http.post("/tools/camera_scanner/api/scans", json={"title": "B's"})

    login(app, "admin01", is_admin=True)
    data = http.get("/tools/camera_scanner/api/scans").get_json()
    assert {s["title"] for s in data["scans"]} == {"A's", "B's"}
    assert data["can_admin"] is True


def test_other_user_cannot_access_scan(client):
    app, http = client
    login(app, "userA")
    scan_id = http.post("/tools/camera_scanner/api/scans", json={}).get_json()["scan"]["id"]

    login(app, "userB")
    assert http.get(f"/tools/camera_scanner/api/scans/{scan_id}").status_code == 403
    assert http.delete(f"/tools/camera_scanner/api/scans/{scan_id}").status_code == 403
    assert upload_side(http, scan_id, "front").status_code == 403


def test_index_page_renders(client):
    app, http = client
    res = http.get("/tools/camera_scanner/")
    assert res.status_code == 200
    assert "カメラスキャナー".encode("utf-8") in res.data
