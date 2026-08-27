import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from .sync_stations import atomic_write_json, sync_station_registry, tdx_station_code
except ImportError:
    from sync_stations import atomic_write_json, sync_station_registry, tdx_station_code

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


def fetch(endpoint, token, query=None):
    resp = requests.get(
        f"{BASE_URL}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"$format": "JSON", **(query or {})},
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


def fetch_all_station_rows(endpoint, token, page_size=1000):
    rows, seen = [], set()
    for offset in range(0, page_size * 30, page_size):
        page = fetch(endpoint, token, {"$top": page_size, "$skip": offset, "$orderby": "StationID"})
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise ValueError("Invalid TDX station page")
        for row in page:
            uid = row.get("StationUID") or row.get("StationID")
            if uid and uid in seen:
                raise ValueError("TDX pagination repeated a station; refusing a partial sync")
            if uid:
                seen.add(uid)
        rows.extend(page)
        if len(page) < page_size:
            return rows
    raise ValueError("TDX station pagination exceeded safety limit")


def station_metadata(token):
    """Official TDX fallback; reuse the already-authorized Actions credentials."""
    rows = []
    for city in ("Taipei", "NewTaipei"):
        metadata = fetch_all_station_rows(f"/v2/Bike/Station/City/{city}", token)
        availability = fetch_all_station_rows(f"/v2/Bike/Availability/City/{city}", token)
        if not metadata or not availability:
            raise ValueError(f"Empty TDX station/availability response for {city}")
        statuses = {tdx_station_code(row): row.get("ServiceStatus") for row in availability}
        for source_row in metadata:
            row = dict(source_row)
            code = tdx_station_code(row)
            if code in statuses:
                row["ServiceStatus"] = statuses[code]
            rows.append(row)
    return rows


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
        print(f"YouBike 公開名單暫時無法取得，改用已授權 TDX: {exc}")
    token = get_token()
    if registry_failed:
        try:
            sync_station_registry(source_rows=station_metadata(token), source_kind="tdx")
            registry_failed = False
        except Exception as exc:
            print(f"TDX 名單同步失敗，保留既有名單並繼續抓取: {exc}")
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
