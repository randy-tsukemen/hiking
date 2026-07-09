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
    type: str  # room | weather | hut | hut
    # room
    course_no: int | None = None
    depart_date: str | None = None
    party: int = 1
    # hut（山屋官網/Yamatan）
    hut_name: str | None = None
    yamatan_id: str | None = None
    stay_date: str | None = None
    min_remaining: int = 1
    # weather
    mountain: str | None = None
    min_score: int = 75  # ◎
    consecutive_days: int = 1
    # hut（山屋官網直查）
    hut_url: str | None = None
    stay_date: str | None = None
    hut_name: str | None = None
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


def add_hut_watch(url: str, stay_date: str, name: str | None = None) -> Watch:
    from .hutdirect import check_hut, detect_provider

    if detect_provider(url) is None:
        raise ValueError("不支援的預約系統網址"
                         "（目前支援 tenawan.ne.jp / d-reserve.jp / yamatan.net）")
    stay = stay_date.replace("/", "-")
    if name is None:  # 加入時查一次，順便取得山屋名並記下目前狀態
        name = check_hut(url, stay).hut
    watches = _load()
    w = Watch(
        id=max((x.id for x in watches), default=0) + 1,
        type="hut", hut_url=url, stay_date=stay, hut_name=name,
    )
    watches.append(w)
    _save(watches)
    return w


def add_hut_watch(hut_name: str, yamatan_id: str, stay_date: str,
                  party: int = 1) -> Watch:
    watches = _load()
    w = Watch(
        id=max((x.id for x in watches), default=0) + 1,
        type="hut", hut_name=hut_name, yamatan_id=yamatan_id,
        stay_date=stay_date.replace("/", "-"), min_remaining=party,
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
    if w.type == "hut":
        return (f"#{w.id} 山屋官網：{w.hut_name}／{w.stay_date} 宿泊"
                f"／需 {w.min_remaining} 位")
    if w.type == "room":
        return f"#{w.id} 房間：course {w.course_no}／{w.depart_date} 出發／{w.party} 人"
    if w.type == "hut":
        return f"#{w.id} 山屋：{w.hut_name}／{w.stay_date} 泊"
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


def _check_hut(w: Watch) -> tuple[str, str, bool]:
    from .hutdirect import check_hut

    r = check_hut(w.hut_url, w.stay_date)
    sig = r.signature()
    open_rooms = [x for x in r.rooms if x.available]
    if open_rooms:
        lines = [f"🎉 {r.hut} {w.stay_date} 泊出現空位！"]
        lines += [f"・{x.room}：{x.status}" for x in open_rooms[:8]]
        lines.append(f"預約：{w.hut_url}")
        return sig, "\n".join(lines), True
    if not r.rooms:
        return sig, f"{r.hut}：{w.stay_date} 無販售中房型", False
    return sig, f"{r.hut}：{w.stay_date} 全房型仍滿房/停售", False


def _check_hut(w: Watch) -> tuple[str, str, bool]:
    from .hut_avail import (booking_page, ensure_adapters_loaded,
                            get_hut_availability)

    ensure_adapters_loaded()
    ref = w.yamatan_id or ""
    adapter, _, hid = ref.partition(":") if ":" in ref else ("yamatan", "", ref)
    d = date.fromisoformat(w.stay_date)
    days = get_hut_availability(adapter, hid, d.year, d.month)
    day = next((x for x in days if x.day == d), None)
    if day is None:
        return "no-data", f"{w.hut_name} {w.stay_date}：查無資料", False
    sig = day.summary
    if day.ok:
        return sig, (f"🏠 {w.hut_name} {w.stay_date} 官網有機會！{day.summary}"
                     f"\n預約 {booking_page(adapter, hid)}"), True
    return sig, f"{w.hut_name} {w.stay_date}：{day.summary}", False


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
        sig = ",".join(sorted({win[0][0].isoformat() for win in windows}))
        return sig, f"🌤 {m.name} 出現天氣窗：{span}", True
    return "none", f"{m.name}：16 天內尚無 ≥{w.min_score} 的窗口", False


def _with_plan(w: Watch, desc: str, sig: str) -> str:
    """天氣窗出現時附上該日的查證事實（不做選擇——判斷留給看通知的人/agent）。"""
    try:
        from .maitabi import MaitabiClient
        from .matcher import MountainDB
        from .planner import verify_candidate

        m = MountainDB.load().find(w.mountain)
        if m is None:
            return desc
        # sig 記錄的第一個窗口日
        first = sig.split(",")[0] if sig else None
        hike_day = date.fromisoformat(first) if first and first != "none" else None
        if hike_day is None:
            return desc
        with MaitabiClient() as client:
            v = verify_candidate(m, client, hike_day)
        lines = [f"\n{hike_day.isoformat()} 查證事實："]
        for p in v["roundtrip_plans"][:4]:
            room = ("房間" + ("✅" if p.get("rooms_all_ok") else "❌")
                    ) if "rooms" in p else ""
            lines.append(f"・[{p['category']}] {p['title'][:26]}… {p['price']} "
                         f"巴士:{p['bus_status']} {room}")
            if p.get("booking_url") and (p.get("rooms_all_ok") or "rooms" not in p):
                lines.append(f"  預約 {p['booking_url']}")
        return desc + "\n".join(lines)
    except Exception as e:  # 查證失敗不影響通知本身
        return desc + f"\n（自動查證失敗：{e}）"


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
    checkers = {"room": _check_room, "weather": _check_weather, "hut": _check_hut}

    def expired(w: Watch) -> bool:
        limit = w.depart_date if w.type == "room" else (
            w.stay_date if w.type == "hut" else None)
        return bool(limit and limit < today)

    for w in watches:
        # 過期的房間/山屋監控自動清掉
        if expired(w):
            results.append(f"{describe(w)} → 已過期，自動移除")
            continue
        try:
            sig, desc, good = checkers[w.type](w)
        except Exception as e:  # 監控不可讓單項錯誤中斷整輪
            results.append(f"{describe(w)} → 檢查失敗：{e}")
            continue
        improved = good and sig != w.last_state
        if improved:
            if w.type == "weather":
                desc = _with_plan(w, desc, sig)
            _notify("yama 監控", desc)
        elif notify_unchanged:
            print(desc)
        w.last_state = sig
        results.append(desc)
    _save([w for w in watches if not expired(w)])
    return results
