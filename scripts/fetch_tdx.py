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

SHOTS = max(1, int(os.environ.get("TDX_SHOTS", "6")))
INTERVAL = max(0, int(os.environ.get("TDX_INTERVAL_SECONDS", "50")))
RETRIES = max(1, int(os.environ.get("TDX_RETRIES", "3")))
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BUCKET_MINUTES = 10
EXPECTED_BUCKETS = 24 * 60 // BUCKET_MINUTES
EXPECTED_BUCKET_LABELS = tuple(
    f"{minute // 60:02d}:{minute % 60:02d}"
    for minute in range(0, 24 * 60, BUCKET_MINUTES)
)


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
    seen = set()
    for city in ["Taipei", "NewTaipei"]:
        avail = fetch_all_station_rows(f"/v2/Bike/Availability/City/{city}", token)
        if not avail:
            raise ValueError(f"Empty availability response for {city}")
        for s in avail:
            uid = s.get("StationUID") or s.get("StationID")
            if not uid or uid in seen:
                if uid in seen:
                    raise ValueError(f"Duplicate availability station: {uid}")
                continue
            seen.add(uid)
            stations.append({
                "id": uid,
                "rent": s.get("AvailableRentBikes"),
                "return": s.get("AvailableReturnBikes"),
            })
    registry_path = DATA_DIR / "stations.json"
    if registry_path.exists():
        with open(registry_path, encoding="utf-8") as f:
            registry = json.load(f)
        registry_count = len(registry.get("stations", [])) if isinstance(registry, dict) else 0
        minimum = max(3000, int(registry_count * 0.9))
    else:
        minimum = 3000
    if len(stations) < minimum:
        raise ValueError(
            f"Partial availability snapshot: {len(stations)} stations, expected at least {minimum}"
        )
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


def ten_minute_bucket(time_str):
    """Return the start label of the 10-minute bucket containing HH:MM."""
    try:
        parsed = datetime.strptime(time_str, "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid history time: {time_str!r}") from exc
    minute = parsed.minute // BUCKET_MINUTES * BUCKET_MINUTES
    return f"{parsed.hour:02d}:{minute:02d}"


def _record_datetime(record):
    if not isinstance(record, dict):
        raise ValueError("Invalid history record; refusing to replace existing records")
    try:
        parsed = datetime.fromisoformat(record["datetime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid history datetime; refusing to replace existing records") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TW_TZ)
    if not isinstance(record.get("stations"), dict) or not record["stations"]:
        raise ValueError("Invalid station observations; refusing to replace existing records")
    ten_minute_bucket(record.get("time"))
    return parsed


def upsert_bucket(records, new_record):
    """Keep the latest complete record in each 10-minute bucket, in time order."""
    latest_by_bucket = {}
    for record in [*records, new_record]:
        observed_at = _record_datetime(record)
        bucket = ten_minute_bucket(record["time"])
        previous = latest_by_bucket.get(bucket)
        if previous is None or observed_at > previous[0]:
            latest_by_bucket[bucket] = (observed_at, record)
    return [
        record
        for _, record in sorted(latest_by_bucket.values(), key=lambda item: item[0])
    ]


def history_index_entry(day_records):
    """Build coverage metadata for the 144 expected 10-minute buckets."""
    if not day_records:
        raise ValueError("Cannot index an empty history day")
    bucket_labels = sorted({ten_minute_bucket(record.get("time")) for record in day_records})
    present = set(bucket_labels)
    missing_labels = [label for label in EXPECTED_BUCKET_LABELS if label not in present]
    latest_record = max(day_records, key=_record_datetime)
    bucket_count = len(bucket_labels)
    return {
        "count": bucket_count,
        "latest": latest_record["time"],
        "buckets": bucket_count,
        "expected": EXPECTED_BUCKETS,
        "coverage": round(bucket_count / EXPECTED_BUCKETS, 6),
        "missing_buckets": len(missing_labels),
        "bucket_labels": bucket_labels,
        "missing_labels": missing_labels,
    }


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
    day_records = upsert_bucket(day_records, rec)

    atomic_write_json(day_path, day_records)
    bucket = ten_minute_bucket(time_str)
    print(
        f"  -> saved {time_str} in bucket {bucket} "
        f"({len(day_records)}/{EXPECTED_BUCKETS}, {len(stations)} stations)"
    )

    index_path = DATA_DIR / "index.json"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}
    if not isinstance(index, dict):
        raise ValueError("Invalid date index; refusing to replace it")
    index[date_str] = history_index_entry(day_records)
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
    print(f"連抓 {SHOTS} 次 (間隔 {INTERVAL}s，每次最多重試 {RETRIES} 次)")
    saved = 0

    for i in range(SHOTS):
        for attempt in range(1, RETRIES + 1):
            try:
                now = datetime.now(TW_TZ)
                print(
                    f"[{i+1}/{SHOTS}] {now.strftime('%H:%M:%S')} "
                    f"抓取中 (嘗試 {attempt}/{RETRIES})..."
                )
                # A long-running hourly collector can outlive an OAuth token.
                # Refresh before every shot/retry so later buckets do not vanish.
                token = get_token()
                stations = snapshot_once(token)
                save_record(now, stations)
                saved += 1
                break
            except Exception as e:
                print(f"  !! 第 {i+1} 次嘗試 {attempt} 失敗: {e}")
                if attempt < RETRIES:
                    time.sleep(min(30, 10 * attempt))

        if i < SHOTS - 1:
            time.sleep(INTERVAL)

    print(f"完成，成功保存 {saved}/{SHOTS} 筆快照")
    if not saved or registry_failed:
        # The workflow still commits successful data, but does not hide a
        # failed sync or a run that collected no history at all.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
