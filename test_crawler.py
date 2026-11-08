#!/usr/bin/env python3
"""
Unit tests for Creator Backlog Crawler & Fresh Upload Prioritization.
"""

import json
import unittest
from pathlib import Path
from creator_backlog_crawler import (
    select_target_upload,
    format_jack_craig_hook,
    get_clipped_video_ids,
)

ROOT_DIR = Path(__file__).resolve().parent


class TestCreatorBacklogCrawler(unittest.TestCase):

    def setUp(self):
        self.test_history = ROOT_DIR / "test_crawler_history.json"
        self.mock_uploads = [
            {"video_id": "vid_latest_100", "title": "Brand New Stunt", "published_at": "2026-11-08T10:00:00Z"},
            {"video_id": "vid_older_99", "title": "Previous Week Challenge", "published_at": "2026-11-01T10:00:00Z"},
            {"video_id": "vid_older_98", "title": "Last Month Experiment", "published_at": "2026-10-15T10:00:00Z"},
            {"video_id": "vid_older_97", "title": "Classic Old Stunt", "published_at": "2026-09-01T10:00:00Z"},
        ]

    def tearDown(self):
        if self.test_history.exists():
            self.test_history.unlink()

    def test_fresh_latest_upload_selected_first(self):
        # Empty history -> must pick the very latest video (index 0)
        res = select_target_upload(
            creator_id="mrbeast",
            history_file=self.test_history,
            mock_uploads=self.mock_uploads,
        )
        self.assertEqual(res["selection_mode"], "fresh_latest_upload")
        self.assertTrue(res["is_latest_unclipped"])
        self.assertEqual(res["backlog_index"], 0)
        self.assertEqual(res["source_video"]["video_id"], "vid_latest_100")
        self.assertIn("Brand New Stunt", res["formatted_title"])
        self.assertIn("(@MrBeast/YT)", res["formatted_title"])

    def test_backlog_older_video_crawl_when_latest_clipped(self):
        # Latest video (vid_latest_100) was already clipped -> must crawl backwards to older #1 (vid_older_99)
        self.test_history.write_text(json.dumps([
            {"creator": "mrbeast", "source_video_id": "vid_latest_100", "video_id": "uploaded_short_1"}
        ]), encoding="utf-8")

        res = select_target_upload(
            creator_id="mrbeast",
            history_file=self.test_history,
            mock_uploads=self.mock_uploads,
        )
        self.assertEqual(res["selection_mode"], "backlog_older_video")
        self.assertFalse(res["is_latest_unclipped"])
        self.assertEqual(res["backlog_index"], 1)
        self.assertEqual(res["source_video"]["video_id"], "vid_older_99")
        self.assertIn("Previous Week Challenge", res["formatted_title"])

    def test_backlog_skips_multiple_already_clipped_videos(self):
        # Latest (vid_latest_100) and older #1 (vid_older_99) both clipped -> crawls to #2 (vid_older_98)
        self.test_history.write_text(json.dumps([
            {"creator": "mrbeast", "source_video_id": "vid_latest_100"},
            {"creator": "mrbeast", "source_video_id": "vid_older_99"},
        ]), encoding="utf-8")

        res = select_target_upload(
            creator_id="mrbeast",
            history_file=self.test_history,
            mock_uploads=self.mock_uploads,
        )
        self.assertEqual(res["selection_mode"], "backlog_older_video")
        self.assertEqual(res["backlog_index"], 2)
        self.assertEqual(res["source_video"]["video_id"], "vid_older_98")
        self.assertIn("Last Month Experiment", res["formatted_title"])

    def test_format_jack_craig_hook(self):
        creator_info = {
            "name": "NileBlue",
            "channel_handle": "@NileBlue",
            "packaging": {"shock_emojis": "🧪😱"},
        }
        title = format_jack_craig_hook("Turning Milk Into Plastic", creator_info)
        self.assertTrue(title.startswith("Can NileBlue Turning Milk Into Plastic?"))
        self.assertIn("🧪😱", title)
        self.assertIn("(@NileBlue/YT)", title)


if __name__ == "__main__":
    unittest.main()
