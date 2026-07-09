"""山屋空位查詢的統一介面：多 adapter dispatch。

山屋預約系統各家不同（調查見 hut_survey），每個系統一個小 adapter，
在此以 `avail` 設定 dispatch：mountains.json 的 hut 帶
  {"avail": {"adapter": "<名稱>", "id": "<系統內識別>"}}
（yamatan 沿用既有 yamatan_id 欄位，向下相容。）

統一輸出：逐日 × 房型的狀態標記。
  ok=True 的狀態：○、△、殘數 ≥1、RQ 等「還有機會」；
  ok=False：×、満室、休、問、直前不可、非公開。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

_OK_MARKS = ("○", "△", "問")
_NG_MARKS = ("×", "満", "休", "直前不可", "-", "")


@dataclass
class RoomStatus:
    room: str
    status: str  # ○/△/×/休/問/残N/直前不可…

    @property
    def ok(self) -> bool:
        s = self.status
        if s.startswith("残"):
            return not s.startswith("残0")
        if any(s.startswith(m) for m in _OK_MARKS):
            return True
        return False


@dataclass
class DayStatus:
    day: date
    rooms: list[RoomStatus]
    note: str = ""  # 休業/非營業期間等

    @property
    def ok(self) -> bool:
        return any(r.ok for r in self.rooms)

    @property
    def summary(self) -> str:
        if self.note:
            return self.note
        if not self.rooms:
            return "無資料"
        oks = [r for r in self.rooms if r.ok]
        if not oks:
            return "満室"
        return "、".join(f"{r.room[:10]}{r.status}" for r in oks[:4])


_REGISTRY: dict[str, Callable[[str, int, int], list[DayStatus]]] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def hut_adapter_config(hut: dict) -> tuple[str, str] | None:
    """從 hut dict 取得 (adapter, id)；無對應回 None。"""
    if hut.get("yamatan_id"):
        return ("yamatan", hut["yamatan_id"])
    av = hut.get("avail")
    if av and av.get("adapter") in _REGISTRY:
        return (av["adapter"], av.get("id", ""))
    return None


def get_hut_availability(adapter: str, hut_id: str,
                         year: int, month: int) -> list[DayStatus]:
    if adapter not in _REGISTRY:
        raise RuntimeError(f"未知的山屋預約系統 adapter：{adapter}")
    return _REGISTRY[adapter](hut_id, year, month)


def booking_page(adapter: str, hut_id: str) -> str:
    from . import enzanso, tenawan, yamatan  # noqa: F401（觸發註冊）

    if adapter == "yamatan":
        return f"https://www.yamatan.net/hut/{hut_id}"
    if adapter == "tenawan":
        return f"https://www.tenawan.ne.jp/lodgment/rec/{hut_id}/pcr.asp"
    if adapter == "enzanso":
        return f"https://enzanso-reservation.jp/reserve/enz0010.php?p={hut_id}&type=10"
    if adapter == "hotaka":
        return "https://www.hotakadakesanso.com/reservation"
    return ""


def ensure_adapters_loaded() -> None:
    """匯入所有 adapter 模組以完成註冊。"""
    from . import enzanso, hotaka, tenawan  # noqa: F401
    from . import yamatan_bridge  # noqa: F401
