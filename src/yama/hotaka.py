"""穂高岳山荘 adapter（涸沢〜奥穂線的山頂側山屋）。

自社預約系統與燕山荘グループ同供應商（sakura.ne.jp 上的 htk 系列），
重用 enz 系解析器。hut_id 固定 "10"（相部屋主日曆）。
"""

from __future__ import annotations

from .enzanso import fetch_enz_style
from .hut_avail import DayStatus, register


@register("hotaka")
def get_month(hut_id: str, year: int, month: int) -> list[DayStatus]:
    return fetch_enz_style(
        "https://hotakadakesanso.sakura.ne.jp/reserve/htk0020.php",
        hut_id or "10", year, month)
