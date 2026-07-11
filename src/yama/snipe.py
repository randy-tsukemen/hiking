"""開賣狙擊：等到 Yamatan 受付開始時刻，出現空位立即通知＋開啟預約頁。

只做查詢與通知，不自動送出預約——下訂仍由人在瀏覽器完成
（Yamatan 下訂需登入與住客個資，且本 repo 的原則是僅讀取）。

節奏：開賣前 60 秒進入密集輪詢（每 3 秒），命中或超時（預設開賣後 10 分鐘）
即停止。宿泊日已在受付中且超過開賣 1 小時的，只查一次並建議改用
`yama watch hut`（長期監控釋出用每小時 cron 就夠，不需要密集輪詢）。

用法：開賣時刻前先跑起來放著即可，例如想搶 10/10 的涸沢ヒュッテ
（1 個月前 08:00 開賣）就在 9/10 早上 7 點多執行：
    yama snipe 涸沢ヒュッテ 2026-10-10 --party 2
"""

from __future__ import annotations

import time as _time
import webbrowser
from datetime import date, datetime, timedelta

from .yamatan import HutDay, booking_url, get_month_availability


def _fetch_day(hut_slug: str, stay: date) -> HutDay | None:
    days = get_month_availability(hut_slug, stay.year, stay.month)
    return next((d for d in days if d.day == stay), None)


def _hits(day: HutDay, party: int) -> list[str]:
    return [f"{r.room}：{r.label}" for r in day.fits(party)]


def snipe(hut_slug: str, hut_name: str, stay: date, party: int = 1,
          open_browser: bool = True, timeout_min: int = 10,
          poll_seconds: float = 3.0,
          echo=print) -> bool:
    """回傳是否搶到（出現 ≥ party 的空位並已通知）。"""
    from .watch import _notify

    url = booking_url(hut_slug)
    day = _fetch_day(hut_slug, stay)
    if day is None or day.holiday or (not day.rooms and not day.opens_at):
        echo(f"{hut_name} {stay}：{day.status if day else '查無資料'}，無法狙擊")
        return False

    def found(hits: list[str]) -> bool:
        msg = (f"🎯 {hut_name} {stay} 有空位！" + "、".join(hits[:4])
               + f"\n馬上預約：{url}")
        _notify("yama 開賣狙擊", msg)
        if open_browser:
            webbrowser.open(url)
        return True

    opens = day.opens_at
    now = datetime.now()
    if opens is None or now > opens + timedelta(hours=1):
        # 早已開賣：密集輪詢等不到釋出，查一次就好
        hits = _hits(day, party)
        if hits:
            return found(hits)
        echo(f"{hut_name} {stay}：{day.status}（已開賣）。"
             f"等取消釋出請用 `yama hunt {hut_name}`（長駐輪詢）"
             f"或 `yama watch hut {hut_name} {stay}`（cron 每小時）")
        return False

    if now < opens - timedelta(hours=12):
        echo(f"{hut_name} {stay} 的受付 {opens:%-m/%-d %H:%M} 才開始——"
             f"開賣當天早上再執行本指令。等不及可先掛每小時監控：\n"
             f"  yama watch hut {hut_name} {stay}")
        return False
    if now < opens:
        echo(f"{hut_name} {stay} 的受付 {opens:%-m/%-d %H:%M} 開始，"
             f"開賣前 60 秒進入密集輪詢（Ctrl-C 可中止）")
        while (remain := (opens - datetime.now()).total_seconds()) > 60:
            echo(f"  距離開賣還有 {int(remain // 60)} 分鐘…")
            _time.sleep(min(300.0, remain - 60))

    deadline = opens + timedelta(minutes=timeout_min)
    echo(f"開始輪詢（每 {poll_seconds:g} 秒，至 {deadline:%H:%M} 為止）…")
    while datetime.now() < deadline:
        try:
            day = _fetch_day(hut_slug, stay)
        except Exception as e:  # 開賣瞬間平台常過載，失敗就下一輪再試
            echo(f"  查詢失敗（{e}），續試")
            day = None
        if day and not day.not_yet_open:
            hits = _hits(day, party)
            if hits:
                return found(hits)
            echo(f"  {datetime.now():%H:%M:%S} {day.status}")
        _time.sleep(poll_seconds)
    echo(f"超時：開賣後 {timeout_min} 分鐘內沒等到 ≥{party} 位的空位。"
         f"改掛長期監控：`yama watch hut {hut_name} {stay}`")
    return False
