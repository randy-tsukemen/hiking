# yama — 登山規劃 CLI

輸入山名，一次取得：

- **毎日あるぺん号巴士方案**（東京發，即時查詢：價格、出發日、催行狀態、直達預約連結）
- **山屋住宿**（巴士＋山屋套裝方案 + 各山域主要山屋官方預約連結）
- **套裝方案的房間空位**（巴士有位≠房間有位——不用走預約流程就能查逐晚房間狀態）
- **Yamap 模範路線數據**（每條路線的距離、累積爬升/下降、標準時間、
  コース定数難度數值與体力度，一眼看懂路線多硬）
- **山頂天氣預報**（Open-Meteo 16 天，依山頂標高修正，附登山適宜度 ◎○△×）

不用再自己查一堆網站。

## 安裝

需要 [uv](https://docs.astral.sh/uv/)：

```sh
cd hiking
uv sync
```

## 給其他 Claude Code 使用者：安裝 plugin

本 repo 同時是 Claude Code plugin marketplace。在 Claude Code 裡執行：

```
/plugin marketplace add randy-tsukemen/hiking
/plugin install yama@yama-tools
```

安裝後在**任何專案**都能直接說「我想爬燕岳」「這週末去哪爬山好？」，
Claude 會透過 uvx 從本 repo 執行 CLI（免 clone、免安裝，只需要 [uv](https://docs.astral.sh/uv/)）。

## 給任何 AI Agent：MCP server

資料層以 [MCP](https://modelcontextprotocol.io/) 開放——Claude Desktop、Cursor、
自建 Agent 等任何 MCP client 都能直接使用（免 clone，只需要 uv）：

```json
{
  "mcpServers": {
    "yama": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/randy-tsukemen/hiking", "yama-mcp"]
    }
  }
}
```

提供 6 個工具：`list_mountains`、`get_mountain_info`（含 Yamap 路線難度數據）、
`get_weather`（16 天適宜度）、`get_bus_options`（含預約連結）、
`rank_mountains_by_weather`、`check_hut_room_availability`（房間空位實查）。

同一份資料層有三種介面：CLI（人用）、Claude Code plugin（Claude Code 用）、
MCP（任何 Agent 用）——挑適合你的。

## 透過 AI Agent 使用（推薦）

本 repo 內建 Claude Code skill（`.claude/skills/yama/`）。在這個目錄開 Claude Code，
直接用自然語言對話即可，不需要記指令：

> 「我想爬燕岳」「這週末去哪爬山好？」「幫我查 8 月去白馬岳的巴士和山屋」

Claude 會自動執行 CLI、解讀報告，並依你的需求整理出建議（含直達預約連結）。

## 直接使用 CLI

```sh
uv run yama 燕岳                # 完整報告：天氣 + 巴士 + 山屋 + 路線圖
uv run yama 燕岳 --month 8      # 指定巴士查詢月份
uv run yama 燕岳 --out plan.md  # 另存 Markdown 檔
uv run yama 燕岳 --html plan.html  # 圖表版 HTML（天氣圖+路線比較圖+方案表，可直接分享）

uv run yama weekend             # 這週末適合去哪些山？（天氣排名 + 巴士可預約標記）
uv run yama weekend --no-bus    # 只看天氣，跳過巴士查詢（較快）
uv run yama best --days 14      # 未來 14 天內每座山的最佳登山日排名

uv run yama hut 燕山荘 2026-09      # 山屋官網逐日空位（15 家：Yamatan/tenawan/燕山荘系/穂高岳山荘）
uv run yama rooms 8574 2026-07-10   # 查套裝方案的山屋「房間」空位（見下方說明）
uv run yama watch room 8574 2026-07-10  # 監控空房釋出，變化時通知（見下方說明）
uv run yama list                # 列出收錄的山岳
```

山名支援日文漢字、平假名與常見別名（`燕岳`、`つばくろだけ`、`木曽駒ケ岳`/`木曽駒ヶ岳`、`涸沢` 都可以）。

## 房間空位查詢（`yama rooms`）— 巴士有位 ≠ 房間有位

含山屋住宿的套裝方案（如「雷鳥荘1泊〈往復〉」），毎日あるぺん号顯示的
「受付中／催行決定」只代表**巴士**還有位子；山屋**房間**的庫存要走到
預約系統第二步才看得到，常發生「看起來能訂、實際房間已滿」。

`yama rooms <course_no> <出發日>` 直接查出逐晚房間狀態，不用手動點預約流程：

```
$ uv run yama rooms 8574 2026-07-10

2026毎日あるぺん号　雷鳥荘1泊（相部屋、到着日泊）〈往復〉（2026/07/10 出發）

| 晚次           | 住宿                     | 房型   | 空位                     |
| 1泊目 7/10(金) | 毎日あるぺん号（車中泊） | 車中泊 | ✅ ○（有空位）           |
| 2泊目 7/11(土) | 雷鳥荘                   | 相部屋 | ❌ ×（受付不可（已滿）） |

全程可訂：❌ 不行（有晚次已滿）
```

- `course_no` 是方案詳細頁網址（`bus.maitabi.jp/detail.html?course_no=N`）裡的編號
- 狀態符號：`○` 有空位、`數字` 剩餘數、`RQ` 請求受理（送出後等旅行社確認）、
  `WT` 候補（キャンセル待ち）、`×` 已滿
- 原理：模擬預約流程的前兩步（選房數/人數 → 宿泊先確認頁）並解析空位表，
  **不會建立任何預約**；多房型方案會自動挑最低人數需求的房型探測

## 監控通知（`yama watch`）— 幫你盯著空房和天氣窗

滿房會釋出（取消費生效前是退訂高峰）、天氣窗稍縱即逝——這種時效性資訊交給排程盯：

```sh
yama watch room 8574 2026-07-10        # 監控套裝房間：×→○/RQ 釋出時通知
yama watch weather 立山 --score 75 --days 2   # 出現連 2 天◎的窗口時通知
yama watch list / remove <id>          # 管理監控項
yama watch run                         # 檢查一輪（給 cron 呼叫）
```

狀態變好時通知（只通知一次）：stdout＋macOS 通知中心＋可選 LINE push
（設 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_USER_ID` 環境變數；注意每月 200 則免費額度）。

排程範例（每小時檢查）：`crontab -e` 加入

```
0 * * * * cd /path/to/hiking && /opt/homebrew/bin/uv run yama watch run
```

## 報告內容

`yama <山名>` 產出的報告包含：

1. 山岳概要（標高、山域、登山口、難度）
2. 山頂 16 天天氣預報與適宜度（週末粗體標示）
3. 行程建議（幾泊幾日、推薦山屋）
4. 路線資料（Yamap 模範路線）：距離、爬升/下降、標準時間、
   **コース定数**（〜19 輕鬆／20〜39 一般／40〜59 健腳／60〜79 吃力／80〜 極吃力）、
   **体力度**（1〜10：1〜3 適合日帰り、4〜5 建議住 1 晚以上、6〜10 多日縱走體力）、
   建議日程（日帰り／1泊2日…），各路線附 Yamap 連結
5. 巴士方案（去程／來回套裝／回程），每個出發日附：
   - 天氣適宜度（16 天內）
   - 催行狀態（催行決定・受付中・残席わずか・満席）
   - **點狀態即直達預約頁面**
6. 山屋清單與官方預約連結、電話
7. Yamap 山岳頁連結

## 資料來源

| 資料 | 來源 | 方式 |
|---|---|---|
| 巴士方案 | [毎日あるぺん号](https://bus.maitabi.jp/)（毎日新聞旅行） | 公開 JSON API 即時查詢 |
| 套裝房間空位 | travel-answer.ne.jp（毎日あるぺん号預約系統） | 模擬預約流程前兩步解析空位表 |
| 天氣 | [Open-Meteo](https://open-meteo.com/) | 免費 API，山頂座標＋標高，快取 1 小時 |
| 路線數據 | [Yamap](https://yamap.com/) 模範路線 | 山岳頁 SSR 資料即時解析 |
| 山岳・山屋 | 內建精選資料庫 `src/yama/data/mountains.json` | 人工整理，含 Yamap 連結 |

注意事項：

- 巴士**套裝方案**的房間空位可用 `yama rooms` 查詢；**自行向山屋訂房**的即時空位
  仍無法統一查詢（各家系統不同），請點官網連結確認
- 巴士方案的「來回・套裝」多含山屋住宿，訂巴士時可一併預約
- 天氣預報僅供參考，出發前請再確認[山の天気](https://tenkura.n-kishou.co.jp/tk/)等來源
- 取消費規定見[毎日あるぺん号 FAQ](https://www.maitabi.jp/guide/QandA.php)

## 開發

```sh
uv run pytest        # 單元測試
uv run yama doctor   # 資料來源健檢（maitabi/天氣/Yamap/預約系統/山屋連結）
```

本專案依賴四個會改版的外部來源；GitHub Actions 每週一自動跑 `doctor`，
來源改版導致功能靜默壞掉時會收到 Actions 失敗通知。

新增山岳：編輯 `src/yama/data/mountains.json`，欄位格式參考現有條目（`maitabi.area_names` 需對應 API 的 district 名稱子字串，`lat/lon` 為山頂座標）。
