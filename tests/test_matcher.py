from yama.matcher import MountainDB, normalize


def test_normalize_ke_variants():
    assert normalize("木曽駒ヶ岳") == normalize("木曽駒ケ岳")
    assert normalize("燧ヶ岳") == normalize("燧ケ岳")


def test_normalize_width_and_case():
    assert normalize("Ｔｓｕｂａｋｕｒｏ") == "tsubakuro"


def test_find_exact_alias():
    db = MountainDB.load()
    assert db.find("燕岳").id == "tsubakuro"
    assert db.find("つばくろだけ").id == "tsubakuro"


def test_find_ke_variant():
    db = MountainDB.load()
    assert db.find("木曽駒ケ岳").id == "kisokoma"
    assert db.find("木曽駒ヶ岳").id == "kisokoma"


def test_find_substring():
    db = MountainDB.load()
    assert db.find("奥穂").id == "okuhotaka"
    assert db.find("涸沢").id == "okuhotaka"


def test_find_missing_returns_none():
    db = MountainDB.load()
    assert db.find("高尾山") is None
    assert db.find("") is None
