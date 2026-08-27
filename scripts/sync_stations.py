"""Keep an append-only station registry alongside the TDX observation archive.

Status 1 (open) and 2 (temporarily suspended) are both public stations.
Missing upstream rows never delete an existing station or its observations.
This updates metadata only; the embedded CPS analysis is not recalculated.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
STATION_URL = "https://apis.youbike.com.tw/json/station-yb2.json"
TW_TZ = timezone(timedelta(hours=8))
CODE_RE = re.compile(r"^500[12]\d{5}$")
METADATA_FIELDS = ("code", "name", "city", "district", "zone", "capacity")


def is_public_code(code):
    return bool(CODE_RE.fullmatch(str(code))) and not str(code).startswith(
        ("500199", "500299")
    )


def load_seed_stations(path):
    html = Path(path).read_text(encoding="utf-8")
    marker = "const STATIONS = "
    start = html.index(marker) + len(marker)
    rows, _ = json.JSONDecoder().raw_decode(html, start)
    return rows


def normalize_source(rows):
    if not isinstance(rows, list):
        raise ValueError("Station source must be a JSON array")
    normalized = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("station_no") or "").strip()
        if not is_public_code(code):
            continue
        try:
            status = int(row.get("status"))
            capacity = int(row.get("parking_spaces"))
        except (TypeError, ValueError):
            continue
        if status not in (1, 2) or capacity < 0:
            continue
        name = re.sub(r"^YouBike2\.0_", "", str(row.get("name_tw") or "").strip())
        district = str(row.get("district_tw") or "").strip()
        if code.startswith("500119"):
            district = "臺大公館校區"
        if not name or not district:
            continue
        if code in normalized:
            raise ValueError("Duplicate station code in source: " + code)
        normalized[code] = {
            "code": code,
            "name": name,
            "city": "台北市" if code.startswith("5001") else "新北市",
            "district": district,
            "capacity": capacity,
            "status": status,
        }
    if not normalized:
        raise ValueError("Empty/invalid station source; existing registry is unchanged")
    return normalized


def merge_registry(previous, source_rows, observed_at, seed_stations=()):
    if previous is None:
        previous = {"schema_version": 1, "stations": []}
    if (
        not isinstance(previous, dict)
        or previous.get("schema_version") != 1
        or not isinstance(previous.get("stations"), list)
    ):
        raise ValueError("Invalid existing station registry; refusing to replace it")

    stations = {}
    for row in previous["stations"]:
        code = str(row.get("code") or "")
        if not is_public_code(code) or code in stations:
            raise ValueError("Invalid/duplicate existing registry code: " + code)
        stations[code] = dict(row)

    # The HTML is a fallback/analysis seed, not an authoritative replacement for
    # newer registry metadata. Preserve all registry fields and all old codes.
    for seed in seed_stations:
        code = str(seed.get("code") or "")
        if not is_public_code(code) or code in stations:
            continue
        stations[code] = {field: seed[field] for field in METADATA_FIELDS if field in seed}
        stations[code]["first_seen_at"] = observed_at

    source = normalize_source(source_rows)
    before_codes = set(stations)
    for code, row in stations.items():
        row["source_present"] = code in source
    for code, metadata in source.items():
        row = stations.setdefault(code, {"code": code, "first_seen_at": observed_at})
        row.update(metadata)
        # Public metadata does not contain CPS responsibility zones. Never guess
        # one or overwrite an existing CPS value when adding/renaming stations.
        if not row.get("zone") or row.get("zone") == "nan":
            row["zone"] = "待設定"
        row.setdefault("first_seen_at", observed_at)
        row["last_seen_at"] = observed_at
        row["source_present"] = True

    result = dict(previous)
    result.update({
        "schema_version": 1,
        "updated_at": observed_at,
        "source": STATION_URL,
        "retention_policy": "append-only; suspended and missing stations are retained",
        "stations": [stations[code] for code in sorted(stations)],
    })
    stats = {
        "total": len(stations),
        "source_open": sum(row["status"] == 1 for row in source.values()),
        "source_suspended": sum(row["status"] == 2 for row in source.values()),
        "added": len(set(stations) - before_codes),
        "retained_missing": len(set(stations) - set(source)),
    }
    return result, stats


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def sync_station_registry(root=ROOT, source_rows=None, now=None):
    root = Path(root)
    registry_path = root / "data" / "stations.json"
    previous = None
    if registry_path.exists():
        previous = json.loads(registry_path.read_text(encoding="utf-8"))
    if source_rows is None:
        response = requests.get(STATION_URL, timeout=45)
        response.raise_for_status()
        source_rows = response.json()
    timestamp = (now or datetime.now(TW_TZ)).isoformat(timespec="seconds")
    registry, stats = merge_registry(
        previous, source_rows, timestamp, load_seed_stations(root / "index.html")
    )
    atomic_write_json(registry_path, registry)
    print("場站名單同步: " + json.dumps(stats, ensure_ascii=False))
    return registry


if __name__ == "__main__":
    sync_station_registry()
