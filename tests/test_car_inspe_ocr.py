import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_car_inspe_module():
    if "pytesseract" not in sys.modules:
        pytesseract = types.ModuleType("pytesseract")
        pytesseract.image_to_string = lambda *_args, **_kwargs: ""
        pytesseract.pytesseract = types.SimpleNamespace(tesseract_cmd="")
        sys.modules["pytesseract"] = pytesseract

    module_path = ROOT / "app" / "tools" / "car_inspe.py"
    spec = importlib.util.spec_from_file_location("car_inspe_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_expiry_date_accepts_reiwa_and_fullwidth_digits():
    module = load_car_inspe_module()

    parsed, error = module.parse_expiry_date("有効期間の満了する日 令和７年５月３１日")

    assert error is None
    assert parsed == "20250531"


def test_parse_expiry_date_accepts_compact_yyyymmdd_candidate():
    module = load_car_inspe_module()

    parsed, error = module.parse_expiry_date("20261201")

    assert error is None
    assert parsed == "20261201"


def test_parse_expiry_date_rejects_invalid_calendar_date():
    module = load_car_inspe_module()

    parsed, error = module.parse_expiry_date("令和7年13月40日")

    assert parsed is None
    assert "日付として不正" in error


def test_clean_registration_number_normalizes_spaces_and_hyphens():
    module = load_car_inspe_module()

    cleaned = module.clean_registration_number("品川 300 あ 12-34")

    assert cleaned == "品川300あ1234"
    assert module.is_valid_reg_number(cleaned)
    assert module.extract_suffix_digits(cleaned) == "1234"


def test_best_candidate_prefers_valid_registration_shape():
    module = load_car_inspe_module()

    value, raw, score = module._best_candidate(["車両番号", "品川300あ1234"], "reg_number")

    assert value == "品川300あ1234"
    assert raw == "品川300あ1234"
    assert score > 0


def test_parse_vehicle_csv_detects_named_columns_and_matches_full_number():
    module = load_car_inspe_module()
    csv_bytes = "契約コード,登録番号,現場名\nA001,品川300あ1234,本社\n".encode("utf-8-sig")

    parsed = module.parse_vehicle_csv(csv_bytes)
    match = module.match_vehicle("品川 300 あ 12-34", parsed["entries"])

    assert parsed["has_header"] is True
    assert parsed["entries"][0]["vehicle_id"] == "A001"
    assert match["status"] == "matched"
    assert match["best"]["location"] == "本社"


def test_parse_vehicle_csv_keeps_legacy_three_column_format():
    module = load_car_inspe_module()
    csv_bytes = "A001,1234,本社\n".encode("cp932")

    parsed = module.parse_vehicle_csv(csv_bytes)

    assert parsed["has_header"] is False
    assert parsed["entries"][0]["suffix"] == "1234"
    assert parsed["entries"][0]["location"] == "本社"


def test_build_output_filename_uses_template_and_sanitizes_parts():
    module = load_car_inspe_module()
    row = {
        "expiry_date": "20250531",
        "vehicle_id": "A:001",
        "location": "本社/車庫",
        "registration_number": "品川300あ1234",
        "original_name": "source.pdf",
    }

    filename = module.build_output_filename(row, "{vehicle_id}_{location}_{registration}_{expiry}")

    assert filename == "A_001_本社_車庫_品川300あ1234_20250531.pdf"


def test_builtin_presets_are_reset_and_store_is_admin_source(tmp_path):
    module = load_car_inspe_module()
    module.PRESET_STORE_PATH = str(tmp_path / "presets.json")

    assert module.COORD_PRESETS == {}
    assert module.all_presets() == {}
    assert module.first_preset_name() == ""

    regions = {"reg_number": [10, 20, 100, 80], "expiry_date": [10, 90, 100, 140]}
    module.write_preset_store({"管理者プリセット": {"dpi": 300, "regions": regions}})

    assert module.first_preset_name() == "管理者プリセット"
    assert module.all_presets()["管理者プリセット"]["regions"] == regions


def test_build_output_filename_uses_business_fallbacks_for_missing_values():
    module = load_car_inspe_module()

    filename = module.build_output_filename({}, "{expiry}_{vehicle_id}_{location}_{registration}")

    assert filename == "満了日未確認_契約コード未確認_現場名未確認_登録番号未確認.pdf"


def test_parse_regions_payload_requires_both_valid_boxes():
    module = load_car_inspe_module()

    regions = module.parse_regions_payload({
        "reg_number": ["10", "20", "110", "90"],
        "expiry_date": [30.4, 40.2, 150.8, 95.6],
        "ignored": [0, 0, 1, 1],
    })

    assert regions == {
        "reg_number": [10, 20, 110, 90],
        "expiry_date": [30, 40, 150, 95],
    }
    assert module.parse_regions_payload({"reg_number": [10, 20, 5, 90]}) is None


def test_extract_with_preset_crops_exact_user_regions_without_expanding(tmp_path):
    module = load_car_inspe_module()
    image_path = tmp_path / "page.png"
    module.Image.new("RGB", (200, 160), "white").save(image_path)
    captured_sizes = []

    def fake_ocr(region, field):
        captured_sizes.append((field, region.size))
        return ["品川300あ1234"] if field == "reg_number" else ["20250531"]

    module._ocr_image_variants = fake_ocr
    regions = {
        "reg_number": [10, 20, 40, 50],
        "expiry_date": [50, 60, 90, 80],
    }

    result = module.extract_with_preset("unused.pdf", "", regions, image_path=str(image_path))

    assert captured_sizes == [("reg_number", (30, 30)), ("expiry_date", (40, 20))]
    assert result["_regions"] == regions
    assert result["reg_number"] == "品川300あ1234"
    assert result["expiry_date"] == "20250531"
