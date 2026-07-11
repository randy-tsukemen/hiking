"""釋出獵手：長駐輪詢山屋的多個目標日（預設所有週六），有位就行動。

與其他監控指令的分工：
- `yama watch hut`：單一日期、cron 每小時跑一次、只通知。
- `yama snipe`：開賣瞬間的密集輪詢（秒級）、單一日期。
- `yama hunt`（本模組）：**已開賣但満室**的多個日期，每 15〜30 分鐘
  掃一輪等取消釋出，命中即通知＋（預設）自動開瀏覽器走預約流程到
  最終確定前一步——最後一下仍由人點，絕不自動下訂。

用法：
    yama hunt 涸沢ヒュッテ --party 2                 # 盯所有已開賣的週六
    yama hunt 涸沢ヒュッテ --weekday 日 --interval 15
    uv run --group book yama hunt 涸沢ヒュッテ --party 2 --room 2名様
（--no-book 或未裝 playwright 時退回「通知＋開預約頁」。）

輪詢禮貌：每輪對每個涉及的月份只打 1 次 API（一輪通常 1〜3 個請求），
最短間隔 5 分鐘。
"""

from __future__ import annotations

import time as _time
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .yamatan import HutDay, booking_url, get_month_availability

_WEEKDAYS = "一二三四五六日"


def parse_weekday(text: str) -> int:
    """「六」「土」「sat」等 → weekday 數字（一=0…日=6）。"""
    t = text.strip().lower()
    aliases = {"土": 5, "sat": 5, "sun": 6, "日曜": 6, "土曜": 5}
    if t in aliases:
        return aliases[t]
    if t and t[0] in _WEEKDAYS:
        return _WEEKDAYS.index(t[0])
    raise ValueError(f"看不懂星期「{text}」（用 一〜日 或 土）")


@dataclass
class _DayState:
    sig: str = ""            # 上次觀測狀態（變化偵測用）
    reported_open: bool = False   # 未開賣提示只講一次
    acted: bool = False      # 已對此日開過預約流程（避免重複開視窗）
    hits: list[str] = field(default_factory=list)


def _month_keys(days: list[date]) -> list[tuple[int, int]]:
    return sorted({(d.year, d.month) for d in days})


def target_dates(weekday: int, today: date, horizon_days: int = 92) -> list[date]:
    """明天起 horizon 天內所有指定星期的日期。"""
    out = []
    d = today + timedelta(days=1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    while d <= today + timedelta(days=horizon_days):
        out.append(d)
        d += timedelta(days=7)
    return out


def _scan(hut_slug: str, targets: list[date],
          echo) -> dict[date, HutDay | None]:
    """一輪掃描：每個涉及月份打一次 API，回傳各目標日的 HutDay。"""
    found: dict[date, HutDay | None] = {t: None for t in targets}
    for y, m in _month_keys(targets):
        try:
            days = get_month_availability(hut_slug, y, m)
        except Exception as e:
            echo(f"  {y}-{m:02d} 查詢失敗（{e}），本輪跳過該月")
            continue
        for d in days:
            if d.day in found:
                found[d.day] = d
    return found


def _act(hut_slug: str, hut_name: str, stay: date, hits: list[str],
         auto_book: bool, book_kwargs: dict, echo) -> None:
    """命中：通知＋自動進預約流程（或退回開預約頁）。"""
    from .watch import _notify

    url = booking_url(hut_slug)
    _notify("yama 釋出獵手",
            f"🎯 {hut_name} {stay}（{_WEEKDAYS[stay.weekday()]}）有空位釋出！"
            + "、".join(hits[:4]) + f"\n{url}")
    if not auto_book:
        webbrowser.open(url)
        return
    try:
        from .book import BookError, book

        echo(f"  自動進入預約流程（走到最終確定前會停下）…")
        book(hut_slug, hut_name, stay, echo=echo, **book_kwargs)
    except Exception as e:  # BookError／playwright 未裝／流程中斷
        echo(f"  ⚠️ 自動預約流程沒走完（{e}），改開預約頁請手動操作")
        webbrowser.open(url)


def hunt(hut_slug: str, hut_name: str, *, weekday: int = 5, party: int = 1,
         interval_min: float = 30, auto_book: bool = True,
         book_kwargs: dict | None = None, horizon_days: int = 92,
         echo=print) -> None:
    """主迴圈：Ctrl-C 中止。auto_book 時命中會開瀏覽器走預約流程。"""
    interval_min = max(5.0, interval_min)
    states: dict[date, _DayState] = {}
    rnd = 0
    while True:
        rnd += 1
        today = date.today()
        targets = target_dates(weekday, today, horizon_days)
        if not targets:
            echo("範圍內沒有目標日期，結束")
            return
        observed = _scan(hut_slug, targets, echo)
        watchable, summary, already = 0, [], []
        for stay in targets:
            st = states.setdefault(stay, _DayState())
            day = observed[stay]
            if day is None:
                continue
            if day.holiday or not day.rooms:
                continue  # 休業/非營業：不列入
            if day.not_yet_open:
                if not st.reported_open:
                    st.reported_open = True
                    echo(f"  {stay}（{_WEEKDAYS[stay.weekday()]}）尚未開賣"
                         f"（{day.opens_at:%-m/%-d %H:%M} 開賣）——開賣要搶請另跑"
                         f" `yama snipe {hut_name} {stay}`")
                continue
            if day.past_deadline:
                continue
            watchable += 1
            fits = day.fits(party)
            hits = [f"{r.room}：{r.label}" for r in fits]
            sig = "|".join(hits)
            improved = bool(hits) and sig != st.sig
            st.sig = sig
            summary.append(f"{stay.month}/{stay.day}"
                           f"({_WEEKDAYS[stay.weekday()]})"
                           + ("✅" + hits[0] if hits else " " + day.status[:12]))
            if hits and rnd == 1:
                # 啟動時就有位的日期不需要「獵」——彙總告知，直接訂即可
                st.acted = True
                already.append(f"{stay}（{_WEEKDAYS[stay.weekday()]}）"
                               + "、".join(hits[:3]))
            elif improved and not st.acted:
                st.acted = True
                _act(hut_slug, hut_name, stay, hits,
                     auto_book, book_kwargs or {}, echo)
            elif improved:
                echo(f"  {stay} 狀態更新：{'、'.join(hits[:3])}")
            if not hits:
                st.acted = False  # 空位消失後重新武裝，下次釋出再行動
        if already:
            echo("💡 以下日期現在就有位，不用等釋出，直接訂："
                 f"{booking_url(hut_slug)}")
            for line in already:
                echo(f"   ・{line}")
        echo(f"[{datetime.now():%H:%M}] 第 {rnd} 輪：盯 {watchable} 個已開賣的"
             f"{_WEEKDAYS[weekday]}曜日｜" + "｜".join(summary[:8]))
        if watchable == 0 and rnd == 1:
            # 全部未開賣/不可訂時仍繼續輪（開賣後會自動納入監視）
            echo("（目前沒有已開賣的目標日；會持續輪詢，開賣後自動盯上）")
        _time.sleep(interval_min * 60)
