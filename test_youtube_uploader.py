#!/usr/bin/env python3
"""
Unit tests for YouTube Uploader Engine & Tested&Proved Packaging.
Validates formatting, Candle Gate enforcement, and dry-run flows.
"""

import json
import unittest
from pathlib import Path
from youtube_uploader import YouTubeUploader, resolve_next_creator_rotation

ROOT_DIR = Path(__file__).resolve().parent


class TestYouTubeUploader(unittest.TestCase):

    def setUp(self):
        self.test_history = ROOT_DIR / "test_upload_history.json"
        self.uploader = YouTubeUploader(history_file=self.test_history)

    def tearDown(self):
        if self.test_history.exists():
            self.test_history.unlink()

    def test_title_formatting(self):
        title_raw = "Can He Escape 100 Real Cops For $500,000?"
        mrbeast_title = self.uploader.format_title(title_raw, creator_id="mrbeast")
        self.assertIn("(@MrBeast/YT)", mrbeast_title)
        self.assertIn("😳😳", mrbeast_title)

        speed_title = self.uploader.format_title(title_raw, creator_id="ishowspeed")
        self.assertIn("(@IShowSpeed/YT)", speed_title)
        self.assertIn("⚡🔥", speed_title)

        ruhi_title = self.uploader.format_title(title_raw, creator_id="ruhi_cenet")
        self.assertIn("(@RuhiCenetDocumentaries/YT)", ruhi_title)
        self.assertIn("🥶🚨", ruhi_title)

    def test_description_formatting(self):
        desc = self.uploader.format_description(
            raw_description="A crazy challenge",
            creator_id="nileblue",
            metadata={"stages": {"hook": "Hook step", "payoff": "Payoff step"}},
        )
        self.assertIn("NileRed", desc)
        self.assertIn("Transformative Value-Add & Fair Use", desc)
        self.assertIn("#shorts", desc)
        self.assertIn("Hook step", desc)

    def test_run_timer_mock_sleep(self):
        sleep_calls = []
        mock_sleep = lambda s: sleep_calls.append(s)
        self.uploader.run_timer(seconds=1800, interval=600, sleep_fn=mock_sleep)
        self.assertEqual(sum(sleep_calls), 1800)
        self.assertEqual(len(sleep_calls), 3)

    def test_resolve_next_creator_rotation(self):
        # Empty history defaults to the NileRed-first rotation (the channel's niche)
        res = resolve_next_creator_rotation(history_file=self.test_history)
        self.assertEqual(res["creator_id"], "nileblue")

        # After nileblue, next in rotation is mrbeast
        self.test_history.write_text(json.dumps([{"creator": "nileblue", "video_id": "test1"}]), encoding="utf-8")
        res2 = resolve_next_creator_rotation(history_file=self.test_history)
        self.assertEqual(res2["creator_id"], "mrbeast")
        self.assertEqual(res2["output_filename"], "hypit_mrbeast_short.mp4")

        # After the last creator in the catalog, it loops back to nileblue
        last = list(json.loads((ROOT_DIR / "creator_catalog.json").read_text(encoding="utf-8"))["creators"])[-1]
        self.test_history.write_text(json.dumps([{"creator": last, "video_id": "test2"}]), encoding="utf-8")
        res3 = resolve_next_creator_rotation(history_file=self.test_history)
        self.assertEqual(res3["creator_id"], "nileblue")

    def test_dry_run_upload_and_history(self):
        video_sample = ROOT_DIR / "test_sample_video.mp4"
        video_sample.write_bytes(b"dummy mp4 content")
        try:
            res = self.uploader.upload(
                video_path=video_sample,
                creator_id="mrbeast",
                dry_run=True,
                force=True,
            )
            self.assertEqual(res["status"], "dry_run_success")
            self.assertIn("https://youtube.com/shorts/", res["url"])
            self.assertIn("(@MrBeast/YT)", res["title"])
        finally:
            if video_sample.exists():
                video_sample.unlink()

    def test_upload_short_keeps_moment_title(self):
        video_sample = ROOT_DIR / "test_sample_short.mp4"
        video_sample.write_bytes(b"dummy mp4 content")
        try:
            t = "Chat Spammed \"NO WAY\" After xQc Did This in GTA 6 😳"
            res = self.uploader.upload_short(video_path=video_sample,
                                             metadata={"title": t, "description": t + "\nCredit: @xQc", "tags": ["GTA6"]},
                                             dry_run=True, bypass_gate=True, creator_id="xqc", title=t)
            self.assertEqual(res["status"], "dry_run_success")
            self.assertEqual(res["title"], t)
        finally:
            video_sample.unlink(missing_ok=True)

    def test_upload_spacing_blocks_double_post(self):
        import os
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        self.test_history.write_text(json.dumps([{"video_id": "abc123", "status": "uploaded", "timestamp": recent}]),
                                     encoding="utf-8")
        ok, msg = self.uploader.check_upload_spacing(2.0)
        self.assertFalse(ok)
        self.assertIn("abc123", msg)
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.test_history.write_text(json.dumps([{"video_id": "abc123", "status": "uploaded", "timestamp": old}]),
                                     encoding="utf-8")
        self.assertTrue(self.uploader.check_upload_spacing(2.0)[0])


if __name__ == "__main__":
    unittest.main()
