from datetime import date, datetime

from app.services.datecalc_service import (
    add_years_months,
    calculate_date_count,
    calculate_datetime_delta,
    calculate_diff,
)


def test_add_years_months_respects_keep_month_end():
    base = datetime(2026, 1, 31, 9, 0)

    assert add_years_months(base, 0, 1, True) == datetime(2026, 2, 28, 9, 0)
    assert add_years_months(base, 0, 1, False) == datetime(2026, 2, 28, 9, 0)

    march_end = datetime(2026, 3, 31, 9, 0)
    assert add_years_months(march_end, 0, 1, True) == datetime(2026, 4, 30, 9, 0)


def test_add_years_months_can_skip_month_end_preservation():
    base = datetime(2026, 2, 28, 9, 0)

    assert add_years_months(base, 0, 1, True) == datetime(2026, 3, 31, 9, 0)
    assert add_years_months(base, 0, 1, False) == datetime(2026, 3, 28, 9, 0)


def test_datetime_delta_includes_days_hours_and_minutes():
    result = calculate_datetime_delta(
        datetime(2026, 5, 1, 9, 15),
        datetime(2026, 5, 3, 10, 45),
        absolute=True,
    )

    assert result["parts"] == "2日 1時間 30分"
    assert result["total_hours"] == 49.5
    assert result["total_minutes"] == 2970


def test_diff_reports_datetime_only():
    result = calculate_diff(
        datetime(2026, 5, 1, 9, 0),
        datetime(2026, 5, 5, 9, 0),
        absolute=True,
    )

    assert result.summary == "4日 0時間 0分"
    assert "総時間: 96.0時間" in result.details
    assert not any("営業日" in detail for detail in result.details)


def test_date_count_counts_weekdays_month_days_exact_dates_and_holidays_without_duplicates():
    result = calculate_date_count(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        weekdays={0},
        month_days={5},
        exact_dates={date(2026, 5, 1), date(2026, 6, 1)},
        include_japan_holidays=True,
    )

    # 2026-05-01〜05-07 の祝日は 5/3(憲法記念日・日曜)・5/4(みどりの日)・
    # 5/5(こどもの日)・5/6(振替休日) の4日。重複除外後は 5/1・5/3・5/4・5/5・5/6 の5日。
    assert result.summary == "5日"
    assert "曜日一致: 1日" in result.details
    assert "毎月の日付一致: 1日" in result.details
    assert "個別日付一致: 1日" in result.details
    assert "祝日一致: 4日" in result.details
    assert "重複除外後: 5日" in result.details


def test_date_count_swaps_reverse_range():
    result = calculate_date_count(
        start_date=date(2026, 5, 7),
        end_date=date(2026, 5, 1),
        weekdays={5},
        month_days=set(),
        exact_dates=set(),
        include_japan_holidays=False,
    )

    assert result.summary == "1日"
    assert "開始日と終了日を入れ替えて計算しました。" in result.details
