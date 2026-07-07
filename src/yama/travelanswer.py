"""travel-answer.ne.jp（毎日あるぺん号預約系統）房間空位查詢。

背景：maitabi API 的「受付中／催行決定」是方案（巴士）層級的狀態；
含山屋的套裝方案，房間庫存要走到預約流程第二步（宿泊先確認頁）才看得到，
常發生「巴士有位但房間已滿」。

本模組模擬預約流程的前兩步（選房數/人數 → 宿泊先確認頁）並解析空き状況表，
不會建立任何預約（流程在確認頁即中止）。

空位符號：○=有空位、數字=剩餘數、RQ=リクエスト受付、WT=キャンセル待ち、×=受付不可
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import httpx

_BASE = "https://www.travel-answer.ne.jp"
_NINZU = _BASE + "/vstour/web/web_tour4_ninzu.aspx"
_UA = {"User-Agent": "yama-cli/0.1 (personal hiking planner)"}

STATUS_LEGEND = {
    "○": "有空位",
    "RQ": "請求受理（需等旅行社確認）",
    "WT": "候補（キャンセル待ち）",
    "×": "受付不可（已滿）",
}


@dataclass
class NightAvailability:
    night: str  # 例：1泊目 7/10(金)
    facility: str  # 例：毎日あるぺん号（車中泊）／雷鳥荘
    room_type: str  # 例：車中泊／相部屋
    status: str  # ○ / 數字 / RQ / WT / ×

    @property
    def ok(self) -> bool:
        return self.status not in ("×",)

    @property
    def label(self) -> str:
        if self.status.isdigit():
            return f"剩 {self.status} 位"
        return STATUS_LEGEND.get(self.status, self.status)


@dataclass
class RoomAvailability:
    course_no: int
    course_cd: str
    title: str
    depart_date: str
    nights: list[NightAvailability]

    @property
    def all_ok(self) -> bool:
        return bool(self.nights) and all(n.ok for n in self.nights)


def _hidden(html: str, name: str) -> str:
    m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def check_room_availability(
    course_no: int, depart_date: str, rooms: int = 1, adults: int = 1
) -> RoomAvailability:
    """查詢套裝方案某出發日的逐晚住宿空位。

    depart_date 格式：YYYY/MM/DD 或 YYYY-MM-DD。
    """
    depart_date = depart_date.replace("-", "/")
    params = {
        "p_from": "1000460",
        "p_company_cd": "1000460",
        "p_course_no": str(course_no),
        "p_date": depart_date,
    }
    with httpx.Client(headers=_UA, timeout=30, follow_redirects=True) as c:
        r1 = c.get(_NINZU, params=params)
        html = r1.content.decode("cp932", errors="replace")

        cd_m = re.search(r'id="Ctrl_hed21_lbCourseNo">([^<]+)<', html)
        title_m = re.search(r'id="Ctrl_hed21_lbCourseName"[^>]*>([^<]+)<', html)

        # 房型列（可能多列：dgRoom:_ctl2, _ctl3...）。每列有人數範圍標示，
        # 例：相部屋利用(1 〜 1人)、2～3名1室、4～8名1室。
        # 探測策略：挑最低人數需求的房型訂 1 室，人數設為該房型下限。
        rows: list[tuple[str, str, int]] = []  # (select_name, code, min_persons)
        for m in re.finditer(
            r'name="(dgRoom:[^"]*dgddRoom)"(.*?)</select>', html, re.S
        ):
            sel, block = m.group(1), m.group(2)
            code_m = re.search(r'value="(\d+)\*', block)
            code = code_m.group(1) if code_m else "01"
            # 範圍標示在 select 之前的同一列文字裡
            prefix = html[: m.start()]
            range_m = None
            for range_m in re.finditer(
                r"(\d+)(?:&nbsp;|\s)*[〜～~](?:&nbsp;|\s)*(\d+)\s*[名人]", prefix
            ):
                pass  # 取最後一個（最接近該 select）
            min_p = int(range_m.group(1)) if range_m else 1
            rows.append((sel, code, min_p))
        if not rows:
            raise RuntimeError(
                f"course {course_no} 的預約頁沒有房型選單（可能非套裝方案或頁面改版）"
            )
        rows.sort(key=lambda r: r[2])
        chosen_sel, _, min_p = rows[0]
        adults = max(adults, min_p)

        data: dict = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _hidden(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(html, "__VIEWSTATEGENERATOR"),
            "dlNinzuA": str(adults),
            "dlNinzuC": "0",
            "dlNinzuI": "0",
            "imgBtnNext.x": "10",
            "imgBtnNext.y": "10",
        }
        for sel, code, _ in rows:
            data[sel] = f"{code}*{rooms if sel == chosen_sel else 0}"
        for name in re.findall(r'name="(hd\w+)"', html):
            data.setdefault(name, _hidden(html, name))

        r2 = c.post(_NINZU, params=params, data=data)
        body = r2.content.decode("cp932", errors="replace")

    if "web_fit2_htl" not in str(r2.url) and "空き状況" not in body:
        raise RuntimeError(
            f"course {course_no} {depart_date}：未到達宿泊先確認頁"
            "（可能該日不可預約或人數/房數不符）"
        )

    # 解析空き状況表：列格式 = N泊目 | 日期 | 都道府縣 | 設施 | 房型 | 狀態
    text = re.sub(r"<[^>]+>", "|", body)
    text = re.sub(r"(\||&nbsp;|\s)+", "|", text)
    nights: list[NightAvailability] = []
    for m in re.finditer(
        r"(\d泊目)\|([\d/]+\([^)]+\))\|(?:[^|]*\|)?([^|]+)\|([^|]+)\|(○|×|RQ|WT|\d+)\|",
        text,
    ):
        nights.append(
            NightAvailability(
                night=f"{m.group(1)} {m.group(2)}",
                facility=m.group(3),
                room_type=m.group(4),
                status=m.group(5),
            )
        )
    return RoomAvailability(
        course_no=course_no,
        course_cd=cd_m.group(1) if cd_m else "",
        title=title_m.group(1) if title_m else "",
        depart_date=depart_date,
        nights=nights,
    )


def sweep_weekend_rooms(
    course_no: int, year: int, month: int, adults: int = 1,
    include_fridays: bool = False,
) -> list[RoomAvailability]:
    """掃描整月週末（可含週五）的房間空位。提前預約推薦前必跑——
    巴士「受付中」不代表房間有位（雷鳥荘 2026-08 全週末滿房事件）。"""
    import calendar
    import time as _time

    out = []
    weekdays = (4, 5, 6) if include_fridays else (5, 6)
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        if d.weekday() not in weekdays or d <= date.today():
            continue
        try:
            out.append(check_room_availability(course_no, d.isoformat(), adults=adults))
        except RuntimeError:
            continue
        _time.sleep(0.4)
    return out
