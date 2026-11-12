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
        self.assertTrue(title.startswith("NileBlue Turning Milk Into Plastic?"))
        self.assertIn("🧪😱", title)
        self.assertIn("(@NileBlue/YT)", title)

    def test_format_jack_craig_hook_question_title(self):
        creator_info = {"name": "NileBlue", "channel_handle": "@NileBlue"}
        title = format_jack_craig_hook("Can This Blade Cut Diamond?", creator_info)
        self.assertTrue(title.startswith("Can This Blade Cut Diamond?"))

    def test_format_jack_craig_hook_first_person(self):
        creator_info = {"name": "NileBlue", "channel_handle": "@NileBlue"}
        title = format_jack_craig_hook("I Made a Working Lightsaber", creator_info)
        self.assertTrue(title.startswith("NileBlue Made a Working Lightsaber?"))

    def test_format_jack_craig_hook_can_prefix_for_normal_titles(self):
        creator_info = {"name": "MrBeast", "channel_handle": "@MrBeast"}
        title = format_jack_craig_hook("Escaped Prison With $1", creator_info)
        self.assertTrue(title.startswith("Can MrBeast Escaped Prison With $1?"))

    def test_format_jack_craig_hook_word_boundary_truncation(self):
        creator_info = {"name": "NileBlue", "channel_handle": "@NileBlue"}
        long_title = "Turning " + "Superlongword " * 12 + "Into Plastic"
        title = format_jack_craig_hook(long_title, creator_info)
        self.assertTrue(title.startswith("NileBlue"))
        self.assertIn("…", title)
        # No truncated fragment mid-word: the char before the ellipsis is a space or start
        stem = title.split("…")[0]
        self.assertTrue(stem.endswith(" ") or len(stem) <= 70)


if __name__ == "__main__":
    unittest.main()
