from datetime import date

from yama.matcher import MountainDB
from yama.planner import parse_nights


def test_parse_nights():
    db = MountainDB.load()
    assert parse_nights(db.find("燕岳")) == 1       # 1泊2日
    assert parse_nights(db.find("奥穂高岳")) == 2   # 2泊3日
    assert parse_nights(db.find("木曽駒ヶ岳")) == 0  # 夜行日帰り

