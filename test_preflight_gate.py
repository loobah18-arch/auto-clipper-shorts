#!/usr/bin/env python3
"""
Unit tests for the 24-hour spacing pre-flight gate (preflight_gate.py).
Pure `decide()` function tests — no sleeping, no network, no GITHUB outputs.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from preflight_gate import decide

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def entry(video_id="r-PaWu5OVgs", ts="2026-11-11T21:05:05.798513+00:00", status="uploaded"):
    return {
        "video_id": video_id,
        "source_video_id": "26P1KyvrmjI",
        "title": "Test Short",
        "url": "https://youtube.com/shorts/" + video_id,
        "creator": "mark_rober",
        "video_path": "output/test.mp4",
        "privacy": "public",
        "timestamp": ts,
        "status": status,
    }


class TestPreflightDecide(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "upload_history.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, records):
        self.path.write_text(json.dumps(records), encoding="utf-8")

    def test_missing_history_file_proceeds(self):
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertIn("no prior successful upload", res["reason"])

    def test_empty_history_proceeds(self):
        self.write([])
        self.assertTrue(decide(self.path, NOW)["proceed"])

    def test_dry_run_only_proceeds(self):
        self.write([entry(video_id="dry_run_1")])
        self.assertTrue(decide(self.path, NOW)["proceed"])

    def test_recent_upload_blocked(self):
        # 5h elapsed < 24h and beyond the 30-min inline window -> skip pipeline
        self.write([entry(ts=(NOW - timedelta(hours=5)).isoformat())])
        res = decide(self.path, NOW)
        self.assertFalse(res["proceed"])
        self.assertEqual(res["sleep_seconds"], 0)
        self.assertAlmostEqual(res["elapsed_hours"], 5.0, places=1)

    def test_skip_reports_exact_due_time(self):
        self.write([entry(ts=(NOW - timedelta(hours=21)).isoformat())])
        res = decide(self.path, NOW)
        self.assertFalse(res["proceed"])
        self.assertEqual(res["remaining_seconds"], 3 * 3600)
        self.assertEqual(res["due_at"], (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_24h_elapsed_proceeds(self):
        self.write([entry(ts=(NOW - timedelta(hours=25)).isoformat())])
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertEqual(res["sleep_seconds"], 0)

    def test_inline_sleep_window(self):
        # 10 minutes remaining -> inline sleep (capped), then proceed
        self.write([entry(ts=(NOW - timedelta(hours=23, minutes=50)).isoformat())])
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertGreater(res["sleep_seconds"], 0)
        self.assertLessEqual(res["sleep_seconds"], 700)

    def test_failed_latest_entry_skipped(self):
        # Last entry FAILED 2h ago; last SUCCESS 30h ago -> proceed (gate measures
        # from the last successful upload, not the last failed attempt)
        self.write([
            entry(ts=(NOW - timedelta(hours=30)).isoformat()),
            entry(video_id="FAILEDvid", ts=(NOW - timedelta(hours=2)).isoformat(), status="failed"),
        ])
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertEqual(res["last_video_id"], "r-PaWu5OVgs")

    def test_multiple_uploads_latest_wins(self):
        self.write([
            entry(ts=(NOW - timedelta(hours=30)).isoformat()),
            entry(video_id="NEWERvid1", ts=(NOW - timedelta(hours=5)).isoformat()),
        ])
        res = decide(self.path, NOW)
        self.assertFalse(res["proceed"])
        self.assertEqual(res["last_video_id"], "NEWERvid1")

    def test_latest_by_timestamp_not_list_order(self):
        # two workflows rebasing the same file can leave an older entry last in the list
        self.write([
            entry(video_id="NEWERvid1", ts=(NOW - timedelta(hours=1)).isoformat()),
            entry(ts=(NOW - timedelta(hours=30)).isoformat()),
        ])
        res = decide(self.path, NOW, min_spacing_hours=2.0, max_inline_wait_hours=0.4)
        self.assertFalse(res["proceed"])
        self.assertEqual(res["last_video_id"], "NEWERvid1")

    def test_two_hour_spacing(self):
        self.write([entry(ts=(NOW - timedelta(hours=1, minutes=10)).isoformat())])
        self.assertFalse(decide(self.path, NOW, min_spacing_hours=2.0, max_inline_wait_hours=0.4)["proceed"])
        self.write([entry(ts=(NOW - timedelta(hours=2, minutes=5)).isoformat())])
        self.assertTrue(decide(self.path, NOW, min_spacing_hours=2.0, max_inline_wait_hours=0.4)["proceed"])

    def test_human_readable_timestamp(self):
        # "Thu 2026-11-12 4:30 PM IST (UTC+05:30)" == 11:00 UTC -> 1h before NOW
        self.write([entry(ts="Thu 2026-11-12 4:30 PM IST (UTC+05:30)")])
        res = decide(self.path, NOW, min_spacing_hours=2.0, max_inline_wait_hours=0.4)
        self.assertFalse(res["proceed"])
        self.assertAlmostEqual(res["elapsed_hours"], 1.0, places=2)

    def test_real_history_is_read(self):
        res = decide(Path(__file__).resolve().parent / "upload_history.json", min_spacing_hours=2.0)
        self.assertIsNotNone(res["last_video_id"])

    def test_malformed_timestamp_fail_open(self):
        self.write([entry(ts="not-a-timestamp")])
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertIn("fail-open", res["reason"])

    def test_entry_without_timestamp_skipped(self):
        self.write([{"video_id": "abc", "status": "uploaded"}])
        self.assertTrue(decide(self.path, NOW)["proceed"])

    def test_corrupt_history_json_fail_open(self):
        self.path.write_text("{not valid json", encoding="utf-8")
        res = decide(self.path, NOW)
        self.assertTrue(res["proceed"])
        self.assertIn("fail-open", res["reason"])


if __name__ == "__main__":
    unittest.main()
