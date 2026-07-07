"""Markdown 報告產生器：天氣 × 巴士 × 山屋 × 路線圖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .maitabi import MaitabiClient, Tour, TourDetail, filter_by_keywords
from .matcher import Mountain, MountainDB
from .weather import DayForecast, get_forecast, get_forecasts, rate_day
from .yamap import fetch_model_routes

_WEEKDAY = "一二三四五六日"

# 每份報告最多抓幾個 course 的詳細（含預約連結），控制請求量與速度
_MAX_DETAILS = 8
# 每個 course 最多列幾個近期出發日
_MAX_SLOTS = 6


def _parse_price(price: str) -> int:
    digits = "".join(c for c in price if c.isdigit())
    return int(digits) if digits else 10**9


def _wd(d: date) -> str:
    return _WEEKDAY[d.weekday()]


def _fmt_date(d: date) -> str:
    mark = "**" if d.weekday() >= 5 else ""
    return f"{mark}{d.month}/{d.day}({_wd(d)}){mark}"


def _weather_map(forecast: list[DayForecast], difficulty: str) -> dict[date, str]:
    return {fc.day: rate_day(fc, difficulty).grade for fc in forecast}


def _weather_table(forecast: list[DayForecast], difficulty: str) -> list[str]:
    lines = [
        "| 日期 | 天氣 | 氣溫 | 降雨 | 風速 | 適宜度 |",
        "|---|---|---|---|---|---|",
    ]
    for fc in forecast:
        s = rate_day(fc, difficulty)
        lines.append(
            f"| {_fmt_date(fc.day)} | {fc.summary} "
            f"| {fc.t_min:.0f}~{fc.t_max:.0f}°C | {fc.rain_prob}% "
            f"| {fc.wind_max:.0f}m/s | {s.grade} {s.score} |"
        )
    return lines


def _slot_line(
    detail: TourDetail,
    grades: dict[date, str],
    today: date,
    grade_offset: int = 0,
) -> str:
    """grade_offset：夜行往路的登山日是出發日+1，天氣標記需對應登山日。"""
    parts = []
    for slot in detail.reserves:
        d = slot.depart_date
        if d is None or d < today:
            continue
        grade = grades.get(d + timedelta(days=grade_offset), "")
        grade_s = f" {grade}" if grade else ""
        parts.append(f"{_fmt_date(d)}{grade_s} [{slot.status}]({slot.link})")
        if len(parts) >= _MAX_SLOTS:
            break
    return "、".join(parts) if parts else "（近期無可預約日）"


def _course_section(
    title: str,
    tours: list[Tour],
    details: dict[int, TourDetail],
    grades: dict[date, str],
    today: date,
    grade_offset: int = 0,
) -> list[str]:
    if not tours:
        return []
    lines = [f"### {title}", ""]
    for t in tours:
        d = details.get(t.course_no)
        lines.append(f"- **{t.title}** — {t.price}")
        if d:
            lines.append(f"  - 出發日：{_slot_line(d, grades, today, grade_offset)}")
            lines.append(f"  - [方案詳細]({d.detail_url})")
        else:
            lines.append(
                f"  - [方案詳細](https://bus.maitabi.jp/detail.html?course_no={t.course_no})"
            )
    lines.append("")
    return lines


def _pick_sample_days(month: int, today: date) -> list[int]:
    """抽樣日：涵蓋不同星期（部分 course 只在週五／週末開行）。

    從查詢起點起取最近的 週三、五、六、日 各一天。
    """
    year = today.year if month >= today.month else today.year + 1
    start = today if month == today.month else date(year, month, 1)
    picked: dict[int, int] = {}  # weekday -> day
    d = start
    while d.month == month and len(picked) < 4:
        if d.weekday() in (2, 4, 5, 6):  # 三五六日
            picked.setdefault(d.weekday(), d.day)
        d += timedelta(days=1)
    return sorted(picked.values()) or [start.day]


@dataclass
class BusData:
    """一座山在某月份的巴士方案結構化資料（CLI 與 bot 共用）。"""

    outbound: list[Tour] = field(default_factory=list)
    roundtrip: list[Tour] = field(default_factory=list)
    inbound: list[Tour] = field(default_factory=list)
    details: dict[int, TourDetail] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.outbound or self.roundtrip or self.inbound)


def fetch_bus_data(
    mountain: Mountain,
    client: MaitabiClient,
    month: int,
    today: date,
    max_details: int = _MAX_DETAILS,
) -> BusData:
    """解析山域→抽樣→抓 course→分組排序→抓 detail（含預約連結）。"""
    area_ids = client.resolve_area_ids(month, mountain.maitabi_area_names)
    if not area_ids:
        return BusData()

    courses: dict[str, Tour] = {}
    sample_days = _pick_sample_days(month, today)
    for aid in area_ids:
        for t in client.list_courses(month, aid, sample_days):
            courses.setdefault(t.course_cd, t)
    tours = list(courses.values())
    matched = filter_by_keywords(tours, mountain.maitabi_title_keywords)
    if matched:
        tours = matched

    data = BusData(
        outbound=[t for t in tours if t.direction == "去程"],
        roundtrip=[t for t in tours if t.direction == "來回"],
        inbound=[t for t in tours if t.direction == "回程"],
    )
    for group in (data.outbound, data.roundtrip, data.inbound):
        group.sort(key=lambda t: _parse_price(t.price))

    # 抓詳細（含各出發日預約連結），三組平均分配額度
    per_group = max(1, max_details // 3)
    for group in (data.outbound, data.roundtrip, data.inbound):
        for t in group[:per_group]:
            data.details[t.course_no] = client.get_tour_detail(t.course_no)
    return data


def build_mountain_report(
    mountain: Mountain,
    client: MaitabiClient,
    month: int | None = None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    month = month or today.month

    lines: list[str] = []
    m = mountain

    # 1. 概要
    lines += [
        f"# {m.name}（{m.elevation}m）登山規劃",
        "",
        f"- **山域**：{m.area_hint}",
        f"- **難度**：{m.difficulty}",
        f"- **登山口**：{'、'.join(m.trailheads)}",
        "",
    ]

    # 2. 天氣
    forecast = get_forecast(m.lat, m.lon, m.elevation)
    grades = _weather_map(forecast, m.difficulty)
    lines += [f"## 山頂天氣預報（16 天，標高 {m.elevation}m）", ""]
    lines += _weather_table(forecast, m.difficulty)
    lines += ["", "適宜度：◎ 適合、○ 尚可、△ 勉強、× 不建議（粗體為週末）", ""]

    # 3. 行程建議
    if m.itineraries:
        lines += ["## 行程建議", ""]
        for it in m.itineraries:
            hut = f"（住宿：{it['recommended_hut']}）" if it.get("recommended_hut") else ""
            lines.append(f"- **{it['days']}**{hut}：{it['plan']}")
        lines.append("")

    # 3.5 Yamap 模範路線（距離、爬升、コース定数＝客觀難度數值）
    routes = fetch_model_routes(m.yamap.get("mountain_url", "")) if m.yamap else []
    if routes:
        lines += [
            "## 路線資料（Yamap 模範路線）",
            "",
            "| 路線 | 距離 | 爬升 | 下降 | 標準時間 | コース定数 | 体力度 | 泊数 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in routes[:8]:
            cc = f"{r.course_constant}（{r.constant_label}）" if r.course_constant else "—"
            fl = f"{r.fitness_level}/10" if r.fitness_level else "—"
            lines.append(
                f"| [{r.name}]({r.url}) | {r.distance_km}km | ↑{r.up_m}m | ↓{r.down_m}m "
                f"| {r.time_hm} | {cc} | {fl} | {r.stays or '日帰り'} |"
            )
        lines += [
            "",
            "コース定数＝體力負荷指標：〜19 輕鬆、20〜39 一般、40〜59 健腳、"
            "60〜79 吃力、80〜 極吃力（標準時間不含休息）。",
            "",
        ]

    # 4. 巴士方案
    lines += [f"## 毎日あるぺん号 巴士方案（東京發，{month} 月）", ""]
    bus = fetch_bus_data(m, client, month, today)
    if bus.empty:
        lines += [f"{month} 月查無「{'、'.join(m.maitabi_area_names)}」方面的巴士方案。", ""]
    else:
        # 往路/往復為夜行：出發日晚上上車、隔天清晨開始爬，天氣標記對應出發日+1
        lines += _course_section(
            "去程（往路）", bus.outbound, bus.details, grades, today, grade_offset=1
        )
        lines += _course_section(
            "來回・套裝（往復，多含山屋住宿）",
            bus.roundtrip, bus.details, grades, today, grade_offset=1,
        )
        lines += _course_section("回程（復路）", bus.inbound, bus.details, grades, today)
        lines += [
            "出發日旁的 ◎○△× 為登山日（往路=出發翌日清晨抵達）的山頂天氣適宜度（僅 16 天內有資料）。",
            "點狀態文字（受付中等）可直接進入預約流程。"
            "[取消費規定](https://www.maitabi.jp/guide/QandA.php)",
            "",
        ]

    # 5. 山屋
    if m.huts:
        lines += [
            "## 山屋住宿",
            "",
            "| 山屋 | 標高 | 備註 | 預約 |",
            "|---|---|---|---|",
        ]
        for h in m.huts:
            phone = f"（{h['phone']}）" if h.get("phone") else ""
            lines.append(
                f"| {h['name']} | {h.get('elevation', '—')}m | {h.get('note', '')} "
                f"| [官網]({h['booking_url']}){phone} |"
            )
        lines += [
            "",
            "山屋空位需至各官網確認；上表「來回・套裝」方案可在訂巴士時一併預約部分山屋。",
            "",
        ]

    # 6. Yamap（各路線連結已列在上方「路線資料」表）
    ym = m.yamap
    if ym and ym.get("mountain_url"):
        lines += [
            "## Yamap 路線圖",
            "",
            f"- [山岳頁面（路線、活動日記、即時狀況）]({ym['mountain_url']})",
            "",
        ]

    lines.append(f"---\n報告產生：{today.isoformat()}（天氣與巴士資料為即時查詢）")
    return "\n".join(lines)


# -- weekend / best 排名 -----------------------------------------------------


def _has_bookable_outbound(
    client: MaitabiClient, m: Mountain, target: date
) -> bool | None:
    """target 當天（或前一晚夜行）是否有可預約的往路/往復方案。None = 查詢失敗。"""
    try:
        area_ids = client.resolve_area_ids(target.month, m.maitabi_area_names)
        for aid in area_ids:
            tours = client.search_tours(
                target.month, aid, day=target.day, max_pages=2
            )
            unavailable = ("満席", "受付終了", "キャンセル待ち", "催行中止")
            for t in tours:
                if t.direction in ("去程", "來回") and not any(
                    u in t.status for u in unavailable
                ):
                    return True
        return False
    except RuntimeError:
        return None


def build_ranking_report(
    db: MountainDB,
    client: MaitabiClient | None,
    target_days: list[date],
    title: str,
    today: date | None = None,
) -> str:
    """依 target_days 的天氣適宜度為所有山排名；client 非 None 時附巴士可預約標記。

    夜行巴士玩法：出發日為登山日前一晚，故巴士查 target 前一天。
    """
    today = today or date.today()
    mountains = db.mountains
    all_fc = get_forecasts([(m.lat, m.lon, m.elevation) for m in mountains])

    rows = []
    for m, fcs in zip(mountains, all_fc):
        day_scores = []
        for d in target_days:
            fc = next((f for f in fcs if f.day == d), None)
            if fc is None:
                continue
            day_scores.append((d, fc, rate_day(fc, m.difficulty)))
        if not day_scores:
            continue
        best_day, best_fc, best_s = max(day_scores, key=lambda x: x[2].score)
        avg = sum(s.score for _, _, s in day_scores) / len(day_scores)

        bus = None
        if client is not None:
            bus_day = best_day - timedelta(days=1)  # 夜行巴士前一晚出發
            bus = _has_bookable_outbound(client, m, bus_day)

        rows.append((m, best_day, best_fc, best_s, avg, bus))

    rows.sort(key=lambda r: (-r[3].score, -r[4]))

    lines = [
        f"# {title}",
        "",
        "| 排名 | 山 | 標高 | 難度 | 最佳日 | 天氣 | 適宜度 | 巴士 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, (m, best_day, fc, s, _avg, bus) in enumerate(rows, 1):
        bus_s = {True: "✅ 可預約", False: "❌ 無班次", None: "—"}[bus]
        lines.append(
            f"| {i} | **{m.name}** | {m.elevation}m | {m.difficulty} "
            f"| {_fmt_date(best_day)} | {fc.summary} {fc.t_min:.0f}~{fc.t_max:.0f}°C "
            f"雨{fc.rain_prob}% | {s.grade} {s.score} | {bus_s} |"
        )
    lines += [
        "",
        "巴士 = 該登山日前一晚有東京發、可預約的往路/往復方案（夜行）。",
        "詳細規劃請執行：`yama <山名>`",
        "",
        f"---\n報告產生：{today.isoformat()}",
    ]
    return "\n".join(lines)
