from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pluslist_template_exposes_dynamic_selection_summary():
    html = (ROOT / "app" / "templates" / "pluslist.html").read_text(encoding="utf-8")
    assert 'id="plus-selection-summary"' in html
    assert "buildSearchSelectionSummary" in html
    assert "updateSearchSelectionSummary" in html
