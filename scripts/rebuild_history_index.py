"""Rebuild history coverage metadata without rewriting historical day files."""

import json
import re
from pathlib import Path

try:
    from .fetch_tdx import DATA_DIR, history_index_entry
    from .sync_stations import atomic_write_json
except ImportError:
    from fetch_tdx import DATA_DIR, history_index_entry
    from sync_stations import atomic_write_json


DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def rebuild(data_dir: Path = DATA_DIR):
    index = {}
    for path in sorted(data_dir.glob("*.json")):
        if not DATE_FILE.match(path.name):
            continue
        with path.open(encoding="utf-8") as handle:
            records = json.load(handle)
        if not isinstance(records, list) or not records:
            continue
        index[path.stem] = history_index_entry(records)
    atomic_write_json(data_dir / "index.json", index)
    return index


if __name__ == "__main__":
    rebuilt = rebuild()
    print(f"rebuilt index for {len(rebuilt)} dates")
