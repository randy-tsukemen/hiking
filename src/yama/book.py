"""預約自動化（實驗性）：開瀏覽器自動走 Yamatan 預約流程到「確定前」一步。

原則：**絕不自動按下最終確定**——填到確認畫面就通知使用者，最後一下由人點。
（含信用卡欄位的頁面也一律停下，不代填、不儲存卡號。）

需要 playwright（dependency group `book`）與系統 Chrome：
    uv run --group book yama book --setup     # 首次：開視窗手動 Google 登入一次
    uv run --group book yama book 涸沢ヒュッテ 2026-10-10 --room 2名様

登入 session 存在 ~/.yama_cache/chrome-profile（獨立 profile，不碰日常 Chrome）。
Google 登入無法自動化（bot 偵測會擋帳密自動輸入），所以採持久 profile：
手動登入一次，之後每次執行都還在登入狀態。

住客資料放 ~/.yama_cache/booking_profile.json（--setup 會產生範本），
表單欄位用關鍵字啟發式配對（氏名/カナ/電話/メール…），沒把握的欄位留白給人補。
"""

from __future__ import annotations

import json
import re
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

_CACHE = Path.home() / ".yama_cache"
_PROFILE_DIR = _CACHE / "chrome-profile"
_GUEST_FILE = _CACHE / "booking_profile.json"
_SHOT_DIR = _CACHE / "book_logs"

_GUEST_TEMPLATE = {
    "姓": "", "名": "", "セイ": "", "メイ": "",
    "電話": "", "メール": "", "郵便番号": "", "住所": "",
    "年齢": "", "性別": "",
    "緊急連絡先氏名": "", "緊急連絡先電話": "",
}

# 欄位配對：key 出現在 input 的 name/placeholder/label 任一處就填對應值
_FIELD_KEYWORDS = {
    "姓": ["姓", "last", "family"],
    "名": ["名", "first", "given"],
    "セイ": ["セイ", "せい", "sei"],
    "メイ": ["メイ", "めい", "mei"],
    "電話": ["電話", "tel", "phone"],
    "メール": ["メール", "mail"],
    "郵便番号": ["郵便", "zip", "postal"],
    "住所": ["住所", "address"],
    "年齢": ["年齢", "age"],
    "緊急連絡先氏名": ["緊急連絡先氏名", "緊急連絡者"],
    "緊急連絡先電話": ["緊急連絡先電話", "緊急.*電話"],
}

_ADVANCE_RE = re.compile(r"次へ|進む|確認画面|入力内容.{0,4}確認|確認する")
_SECTIONS = ("大人", "小学生", "未就学児")
# プラン自動選擇的預設優先關鍵字（--plan 可覆寫）：先找二食付
_PLAN_PREF = ("2食", "夕食＋朝食", "夕朝食", "夕食")
_FINAL_RE = re.compile(r"確定|申し?込み|支払|同意して予約|予約する$")


class BookError(RuntimeError):
    pass


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BookError(
            "需要 playwright：請用 `uv run --group book yama book …` 執行")
    return sync_playwright


def _launch(p, headless: bool = False):
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(_PROFILE_DIR), channel="chrome", headless=headless,
        ignore_default_args=["--enable-automation"],
        # 不帶自動化痕跡（navigator.webdriver）——Google OAuth 會據此
        # 顯示「這個瀏覽器可能有安全疑慮」並拒絕登入
        args=["--disable-blink-features=AutomationControlled"],
        viewport=None)


def _logged_in(page) -> bool:
    """問 session API（未登入回 null）——DOM 是客戶端渲染，靠不住。

    必須先確認 page 在 yamatan.net 上：OAuth 過程中頁面在 Google 網域，
    相對路徑 fetch 會打到 Google 的 404 HTML，非空回應會造成誤判已登入。
    回應也必須是非空 JSON 物件才算數。
    """
    if not page.url.startswith("https://www.yamatan.net"):
        return False
    try:
        r = page.evaluate(
            "fetch('/api/auth/get-session',{credentials:'include'})"
            ".then(r=>r.text())")
        return bool(json.loads(r or "null"))
    except Exception:
        return False


def _session_user(page) -> str:
    try:
        s = json.loads(page.evaluate(
            "fetch('/api/auth/get-session',{credentials:'include'})"
            ".then(r=>r.text())") or "null") or {}
        u = s.get("user") or s
        return u.get("name") or u.get("email") or ""
    except Exception:
        return ""


def _shot(page, tag: str, echo) -> None:
    _SHOT_DIR.mkdir(parents=True, exist_ok=True)
    f = _SHOT_DIR / f"{datetime.now():%m%d-%H%M%S}-{tag}.png"
    try:
        page.screenshot(path=str(f))
        echo(f"  （截圖：{f}）")
    except Exception:
        pass


def setup(echo=print) -> None:
    """首次設定：開視窗讓使用者手動 Google 登入，並產生住客資料範本。"""
    if not _GUEST_FILE.exists():
        _GUEST_FILE.write_text(json.dumps(_GUEST_TEMPLATE,
                                          ensure_ascii=False, indent=1))
        echo(f"已建立住客資料範本，請編輯：{_GUEST_FILE}")
    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        ctx = _launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.yamatan.net/auth")
        if _logged_in(page):
            echo(f"✅ 已是登入狀態（{_session_user(page) or '帳號不明'}），設定完成")
            ctx.close()
            return
        echo("請在開啟的視窗完成登入（「Google で続ける」→ 選帳號），"
             "完成後本指令會自動偵測…")
        for _ in range(150):  # 最多等 5 分鐘
            page.wait_for_timeout(2000)
            try:
                if _logged_in(page):
                    echo(f"✅ 登入完成（{_session_user(page) or '帳號不明'}），"
                         "session 已保存，之後可直接 yama book")
                    ctx.close()
                    return
            except Exception:  # 登入過程中頁面跳轉會讓 locator 短暫失效
                continue
        raise BookError("等了 5 分鐘沒偵測到登入完成，請重跑 --setup")


def _load_guest() -> dict:
    try:
        return {k: str(v) for k, v in
                json.loads(_GUEST_FILE.read_text()).items() if str(v).strip()}
    except (OSError, ValueError):
        return {}


def _fill_fields(page, guest: dict, echo) -> None:
    """啟發式填表：只填有把握的欄位，其餘留白。"""
    for inp in page.locator("input[type='text'], input[type='tel'], "
                            "input[type='email'], input:not([type]), "
                            "textarea").all():
        try:
            if inp.input_value(timeout=500):
                continue  # 已有值（平台常自動帶會員資料）不覆寫
            hint = " ".join(filter(None, [
                inp.get_attribute("name") or "",
                inp.get_attribute("placeholder") or "",
                inp.get_attribute("id") or "",
                inp.evaluate(
                    "e => e.labels?.[0]?.textContent"
                    " ?? e.closest('label,dl,tr,div')?.textContent?.slice(0,60)"
                    " ?? ''"),
            ]))
        except Exception:
            continue
        for key, pats in _FIELD_KEYWORDS.items():
            if key in guest and any(re.search(pt, hint, re.I) for pt in pats):
                try:
                    inp.fill(guest[key], timeout=1500)
                    echo(f"  填入 {key}")
                except Exception:
                    pass
                break


def _ancestor_texts(el) -> list[str]:
    return el.evaluate(
        "e=>{let t=[],n=e;for(let i=0;i<7&&n;i++)"
        "{t.push(n.innerText||'');n=n.parentElement}return t}")


def _bump(dlg, want_section: str, gender: str, times: int, echo) -> None:
    """ゲスト計數器：+按鈕的祖先鏈須含指定區塊（大人…）且列首為 男/女。"""
    if times <= 0:
        return
    others = set(_SECTIONS) - {want_section}
    for b in dlg.locator("button[aria-label='increment']").all():
        chain = _ancestor_texts(b)
        row = next((x.strip() for x in chain
                    if x.strip().startswith(("男", "女"))), "")
        sec = next((x for x in chain
                    if any(k in x for k in _SECTIONS)), "")
        if (row.startswith(gender) and want_section in sec
                and not any(o in sec for o in others)):
            for _ in range(times):
                b.click()
            echo(f"  {want_section}（{gender}）×{times}")
            return
    echo(f"  ⚠️ 找不到 {want_section}（{gender}）的人數計數器，請手動補")


def _bump_option(dlg, keyword: str, times: int, echo) -> None:
    """選配計數器（朝弁当/昼弁当…）：列須含「個」，關鍵字只看近層祖先。

    不能掃到 modal 最外層——整個 modal 的文字含所有關鍵字，會誤中
    人數或泊数的計數器（實際發生過：朝弁当誤按泊数＋變成 2 泊）。
    """
    if times <= 0:
        return
    for b in dlg.locator("button[aria-label='increment']").all():
        chain = _ancestor_texts(b)
        row = next((x.strip() for x in chain if 0 < len(x.strip()) <= 15), "")
        if row.startswith(("男", "女")) or "泊" in row or "個" not in row:
            continue  # 人數/泊数計數器，或非數量列
        near = [x for x in chain[:5] if len(x) <= 300]
        if any(keyword in x for x in near):
            for _ in range(times):
                b.click()
            echo(f"  {keyword} ×{times}")
            return
    echo(f"  ⚠️ 找不到選配「{keyword}」，請手動補")


def _scroll_to_bottom(dlg) -> None:
    """把 modal 內的可捲容器捲到底——底部按鈕捲完才會啟用。"""
    dlg.evaluate("""d => {
        for (const e of [d, ...d.querySelectorAll('*')])
            if (e.scrollHeight > e.clientHeight + 20) { e.scrollTop = e.scrollHeight; return }
    }""")


def _handle_plan_modal(page, dlg, plan_kw: str | None,
                       men: int, women: int, opts: dict[str, int],
                       echo) -> None:
    """預約 modal：選プラン → 設人數 → 選配 → 捲到底 → 按「予約へ進む」。"""
    # プラン下拉用 name 精準抓——modal 可能還有已鎖定的個室下拉
    # （privateRoomId，由點選的日曆格帶入、disabled），不能抓「第一個 select」
    sel = dlg.locator("select[name*='planId']")
    sel = sel.first if sel.count() else None
    if sel is None:
        sel = next((s for s in dlg.locator("select").all()
                    if not s.is_disabled()), None)
    if sel is not None:
        options = [o for o in sel.locator("option").all_inner_texts()
                   if "選択してください" not in o]
        prefs = [plan_kw] if plan_kw else list(_PLAN_PREF)
        pick = next((o for kw in prefs for o in options if kw and kw in o),
                    options[0] if options else None)
        if pick is None:
            raise BookError("modal 裡沒有可選的プラン")
        sel.select_option(label=pick)
        echo(f"  プラン：{pick}")
        page.wait_for_timeout(500)  # 選プラン後會展開選配與料金
    _bump(dlg, "大人", "男", men, echo)
    _bump(dlg, "大人", "女", women, echo)
    for kw, n in opts.items():
        _bump_option(dlg, kw, n, echo)
    # 捲到底啟用底部按鈕；渲染慢時重捲重試（最多 ~3 秒）
    for _ in range(6):
        _scroll_to_bottom(dlg)
        page.wait_for_timeout(400)
        for b in dlg.locator("button").all():
            label = (b.inner_text() or "").strip()
            if _FINAL_RE.search(label):
                echo(f"  底部按鈕「{label}」是最終確定——停下交給人")
                return
            if _ADVANCE_RE.search(label) and not b.is_disabled():
                echo(f"  →「{label}」")
                b.click()
                page.wait_for_timeout(1500)
                # 只認預約失敗的 toast——頁面靜態文字常含「〜できません」
                err = page.locator("text=/予約できません|エラーが発生/")
                if err.count():
                    raise BookError(f"平台回應：{err.first.inner_text().strip()}")
                return
    echo("  ⚠️ 捲到底後仍找不到可按的下一步，請在視窗手動接手")


def _find_slot(page, stay: date, room: str | None):
    """日曆（FullCalendar）上找當日可點的房型連結；沒有回 None。"""
    cell = page.locator(f'td[data-date="{stay.isoformat()}"]')
    if cell.count() == 0:
        # 備援：改版離開 FullCalendar 時退回文字比對
        cell = None
        for c in page.locator("td, li, [class*='cell'], [class*='day']").all():
            try:
                txt = c.inner_text(timeout=500).strip()
            except Exception:
                continue
            m = re.match(r"(\d{1,2})\b", txt)
            if m and int(m.group(1)) == stay.day and "名様" in txt:
                cell = c
        if cell is None:
            return None
    else:
        cell = cell.first
    links = (cell.locator("a.fc-event", has_text=room) if room
             else cell.locator("a.fc-event"))
    for a in links.all():
        # 圖例：○空き ×満室or未開放 休定休 前＝予約開始前（未開賣佔位）
        if not a.inner_text().strip().startswith(("×", "前", "休", "満")):
            return a
    return None


def _wait_for_open(page, hut_slug: str, stay: date, room: str | None,
                   party: int, echo):
    """未開賣時待機到受付開始：API 輕量輪詢，有位就重載日曆回傳房型連結。

    已開賣仍沒位（＝満室）回 None 交呼叫端報錯；
    受付超過 12 小時後才開始的，提示改天再跑（前台程序活不了那麼久）。
    """
    from .yamatan import get_month_availability

    def api_day():
        try:
            return next((d for d in get_month_availability(
                hut_slug, stay.year, stay.month) if d.day == stay), None)
        except Exception:
            return None

    d = api_day()
    opens = d.opens_at if d else None
    if not opens or datetime.now() >= opens:
        return None
    if opens - datetime.now() > timedelta(hours=12):
        raise BookError(f"{stay} 的受付 {opens:%-m/%-d %H:%M} 才開始——"
                        f"開賣當天早上再跑本指令")
    echo(f"⏳ {stay} 尚未開賣（{opens:%H:%M} 受付開始）——"
         "瀏覽器待機中，開賣即自動搶（Ctrl-C 可中止）")
    while (r := (opens - datetime.now()).total_seconds()) > 15:
        if r > 120:
            echo(f"  距離開賣還有 {int(r // 60)} 分鐘…")
        _time.sleep(min(60.0, r - 15))
    deadline = opens + timedelta(minutes=10)
    echo(f"  開賣！開始重載日曆搶位（至 {deadline:%H:%M} 為止）…")
    # 不用 API 的殘量判斷——adapter 對個室（private_rooms 單位制）會低估，
    # 直接重載日曆找可點房型（與人手動 F5 等價、判斷與點擊同一來源）
    while datetime.now() < deadline:
        page.reload(wait_until="domcontentloaded")
        try:
            page.wait_for_selector("td[data-date]", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(600)
        t = _find_slot(page, stay, room)
        if t is not None:
            return t
        _time.sleep(2)
    return None


def book(hut_slug: str, hut_name: str, stay: date,
         room: str | None = None, plan: str | None = None,
         men: int = 1, women: int = 0,
         opts: dict[str, int] | None = None, echo=print) -> None:
    """自動走到確認畫面前：選日→選房→填表→停在最終確定前並通知。"""
    from .watch import _notify

    sync_playwright = _require_playwright()
    guest = _load_guest()
    if not guest:
        echo(f"⚠️ 住客資料 {_GUEST_FILE} 是空的，表單會留白給你手動填"
             "（先跑 `yama book --setup` 建範本）")

    with sync_playwright() as p:
        ctx = _launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://www.yamatan.net/hut/{hut_slug}/plan"
                  f"?year={stay.year}&month={stay.month:02d}",
                  timeout=30000, wait_until="domcontentloaded")
        try:  # 等日曆渲染（networkidle 在此站會被輪詢請求拖住，不可靠）
            page.wait_for_selector("td[data-date]", timeout=20000)
        except Exception:
            pass
        if not _logged_in(page):
            raise BookError("尚未登入 Yamatan——先跑 `yama book --setup` "
                            "在視窗裡用 Google 登入一次")

        target = _find_slot(page, stay, room)
        if target is None:
            # 未開賣的話待機到受付開始，開賣瞬間自動搶
            target = _wait_for_open(page, hut_slug, stay, room,
                                    max(1, men + women), echo)
        if target is None:
            _shot(page, "calendar", echo)
            raise BookError(f"{stay} 找不到可訂的房型"
                            f"{f'「{room}」' if room else ''}"
                            "（満室/非營業？先用 yama hut 確認）")
        echo(f"選擇 {stay} {target.inner_text().strip()}")
        target.click()
        try:
            page.wait_for_selector("[role='dialog']", timeout=8000)
            page.wait_for_timeout(400)
        except Exception:
            pass
        dlg = page.locator("[role='dialog']").last
        if dlg.count():
            _shot(page, "modal", echo)
            _handle_plan_modal(page, dlg, plan, men, women, opts or {}, echo)
            page.wait_for_timeout(1200)

        # 逐步推進：填表 → 按「次へ/確認」，遇到最終確定或卡片輸入欄就停
        for step in range(1, 7):
            page.wait_for_timeout(800)
            final_btns = [b for b in page.locator("button, input[type=submit]").all()
                          if _FINAL_RE.search((b.inner_text() or
                                               b.get_attribute("value") or ""))]
            if final_btns:
                # 同意條款的勾選框先勾好（最終的「同意して予約」仍由人按）
                for cb in page.locator("input[type='checkbox']").all():
                    try:
                        lbl = cb.evaluate(
                            "e => e.labels?.[0]?.textContent"
                            " ?? e.closest('label,div')?.textContent ?? ''")
                        if "同意" in lbl and not cb.is_checked():
                            cb.check()
                            echo("  已勾選同意條款")
                    except Exception:
                        pass
                _fill_fields(page, guest, echo)
                _shot(page, f"step{step}-confirm", echo)
                _notify("yama book",
                        f"🎯 {hut_name} {stay} 已填到確認畫面！"
                        "請檢查內容後自行按下最後的確定鍵")
                break
            # 有卡號「輸入欄」才算付款頁——確認頁顯示已註冊卡號是純文字
            if page.locator("input[name*='card' i], input[autocomplete^='cc-'], "
                            "input[placeholder*='カード']").count():
                _shot(page, f"step{step}-card", echo)
                _notify("yama book", f"💳 {hut_name} {stay}：需要輸入卡片資訊，"
                        "請自行輸入（不代填）")
                break
            _fill_fields(page, guest, echo)
            # 捲到底：有些「下一步」要捲完才啟用
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            d2 = page.locator("[role='dialog']").last
            if d2.count():
                _scroll_to_bottom(d2)
            page.wait_for_timeout(800)
            _shot(page, f"step{step}", echo)
            adv = None
            for b in page.locator("button, input[type=submit], a").all():
                label = (b.inner_text() or b.get_attribute("value") or "").strip()
                if (_ADVANCE_RE.search(label) and not _FINAL_RE.search(label)
                        and not b.is_disabled()):
                    adv = b
                    break
            if adv is None:
                echo(f"  第 {step} 步找不到「次へ/確認」按鈕——"
                     "可能有必填欄位沒填到，請在視窗裡手動接手")
                _notify("yama book", f"{hut_name} {stay}：自動填表停在第 {step} 步，"
                        "請到瀏覽器視窗接手")
                break
            echo(f"  第 {step} 步 →「{adv.inner_text().strip() or '次へ'}」")
            adv.click()
            page.wait_for_timeout(1200)
        else:
            _notify("yama book", f"{hut_name} {stay}：步驟超過上限，請到視窗接手")

        echo("瀏覽器保持開啟，確認完直接在視窗操作；關閉視窗即結束本指令")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()
