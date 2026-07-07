from datetime import date

from yama.matcher import MountainDB
from yama.planner import candidate_dates, parse_nights


def test_parse_nights():
    db = MountainDB.load()
    assert parse_nights(db.find("燕岳")) == 1       # 1泊2日
    assert parse_nights(db.find("奥穂高岳")) == 2   # 2泊3日
    assert parse_nights(db.find("木曽駒ヶ岳")) == 0  # 夜行日帰り


def test_candidate_dates_weekend():
    tue = date(2026, 7, 7)
    days = candidate_dates("weekend", tue)
    assert days[0].weekday() == 5 and days[0] == date(2026, 7, 11)
    sat = date(2026, 7, 11)
    assert candidate_dates("weekend", sat)[0] == date(2026, 7, 18)  # 週六當天→下週末


def test_candidate_dates_explicit():
    assert candidate_dates("2026-08-01", date(2026, 7, 7)) == [date(2026, 8, 1)]
    assert candidate_dates("best", date(2026, 7, 7), horizon=5) == [
        date(2026, 7, 8 + i) for i in range(5)
    ]
