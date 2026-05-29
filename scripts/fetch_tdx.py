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


def main():
    token = get_token()
    now = datetime.now(TW_TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    stations = []
    for city in ["Taipei", "NewTaipei"]:
        avail = fetch(f"/v2/Bike/Availability/City/{city}", token)
        for s in avail:
            stations.append({
                "id": s.get("StationUID") or s.get("StationID"),
                "rent": s.get("AvailableRentBikes"),
                "return": s.get("AvailableReturnBikes"),
            })

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
    print(f"{time_str} saved record #{len(day_records)} ({len(stations)} stations)")

    index_path = os.path.join("data", "index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}
    index[date_str] = {"count": len(day_records), "latest": time_str}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"index.json updated: {date_str} -> {index[date_str]}")


if __name__ == "__main__":
    main()
