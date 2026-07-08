"""行程查證器：對指定登山日驗證全部事實，不做任何選擇。

事實與驗證屬於程式（昂貴、客觀）；選哪天、選哪個方案、天氣夠不夠好
屬於使用者的 agent（知道使用者偏好）。本模組只提供前者。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .maitabi import MaitabiClient
from .matcher import Mountain
from .report import fetch_bus_data
from .travelanswer import check_room_availability
from .weather import get_forecast, rate_day

_UNBOOKABLE = ("満席", "受付終了", "キャンセル待ち", "催行中止")
_HUT_HINT = re.compile(r"[荘館]|ヒュッテ|小屋|セット")


def parse_nights(mountain: Mountain) -> int:
    """從資料庫第一個行程建議推出泊數（1泊2日→1、日帰り→0）。"""
    for it in mountain.itineraries:
        days = it.get("days", "")
        m = re.search(r"(\d)泊", days)
        if m:
            return int(m.group(1))
        if "日帰り" in days:
            return 0
    return 1


def _slot_for(detail, d: date):
    for s in detail.reserves:
        if s.depart_date == d:
            return s
    return None


def verify_candidate(
    mountain: Mountain,
    client: MaitabiClient,
    hike_day: date,
    nights: int | None = None,
    party: int = 1,
    max_room_probes: int = 4,
    today: date | None = None,
) -> dict:
    """查證單一登山日的全部事實，**不做任何選擇**——判斷留給 agent。

    回傳：逐日天氣原始數據、該出發日各往復方案的巴士狀態、
    套裝方案的逐晚房間實查結果。偏好（要不要去、選哪個）不在這裡。
    """
    from .matching import plan_category

    today = today or date.today()
    if nights is None:
        nights = parse_nights(mountain)
    depart = hike_day - timedelta(days=1)

    forecast = {fc.day: fc for fc in get_forecast(
        mountain.lat, mountain.lon, mountain.elevation)}
    weather = []
    for i in range(nights + 1):
        d = hike_day + timedelta(days=i)
        fc = forecast.get(d)
        if fc:
            r = rate_day(fc, mountain.difficulty)
            weather.append({
                "date": d.isoformat(), "summary": fc.summary,
                "t_min": round(fc.t_min), "t_max": round(fc.t_max),
                "rain_prob_pct": fc.rain_prob, "wind_ms": round(fc.wind_max),
                "score": r.score, "grade": r.grade,
            })
        else:
            weather.append({"date": d.isoformat(), "note": "超出16天預報範圍"})

    bus = fetch_bus_data(mountain, client, depart.month, today, max_details=6)
    plans = []
    probes = 0
    for t in bus.roundtrip:
        d = bus.details.get(t.course_no)
        if not d:
            d = client.get_tour_detail(t.course_no)
            bus.details[t.course_no] = d
        slot = _slot_for(d, depart)
        cat = plan_category(t, mountain)
        entry: dict = {
            "title": t.title, "category": cat, "price": t.price,
            "detail_url": f"https://bus.maitabi.jp/detail.html?course_no={t.course_no}",
            "bus_status": slot.status if slot else "該日無班次",
            "bookable": bool(slot) and not any(u in slot.status for u in _UNBOOKABLE),
        }
        if slot:
            entry["booking_url"] = slot.link
        # 套裝且巴士可訂 → 逐晚房間實查
        if entry["bookable"] and cat in ("山小屋セット", "異口縱走用")                 and _HUT_HINT.search(t.title) and probes < max_room_probes:
            probes += 1
            try:
                rooms = check_room_availability(
                    t.course_no, depart.isoformat(), adults=party)
                entry["rooms"] = [
                    {"night": n.night, "facility": n.facility,
                     "room_type": n.room_type, "status": n.status, "ok": n.ok}
                    for n in rooms.nights
                ]
                entry["rooms_all_ok"] = rooms.all_ok
            except RuntimeError as e:
                entry["rooms_error"] = str(e)[:80]
        plans.append(entry)

    return {
        "mountain": mountain.name,
        "hike_dates": [ (hike_day + timedelta(days=i)).isoformat()
                        for i in range(nights + 1)],
        "depart_date": depart.isoformat(),
        "nights": nights,
        "weather": weather,
        "roundtrip_plans": plans,
        "note": "本結果只有事實與驗證，未做任何取捨；巴士可訂≠房間可訂，rooms 欄位才是房間實況",
    }


