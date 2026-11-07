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
        self.svml_path = SOURCES_DIR / "mrbeast_yacht.svml"
        self.svrun_path = SOURCES_DIR / "production.svrun"
        self.look_path = SOURCES_DIR / "look.svs"

    def test_sources_exist(self):
        self.assertTrue(self.svml_path.exists(), "mrbeast_yacht.svml should exist")
        self.assertTrue(self.svrun_path.exists(), "production.svrun should exist")
        self.assertTrue(self.look_path.exists(), "look.svs should exist")

    def test_svml_parser(self):
        parser = HypitSVMLParser(self.svml_path).parse()
        self.assertEqual(parser.canvas_width, 1080)
        self.assertEqual(parser.canvas_height, 1920)
        self.assertEqual(parser.fps, 30)
        self.assertEqual(parser.timeline_duration, 20.0)
        self.assertIn("hook", parser.windows)
        self.assertIn("opening", parser.script_segments)

    def test_svrun_parser(self):
        run_parser = HypitRunParser(self.svrun_path).parse()
        self.assertIsNotNone(run_parser.author_svml)
        self.assertTrue(run_parser.author_svml.exists())
        self.assertIn("mrbeast_raw", run_parser.inputs)
        self.assertIsNotNone(run_parser.target_output)

    def test_subtitle_generation(self):
        engine = HypitEngine(self.svrun_path)
        test_ass = ROOT_DIR / "test_subtitles.ass"
        try:
            ass_path = engine.generate_subtitles(test_ass)
            self.assertTrue(ass_path.exists())
            content = ass_path.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn("WHAT IF", content)
            self.assertIn("Style: Highlight", content)
        finally:
            if test_ass.exists():
                test_ass.unlink()


if __name__ == "__main__":
    unittest.main()
