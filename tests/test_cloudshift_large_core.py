from openpyxl import load_workbook
from pypdf import PdfReader

from app.services.cloudshift_large import calculate_large_month, default_large_config, normalize_large_config
from app.tools.cloudshift_large_export import _baseline_changed, large_pdf_bytes, large_xlsx_bytes
from app.tools.shiftersync_format import normalize_large_entries_for_month


def _sample():
    config = default_large_config()
    config["members"] = [{"id": "m1", "display_name": "職員A", "active": True, "order": 10}]
    config["codes"].append({
        "key": "A", "label": "A", "category": "work", "active": True, "order": 1,
        "times": {key: {"start": "09:00", "end": "18:00"} for key in ("weekday", "saturday", "holiday")},
        "color": "#dbeafe",
    })
    config = normalize_large_config(config)
    month = {
        "year": 2026, "month": 7,
        "entries_per_day": {"1": [{"member_id": "m1", "value": "A", "employee_name": "職員A"}]},
        "meta_data": {"day_types": {}, "day_notes": {"1": "朝礼"}},
    }
    return config, month


def test_large_normalizer_deduplicates_and_discards_empty_cells():
    normalized = normalize_large_entries_for_month({
        "1": [
            {"member_id": "m1", "value": "A"},
            {"member_id": "m1", "value": "B", "comment": "後勝ち"},
            {"member_id": "m2", "value": ""},
            {"member_id": "m3", "value": "", "holiday_kind": "legal"},
        ]
    }, 2026, 7)
    assert [(row["member_id"], row["value"]) for row in normalized["1"]] == [("m1", "B"), ("m3", "")]
    assert normalized["1"][0]["comment"] == "後勝ち"


def test_large_config_recovers_legacy_substitute_column_type_from_id():
    config = default_large_config()
    config["members"] = [
        {
            "id": "mem_regular_1",
            "display_name": "Regular",
            "employee_number": "",
            "employee_name": "",
        },
        {
            "id": "sub_legacy_1",
            "display_name": "Substitute",
            "employee_number": "",
            "employee_name": "",
        },
    ]

    members = normalize_large_config(config)["members"]

    assert [member["column_type"] for member in members] == ["regular", "substitute"]


def test_default_leave_choices_and_external_assignment_metadata_are_user_facing():
    config = default_large_config()
    assert [code["label"] for code in config["codes"]] == [
        "法定休", "所定休", "振休", "有休", "希望休", "希望有休", "希望振休",
    ]
    normalized = normalize_large_entries_for_month({"1": [{
        "member_id": "m1",
        "assignments": [
            {"code_key": "A", "source_type": "local"},
            {"code_key": "E", "source_type": "scene", "source_project_id": "scene-2", "source_project_title": "別現場", "source_site_row_id": "22", "source_site_id": "site-2", "source_site_name": "第二現場", "option_key": "E", "second_option": "SUB", "custom_label": "応援"},
        ],
    }]}, 2026, 7)["1"][0]
    assert normalized["value"] == "A"
    assert [item["code_key"] for item in normalized["assignments"]] == ["A", "E"]
    assert normalized["assignments"][1] == {
        **normalized["assignments"][1],
        "source_type": "scene", "source_project_id": "scene-2",
        "source_project_title": "別現場", "source_site_row_id": "22",
        "source_site_id": "site-2", "source_site_name": "第二現場",
        "option_key": "E", "second_option": "SUB", "custom_label": "応援",
    }


def test_external_only_assignment_is_not_counted_as_empty_or_local_work():
    config, month = _sample()
    month["entries_per_day"] = {"1": [{
        "member_id": "m1", "value": "E",
        "assignments": [{"code_key": "E", "source_type": "scene", "source_site_name": "第二現場", "custom_label": "応援"}],
    }]}
    person = calculate_large_month(config, month, [])["people"][0]
    assert person["days"][0]["category"] == "external"
    assert person["totals"]["work_days"] == 0
    assert person["totals"]["leave_counts"]["empty"] == 30


def test_baseline_comparison_ignores_comment_only_changes():
    month = {"meta_data": {"baseline": {"entries_per_day": {"1": [
        {"member_id": "m1", "value": "A", "comment": "旧コメント"}
    ]}}}}
    assert not _baseline_changed("1", "m1", {"member_id": "m1", "value": "A", "comment": "新コメント"}, month)
    assert not _baseline_changed("2", "m1", {"member_id": "m1", "value": "", "comment": "コメントのみ"}, month)
    assert _baseline_changed("1", "m1", {"member_id": "m1", "value": "B", "comment": "旧コメント"}, month)


def test_large_exports_are_readable_and_contain_expected_sheets():
    config, month = _sample()
    month["entries_per_day"]["1"][0]["comment"] = "引継ぎあり"
    result = calculate_large_month(config, month, [])
    xlsx = large_xlsx_bytes("大規模テスト", config, month, result)
    workbook = load_workbook(xlsx, read_only=True)
    assert workbook.sheetnames == ["シフト表", "集計", "個人明細"]
    assert workbook["シフト表"]["D3"].value == "A"
    pdf = large_pdf_bytes("大規模テスト", config, month, result)
    reader = PdfReader(pdf)
    assert len(reader.pages) >= 1
    assert len(pdf.getvalue()) > 1000
    assert "引継ぎあり" in "".join(page.extract_text() or "" for page in reader.pages)
