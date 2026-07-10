"""Yamatan（yamatan.net，山小屋預約平台）空位查詢 adapter。

山屋官網直訂是整個登山行程最難搶的資源。Yamatan 是多家山屋共用的
預約平台（tRPC API），`hutEvent.getEvent` 一次回傳該月的：
房型（容量、公開期間）、匿名化預約記錄、容量調整、休業日——
平台前端就是用「容量±調整−已訂人數」計算空位，本模組做同樣的計算。

注意：受付開始**前**的日期也會回傳滿容量，看起來像整月有位——那是佔位顯示。
受付窗口在同一 response 的 beforeReservationType（months/days）＋
before_reservation_num ＋ canReserveStartDateTime（開賣時刻），
例：涸沢ヒュッテ＝宿泊日 1 個月前 08:00、横尾山荘＝2 個月前 07:00。
本模組據此標記未開賣日（HutDay.not_yet_open / opens_at）。

僅讀取（與網頁瀏覽等價），不建立預約；輪詢請保持禮貌頻率。
"""

from __future__ import annotations

import calendar
import json
import urllib.parse
from dataclasses import dataclass
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


@dataclass
class RoomDay:
    room: str
    capacity: int
    booked: int

    @property
    def remaining(self) -> int:
        return max(self.capacity - self.booked, 0)


@dataclass
class HutDay:
    day: date
    holiday: bool
    rooms: list[RoomDay]
    opens_at: datetime | None = None  # 受付開始時刻（None＝平台未提供窗口資訊）

    @property
    def remaining_total(self) -> int:
        return sum(r.remaining for r in self.rooms)

    @property
    def not_yet_open(self) -> bool:
        """尚未開賣：此時空位數只是滿容量佔位，不代表可訂。"""
        return bool(self.opens_at and datetime.now() < self.opens_at)

    @property
    def status(self) -> str:
        if self.holiday:
            return "休業"
        if not self.rooms:
            return "非營業期間"
        if self.not_yet_open:
            return f"未開賣（{self.opens_at:%-m/%-d %H:%M} 開賣）"
        if self.remaining_total == 0:
            return "満室"
        return f"残{self.remaining_total}"


def get_month_availability(hut_slug: str, year: int, month: int) -> list[HutDay]:
    """計算某山屋某月逐日空位（各房型 容量±調整−已訂）。"""
    ev = _trpc("hutEvent.getEvent",
               {"hutId": hut_slug, "year": str(year), "month": f"{month:02d}"})

    window = _parse_window(ev)
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
        d = h.get("date") or h.get("start_date")
        if d:
            holidays.add(d)

    # 容量調整：(room_id, date) → adjustment_num（該日容量的絕對值覆蓋或調整值）
    adjustments: dict[tuple[str, str], int] = {}
    for a in ev.get("adjustments", []) + ev.get("roomAdjustments", []):
        d0 = date.fromisoformat(a["start_date"])
        d1 = date.fromisoformat(a["end_date"])
        cur = d0
        while cur <= d1:
            adjustments[(a["room_id"], cur.isoformat())] = a["adjustment_num"]
            cur = date.fromordinal(cur.toordinal() + 1)

    # 已訂人數：(room_id, date) → 人數合計（住宿日 = start_date ≤ d < end_date）
    booked: dict[tuple[str, str], int] = {}
    for rsv in ev.get("reservations", []):
        d0 = date.fromisoformat(rsv["start_date"])
        d1 = date.fromisoformat(rsv["end_date"])
        cur = d0
        while cur < d1:
            key = (rsv["room_id"], cur.isoformat())
            booked[key] = booked.get(key, 0) + int(rsv.get("total_guest_num") or 0)
            cur = date.fromordinal(cur.toordinal() + 1)

    out: list[HutDay] = []
    for day_n in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day_n)
        ds = d.isoformat()
        day_rooms: list[RoomDay] = []
        for r in rooms:
            ps, pe = r.get("public_start_date"), r.get("public_end_date")
            if ps and ds < ps:
                continue
            if pe and ds > pe:
                continue
            cap = adjustments.get((r["id"], ds), r.get("capacity") or 0)
            day_rooms.append(RoomDay(
                room=r.get("name", "?"), capacity=cap,
                booked=booked.get((r["id"], ds), 0)))
        out.append(HutDay(
            day=d, holiday=ds in holidays, rooms=day_rooms,
            opens_at=window.opens_at(d) if window and day_rooms else None))
    return out


def booking_url(hut_slug: str) -> str:
    return f"{_BASE}/hut/{hut_slug}"
