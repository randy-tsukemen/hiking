"""把 yamatan adapter 橋接到 hut_avail 統一介面。"""

from __future__ import annotations

from .hut_avail import DayStatus, RoomStatus, register
from .yamatan import get_month_availability


@register("yamatan")
def get_month(hut_id: str, year: int, month: int) -> list[DayStatus]:
    out = []
    for d in get_month_availability(hut_id, year, month):
        if d.not_yet_open and not d.holiday:
            # 開賣前的空位數是滿容量佔位——標未開賣、不列房型，以免誤判可訂
            out.append(DayStatus(day=d.day, rooms=[], note=d.status))
            continue
        rooms = [RoomStatus(room=r.room,
                            status=f"残{r.remaining}" if r.capacity else "×")
                 for r in d.rooms]
        note = "休業" if d.holiday else ("非營業期間" if not d.rooms else "")
        out.append(DayStatus(day=d.day, rooms=rooms, note=note))
    return out
