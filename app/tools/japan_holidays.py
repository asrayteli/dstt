"""日本の国民の祝日を算出するモジュール。

`JAPAN_HOLIDAYS` は "YYYY-MM-DD" 形式の文字列リストで、従来の固定リストと
同じインターフェースを保ちつつ、祝日法に基づいて正しく算出する。

考慮している規定:
- 固定日の祝日（元日・建国記念の日・天皇誕生日(2/23, 2020年〜)・昭和の日 等）
- ハッピーマンデー（成人の日・海の日・敬老の日・スポーツの日）
- 春分の日・秋分の日（天文計算の近似式: 1980〜2099年で有効）
- 振替休日（祝日が日曜の場合、その後の最初の平日を休日にする）
- 国民の休日（前日・翌日がともに祝日である平日を休日にする）
- 2020年・2021年の東京五輪に伴う祝日移動の特例

対象期間は HOLIDAY_START_YEAR〜HOLIDAY_END_YEAR。利用側は集合化して
日付の所属判定や表示に用いるため、期間を広げても副作用はない。
"""

from datetime import date, timedelta

HOLIDAY_START_YEAR = 2020
HOLIDAY_END_YEAR = 2040

# 五輪特例（祝日法の臨時措置）による移動分。{年: {名称: (月, 日)}}
_OLYMPIC_OVERRIDES = {
    2020: {"marine": (7, 23), "sports": (7, 24), "mountain": (8, 10)},
    2021: {"marine": (7, 22), "sports": (7, 23), "mountain": (8, 8)},
}


def _nth_monday(year: int, month: int, nth: int) -> date:
    """その月の第 nth 月曜日を返す。"""
    d = date(year, month, 1)
    offset = (7 - d.weekday()) % 7  # 月初から最初の月曜までの日数（月曜=0）
    return d + timedelta(days=offset + (nth - 1) * 7)


def _vernal_equinox_day(year: int) -> int:
    """春分の日（3月）。1980〜2099年で有効な近似式。"""
    return int(20.8431 + 0.242194 * (year - 1980) - (year - 1980) // 4)


def _autumnal_equinox_day(year: int) -> int:
    """秋分の日（9月）。1980〜2099年で有効な近似式。"""
    return int(23.2488 + 0.242194 * (year - 1980) - (year - 1980) // 4)


def _primary_holidays(year: int) -> set:
    """振替休日・国民の休日を除く、その年の「国民の祝日」本体を返す。"""
    override = _OLYMPIC_OVERRIDES.get(year, {})
    holidays = {
        date(year, 1, 1),                            # 元日
        _nth_monday(year, 1, 2),                     # 成人の日
        date(year, 2, 11),                           # 建国記念の日
        date(year, 2, 23),                           # 天皇誕生日（2020年〜）
        date(year, 3, _vernal_equinox_day(year)),    # 春分の日
        date(year, 4, 29),                           # 昭和の日
        date(year, 5, 3),                            # 憲法記念日
        date(year, 5, 4),                            # みどりの日
        date(year, 5, 5),                            # こどもの日
        date(year, 9, _autumnal_equinox_day(year)),  # 秋分の日
        date(year, 11, 3),                           # 文化の日
        date(year, 11, 23),                          # 勤労感謝の日
    }

    # ハッピーマンデー（五輪特例があればそちらを優先）
    if "marine" in override:
        holidays.add(date(year, *override["marine"]))
    else:
        holidays.add(_nth_monday(year, 7, 3))        # 海の日
    holidays.add(_nth_monday(year, 9, 3))            # 敬老の日
    if "sports" in override:
        holidays.add(date(year, *override["sports"]))
    else:
        holidays.add(_nth_monday(year, 10, 2))       # スポーツの日
    if "mountain" in override:
        holidays.add(date(year, *override["mountain"]))
    else:
        holidays.add(date(year, 8, 11))              # 山の日

    return holidays


def _citizens_holidays(primary: set) -> set:
    """国民の休日（前日・翌日がともに祝日の平日）を返す。"""
    extra = set()
    for d in primary:
        candidate = d + timedelta(days=1)
        if candidate in primary:
            continue
        if (candidate + timedelta(days=1)) in primary and candidate.weekday() != 6:
            extra.add(candidate)
    return extra


def _substitute_holidays(holidays: set) -> set:
    """振替休日（日曜の祝日に対し、後続の最初の非祝日）を返す。"""
    extra = set()
    for d in sorted(holidays):
        if d.weekday() != 6:  # 日曜以外は対象外
            continue
        nxt = d + timedelta(days=1)
        while nxt in holidays or nxt in extra:
            nxt += timedelta(days=1)
        extra.add(nxt)
    return extra


def holidays_for_year(year: int) -> set:
    """指定年の全祝日（本体＋国民の休日＋振替休日）を date 集合で返す。"""
    primary = _primary_holidays(year)
    all_holidays = set(primary)
    all_holidays |= _citizens_holidays(primary)
    all_holidays |= _substitute_holidays(all_holidays)
    return all_holidays


def build_japan_holidays(start_year: int = HOLIDAY_START_YEAR,
                         end_year: int = HOLIDAY_END_YEAR) -> list:
    """期間内の祝日を "YYYY-MM-DD" 文字列の昇順リストで返す。"""
    result = set()
    for year in range(start_year, end_year + 1):
        result |= holidays_for_year(year)
    return [d.isoformat() for d in sorted(result)]


# 従来と同じインターフェース（"YYYY-MM-DD" 文字列のリスト）
JAPAN_HOLIDAYS = build_japan_holidays()
