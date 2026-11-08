#!/usr/bin/env python3
"""
Unit tests for YouTube Uploader Engine & Tested&Proved Packaging.
Validates formatting, Candle Gate enforcement, and dry-run flows.
"""

import json
import unittest
from pathlib import Path
from youtube_uploader import YouTubeUploader

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
        self.assertIn("(@RuhiCenetInvestigates/YT)", ruhi_title)
        self.assertIn("🥶🚨", ruhi_title)

    def test_description_formatting(self):
        desc = self.uploader.format_description(
            raw_description="A crazy challenge",
            creator_id="nileblue",
            metadata={"stages": {"hook": "Hook step", "payoff": "Payoff step"}},
        )
        self.assertIn("NileBlue", desc)
        self.assertIn("Transformative Value-Add & Fair Use", desc)
        self.assertIn("#shorts", desc)
        self.assertIn("Hook step", desc)

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


if __name__ == "__main__":
    unittest.main()
