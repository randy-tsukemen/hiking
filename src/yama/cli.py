"""yama — 登山規劃 CLI。

用法：
  yama 燕岳                # 完整報告：天氣 + 巴士 + 山屋 + 路線圖
  yama 燕岳 --month 8      # 指定巴士查詢月份
  yama 燕岳 --out r.md     # 另存 Markdown
  yama weekend             # 這週末適合去哪些山（排名 + 巴士標記）
  yama best --days 14      # 未來 N 天最佳登山日排名
  yama list                # 列出收錄的山
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

from .maitabi import MaitabiClient
from .matcher import MountainDB
from .report import build_mountain_report, build_ranking_report

app = typer.Typer(
    add_completion=False,
    help="登山規劃：輸入山名，取得巴士方案、山屋、路線圖與天氣。",
)
console = Console()


def _output(md: str, out: Path | None) -> None:
    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"已輸出：{out}")
    else:
        console.print(Markdown(md))


def _next_weekend(today: date) -> list[date]:
    """回傳即將到來的週六、週日（若今天已是週末，取本週末剩餘日＋下週末）。"""
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    return [sat, sat + timedelta(days=1)]


@app.command("list")
def list_mountains() -> None:
    """列出資料庫收錄的山岳。"""
    db = MountainDB.load()
    lines = ["| 山 | 標高 | 難度 | 山域 |", "|---|---|---|---|"]
    for m in db.mountains:
        lines.append(f"| **{m.name}** | {m.elevation}m | {m.difficulty} | {m.area_hint} |")
    console.print(Markdown("\n".join(lines)))


@app.command("weekend")
def weekend(
    no_bus: bool = typer.Option(False, "--no-bus", help="跳過巴士查詢（較快）"),
    out: Path | None = typer.Option(None, "--out", help="輸出 Markdown 檔案"),
) -> None:
    """這週末適合去哪些山：全部山岳依天氣適宜度排名。"""
    db = MountainDB.load()
    today = date.today()
    days = _next_weekend(today)
    title = f"這週末（{days[0].month}/{days[0].day}–{days[1].month}/{days[1].day}）適合去哪些山？"
    with console.status("查詢天氣與巴士方案中…"):
        if no_bus:
            md = build_ranking_report(db, None, days, title, today)
        else:
            with MaitabiClient() as client:
                md = build_ranking_report(db, client, days, title, today)
    _output(md, out)


@app.command("best")
def best(
    days: int = typer.Option(14, "--days", "-d", min=1, max=16, help="往後看幾天"),
    no_bus: bool = typer.Option(False, "--no-bus", help="跳過巴士查詢（較快）"),
    out: Path | None = typer.Option(None, "--out", help="輸出 Markdown 檔案"),
) -> None:
    """未來 N 天內每座山的最佳登山日排名。"""
    db = MountainDB.load()
    today = date.today()
    targets = [today + timedelta(days=i) for i in range(1, days + 1)]
    title = f"未來 {days} 天最佳登山日排名"
    with console.status("查詢天氣與巴士方案中…"):
        if no_bus:
            md = build_ranking_report(db, None, targets, title, today)
        else:
            with MaitabiClient() as client:
                md = build_ranking_report(db, client, targets, title, today)
    _output(md, out)


watch_app = typer.Typer(help="監控空房與天氣窗，變化時通知（配合 cron 定期執行）")
app.add_typer(watch_app, name="watch")


@watch_app.command("room")
def watch_room(
    course_no: int = typer.Argument(..., help="方案編號"),
    depart: str = typer.Argument(..., help="出發日 YYYY-MM-DD"),
    party: int = typer.Option(1, "--party", "-p", min=1, max=8),
) -> None:
    """監控套裝方案的房間：滿房釋出（×→○/RQ/WT）時通知。"""
    from .watch import add_room_watch, describe

    w = add_room_watch(course_no, depart, party)
    console.print(f"已加入監控：{describe(w)}")
    console.print("用 `yama watch run` 檢查；排程請見 `yama watch run --help`")


@watch_app.command("weather")
def watch_weather(
    mountain: str = typer.Argument(..., help="山名"),
    score: int = typer.Option(75, "--score", help="適宜度門檻（75=◎、55=○）"),
    days: int = typer.Option(1, "--days", help="需要連續幾天"),
) -> None:
    """監控天氣窗：出現連續 N 天 ≥ 門檻的日子時通知。"""
    from .watch import add_weather_watch, describe

    w = add_weather_watch(mountain, score, days)
    console.print(f"已加入監控：{describe(w)}")


@watch_app.command("list")
def watch_list() -> None:
    """列出監控項。"""
    from .watch import describe, list_watches

    ws = list_watches()
    if not ws:
        console.print("（沒有監控項）")
    for w in ws:
        console.print(f"{describe(w)}｜上次狀態：{w.last_state or '—'}")


@watch_app.command("remove")
def watch_remove(watch_id: int = typer.Argument(...)) -> None:
    """移除監控項。"""
    from .watch import remove_watch

    console.print("已移除" if remove_watch(watch_id) else "找不到該編號")


@watch_app.command("run")
def watch_run(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="連未變化的也印出"),
) -> None:
    """檢查所有監控項，狀態好轉時通知（stdout＋macOS 通知＋可選 LINE push）。

    排程範例（每小時）：
      crontab -e 加入：
      0 * * * * cd /path/to/hiking && /opt/homebrew/bin/uv run yama watch run
    """
    from .watch import run_checks

    for line in run_checks(notify_unchanged=verbose):
        console.print(f"・{line}")


@app.command("doctor")
def doctor() -> None:
    """健檢所有外部資料來源（maitabi/天氣/Yamap/預約系統/山屋連結）。"""
    from .doctor import run_doctor

    with console.status("對各資料來源做真實查詢健檢中（約 30 秒）…"):
        results, all_ok = run_doctor()
    for r in results:
        mark = "[green]✅[/green]" if r.ok else "[red]❌[/red]"
        console.print(f"{mark} {r.name}：{r.detail}")
    if not all_ok:
        console.print("[red]有項目失敗——對應功能可能已靜默壞掉，請檢查來源是否改版[/red]")
        raise typer.Exit(1)
    console.print("[green]全部通過[/green]")


@app.command("rooms")
def rooms(
    course_no: int = typer.Argument(..., help="方案編號（預約連結中的 course_no）"),
    date: str = typer.Argument(..., help="出發日 YYYY-MM-DD，或 YYYY-MM 掃描整月週末"),
) -> None:
    """查詢套裝方案的住宿空位（巴士有位≠房間有位）。給月份則掃描該月全部週末。"""
    from .travelanswer import check_room_availability, sweep_weekend_rooms

    if len(date) == 7:  # YYYY-MM → 週末掃描
        y, mo = int(date[:4]), int(date[5:7])
        with console.status(f"掃描 {mo} 月週末房間空位中（約 30 秒）…"):
            results = sweep_weekend_rooms(course_no, y, mo)
        if not results:
            console.print("[red]該月無可查詢的週末出發日[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]{results[0].title}[/bold]")
        for r in results:
            hut = next((n for n in r.nights if "あるぺん号" not in n.facility), None)
            mark = "✅" if r.all_ok else "❌"
            wd = "一二三四五六日"[__import__("datetime").date.fromisoformat(r.depart_date.replace("/","-")).weekday()]
            console.print(f"  {r.depart_date}({wd}) {mark} {hut.status if hut else '?'}（{hut.label if hut else ''}）")
        return
    with console.status("查詢預約系統房間空位中…"):
        try:
            r = check_room_availability(course_no, date)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    lines = [
        f"# {r.title or f'course {course_no}'}（{r.depart_date} 出發）",
        "",
        "| 晚次 | 住宿 | 房型 | 空位 |",
        "|---|---|---|---|",
    ]
    for n in r.nights:
        mark = "✅" if n.ok else "❌"
        lines.append(f"| {n.night} | {n.facility} | {n.room_type} | {mark} {n.status}（{n.label}） |")
    lines += ["", "**全程可訂：" + ("✅ 可以" if r.all_ok else "❌ 不行（有晚次已滿）") + "**"]
    console.print(Markdown("\n".join(lines)))


@app.command("plan")
def plan(
    mountain: str = typer.Argument(..., help="山名（支援日文、假名、常見別名）"),
    month: int | None = typer.Option(None, "--month", "-m", min=1, max=12, help="巴士查詢月份（預設當月）"),
    out: Path | None = typer.Option(None, "--out", help="輸出 Markdown 檔案"),
    html: Path | None = typer.Option(None, "--html", help="輸出圖表版 HTML（可直接分享）"),
) -> None:
    """產生指定山岳的完整登山規劃報告。"""
    db = MountainDB.load()
    m = db.find(mountain)
    if m is None:
        console.print(f"[red]找不到「{mountain}」。[/red]目前收錄：")
        console.print("、".join(x.name for x in db.mountains))
        raise typer.Exit(1)
    with console.status(f"查詢 {m.name} 的天氣與巴士方案中…"):
        with MaitabiClient() as client:
            if html:
                from .html_report import render_html

                html.write_text(render_html(m, client, month=month), encoding="utf-8")
                console.print(f"已輸出 HTML：{html}")
                return
            md = build_mountain_report(m, client, month=month)
    _output(md, out)


_COMMANDS = {"list", "weekend", "best", "plan", "rooms", "watch", "doctor"}


def run() -> None:
    """進入點：`yama 燕岳` 等同 `yama plan 燕岳`。"""
    import sys

    args = sys.argv[1:]
    if args and not args[0].startswith("-") and args[0] not in _COMMANDS:
        sys.argv.insert(1, "plan")
    app()


if __name__ == "__main__":
    run()
