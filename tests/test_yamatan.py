"""yamatan 受付窗口計算與未開賣標示（純邏輯，不打網路）。"""

from datetime import date, datetime, time, timedelta

from yama.yamatan import BookingWindow, HutDay, RoomDay, _months_before, _parse_window


def test_months_before_same_day():
    assert _months_before(date(2026, 10, 10), 1) == date(2026, 9, 10)
    assert _months_before(date(2026, 10, 5), 2) == date(2026, 8, 5)


def test_months_before_clamps_to_month_end():
    # 10/31 的 1 個月前沒有 9/31 → 取 9/30（寧早勿晚）
    assert _months_before(date(2026, 10, 31), 1) == date(2026, 9, 30)
    assert _months_before(date(2026, 3, 30), 1) == date(2026, 2, 28)


def test_months_before_crosses_year():
    assert _months_before(date(2026, 1, 15), 2) == date(2025, 11, 15)


def test_booking_window_opens_at():
    w = BookingWindow(unit="months", num=1, open_time=time(8, 0))
    assert w.opens_at(date(2026, 10, 10)) == datetime(2026, 9, 10, 8, 0)
    w = BookingWindow(unit="days", num=14, open_time=time(7, 0))
    assert w.opens_at(date(2026, 10, 10)) == datetime(2026, 9, 26, 7, 0)


def test_parse_window():
    ev = {"beforeReservationType": "months", "before_reservation_num": 1,
          "canReserveStartDateTime": "08:00:00"}
    w = _parse_window(ev)
    assert (w.unit, w.num, w.open_time) == ("months", 1, time(8, 0))
    assert _parse_window({}) is None
    assert _parse_window({"beforeReservationType": "always"}) is None


def _day(opens_delta_hours: int) -> HutDay:
    return HutDay(day=date(2026, 10, 10), holiday=False,
                  rooms=[RoomDay(room="2名様", capacity=10, booked=0)],
                  opens_at=datetime.now() + timedelta(hours=opens_delta_hours))


def test_not_yet_open_masks_placeholder_capacity():
    d = _day(opens_delta_hours=24)
    assert d.not_yet_open
    assert d.status.startswith("未開賣")


def test_open_day_shows_remaining():
    d = _day(opens_delta_hours=-24)
    assert not d.not_yet_open
    assert d.status == "残10"


def test_no_window_info_behaves_as_before():
    d = HutDay(day=date(2026, 10, 10), holiday=False,
               rooms=[RoomDay(room="2名様", capacity=10, booked=10)])
    assert not d.not_yet_open
    assert d.status == "満室"
