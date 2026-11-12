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
