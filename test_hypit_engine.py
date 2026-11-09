#!/usr/bin/env python3
"""
Unit tests for Hypit Video Engine.
Validates SVML/SVS/SVRun parsing, subtitle generation, and pipeline integrity.
"""

import unittest
from pathlib import Path
from hypit_engine import HypitSVMLParser, HypitRunParser, HypitEngine

ROOT_DIR = Path(__file__).resolve().parent
SOURCES_DIR = ROOT_DIR / "sources"


class TestHypitEngine(unittest.TestCase):

    def setUp(self):
        self.svrun_path = SOURCES_DIR / "production.svrun"
        self.look_path = SOURCES_DIR / "look.svs"
        self.challenge_svml = SOURCES_DIR / "mrbeast_challenge.svml"

    def test_sources_exist(self):
        self.assertTrue(self.challenge_svml.exists(), "mrbeast_challenge.svml should exist")
        self.assertTrue(self.svrun_path.exists(), "production.svrun should exist")
        self.assertTrue(self.look_path.exists(), "look.svs should exist")

    def test_svml_parser(self):
        parser = HypitSVMLParser(self.challenge_svml).parse()
        self.assertEqual(parser.canvas_width, 1080)
        self.assertEqual(parser.canvas_height, 1440)
        self.assertEqual(parser.fps, 30)
        self.assertEqual(parser.timeline_duration, 60.0)
        self.assertIn("title-hook", parser.windows)
        self.assertIn("hook", parser.windows)
        self.assertIn("comeback", parser.windows)
        self.assertIn("climax", parser.windows)
        self.assertIn("payoff", parser.windows)
        self.assertIn("intro", parser.script_segments)
        self.assertIn("opening", parser.script_segments)
        self.assertIn("comeback", parser.script_segments)
        self.assertIn("payoff", parser.script_segments)

    def test_svrun_parser(self):
        run_parser = HypitRunParser(self.svrun_path).parse()
        self.assertIsNotNone(run_parser.author_svml)
        self.assertTrue(run_parser.author_svml.exists())
        self.assertIn("mrbeast_raw", run_parser.inputs)
        self.assertIn("narrator_intro", run_parser.inputs)
        self.assertIsNotNone(run_parser.target_output)

    def test_direct_svml_parsing_in_engine(self):
        """Validates that passing an .svml file directly to HypitEngine works without an .svrun manifest."""
        nile_svml = SOURCES_DIR / "nileblue_experiment.svml"
        engine = HypitEngine(nile_svml)
        self.assertEqual(engine.svml.timeline_duration, 60.0)
        self.assertEqual(engine.svrun.author_svml, nile_svml)
        self.assertIn("mrbeast_raw", engine.svrun.inputs)

    def test_subtitle_generation(self):
        engine = HypitEngine(self.svrun_path)
        test_ass = ROOT_DIR / "test_subtitles.ass"
        try:
            ass_path = engine.generate_subtitles(test_ass)
            self.assertTrue(ass_path.exists())
            content = ass_path.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1440", content)
            self.assertIn("CAN HE ESCAPE", content)
            self.assertIn("BEHIND ME ARE 100 COPS!", content)
            self.assertIn("AND WON THAT HALF A MILLION DOLLARS!", content)
            self.assertIn("Style: TitleYellow", content)
            self.assertIn("Style: SimpleWhite", content)
            self.assertIn("Style: Badge", content)
            self.assertIn("$500,000 ESCAPE CHALLENGE", content)
        finally:
            if test_ass.exists():
                test_ass.unlink()

    def test_sfx_bed_generation(self):
        engine = HypitEngine(self.svrun_path)
        test_wav = ROOT_DIR / "test_sfx_bed.wav"
        try:
            bed = engine.generate_sfx_bed(test_wav, duration=10.0)
            self.assertIsNotNone(bed)
            self.assertTrue(test_wav.exists())
            self.assertGreater(test_wav.stat().st_size, 1000)
        finally:
            if test_wav.exists():
                test_wav.unlink()

    def test_refuse_mismatched_footage_fallback(self):
        """Verifies engine rejects using MrBeast footage when rendering NileBlue without raw video."""
        nile_svml = SOURCES_DIR / "nileblue_experiment.svml"
        engine = HypitEngine(nile_svml, creator="nileblue")
        # Trying to render without raw video must raise FileNotFoundError, NOT use mrbeast_raw
        with self.assertRaises(FileNotFoundError) as ctx:
            engine.render(destination=ROOT_DIR / "output" / "test_fake.mp4")
        self.assertIn("Refusing to substitute mismatched MrBeast footage", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
