"""自包含 HTML 圖表報告：天氣長條圖＋路線比較圖＋方案表。

設計沿用專案的儀表板視覺（湖水藍×琥珀、明朝體標題），
狀態色盤兩套（明/暗）皆通過色盲安全驗證：
  light: ◎#2e7d4f ○#1668b0 △#b07a24 ×#b3402e
  dark : ◎#3f9e6e ○#3b8fc9 △#bd8028 ×#d45f4a
圖表為 Python 生成的靜態 SVG，hover 提示用原生 <title>，零 JS。
"""

from __future__ import annotations

import html
from datetime import date, timedelta

from .maitabi import MaitabiClient
from .matcher import Mountain
from .report import fetch_bus_data
from .weather import get_forecast, rate_day
from .yamap import fetch_all_routes

_WD = "一二三四五六日"

_CSS = """
:root{--ground:#f6f7f5;--card:#fff;--ink:#24333a;--mist:#64777f;--faint:#e3e8e6;
--grid:#edf0ee;--lake:#22677d;--lake-soft:#e4eef1;--gold:#b07a24;--gold-soft:#f6ecd9;
--st-good:#2e7d4f;--st-ok:#1668b0;--st-warn:#b07a24;--st-bad:#b3402e}
@media(prefers-color-scheme:dark){:root{--ground:#131c1e;--card:#1b2629;--ink:#e2e9e8;
--mist:#93a5aa;--faint:#2b393d;--grid:#223034;--lake:#6db4c8;--lake-soft:#1e3138;
--gold:#d9a558;--gold-soft:#33291a;--st-good:#3f9e6e;--st-ok:#3b8fc9;--st-warn:#bd8028;--st-bad:#d45f4a}}
body{background:var(--ground);color:var(--ink);margin:0;padding:24px 14px 44px;
font-family:"Hiragino Sans","Yu Gothic","PingFang TC",sans-serif;line-height:1.7}
.wrap{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.panel{background:var(--card);border:1px solid var(--faint);border-radius:4px;padding:20px 24px}
h1{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:38px;font-weight:600;
letter-spacing:.06em;margin:0;line-height:1.15}
h1 small{font-size:15px;color:var(--mist);font-weight:500;margin-left:10px}
h2{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:18px;font-weight:600;margin:0 0 4px}
.eyebrow{font-size:11.5px;letter-spacing:.2em;color:var(--lake);font-weight:600;margin:0 0 6px}
.sub{font-size:12.5px;color:var(--mist);margin:0 0 12px}
.meta{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:13px;color:var(--mist)}
.meta b{color:var(--ink)}
svg.chart{width:100%;height:auto;display:block}
.grid{stroke:var(--grid)}.axis{fill:var(--mist);font-size:11px}
.axis.wk{fill:var(--ink);font-weight:700}.glyph{font-size:12px;font-weight:700}
.val{fill:var(--mist);font-size:11px}.rname{fill:var(--ink);font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{text-align:left;color:var(--mist);font-size:11.5px;letter-spacing:.05em;
border-bottom:1px solid var(--faint);padding:5px 8px 5px 0}
td{border-bottom:1px solid var(--faint);padding:7px 8px 7px 0;vertical-align:top}
tr:last-child td{border-bottom:none}.num{text-align:right}
.scroll{overflow-x:auto}
a{color:var(--lake)}
.chip{display:inline-block;font-size:11.5px;font-weight:700;padding:1px 9px;
border-radius:999px;background:var(--lake-soft);color:var(--lake);white-space:nowrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--mist);margin-top:8px}
.legend span::before{content:"";display:inline-block;width:10px;height:10px;
border-radius:2px;margin-right:5px;vertical-align:-1px}
.lg-good::before{background:var(--st-good)}.lg-ok::before{background:var(--st-ok)}
.lg-warn::before{background:var(--st-warn)}.lg-bad::before{background:var(--st-bad)}
.tier{margin:10px 0 2px;font-weight:700}
footer{font-size:12px;color:var(--mist);text-align:center}
"""

_GRADE_VAR = {"◎": "--st-good", "○": "--st-ok", "△": "--st-warn", "×": "--st-bad"}
_UNBOOKABLE = ("満席", "受付終了", "キャンセル待ち", "催行中止")


def _e(s: str) -> str:
    return html.escape(str(s), quote=True)


def _weather_svg(rows: list[dict]) -> str:
    n = len(rows)
    bw, gap, x0 = 34, 14, 44
    width = x0 + n * (bw + gap)
    parts = [f'<svg class="chart" viewBox="0 0 {width} 205" role="img" '
             f'aria-label="每日登山適宜度長條圖">']
    for y, lab in ((30, "100"), (95, "50"), (160, "0")):
        parts.append(f'<line class="grid" x1="{x0-10}" y1="{y}" x2="{width-6}" y2="{y}"/>'
                     f'<text class="val" x="{x0-14}" y="{y+4}" text-anchor="end">{lab}</text>')
    for i, r in enumerate(rows):
        x = x0 + i * (bw + gap)
        h = max(r["score"] * 1.3, 2)
        y = 160 - h
        c = f'var({_GRADE_VAR[r["grade"]]})'
        wk = ' wk' if r["weekend"] else ''
        tip = (f'{r["label"]} {r["summary"]}　{r["tmin"]}〜{r["tmax"]}°C　'
               f'雨{r["rain"]}%　適宜度{r["grade"]}{r["score"]}')
        parts.append(
            f'<g><title>{_e(tip)}</title>'
            f'<rect x="{x}" y="{y:.0f}" width="{bw}" height="{h:.0f}" rx="3" fill="{c}"/>'
            f'<text class="glyph" x="{x+bw/2}" y="{y-6:.0f}" text-anchor="middle" fill="{c}">{r["grade"]}</text>'
            f'<text class="axis{wk}" x="{x+bw/2}" y="176" text-anchor="middle">{r["md"]}</text>'
            f'<text class="axis{wk}" x="{x+bw/2}" y="190" text-anchor="middle">{r["wd"]}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def _routes_svg(routes) -> str:
    rows = routes[:10]
    rh, x0, plot_w = 36, 250, 520
    height = len(rows) * rh + 16
    cmax = max((r.course_constant or 0) for r in rows) or 1
    parts = [f'<svg class="chart" viewBox="0 0 {x0+plot_w+60} {height}" role="img" '
             f'aria-label="路線コース定数比較">']
    for i, r in enumerate(rows):
        y = 10 + i * rh
        w = (r.course_constant or 0) / cmax * plot_w
        name = r.name if len(r.name) <= 20 else r.name[:19] + "…"
        tip = (f'{r.name}　{r.distance_km}km ↑{r.up_m}m 標準{r.time_hm}　'
               f'定数{r.course_constant}（{r.constant_label}）{r.schedule_label}')
        parts.append(
            f'<g><title>{_e(tip)}</title>'
            f'<text class="rname" x="{x0-10}" y="{y+15}" text-anchor="end">{_e(name)}</text>'
            f'<rect x="{x0}" y="{y}" width="{w:.0f}" height="20" rx="3" fill="var(--lake)"/>'
            f'<text class="val" x="{x0+w+8:.0f}" y="{y+14}">{r.course_constant or "—"}・{r.time_hm}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(
    mountain: Mountain,
    client: MaitabiClient,
    month: int | None = None,
    today: date | None = None,
) -> str:
    m = mountain
    today = today or date.today()
    month = month or today.month
    month_filter = month if month != today.month else None

    # 資料
    forecast = get_forecast(m.lat, m.lon, m.elevation)
    wrows = []
    for fc in forecast:
        r = rate_day(fc, m.difficulty)
        wrows.append({
            "md": f"{fc.day.month}/{fc.day.day}", "wd": _WD[fc.day.weekday()],
            "label": f"{fc.day.month}/{fc.day.day}({_WD[fc.day.weekday()]})",
            "weekend": fc.day.weekday() >= 5, "summary": fc.summary,
            "tmin": round(fc.t_min), "tmax": round(fc.t_max),
            "rain": fc.rain_prob, "score": r.score, "grade": r.grade,
        })
    routes = fetch_all_routes(m.yamap) if m.yamap else []
    bus = fetch_bus_data(m, client, month, today)

    o = []
    o.append(f"<title>{_e(m.name)} 登山規劃｜{m.elevation}m</title>")
    o.append(f"<style>{_CSS}</style>")
    o.append('<div class="wrap">')

    # header
    o.append(
        f'<header class="panel"><p class="eyebrow">登山規劃報告</p>'
        f'<h1>{_e(m.name)}<small>{m.elevation}m</small></h1>'
        f'<div class="meta"><span>山域 <b>{_e(m.area_hint[:38])}</b></span>'
        f'<span>登山口 <b>{_e("、".join(m.trailheads[:2]))}</b></span>'
        f'<span>難度 <b>{_e(m.difficulty)}</b></span>'
        f'<span>資料 <b>{today.isoformat()} 查詢</b></span></div></header>')

    # weather
    o.append('<section class="panel"><h2>16 天登山適宜度</h2>'
             f'<p class="sub">山頂 {m.elevation}m・柱高＝分數・滑鼠移上看詳情</p>')
    o.append(_weather_svg(wrows))
    o.append('<div class="legend"><span class="lg-good">◎ 適合(75+)</span>'
             '<span class="lg-ok">○ 尚可(55+)</span><span class="lg-warn">△ 勉強(35+)</span>'
             '<span class="lg-bad">× 不建議</span></div></section>')

    # routes
    if routes:
        o.append('<section class="panel"><h2>路線難度比較（コース定数）</h2>'
                 '<p class="sub">柱長＝體力負荷：〜19輕鬆／20〜39一般／40〜59健腳／60〜79吃力</p>')
        o.append(_routes_svg(routes))
        o.append('<div class="scroll"><table><tr><th>路線</th><th class="num">距離</th>'
                 '<th class="num">爬升</th><th class="num">標準</th><th class="num">定数</th>'
                 '<th class="num">体力</th><th>日程</th></tr>')
        for r in routes[:10]:
            fl = f"{r.fitness_level}/10" if r.fitness_level else "—"
            o.append(f'<tr><td><a href="{_e(r.url)}">{_e(r.name[:30])}</a></td>'
                     f'<td class="num">{r.distance_km}km</td><td class="num">↑{r.up_m}m</td>'
                     f'<td class="num">{r.time_hm}</td><td class="num">{r.course_constant or "—"}</td>'
                     f'<td class="num">{fl}</td><td>{r.schedule_label}</td></tr>')
        o.append('</table></div></section>')

    # bus plans（含出發日與預約連結）
    if not bus.empty:
        o.append(f'<section class="panel"><h2>巴士方案與出發日（{month} 月）</h2>'
                 '<p class="sub">點日期直接進預約流程；套裝方案房間空位建議先以 yama rooms 確認</p>'
                 '<div class="scroll"><table><tr><th>方案</th><th class="num">價格</th>'
                 '<th>出發日（可預約）</th></tr>')
        groups = [("去程", bus.outbound), ("來回", bus.roundtrip), ("回程", bus.inbound)]
        for gname, tours in groups:
            for t in tours[:3]:
                d = bus.details.get(t.course_no)
                slots = []
                if d:
                    for s in d.reserves:
                        sd = s.depart_date
                        if sd is None or sd < today:
                            continue
                        if month_filter and sd.month != month_filter:
                            continue
                        if any(u in s.status for u in _UNBOOKABLE):
                            continue
                        wk = "（六日）" if sd.weekday() >= 5 else ""
                        slots.append(f'<a href="{_e(s.link)}" title="{_e(s.status)}">'
                                     f'{sd.month}/{sd.day}{wk}</a>')
                        if len(slots) >= 8:
                            break
                o.append(
                    f'<tr><td><span class="chip">{gname}</span> '
                    f'<a href="https://bus.maitabi.jp/detail.html?course_no={t.course_no}">'
                    f'{_e(t.title[:34])}</a></td>'
                    f'<td class="num">{_e(t.price)}</td>'
                    f'<td>{"、".join(slots) if slots else "—"}</td></tr>')
        o.append("</table></div></section>")

    # huts
    if m.huts:
        o.append('<section class="panel"><h2>山屋</h2><div class="scroll"><table>'
                 '<tr><th>山屋</th><th class="num">標高</th><th>備註</th><th>預約</th></tr>')
        for h in m.huts:
            phone = f"　{_e(h['phone'])}" if h.get("phone") else ""
            o.append(f'<tr><td><b>{_e(h["name"])}</b></td>'
                     f'<td class="num">{h.get("elevation", "—")}m</td>'
                     f'<td>{_e(h.get("note", ""))}</td>'
                     f'<td><a href="{_e(h["booking_url"])}">官網</a>{phone}</td></tr>')
        o.append("</table></div></section>")

    ym_url = (m.yamap or {}).get("mountain_url", "")
    o.append(f'<footer>路線圖 <a href="{_e(ym_url)}">Yamap</a>・'
             f'出發前確認 <a href="https://tenkura.n-kishou.co.jp/tk/">てんくら</a>・'
             f'<a href="https://www.maitabi.jp/guide/QandA.php">取消費規定</a><br>'
             f'資料查詢於 {today.isoformat()}，班次/房間以預約網站為準</footer>')
    o.append("</div>")

    body = "\n".join(o)
    return (f'<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'</head>\n<body>\n{body}\n</body>\n</html>')
