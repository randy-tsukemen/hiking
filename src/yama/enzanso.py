"""enzanso-reservation.jp（燕山荘グループ自社預約系統）adapter。

覆蓋：燕山荘(p=10)、大天荘(p=20)、ヒュッテ大槍(p=30)、有明荘(p=40)。
日曆：GET enz0020.php?p={p}&date={YYYYMM01} 回傳該月日曆，
日格 = 満（滿）或 <a onclick doPost('YYYYMMDD')>日<br>&#9711(○)/&#9651(△)。
無房型細分（山屋整體一個狀態）。
"""

from __future__ import annotations

import calendar
import re
from datetime import date

import httpx

from .hut_avail import DayStatus, RoomStatus, register

_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36"),
    "Accept-Language": "ja,en;q=0.8",
}
_MARK = {"9711": "○", "9651": "△"}


def fetch_enz_style(base: str, hut_id: str, year: int, month: int) -> list[DayStatus]:
    """enz 系自社預約系統（燕山荘グループ與穂高岳山荘同一供應商）共用解析。"""
    url = f"{base}?p={hut_id}&date={year:04d}{month:02d}01"
    r = httpx.get(url, headers=_UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    html = r.text

    if not re.search(rf"{year}年\s*{month}月", html):
        return []  # 該月不在營業季/尚未開放

    statuses: dict[int, str] = {}
    # 可預約日：<a ... doPost(...'YYYYMMDD');">D<br>&#9711;
    for m in re.finditer(
        r"<div class='day'><a[^>]*'(\d{8})'[^>]*>(\d+)<br>&#(\d+)", html
    ):
        if int(m.group(1)[:4]) == year and int(m.group(1)[4:6]) == month:
            statuses[int(m.group(2))] = _MARK.get(m.group(3), "?")
    # 不可預約日：<div class='day'>D<br>満／&#10006(✖)／空白
    for m in re.finditer(r"<div class='day'>(\d+)<br>(?:&#(\d+))?([^<&]{0,4})<", html):
        day_n = int(m.group(1))
        if m.group(2):
            statuses.setdefault(day_n, {"10006": "×"}.get(m.group(2), "×"))
        elif m.group(3).strip():
            statuses.setdefault(day_n, m.group(3).strip())

    out = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        s = statuses.get(d)
        if s is None or s == "":
            out.append(DayStatus(day=date(year, month, d), rooms=[],
                                 note="非營業/無資料"))
        else:
            out.append(DayStatus(day=date(year, month, d),
                                 rooms=[RoomStatus(room="宿泊", status=s)]))
    return out


@register("enzanso")
def get_month(hut_id: str, year: int, month: int) -> list[DayStatus]:
    return fetch_enz_style(
        "https://enzanso-reservation.jp/reserve/enz0020.php", hut_id, year, month)
