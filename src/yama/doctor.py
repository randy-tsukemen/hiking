"""煙霧測試：對所有外部資料來源做真實查詢健檢。

這個工具依賴四個非官方/會改版的來源（maitabi API、travel-answer 預約頁、
Yamap SSR 頁面、Open-Meteo），任何一個改版都會讓功能「靜默壞掉」
（回空資料而不是報錯）。`yama doctor` 逐項驗證資料形狀，
供每週 CI 執行，壞了立刻知道。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

import httpx


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _check_maitabi() -> list[CheckResult]:
    from .maitabi import MaitabiClient

    out = []
    month = (date.today() + timedelta(days=21)).month
    with MaitabiClient() as c:
        districts = c.get_districts(month)
        out.append(CheckResult(
            "maitabi 山域目錄", len(districts) >= 5,
            f"{month} 月有 {len(districts)} 個山域"))
        aid = next((v for k, v in districts.items() if "上高地" in k), None)
        if aid is None:
            out.append(CheckResult("maitabi 上高地 district", False, "目錄中找不到上高地"))
            return out
        tours = c.search_tours(month, aid, max_pages=1)
        out.append(CheckResult(
            "maitabi 方案搜尋", len(tours) > 0,
            f"上高地 {month} 月首頁 {len(tours)} 筆"))
        if tours:
            d = c.get_tour_detail(tours[0].course_no)
            has_link = any(s.link.startswith("https://") for s in d.reserves)
            out.append(CheckResult(
                "maitabi 方案詳細＋預約連結", bool(d.title) and has_link,
                f"{len(d.reserves)} 個出發日"))
    return out


def _check_weather() -> list[CheckResult]:
    from .weather import get_forecast

    fc = get_forecast(36.4041, 137.7128, 2763)  # 燕岳
    ok = len(fc) >= 14 and all(-40 < f.t_min < 40 for f in fc[:3])
    return [CheckResult("Open-Meteo 天氣", ok, f"{len(fc)} 天預報")]


def _check_yamap() -> list[CheckResult]:
    from .yamap import fetch_model_routes, fetch_route_page

    routes = fetch_model_routes("https://yamap.com/mountains/150")  # 燕岳
    out = [CheckResult(
        "Yamap 山岳頁路線解析", len(routes) >= 3,
        f"燕岳 {len(routes)} 條路線" + (
            f"，首條含定数 {routes[0].course_constant}" if routes else ""))]
    r = fetch_route_page(26242)  # 吉田ルート
    out.append(CheckResult(
        "Yamap 單一路線頁解析", r is not None and r.distance_m > 5000,
        f"吉田ルート {r.distance_km}km" if r else "解析失敗"))
    return out


def _check_travelanswer() -> list[CheckResult]:
    from .maitabi import MaitabiClient
    from .travelanswer import check_room_availability

    # 找一個近期可預約的套裝方案動態測試（不寫死 course_no，避免季節性失效）
    month = (date.today() + timedelta(days=21)).month
    with MaitabiClient() as c:
        districts = c.get_districts(month)
        aid = next((v for k, v in districts.items() if "立山" in k), None)
        if aid is None:
            return [CheckResult("travel-answer 房間解析", False, "找不到立山 district")]
        tours = [t for t in c.search_tours(month, aid, max_pages=2)
                 if "泊" in t.title and "往復" in t.title]
        random.shuffle(tours)
        for t in tours[:3]:
            d = c.get_tour_detail(t.course_no)
            slot = next((s for s in d.reserves
                         if s.depart_date and s.depart_date > date.today()), None)
            if not slot:
                continue
            try:
                r = check_room_availability(t.course_no, slot.date_raw)
            except RuntimeError as e:
                return [CheckResult("travel-answer 房間解析", False, str(e)[:80])]
            if r.nights:
                return [CheckResult(
                    "travel-answer 房間解析", True,
                    f"{t.title[:24]}… {slot.date_raw}：{len(r.nights)} 晚狀態")]
        return [CheckResult("travel-answer 房間解析", False, "找不到可測試的套裝方案")]


def _check_hut_links(sample: int = 6) -> list[CheckResult]:
    from .matcher import MountainDB

    huts = [h for m in MountainDB.load().mountains for h in m.huts]
    random.shuffle(huts)
    dead = []
    for h in huts[:sample]:
        try:
            r = httpx.head(h["booking_url"], follow_redirects=True, timeout=10,
                           headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code in (403, 405):
                r = httpx.get(h["booking_url"], follow_redirects=True, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code >= 400:
                dead.append(f"{h['name']}({r.status_code})")
        except httpx.HTTPError:
            dead.append(f"{h['name']}(連線失敗)")
    return [CheckResult(
        f"山屋連結抽查（{sample} 家）", not dead,
        "全部存活" if not dead else "失效：" + "、".join(dead))]


def run_doctor() -> tuple[list[CheckResult], bool]:
    """跑全部健檢。回傳（結果列表, 是否全過）。"""
    results: list[CheckResult] = []
    for fn in (_check_maitabi, _check_weather, _check_yamap,
               _check_travelanswer, _check_hut_links):
        try:
            results.extend(fn())
        except Exception as e:  # 健檢本身不可炸掉
            results.append(CheckResult(fn.__name__, False, f"例外：{e}"))
    return results, all(r.ok for r in results)
