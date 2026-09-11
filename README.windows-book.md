# Windows／台灣使用者：開賣當天搶涸沢ヒュッテ

這份指南只介紹 `book`：在開賣前待機，開賣後自動選日期、房型、方案和填寫住客資料，
盡量走到最後確認頁。**最後的預約確定由你親自按下，程式不會自動完成下訂。**
遇到無法辨識的必填欄位或信用卡欄位時，可能提前停下，需要手動接手。

小屋名稱是 **涸沢ヒュッテ（Karasawa Hütte）**；複製以下日文名稱即可。
所有指令都在 Windows **PowerShell** 執行。

## 1. 事先安裝（不要等開賣才做）

需要 Git、uv 和 **Google Chrome**。Python 與 Playwright 由 uv 安裝，
不用另外裝 Anaconda 或 WSL，也不需要 AI 軟體或 API key。

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id Google.Chrome -e
```

已安裝的項目可跳過。安裝後關掉 PowerShell，重新開啟，再依序執行：

```powershell
git --version
uv --version
cd $HOME
git clone https://github.com/randy-tsukemen/hiking.git
cd hiking
uv python install 3.12
uv sync --python 3.12 --group book
```

若已下載過專案，直接進入原本的 `hiking` 資料夾，不必再 clone。
uv 會管理 `.venv`，不需要手動啟用虛擬環境。
uv 的 Windows 安裝方式可參考[官方文件](https://docs.astral.sh/uv/getting-started/installation/)。

## 2. 登入並準備住客資料

為避免 Windows 預設文字編碼造成日文資料讀寫問題，每次開新 PowerShell，
先設定下列變數，再執行本指南的 Python 指令：

```powershell
$env:PYTHONUTF8 = "1"
cd $HOME\hiking
uv run --group book yama book --setup
```

程式會開啟專用 Chrome 視窗，在裡面手動完成 Google 登入。
看到終端機顯示登入完成、視窗自動關閉後，就可以繼續。
日常 Chrome 已登入的帳號不會自動帶入這個專用視窗。

接著開啟剛產生的資料範本：

```powershell
notepad "$HOME\.yama_cache\booking_profile.json"
```

把冒號後面的空字串 `""` 填上資料，保留原本的欄位名稱、雙引號與逗號，
並以 **UTF-8** 儲存。例如 `"姓": "你的姓"`。

| 欄位 | 填寫內容 |
|---|---|
| `姓`、`名` | 訂房代表人的姓、名 |
| `セイ`、`メイ` | 姓、名的片假名讀音 |
| `電話`、`メール` | 電話、電子郵件 |
| `郵便番号`、`住所` | 郵遞區號、地址 |
| `年齢`、`性別` | 年齡、性別 |
| `緊急連絡先氏名`、`緊急連絡先電話` | 緊急聯絡人與電話 |

程式只會嘗試填入能辨識的欄位；範本有填也不代表每一欄都會自動帶入。
海外地址、電話格式及不確定的欄位，請在預約頁依網站要求確認或補填。
這份檔案含個資，留在自己的電腦即可。

## 3. 台灣時區：維持原設定即可

**不需要修改 Windows 時區。**程式固定以日本時間（JST／UTC+09:00）判斷開賣、
倒數與預約截止；Windows 可以保持台北（UTC+08:00），只要系統時鐘正確同步即可。
程式的開賣時間提示以日本時間顯示。

例如官網公告日本 **08:00** 開賣，就是台灣 **07:00**。
你可以在台灣 **06:50** 啟動程式，讓它等待日本 08:00 開賣。
指令中的日期仍是**日本當地住宿日期**。

以下日期僅為操作範例，實際開賣日期與時間請事先確認
[涸沢ヒュッテ官網](https://karasawa-hyutte.com/)或 Yamatan 的公告。
若先前已下載舊版，請先在專案資料夾執行 `git pull`，再執行
`uv sync --group book`，取得包含時區修正的版本。

## 4. 開賣當天：執行這個指令

建議在確認的開賣時間前約 10 分鐘啟動；待機只支援**開賣前 12 小時內**。
在 PowerShell 執行：

```powershell
$env:PYTHONUTF8 = "1"
cd $HOME\hiking

# 範例：2026-10-17 入住，一位男性大人、一位女性大人
uv run --group book yama book 涸沢ヒュッテ 2026-10-17 -m 1 -w 1
```

執行前修改以下內容：

- `2026-10-17`：改成你要的**日本當地住宿日期**，不是開賣日或今天日期。
- `-m 1`：男性大人人數；`-w 1`：女性大人人數。例如兩位女性是 `-m 0 -w 2`。
- 若需指定房型，可在最後加上 `--room "2名様"`，文字必須符合網站的房型名稱。
  不指定時由程式尋找可點選的房型，最後仍需核對房型與人數。

不必再啟動其他搶位指令。`book` 會開啟 Chrome；尚未開賣時，終端機應顯示
「尚未開賣」「瀏覽器待機中」等提示。保持電腦連網、不睡眠，保留 PowerShell 和
Chrome 視窗，等待它自動操作。

開賣後程式會嘗試選日期、房型、方案、人數和填表。
到確認頁或提示需要接手時，立即檢查**山屋、入住日期、房型、人數、餐食、金額與住客資料**，
補填必要問卷，再親自按下最終確定。以網站顯示的預約完成訊息為準，開到確認頁不等於訂到。

## 遇到問題

- **顯示尚未登入**：先重新執行 `uv run --group book yama book --setup`。
- **找不到 Chrome**：確認已安裝 Google Chrome；只有 Edge 不夠。
- **資料沒填入或停在中途**：直接在程式開啟的瀏覽器手動接手，不要等待它自動完成所有欄位。
- **找不到可訂房型**：可能已滿、日期不營業或房型名稱不符；到預約頁確認。
- **提示距開賣超過 12 小時**：請在確認的開賣前約 10 分鐘再執行。
- **要停止**：在 PowerShell 按 `Ctrl+C`。同一時間只執行一個 `book`／登入程序，避免共用 Chrome profile 衝突。

本功能仍屬實驗性，以上依目前程式行為整理，尚未完成 Windows 實機驗證。
