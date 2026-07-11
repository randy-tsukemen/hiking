"""yamatan 受付窗口與空位計算（純邏輯，不打網路）。

空位計算規則逆向自平台前端 bundle，並以官網日曆 DOM 比對驗證；
以合成 payload 鎖住行為。
"""

from datetime import date, datetime, time, timedelta

from yama.hunt import parse_weekday, target_dates
from yama.yamatan import (BookingWindow, HutDay, RoomDay, _compute_month,
                          _months_before, _parse_window)


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


def _hutday(opens_delta_hours: int) -> HutDay:
    return HutDay(day=date(2026, 10, 10), holiday=False,
                  rooms=[RoomDay(room="相部屋", capacity=10, booked=0, stock=10)],
                  opens_at=datetime.now() + timedelta(hours=opens_delta_hours))


def test_not_yet_open_masks_placeholder_capacity():
    d = _hutday(opens_delta_hours=24)
    assert d.not_yet_open
    assert d.status.startswith("未開賣")
    assert d.fits(1) == []


def test_open_day_shows_remaining():
    d = _hutday(opens_delta_hours=-24)
    assert not d.not_yet_open
    assert d.status == "相部屋残10"
    assert [r.room for r in d.fits(2)] == ["相部屋"]


def test_no_window_info_behaves_as_before():
    d = HutDay(day=date(2026, 10, 10), holiday=False,
               rooms=[RoomDay(room="相部屋", capacity=10, booked=10, stock=10)])
    assert not d.not_yet_open
    assert d.status == "満室"


# -- 月份計算（合成 payload） -------------------------------------------------

NOW = datetime(2026, 7, 11, 12, 0)


def _event(**over):
    ev = {
        "beforeReservationType": "months",
        "before_reservation_num": 1,
        "canReserveStartDateTime": "08:00:00",
        "rsvLimitBeforeDaysNum": 1,
        "rsvLimitBeforeTime": "15:00:00",
        "rooms": [
            {"id": "R2", "name": "2名様", "capacity": 2, "total": 8,
             "publish": True, "private_rooms": [{"id": "P2"}],
             "public_start_date": "2026-04-27", "public_end_date": "2026-11-03",
             "DateRsvAvailabilityControlToRoom": []},
            {"id": "DORM", "name": "大部屋【電話】", "capacity": 41, "total": 1,
             "publish": True, "private_rooms": [],
             "public_start_date": "2026-04-27", "public_end_date": "2026-11-03",
             "DateRsvAvailabilityControlToRoom": [
                 {"DateRsvAvailabilityControl":
                  {"date": "2026-08-05", "prohibitNewRsvForUser": True}}]},
        ],
        "private_rooms": [{"id": "P2", "room_id": "R2", "publish": True,
                           "min_guests": 2, "max_guests": 2}],
        "plans": [],  # DORM 無 plan → 電話受付
        "reservations": [
            # 單位制：3 筆有 private_room_id（各佔 1 室，人數無關）
            *[{"id": f"u{i}", "room_id": "R2", "private_room_id": "P2",
               "start_date": "2026-08-01", "end_date": "2026-08-02",
               "total_guest_num": 2} for i in range(3)],
            # private_room_id 為空的預約不佔個室庫存
            {"id": "x", "room_id": "R2", "private_room_id": None,
             "start_date": "2026-08-01", "end_date": "2026-08-02",
             "total_guest_num": 2},
            # 人數制：大部屋 30 人
            {"id": "d", "room_id": "DORM", "private_room_id": None,
             "start_date": "2026-08-01", "end_date": "2026-08-02",
             "total_guest_num": 30},
        ],
        "adjustments": [  # 人數制加法：8/1 大部屋 +10 人
            {"room_id": "DORM", "start_date": "2026-08-01",
             "end_date": "2026-08-01", "adjustment_num": 10}],
        "roomAdjustments": [  # 單位制加法：8/1 個室 +2 室
            {"room_id": "R2", "start_date": "2026-08-01",
             "end_date": "2026-08-01", "adjustment_num": 2}],
        "holidays": [{"holiday_date": "2026-08-03"}],
    }
    ev.update(over)
    return ev


def _day(days, d):
    return next(x for x in days if x.day == d)


def test_unit_room_counts_reservations_not_guests():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    r2 = next(r for r in _day(days, date(2026, 8, 1)).rooms
              if r.room == "2名様")
    # 8+2 室 − 3 筆（private_room_id 非空）＝残 7 室；人數與空 id 的預約不影響
    assert r2.unit_based and r2.stock == 10 and r2.booked == 3
    assert r2.remaining == 7 and r2.label == "残7室"


def test_dorm_uses_guests_and_additive_adjustment():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    dorm = next(r for r in _day(days, date(2026, 8, 1)).rooms
                if r.room.startswith("大部屋"))
    assert dorm.stock == 51 and dorm.booked == 30 and dorm.remaining == 21


def test_dorm_without_plan_is_phone_only():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    dorm = next(r for r in _day(days, date(2026, 8, 1)).rooms
                if r.room.startswith("大部屋"))
    assert dorm.phone_only and not dorm.fits(2)
    assert dorm.label.startswith("要電話")


def test_min_guests_blocks_solo_in_double_room():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    r2 = next(r for r in _day(days, date(2026, 8, 1)).rooms
              if r.room == "2名様")
    assert not r2.fits(1) and r2.fits(2) and not r2.fits(3)


def test_date_lock_prohibits_booking():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    dorm = next(r for r in _day(days, date(2026, 8, 5)).rooms
                if r.room.startswith("大部屋"))
    assert dorm.locked and dorm.label == "×鎖定" and not dorm.fits(1)


def test_holiday_field_name():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    assert _day(days, date(2026, 8, 3)).holiday


def test_not_yet_open_placeholder():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    d = _day(days, date(2026, 8, 20))  # 開賣日 7/20 08:00 > now 7/11
    assert d.not_yet_open and d.fits(2) == []
    assert "未開賣" in d.status


def test_past_deadline_closes_day():
    # 宿泊 7/12 的締切＝7/11 15:00；用 16:00 當作現在 → 受付結束
    late = datetime(2026, 7, 11, 16, 0)
    days = _compute_month(_event(), 2026, 7, _now=late)
    d = _day(days, date(2026, 7, 12))
    assert d.past_deadline and d.fits(2) == [] and d.status == "受付締切"


def test_day_fits_only_online_bookable():
    days = _compute_month(_event(), 2026, 8, _now=NOW)
    d = _day(days, date(2026, 8, 1))
    assert [r.room for r in d.fits(2)] == ["2名様"]  # 電話制大部屋不算


def test_hunt_target_dates_saturdays():
    today = date(2026, 7, 11)  # 週六
    ds = target_dates(parse_weekday("六"), today, horizon_days=21)
    assert ds == [date(2026, 7, 18), date(2026, 7, 25), date(2026, 8, 1)]
    assert all(d.weekday() == 5 for d in ds)


def test_hunt_acts_on_release_transition(monkeypatch):
    """第 1 輪満室 → 第 2 輪釋出：只在轉變時行動一次。"""
    import yama.hunt as H

    full = [RoomDay(room="2名様", capacity=2, booked=8, stock=8,
                    unit_based=True, min_guests=2)]
    freed = [RoomDay(room="2名様", capacity=2, booked=7, stock=8,
                     unit_based=True, min_guests=2)]
    stay = target_dates(5, date.today(), horizon_days=8)[0]
    rounds = iter([full, freed, freed])
    acted = []

    def fake_scan(slug, targets, echo):
        rooms = next(rounds)
        return {t: (HutDay(day=t, holiday=False, rooms=rooms)
                    if t == stay else None) for t in targets}

    def fake_act(slug, name, d, hits, auto_book, kw, echo):
        acted.append((d, tuple(hits)))

    def fake_sleep(sec):
        if len(fake_sleep.calls) >= 2:
            raise KeyboardInterrupt
        fake_sleep.calls.append(sec)
    fake_sleep.calls = []

    monkeypatch.setattr(H, "_scan", fake_scan)
    monkeypatch.setattr(H, "_act", fake_act)
    monkeypatch.setattr(H._time, "sleep", fake_sleep)
    try:
        H.hunt("slug", "テスト小屋", weekday=5, party=2,
               interval_min=30, auto_book=True, horizon_days=8,
               echo=lambda *a: None)
    except KeyboardInterrupt:
        pass
    # 第 2 輪釋出時行動一次；第 3 輪狀態未變不重複行動
    assert acted == [(stay, ("2名様：残1室",))]
