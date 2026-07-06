# yama — 登山規劃 CLI

輸入山名，一次取得：

- **毎日あるぺん号巴士方案**（東京發，即時查詢：價格、出發日、催行狀態、直達預約連結）
- **山屋住宿**（巴士＋山屋套裝方案 + 各山域主要山屋官方預約連結）
- **套裝方案的房間空位**（巴士有位≠房間有位——不用走預約流程就能查逐晚房間狀態）
- **Yamap 路線圖**（山岳頁面與模範路線）
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

uv run yama weekend             # 這週末適合去哪些山？（天氣排名 + 巴士可預約標記）
uv run yama weekend --no-bus    # 只看天氣，跳過巴士查詢（較快）
uv run yama best --days 14      # 未來 14 天內每座山的最佳登山日排名

uv run yama rooms 8574 2026-07-10   # 查套裝方案的山屋「房間」空位（見下方說明）
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

## 報告內容

`yama <山名>` 產出的報告包含：

1. 山岳概要（標高、山域、登山口、難度）
2. 山頂 16 天天氣預報與適宜度（週末粗體標示）
3. 行程建議（幾泊幾日、推薦山屋）
4. 巴士方案（去程／來回套裝／回程），每個出發日附：
   - 天氣適宜度（16 天內）
   - 催行狀態（催行決定・受付中・残席わずか・満席）
   - **點狀態即直達預約頁面**
5. 山屋清單與官方預約連結、電話
6. Yamap 路線圖連結

## 資料來源

| 資料 | 來源 | 方式 |
|---|---|---|
| 巴士方案 | [毎日あるぺん号](https://bus.maitabi.jp/)（毎日新聞旅行） | 公開 JSON API 即時查詢 |
| 套裝房間空位 | travel-answer.ne.jp（毎日あるぺん号預約系統） | 模擬預約流程前兩步解析空位表 |
| 天氣 | [Open-Meteo](https://open-meteo.com/) | 免費 API，山頂座標＋標高，快取 1 小時 |
| 山岳・山屋・路線 | 內建精選資料庫 `src/yama/data/mountains.json` | 人工整理，含 Yamap 連結 |

注意事項：

- 巴士**套裝方案**的房間空位可用 `yama rooms` 查詢；**自行向山屋訂房**的即時空位
  仍無法統一查詢（各家系統不同），請點官網連結確認
- 巴士方案的「來回・套裝」多含山屋住宿，訂巴士時可一併預約
- 天氣預報僅供參考，出發前請再確認[山の天気](https://tenkura.n-kishou.co.jp/tk/)等來源
- 取消費規定見[毎日あるぺん号 FAQ](https://www.maitabi.jp/guide/QandA.php)

## 開發

```sh
uv run pytest        # 單元測試
```

新增山岳：編輯 `src/yama/data/mountains.json`，欄位格式參考現有條目（`maitabi.area_names` 需對應 API 的 district 名稱子字串，`lat/lon` 為山頂座標）。
