#!/usr/bin/env python3
"""
Unit tests for the Cinematic Story-Driven Editor.
Validates:
- Profile resolution, dimensions, aspect ratios, and safe margins
- Hook score quantitative evaluation (curiosity, stakes, brevity)
- StoryRole inference and EffectPlan parameters (restrained zoom <= 1.08)
- Visual relevance scoring, continuity bonus, and repetition penalty
- Dynamic shot durations and multi-stage fallback ladder (FALLBACK_USED)
- Cinematic ASS subtitle formatting with selective keyword highlights
- Narrative title generation
- Editorial manifest serialization
"""

import json
import tempfile
import unittest
from pathlib import Path

from movie_profiles import (
    CaptionStyle,
    EffectIntensity,
    ProfileConfig,
    ProfileType,
    StoryRole,
    VisualPriority,
    resolve_profile,
)
from movie_story_engine import (
    CaptionPlan,
    EditingManifest,
    EditorialCut,
    EditorialTimelineBuilder,
    EffectPlan,
    HookScoreEvaluator,
    QualityScorer,
    SceneCandidate,
    SceneIndexManager,
    StoryBeat,
    StoryBeatAnalyzer,
    VisualRelevanceEngine,
    format_cinematic_subtitles,
    generate_story_title,
)


class TestCinematicEditorProfiles(unittest.TestCase):
    """Test the profile registry, dimensions, aspect ratios, and defaults."""

    def test_resolve_profile_aliases(self):
        # Default fallback on None or empty string (CINERUSH_STYLE 1:1 square)
        p_default = resolve_profile(None)
        self.assertEqual(p_default.profile_type, ProfileType.CINERUSH_STYLE)
        self.assertEqual(p_default.width, 1920)
        self.assertEqual(p_default.height, 1920)
        self.assertTrue(p_default.is_square)
        self.assertFalse(p_default.is_vertical)

        # CineRush 1:1 square
        for alias in ["cinerush", "cinerush_style", "square", "1:1"]:
            p = resolve_profile(alias)
            self.assertEqual(p.profile_type, ProfileType.CINERUSH_STYLE)
            self.assertEqual(p.width, 1920)
            self.assertEqual(p.height, 1920)
            self.assertEqual(p.aspect_ratio, "1:1")
            self.assertTrue(p.is_square)
            self.assertFalse(p.is_vertical)
            self.assertLessEqual(p.max_zoom_factor, 1.08)

        # Landscape 16:9 widescreen
        for alias in ["landscape", "16:9", "long_video"]:
            p = resolve_profile(alias)
            self.assertEqual(p.profile_type, ProfileType.LANDSCAPE)
            self.assertEqual(p.width, 1920)
            self.assertEqual(p.height, 1080)
            self.assertEqual(p.aspect_ratio, "16:9")
            self.assertTrue(p.is_landscape)

    def test_caption_style_and_safe_margins(self):
        shorts = resolve_profile("shorts_9x16")
        cinerush = resolve_profile("cinerush_style")
        landscape = resolve_profile("landscape")

        # Vertical shorts must have higher bottom margin to avoid UI overlay
        self.assertGreater(shorts.caption_style.margin_v, cinerush.caption_style.margin_v)
        self.assertGreater(cinerush.caption_style.margin_v, landscape.caption_style.margin_v)

        data = shorts.to_dict()
        self.assertIn("profile_type", data)
        self.assertIn("caption_style", data)
        self.assertEqual(data["width"], 1080)
        self.assertEqual(data["height"], 1920)


class TestHookScoreEvaluator(unittest.TestCase):
    """Test quantitative hook scoring based on curiosity, stakes, and brevity."""

    def test_high_retention_hook(self):
        text = "What would you do if you woke up trapped inside a locked titanium vault with 60 seconds to live?"
        score = HookScoreEvaluator.calculate_hook_score(text)
        # Should score very high due to question, trap/death stakes, urgency, and concise length
        self.assertGreaterEqual(score, 0.75)

    def test_low_retention_hook(self):
        text = "A man is walking in the forest and looks at the trees on a sunny Tuesday afternoon."
        score = HookScoreEvaluator.calculate_hook_score(text, shot_type="wide", detected_faces=0, intensity=1)
        # Low score: no stakes, no mystery, no urgency, wide shot
        self.assertLess(score, 0.55)

    def test_brevity_penalty_on_rambling_hook(self):
        concise = "He opened the door and found his dead body."
        long_run = "He opened the door and slowly looked around the very large dusty room and then after thinking for a while noticed something weird on the floor that turned out to be another version of himself."
        score_concise = HookScoreEvaluator.calculate_hook_score(concise)
        score_long = HookScoreEvaluator.calculate_hook_score(long_run)
        self.assertGreater(score_concise, score_long)


class TestStoryRoleAndEffectPlans(unittest.TestCase):
    """Test narrative role classification and effect plan generation."""

    def test_story_role_inference(self):
        script = (
            "What if your reflection could kill you? "
            "Mark is a young detective investigating unexplained mirror deaths. "
            "Suddenly the mirror shatters and a shadow grabs his arm! "
            "He discovers the horrifying truth about the house. "
            "In the shocking climax he destroys the cursed mirror forever."
        )
        beats = StoryBeatAnalyzer.analyze_script(script, total_duration=45.0)
        self.assertGreaterEqual(len(beats), 4)

        # First beat must be HOOK with high hook score
        self.assertEqual(beats[0].story_role, StoryRole.HOOK.value)
        self.assertGreaterEqual(beats[0].hook_score, 0.70)

        # Later beats should reflect conflict, escalation, discovery, or payoff/ending
        roles = [b.story_role for b in beats]
        self.assertIn(StoryRole.HOOK.value, roles)
        self.assertTrue(any(r in [StoryRole.CONFLICT.value, StoryRole.ESCALATION.value, StoryRole.DISCOVERY.value, StoryRole.PAYOFF.value, StoryRole.ENDING.value] for r in roles))

        # Check effect plan properties
        for b in beats:
            plan = b.effect_plan
            self.assertIsNotNone(plan)
            self.assertIsInstance(plan, EffectPlan)
            self.assertLessEqual(plan.scale_end, 1.08)
            self.assertGreaterEqual(plan.scale_end, 1.0)
            self.assertIn(plan.intensity, [EffectIntensity.SUBTLE.value, EffectIntensity.NORMAL.value, EffectIntensity.DRAMATIC.value])
            self.assertIn(plan.visual_priority, [VisualPriority.CRITICAL.value, VisualPriority.HIGH.value, VisualPriority.MEDIUM.value, VisualPriority.LOW.value])


class TestVisualRelevanceAndContinuity(unittest.TestCase):
    """Test candidate relevance scoring, character continuity bonus, and repetition penalty."""

    def setUp(self):
        self.engine = VisualRelevanceEngine()
        self.beat = StoryBeat(
            beat_id=2,
            text="Mark reaches into his trenchcoat to draw his weapon.",
            start_estimate=3.0,
            end_estimate=6.0,
            importance=0.85,
            emotion="tension",
            intensity=3,
            characters=["Mark"],
            locations=["alley"],
            objects=["weapon", "gun"],
            actions=["draw"],
            visual_requirement="weapon draw action",
            preferred_shot_type="medium_shot",
            story_role=StoryRole.CONFLICT.value,
        )

    def test_character_and_action_match_scores_higher(self):
        good_candidate = SceneCandidate(
            scene_id="cand_good",
            start_time=10.0,
            end_time=13.0,
            duration=3.0,
            shot_size="medium",
            detected_characters=["Mark"],
            action="draw",
            tags=["weapon", "alley", "draw"],
            dominant_emotion="tension",
            scene_quality=0.9,
        )
        unrelated_candidate = SceneCandidate(
            scene_id="cand_unrelated",
            start_time=50.0,
            end_time=53.0,
            duration=3.0,
            shot_size="wide",
            detected_characters=["Sarah"],
            action="eating",
            tags=["restaurant", "food"],
            dominant_emotion="calm",
            scene_quality=0.7,
        )

        score_good, _ = self.engine.calculate_score(good_candidate, self.beat, active_character="Mark")
        score_unrelated, _ = self.engine.calculate_score(unrelated_candidate, self.beat, active_character="Mark")
        self.assertGreater(score_good, score_unrelated)

    def test_repetition_penalty(self):
        cand = SceneCandidate(
            scene_id="cand_repeat",
            start_time=10.0,
            end_time=13.0,
            duration=3.0,
            shot_size="medium",
            detected_characters=["Mark"],
        )

        # Fresh score
        score_fresh, _ = self.engine.calculate_score(cand, self.beat)

        # Score when used multiple times
        score_repeated, _ = self.engine.calculate_score(
            cand,
            self.beat,
            used_scene_ids={"cand_repeat"},
            recent_scene_ids=["cand_repeat", "cand_repeat"],
        )

        self.assertLess(score_repeated, score_fresh)

    def test_continuity_bonus(self):
        cand = SceneCandidate(
            scene_id="cand_cont",
            start_time=14.0,
            end_time=17.0,
            duration=3.0,
            shot_size="medium",
            detected_characters=["Mark"],
            location="alley",
        )
        prev_scene_matching = SceneCandidate(
            scene_id="cand_prev",
            start_time=10.0,
            end_time=14.0,
            duration=4.0,
            shot_size="close_up",
            detected_characters=["Mark"],
            location="alley",
        )
        prev_scene_different = SceneCandidate(
            scene_id="cand_other",
            start_time=80.0,
            end_time=84.0,
            duration=4.0,
            shot_size="wide",
            detected_characters=["Unknown"],
            location="desert",
        )

        score_with_continuity, _ = self.engine.calculate_score(
            cand, self.beat, previous_scene=prev_scene_matching, active_character="Mark"
        )
        score_without_continuity, _ = self.engine.calculate_score(
            cand, self.beat, previous_scene=prev_scene_different, active_character="Mark"
        )
        self.assertGreater(score_with_continuity, score_without_continuity)


class TestEditorialTimelineAndFallbackLadder(unittest.TestCase):
    """Test dynamic shot durations and fallback ladder logging."""

    def test_dynamic_durations_by_story_role(self):
        builder = EditorialTimelineBuilder()

        beat_hook = StoryBeat(
            beat_id=1,
            text="Imagine waking up inside a titanium box with 60 seconds left.",
            start_estimate=0.0,
            end_estimate=5.0,
            story_role=StoryRole.HOOK.value,
        )
        hook_dur = builder.determine_dynamic_shot_durations(beat_hook)
        # Hook with 5s duration produces 3 dynamic micro-cuts
        self.assertGreaterEqual(len(hook_dur), 2)
        self.assertTrue(all(d <= 2.5 for d in hook_dur))

        beat_reveal = StoryBeat(
            beat_id=4,
            text="He opens the chamber to discover the shocking truth.",
            start_estimate=15.0,
            end_estimate=19.0,
            story_role=StoryRole.REVEAL.value,
        )
        reveal_dur = builder.determine_dynamic_shot_durations(beat_reveal)
        self.assertTrue(all(d >= 1.0 for d in reveal_dur))

    def test_fallback_ladder_logs_reason(self):
        # Scene candidate far outside beat requirements
        candidates = [
            SceneCandidate(
                scene_id="far_scene",
                start_time=120.0,
                end_time=125.0,
                duration=5.0,
                shot_size="wide",
            )
        ]
        beats = [
            StoryBeat(
                beat_id=1,
                text="A close-up discovery inside a tiny box.",
                start_estimate=0.0,
                end_estimate=3.0,
                importance=0.9,
                preferred_shot_type="close_up",
                story_role=StoryRole.HOOK.value,
            )
        ]

        builder = EditorialTimelineBuilder()
        cuts = builder.build_timeline(beats, candidates, total_duration=3.0)
        self.assertGreaterEqual(len(cuts), 1)
        cut = cuts[0]
        # Should record fallback
        self.assertTrue(cut.fallback_used)
        self.assertIn("fallback", cut.fallback_reason.lower())


class TestCinematicSubtitlesFormatting(unittest.TestCase):
    """Test ASS subtitle generation, profile styling, and selective word highlights."""

    def test_cinematic_subtitles_generation(self):
        words = [
            {"word": "Imagine", "start": 0.1, "end": 0.5},
            {"word": "waking", "start": 0.5, "end": 0.9},
            {"word": "up", "start": 0.9, "end": 1.1},
            {"word": "trapped", "start": 1.1, "end": 1.6},
            {"word": "inside", "start": 1.6, "end": 2.0},
            {"word": "a", "start": 2.0, "end": 2.1},
            {"word": "vault", "start": 2.1, "end": 2.6},
        ]
        profile = resolve_profile("cinerush_style")

        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as f:
            out_path = Path(f.name)

        try:
            format_cinematic_subtitles(
                words=words,
                output_ass_path=out_path,
                profile=profile,
                emphasis_keywords={"TRAPPED", "VAULT"},
                max_duration=3.0,
            )
            content = out_path.read_text(encoding="utf-8")

            # Check ASS sections
            self.assertIn("[Script Info]", content)
            self.assertIn("[V4+ Styles]", content)
            self.assertIn("[Events]", content)

            # Check profile font size and safe margin in style definition
            self.assertIn(f",DejaVu Sans,{profile.caption_style.font_size},", content)
            self.assertIn(f",40,40,{profile.caption_style.margin_v},1", content)

            # Check fade effect and highlight tags
            self.assertIn("\\fad(140,90)", content)
            self.assertTrue("\\c&H0000D4FF&" in content or "\\c&H0038B6FF&" in content)
        finally:
            if out_path.exists():
                out_path.unlink()


class TestStoryTitleGeneration(unittest.TestCase):
    """Test high-retention cinematic narrative title generation."""

    def test_narrative_title_format(self):
        title = generate_story_title("The Matrix", script_text="He wakes up to discover the truth...", part_num=1)
        self.assertIn("The Matrix", title)
        self.assertIn("#Shorts", title)
        self.assertIn("Part 1", title)
        self.assertTrue(any(word in title for word in ["Secret", "Truth", "Idea", "Choice"]))


class TestEditingManifest(unittest.TestCase):
    """Test machine-readable editing manifest creation and serialization."""

    def test_manifest_serialization(self):
        manifest = EditingManifest(
            movie="Inception",
            part=1,
            narration_duration=50.0,
            total_video_duration=54.5,
            selected_scenes=[
                {
                    "cut_id": 1,
                    "scene_id": "sc_1",
                    "duration": 2.5,
                    "target_role": "HOOK",
                    "fallback_used": False,
                }
            ],
            quality_score={"final_editorial_score": 88.5},
            retention_report=[],
            bgm_track="aerohead_fragments.mp3",
            profile="cinerush_style",
            resolution="1920x1920",
            aspect_ratio="1:1",
            story_beats=[{"beat_id": 1, "story_role": "HOOK"}],
            effect_plans=[{"intensity": "NORMAL", "scale_end": 1.05}],
            warnings=[],
            fallback_events=[],
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = Path(f.name)

        try:
            manifest.write_to_file(out_path)
            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["movie"], "Inception")
            self.assertEqual(data["profile"], "cinerush_style")
            self.assertEqual(data["resolution"], "1920x1920")
            self.assertEqual(data["aspect_ratio"], "1:1")
            self.assertEqual(data["quality_score"]["final_editorial_score"], 88.5)
            self.assertEqual(len(data["story_beats"]), 1)
            self.assertEqual(len(data["effect_plans"]), 1)
        finally:
            if out_path.exists():
                out_path.unlink()


class TestCriticalVisualUpgrades(unittest.TestCase):
    """Test critical visual upgrades: credit detection, visual clarity, highlight restraint, and manifest schema."""

    def test_end_credit_detection_and_rejection(self):
        """End credits must receive high credit score and be penalized/rejected from timeline."""
        # Rolling credit scene at end of movie with low motion
        credit_score, is_cred = SceneIndexManager.evaluate_credit_score(
            c_start=6800.0, end_sec=7000.0, brightness=0.18, motion=0.10, idx=34, total_scenes=36
        )
        self.assertGreaterEqual(credit_score, 0.70)
        self.assertTrue(is_cred)

        # Non-credit climactic scene
        action_score, is_act_cred = SceneIndexManager.evaluate_credit_score(
            c_start=5500.0, end_sec=7000.0, brightness=0.55, motion=0.75, idx=25, total_scenes=36
        )
        self.assertLess(action_score, 0.20)
        self.assertFalse(is_act_cred)

    def test_visual_clarity_scoring(self):
        """Dark, muddy frames must receive low visual clarity score."""
        dark_clarity = SceneIndexManager.evaluate_visual_clarity(brightness=0.10, motion=0.20)
        self.assertLessEqual(dark_clarity, 0.30)

        bright_clarity = SceneIndexManager.evaluate_visual_clarity(brightness=0.55, motion=0.60)
        self.assertGreaterEqual(bright_clarity, 0.80)

    def test_caption_highlight_restraint(self):
        """Captions must limit yellow/accent highlighting to maximum 2 words per card."""
        words = [
            {"word": "EDDIE", "raw_word": "Eddie", "start": 0.0, "end": 0.4},
            {"word": "DISCOVERS", "raw_word": "discovers", "start": 0.4, "end": 0.8},
            {"word": "THE", "raw_word": "the", "start": 0.8, "end": 1.1},
            {"word": "DEADLY", "raw_word": "deadly", "start": 1.1, "end": 1.5},
            {"word": "SECRET", "raw_word": "secret", "start": 1.5, "end": 1.9},
            {"word": "INSIDE", "raw_word": "inside", "start": 1.9, "end": 2.3},
        ]
        emphasis_all = {"EDDIE", "DISCOVERS", "DEADLY", "SECRET", "INSIDE"}
        profile = resolve_profile("cinerush_style")

        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as f:
            out_path = Path(f.name)

        try:
            format_cinematic_subtitles(
                words=words,
                output_ass_path=out_path,
                profile=profile,
                emphasis_keywords=emphasis_all,
            )
            content = out_path.read_text(encoding="utf-8")
            dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
            self.assertTrue(len(dialogue_lines) >= 1)

            for line in dialogue_lines:
                # Count occurrences of gold highlight tag \c&H0000D4FF& in line
                gold_count = line.count(profile.caption_style.highlight_color_primary)
                self.assertLessEqual(gold_count, 2, "A single caption card must not have more than 2 highlighted words")
        finally:
            if out_path.exists():
                out_path.unlink()

    def test_editorial_cut_manifest_schema(self):
        """EditorialCut to_dict must output all Section 30 manifest fields."""
        cut = EditorialCut(
            cut_id=1,
            beat_id=1,
            scene_id="scene_001",
            source_start=12.5,
            duration=2.4,
            target_start=0.0,
            target_end=2.4,
            relevance_score=0.91,
            shot_type="close_up",
            semantic_score=0.92,
            character_score=0.88,
            action_score=0.75,
            emotion_score=0.80,
            visual_clarity_score=0.89,
            credit_score=0.0,
            crop={"mode": "cinematic_fill", "bias": "center", "zoom": 1.05},
            caption_position="LOWER_CENTER",
            caption_style="cinematic_subtitles",
            effect="cut",
            selection_reason="Opening hook with high visual clarity and protagonist focus.",
        )
        data = cut.to_dict()
        self.assertEqual(data["shot_id"], "shot_001")
        self.assertEqual(data["start"], 0.0)
        self.assertEqual(data["end"], 2.4)
        self.assertEqual(data["source_timestamp"], "12.50s")
        self.assertIn("Opening hook", data["selection_reason"])
        self.assertEqual(data["visual_clarity_score"], 0.89)
        self.assertEqual(data["credit_score"], 0.0)
        self.assertEqual(data["caption_position"], "LOWER_CENTER")
        self.assertEqual(data["effect"], "cut")

    def test_qc_specific_metrics(self):
        """QualityScoreReport must include all 8 Section 32 QC metrics."""
        beats = StoryBeatAnalyzer.analyze_script("A scientist discovers a breach.", total_duration=45.0)
        cuts = [
            EditorialCut(
                cut_id=1,
                beat_id=1,
                scene_id="scene_01",
                source_start=10.0,
                duration=3.0,
                target_start=0.0,
                target_end=3.0,
                relevance_score=0.90,
                shot_type="close_up",
                visual_clarity_score=0.88,
                credit_score=0.0,
            )
        ]
        report = QualityScorer.evaluate(cuts, beats, has_bgm=True, has_subtitles=True)
        self.assertGreaterEqual(report.frame_occupancy_score, 0.90)
        self.assertGreaterEqual(report.visual_clarity_score, 0.70)
        self.assertLessEqual(report.credit_score, 0.10)
        self.assertGreaterEqual(report.caption_safe_zone_score, 0.80)
        self.assertGreaterEqual(report.hook_score, 14.0)
        self.assertGreaterEqual(report.visual_narration_alignment_score, 15.0)
        self.assertLessEqual(report.effect_density_score, 0.50)
        self.assertGreaterEqual(report.ending_score, 3.0)


if __name__ == "__main__":
    unittest.main()
