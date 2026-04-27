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


def test_parse_expiry_date_accepts_heisei_text():
    module = load_car_inspe_module()

    parsed, error = module.parse_expiry_date("有効期間の満了する日 平成31年4月30日")

    assert error is None
    assert parsed == "20190430"


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


def test_best_candidate_normalizes_first_registration_month():
    module = load_car_inspe_module()

    value, raw, score = module._best_candidate(["初度検査年月 R5 年 4 月", "検査年月"], "first_registration_month")

    assert value == "2023年4月"
    assert raw == "初度検査年月 R5 年 4 月"
    assert score > 0


def test_first_registration_month_supports_heisei_and_rejects_noise():
    module = load_car_inspe_module()

    assert module.normalize_first_registration_month("初度検査年月 平成 30 年 11 月") == "2018年11月"
    assert module.normalize_first_registration_month("平成31年4月") == "2019年4月"
    assert module.normalize_first_registration_month("2023年4月") == "2023年4月"
    assert module.normalize_first_registration_month("SR424") == ""
    assert module.normalize_first_registration_month("令和42年4月") == ""


def test_best_candidate_normalizes_capacity_and_displacement_units():
    module = load_car_inspe_module()

    capacity, _raw_capacity, capacity_score = module._best_candidate(["5 入", "定員"], "passenger_capacity")
    displacement, _raw_displacement, displacement_score = module._best_candidate(["1.50 l", "排気量"], "displacement")

    assert capacity == "5人"
    assert displacement == "1.5L"
    assert capacity_score > 0
    assert displacement_score > 0


def test_displacement_falls_back_to_liter_for_decimal_without_unit():
    module = load_car_inspe_module()

    value, _raw, score = module._best_candidate(["0.65"], "displacement")

    assert value == "0.65L"
    assert score > 0


def test_displacement_rounds_decimal_to_two_places():
    module = load_car_inspe_module()

    value, _raw, score = module._best_candidate(["0.658L"], "displacement")

    assert value == "0.66L"
    assert score > 0


def test_capacity_handles_label_and_common_ocr_digit_confusion():
    module = load_car_inspe_module()

    value, _raw, score = module._best_candidate(["乗車定員 S 入"], "passenger_capacity")

    assert value == "5人"
    assert score > 0


def test_displacement_accepts_cc_and_missing_decimal_candidates():
    module = load_car_inspe_module()

    assert module.normalize_displacement("総排気量 1,500cc") == "1.5L"
    assert module.normalize_displacement("0.65L") == "0.65L"
    assert module.normalize_displacement("2L") == "2L"
    assert module.normalize_displacement("065L") == "65L"
    assert module.normalize_displacement("65L") == "65L"
    assert module.normalize_displacement("65.00L") == "65L"
    assert module.normalize_displacement("15L") == "15L"
    assert module.normalize_displacement("660L") == "660L"


def test_ocr_preprocessing_restores_high_accuracy_variants():
    module = load_car_inspe_module()
    image = module.Image.new("RGB", (80, 28), "white")
    draw = module.ImageDraw.Draw(image)
    draw.text((4, 6), "0.65L", fill="black")

    reg_variants = module._prepare_region_variants(image, "reg_number")
    numeric_variants = module._prepare_region_variants(image, "displacement")
    retry_variants = module._prepare_region_variants(image, "displacement", retry=True)

    assert len(reg_variants) >= 80
    assert len(numeric_variants) >= 120
    assert len(retry_variants) >= 120
    assert numeric_variants[0].mode == "L"
    assert len({variant.size for variant in numeric_variants}) > 1
    assert len({variant.tobytes() for variant in numeric_variants[:40]}) == 40


def test_ocr_configs_follow_field_specific_guideline():
    module = load_car_inspe_module()

    assert "--psm 7" in module._ocr_configs_for_field("reg_number")[0]
    assert "--psm 6" in module._ocr_configs_for_field("reg_number", retry=True)[0]
    assert module._ocr_configs_for_field("expiry_date")[0] == "--oem 1 --psm 7"
    assert all("tessedit_char_whitelist" not in config for config in module._ocr_configs_for_field("expiry_date"))
    assert module._ocr_configs_for_field("first_registration_month")[0] == "--oem 1 --psm 7"
    assert all("tessedit_char_whitelist" not in config for config in module._ocr_configs_for_field("first_registration_month"))
    assert "--psm 8" in module._ocr_configs_for_field("passenger_capacity")[0]
    assert "--psm 10" in module._ocr_configs_for_field("passenger_capacity", retry=True)[0]
    assert "tessedit_char_whitelist=0123456789.LlKkWw" in module._ocr_configs_for_field("displacement")[0]
    assert module._ocr_lang_for_field("displacement") == "eng"
    assert module._ocr_lang_for_field("expiry_date") == "jpn+eng"


def test_ocr_retry_runs_only_when_primary_score_is_low():
    module = load_car_inspe_module()
    image = module.Image.new("RGB", (80, 28), "white")
    calls = []

    def fake_ocr(_region, field, configs, *, retry=False, **kwargs):
        calls.append((field, retry, configs[0], kwargs))
        return [] if not retry else ["20261201"]

    module._ocr_image_variants_tesseract = fake_ocr

    texts = module._ocr_image_variants(image, "expiry_date")

    assert texts == ["20261201"]
    assert any(call[1] is False for call in calls)
    assert any(call[1] is True for call in calls)


def test_ocr_retry_is_skipped_when_primary_score_is_high():
    module = load_car_inspe_module()
    image = module.Image.new("RGB", (80, 28), "white")
    calls = []

    def fake_ocr(_region, field, configs, *, retry=False, **kwargs):
        calls.append((field, retry, configs[0], kwargs))
        return ["20261201"]

    module._ocr_image_variants_tesseract = fake_ocr

    texts = module._ocr_image_variants(image, "expiry_date")

    assert texts == ["20261201"]
    assert len(calls) == 1
    assert calls[0][1] is False
    assert calls[0][3]["variant_limit"] == module._ocr_fast_variant_count("expiry_date")


def test_match_vehicle_uses_siteplus_contract_vehicle_number():
    module = load_car_inspe_module()
    entries = [
        {
            "vehicle_id": "01234001",
            "contract_code": "01234001",
            "vehicle_number": "1234",
            "registration": "",
            "suffix": "1234",
            "location": "本社",
        }
    ]

    match = module.match_vehicle("品川300あ1234", entries)

    assert match["status"] == "matched"
    assert match["best"]["vehicle_id"] == "01234001"
    assert match["best"]["vehicle_number"] == "1234"


def test_extract_suffix_digits_returns_blank_without_trailing_digits():
    module = load_car_inspe_module()

    assert module.extract_suffix_digits("") == ""
    assert module.extract_suffix_digits("ABC") == ""


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
