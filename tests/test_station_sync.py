import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import fetch_tdx, sync_stations


def source(code="500101003", status=1, name="測試站", capacity=28):
    return {"station_no": code, "status": status, "name_tw": "YouBike2.0_" + name,
            "district_tw": "大安區", "parking_spaces": capacity}


def station(code="500101003"):
    return {"code": code, "name": "原站名", "city": "台北市", "district": "大安區",
            "zone": "ZB3", "capacity": 28, "status": 1,
            "first_seen_at": "2026-05-18T06:00:00+08:00", "last_seen_at": "2026-08-26T12:00:00+08:00"}


def tdx_source(code="500101003", status=1, capacity=28):
    return {"StationID": code, "StationUID": "TPE" + code,
            "StationName": {"Zh_tw": "YouBike2.0_新站名"},
            "BikesCapacity": capacity, "ServiceStatus": status,
            "StationAddress": {"Zh_tw": "非行政區欄位"}}


class RegistryTests(unittest.TestCase):
    def test_both_open_and_suspended_and_string_status_are_public(self):
        rows = sync_stations.normalize_source([source(), source("500201001", "2")])
        self.assertEqual(set(rows), {"500101003", "500201001"})
        self.assertEqual(rows["500201001"]["status"], 2)
        self.assertEqual(rows["500201001"]["city"], "新北市")

    def test_internal_codes_not_public(self):
        rows = sync_stations.normalize_source([source(), source("500199001"), source("500299001")])
        self.assertEqual(list(rows), ["500101003"])

    def test_union_preserves_missing_suspended_and_history_fields(self):
        previous = {"schema_version": 1, "stations": [station(), station("500101004")]}
        previous["stations"][0]["deltas"] = [7] * 24
        original = copy.deepcopy(previous)
        result, stats = sync_stations.merge_registry(
            previous, [source(status=2, name="新站名", capacity=32), source("500201005")], "now")
        rows = {s["code"]: s for s in result["stations"]}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["500101003"]["name"], "新站名")
        self.assertEqual(rows["500101003"]["status"], 2)
        self.assertEqual(rows["500101003"]["zone"], "ZB3")
        self.assertEqual(rows["500101003"]["deltas"], [7] * 24)
        self.assertFalse(rows["500101004"]["source_present"])
        self.assertEqual(rows["500101004"]["last_seen_at"], original["stations"][1]["last_seen_at"])
        self.assertEqual(rows["500201005"]["zone"], "待設定")
        self.assertEqual(stats["retained_missing"], 1)
        self.assertEqual(previous, original)

    def test_seed_cannot_overwrite_newer_registry_metadata(self):
        previous = {"schema_version": 1, "stations": [station()]}
        previous["stations"][0]["zone"] = "ZB4"
        result, _ = sync_stations.merge_registry(previous, [source()], "now", [station()])
        self.assertEqual(result["stations"][0]["zone"], "ZB4")

    def test_repeat_sync_does_not_duplicate_or_change_first_seen(self):
        first, _ = sync_stations.merge_registry(None, [source()], "first")
        second, stats = sync_stations.merge_registry(first, [source(status=2)], "second")
        self.assertEqual(len(second["stations"]), 1)
        self.assertEqual(second["stations"][0]["first_seen_at"], "first")
        self.assertEqual(second["stations"][0]["last_seen_at"], "second")
        self.assertEqual(stats["added"], 0)

    def test_incomplete_source_row_does_not_blank_existing_metadata(self):
        old = station()
        bad = source()
        bad["name_tw"] = ""
        result, _ = sync_stations.merge_registry(
            {"schema_version": 1, "stations": [old]}, [bad, source("500201005")], "now")
        retained = next(s for s in result["stations"] if s["code"] == old["code"])
        self.assertEqual(retained["name"], old["name"])
        self.assertEqual(retained["capacity"], old["capacity"])

    def test_zero_capacity_suspended_station_is_retained(self):
        rows = sync_stations.normalize_source([source(status=2, capacity=0)])
        self.assertEqual(rows["500101003"]["capacity"], 0)

    def test_empty_invalid_duplicate_sources_fail_closed(self):
        for rows in ([], {}, [source(), source()], [source(capacity=-1)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                sync_stations.normalize_source(rows)

    def test_invalid_existing_registry_fails_closed(self):
        for old in ([], {}, {"schema_version": 1, "stations": [station(), station()]}):
            with self.subTest(old=old), self.assertRaises(ValueError):
                sync_stations.merge_registry(old, [source()], "now")

    def test_api_error_does_not_replace_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            path = root / "data" / "stations.json"
            path.write_text('{"schema_version":1,"stations":[]}', encoding="utf-8")
            before = path.read_bytes()
            with patch.object(sync_stations.requests, "get", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    sync_stations.sync_station_registry(root)
            self.assertEqual(path.read_bytes(), before)

    def test_atomic_write_failure_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text("original", encoding="utf-8")
            with self.assertRaises(ValueError):
                sync_stations.atomic_write_json(path, {"invalid": float("nan")})
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(Path(directory).iterdir()), [path])


class TdxFallbackTests(unittest.TestCase):
    def test_tdx_updates_metadata_and_status_without_overwriting_cps_fields(self):
        previous = {"schema_version": 1, "stations": [station(), station("500101004")]}
        previous["stations"][0]["deltas"] = [3] * 24
        result, stats = sync_stations.merge_registry(
            previous, [tdx_source(status=2, capacity=30), tdx_source("500201005", status=0)],
            "now", source_kind="tdx")
        rows = {s["code"]: s for s in result["stations"]}
        self.assertEqual(rows["500101003"]["name"], "新站名")
        self.assertEqual(rows["500101003"]["status"], 2)
        self.assertEqual(rows["500101003"]["district"], "大安區")
        self.assertEqual(rows["500101003"]["zone"], "ZB3")
        self.assertEqual(rows["500101003"]["deltas"], [3] * 24)
        self.assertEqual(rows["500201005"]["district"], "待確認")
        self.assertEqual(rows["500201005"]["zone"], "待設定")
        self.assertEqual(rows["500201005"]["status"], 0)
        self.assertIn("500101004", rows)
        self.assertEqual(stats["source_suspended"], 1)
        self.assertEqual(stats["source_stopped"], 1)
        self.assertEqual(result["source_urls"], sync_stations.TDX_URLS)

    def test_missing_status_is_unknown_and_null_capacity_preserves_known_value(self):
        rows = sync_stations.normalize_tdx_source(
            [tdx_source(status=None, capacity=None), tdx_source("500201005", status=None)],
            {"500101003": station()})
        self.assertEqual(rows["500101003"]["capacity"], 28)
        self.assertNotIn("status", rows["500201005"])

    def test_unknown_new_capacity_is_not_invented_as_zero(self):
        rows = sync_stations.normalize_tdx_source([tdx_source(), tdx_source("500201005", capacity=None)], {})
        self.assertNotIn("500201005", rows)

    def test_tdx_uid_fallback_is_strict_and_internal_codes_stay_excluded(self):
        self.assertEqual(sync_stations.tdx_station_code({"StationUID": "NWT500201005"}), "500201005")
        for uid in ("garbage500201005", "TPE500199001", "NWT500299001", "TPE123"):
            self.assertIsNone(sync_stations.tdx_station_code({"StationUID": uid}))

    def test_empty_duplicate_and_invalid_tdx_metadata_fail_closed(self):
        for rows in ([], {}, [tdx_source(), tdx_source()], [tdx_source(capacity=-1)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                sync_stations.normalize_tdx_source(rows, {})

    def test_later_cps_seed_can_fill_only_unknown_district_and_zone(self):
        old = station()
        old.update(district="待確認", zone="待設定")
        result, _ = sync_stations.merge_registry(
            {"schema_version": 1, "stations": [old]}, [tdx_source()], "now", [station()], "tdx")
        self.assertEqual(result["stations"][0]["district"], "大安區")
        self.assertEqual(result["stations"][0]["zone"], "ZB3")

    def test_metadata_joins_real_availability_status_by_station_code(self):
        responses = [
            [tdx_source()], [{"StationUID": "TPE500101003", "ServiceStatus": 2}],
            [tdx_source("500201005")], [{"StationID": "500201005", "ServiceStatus": 0}],
        ]
        with patch.object(fetch_tdx, "fetch", side_effect=responses) as fetch:
            rows = fetch_tdx.station_metadata("authorized-token")
        self.assertEqual([row["ServiceStatus"] for row in rows], [2, 0])
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(all(call.args[1] == "authorized-token" for call in fetch.call_args_list))

    def test_primary_403_uses_authorized_fallback_and_still_captures(self):
        with patch.object(fetch_tdx, "sync_station_registry", side_effect=[RuntimeError("403"), None]) as sync, \
             patch.object(fetch_tdx, "get_token", return_value="test"), \
             patch.object(fetch_tdx, "station_metadata", return_value=[tdx_source()]), \
             patch.object(fetch_tdx, "SHOTS", 1), \
             patch.object(fetch_tdx, "snapshot_once", return_value=[]), \
             patch.object(fetch_tdx, "save_record") as save:
            fetch_tdx.main()
            self.assertEqual(sync.call_args.kwargs["source_kind"], "tdx")
            save.assert_called_once()

    def test_all_pages_are_read_so_new_stations_outside_first_page_are_not_missed(self):
        with patch.object(fetch_tdx, "fetch", side_effect=[
            [tdx_source(), tdx_source("500101004")], [tdx_source("500101005")]
        ]) as fetch:
            rows = fetch_tdx.fetch_all_station_rows("/station", "test", page_size=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(fetch.call_args.args[2]["$skip"], 2)

    def test_repeated_page_fails_instead_of_looping_or_silently_truncating(self):
        with patch.object(fetch_tdx, "fetch", return_value=[tdx_source()]):
            with self.assertRaises(ValueError):
                fetch_tdx.fetch_all_station_rows("/station", "test", page_size=1)


class HistoryTests(unittest.TestCase):
    def test_append_unknown_and_suspended_station_without_losing_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch_tdx, "DATA_DIR", Path(directory)):
            old = {"time": "06:00", "datetime": "2026-08-27T06:00:00+08:00", "stations": {"TPE500101003": [5, 23]}}
            path = Path(directory) / "2026-08-27.json"
            path.write_text(json.dumps([old]), encoding="utf-8")
            fetch_tdx.save_record(datetime.fromisoformat("2026-08-27T06:10:00+08:00"), [
                {"id": "TPE500199999", "rent": 0, "return": 0},
                {"id": "TPE500101999", "rent": 3, "return": 17},
                {"id": "TPE500101004", "rent": None, "return": None},
            ])
            saved = json.loads(path.read_text())
            self.assertEqual(saved[0], old)
            self.assertEqual(saved[1]["stations"]["TPE500101999"], [3, 17])
            self.assertEqual(saved[1]["stations"]["TPE500199999"], [0, 0])
            self.assertEqual(saved[1]["stations"]["TPE500101004"], [None, None])
            self.assertEqual(json.loads((Path(directory) / "index.json").read_text())["2026-08-27"]["count"], 2)

    def test_midnight_creates_new_day_and_preserves_previous(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch_tdx, "DATA_DIR", Path(directory)):
            rows = [{"id": "TPE500101003", "rent": 1, "return": 27}]
            fetch_tdx.save_record(datetime.fromisoformat("2026-08-27T23:59:00+08:00"), rows)
            previous = (Path(directory) / "2026-08-27.json").read_bytes()
            fetch_tdx.save_record(datetime.fromisoformat("2026-08-28T00:00:00+08:00"), rows)
            self.assertEqual((Path(directory) / "2026-08-27.json").read_bytes(), previous)
            self.assertEqual(set(json.loads((Path(directory) / "index.json").read_text())), {"2026-08-27", "2026-08-28"})

    def test_empty_observations_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch_tdx, "DATA_DIR", Path(directory)):
            with self.assertRaises(ValueError):
                fetch_tdx.save_record(datetime.now(), [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_bad_history_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch_tdx, "DATA_DIR", Path(directory)):
            path = Path(directory) / "2026-08-27.json"
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                fetch_tdx.save_record(datetime.fromisoformat("2026-08-27T12:00:00+08:00"), [])
            self.assertEqual(path.read_text(), "broken")

    def test_metadata_failure_still_collects_history_and_is_reported(self):
        with patch.object(fetch_tdx, "sync_station_registry", side_effect=RuntimeError("offline")), \
             patch.object(fetch_tdx, "get_token", return_value="test"), \
             patch.object(fetch_tdx, "station_metadata", side_effect=RuntimeError("offline")), \
             patch.object(fetch_tdx, "SHOTS", 1), \
             patch.object(fetch_tdx, "snapshot_once", return_value=[{"id": "new", "rent": 2, "return": 8}]), \
             patch.object(fetch_tdx, "save_record") as save:
            with self.assertRaises(SystemExit):
                fetch_tdx.main()
            save.assert_called_once()

    def test_all_snapshot_failures_are_not_reported_as_success(self):
        with patch.object(fetch_tdx, "sync_station_registry"), \
             patch.object(fetch_tdx, "get_token", return_value="test"), \
             patch.object(fetch_tdx, "SHOTS", 1), \
             patch.object(fetch_tdx, "snapshot_once", side_effect=RuntimeError("offline")), \
             patch.object(fetch_tdx, "save_record") as save:
            with self.assertRaises(SystemExit):
                fetch_tdx.main()
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
