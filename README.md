# 雙北 YouBike 場站配置模擬器

互動式網頁工具，分析雙北 YouBike 場站車輛配置，並串接 TDX 即時資料。

## 功能
- **歷史分析**：5/18~22 民眾借還平均，計算每站最優/次優/最後解
- **即時資料**：每小時自動從 TDX 抓取在站車輛數（GitHub Actions 排程）
- **當日回放**：選擇日期，查看該天每小時在站車輛數變化
- 圖表 5+2 條線可自由勾選，互動調度模擬

## 目錄結構
```
.
├── index.html              # 主程式(單檔, 歷史資料已內嵌)
├── CLAUDE.md               # 專案脈絡說明(給 AI 開發用)
├── scripts/fetch_tdx.py    # TDX 抓取腳本
├── .github/workflows/fetch.yml  # 每小時排程
└── data/                   # 抓取的歷史快照(自動產生)
    ├── index.json          # 日期->小時清單
    └── YYYY-MM-DD/HH.json  # 各時段在站車輛數快照
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
之後每小時整點，Actions 自動抓 TDX 資料存進 `data/`，網頁開啟時自動讀取最新即時值，並可選日期回放。

## 注意
- TDX 即時 API 給的是「當下在站車輛數」(存量)，非借減還流量。
- 「即時在站」「當日回放」兩條線需 `data/` 有資料才會顯示；剛部署時 data 為空，這兩條線不會出現，待第一次抓取後才有。
- 頻率限制：TDX 每 IP 每分鐘最多 20 次，每小時抓一次遠低於限制。
