#!/usr/bin/env python3
"""
Unit tests for Hypit Video Engine.
Validates SVML/SVS/SVRun parsing, subtitle generation, and pipeline integrity.
"""

import unittest
from pathlib import Path
from hypit_engine import ARC_STAGES, HypitSVMLParser, HypitRunParser, HypitEngine, build_arc_filter_complex, plan_arc_cuts

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


class TestStoryArcCutting(unittest.TestCase):
    """Validates 6-stage story-arc cutting (HOOK -> RISING ACTION -> CONFLICT -> COMEBACK -> RISING ACTION -> PAYOFF)."""

    def test_arc_stage_order(self):
        self.assertEqual(
            [stage for stage, _ in ARC_STAGES],
            ["HOOK", "RISING ACTION", "CONFLICT", "COMEBACK", "RISING ACTION", "PAYOFF"],
        )

    def test_arc_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(w for _, w in ARC_STAGES), 1.0, places=6)

    def test_plan_without_narration_sums_to_duration(self):
        cuts = plan_arc_cuts(900.0, 60.0, has_narration=False)
        self.assertIsNotNone(cuts)
        self.assertEqual(len(cuts), 6)
        self.assertAlmostEqual(sum(dur for _, _, dur in cuts), 60.0, places=6)

    def test_plan_windows_monotonic_nonoverlapping_and_bounded(self):
        raw = 900.0
        cuts = plan_arc_cuts(raw, 60.0, has_narration=False)
        cursor = 0.0
        for _, start, dur in cuts:
            self.assertGreaterEqual(start, cursor)  # spread across raw, never overlaps
            cursor = start + dur
        self.assertAlmostEqual(cursor, raw, places=6)  # PAYOFF lands at the raw end

    def test_plan_with_narration_pins_hook_to_intro(self):
        cuts = plan_arc_cuts(900.0, 60.0, has_narration=True)
        self.assertIsNotNone(cuts)
        self.assertAlmostEqual(cuts[0][2], 3.75, places=6)
        self.assertAlmostEqual(sum(dur for _, _, dur in cuts), 60.0, places=6)

    def test_plan_without_narration_hook_is_proportional(self):
        cuts = plan_arc_cuts(900.0, 60.0, has_narration=False)
        self.assertAlmostEqual(cuts[0][2], 0.14 * 60.0, places=6)

    def test_plan_returns_none_when_raw_unknown_or_too_short(self):
        self.assertIsNone(plan_arc_cuts(None, 60.0, has_narration=False))
        self.assertIsNone(plan_arc_cuts(0.0, 60.0, has_narration=False))
        self.assertIsNone(plan_arc_cuts(30.0, 60.0, has_narration=False))

    def test_filter_builder_concatenates_six_segments(self):
        cuts = plan_arc_cuts(900.0, 60.0, has_narration=False)
        fc = build_arc_filter_complex(cuts, 1080, 1920, "sub.ass", ":fontsdir=/x", has_narration=False, has_sfx=False)
        self.assertIn("concat=n=6:v=1:a=0", fc)
        self.assertIn("concat=n=6:v=0:a=1", fc)
        self.assertIn("subtitles='sub.ass'", fc)
        self.assertEqual(fc.count(":v]trim=start="), 6)
        self.assertEqual(fc.count(":a]atrim=start="), 6)
        self.assertNotIn("[2:a]", fc)

    def test_filter_builder_narration_mixes_intro_vox(self):
        cuts = plan_arc_cuts(900.0, 60.0, has_narration=True)
        fc = build_arc_filter_complex(cuts, 1080, 1920, "sub.ass", "", has_narration=True, has_sfx=True)
        self.assertIn("[1:a]volume=1.40[a_vox];", fc)
        self.assertIn("[2:a]volume=0.90[a_sfx];", fc)
        self.assertIn("alimiter=limit=0.95[a]", fc)


if __name__ == "__main__":
    unittest.main()
