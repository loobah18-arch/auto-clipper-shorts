#!/usr/bin/env python3
"""
Unit tests for Woo's Clips Multi-Agent Anti-Copyright Filter System.
Fast, mocked tests running in <1s locally on device.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from movie_copyright_filter import (
    MovieVideoCopyrightAgent,
    MovieTitleCopyrightAgent,
    MovieDescriptionCopyrightAgent,
    MovieMultiAgentCopyrightShield,
    MAX_SHORT_DURATION,
    MAX_SHOT_DURATION,
)


class TestMovieTitleCopyrightAgent(unittest.TestCase):
    def setUp(self):
        self.agent = MovieTitleCopyrightAgent(blocked_titles=["Blocked Movie Video Title"])

    def test_rejects_missing_explanation_marker(self):
        res = self.agent.evaluate_title("Dune Part 2 Paul Atreides Speech #shorts", is_short=True)
        self.assertFalse(res.passed)
        self.assertTrue(any("transformative marker" in v for v in res.violations))

    def test_rejects_raw_scraper_episode_tags(self):
        res = self.agent.evaluate_title(
            "Stranger Things S1 Ep 5 Explanation Part 2 #shorts",
            is_short=True,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("scraper pattern" in v for v in res.violations))

    def test_rejects_season_episode_long_pattern(self):
        res = self.agent.evaluate_title(
            "Breaking Bad Season 1 Episode 5 Explanation Part 1 #shorts",
            is_short=True,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("scraper pattern" in v for v in res.violations))

    def test_rejects_blocked_titles(self):
        res = self.agent.evaluate_title(
            "Blocked Movie Video Title Story Recap Ending Explained #shorts",
            is_short=True,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("previously blocked" in v for v in res.violations))

    def test_rejects_missing_shorts_hashtag(self):
        res = self.agent.evaluate_title(
            "Interstellar The Black Hole Secret Ending Explained Part 1",
            is_short=True,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("#shorts" in v for v in res.violations))

    def test_passes_valid_short_title(self):
        res = self.agent.evaluate_title(
            "Dune Part 1 Ending Explained 😱 #Shorts #MovieExplained",
            is_short=True,
        )
        self.assertTrue(res.passed)
        self.assertEqual(len(res.violations), 0)

    def test_passes_valid_full_video_title(self):
        res = self.agent.evaluate_title(
            "Interstellar (2014) Full Movie Explained | Complete Story Recap",
            is_short=False,
        )
        self.assertTrue(res.passed)
        self.assertEqual(len(res.violations), 0)

    def test_remediate_title(self):
        raw = "Stranger Things S1 Ep 5 Part 2"
        remediated = self.agent.remediate_title(raw, is_short=True, part_num=2)
        self.assertIn("Story Recap", remediated)
        self.assertIn("Ending Explained", remediated)
        self.assertIn("#Shorts", remediated)
        self.assertNotIn("S1 Ep 5", remediated)
        # Verify remediated title passes evaluation
        eval_res = self.agent.evaluate_title(remediated, is_short=True)
        self.assertTrue(eval_res.passed)


class TestMovieDescriptionCopyrightAgent(unittest.TestCase):
    def setUp(self):
        self.agent = MovieDescriptionCopyrightAgent()

    def test_rejects_missing_fair_use_disclaimer(self):
        desc = "Here is a quick summary of the movie. Subscribe for more!"
        res = self.agent.evaluate_description(desc)
        self.assertFalse(res.passed)
        self.assertTrue(any("Section 107" in v for v in res.violations))

    def test_passes_description_with_section_107(self):
        desc = (
            "Dune ending explained.\n\n"
            "Copyright Disclaimer Under Section 107 of the Copyright Act 1976:\n"
            "Allowance is made for fair use for transformative commentary and critical analysis."
        )
        res = self.agent.evaluate_description(desc)
        self.assertTrue(res.passed)

    def test_remediate_description(self):
        raw = "A deep look into Paul Atreides' destiny."
        remediated = self.agent.remediate_description(raw)
        self.assertIn("Section 107", remediated)
        self.assertIn("Fair Use", remediated)
        eval_res = self.agent.evaluate_description(remediated)
        self.assertTrue(eval_res.passed)


class TestMovieVideoCopyrightAgent(unittest.TestCase):
    def setUp(self):
        self.agent = MovieVideoCopyrightAgent(max_short_duration=58.5)

    def test_rejects_nonexistent_video(self):
        res = self.agent.evaluate_video("/non/existent/movie.mp4", is_short=True)
        self.assertFalse(res.passed)
        self.assertTrue(any("does not exist" in v for v in res.violations))

    def test_rejects_contiguous_shots_exceeding_threshold(self):
        mock_shots = [
            {"duration": 2.1},
            {"duration": 5.4},  # Exceeds 3.2s
        ]
        res = self.agent.evaluate_video(
            video_path="/non/existent/movie.mp4",
            is_short=True,
            shots_info=mock_shots,
        )
        self.assertFalse(res.passed)
        self.assertTrue(any("contiguous micro-cut threshold" in v for v in res.violations))

    def test_probes_valid_mocked_video(self):
        mock_probe = {
            "format": {"duration": "45.2"},
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920},
                {"codec_type": "audio"},
            ],
        }
        with patch("pathlib.Path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout='{"format": {"duration": "45.2"}, "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}]}'
                )
                res = self.agent.evaluate_video("mock_valid.mp4", is_short=True)
                self.assertTrue(res.passed)
                self.assertEqual(len(res.violations), 0)

    def test_rejects_short_exceeding_safe_duration(self):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout='{"format": {"duration": "61.5"}, "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}]}'
                )
                res = self.agent.evaluate_video("mock_too_long.mp4", is_short=True)
                self.assertFalse(res.passed)
                self.assertTrue(any("exceeds safe ceiling" in v for v in res.violations))


class TestMovieMultiAgentCopyrightShield(unittest.TestCase):
    def setUp(self):
        self.shield = MovieMultiAgentCopyrightShield()

    def test_inspect_candidate_and_remediate(self):
        bad_title = "Inception S1 Ep 2 Part 1"
        bad_desc = "Cobb enters the dream."

        clean_title, clean_desc = self.shield.remediate_metadata(
            title=bad_title,
            description=bad_desc,
            is_short=True,
            part_num=1,
        )
        self.assertIn("Story Recap", clean_title)
        self.assertIn("Ending Explained", clean_title)
        self.assertIn("#Shorts", clean_title)
        self.assertIn("Section 107", clean_desc)

        # Title and description evaluations should pass
        t_res = self.shield.title_agent.evaluate_title(clean_title, is_short=True)
        d_res = self.shield.desc_agent.evaluate_description(clean_desc)
        self.assertTrue(t_res.passed)
        self.assertTrue(d_res.passed)


if __name__ == "__main__":
    unittest.main()
