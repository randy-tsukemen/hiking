from datetime import date

import pytest

from yama import book


class Calendar:
    def __init__(self, labels):
        self.links = [Link(label) for label in labels]

    def locator(self, selector, has_text=None):
        if selector.startswith("td["):
            return self
        assert selector == "a.fc-event"
        result = Calendar([])
        result.links = [a for a in self.links if not has_text or has_text in a.label]
        return result

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def all(self):
        return self.links


class Link:
    def __init__(self, label):
        self.label = label

    def inner_text(self):
        return self.label


def test_four_guests_skip_one_and_two_person_rooms():
    page = Calendar(["○ 1名様 1名様利用", "○ 2名様", "○ 4名様 4名様利用"])
    assert book._find_slot(page, date(2026, 10, 23), None, 4) is page.links[2]


@pytest.mark.parametrize("status", ["×", "前", "休", "満"])
def test_no_fallback_to_smaller_room_when_four_person_room_unavailable(status):
    page = Calendar(["○ 1名様", f"{status} 4名様"])
    assert book._find_slot(page, date(2026, 10, 23), None, 4) is None


def test_explicit_room_still_checked_against_party():
    page = Calendar(["○ 14名様", "○ 4名様"])
    assert book._find_slot(page, date(2026, 10, 23), "4名様", 4) is page.links[1]


@pytest.mark.parametrize("label,party,expected", [
    ("○ 1名様", 1, True),
    ("○ 3〜4名様", 4, True),
    ("○ 3名～4名様", 2, False),
    ("○ 相部屋", 4, True),
])
def test_room_occupancy_labels(label, party, expected):
    assert book._room_fits_party(label, party) is expected


@pytest.mark.parametrize("kwargs", [
    {"men": 0, "women": 0},
    {"men": 2, "women": 2, "room": "1名様"},
])
def test_invalid_party_or_room_rejected_before_browser_launch(kwargs):
    with pytest.raises(book.BookError):
        book.book("karasawa", "涸沢ヒュッテ", date(2026, 10, 23), **kwargs)
