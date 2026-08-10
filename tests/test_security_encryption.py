"""追加されたセキュリティ機能（暗号化 / 権限 / 同一オリジン）のテスト。"""
from __future__ import annotations

import io
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import importlib.util
import pytest
from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_optional_deps():
    if "openpyxl" in sys.modules:
        return
    openpyxl = types.ModuleType("openpyxl")
    openpyxl.Workbook = object
    openpyxl.load_workbook = lambda *_a, **_k: object()
    styles = types.ModuleType("openpyxl.styles")
    for name in ("Font", "PatternFill", "Border", "Side", "Alignment"):
        setattr(styles, name, object)
    sys.modules["openpyxl"] = openpyxl
    sys.modules["openpyxl.styles"] = styles
    if "qrcode" not in sys.modules:
        qrcode = types.ModuleType("qrcode")
        qrcode.make = lambda *_a, **_k: SimpleNamespace(save=lambda *_a, **_k: None)
        sys.modules["qrcode"] = qrcode
    if "pytesseract" not in sys.modules:
        pytess = types.ModuleType("pytesseract")
        pytess.image_to_string = lambda *_a, **_k: ""
        sys.modules["pytesseract"] = pytess


@pytest.fixture(autouse=True)
def _reset_key_cache():
    from app.security import file_crypto
    file_crypto.reset_key_cache_for_tests()
    yield
    file_crypto.reset_key_cache_for_tests()


# ------------------------------------------------------------------
# file_crypto ラウンドトリップ
# ------------------------------------------------------------------

def test_file_crypto_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)
    from app.security import file_crypto

    plaintext = "social security style pii: 社員名 090-1234-5678".encode("utf-8")
    blob = file_crypto.encrypt_bytes(plaintext)
    assert blob.startswith(b"DSTT1\x00")
    assert plaintext not in blob
    assert file_crypto.decrypt_bytes(blob) == plaintext


def test_encrypt_json_file_does_not_contain_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)
    from app.security import file_crypto

    target = tmp_path / "session.enc"
    payload = {"employee_name": "山田太郎", "address": "東京都千代田区1-2-3"}
    file_crypto.encrypt_json_to_file(str(target), payload)

    data = target.read_bytes()
    assert b"\xe5\xb1\xb1\xe7\x94\xb0\xe5\xa4\xaa\xe9\x83\x8e" not in data  # 山田太郎
    assert b"employee_name" not in data

    loaded = file_crypto.decrypt_json_from_file(str(target))
    assert loaded == payload


def test_encrypt_reads_legacy_plaintext_json(tmp_path, monkeypatch):
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)
    from app.security import file_crypto

    target = tmp_path / "legacy.json"
    target.write_text('{"a": 1}', encoding="utf-8")
    assert file_crypto.decrypt_json_from_file(str(target)) == {"a": 1}


def test_encryption_key_missing_in_required_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.setenv("DSTT_REQUIRE_ENCRYPTION_KEY_ENV", "1")
    monkeypatch.chdir(tmp_path)
    from app.security import file_crypto
    file_crypto.reset_key_cache_for_tests()

    with pytest.raises(file_crypto.EncryptionKeyMissingError):
        file_crypto.get_data_encryption_key()


# ------------------------------------------------------------------
# カラム暗号化（EditHistory）
# ------------------------------------------------------------------

def test_edit_history_value_is_encrypted_at_rest(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app
    from app.models import EditHistory, db

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'crypt.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        rec = EditHistory(
            employee_number="E1",
            edited_by="tester",
            action="update",
            field_name="employee_name",
            old_value="山田 太郎",
            new_value="山田 次郎",
        )
        db.session.add(rec)
        db.session.commit()
        rid = rec.id

        # ORM 経由では復号された値が読める
        loaded = db.session.get(EditHistory, rid)
        assert loaded.old_value == "山田 太郎"
        assert loaded.new_value == "山田 次郎"

        # 生 SQL ではプリフィックスと Base64 な暗号文が保存されている
        raw = db.session.execute(
            db.text("SELECT old_value, new_value FROM edit_histories WHERE id=:id"),
            {"id": rid},
        ).fetchone()
        assert raw is not None
        assert raw[0].startswith("enc1:")
        assert raw[1].startswith("enc1:")
        assert "山田" not in raw[0]
        assert "山田" not in raw[1]


def test_edit_history_legacy_plaintext_still_readable(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app
    from app.models import EditHistory, db

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'legacy.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        # 旧来の平文形式を直接 INSERT
        db.session.execute(
            db.text(
                "INSERT INTO edit_histories (employee_number, edited_by, action, field_name, old_value, new_value)"
                " VALUES (:e, :u, :a, :f, :o, :n)"
            ),
            {"e": "E1", "u": "t", "a": "update", "f": "x", "o": "平文旧データ", "n": "plain legacy"},
        )
        db.session.commit()

        row = EditHistory.query.first()
        assert row.old_value == "平文旧データ"
        assert row.new_value == "plain legacy"


# ------------------------------------------------------------------
# startup guard
# ------------------------------------------------------------------

def test_startup_fails_when_key_required_and_missing(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.setenv("DSTT_REQUIRE_ENCRYPTION_KEY_ENV", "1")
    monkeypatch.chdir(tmp_path)

    from app import create_app
    from app.security import file_crypto
    file_crypto.reset_key_cache_for_tests()

    with pytest.raises(RuntimeError):
        create_app(
            {
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'noenv.db'}",
                "SECRET_KEY": "test",
                # TESTING=False を明示してガードを発火させる
            }
        )


# ------------------------------------------------------------------
# siteplus の営業所コード権限強化
# ------------------------------------------------------------------

def _load_siteplus_module():
    from app.models import db  # noqa: F401
    module_path = ROOT / "app" / "tools" / "siteplus.py"
    spec = importlib.util.spec_from_file_location("siteplus_test_enc_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _siteplus_client_with_user(tmp_path, monkeypatch, user_id=42):
    module = _load_siteplus_module()
    from app.models import db, AccessBranch, AccessOffice, UserAccessibleOffice

    app = Flask(
        __name__,
        root_path=str(ROOT / "app"),
        template_folder="templates",
        instance_path=str(tmp_path / "instance"),
    )
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.config["LOGIN_DISABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'siteplus_enc.db').as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    app.register_blueprint(module.siteplus_bp)

    with app.app_context():
        db.create_all()
        branch = AccessBranch(name="東京", code="T01")
        db.session.add(branch)
        db.session.flush()
        allowed = AccessOffice(branch_id=branch.id, name="新宿", code="S01")
        denied = AccessOffice(branch_id=branch.id, name="渋谷", code="S99")
        db.session.add_all([allowed, denied])
        db.session.flush()
        db.session.add(UserAccessibleOffice(user_id=user_id, office_id=allowed.id))
        db.session.commit()

    fake_user = SimpleNamespace(
        is_authenticated=True,
        username=f"user{user_id}",
        name="Tester",
        id=user_id,
        is_admin=False,
        branch_id=None,
        office_id=None,
        department_id=None,
    )
    # current_user を偽ユーザーに差し替える。monkeypatch を使い、テスト終了時に
    # 必ず元へ戻す（戻さないと app.access_control.current_user がセッション全体で
    # 偽ユーザーのまま残り、後続テストを汚染する）。
    monkeypatch.setattr(module, "current_user", fake_user, raising=False)
    from app import access_control as _ac
    monkeypatch.setattr(_ac, "current_user", fake_user, raising=False)
    return module, app, app.test_client()


def test_siteplus_import_rejects_forbidden_office_for_real_user(tmp_path, monkeypatch):
    _stub_optional_deps()
    module, app, client = _siteplus_client_with_user(tmp_path, monkeypatch)

    csv_text = "\n".join(
        [
            "契約コード,セグメント,担当者姓,担当者名,担当者ID,現場名,営業所コード",
            "01234001,一般,山田,太郎,9001,許可現場,S01",
            "02234001,一般,山田,太郎,9001,非許可現場,S99",
            "03234001,一般,山田,太郎,9001,コード無し,",
        ]
    )
    resp = client.post(
        "/tools/siteplus/api/import-site-table",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "t.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    summary = resp.get_json()["summary"]
    assert summary["processed_rows"] == 1
    assert summary["office_code_forbidden"] == 2
    forbidden_msgs = [e for e in summary["errors"] if "アクセス権限がありません" in e["message"] or "未設定の行" in e["message"]]
    assert len(forbidden_msgs) == 2


# ------------------------------------------------------------------
# pluslist: session JSON がディスクに平文で残らない
# ------------------------------------------------------------------

def test_pluslist_session_file_is_encrypted(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app.security import file_crypto
    from app.tools.pluslist import _encrypt_session_json, _session_path, _load_session_payload

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    session_key = "upload_session_tester_1.0"
    session_file = _session_path(str(uploads), session_key)
    payload = {
        "diff_result": {"to_add": [{"employee_name": "山田太郎", "address1": "東京都港区"}]},
        "filename": "a.xlsx",
        "office_codes": ["S01"],
    }
    _encrypt_session_json(session_file, payload)

    # ディスクには平文 PII が残らない
    blob = Path(session_file).read_bytes()
    assert blob.startswith(b"DSTT1\x00")
    assert "山田太郎".encode("utf-8") not in blob
    assert "東京都港区".encode("utf-8") not in blob

    # 読み戻しは正常
    _, loaded = _load_session_payload(str(uploads), session_key)
    assert loaded == payload


# ------------------------------------------------------------------
# Origin 同一オリジン検査
# ------------------------------------------------------------------

def test_same_origin_check_blocks_cross_origin_post(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'origin.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
            "DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS": True,
            "LOGIN_DISABLED": True,
        }
    )
    client = app.test_client()
    # Cross-origin POST
    resp = client.post(
        "/tools/pluslist/api/import",
        json={"session_key": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_fetch_metadata_blocks_cross_site_without_origin(tmp_path, monkeypatch):
    """Origin が除去されてもブラウザが cross-site と明示した要求は拒否する。"""
    _stub_optional_deps()
    monkeypatch.chdir(tmp_path)
    from app import create_app

    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "TESTING": True,
        "DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS": True,
    })
    response = app.test_client().post(
        "/auth/login",
        data={"username": "nobody", "password": "wrong"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_same_origin_check_allows_same_origin_post(tmp_path, monkeypatch):
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'origin2.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
            "DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS": True,
            "LOGIN_DISABLED": True,
        }
    )
    client = app.test_client()
    # 同一オリジン: Origin が localhost に一致すれば origin チェックは通る
    resp = client.post(
        "/tools/pluslist/api/import",
        json={"session_key": "missing"},
        headers={"Origin": "http://localhost"},
    )
    # origin チェックを通過し、セッション欠落（400）または認証系のレスポンスになる
    assert resp.status_code != 403 or "このツール" in (resp.get_json() or {}).get("error", "")


def test_same_origin_check_applies_to_non_tool_mutations(tmp_path, monkeypatch):
    """旧来は接頭辞限定だった保護を全体へ統一: ツール API 以外の POST も検査される。"""
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'origin3.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
            "DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS": True,
        }
    )
    client = app.test_client()

    # 旧プレフィックス外の認証フォーム POST もクロスオリジンならブロックされる
    blocked = client.post(
        "/auth/login",
        data={"username": "x", "password": "y"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert blocked.status_code == 403

    # 同一オリジンならブロックされない（403 以外）
    allowed = client.post(
        "/auth/login",
        data={"username": "x", "password": "y"},
        headers={"Origin": "http://localhost"},
    )
    assert allowed.status_code != 403


def test_same_origin_check_blocks_null_origin_post(tmp_path, monkeypatch):
    """sandboxed iframe 等が送る "Origin: null" は未設定扱いにせず拒否する。"""
    _stub_optional_deps()
    monkeypatch.delenv("DSTT_DATA_ENCRYPTION_KEY_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    from app import create_app

    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'origin4.db'}",
            "SECRET_KEY": "test",
            "TESTING": True,
            "DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS": True,
            "LOGIN_DISABLED": True,
        }
    )
    client = app.test_client()
    resp = client.post(
        "/tools/pluslist/api/import",
        json={"session_key": "x"},
        headers={"Origin": "null"},
    )
    assert resp.status_code == 403
