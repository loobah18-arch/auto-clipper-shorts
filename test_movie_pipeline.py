#!/usr/bin/env python3
"""
Unit tests for the Movie Explanation Shorts pipeline (MovieGyan style).
Tests catalog loading, AI script fallback, subtitle generation, and duration checks.
"""

import os
import sys
import unittest
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

from movie_ai_script import load_catalog, get_movie_from_catalog, generate_movie_script_ai
from movie_video_engine import (
    create_word_timestamps_from_sentences,
    generate_moviegyan_subtitles,
    find_system_font,
    parse_timestamp_to_seconds,
    OUTPUT_DIR
)


class TestMoviePipeline(unittest.TestCase):

    def test_catalog_structure(self):
        catalog = load_catalog()
        self.assertIn("movies", catalog)
        self.assertGreaterEqual(len(catalog["movies"]), 10)
        first_movie = catalog["movies"][0]
        self.assertIn("title", first_movie)
        self.assertIn("script", first_movie)
        self.assertIn("hook", first_movie)
        self.assertIn("badge", first_movie)
        self.assertIn("tags", first_movie)

    def test_get_movie_from_catalog(self):
        m = get_movie_from_catalog("The Platform")
        self.assertIsNotNone(m)
        self.assertIn("Platform", m["title"])

        m2 = get_movie_from_catalog("NonExistentMovie12345")
        self.assertIsNone(m2)

    def test_ai_script_generator_fallback(self):
        res = generate_movie_script_ai("Shutter Island", language="en")
        self.assertIsNotNone(res)
        self.assertIn("script", res)
        self.assertGreater(len(res["script"].split()), 50)

    def test_word_timestamp_generation(self):
        sentences = [
            {"text": "In this terrifying vertical prison", "start": 0.0, "end": 2.5},
            {"text": "hundreds of inmates starve", "start": 2.6, "end": 4.5}
        ]
        words = create_word_timestamps_from_sentences(sentences)
        self.assertEqual(len(words), 9)
        self.assertEqual(words[0]["word"], "IN")
        self.assertAlmostEqual(words[0]["start"], 0.0, places=1)
        self.assertTrue(words[-1]["end"] <= 4.6)

    def test_subtitle_ass_generation(self):
        words = [
            {"word": "IN", "start": 0.0, "end": 0.3},
            {"word": "THIS", "start": 0.3, "end": 0.6},
            {"word": "PRISON", "start": 0.6, "end": 1.0},
            {"word": "SURVIVAL", "start": 1.0, "end": 1.5},
            {"word": "IS", "start": 1.5, "end": 1.8},
            {"word": "IMPOSSIBLE", "start": 1.8, "end": 2.4}
        ]
        test_ass = OUTPUT_DIR / "test_subtitles.ass"
        generate_moviegyan_subtitles(words, test_ass, group_size=3)
        self.assertTrue(test_ass.exists())
        with open(test_ass, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Script Info]", content)
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertIn("&H0000FFFF&", content)  # Neon Yellow active color
        self.assertIn("PRISON", content)
        if test_ass.exists():
            test_ass.unlink()

    def test_font_finder(self):
        font = find_system_font()
        self.assertTrue(len(font) > 0)

    def test_parse_timestamp_to_seconds(self):
        self.assertEqual(parse_timestamp_to_seconds("00:01:30"), 90.0)
        self.assertEqual(parse_timestamp_to_seconds("01:25:00"), 5100.0)
        self.assertEqual(parse_timestamp_to_seconds("05:15"), 315.0)
        self.assertEqual(parse_timestamp_to_seconds("45"), 45.0)

    def test_multipart_movie_catalog(self):
        m = get_movie_from_catalog("The Avengers")
        self.assertIsNotNone(m)
        self.assertIn("parts", m)
        self.assertEqual(len(m["parts"]), 5)
        self.assertEqual(m.get("gdrive_folder_id"), "1Ru9E0k_GkY8jVHpn7S3mwLbJcToUXTRy")
        self.assertTrue(len(m.get("gdrive_file_id")) > 15)

        part1 = m["parts"][0]
        self.assertEqual(part1["part_number"], 1)
        self.assertIn("timeline_start", part1)
        self.assertIn("timeline_end", part1)
        word_count = len(part1["script"].split())
        self.assertTrue(120 <= word_count <= 280)

    def test_select_next_movie_multipart_resolution(self):
        from generate_movie_short import select_next_movie
        res = select_next_movie("The Avengers (2012)", requested_part=2)
        self.assertEqual(res["part_number"], 2)
        self.assertIn("Part 2", res["title"])
        self.assertEqual(res["timeline_start"], "00:26:00")
        self.assertEqual(res["timeline_end"], "00:52:00")


if __name__ == "__main__":
    unittest.main()
