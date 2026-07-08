"""一鍵成案：把天氣→巴士→房間實查串成一個決策，輸出唯一建議。

決策流程：
1. 依 when（weekend / best / 指定日）產生候選登山日，天氣分數高者優先
2. 逐候選日驗證：前一晚有可預約的往復巴士 → 套裝方案逐一實查房間
   （RQ 視為可申請）→ 全部通過即成案
3. 套裝全滿時退而求其次：純巴士 + 資料庫推薦山屋（住宿自理）
4. 候選日天氣全部太差時，改建議 16 天內最佳天氣窗
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .maitabi import MaitabiClient, Tour
from .matcher import Mountain
from .report import fetch_bus_data
from .travelanswer import RoomAvailability, check_room_availability
from .weather import get_forecast, rate_day

_UNBOOKABLE = ("満席", "受付終了", "キャンセル待ち", "催行中止")
_HUT_HINT = re.compile(r"[荘館]|ヒュッテ|小屋|セット")
_MIN_SCORE = 55  # ○ 以上才成案
_MAX_ROOM_PROBES = 3


@dataclass
class TripPlan:
    ok: bool
    mountain: str
    reason: str = ""
    hike_dates: list[date] = field(default_factory=list)
    weather_notes: list[str] = field(default_factory=list)
    depart_date: date | None = None
    course_title: str = ""
    course_no: int | None = None
    price: str = ""
    booking_url: str = ""
    detail_url: str = ""
    rooms: RoomAvailability | None = None
    lodging_note: str = ""
    itinerary: str = ""
    alternatives: list[str] = field(default_factory=list)


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


def candidate_dates(when: str, today: date, horizon: int = 14) -> list[date]:
    """when: weekend | best | YYYY-MM-DD → 候選登山日（未排序）。"""
    if when == "weekend":
        sat = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        return [sat, sat + timedelta(days=1)]
    if when == "best":
        return [today + timedelta(days=i) for i in range(1, horizon + 1)]
    return [date.fromisoformat(when.replace("/", "-"))]


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


def plan_trip(
    mountain: Mountain,
    client: MaitabiClient,
    when: str = "weekend",
    party: int = 1,
    today: date | None = None,
) -> TripPlan:
    today = today or date.today()
    nights = parse_nights(mountain)

    # 1) 天氣：候選日評分
    forecast = {fc.day: fc for fc in get_forecast(
        mountain.lat, mountain.lon, mountain.elevation)}
    scored: list[tuple[date, int, str]] = []
    for d in candidate_dates(when, today):
        # 多日行程：所有登山日都要在預報內取最低分做代表
        days = [d + timedelta(days=i) for i in range(nights + 1)]
        fcs = [forecast.get(x) for x in days]
        if any(f is None for f in fcs) or d <= today:
            continue
        rates = [rate_day(f, mountain.difficulty) for f in fcs]
        worst = min(rates, key=lambda r: r.score)
        scored.append((d, worst.score, worst.grade))
    scored.sort(key=lambda x: -x[1])

    if not scored:
        return TripPlan(ok=False, mountain=mountain.name,
                        reason="候選日期不在 16 天預報範圍內（或已過期），請改用 --when best 或提供近期日期")

    viable = [s for s in scored if s[1] >= _MIN_SCORE]
    if not viable:
        best = scored[0]
        return TripPlan(
            ok=False, mountain=mountain.name,
            reason=f"候選日天氣皆不佳（最好僅 {best[2]}{best[1]}），不建議成行",
            weather_notes=[f"{d.month}/{d.day} {g}{s}" for d, s, g in scored[:5]],
        )

    # 2) 逐候選日驗證巴士與房間
    bus_cache: dict[int, object] = {}
    probes = 0
    fallback: TripPlan | None = None
    for hike_day, score, grade in viable:
        depart = hike_day - timedelta(days=1)
        if depart <= today:
            continue
        if depart.month not in bus_cache:
            bus_cache[depart.month] = fetch_bus_data(
                mountain, client, depart.month, today, max_details=6)
        bus = bus_cache[depart.month]
        if bus.empty:
            continue

        hike_dates = [hike_day + timedelta(days=i) for i in range(nights + 1)]
        notes = []
        for x in hike_dates:
            fc = forecast.get(x)
            if fc:
                r = rate_day(fc, mountain.difficulty)
                notes.append(
                    f"{x.month}/{x.day}({'一二三四五六日'[x.weekday()]}) "
                    f"{fc.summary} {fc.t_min:.0f}~{fc.t_max:.0f}°C 雨{fc.rain_prob}% {r.grade}{r.score}")

        # 往復方案（含套裝），套裝先驗房間。
        # 排序：資料庫推薦山屋優先、異口縱走（復路非本山登山口）降級、再比價格。
        hut_sets = [t for t in bus.roundtrip if _HUT_HINT.search(t.title)]
        pure = [t for t in bus.roundtrip if not _HUT_HINT.search(t.title)]

        def _set_rank(t: Tour) -> tuple:
            rank = 0
            if any(h["name"] in t.title for h in mountain.huts):
                rank -= 2
            m2 = re.search(r"復路[/／]([^発〈（]+)", t.title)
            if m2 and not any(
                th[:2] in m2.group(1) for th in mountain.trailheads
            ):
                rank += 3  # 回程從別的登山口出＝不同路線的縱走套裝
            return (rank, _price_of(t))

        def _price_of(t: Tour) -> int:
            digits = "".join(c for c in t.price if c.isdigit())
            return int(digits) if digits else 10**9

        hut_sets.sort(key=_set_rank)

        def bookable(t: Tour):
            # 報告用的 detail 配額偏向便宜方案；成案需要的按需補抓
            d = bus.details.get(t.course_no)
            if not d:
                d = client.get_tour_detail(t.course_no)
                bus.details[t.course_no] = d
            s = _slot_for(d, depart)
            if s and not any(u in s.status for u in _UNBOOKABLE):
                return s
            return None

        for t in hut_sets[:4]:
            if probes >= _MAX_ROOM_PROBES:
                break
            slot = bookable(t)
            if not slot:
                continue
            probes += 1
            try:
                rooms = check_room_availability(
                    t.course_no, depart.isoformat(), adults=party)
            except RuntimeError:
                continue
            if rooms.all_ok:
                return TripPlan(
                    ok=True, mountain=mountain.name,
                    hike_dates=hike_dates, weather_notes=notes,
                    depart_date=depart, course_title=t.title,
                    course_no=t.course_no, price=slot.price,
                    booking_url=slot.link,
                    detail_url=f"https://bus.maitabi.jp/detail.html?course_no={t.course_no}",
                    rooms=rooms,
                    itinerary=mountain.itineraries[0]["plan"] if mountain.itineraries else "",
                    alternatives=[f"純巴士 {p.price}（住宿自訂）" for p in pure[:1]],
                )

        # 套裝都不行 → 純巴士備案（記住第一個，繼續試更好的天氣日）
        if fallback is None:
            for t in pure:
                slot = bookable(t)
                if slot:
                    huts = "、".join(
                        f"{h['name']}（{h['booking_url']}）" for h in mountain.huts[:2])
                    fallback = TripPlan(
                        ok=True, mountain=mountain.name,
                        hike_dates=hike_dates, weather_notes=notes,
                        depart_date=depart, course_title=t.title,
                        course_no=t.course_no, price=slot.price,
                        booking_url=slot.link,
                        detail_url=f"https://bus.maitabi.jp/detail.html?course_no={t.course_no}",
                        lodging_note=f"套裝山屋已滿或無套裝；住宿自訂：{huts}" if nights else "",
                        itinerary=mountain.itineraries[0]["plan"] if mountain.itineraries else "",
                    )
                    break

    if fallback:
        return fallback
    return TripPlan(
        ok=False, mountain=mountain.name,
        reason="天氣可行的日期都沒有可預約的往復巴士班次",
        weather_notes=[f"{d.month}/{d.day} {g}{s}" for d, s, g in viable[:5]],
    )


def render_plan(p: TripPlan) -> str:
    """輸出純文字友善的成案結果。"""
    wd = "一二三四五六日"
    if not p.ok:
        lines = [f"⛔ {p.mountain}：暫不建議成行", f"原因：{p.reason}"]
        if p.weather_notes:
            lines.append("近期天氣參考：" + "、".join(p.weather_notes))
        return "\n".join(lines)

    d0, d_end = p.hike_dates[0], p.hike_dates[-1]
    lines = [
        f"✅ {p.mountain} 成案（已驗證：天氣・巴士・房間）",
        "",
        f"日期：{p.depart_date.month}/{p.depart_date.day}"
        f"({wd[p.depart_date.weekday()]})夜發 → "
        f"{d0.month}/{d0.day}〜{d_end.month}/{d_end.day} 登山",
        "",
        "天氣（登山日）：",
    ]
    lines += [f"・{n}" for n in p.weather_notes]
    lines += ["", f"方案：{p.course_title}", f"價格：{p.price}"]
    if p.rooms:
        lines.append("房間實查：")
        for n in p.rooms.nights:
            mark = "✅" if n.ok else "❌"
            lines.append(f"・{n.night} {n.facility} {n.room_type} {mark}{n.status}（{n.label}）")
    if p.lodging_note:
        lines.append(p.lodging_note)
    if p.itinerary:
        lines += ["", f"行程：{p.itinerary}"]
    lines += [
        "",
        f"方案詳細：{p.detail_url}",
        f"👉 預約：{p.booking_url}",
    ]
    if p.alternatives:
        lines.append("備選：" + "；".join(p.alternatives))
    return "\n".join(lines)
