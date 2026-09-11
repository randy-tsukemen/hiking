"""Booking clocks must not depend on the user's operating-system timezone."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from yama import book, japan_time, yamatan


def freeze_local_clock(monkeypatch, local):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return local.astimezone(tz) if tz else local.replace(tzinfo=None)

    monkeypatch.setattr(japan_time, "datetime", Clock)


@pytest.mark.parametrize("offset", [8, 0, -7])
def test_opening_boundary_independent_of_local_timezone(monkeypatch, offset):
    opening = datetime(2026, 9, 17, 8, tzinfo=japan_time.JST)
    day = yamatan.HutDay(
        day=date(2026, 10, 17), holiday=False,
        rooms=[yamatan.RoomDay("相部屋", 2, 0, stock=2)],
        opens_at=yamatan.BookingWindow("months", 1, time(8)).opens_at(
            date(2026, 10, 17)),
    )
    local_zone = timezone(timedelta(hours=offset))
    freeze_local_clock(monkeypatch, (opening - timedelta(seconds=1)).astimezone(local_zone))
    assert day.not_yet_open
    assert day.fits(2) == []
    assert day.status == "未開賣（09/17 08:00 開賣）"
    freeze_local_clock(monkeypatch, opening.astimezone(local_zone))
    assert not day.not_yet_open
    assert day.fits(2)
    day.deadline = day.opens_at
    assert day.past_deadline


def test_taiwan_evening_uses_next_japanese_date(monkeypatch):
    freeze_local_clock(monkeypatch, datetime(
        2026, 9, 17, 23, 30, tzinfo=timezone(timedelta(hours=8))))
    assert japan_time.japan_now() == datetime(2026, 9, 18, 0, 30)


def test_book_waits_until_japanese_opening(monkeypatch):
    # Taiwan 06:59:50 is ten seconds before Japan's 08:00 opening.
    local = datetime(2026, 9, 17, 6, 59, 50,
                     tzinfo=timezone(timedelta(hours=8)))
    freeze_local_clock(monkeypatch, local)
    stay = date(2026, 10, 17)
    day = yamatan.HutDay(stay, False, [], opens_at=datetime(2026, 9, 17, 8))
    monkeypatch.setattr(yamatan, "get_month_availability", lambda *a: [day])
    slot = object()
    monkeypatch.setattr(book, "_find_slot", lambda *a: slot)

    class Page:
        def reload(self, **kwargs):
            freeze_local_clock(monkeypatch, local + timedelta(seconds=10))

        def wait_for_selector(self, *args, **kwargs):
            pass

        def wait_for_timeout(self, *args):
            pass

    assert book._wait_for_open(Page(), "hut", stay, None, 2, lambda *a: None) is slot


def test_book_too_early_message_uses_portable_format(monkeypatch):
    freeze_local_clock(monkeypatch, datetime(2026, 9, 16, 8, tzinfo=japan_time.JST))
    stay = date(2026, 10, 17)
    day = yamatan.HutDay(stay, False, [], opens_at=datetime(2026, 9, 17, 8))
    monkeypatch.setattr(yamatan, "get_month_availability", lambda *a: [day])
    with pytest.raises(book.BookError, match="09/17 08:00"):
        book._wait_for_open(None, "hut", stay, None, 2, lambda *a: None)
