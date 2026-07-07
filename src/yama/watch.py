"""監控：空房釋出與天氣窗出現時主動通知。

監控項存於 ~/.yama_cache/watches.json，`yama watch run` 檢查全部項目，
與上次狀態比較，變化時通知。設計給 cron / launchd 定期執行：

    # crontab -e：每小時檢查一次
    0 * * * * /path/to/uv run --directory /path/to/hiking yama watch run

通知管道（依可用性疊加）：
- stdout（一定有）
- macOS 通知中心（osascript，macOS 限定）
- LINE push（設定 LINE_CHANNEL_ACCESS_TOKEN 與 LINE_USER_ID 環境變數時；
  注意免費額度每月 200 則）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import httpx

_STATE_DIR = Path.home() / ".yama_cache"
_WATCH_FILE = _STATE_DIR / "watches.json"

_ROOM_OK = ("○", "RQ", "WT")  # × 以外都視為「出現機會」


@dataclass
class Watch:
    id: int
    type: str  # room | weather
    # room
    course_no: int | None = None
    depart_date: str | None = None
    party: int = 1
    # weather
    mountain: str | None = None
    min_score: int = 75  # ◎
    consecutive_days: int = 1
    # 狀態
    last_state: str = ""
    created: str = field(default_factory=lambda: date.today().isoformat())


def _load() -> list[Watch]:
    try:
        data = json.loads(_WATCH_FILE.read_text())
        return [Watch(**w) for w in data]
    except (OSError, ValueError, TypeError):
        return []


def _save(watches: list[Watch]) -> None:
    _STATE_DIR.mkdir(exist_ok=True)
    _WATCH_FILE.write_text(
        json.dumps([asdict(w) for w in watches], ensure_ascii=False, indent=1)
    )


def add_room_watch(course_no: int, depart_date: str, party: int = 1) -> Watch:
    watches = _load()
    w = Watch(
        id=max((x.id for x in watches), default=0) + 1,
        type="room", course_no=course_no,
        depart_date=depart_date.replace("/", "-"), party=party,
    )
    watches.append(w)
    _save(watches)
    return w


def add_weather_watch(mountain: str, min_score: int = 75,
                      consecutive_days: int = 1) -> Watch:
    watches = _load()
    w = Watch(
        id=max((x.id for x in watches), default=0) + 1,
        type="weather", mountain=mountain,
        min_score=min_score, consecutive_days=consecutive_days,
    )
    watches.append(w)
    _save(watches)
    return w


def list_watches() -> list[Watch]:
    return _load()


def remove_watch(watch_id: int) -> bool:
    watches = _load()
    kept = [w for w in watches if w.id != watch_id]
    _save(kept)
    return len(kept) < len(watches)


def describe(w: Watch) -> str:
    if w.type == "room":
        return f"#{w.id} 房間：course {w.course_no}／{w.depart_date} 出發／{w.party} 人"
    return (f"#{w.id} 天氣：{w.mountain}／適宜度 ≥{w.min_score}"
            f"／連續 {w.consecutive_days} 天")


# -- 檢查 --------------------------------------------------------------------


def _check_room(w: Watch) -> tuple[str, str, bool]:
    """回傳 (狀態指紋, 人話描述, 是否為好消息)。"""
    from .travelanswer import check_room_availability

    r = check_room_availability(w.course_no, w.depart_date, adults=w.party)
    sig = "|".join(n.status for n in r.nights)
    hut = next((n for n in r.nights if "あるぺん号" not in n.facility), None)
    if r.all_ok and hut:
        desc = (f"🎉 有機會了！{r.title}（{w.depart_date} 出發）"
                f"{hut.facility} {hut.room_type}：{hut.status}（{hut.label}）"
                f"\n預約：https://www.travel-answer.ne.jp/vstour/web/"
                f"web_tour4_ninzu.aspx?p_from=1000460&p_company_cd=1000460"
                f"&p_course_no={w.course_no}&p_date={w.depart_date.replace('-', '/')}")
        return sig, desc, True
    return sig, f"{r.title or w.course_no}：房間仍為 " + sig, False


def _check_weather(w: Watch) -> tuple[str, str, bool]:
    from .matcher import MountainDB
    from .weather import get_forecast, rate_day

    m = MountainDB.load().find(w.mountain)
    if m is None:
        return "not-found", f"{w.mountain} 不在資料庫", False
    days = []
    for fc in get_forecast(m.lat, m.lon, m.elevation):
        r = rate_day(fc, m.difficulty)
        days.append((fc.day, r.score, r.grade))
    # 找連續 N 天 ≥ min_score 的窗口
    windows = []
    run: list = []
    for d, score, grade in days:
        if score >= w.min_score and d > date.today():
            run.append((d, grade, score))
            if len(run) >= w.consecutive_days:
                windows.append(list(run))
        else:
            run = []
    if windows:
        first = windows[0][:3]
        span = "、".join(
            f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]}){g}{s}"
            for d, g, s in first)
        sig = ",".join(d.isoformat() for win in windows for d, _, _ in win[:1])
        return sig, f"🌤 {m.name} 出現天氣窗：{span}（可用 yama go {m.name} 成案）", True
    return "none", f"{m.name}：16 天內尚無 ≥{w.min_score} 的窗口", False


def _notify(title: str, message: str) -> None:
    print(f"[通知] {title}｜{message}")
    # macOS 通知中心
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=10, check=False,
            )
        except OSError:
            pass
    # LINE push（可選）
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if token and user_id:
        try:
            httpx.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}"},
                json={"to": user_id,
                      "messages": [{"type": "text",
                                    "text": f"{title}\n{message}"}]},
                timeout=15,
            )
        except httpx.HTTPError:
            pass


def run_checks(notify_unchanged: bool = False) -> list[str]:
    """檢查所有監控項；狀態變化（或首次）時通知。回傳本輪結果描述。"""
    watches = _load()
    results = []
    today = date.today().isoformat()
    for w in watches:
        # 過期的房間監控自動清掉
        if w.type == "room" and w.depart_date and w.depart_date < today:
            results.append(f"{describe(w)} → 已過期，自動移除")
            continue
        try:
            sig, desc, good = (_check_room if w.type == "room" else _check_weather)(w)
        except Exception as e:  # 監控不可讓單項錯誤中斷整輪
            results.append(f"{describe(w)} → 檢查失敗：{e}")
            continue
        improved = good and sig != w.last_state
        if improved:
            _notify("yama 監控", desc)
        elif notify_unchanged:
            print(desc)
        w.last_state = sig
        results.append(desc)
    _save([w for w in watches
           if not (w.type == "room" and w.depart_date and w.depart_date < today)])
    return results
