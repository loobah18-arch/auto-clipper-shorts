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
    generate_minimax_h3_avatar_gesture,
    OUTPUT_DIR
)
from unittest.mock import patch, MagicMock
import io


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
        self.assertIn("002BF5FF", content)  # Neon active highlight
        self.assertIn("fscx118", content)   # Scale pop animation
        if test_ass_path.exists():
            test_ass_path.unlink()

    def test_parse_llm_json_variants(self):
        # Plain json
        j1 = parse_llm_json('{"start_seconds": 45.0, "end_seconds": 85.0, "viral_title": "Epic #shorts"}')
        self.assertEqual(j1["start_seconds"], 45.0)

        # Markdown wrapped
        j2 = parse_llm_json('```json\n{"start_seconds": 12.5, "end_seconds": 55.0, "viral_title": "Hook"}\n```')
        self.assertEqual(j2["start_seconds"], 12.5)

        # Thinking tag wrapped (e.g. reasoning LLMs)
        j3 = parse_llm_json('<think>\nAnalyzing the transcript for hooks...\n</think>\n{"start_seconds": 10.0, "end_seconds": 40.0, "viral_title": "Mindset"}')
        self.assertEqual(j3["start_seconds"], 10.0)

        # Conversational reasoning prefix before JSON
        j4 = parse_llm_json('I have analyzed the transcript and here is the JSON:\n{"start_seconds": 22.0, "end_seconds": 52.0, "viral_title": "Focus"}')
        self.assertEqual(j4["start_seconds"], 22.0)

    def test_format_ass_time(self):
        self.assertEqual(format_ass_time(0.0), "0:00:00.00")
        self.assertEqual(format_ass_time(65.42), "0:01:05.42")
        self.assertEqual(format_ass_time(3661.05), "1:01:01.05")

    def test_channel_picker_rotation(self):
        catalog = {
            "tech_channels": [
                {"id": "fireship", "name": "Fireship"},
                {"id": "networkchuck", "name": "NetworkChuck"}
            ]
        }
        history = {"last_channel_index": 0}
        p, idx = pick_next_channel(catalog, history)
        self.assertEqual(idx, 1)
        self.assertEqual(p["id"], "networkchuck")

    def test_tech_catalog_json_validity(self):
        tech_cat_path = Path(__file__).resolve().parent / "tech_catalog.json"
        self.assertTrue(tech_cat_path.exists())
        with open(tech_cat_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("tech_channels", data)
        self.assertIn("curated_tech_categories", data)
        self.assertGreaterEqual(len(data["tech_channels"]), 3)
        self.assertTrue(any(c["id"] == "fireship" for c in data["tech_channels"]))


    def test_find_system_font(self):
        font_path = find_system_font()
        self.assertIsInstance(font_path, str)
        self.assertTrue(len(font_path) > 0)

    def test_multi_part_series_database_structure(self):
        from generate_tech_fact_short import FALLBACK_SERIES_DATABASE
        self.assertGreaterEqual(len(FALLBACK_SERIES_DATABASE), 3)
        for s in FALLBACK_SERIES_DATABASE:
            self.assertIn("topic", s)
            self.assertIn("badge_base", s)
            self.assertIn("next_topic_teaser", s)
            self.assertGreaterEqual(s["total_parts"], 2)
            self.assertEqual(len(s["parts"]), s["total_parts"])
            
            # Verify Part 1 has cliffhanger / Part 2 hook
            p1_script = s["parts"][0]["script"].lower()
            self.assertTrue("part 2" in p1_script or "next part" in p1_script)
            self.assertTrue("subscribe" in p1_script or "like" in p1_script)
            
            # Verify Final Part has next topic teaser & CTA
            final_script = s["parts"][-1]["script"].lower()
            self.assertTrue("next video" in final_script or "next topic" in final_script or "stay tuned" in final_script)
            self.assertTrue("subscribe" in final_script or "like" in final_script)

    def test_multi_part_series_state_progression(self):
        from generate_tech_fact_short import FALLBACK_SERIES_DATABASE
        series = json.loads(json.dumps(FALLBACK_SERIES_DATABASE[0]))
        series["current_part_index"] = 0
        
        # Step 1: Part 1 -> Part 2
        p1 = series["parts"][series["current_part_index"]]
        self.assertEqual(p1["part_number"], 1)
        next_part_idx = series["current_part_index"] + 1
        self.assertLess(next_part_idx, len(series["parts"]))
        series["current_part_index"] = next_part_idx
        
        # Step 2: Part 2 -> Conclude
        p2 = series["parts"][series["current_part_index"]]
        self.assertEqual(p2["part_number"], 2)
        next_part_idx = series["current_part_index"] + 1
        is_series_complete = (next_part_idx >= len(series["parts"]))
        self.assertTrue(is_series_complete)


    def test_minimax_h3_fallback_when_no_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            res = generate_minimax_h3_avatar_gesture(
                avatar_image_path=Path("non_existent.jpg"),
                topic_prompt="Mindset",
                duration=30.0
            )
            self.assertIsNone(res)

    def test_minimax_h3_mock_success(self):
        # Create a temp dummy image
        test_img = OUTPUT_DIR / "dummy_test_avatar.jpg"
        with open(test_img, "wb") as f:
            f.write(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xDB\x00C\x00")
            
        test_out = OUTPUT_DIR / "test_minimax_out.mp4"
        
        create_resp_json = json.dumps({"task_id": "test_task_123"}).encode("utf-8")
        poll_resp_json = json.dumps({"status": "Success", "video_url": "https://example.com/video.mp4"}).encode("utf-8")
        dummy_mp4_bytes = b"fake mp4 video binary content with length greater than threshold" * 300
        
        def make_cm(data_bytes):
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = data_bytes
            return cm

        responses = [
            make_cm(create_resp_json),
            make_cm(poll_resp_json),
            make_cm(dummy_mp4_bytes)
        ]
        
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test_mm_key_abc"}):
            with patch("urllib.request.urlopen", side_effect=responses), patch("time.sleep", return_value=None):
                res = generate_minimax_h3_avatar_gesture(
                    avatar_image_path=test_img,
                    topic_prompt="Atomic Habits",
                    duration=30.0,
                    output_video_path=test_out
                )
                self.assertIsNotNone(res)
                self.assertTrue(test_out.exists())
                
        if test_img.exists():
            test_img.unlink()
        if test_out.exists():
            test_out.unlink()

    def test_compute_intelligent_shock_condition(self):
        from main import compute_intelligent_shock_condition
        
        sample_segments = [
            {
                "start": 0.0,
                "end": 6.0,
                "words": [
                    {"word": "Most", "start": 0.5},
                    {"word": "people", "start": 1.2},
                    {"word": "think", "start": 2.0},
                    {"word": "internet", "start": 3.0},
                    {"word": "is", "start": 4.0},
                    {"word": "satellites", "start": 5.0}
                ]
            },
            {
                "start": 6.5,
                "end": 14.0,
                "words": [
                    {"word": "But", "start": 7.0},
                    {"word": "actually", "start": 8.5},
                    {"word": "99%", "start": 9.2},
                    {"word": "travels", "start": 10.0},
                    {"word": "through", "start": 10.5},
                    {"word": "underwater", "start": 11.2},
                    {"word": "ocean", "start": 12.0},
                    {"word": "cables", "start": 12.8}
                ]
            }
        ]
        
        # Simulated vocal energy peaks: peak at 8.6s (coinciding with "actually")
        vocal_peaks = [(2.1, 1400.0, 1.45), (8.6, 2200.0, 2.10), (12.1, 1950.0, 1.85)]
        
        shock_expr = compute_intelligent_shock_condition(
            transcript_segments=sample_segments,
            vocal_peaks=vocal_peaks,
            duration=16.0,
            start_sec=0.0
        )
        
        self.assertIn("between(t,", shock_expr)
        # Should align closely with the 8.6s vocal peak (8.50s)
        self.assertIn("8.50", shock_expr)

        # Test calm standard fact without trigger words -> Shock = 0
        calm_segments = [
            {"start": 0.0, "end": 10.0, "words": [{"word": "Water", "start": 1.0}, {"word": "is", "start": 2.0}, {"word": "wet", "start": 3.0}]}
        ]
        calm_shock = compute_intelligent_shock_condition(
            transcript_segments=calm_segments,
            vocal_peaks=[(2.0, 500.0, 1.0)],
            duration=10.0,
            start_sec=0.0
        )
        self.assertEqual(calm_shock, "0")


if __name__ == "__main__":
    unittest.main()
