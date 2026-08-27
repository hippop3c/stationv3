# 雙北 YouBike 場站配置模擬器

互動式網頁工具，分析雙北 YouBike 場站車輛配置，並串接 TDX 即時資料。

## 功能

- **歷史分析**：5/18~22 民眾借還平均，計算每站最優/次優/最後解
- **名單自動更新**：每次排程同步 YouBike 官方公開名單，營運與暫停站皆納入；新站自動加入，舊站不因暫停或來源暫時缺漏而刪除
- **即時資料**：沿用 GitHub Actions 每10分鐘排程，每輪6次、間隔50秒，抓取 TDX 全站在站數與可還空位（實際執行時間可能受 Actions 排程延遲影響）
- **當日回放**：選擇日期查看歷史；即時模式每10分鐘一格，取該格最後一筆觀測
- **網頁刷新**：每6分鐘更新名單、日期與最新資料，保留使用者選站、篩選及手動調度；手動選舊日期時不跳回最新日
- 圖表 5+2 條線可自由勾選，互動調度模擬

## 目錄結構

```
.
├── index.html              # 主程式，原始 CPS 分析與備援名單內嵌
├── CLAUDE.md               # 專案脈絡說明(給 AI 開發用)
├── scripts/fetch_tdx.py    # TDX 全站抓取、歷史追加
├── scripts/sync_stations.py    # 名單聯集合併，保存 CPS 責任區
├── scripts/station_registry.js # 前端 metadata 合併與資料驗證
├── tests/                  # 名單、保存與前端刷新測試
├── .github/workflows/fetch.yml  # 原每10分鐘排程及手動執行
└── data/
    ├── stations.json       # 持續更新的公開站 metadata；不刪暫停或失聯站
    ├── station_analysis_archive.json # 原內部維調據點的完整分析存檔
    ├── index.json          # 日期 -> {count, latest}
    └── YYYY-MM-DD.json     # 全天快照陣列，持續追加、不清除舊日期
```

## 同步與資料保存

- 正式網站從本 repo 的 `raw.githubusercontent.com/.../main/data` 讀取最新資料；本地預覽讀相對 `data/`。因此 Actions 資料提交即使沒有觸發 Pages 重建，也能被網站讀取。
- 名單來源為 `https://apis.youbike.com.tw/json/station-yb2.json`，雙北公開站 `status=1/2` 均保留。官方名稱、行政區、車位數可更新，但不覆寫 CPS 分析或瀏覽器中的手動模擬。
- 既有責任區沿用 CPS 匯出資料；公開名單沒有 CPS 責任區欄位，新站暫列「待設定」，不以行政區猜測。
- TDX 抓取不受前端站點清單限制。新站只要出現在 TDX 即累積觀測；若已有舊快照，直接沿用，不從新增日截斷。暫停或暫時未回傳的站與既有歷史一律保留。
- 新站標示「無歷史分析」：不將觀測存量冒充借還流量，不自動產生未經計算的 CPS 建議值。模擬初始零值不代表真實用量為零。
- 名單同步失敗仍繼續抓取 TDX；全輪抓取失敗會讓 Actions 顯示失敗。檔案採原子寫入，工作流禁止同分支並行，瀏覽器請求45秒逾時後可於下一輪重試。同步程式、前端或測試更新到 main 時，也會立即驗證並抓取一輪；僅 data 提交不重複觸發。

## 驗證

```sh
pip install requests
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
```

## 部署步驟

### 1. 設定 TDX 金鑰 (GitHub Secrets)
進 repo → Settings → Secrets and variables → Actions → New repository secret，新增兩個：
- `TDX_CLIENT_ID`：你的 TDX Client ID
- `TDX_CLIENT_SECRET`：你的 TDX Client Secret

（金鑰從 TDX 會員中心 > API金鑰 取得，**切勿寫進程式碼**）

### 2. 啟用 GitHub Pages
Settings → Pages → Source 選 `Deploy from a branch` → 分支 `main` → 資料夾 `/ (root)` → Save。
網址會是 `https://<帳號>.github.io/<repo>/`

### 3. 啟用 Actions
進 Actions 頁面，若提示啟用就按啟用。
可手動按 `Run workflow` 立即抓第一筆資料測試（不必等整點）。

### 4. 完成
之後沿用每10分鐘排程，Actions 同步名單並追加 TDX 資料到 `data/`；網頁自動讀取最新值，並可選日期回放。

## 注意
- TDX 即時 API 給的是「當下在站車輛數」(存量)，非借減還流量。
- 「即時在站」「當日回放」兩條線需 `data/` 有資料才會顯示；剛部署時 data 為空，這兩條線不會出現，待第一次抓取後才有。
- 不需在前端放置 TDX 金鑰；沿用 Actions Secrets，不將帳密或 token 寫入程式或資料。
