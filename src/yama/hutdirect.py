"""山屋官網空房直查：不經毎日あるぺん号套裝，直接查山屋自家預約系統。

支援三種預約系統（依 URL 網域自動判別）：
- tenawan.ne.jp   … 雷鳥荘等（Shift-JIS 舊式 ASP，全期間料金カレンダー）
- d-reserve.jp    … みくりが池温泉等（/v1/search/hotels/{code}/calendar JSON API）
- yamatan.net     … 立山室堂山荘等（tRPC hutEvent.getEvent，重算各房型剩餘）

供 watch 監控與 agent 即時查詢共用。回傳「事實」（各房型狀態），
可不可以訂、要不要等釋出的判斷留給呼叫端。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote, urlparse

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


@dataclass
class HutRoom:
    room: str        # 房型／方案名
    status: str      # ○ / × / 残N / IN_STOCK / NO_SALE …（來源系統的原始語彙）
    available: bool


@dataclass
class HutAvailability:
    hut: str
    provider: str    # tenawan / d-reserve / yamatan
    stay_date: str   # 入住日 YYYY-MM-DD
    rooms: list[HutRoom] = field(default_factory=list)

    @property
    def any_available(self) -> bool:
        return any(r.available for r in self.rooms)

    def signature(self) -> str:
        return "|".join(f"{r.room}:{r.status}" for r in self.rooms)


def detect_provider(url: str) -> str | None:
    host = urlparse(url).netloc
    for key in ("tenawan", "d-reserve", "yamatan"):
        if key in host:
            return key
    return None


def check_hut(url: str, stay_date: str, timeout: float = 20.0) -> HutAvailability:
    """查詢山屋官網某入住日的各房型空位。url 為使用者拿到的預約頁連結。"""
    provider = detect_provider(url)
    d = date.fromisoformat(stay_date)
    if provider == "tenawan":
        return _check_tenawan(url, d, timeout)
    if provider == "d-reserve":
        return _check_dreserve(url, d, timeout)
    if provider == "yamatan":
        return _check_yamatan(url, d, timeout)
    raise ValueError(f"不支援的預約系統：{urlparse(url).netloc}"
                     "（目前支援 tenawan.ne.jp / d-reserve.jp / yamatan.net）")


# -- tenawan（雷鳥荘）---------------------------------------------------------
# 構造：<base>/pcpl.asp 列出全部方案（pd.asp?Pnnn 連結）；
# <base>/pcpd.asp?Pnnn 是單一方案的全期間料金カレンダー，
# 可訂日是 f2.asp?<yymmdd>P<nnn> 連結、滿房日是 <em>日</em><span>×</span>。


def _tenawan_base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rsplit('/', 1)[0]}"


def _check_tenawan(url: str, d: date, timeout: float) -> HutAvailability:
    base = _tenawan_base(url)
    with httpx.Client(headers=_UA, timeout=timeout, follow_redirects=True) as c:
        top = c.get(f"{base}/pcpl.asp").content.decode("cp932", "ignore")
        hut = _first(r"<title>([^|<]+)", top) or "tenawan 山屋"
        codes = list(dict.fromkeys(re.findall(r"pd\.asp\?P(\d+)", top)))
        yymmdd = d.strftime("%y%m%d")
        rooms = []
        for code in codes:
            page = c.get(f"{base}/pcpd.asp?P{code}").content.decode("cp932", "ignore")
            name = _first(r"<h3>([^<]+)</h3>", page) or f"P{code}"
            if re.search(rf"f2\.asp\?{yymmdd}P{code}", page):
                status, ok = "○", True
            elif _tenawan_day_closed(page, d):
                status, ok = "×", False
            else:
                status, ok = "期間外/未販売", False
            rooms.append(HutRoom(name.strip(), status, ok))
            time.sleep(0.2)  # 舊式主機，溫柔一點
    return HutAvailability(hut.strip(), "tenawan", d.isoformat(), rooms)


def _tenawan_day_closed(page: str, d: date) -> bool:
    """在該月份的月曆表格裡，該日以 × 顯示（滿房／受付終了）。"""
    m = re.search(
        rf'class="month">{d.year}年[^<]*{d.month}月</th>(.*?)(?:class="month">|</table>)',
        page, re.S)
    if not m:
        return False
    for cell in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S):
        em = re.search(r"<em>\s*(?:<div[^>]*>)?\s*(\d+)", cell)
        if em and int(em.group(1)) == d.day:
            return "×" in cell
    return False


# -- d-reserve（みくりが池温泉）----------------------------------------------
# GET /v1/search/hotels/{hotelCode}/calendar?fromYM&toYM&lodgerCode=0&lodgerNum=2
# 回傳各房型每日 stockStatus（IN_STOCK/FEW_STOCK/SOLD_OUT/NO_SALE）。


def _check_dreserve(url: str, d: date, timeout: float) -> HutAvailability:
    qs = dict(re.findall(r"[?&]([^=&]+)=([^&]*)", url))
    code = qs.get("hotelCode")
    if not code:
        raise ValueError("d-reserve URL 缺少 hotelCode 參數")
    ym = d.strftime("%Y%m")
    api = (f"https://d-reserve.jp/v1/search/hotels/{code}/calendar"
           f"?fromYM={ym}&toYM={ym}&lodgerCode=0&lodgerNum=2&stays=1")
    with httpx.Client(headers={**_UA, "Accept-Language": "ja"},
                      timeout=timeout) as c:
        data = c.get(api).json()
        hut = _dreserve_hotel_name(c, code) or f"d-reserve {code}"
    rooms = []
    for r in data.get("data") or []:
        day = next((x for x in r.get("dailySalesStatusList", [])
                    if x.get("salesDate") == d.isoformat()), None)
        status = (day or {}).get("stockStatus", "UNKNOWN")
        rooms.append(HutRoom(r.get("name", "?"), status,
                             status in ("IN_STOCK", "FEW_STOCK")))
    return HutAvailability(hut, "d-reserve", d.isoformat(), rooms)


def _dreserve_hotel_name(c: httpx.Client, code: str) -> str | None:
    try:
        page = c.get("https://d-reserve.jp/GSEA001F01300/GSEA001A01"
                     f"?hotelCode={code}").text
        return _first(r'"hotelNameJp":"((?:[^"\\]|\\.)+)"', page, unescape=True)
    except httpx.HTTPError:
        return None


# -- yamatan（立山室堂山荘）---------------------------------------------------
# tRPC hutEvent.getEvent 回傳房型（total）、每日增減（roomAdjustments）與
# 預約明細；剩餘量照官網前端邏輯重算：
#   個室型（有 private_rooms）… (total+調整) − 該晚有 private_room_id 的預約件數
#   相部屋型                 … (total+調整) − 該晚預約人數合計


def _check_yamatan(url: str, d: date, timeout: float) -> HutAvailability:
    m = re.search(r"/hut/([^/]+)/", urlparse(url).path)
    if not m:
        raise ValueError("yamatan URL 找不到 hut id（預期 /hut/<id>/…）")
    hut_id = m.group(1)
    inp = quote(json.dumps({"0": {"json": {
        "hutId": hut_id, "year": str(d.year), "month": f"{d.month:02d}"}}}))
    api = f"https://www.yamatan.net/api/trpc/hutEvent.getEvent?batch=1&input={inp}"
    with httpx.Client(headers={**_UA, "Accept": "application/json"},
                      timeout=timeout) as c:
        j = c.get(api).json()[0]["result"]["data"]["json"]
    target = d.isoformat()

    def covers(x, end_inclusive=False):
        s, e = x["start_date"][:10], x["end_date"][:10]
        return s <= target <= e if end_inclusive else s <= target < e

    rooms = []
    for room in j.get("rooms", []):
        if not room.get("publish"):
            continue
        if not (room.get("public_start_date", "")[:10] <= target
                <= room.get("public_end_date", "9999")[:10]):
            continue
        stock = (room.get("total") or 0) + sum(
            a["adjustment_num"] for a in j.get("roomAdjustments", [])
            if a["room_id"] == room["id"] and covers(a, end_inclusive=True))
        res = [r for r in j.get("reservations", [])
               if r.get("room_id") == room["id"] and covers(r)]
        if room.get("private_rooms"):
            booked = sum(1 for r in res if r.get("private_room_id"))
        else:
            booked = sum(r.get("total_guest_num") or 0 for r in res)
        prohibited = any(
            c["DateRsvAvailabilityControl"]["date"][:10] == target
            and c["DateRsvAvailabilityControl"].get("prohibitNewRsvForUser")
            for c in room.get("DateRsvAvailabilityControlToRoom", []))
        remaining = stock - booked
        if stock <= 0 or prohibited:
            status, ok = "未販売/停售", False
        elif remaining > 0:
            status, ok = f"残{remaining}", True
        else:
            status, ok = "×", False
        rooms.append(HutRoom(room.get("name", "?"), status, ok))
    hut = _yamatan_hut_name(hut_id) or hut_id
    return HutAvailability(hut, "yamatan", target, rooms)


def _yamatan_hut_name(hut_id: str, timeout: float = 15.0) -> str | None:
    inp = quote(json.dumps({"0": {"json": {"hutId": hut_id}}}))
    api = f"https://www.yamatan.net/api/trpc/hut.getWithRelation?batch=1&input={inp}"
    try:
        with httpx.Client(headers={**_UA, "Accept": "application/json"},
                          timeout=timeout) as c:
            return c.get(api).json()[0]["result"]["data"]["json"].get("name")
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None


def _first(pattern: str, text: str, unescape: bool = False) -> str | None:
    m = re.search(pattern, text)
    if not m:
        return None
    s = m.group(1)
    if unescape:
        s = json.loads(f'"{s}"')
    return s
