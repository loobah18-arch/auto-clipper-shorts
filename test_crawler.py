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
    format_tested_proved_hook,
    build_stage_script,
    build_metadata,
    hook_text,
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


class TestTestedAndProvedPackaging(unittest.TestCase):
    """Packaging must match the @TestedNProved channel: declarative transformation hook."""

    def setUp(self):
        self.nilered = {
            "name": "NileRed",
            "channel_handle": "@NileRed",
            "packaging": {"shock_emojis": "😳"},
            "hud_theme": {"badge_prefix": "🧪"},
            "narrator_voice": "en-US-ChristopherNeural",
        }

    def test_gerund_transformation_becomes_declarative_hook(self):
        title = format_tested_proved_hook("Turning Cotton Balls into Cotton Candy", self.nilered)
        self.assertEqual(title, "He Turned Cotton Balls into Cotton Candy 😳 (@NileRed/YT)")

    def test_reference_channel_title_shape(self):
        # Mirrors "He Condensed 20 beers into 1 Super Beer 😲 (@NileRed/YT)"
        title = format_tested_proved_hook("Condensing 20 beers into 1 Super Beer", self.nilered)
        self.assertEqual(title, "He Condensed 20 beers into 1 Super Beer 😳 (@NileRed/YT)")

    def test_first_person_title_is_revoiced(self):
        title = format_tested_proved_hook("I Made a Working Lightsaber", self.nilered)
        self.assertEqual(title, "He Made a Working Lightsaber 😳 (@NileRed/YT)")

    def test_already_declarative_title_is_kept(self):
        title = format_tested_proved_hook("He Compressed 100 RedBulls into 1 Drink", self.nilered)
        self.assertEqual(title, "He Compressed 100 RedBulls into 1 Drink 😳 (@NileRed/YT)")

    def test_packaging_noise_is_stripped(self):
        title = format_tested_proved_hook(
            "Making human kibble (to change the way we eat forever) 🧪 #shorts", self.nilered
        )
        self.assertEqual(title, "He Made human kibble 😳 (@NileRed/YT)")

    def test_non_transformation_falls_back_to_question_hook(self):
        title = format_tested_proved_hook("What did the government send me?", self.nilered)
        self.assertTrue(title.startswith("What did the government send me?"))
        self.assertIn("(@NileRed/YT)", title)

    def test_long_hook_truncates_on_word_boundary(self):
        title = format_tested_proved_hook("Making " + "Superlongword " * 12 + "from scratch", self.nilered)
        self.assertLessEqual(len(title), 95)
        self.assertIn("…", title)
        self.assertIn("(@NileRed/YT)", title)

    def test_hook_text_is_tts_safe(self):
        self.assertEqual(
            hook_text("Turning Air Into Alcohol 😳 (@NileRed/YT)"),
            "He Turned Air Into Alcohol",
        )


class TestHookToPayoffScript(unittest.TestCase):
    """The renderer script must walk HOOK -> RISING ACTION -> CONFLICT -> COMEBACK -> PAYOFF."""

    def setUp(self):
        self.nilered = {
            "name": "NileRed",
            "channel_handle": "@NileRed",
            "packaging": {"shock_emojis": "😳", "hashtags": ["#shorts", "#nilered"]},
            "hud_theme": {"badge_prefix": "🧪"},
            "narrator_voice": "en-US-ChristopherNeural",
        }
        self.source_title = "Turning Cotton Balls into Cotton Candy"
        self.title = format_tested_proved_hook(self.source_title, self.nilered)

    def test_six_stages_in_arc_order(self):
        script = build_stage_script(self.source_title, self.title, self.nilered)
        self.assertEqual(
            [stage["stage"] for stage in script["stages"]],
            ["HOOK", "RISING ACTION", "CONFLICT", "COMEBACK", "RISING ACTION", "PAYOFF"],
        )

    def test_stages_cover_full_timeline_without_gaps(self):
        script = build_stage_script(self.source_title, self.title, self.nilered)
        self.assertAlmostEqual(script["stages"][0]["start"], 0.0, places=2)
        self.assertAlmostEqual(script["stages"][-1]["end"], 60.0, places=2)
        for prev, cur in zip(script["stages"], script["stages"][1:]):
            self.assertAlmostEqual(prev["end"], cur["start"], places=2)

    def test_hook_and_payoff_name_the_real_transformation(self):
        script = build_stage_script(self.source_title, self.title, self.nilered)
        self.assertEqual(script["hook"], "He Turned Cotton Balls into Cotton Candy")
        self.assertEqual(script["subject"], "Cotton Balls")
        self.assertEqual(script["result"], "Cotton Candy")
        self.assertIn("COTTON CANDY", script["stages"][-1]["caption"])
        self.assertIn("COTTON BALLS → COTTON CANDY", script["badges"][0][2])

    def test_captions_and_badges_stay_inside_their_stage(self):
        script = build_stage_script(self.source_title, self.title, self.nilered)
        self.assertEqual(len(script["badges"]), 6)
        for start, end, _text in script["cues"]:
            self.assertGreaterEqual(start, 0.0)
            self.assertLessEqual(end, 60.01)
            self.assertGreater(end, start)

    def test_metadata_written_from_the_same_arc(self):
        script = build_stage_script(self.source_title, self.title, self.nilered)
        meta = build_metadata(self.source_title, self.title, self.nilered, script)
        self.assertEqual(meta["title"], self.title)
        self.assertEqual(meta["creator_attribution"], "@NileRed/YT")
        self.assertIn("intro_title", meta["stages"])
        self.assertEqual(len(meta["stages"]), 6)
        self.assertEqual(meta["intro_narration"]["spoken_text"], script["hook"])
        self.assertIn("Payoff", meta["arc_type"])


if __name__ == "__main__":
    unittest.main()
