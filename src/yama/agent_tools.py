"""Agent 工具層：包裝 yama 模組，回傳精簡結構化資料。

供 MCP server 與 LINE bot（Gemini）共用。設計原則：
輸出小而精（聊天介面有長度/時限限制）、巴士工具限制 detail 請求數。

注意：本模組不可使用 `from __future__ import annotations`——
google-genai SDK 以 inspect.signature 讀取參數註記做執行期型別檢查，
字串化註記會導致 isinstance() 錯誤。
"""

from datetime import date, timedelta
from typing import Any

from .maitabi import MaitabiClient
from .matcher import MountainDB
from .report import fetch_bus_data
from .weather import get_forecast, get_forecasts, rate_day

_db: MountainDB | None = None


def _get_db() -> MountainDB:
    global _db
    if _db is None:
        _db = MountainDB.load()
    return _db


def _not_found(name: str) -> dict[str, Any]:
    return {
        "error": f"資料庫未收錄「{name}」",
        "available_mountains": [m.name for m in _get_db().mountains],
    }


def list_mountains() -> dict[str, Any]:
    """列出資料庫收錄的所有山岳（名稱、標高、難度、山域）。"""
    return {
        "mountains": [
            {
                "name": m.name,
                "elevation_m": m.elevation,
                "difficulty": m.difficulty,
                "area": m.area_hint,
            }
            for m in _get_db().mountains
        ]
    }


def get_mountain_info(mountain: str) -> dict[str, Any]:
    """查詢一座山的概要、行程建議、山屋清單（含預約連結）、
    Yamap 模範路線（距離、爬升、標準時間、コース定数難度數值、体力度）。"""
    from .yamap import fetch_all_routes

    m = _get_db().find(mountain)
    if m is None:
        return _not_found(mountain)
    routes = [
        {
            "name": r.name,
            "distance_km": r.distance_km,
            "ascent_m": r.up_m,
            "descent_m": r.down_m,
            "standard_time": r.time_hm,
            "course_constant": r.course_constant,
            "course_constant_label": r.constant_label,
            "fitness_level_of_10": r.fitness_level,
            "fitness_label": r.fitness_label,
            "schedule": r.schedule_label,
            "url": r.url,
        }
        for r in fetch_all_routes(m.yamap)[:8]
    ]
    return {
        "name": m.name,
        "elevation_m": m.elevation,
        "difficulty": m.difficulty,
        "area": m.area_hint,
        "trailheads": m.trailheads,
        "itineraries": m.itineraries,
        "yamap_model_routes": routes,
        "huts": [
            {
                "name": h["name"],
                "elevation_m": h.get("elevation"),
                "note": h.get("note", ""),
                "booking_url": h["booking_url"],
                "phone": h.get("phone"),
            }
            for h in m.huts
        ],
        "yamap": m.yamap,
    }


def get_weather(mountain: str) -> dict[str, Any]:
    """查詢一座山未來 16 天的山頂天氣預報與登山適宜度（◎○△×）。"""
    m = _get_db().find(mountain)
    if m is None:
        return _not_found(mountain)
    days = []
    for fc in get_forecast(m.lat, m.lon, m.elevation):
        s = rate_day(fc, m.difficulty)
        days.append(
            {
                "date": fc.day.isoformat(),
                "weekday": "一二三四五六日"[fc.day.weekday()],
                "summary": fc.summary,
                "temp_c": f"{fc.t_min:.0f}~{fc.t_max:.0f}",
                "rain_prob_pct": fc.rain_prob,
                "wind_ms": round(fc.wind_max),
                "grade": s.grade,
                "score": s.score,
            }
        )
    return {"name": m.name, "elevation_m": m.elevation, "forecast_16d": days}


def get_bus_options(mountain: str, month: int | None = None,
                    include_departures: bool = False) -> dict[str, Any]:
    """查詢一座山的毎日あるぺん号巴士方案（東京發）。

    預設（include_departures=False）快速回傳方案清單（名稱/分類/價格/
    詳細連結，去程/來回/回程各前 3），**不含出發日班次**——適合第一階段
    介紹方案組合。使用者對特定方案有興趣後，再以 include_departures=True
    取得出發日與預約連結（較慢），房間空位另用 check_hut_room_availability。
    month 省略時查當月。
    """
    m = _get_db().find(mountain)
    if m is None:
        return _not_found(mountain)
    today = date.today()
    month = month or today.month
    grades: dict[date, str] = {}
    if include_departures:
        for fc in get_forecast(m.lat, m.lon, m.elevation):
            grades[fc.day] = rate_day(fc, m.difficulty).grade

    with MaitabiClient() as client:
        bus = fetch_bus_data(m, client, month, today,
                             max_details=4 if include_departures else 0)
    if bus.empty:
        return {
            "name": m.name,
            "month": month,
            "message": f"{month} 月查無「{'、'.join(m.maitabi_area_names)}」方面的巴士方案",
        }

    from .matching import plan_category

    def pack(tours, night_bus: bool) -> list[dict[str, Any]]:
        out = []
        for t in tours[: 2 if include_departures else 3]:
            d = bus.details.get(t.course_no)
            slots = []
            if d:
                for slot in d.reserves:
                    sd = slot.depart_date
                    if sd is None or sd < today:
                        continue
                    hike_day = sd + timedelta(days=1) if night_bus else sd
                    slots.append(
                        {
                            "depart_date": slot.date_raw,
                            "status": slot.status,
                            "price": slot.price,
                            "booking_url": slot.link,
                            "hike_day_weather_grade": grades.get(hike_day),
                        }
                    )
                    if len(slots) >= 4:
                        break
            out.append(
                {
                    "title": t.title,
                    "category": plan_category(t, m),
                    "price": t.price,
                    "detail_url": f"https://bus.maitabi.jp/detail.html?course_no={t.course_no}",
                    "departures": slots,
                }
            )
        return out

    return {
        "name": m.name,
        "month": month,
        "note": "去程/來回為夜行巴士：晚上出發、翌日清晨抵達登山口。天氣適宜度對應實際登山日。",
        "outbound": pack(bus.outbound, night_bus=True),
        # 含住宿路線優先展示山小屋セット：取最便宜套裝＋最便宜純巴士各一
        "roundtrip_with_hut_packages": pack(
            sorted(bus.roundtrip,
                   key=lambda t: (plan_category(t, m) != "山小屋セット",))[:1]
            + [t for t in bus.roundtrip if plan_category(t, m) != "山小屋セット"][:1],
            night_bus=True),
        "inbound": pack(bus.inbound, night_bus=False),
    }


def rank_mountains_by_weather(days_ahead: int = 7) -> dict[str, Any]:
    """依天氣適宜度為所有收錄山岳排名（未來 N 天內的最佳登山日）。

    weekend 問題可用 days_ahead=7 再由回答聚焦在週六日。
    不含巴士查詢（避免逾時）；建議使用者選定山後再用 get_bus_options 查巴士。
    """
    db = _get_db()
    today = date.today()
    targets = [today + timedelta(days=i) for i in range(1, min(days_ahead, 16) + 1)]
    all_fc = get_forecasts([(m.lat, m.lon, m.elevation) for m in db.mountains])

    rows = []
    for m, fcs in zip(db.mountains, all_fc):
        scored = [
            (fc, rate_day(fc, m.difficulty)) for fc in fcs if fc.day in targets
        ]
        if not scored:
            continue
        best_fc, best_s = max(scored, key=lambda x: x[1].score)
        rows.append(
            {
                "name": m.name,
                "elevation_m": m.elevation,
                "difficulty": m.difficulty,
                "best_date": best_fc.day.isoformat(),
                "best_weekday": "一二三四五六日"[best_fc.day.weekday()],
                "grade": best_s.grade,
                "score": best_s.score,
                "weather": best_fc.summary,
                "temp_c": f"{best_fc.t_min:.0f}~{best_fc.t_max:.0f}",
                "rain_prob_pct": best_fc.rain_prob,
            }
        )
    rows.sort(key=lambda r: -r["score"])
    return {"period_days": len(targets), "ranking": rows}


def check_hut_room_availability(course_no: int, depart_date: str) -> dict[str, Any]:
    """查詢巴士套裝方案某出發日的「房間」空位（逐晚）。

    重要：巴士的「受付中/催行決定」不代表山屋房間有空位。
    推薦含山屋的套裝方案時應以本工具確認房間狀態。
    depart_date 格式 YYYY-MM-DD。
    狀態：○=有空位、數字=剩餘數、RQ=請求受理、WT=候補、×=已滿。
    """
    from .travelanswer import check_room_availability

    try:
        r = check_room_availability(course_no, depart_date)
    except RuntimeError as e:
        return {"course_no": course_no, "depart_date": depart_date, "error": str(e)}
    return {
        "course_no": course_no,
        "title": r.title,
        "depart_date": r.depart_date,
        "bookable_all_nights": r.all_ok,
        "nights": [
            {
                "night": n.night,
                "facility": n.facility,
                "room_type": n.room_type,
                "status": n.status,
                "status_label": n.label,
                "ok": n.ok,
            }
            for n in r.nights
        ],
    }



def verify_trip_candidate(mountain: str, hike_date: str, party: int = 1,
                          nights: int = -1) -> dict[str, Any]:
    """查證某個登山日的全部事實（天氣數據、巴士班次狀態、套裝房間逐晚實查），
    **不做推薦**——取捨由你依使用者偏好判斷。

    hike_date: YYYY-MM-DD（登山日，夜行巴士為前一晚出發）。
    nights: 泊數；-1 表示用資料庫預設行程的泊數。
    近期規劃的正確流程：先 get_weather 看天氣→與使用者確認候選日→
    對候選日呼叫本工具→依使用者偏好（價格/山屋/風險容忍）組合建議。
    """
    from datetime import date as _date

    from .planner import verify_candidate

    m = _get_db().find(mountain)
    if m is None:
        return _not_found(mountain)
    with MaitabiClient() as client:
        return verify_candidate(
            m, client, _date.fromisoformat(hike_date),
            nights=None if nights < 0 else nights, party=party)


def plan_trip(mountain: str, when: str = "weekend", party: int = 1) -> dict[str, Any]:
    """一鍵成案：驗證天氣→巴士→房間後回傳唯一建議（人只需按預約連結確認）。

    when: "weekend"（預設，下個週末）/ "best"（14 天內最佳）/ "YYYY-MM-DD"（指定登山日）。
    party: 人數。回傳含已驗證的天氣、方案、價格、逐晚房間狀態、預約與詳細連結；
    ok=False 時 reason 說明不成案原因（天氣差/無巴士）。
    """
    from .planner import plan_trip as _plan

    m = _get_db().find(mountain)
    if m is None:
        return _not_found(mountain)
    with MaitabiClient() as client:
        p = _plan(m, client, when=when, party=party)
    out: dict[str, Any] = {
        "ok": p.ok,
        "mountain": p.mountain,
        "reason": p.reason,
        "hike_dates": [d.isoformat() for d in p.hike_dates],
        "weather": p.weather_notes,
        "depart_date": p.depart_date.isoformat() if p.depart_date else None,
        "course_title": p.course_title,
        "price": p.price,
        "detail_url": p.detail_url,
        "booking_url": p.booking_url,
        "itinerary": p.itinerary,
        "lodging_note": p.lodging_note,
        "alternatives": p.alternatives,
    }
    if p.rooms:
        out["rooms"] = [
            {"night": n.night, "facility": n.facility, "room_type": n.room_type,
             "status": n.status, "ok": n.ok}
            for n in p.rooms.nights
        ]
    return out


# Gemini function declarations（給 agent.py 註冊用）
TOOL_FUNCTIONS = [
    list_mountains,
    get_mountain_info,
    get_weather,
    get_bus_options,
    rank_mountains_by_weather,
    check_hut_room_availability,
    verify_trip_candidate,
    plan_trip,
]
