"""Yamatan（yamatan.net，山小屋預約平台）空位查詢 adapter。

山屋官網直訂是整個登山行程最難搶的資源。Yamatan 是多家山屋共用的
預約平台（tRPC API），`hutEvent.getEvent` 一次回傳該月的：
房型（容量、公開期間）、匿名化預約記錄、容量調整、休業日、日期鎖。

計算邏輯逆向自平台前端 bundle（genericCalculateCalenderEvent），
並以官網日曆 DOM 逐格比對驗證過。房型分兩制：
- 單位制（個室；rooms.private_rooms 非空）：庫存 = total ＋ roomAdjustments
  （加法），已訂 = 該日 private_room_id 非空的預約「筆數」——人數無關。
- 人數制（相部屋/大部屋）：容量 = capacity ＋ adjustments（加法），
  已訂 = Σ total_guest_num。
其他規則：
- 沒有任何有效 plan 的人數制房型不會出現在線上日曆（多半是電話受付，
  如涸沢「大部屋【電話】」）→ 標 phone_only，不算線上可訂。
- DateRsvAvailabilityControl.prohibitNewRsvForUser ＝該日鎖定新預約（×）。
- 受付開始**前**的日期空位數只是滿容量佔位（HutDay.not_yet_open / opens_at）；
  受付窗口在 beforeReservationType/before_reservation_num/canReserveStartDateTime。
- rsvLimitBefore*（直前締切）過了的日期整日消失＝不可線上預約。

僅讀取（與網頁瀏覽等價），不建立預約；輪詢請保持禮貌頻率。
"""

from __future__ import annotations

import calendar
import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import httpx

_BASE = "https://www.yamatan.net"
_UA = {"User-Agent": "yama-cli/0.1 (personal hiking planner)"}


class YamatanError(RuntimeError):
    pass


def _trpc(proc: str, payload: dict, timeout: float = 30.0) -> dict:
    inp = {"0": {"json": payload}}
    url = (f"{_BASE}/api/trpc/{proc}?batch=1&input="
           + urllib.parse.quote(json.dumps(inp)))
    r = httpx.get(url, headers=_UA, timeout=timeout)
    body = r.json()
    if r.status_code != 200 or "error" in body[0]:
        msg = body[0].get("error", {}).get("json", {}).get("message", r.text[:120])
        raise YamatanError(f"yamatan {proc}: {msg}")
    return body[0]["result"]["data"]["json"]


def _months_before(d: date, n: int) -> date:
    """d 的 n 個月前同日；該月無同日時取月底。

    Yamatan 對「同日不存在」的實際規則未知；取月底會比任何合理解讀早，
    寧可提早開始輪詢也不要把已開賣的日子誤標成未開賣。
    """
    y, m = d.year, d.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass
class BookingWindow:
    """受付窗口：宿泊日 num 個 unit（months/days）前的 open_time 起可訂。"""
    unit: str
    num: int
    open_time: time

    def opens_at(self, stay: date) -> datetime:
        """該宿泊日的受付開始時刻（日本時間；本模組假設本機時區為 JST）。"""
        if self.unit == "months":
            open_day = _months_before(stay, self.num)
        else:
            open_day = stay - timedelta(days=self.num)
        return datetime.combine(open_day, self.open_time)


def _parse_window(ev: dict) -> BookingWindow | None:
    unit = ev.get("beforeReservationType")
    num = ev.get("before_reservation_num")
    if unit not in ("months", "days") or not num:
        return None
    try:
        t = time.fromisoformat(ev.get("canReserveStartDateTime") or "00:00:00")
    except ValueError:
        t = time(0, 0)
    return BookingWindow(unit=unit, num=int(num), open_time=t)


def _parse_deadline(ev: dict) -> tuple[int, time] | None:
    """直前締切：宿泊日 N 天前的 T 時之後不可再訂（過了整日從日曆消失）。"""
    num = ev.get("rsvLimitBeforeDaysNum")
    if num is None:
        return None
    try:
        t = time.fromisoformat(ev.get("rsvLimitBeforeTime") or "23:59:59")
    except ValueError:
        t = time(23, 59, 59)
    return int(num), t


@dataclass
class RoomDay:
    room: str
    capacity: int          # 單位制：每室可住人數；人數制：基準容量（不含調整）
    booked: int            # 單位制：已訂室數；人數制：已訂人數
    stock: int = 0         # 可售量（含當日調整）：單位制＝室數、人數制＝人數
    unit_based: bool = False
    min_guests: int = 1    # 單位制個室的最少入住人數（2名様通常不收 1 人）
    locked: bool = False   # 該日禁止新預約（平台日期鎖）
    phone_only: bool = False  # 無線上 plan——不在線上日曆，通常需電話

    @property
    def remaining(self) -> int:
        """剩餘量：單位制＝剩餘室數、人數制＝剩餘人數。"""
        return max(self.stock - self.booked, 0)

    def fits(self, party: int) -> bool:
        """party 人能否在線上訂到本房型（單一筆預約）。"""
        if self.locked or self.phone_only or self.remaining <= 0:
            return False
        if self.unit_based:
            return self.min_guests <= party <= max(self.capacity, 1)
        return self.remaining >= party

    @property
    def label(self) -> str:
        if self.locked:
            return "×鎖定"
        n = self.remaining
        if self.phone_only:
            return f"要電話（残{n}）" if n > 0 else "要電話"
        if n <= 0:
            return "満"
        return f"残{n}室" if self.unit_based else f"残{n}"


@dataclass
class HutDay:
    day: date
    holiday: bool
    rooms: list[RoomDay]
    opens_at: datetime | None = None  # 受付開始時刻（None＝平台未提供窗口資訊）
    deadline: datetime | None = None  # 直前締切時刻（None＝無締切設定）
    _now: datetime | None = field(default=None, repr=False)  # 測試用時刻注入

    def _clock(self) -> datetime:
        return self._now or datetime.now()

    @property
    def remaining_total(self) -> int:
        """線上可訂的剩餘量合計（單位制算室、人數制算人；電話制/鎖定不計）。"""
        return sum(r.remaining for r in self.rooms
                   if not r.locked and not r.phone_only)

    @property
    def not_yet_open(self) -> bool:
        """尚未開賣：此時空位數只是滿容量佔位，不代表可訂。"""
        return bool(self.opens_at and self._clock() < self.opens_at)

    @property
    def past_deadline(self) -> bool:
        """已過直前締切：線上受付結束（日曆上整日消失）。"""
        return bool(self.deadline and self._clock() >= self.deadline)

    def fits(self, party: int) -> list[RoomDay]:
        """回傳 party 人現在就能線上訂的房型（未開賣/過締切一律空）。"""
        if self.holiday or self.not_yet_open or self.past_deadline:
            return []
        return [r for r in self.rooms if r.fits(party)]

    @property
    def status(self) -> str:
        if self.holiday:
            return "休業"
        if not self.rooms:
            return "非營業期間"
        if self.not_yet_open:
            return f"未開賣（{self.opens_at:%-m/%-d %H:%M} 開賣）"
        if self.past_deadline:
            return "受付締切"
        avail = [r for r in self.rooms
                 if not r.locked and not r.phone_only and r.remaining > 0]
        if avail:
            return "、".join(f"{r.room[:10]}{r.label}" for r in avail[:4])
        phones = [r for r in self.rooms if r.phone_only and r.remaining > 0]
        if phones:
            return "満室（" + "、".join(
                f"{r.room[:10]}{r.label}" for r in phones[:2]) + "）"
        return "満室"


def _sum_adjust(adjs: list[dict], ds: str) -> int:
    """該日的容量調整合計（加法；平台的 end_date 判定含當日）。"""
    return sum(a.get("adjustment_num") or 0 for a in adjs
               if a["start_date"] <= ds <= a["end_date"])


def get_month_availability(hut_slug: str, year: int, month: int,
                           *, _now: datetime | None = None) -> list[HutDay]:
    """計算某山屋某月逐日空位（與平台前端同一套邏輯）。"""
    ev = _trpc("hutEvent.getEvent",
               {"hutId": hut_slug, "year": str(year), "month": f"{month:02d}"})
    return _compute_month(ev, year, month, _now=_now)


def _compute_month(ev: dict, year: int, month: int,
                   *, _now: datetime | None = None) -> list[HutDay]:
    window = _parse_window(ev)
    deadline_rule = _parse_deadline(ev)
    rooms = [r for r in ev.get("rooms", []) if r.get("publish", True)]
    # 停更偵測：所有房型的公開期間都早於查詢年份 → 山屋已離開平台
    ends = [r.get("public_end_date") or "" for r in rooms]
    latest = max(ends) if ends else ""
    if latest and latest < f"{year:04d}-01-01":
        raise YamatanError(
            f"此山屋在 Yamatan 的資料已停止更新（房型公開期間最晚至 {latest}），"
            "請改用山屋官網或電話預約")

    holidays = set()
    for h in ev.get("holidays", []):
        d = h.get("holiday_date") or h.get("date") or h.get("start_date")
        if d:
            holidays.add(d[:10])

    # 房型分制：單位制＝top-level private_rooms 有公開的對應項
    unit_rooms = {p["room_id"] for p in ev.get("private_rooms", [])
                  if p.get("publish", True)}
    min_guests = {}
    for p in ev.get("private_rooms", []):
        rid = p["room_id"]
        min_guests[rid] = min(min_guests.get(rid, 99), int(p.get("min_guests") or 1))

    # 人數制房型需有有效 plan 才會上線上日曆；否則視為電話受付
    plans_by_room: dict[str, list[dict]] = {}
    for pl in ev.get("plans", []):
        plans_by_room.setdefault(pl.get("room_id") or "", []).append(pl)

    # 調整值（加法）：人數制吃 adjustments、單位制吃 roomAdjustments
    adj: dict[str, list[dict]] = {}
    for a in ev.get("adjustments", []):
        adj.setdefault(a["room_id"], []).append(a)
    radj: dict[str, list[dict]] = {}
    for a in ev.get("roomAdjustments", []):
        radj.setdefault(a["room_id"], []).append(a)

    # 日期鎖：room_id → {禁止新預約的日期}
    locks: dict[str, set[str]] = {}
    for r in ev.get("rooms", []):
        s = {c["DateRsvAvailabilityControl"]["date"][:10]
             for c in r.get("DateRsvAvailabilityControlToRoom", [])
             if c.get("DateRsvAvailabilityControl", {}).get("prohibitNewRsvForUser")}
        if s:
            locks[r["id"]] = s

    # 已訂量：單位制數「筆數」（private_room_id 非空）、人數制加總人數
    booked_units: dict[tuple[str, str], int] = {}
    booked_guests: dict[tuple[str, str], int] = {}
    seen_rsv = set()
    for rsv in ev.get("reservations", []):
        if rsv.get("id") in seen_rsv:
            continue
        seen_rsv.add(rsv.get("id"))
        d0 = date.fromisoformat(rsv["start_date"])
        d1 = date.fromisoformat(rsv["end_date"])
        cur = d0
        while cur < d1:
            key = (rsv["room_id"], cur.isoformat())
            if rsv.get("private_room_id"):
                booked_units[key] = booked_units.get(key, 0) + 1
            booked_guests[key] = (booked_guests.get(key, 0)
                                  + int(rsv.get("total_guest_num") or 0))
            cur = date.fromordinal(cur.toordinal() + 1)

    def has_active_plan(room_id: str, ds: str) -> bool:
        pls = plans_by_room.get(room_id, [])
        return any(not (p.get("archived_at") and str(p["archived_at"])[:10] <= ds)
                   for p in pls)

    out: list[HutDay] = []
    for day_n in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day_n)
        ds = d.isoformat()
        day_rooms: list[RoomDay] = []
        for r in rooms:
            ps, pe = r.get("public_start_date"), r.get("public_end_date")
            if (ps and ds < ps) or (pe and ds > pe):
                continue
            rid = r["id"]
            locked = ds in locks.get(rid, ())
            if rid in unit_rooms:
                stock = (r.get("total") or 1) + _sum_adjust(radj.get(rid, []), ds)
                day_rooms.append(RoomDay(
                    room=r.get("name", "?"), capacity=r.get("capacity") or 1,
                    booked=booked_units.get((rid, ds), 0), stock=stock,
                    unit_based=True, min_guests=min_guests.get(rid, 1),
                    locked=locked))
            else:
                stock = (r.get("capacity") or 0) + _sum_adjust(adj.get(rid, []), ds)
                day_rooms.append(RoomDay(
                    room=r.get("name", "?"), capacity=r.get("capacity") or 0,
                    booked=booked_guests.get((rid, ds), 0), stock=stock,
                    locked=locked, phone_only=not has_active_plan(rid, ds)))
        deadline = None
        if deadline_rule and day_rooms:
            n_days, t = deadline_rule
            deadline = datetime.combine(d - timedelta(days=n_days), t)
        out.append(HutDay(
            day=d, holiday=ds in holidays, rooms=day_rooms,
            opens_at=window.opens_at(d) if window and day_rooms else None,
            deadline=deadline, _now=_now))
    return out


def booking_url(hut_slug: str) -> str:
    return f"{_BASE}/hut/{hut_slug}"
