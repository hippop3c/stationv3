import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from .sync_stations import atomic_write_json, sync_station_registry
except ImportError:
    from sync_stations import atomic_write_json, sync_station_registry

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"
TW_TZ = timezone(timedelta(hours=8))

SHOTS = 6
INTERVAL = 50
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


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

    day_path = DATA_DIR / f"{date_str}.json"
    if day_path.exists():
        with open(day_path, encoding="utf-8") as f:
            day_records = json.load(f)
    else:
        day_records = []
    if not isinstance(day_records, list):
        raise ValueError("Invalid history file; refusing to replace existing records")

    observations = {
        s["id"]: [s["rent"], s["return"]]
        for s in stations if isinstance(s.get("id"), str) and s["id"]
    }
    if not observations:
        raise ValueError("No station observations; history is unchanged")

    rec = {
        "time": time_str,
        "datetime": now.isoformat(),
        "stations": observations,
    }
    day_records.append(rec)

    atomic_write_json(day_path, day_records)
    print(f"  -> saved {time_str} as record #{len(day_records)} ({len(stations)} stations)")

    index_path = DATA_DIR / "index.json"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}
    if not isinstance(index, dict):
        raise ValueError("Invalid date index; refusing to replace it")
    index[date_str] = {"count": len(day_records), "latest": time_str}
    atomic_write_json(index_path, index)
    return rec


def main():
    registry_failed = False
    try:
        sync_station_registry()
    except Exception as exc:
        # Metadata errors must not interrupt capture of live observations.
        registry_failed = True
        print(f"場站名單同步失敗，保留既有名單並繼續抓取: {exc}")
    token = get_token()
    print(f"連抓 {SHOTS} 次 (間隔 {INTERVAL}s)")
    saved = 0

    for i in range(SHOTS):
        try:
            now = datetime.now(TW_TZ)
            print(f"[{i+1}/{SHOTS}] {now.strftime('%H:%M:%S')} 抓取中...")
            stations = snapshot_once(token)
            save_record(now, stations)
            saved += 1
        except Exception as e:
            print(f"  !! 第 {i+1} 次失敗: {e}")

        if i < SHOTS - 1:
            time.sleep(INTERVAL)

    print(f"完成，成功保存 {saved}/{SHOTS} 筆快照")
    if not saved or registry_failed:
        # The workflow still commits successful data, but does not hide a
        # failed sync or a run that collected no history at all.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
