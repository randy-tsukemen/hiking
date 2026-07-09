"""tenawan（天和，tenawan.ne.jp）預約系統 adapter。

覆蓋：雷鳥荘、白馬岳頂上宿舎（村営）、太子舘 等。
空室頁 pcr.asp 一頁含整季各月日曆（caltbl 表格）：
列首為房型清單，各日格為 <b>日</b> + 各房型狀態（○/△/×/休/問/直前不可）。
CP932 編碼；需要瀏覽器 UA 否則 403。
"""

from __future__ import annotations

import re
from datetime import date

import httpx

from .hut_avail import DayStatus, RoomStatus, register

_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "ja,en;q=0.8",
}


def _fetch_season(hut_id: str) -> str:
    url = f"https://www.tenawan.ne.jp/lodgment/rec/{hut_id}/pcr.asp"
    r = httpx.get(url, headers=_UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content.decode("cp932", errors="replace")


@register("tenawan")
def get_month(hut_id: str, year: int, month: int) -> list[DayStatus]:
    html = _fetch_season(hut_id)

    # 逐月切塊：月表頭 → 下一個月表頭之間
    heads = [(m.start(), int(m.group(1)), int(m.group(2)))
             for m in re.finditer(r'class="month">(\d{4})年[^<]*?(\d+)月', html)]
    block = None
    for i, (pos, y, mo) in enumerate(heads):
        if y == year and mo == month:
            end = heads[i + 1][0] if i + 1 < len(heads) else len(html)
            block = html[pos:end]
            break
    if block is None:
        return []  # 該月不在營業季（頁面只列營業月份）

    # 房型清單：列首 .room 內的 tdr 格
    rooms_m = re.search(r'class="room".*?<table>(.*?)</table>', block, re.S)
    room_names = re.findall(r'class="tdr\d">([^<]*)<', rooms_m.group(1)) if rooms_m else []

    out: dict[int, DayStatus] = {}
    for m in re.finditer(
        r'<b>(\d+)</b>.*?<table>(.*?)</table>', block, re.S
    ):
        day_n = int(m.group(1))
        statuses = re.findall(r'class="tdc\d">(?:<a[^>]*>)?([^<]*)<', m.group(2))
        rooms = [RoomStatus(room=room_names[i] if i < len(room_names) else f"房型{i+1}",
                            status=s.strip())
                 for i, s in enumerate(statuses)]
        note = "休業" if rooms and all(r.status == "休" for r in rooms) else ""
        try:
            out[day_n] = DayStatus(day=date(year, month, day_n),
                                   rooms=rooms, note=note)
        except ValueError:
            continue
    # 補齊該月沒出現在日曆的日子（非營業）
    import calendar as _cal
    result = []
    for d in range(1, _cal.monthrange(year, month)[1] + 1):
        result.append(out.get(d, DayStatus(day=date(year, month, d),
                                           rooms=[], note="非營業/無資料")))
    return result
