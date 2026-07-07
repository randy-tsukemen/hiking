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
