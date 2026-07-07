"""Yamap 模範路線（モデルコース）資料抓取。

山岳頁面（yamap.com/mountains/{id}）是 Next.js SSR，__NEXT_DATA__ 內含
modelCourses：每條路線的距離、累積爬升/下降、標準行動時間、
コース定数（客觀體力負荷數值）與体力度（1〜10）。

コース定数（鹿屋体育大学 山本正嘉教授的公式）參考區間：
  〜19 輕鬆　20〜39 一般日帰り　40〜59 健腳日帰り／宜1泊
  60〜79 相當吃力（縱走級）　80〜 非常吃力（長程縱走）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) yama-cli/0.1"}


@dataclass
class ModelRoute:
    id: int
    name: str
    distance_m: int
    up_m: int
    down_m: int
    time_sec: int
    course_constant: int | None
    fitness_level: int | None  # 体力度 1-10
    difficulty_level: int | None  # 難易度 1-5
    stays: int  # 建議泊數（0=日帰り）

    @property
    def url(self) -> str:
        return f"https://yamap.com/model-courses/{self.id}"

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000, 1)

    @property
    def time_hm(self) -> str:
        h, m = divmod(self.time_sec // 60, 60)
        return f"{h}:{m:02d}"

    @property
    def schedule_label(self) -> str:
        """Yamap 的 stays 是「日程天數」：1=日帰り、2=1泊2日、3=2泊3日…"""
        if self.stays <= 1:
            return "日帰り"
        return f"{self.stays - 1}泊{self.stays}日"

    @property
    def fitness_label(self) -> str:
        """体力度（1〜10）的實務意義。"""
        f = self.fitness_level
        if f is None:
            return ""
        if f <= 3:
            return "適合日帰り"
        if f <= 5:
            return "建議住1晚以上"
        return "多日縱走體力"

    @property
    def constant_label(self) -> str:
        c = self.course_constant
        if c is None:
            return ""
        if c < 20:
            return "輕鬆"
        if c < 40:
            return "一般"
        if c < 60:
            return "健腳"
        if c < 80:
            return "吃力"
        return "極吃力"


def fetch_route_page(course_id: int, timeout: float = 20.0) -> ModelRoute | None:
    """抓取單一模範路線頁（yamap.com/model-courses/{id}，Nuxt SSR）。

    用於山岳頁沒收錄、但想額外顯示的路線（資料庫 yamap.extra_course_ids）。
    頁面沒有体力度與日程欄位，該兩項回傳 None／預設。
    """
    try:
        resp = httpx.get(
            f"https://yamap.com/model-courses/{course_id}",
            headers=_UA, timeout=timeout, follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    title_m = re.search(r"<title>([^<|]+?)の地図", resp.text)
    plain = re.sub(r"<[^>]+>", "|", resp.text)
    plain = re.sub(r"(\||\s)+", "|", plain)
    # 版面：コース定数|標準タイム|HH:MM|で算出|<等級>|<定数>|HH:MM|<距離>|km|<のぼり>|m
    cc_m = re.search(r"コース定数\|[^|]*\|[\d:]+\|[^|]*\|[^|]*\|(\d+)\|", plain)
    dist_m = re.search(r"距離\|([\d.]+)\|km\|のぼり\|(\d+)\|m\|くだり\|(\d+)\|m", plain)
    time_m = re.search(r"タイム\|(\d+):(\d+)", plain)
    if not dist_m:
        return None
    time_sec = 0
    if time_m:
        time_sec = int(time_m.group(1)) * 3600 + int(time_m.group(2)) * 60
    return ModelRoute(
        id=course_id,
        name=title_m.group(1).strip() if title_m else f"model-course {course_id}",
        distance_m=int(float(dist_m.group(1)) * 1000),
        up_m=int(dist_m.group(2)),
        down_m=int(dist_m.group(3)),
        time_sec=time_sec,
        course_constant=int(cc_m.group(1)) if cc_m else None,
        fitness_level=None,
        difficulty_level=None,
        stays=1,
    )


def fetch_model_routes(mountain_url: str, timeout: float = 20.0) -> list[ModelRoute]:
    """從 Yamap 山岳頁抓取模範路線清單（依コース定数升冪）。失敗回傳空列表。"""
    try:
        resp = httpx.get(mountain_url, headers=_UA, timeout=timeout,
                         follow_redirects=True)
        resp.raise_for_status()
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S
        )
        if not m:
            return []
        data = json.loads(m.group(1))
    except (httpx.HTTPError, ValueError):
        return []

    found: list = []

    def walk(o):
        if isinstance(o, dict):
            if "modelCourses" in o and isinstance(o["modelCourses"], list):
                found.extend(o["modelCourses"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    routes: dict[int, ModelRoute] = {}
    for c in found:
        try:
            ratings = c.get("ratings") or {}
            r = ModelRoute(
                id=int(c["id"]),
                name=c.get("name", ""),
                distance_m=int(c.get("distance") or 0),
                up_m=int(c.get("cumulativeUp") or 0),
                down_m=int(c.get("cumulativeDown") or 0),
                time_sec=int(c.get("courseTime") or 0),
                course_constant=c.get("courseConstant"),
                fitness_level=ratings.get("fitnessLevel"),
                difficulty_level=ratings.get("difficultyLevel"),
                stays=int(c.get("stays") or 0),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if r.distance_m > 0:
            routes.setdefault(r.id, r)
    return sorted(routes.values(), key=lambda r: (r.course_constant or 999))


def fetch_all_routes(yamap_info: dict, timeout: float = 20.0) -> list[ModelRoute]:
    """山岳頁路線 + 資料庫指定的額外路線（extra_course_ids），去重並依定数排序。"""
    routes = fetch_model_routes(yamap_info.get("mountain_url", ""), timeout)
    seen = {r.id for r in routes}
    for cid in yamap_info.get("extra_course_ids", []):
        if cid in seen:
            continue
        r = fetch_route_page(cid, timeout)
        if r:
            routes.append(r)
            seen.add(cid)
    return sorted(routes, key=lambda r: (r.course_constant or 999))
