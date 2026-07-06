"""毎日あるぺん号（毎日新聞旅行）公開 API client。

API base: https://api.bus.maitabi.jp（無需認證，CORS 全開）
- /tour_course   : 該月的分類目錄（district=山域/登山口、stay=山小屋加購、style=往復型態）
- /tour_search   : 方案列表（每筆 = course × 出發日）
- /tour_detail   : 方案詳細（乘車資訊、行程、tour_reserve[] 各出發日的預約連結）
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import httpx

API_BASE = "https://api.bus.maitabi.jp"
DEPARTURE_TOKYO = 1
USER_AGENT = "yama-cli/0.1 (personal hiking planner; contact via github)"
_POLITE_INTERVAL = 0.3  # 對小型旅行社 API 的禮貌間隔（秒）

_DATE_RE = re.compile(r"(\d{4})年(\d{2})月(\d{2})日")


def _norm(s: str) -> str:
    """全形/半形與 ヶ/ケ 正規化，供名稱比對。"""
    s = unicodedata.normalize("NFKC", s)
    return s.replace("ヶ", "ケ").replace("が岳", "ケ岳").replace("ガ岳", "ケ岳")


def parse_ja_date(s: str) -> date | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


@dataclass
class Tour:
    course_no: int
    course_cd: str
    date_raw: str
    title: str
    price: str
    status: str

    @property
    def depart_date(self) -> date | None:
        return parse_ja_date(self.date_raw)

    @property
    def direction(self) -> str:
        """去程 / 回程 / 來回（依標題的〈往路〉〈復路〉往復 判斷）。"""
        t = self.title
        if "往復" in t:
            return "來回"
        if "往路" in t:
            return "去程"
        if "復路" in t:
            return "回程"
        return "其他"


@dataclass
class ReserveSlot:
    date_raw: str  # "2026-07-12"
    status: str  # 催行決定 / 受付中 ...
    price: str
    link: str  # travel-answer.ne.jp 直達預約連結

    @property
    def depart_date(self) -> date | None:
        try:
            return date.fromisoformat(self.date_raw)
        except ValueError:
            return None


@dataclass
class TourDetail:
    course_no: int
    course_cd: str
    title: str
    comments: str  # HTML：乘車地點、時刻等
    schedules: list[dict] = field(default_factory=list)
    tour_info: dict = field(default_factory=dict)
    reserves: list[ReserveSlot] = field(default_factory=list)

    @property
    def detail_url(self) -> str:
        return f"https://bus.maitabi.jp/detail.html?course_no={self.course_no}"


class MaitabiClient:
    def __init__(self, timeout: float = 15.0):
        self._http = httpx.Client(
            base_url=API_BASE,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: dict) -> dict:
        wait = _POLITE_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._http.get(path, params=params)
                self._last_request = time.monotonic()
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"maitabi API 請求失敗：{path} {params}") from last_err

    # -- 目錄 --------------------------------------------------------------

    def get_districts(self, month: int) -> dict[str, int]:
        """回傳該月的山域名稱 → area id 對照（tour_search 的 area 參數）。"""
        data = self._get(
            "/tour_course", {"departure": DEPARTURE_TOKYO, "month": month}
        )
        districts = data.get("pulldown_options", {}).get("district", [])
        return {
            d["name"]: int(d["id"])
            for d in districts
            if int(d.get("tour_count") or 0) > 0
        }

    def resolve_area_ids(self, month: int, name_patterns: list[str]) -> list[int]:
        """以名稱子字串比對解析 area id（id 可能隨季節變動，故不寫死）。"""
        districts = self.get_districts(month)
        ids: list[int] = []
        for name, area_id in districts.items():
            n = _norm(name)
            if any(_norm(p) in n for p in name_patterns):
                ids.append(area_id)
        return ids

    # -- 方案搜尋 ----------------------------------------------------------

    def search_tours(
        self,
        month: int,
        area_id: int,
        day: int | None = None,
        max_pages: int = 5,
    ) -> list[Tour]:
        """搜尋方案。每筆 = course × 出發日；指定 day 可縮小為單日的 course 清單。"""
        tours: list[Tour] = []
        page = 1
        while page <= max_pages:
            params: dict = {
                "departure": DEPARTURE_TOKYO,
                "month": month,
                "area": area_id,
                "page": page,
            }
            if day is not None:
                params["day"] = day
            data = self._get("/tour_search", params)
            batch = data.get("tour") or []
            for t in batch:
                tours.append(
                    Tour(
                        course_no=int(t["course_no"]),
                        course_cd=t["course_cd"],
                        date_raw=t["date"],
                        title=t["title"],
                        price=t["price"],
                        status=t["status"],
                    )
                )
            if len(tours) >= int(data.get("count") or 0) or not batch:
                break
            page += 1
        return tours

    def list_courses(
        self, month: int, area_id: int, sample_days: list[int]
    ) -> list[Tour]:
        """取得該山域的不重複 course 清單（以幾個代表日抽樣，去重 course_cd）。"""
        seen: dict[str, Tour] = {}
        for d in sample_days:
            for t in self.search_tours(month, area_id, day=d):
                seen.setdefault(t.course_cd, t)
        return list(seen.values())

    # -- 方案詳細 ----------------------------------------------------------

    def get_tour_detail(self, course_no: int) -> TourDetail:
        data = self._get("/tour_detail", {"course_no": course_no})
        reserves = [
            ReserveSlot(
                date_raw=r.get("date", ""),
                status=r.get("status", ""),
                price=r.get("price", ""),
                link=r.get("link", ""),
            )
            for r in data.get("tour_reserve") or []
        ]
        return TourDetail(
            course_no=course_no,
            course_cd=data.get("cource_no", ""),  # API 欄位名的拼字如此
            title=data.get("title", ""),
            comments=data.get("comments", ""),
            schedules=data.get("schedules") or [],
            tour_info=data.get("tour_info") or {},
            reserves=reserves,
        )


def filter_by_keywords(tours: list[Tour], keywords: list[str]) -> list[Tour]:
    """以標題關鍵字（登山口等）過濾方案。"""
    norm_keys = [_norm(k) for k in keywords]
    return [t for t in tours if any(k in _norm(t.title) for k in norm_keys)]
