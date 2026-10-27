#!/usr/bin/env python3
"""
Unit tests for the Story-Driven Video Editorial Engine (movie_story_engine.py).
Tests:
- Story beat generation (entities, emotions, intensities, visual requirements)
- Scene relevance scoring & configurable weights
- Scene reuse penalty
- Visual continuity tracking
- Caption timing & keyword emphasis
- Duration constraints & micro-cuts
- Retention engine audit & automatic revision
- Final 100-point quality score
- Machine-readable editing manifest
- Fallback behavior & GDrive resolution compatibility
"""

import json
import tempfile
import unittest
from pathlib import Path

from movie_story_engine import (
    EditorialCut,
    EditorialTimelineBuilder,
    EditingManifest,
    QualityScoreReport,
    QualityScorer,
    RetentionEngine,
    RetentionWindowReport,
    SceneCandidate,
    SceneIndexManager,
    StoryBeat,
    StoryBeatAnalyzer,
    VisualRelevanceEngine,
    format_karaoke_subtitles_advanced,
)
from movie_video_engine import resolve_gdrive_source, get_last_editorial_cuts


class TestMovieStoryEngine(unittest.TestCase):

    def setUp(self):
        self.sample_script = (
            "Imagine waking up trapped inside an airtight vertical titanium vault with ten minutes of air left. "
            "Mark discovers the control panel is locked behind an electric keycode. "
            "He hears heavy footsteps approaching the outer reinforced steel door. "
            "With five seconds remaining, he enters the room and realizes someone has been watching him the entire time! "
            "Wait until you see the shocking secret in Part 2!"
        )

    def test_story_beat_generation(self):
        """StoryBeatAnalyzer must extract structured story beats with all required fields."""
        beats = StoryBeatAnalyzer.analyze_script(self.sample_script, total_duration=50.0)
        self.assertGreaterEqual(len(beats), 4)

        for b in beats:
            self.assertIsInstance(b.beat_id, int)
            self.assertTrue(len(b.text) > 0)
            self.assertGreaterEqual(b.end_estimate, b.start_estimate)
            self.assertGreaterEqual(b.importance, 0.5)
            self.assertIn(b.emotion, ["suspense", "shock", "tension", "curiosity", "revelation", "determination", "dread"])
            self.assertGreaterEqual(b.intensity, 1)
            self.assertLessEqual(b.intensity, 5)
            self.assertTrue(len(b.visual_requirement) > 0)
            self.assertIn(b.preferred_shot_type, ["close_up", "medium_shot", "wide_shot", "reaction", "detail"])
            self.assertIsInstance(b.needs_reaction, bool)
            self.assertIsInstance(b.needs_pattern_interrupt, bool)

        # Hook beat (first 3 seconds) must have highest importance & close-up preference
        hook_beat = beats[0]
        self.assertGreaterEqual(hook_beat.importance, 0.95)
        self.assertEqual(hook_beat.preferred_shot_type, "close_up")
        self.assertTrue(hook_beat.needs_reaction)

    def test_scene_relevance_scoring(self):
        """VisualRelevanceEngine must score matching candidates higher than unrelated shots."""
        engine = VisualRelevanceEngine()
        beat = StoryBeat(
            beat_id=1,
            text="He enters the dark room and spots the bloody weapon on the floor.",
            start_estimate=0.0,
            end_estimate=3.5,
            importance=0.95,
            emotion="suspense",
            intensity=4,
            characters=["protagonist_male"],
            locations=["room"],
            objects=["weapon", "blood"],
            actions=["enters", "spots"],
            visual_requirement="intense character discovery",
            preferred_shot_type="close_up",
            needs_reaction=True,
        )

        relevant_candidate = SceneCandidate(
            scene_id="cand_relevant",
            start_time=12.0,
            end_time=15.0,
            duration=3.0,
            shot_size="close_up",
            motion_level=0.65,
            brightness=0.45,
            scene_quality=0.92,
            dominant_emotion="suspense",
            detected_faces=1,
            objects=["weapon"],
            location="dark room",
            action="enters room",
            tags=["face", "reaction", "weapon", "action"],
        )

        unrelated_candidate = SceneCandidate(
            scene_id="cand_unrelated",
            start_time=120.0,
            end_time=123.0,
            duration=3.0,
            shot_size="wide",
            motion_level=0.10,
            brightness=0.85,
            scene_quality=0.75,
            dominant_emotion="calm",
            detected_faces=0,
            objects=[],
            location="open field",
            action="static landscape",
            tags=["nature", "landscape", "sun"],
        )

        rel_score, rel_breakdown = engine.calculate_score(relevant_candidate, beat)
        unrel_score, unrel_breakdown = engine.calculate_score(unrelated_candidate, beat)

        self.assertGreater(rel_score, unrel_score)
        self.assertGreaterEqual(rel_score, 0.70)
        self.assertLess(unrel_score, 0.55)
        self.assertIn("semantic_match", rel_breakdown)
        self.assertIn("character_match", rel_breakdown)
        self.assertIn("action_match", rel_breakdown)

    def test_scene_reuse_penalty(self):
        """Scenes used recently must incur heavy repetition penalty."""
        engine = VisualRelevanceEngine()
        beat = StoryBeat(
            beat_id=3,
            text="He stares in horror.",
            start_estimate=6.0,
            end_estimate=9.0,
            importance=0.90,
            emotion="shock",
            intensity=4,
            preferred_shot_type="close_up",
        )
        candidate = SceneCandidate(
            scene_id="scene_001",
            start_time=10.0,
            end_time=13.0,
            duration=3.0,
            shot_size="close_up",
            motion_level=0.5,
            brightness=0.5,
            scene_quality=0.9,
            dominant_emotion="shock",
            tags=["face", "reaction"],
        )

        # Fresh scene
        score_fresh, brk_fresh = engine.calculate_score(candidate, beat, recent_scene_ids=[])
        # Scene used in the immediately preceding shot
        score_recent, brk_recent = engine.calculate_score(candidate, beat, recent_scene_ids=["scene_001"])

        self.assertGreater(score_fresh, score_recent)
        self.assertEqual(brk_fresh["repetition_penalty"], 0.0)
        self.assertGreaterEqual(brk_recent["repetition_penalty"], 0.50)

    def test_visual_continuity(self):
        """Continuity score accounts for abrupt brightness jumps and shot variety."""
        engine = VisualRelevanceEngine()
        beat = StoryBeat(beat_id=2, text="Continuing the escape.", start_estimate=3.0, end_estimate=6.0)

        prev_shot = SceneCandidate(
            scene_id="prev_shot",
            start_time=0.0,
            end_time=3.0,
            duration=3.0,
            shot_size="wide",
            brightness=0.30,
        )

        matching_lighting = SceneCandidate(
            scene_id="shot_a",
            start_time=10.0,
            end_time=13.0,
            duration=3.0,
            shot_size="close_up",
            brightness=0.35,
        )

        jarring_lighting = SceneCandidate(
            scene_id="shot_b",
            start_time=20.0,
            end_time=23.0,
            duration=3.0,
            shot_size="wide",
            brightness=0.95,  # Blinding jump
        )

        _, brk_a = engine.calculate_score(matching_lighting, beat, previous_scene=prev_shot)
        _, brk_b = engine.calculate_score(jarring_lighting, beat, previous_scene=prev_shot)

        self.assertGreater(brk_a["continuity"], brk_b["continuity"])

    def test_caption_timing_and_emphasis(self):
        """format_karaoke_subtitles_advanced must group phrases and apply keyword emphasis."""
        words = [
            {"word": "IMAGINE", "start": 0.0, "end": 0.4},
            {"word": "WAKING", "start": 0.4, "end": 0.8},
            {"word": "UP", "start": 0.8, "end": 1.1},
            {"word": "TRAPPED", "start": 1.1, "end": 1.6},
            {"word": "INSIDE", "start": 1.6, "end": 2.0},
            {"word": "LEVEL", "start": 2.0, "end": 2.3},
            {"word": "333", "start": 2.3, "end": 2.8},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "test_subs.ass"
            events_count = format_karaoke_subtitles_advanced(
                words,
                ass_path,
                emphasis_keywords={"TRAPPED", "333"},
            )
            self.assertGreaterEqual(events_count, 1)
            content = ass_path.read_text(encoding="utf-8")
            self.assertIn("[Script Info]", content)
            self.assertIn("&H0000FFFF&", content)  # Neon yellow active word
            self.assertIn("&H00FFFF00&", content)  # Cyan-yellow emphasis keyword
            self.assertIn("TRAPPED", content)
            self.assertIn("480", content)          # UI-safe lower-third margin

    def test_duration_constraints_and_micro_cuts(self):
        """Timeline builder must split long intense beats into complementary micro-cuts."""
        long_beat = StoryBeat(
            beat_id=1,
            text="He enters the room, spots the blood, and realizes someone has been watching him.",
            start_estimate=0.0,
            end_estimate=5.0,  # 5.0 seconds -> warrants micro-cuts
            intensity=4,
            importance=0.95,
            needs_pattern_interrupt=True,
        )
        candidates = [
            SceneCandidate(f"s_{i}", start_time=i * 5.0, end_time=(i * 5.0) + 3.0, duration=3.0, shot_size="close_up")
            for i in range(10)
        ]

        cuts = EditorialTimelineBuilder.build_timeline([long_beat], candidates, total_duration=5.0)
        # 5.0s beat with intensity 4 must be split into multiple micro-cuts
        self.assertGreaterEqual(len(cuts), 2)
        total_dur = sum(c.duration for c in cuts)
        self.assertAlmostEqual(total_dur, 5.0, places=2)

        # First cut should have hook overlay
        self.assertIsNotNone(cuts[0].hook_overlay)

    def test_retention_engine_and_automatic_revision(self):
        """RetentionEngine must audit 3s rolling windows and revise stagnant sections."""
        beats = StoryBeatAnalyzer.analyze_script(self.sample_script, total_duration=45.0)
        candidates = [
            SceneCandidate(f"scene_{i:03d}", start_time=i * 4.0, end_time=(i * 4.0) + 3.0, duration=3.0)
            for i in range(15)
        ]
        cuts = EditorialTimelineBuilder.build_timeline(beats, candidates, total_duration=45.0)

        reports = RetentionEngine.audit_timeline(cuts, beats, total_duration=45.0)
        self.assertGreaterEqual(len(reports), 10)
        for r in reports:
            self.assertIsInstance(r, RetentionWindowReport)
            self.assertGreaterEqual(r.visual_change_score, 0.0)

        revised = RetentionEngine.automatically_revise_timeline(cuts, candidates, reports)
        self.assertEqual(len(revised), len(cuts))

    def test_final_quality_score(self):
        """QualityScorer evaluates 8 dimensions out of 100 with >= 85 pass threshold."""
        beats = StoryBeatAnalyzer.analyze_script(self.sample_script, total_duration=50.0)
        cuts = [
            EditorialCut(
                cut_id=i + 1,
                beat_id=i + 1,
                scene_id=f"scene_{i}",
                source_start=i * 3.0,
                duration=3.0,
                target_start=i * 3.0,
                target_end=(i + 1) * 3.0,
                relevance_score=0.88,
                shot_type="close_up" if i == 0 else "medium",
                hook_overlay="TRAPPED WITH TEN MINUTES" if i == 0 else None,
            )
            for i in range(16)
        ]

        report = QualityScorer.evaluate(cuts, beats, has_bgm=True, has_subtitles=True)
        self.assertIsInstance(report, QualityScoreReport)
        self.assertGreaterEqual(report.total_score, 85.0)
        self.assertTrue(report.passed)
        self.assertLessEqual(report.hook_score, 20.0)
        self.assertLessEqual(report.visual_relevance_score, 20.0)
        self.assertLessEqual(report.story_clarity_score, 15.0)
        self.assertLessEqual(report.pacing_score, 15.0)
        self.assertLessEqual(report.captions_score, 10.0)
        self.assertLessEqual(report.audio_score, 10.0)
        self.assertLessEqual(report.emotional_impact_score, 5.0)
        self.assertLessEqual(report.ending_score, 5.0)

    def test_editing_manifest_serialization(self):
        """EditingManifest must capture all editorial parameters in valid JSON."""
        manifest = EditingManifest(
            movie="Inception",
            part=1,
            narration_duration=48.5,
            total_video_duration=48.5,
            selected_scenes=[{"cut_id": 1, "scene_id": "scene_001", "relevance_score": 0.92}],
            quality_score={"total_score": 88.5, "passed": True},
            retention_report=[{"window_start": 0.0, "window_end": 3.0, "stagnation_flag": False}],
            bgm_track="aerohead_fragments_recap.mp3",
            warnings=[],
            fallback_events=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "manifest.json"
            manifest.write_to_file(p)
            self.assertTrue(p.exists())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["movie"], "Inception")
            self.assertEqual(data["quality_score"]["total_score"], 88.5)

    def test_gdrive_media_resolution_and_fallback_compatibility(self):
        """resolve_gdrive_source and last editorial cuts must maintain complete pipeline compatibility."""
        res = resolve_gdrive_source("non_existent_movie", {})
        self.assertIsNone(res)

        editorial_cuts = get_last_editorial_cuts()
        self.assertIsInstance(editorial_cuts, list)


if __name__ == "__main__":
    unittest.main()
