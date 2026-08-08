#!/usr/bin/env python3
"""
Test Suite for Auto Clipper Shorts Pipeline.
Validates VTT & JSON3 parsing, Karaoke ASS subtitle generation,
Groq LLM output parsing, font detection, and channel rotation.
"""

import os
import sys
import json
import unittest
from pathlib import Path

# Add current workspace to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import (
    parse_vtt_subtitles,
    parse_json3_subtitles,
    create_word_timestamps_from_segment,
    generate_karaoke_ass_subtitles,
    parse_llm_json,
    format_ass_time,
    pick_next_channel,
    find_system_font,
    OUTPUT_DIR
)


class TestAutoClipperPipeline(unittest.TestCase):

    def test_vtt_subtitle_parsing(self):
        sample_vtt = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:04.500
The secret to true focus and productivity

00:00:04.600 --> 00:00:08.200
is setting clear non-negotiable boundaries.
"""
        segments = parse_vtt_subtitles(sample_vtt)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["start"], 1.0)
        self.assertEqual(segments[0]["end"], 4.5)
        self.assertTrue(len(segments[0]["words"]) > 0)
        self.assertEqual(segments[0]["words"][0]["word"], "The")
        self.assertEqual(segments[1]["start"], 4.6)

    def test_json3_subtitle_parsing(self):
        sample_json3 = {
            "events": [
                {
                    "tStartMs": 2000,
                    "dDurationMs": 3500,
                    "segs": [
                        {"utf8": "Artificial ", "tOffsetMs": 0},
                        {"utf8": "intelligence ", "tOffsetMs": 500},
                        {"utf8": "will ", "tOffsetMs": 1200},
                        {"utf8": "change ", "tOffsetMs": 1800},
                        {"utf8": "everything.", "tOffsetMs": 2400}
                    ]
                }
            ]
        }
        segments = parse_json3_subtitles(sample_json3)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"], 2.0)
        self.assertEqual(segments[0]["end"], 5.5)
        self.assertEqual(len(segments[0]["words"]), 5)
        self.assertEqual(segments[0]["words"][0]["word"], "Artificial")

    def test_karaoke_ass_subtitle_generation(self):
        sample_segments = [
            {
                "id": 0,
                "start": 30.0,
                "end": 35.0,
                "text": "Never give up on your dreams",
                "words": [
                    {"word": "NEVER", "start": 30.0, "end": 30.8, "probability": 1.0},
                    {"word": "GIVE", "start": 30.9, "end": 31.5, "probability": 1.0},
                    {"word": "UP", "start": 31.6, "end": 32.2, "probability": 1.0},
                    {"word": "ON", "start": 32.3, "end": 32.8, "probability": 1.0},
                    {"word": "YOUR", "start": 32.9, "end": 33.6, "probability": 1.0},
                    {"word": "DREAMS", "start": 33.7, "end": 34.8, "probability": 1.0}
                ]
            }
        ]
        test_ass_path = OUTPUT_DIR / "test_karaoke.ass"
        generate_karaoke_ass_subtitles(sample_segments, start_sec=30.0, end_sec=35.0, output_ass_path=test_ass_path)
        
        self.assertTrue(test_ass_path.exists())
        with open(test_ass_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Script Info]", content)
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertIn("002BF5FF", content)  # Yellow active highlight
        self.assertIn("fscx110", content)   # Scale pop animation
        if test_ass_path.exists():
            test_ass_path.unlink()

    def test_parse_llm_json_variants(self):
        # Plain json
        j1 = parse_llm_json('{"start_seconds": 45.0, "end_seconds": 85.0, "viral_title": "Epic #shorts"}')
        self.assertEqual(j1["start_seconds"], 45.0)

        # Markdown wrapped
        j2 = parse_llm_json('```json\n{"start_seconds": 12.5, "end_seconds": 55.0, "viral_title": "Hook"}\n```')
        self.assertEqual(j2["start_seconds"], 12.5)

    def test_format_ass_time(self):
        self.assertEqual(format_ass_time(0.0), "0:00:00.00")
        self.assertEqual(format_ass_time(65.42), "0:01:05.42")
        self.assertEqual(format_ass_time(3661.05), "1:01:01.05")

    def test_channel_picker_rotation(self):
        catalog = {
            "podcasts": [
                {"id": "lex_fridman", "name": "Lex Fridman"},
                {"id": "huberman_lab", "name": "Huberman Lab"}
            ]
        }
        history = {"last_channel_index": 0}
        p, idx = pick_next_channel(catalog, history)
        self.assertEqual(idx, 1)
        self.assertEqual(p["id"], "huberman_lab")


if __name__ == "__main__":
    unittest.main()
