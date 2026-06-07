"""日本の祝日算出（app.tools.japan_holidays）の正当性テスト。"""

from datetime import date

from app.tools.japan_holidays import (
    JAPAN_HOLIDAYS,
    build_japan_holidays,
    holidays_for_year,
)


def _iso_set(year):
    return {d.isoformat() for d in holidays_for_year(year)}


def test_2025_matches_official_calendar():
    # 内閣府公表の2025年「国民の祝日」（振替休日・補正含む）と完全一致すること。
    expected = {
        "2025-01-01",  # 元日
        "2025-01-13",  # 成人の日（1月第2月曜）
        "2025-02-11",  # 建国記念の日
        "2025-02-23",  # 天皇誕生日（日曜）
        "2025-02-24",  # 振替休日
        "2025-03-20",  # 春分の日
        "2025-04-29",  # 昭和の日
        "2025-05-03",  # 憲法記念日
        "2025-05-04",  # みどりの日（日曜）
        "2025-05-05",  # こどもの日
        "2025-05-06",  # 振替休日
        "2025-07-21",  # 海の日（7月第3月曜）
        "2025-08-11",  # 山の日
        "2025-09-15",  # 敬老の日（9月第3月曜）
        "2025-09-23",  # 秋分の日
        "2025-10-13",  # スポーツの日（10月第2月曜）
        "2025-11-03",  # 文化の日
        "2025-11-23",  # 勤労感謝の日（日曜）
        "2025-11-24",  # 振替休日
    }
    assert _iso_set(2025) == expected


def test_emperor_birthday_present_every_year_since_2020():
    # 2020年以降、天皇誕生日 2/23 が毎年存在する（旧データでは欠落していた）。
    for year in range(2020, 2031):
        assert date(year, 2, 23).isoformat() in {d.isoformat() for d in holidays_for_year(year)}


def test_substitute_holiday_when_holiday_falls_on_sunday():
    # 2025-11-23（勤労感謝の日）は日曜のため 11-24 が振替休日になる。
    days = holidays_for_year(2025)
    assert date(2025, 11, 23) in days
    assert date(2025, 11, 24) in days
    assert date(2025, 11, 24).weekday() == 0  # 月曜


def test_citizens_holiday_silver_week_2026():
    # 2026年: 敬老の日(9/21)と秋分の日(9/23)に挟まれた 9/22 が国民の休日。
    days = holidays_for_year(2026)
    assert date(2026, 9, 21) in days
    assert date(2026, 9, 22) in days
    assert date(2026, 9, 23) in days


def test_happy_monday_dates_are_correct():
    # ハッピーマンデーが正しい第n月曜になっていること（旧データの誤りを防ぐ）。
    days2025 = holidays_for_year(2025)
    assert date(2025, 7, 21) in days2025   # 海の日（旧データは 7/20 と誤り）
    assert date(2025, 10, 13) in days2025  # スポーツの日（旧データは 10/10 と誤り）
    assert date(2025, 7, 20) not in days2025
    assert date(2025, 10, 10) not in days2025


def test_olympic_year_overrides_2020_2021():
    # 2020・2021年の五輪特例による移動。
    d2020 = holidays_for_year(2020)
    assert date(2020, 7, 23) in d2020   # 海の日
    assert date(2020, 7, 24) in d2020   # スポーツの日
    assert date(2020, 8, 10) in d2020   # 山の日
    d2021 = holidays_for_year(2021)
    assert date(2021, 7, 22) in d2021
    assert date(2021, 7, 23) in d2021
    assert date(2021, 8, 8) in d2021


def test_interface_is_sorted_iso_string_list():
    # 従来インターフェース（"YYYY-MM-DD" 文字列の昇順リスト）を維持していること。
    assert isinstance(JAPAN_HOLIDAYS, list)
    assert all(isinstance(s, str) and len(s) == 10 for s in JAPAN_HOLIDAYS)
    assert JAPAN_HOLIDAYS == sorted(JAPAN_HOLIDAYS)
    assert len(JAPAN_HOLIDAYS) == len(set(JAPAN_HOLIDAYS))  # 重複なし


def test_build_range_is_inclusive():
    holidays = build_japan_holidays(2025, 2025)
    assert all(s.startswith("2025-") for s in holidays)
    assert "2025-01-01" in holidays
