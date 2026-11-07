#!/usr/bin/env python3
"""
Unit tests for Isla Value-Add Creator Shorts Clipper.
Verifies:
1. Multi-niche creator catalog loading and schema validation.
2. Two-phase fetcher helpers (time formatting, vtt cleaner).
3. Looped Arc story engine (3 viral criteria, open-loop hook, seamless loop connector).
4. Kinetic karaoke ASS subtitle generation with high-impact styles.
5. Full-bleed 9:16 crop command construction (zero letterbox bars).
6. Standalone title formatting (removal of 'Part X' labels).
7. Upload guard dry-run and description fair use compliance.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from creator_fetcher import (
    CreatorVideoMetadata,
    _clean_vtt_text,
    _format_time_sec,
)
from generate_isla_short import load_catalog, pick_target_url
from isla_story_engine import (
    AudioCutSegment,
    LoopedArcScript,
    generate_deterministic_fallback_script,
    generate_looped_arc_script,
)
from isla_upload_guard import format_isla_description, format_isla_title, upload_isla_short
from isla_video_engine import build_isla_karaoke_ass, crop_to_fullbleed_9x16_cmd


class TestIslaClipper(unittest.TestCase):
    def test_catalog_structure(self):
        catalog = load_catalog()
        self.assertIn("niches", catalog)
        self.assertIn("science_experiments", catalog["niches"])
        self.assertIn("podcasts_mindset", catalog["niches"])
        self.assertIn("tech_inventions", catalog["niches"])

        # Check target creators
        science = catalog["niches"]["science_experiments"]
        creators = [c["name"] for c in science["target_creators"]]
        self.assertIn("NileBlue", creators)

    def test_pick_target_url(self):
        catalog = load_catalog()
        url, creator = pick_target_url(catalog, "science_experiments")
        self.assertTrue(url.startswith("http"))
        self.assertTrue(len(creator) > 0)

    def test_format_time_sec(self):
        self.assertEqual(_format_time_sec(0), "00:00:00")
        self.assertEqual(_format_time_sec(75), "00:01:15")
        self.assertEqual(_format_time_sec(3665), "01:01:05")

    def test_clean_vtt_text(self):
        with tempfile.NamedTemporaryFile("w", suffix=".vtt", delete=False) as f:
            f.write("""WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
<00:00:01.000><c> Hello</c><00:00:01.500><c> world</c>

00:00:03.000 --> 00:00:05.000
<00:00:03.000><c> Testing</c><00:00:03.500><c> science</c>
""")
            vtt_path = Path(f.name)

        try:
            text = _clean_vtt_text(vtt_path)
            self.assertIn("Hello world", text)
            self.assertIn("Testing science", text)
            self.assertNotIn("WEBVTT", text)
        finally:
            if vtt_path.exists():
                vtt_path.unlink()

    def test_looped_arc_structure(self):
        script = generate_deterministic_fallback_script(
            title="Boiling 100 Cans of Red Bull into Powder",
            channel_name="NileBlue",
            duration_sec=600.0,
        )
        self.assertIsInstance(script, LoopedArcScript)
        # Criteria 2: Must have at least 5 segments (Hook -> Process -> Conflict -> Comeback -> Payoff)
        self.assertGreaterEqual(len(script.segments), 5)

        # Criteria 3: Hook must end in question mark or plant question
        self.assertTrue("?" in script.hook_text or "wondered" in script.hook_text)

        # Must have both narrator and creator cut-ins
        speakers = {s.speaker for s in script.segments}
        self.assertIn("narrator", speakers)
        self.assertIn("creator", speakers)

        # Must have silence bgm_action before climax
        bgm_actions = {s.bgm_action for s in script.segments}
        self.assertIn("silence", bgm_actions)

    def test_title_formatting_bans_part_numbering(self):
        script = LoopedArcScript(
            title="Part 4/6: What happened when the flask cracked?",
            concept="Chemistry experiment turnaround",
            hook_text="What happened?",
            loop_connector="And that's why...",
            segments=[],
            raw_clip_start=10.0,
            raw_clip_end=55.0,
            estimated_duration=45.0,
            keywords=["science"],
        )
        formatted = format_isla_title(script, "NileBlue")
        self.assertNotIn("Part 4/6", formatted)
        self.assertNotIn("Part 4", formatted)
        self.assertIn("#shorts", formatted)

    def test_description_has_creator_credit_and_fair_use(self):
        script = LoopedArcScript(
            title="Insane Experiment",
            concept="Testing high voltage arc",
            hook_text="Can voltage pierce glass?",
            loop_connector="Which is why...",
            segments=[],
            raw_clip_start=10.0,
            raw_clip_end=55.0,
            estimated_duration=45.0,
            keywords=["voltage"],
        )
        desc = format_isla_description(script, "ActionLab", "https://youtube.com/watch?v=123")
        self.assertIn("ActionLab", desc)
        self.assertIn("Original Creator", desc)
        self.assertIn("Fair Use", desc)

    def test_fullbleed_crop_command(self):
        cmd = crop_to_fullbleed_9x16_cmd(
            input_video=Path("test_in.mp4"),
            output_video=Path("test_out.mp4"),
            start_sec=10.5,
            duration_sec=45.0,
        )
        cmd_str = " ".join(cmd)
        self.assertIn("scale=1080*16/9:1920:force_original_aspect_ratio=increase", cmd_str)
        self.assertIn("crop=1080:1920", cmd_str)
        self.assertIn("-ss 10.50", cmd_str)
        self.assertIn("-t 45.00", cmd_str)

    def test_build_karaoke_ass(self):
        script = generate_deterministic_fallback_script("Test Video", "Creator", 300.0)
        with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False) as f:
            temp_ass = Path(f.name)

        try:
            build_isla_karaoke_ass(script, temp_ass, video_width=1080, video_height=1920)
            content = temp_ass.read_text(encoding="utf-8")
            self.assertIn("[Script Info]", content)
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn("Dialogue:", content)
            self.assertIn("420", content)
            self.assertIn("&H0000FFFF&", content)
        finally:
            if temp_ass.exists():
                temp_ass.unlink()

    def test_nile_blue_redbull_benchmark_script(self):
        script = generate_deterministic_fallback_script(
            title="Making the world's most powerful Red Bull",
            channel_name="NileBlue",
            duration_sec=2533.0,
        )
        self.assertGreaterEqual(len(script.scenes), 3)
        self.assertEqual(script.scenes[0].name, "setup")
        self.assertEqual(script.scenes[1].name, "conflict")
        self.assertEqual(script.scenes[2].name, "payoff")
        self.assertEqual(script.scenes[0].start_sec, 76.0)
        self.assertEqual(script.scenes[1].start_sec, 1655.0)
        self.assertEqual(script.scenes[2].start_sec, 2355.0)
        self.assertIn("100 Red Bulls into one drink", script.title)
        self.assertIn("What would happen", script.hook_text)

    def test_upload_guard_dry_run(self):
        script = generate_deterministic_fallback_script("Test", "Creator", 200.0)
        result = upload_isla_short(
            video_path=Path("dummy.mp4"),
            script=script,
            channel_name="NileBlue",
            source_url="https://youtube.com/watch?v=abc",
            dry_run=True,
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("dry_run_", result["youtube_id"])


if __name__ == "__main__":
    unittest.main()
