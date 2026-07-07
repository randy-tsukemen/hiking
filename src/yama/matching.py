"""路線 × 巴士方案的自動對應。

路線決定天數與進出點，天數與進出點決定該訂哪種方案：
  日帰り路線     → 夜行日帰り往復
  1泊2日（同口） → 山小屋セット往復／純巴士往復＋自訂山屋
  A進B出縱走     → 復路異口方案（注意回程上車點）
以前這層 join 只存在於回覆文字裡，這裡把它變成資料。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .maitabi import Tour
from .matcher import Mountain
from .report import BusData
from .yamap import ModelRoute

_HUT_HINT = re.compile(r"[荘館]|ヒュッテ|小屋|セット")
_EXIT_RE = re.compile(r"復路[/／]([^発〈（、]+)")


def plan_category(t: Tour, mountain: Mountain) -> str:
    """方案分類：夜行日帰り往復 / 山小屋セット / 異口縱走用 / 純巴士往復 / 單程。"""
    title = t.title
    if t.direction != "來回":
        return "單程"
    exit_m = _EXIT_RE.search(title)
    if exit_m and not any(th[:2] in exit_m.group(1) for th in mountain.trailheads):
        return "異口縱走用"
    if "日帰り" in title:
        return "夜行日帰り往復"
    if _HUT_HINT.search(title) and "お客様手配" not in title:
        return "山小屋セット"
    return "純巴士往復"


def route_tier(r: ModelRoute) -> str:
    """路線分層：輕鬆日帰り / 日帰り / 1泊2日 / 多日縱走。"""
    if r.stays >= 3 or (r.stays >= 2 and "縦走" in r.name):
        return "多日縱走"
    if r.stays == 2:
        return "1泊2日"
    if (r.course_constant or 99) < 20:
        return "輕鬆日帰り"
    return "日帰り"


# 各路線層級適用的方案分類與各分類上限（依序＝推薦順）。
# 含住宿的層級（1泊2日/多日縱走）保證山小屋セット出現——
# 一次訂齊巴士＋房間、還能用 yama rooms 實查空位。
TIER_TO_CATEGORIES: dict[str, list[tuple[str, int]]] = {
    "輕鬆日帰り": [("夜行日帰り往復", 2), ("純巴士往復", 1)],
    "日帰り": [("夜行日帰り往復", 2), ("純巴士往復", 1)],
    "1泊2日": [("山小屋セット", 2), ("純巴士往復", 1)],
    "多日縱走": [("異口縱走用", 2), ("山小屋セット", 1), ("純巴士往復", 1)],
}


@dataclass
class TierMatch:
    tier: str
    routes: list[ModelRoute] = field(default_factory=list)
    plans: list[tuple[str, Tour]] = field(default_factory=list)  # (分類, 方案)


def match_routes_to_plans(
    routes: list[ModelRoute], bus: BusData, mountain: Mountain,
    plans_per_tier: int = 3,
) -> list[TierMatch]:
    """把路線分層並對應到該層可訂的方案（每層最多 plans_per_tier 個，便宜優先）。"""
    categorized: dict[str, list[Tour]] = {}
    for t in bus.roundtrip:
        categorized.setdefault(plan_category(t, mountain), []).append(t)
    # 異口方案裡「含山屋」的排前面（縱走通常要住，含住宿優先）
    if "異口縱走用" in categorized:
        categorized["異口縱走用"].sort(
            key=lambda t: (not _HUT_HINT.search(t.title), _price(t)))

    out: list[TierMatch] = []
    for tier in ("輕鬆日帰り", "日帰り", "1泊2日", "多日縱走"):
        tier_routes = [r for r in routes if route_tier(r) == tier]
        if not tier_routes:
            continue
        m = TierMatch(tier=tier, routes=tier_routes)
        for cat, cap in TIER_TO_CATEGORIES[tier]:
            for t in categorized.get(cat, [])[:cap]:
                if len(m.plans) >= plans_per_tier + 1:
                    break
                m.plans.append((cat, t))
        out.append(m)
    return out


def _price(t: Tour) -> int:
    digits = "".join(c for c in t.price if c.isdigit())
    return int(digits) if digits else 10**9


def exit_stop(t: Tour) -> str | None:
    """異口方案的回程上車點（從標題解析）。"""
    m = _EXIT_RE.search(t.title)
    return m.group(1).strip() if m else None
