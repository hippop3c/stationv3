import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"
TW_TZ = timezone(timedelta(hours=8))

SHOTS = 6
INTERVAL = 50


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


def snapshot_once(token):
    stations = []
    for city in ["Taipei", "NewTaipei"]:
        avail = fetch(f"/v2/Bike/Availability/City/{city}", token)
        for s in avail:
            stations.append({
                "id": s.get("StationUID") or s.get("StationID"),
                "rent": s.get("AvailableRentBikes"),
                "return": s.get("AvailableReturnBikes"),
            })
    return stations


def save_record(now, stations):
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    day_path = os.path.join("data", f"{date_str}.json")
    if os.path.exists(day_path):
        with open(day_path, encoding="utf-8") as f:
            day_records = json.load(f)
    else:
        day_records = []

    rec = {
        "time": time_str,
        "datetime": now.isoformat(),
        "stations": {s["id"]: [s["rent"], s["return"]] for s in stations},
    }
    day_records.append(rec)

    os.makedirs("data", exist_ok=True)
    with open(day_path, "w", encoding="utf-8") as f:
        json.dump(day_records, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  -> saved {time_str} as record #{len(day_records)} ({len(stations)} stations)")

    index_path = os.path.join("data", "index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}
    index[date_str] = {"count": len(day_records), "latest": time_str}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))


def main():
    token = get_token()
    print(f"連抓 {SHOTS} 次 (間隔 {INTERVAL}s)")

    for i in range(SHOTS):
        try:
            now = datetime.now(TW_TZ)
            print(f"[{i+1}/{SHOTS}] {now.strftime('%H:%M:%S')} 抓取中...")
            stations = snapshot_once(token)
            save_record(now, stations)
        except Exception as e:
            print(f"  !! 第 {i+1} 次失敗: {e}")

        if i < SHOTS - 1:
            time.sleep(INTERVAL)

    print("完成")


if __name__ == "__main__":
    main()
