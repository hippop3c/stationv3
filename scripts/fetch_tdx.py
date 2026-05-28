"""
TDX YouBike 雙北即時資料抓取 — 每 6 分鐘執行 (GitHub Actions)
金鑰從環境變數讀取: TDX_CLIENT_ID / TDX_CLIENT_SECRET

儲存邏輯:
- 每天一個檔 data/{YYYY-MM-DD}.json, 內容為當天所有「有變化時間點」的陣列
- 每次抓取與當天最後一筆比對, 只要雙北任一站的在站數或空位數有變, 就把整批全站快照 append 進去; 完全沒變則跳過
- data/index.json 紀錄有資料的日期清單與當天最新時間
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"
TW_TZ = timezone(timedelta(hours=8))


def get_token():
    resp = requests.post(AUTH_URL, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["TDX_CLIENT_ID"],
        "client_secret": os.environ["TDX_CLIENT_SECRET"],
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch(endpoint, token):
    resp = requests.get(
        f"{BASE_URL}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"$format": "JSON"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def snapshot_signature(stations):
    """產生快照的比對指紋: {站id: (rent, return)} 的排序字串"""
    items = sorted((s["id"], s["rent"], s["return"]) for s in stations)
    return json.dumps(items, separators=(",", ":"))


def main():
    token = get_token()
    now = datetime.now(TW_TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    # 抓雙北即時車位
    stations = []
    for city in ["Taipei", "NewTaipei"]:
        avail = fetch(f"/v2/Bike/Availability/City/{city}", token)
        for s in avail:
            stations.append({
                "id": s.get("StationUID") or s.get("StationID"),
