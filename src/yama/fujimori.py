"""akadake.sakura.ne.jp（株式会社ふじもり 宿泊予約）adapter。

覆蓋：八ヶ岳山荘(hotel_id=1)、赤岳天望荘(2)、美濃戸山荘(3)、ヒュッテ夏沢(4)。
日曆頁 status.php?hotel_id={id}&year={Y}&month={M} 把整月庫存以
`const realInventoryData = {...};` 內嵌在 HTML，直接取 JSON：
  {"YYYY-MM-DD": {"LGRM": {"name": "大部屋（相部屋）", "unit": "名",
                            "stocks": 0, "cap": 40}, ...}, ...}
cap=0 的房型該日未販售；當日券不受理（電話制），與過去日期同樣略過。
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date

import httpx

from .hut_avail import DayStatus, RoomStatus, register

_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36"),
    "Accept-Language": "ja,en;q=0.8",
}

_BASE = "https://akadake.sakura.ne.jp/fj/reservation/status.php"


@register("fujimori")
def get_month(hut_id: str, year: int, month: int) -> list[DayStatus]:
    url = f"{_BASE}?hotel_id={hut_id}&year={year}&month={month}"
    r = httpx.get(url, headers=_UA, timeout=30, follow_redirects=True)
    r.raise_for_status()

    m = re.search(r"realInventoryData\s*=\s*(\{.*?\});", r.text, re.DOTALL)
    if m is None:
        return []
    inventory = json.loads(m.group(1))

    out = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date(year, month, d)
        rooms_raw = inventory.get(day.isoformat())
        if rooms_raw is None:
            out.append(DayStatus(day=day, rooms=[], note="非營業/無資料"))
            continue
        offered = {k: v for k, v in rooms_raw.items() if v.get("cap", 0) > 0}
        if not offered:
            out.append(DayStatus(day=day, rooms=[], note="休業"))
            continue
        rooms = [
            RoomStatus(
                room=v.get("name", k),
                status=(f"残{v['stocks']}{v.get('unit', '')}"
                        if v.get("stocks", 0) > 0 else "×"),
            )
            for k, v in offered.items()
        ]
        out.append(DayStatus(day=day, rooms=rooms))
    return out
