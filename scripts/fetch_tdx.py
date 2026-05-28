"""
TDX YouBike 雙北即時資料抓取 — 供 GitHub Actions 每小時排程執行
金鑰從環境變數讀取: TDX_CLIENT_ID / TDX_CLIENT_SECRET
輸出: data/{YYYY-MM-DD}/{HH}.json  (該小時雙北所有站的在站車輛數快照)
另維護: data/index.json (所有可用日期清單, 供前端日期選擇器讀取)
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"

# 台灣時間 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))


def get_token():
    cid = os.environ["TDX_CLIENT_ID"]
    secret = os.environ["TDX_CLIENT_SECRET"]
    resp = requests.post(AUTH_URL, data={
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
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


def main():
    token = get_token()
    now = datetime.now(TW_TZ)
    date_str = now.strftime("%Y-%m-%d")
    hour_str = now.strftime("%H")

    # 抓雙北即時車位
    snapshot = []
    for city in ["Taipei", "NewTaipei"]:
        avail = fetch(f"/v2/Bike/Availability/City/{city}", token)
        for s in avail:
            snapshot.append({
                "id": s.get("StationUID") or s.get("StationID"),
                "rent": s.get("AvailableRentBikes"),      # 可借(≈在站車輛數)
                "return": s.get("AvailableReturnBikes"),  # 可還空位
                "city": city,
            })

    # 寫入該小時快照
    day_dir = os.path.join("data", date_str)
    os.makedirs(day_dir, exist_ok=True)
    out_path = os.path.join(day_dir, f"{hour_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "datetime": now.isoformat(),
            "date": date_str,
            "hour": int(hour_str),
            "stations": snapshot,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已寫入 {out_path} ({len(snapshot)} 站)")

    # 更新 index.json (日期 -> 該日已有的小時清單)
    index_path = os.path.join("data", "index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}
    hours = set(index.get(date_str, []))
    hours.add(int(hour_str))
    index[date_str] = sorted(hours)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已更新 index.json: {date_str} -> {index[date_str]}")


if __name__ == "__main__":
    main()
