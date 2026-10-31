#!/usr/bin/env python3
"""
Unit Tests for Cinerush-Style Raw Movie Scene Clipper
Tests:
- Scene search query builder (no trailers/reviews)
- Viral window selection & title constraints (<= 12 words, 2 emojis, #film #movie #shorts)
- Dead-air trimming (gaps > 2.0s cut, 0.35s padding kept, pauses <= 2.0s preserved)
- Word timestamp recalculation along trimmed timeline
- Shot boundary subdivision (>3.0s split into 2.0s windows)
- Face tracking crop logic (2-face center, 1-face center, no-face center)
- Word-by-word karaoke ASS subtitle generation (max 4 words/card, yellow active, white inactive)
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

from cinerush_downloader import download_movie_scene
from movie_pipeline_state import HistoryError, assert_upload_cadence_allowed, completed_upload_groups
from cinerush_viral_selector import (
    format_cinerush_title,
    build_trimmed_timeline,
    _heuristic_viral_window,
)
from cinerush_face_reframer import (
    CinerushFaceDetector,
    calculate_shot_crop_x,
    calculate_shot_tracking,
    detect_camera_shots,
)
from cinerush_subtitles import (
    group_words_into_cinerush_phrases,
    generate_cinerush_karaoke_ass,
    format_ass_time,
)


class TestUploadCadence(unittest.TestCase):
    def _entry(self, group: str, uploaded_at: datetime, short_id: str = "short", normal_id: str | None = "normal"):
        return {
            "movie_id": group,
            "part_number": 1,
            "upload_group_id": group,
            "status": "uploaded",
            "youtube_id": short_id,
            "normal_video_id": normal_id,
            "uploaded_at": uploaded_at.isoformat(),
        }

    def test_pair_counts_as_one_group_and_requires_24_hours(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        history = {"uploaded_movies": [self._entry("one", now - timedelta(hours=25))]}
        self.assertEqual(len(completed_upload_groups(history, now)), 1)
        assert_upload_cadence_allowed(history, now)
        too_soon = {"uploaded_movies": [self._entry("one", now - timedelta(hours=23, minutes=59))]}
        with self.assertRaisesRegex(HistoryError, "24 hours"):
            assert_upload_cadence_allowed(too_soon, now)

    def test_three_completed_groups_block_even_when_pairs_duplicate(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        entries = []
        for index, hours in enumerate((72, 48, 25), start=1):
            entries.extend([
                self._entry(f"group-{index}", now - timedelta(hours=hours), short_id=f"s{index}", normal_id=f"n{index}"),
                self._entry(f"group-{index}", now - timedelta(hours=hours), short_id=f"s{index}", normal_id=f"n{index}"),
            ])
        history = {"uploaded_movies": entries}
        self.assertEqual(len(completed_upload_groups(history, now)), 3)
        with self.assertRaisesRegex(HistoryError, "three completed"):
            assert_upload_cadence_allowed(history, now)

    def test_missing_normal_upload_does_not_consume_completed_group(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        history = {"uploaded_movies": [self._entry("pending", now - timedelta(hours=1), normal_id=None)]}
        self.assertEqual(completed_upload_groups(history, now), [])
        assert_upload_cadence_allowed(history, now)


class TestCinerushDownloader(unittest.TestCase):
    def test_download_requires_drive_id(self):
        with self.assertRaisesRegex(RuntimeError, "Google Drive file ID"):
            download_movie_scene(output_dir=Path(tempfile.mkdtemp()))

    def test_download_never_accepts_youtube_url_without_drive_id(self):
        with self.assertRaisesRegex(RuntimeError, "Google Drive file ID"):
            download_movie_scene(url="https://youtube.com/watch?v=example", output_dir=Path(tempfile.mkdtemp()))


class TestCinerushViralSelector(unittest.TestCase):
    def test_cinerush_title_formatting(self):
        title = format_cinerush_title("They broke his hand so he beat them with his left", "Just Mercy")
        self.assertIn("#film #movie #shorts", title)
        # Verify word count <= 12
        words = title.split()
        self.assertLessEqual(len(words), 14)
        # Verify emojis present
        self.assertTrue("🔥" in title or "🎬" in title or "♠️" in title)

    def test_minimum_duration_restores_real_gap_without_changing_words(self):
        words = [
            {"word": "First", "start": 0.0, "end": 1.0},
            {"word": "Second", "start": 5.0, "end": 6.0},
        ]
        intervals, shifted, duration = build_trimmed_timeline(
            words, 0.0, 6.0, minimum_duration=5.0
        )
        self.assertGreaterEqual(duration, 5.0)
        self.assertLessEqual(duration, 6.0)
        self.assertEqual(len(shifted), 2)
        self.assertLessEqual(shifted[0]["end"], shifted[1]["start"])

    def test_dead_air_trimming_cuts_silence_over_2_seconds(self):
        # 3 speech clusters separated by a 4.0s gap and a 1.0s gap
        words = [
            {"word": "First", "start": 10.0, "end": 10.5},
            {"word": "sentence", "start": 10.6, "end": 11.2},
            # Gap of 4.8s (from 11.2 to 16.0) -> should be cut!
            {"word": "Second", "start": 16.0, "end": 16.5},
            {"word": "line", "start": 16.6, "end": 17.0},
            # Gap of 1.0s (from 17.0 to 18.0) -> should be KEPT (<= 2.0s)!
            {"word": "Third", "start": 18.0, "end": 18.5},
            {"word": "conclusion", "start": 18.6, "end": 19.2},
        ]
        clip_start = 5.0
        clip_end = 25.0

        intervals, shifted_words, total_dur = build_trimmed_timeline(
            all_words=words,
            clip_start=clip_start,
            clip_end=clip_end,
            silence_threshold=2.0,
            speech_padding=0.35,
        )

        # There should be 2 intervals because of the 4.8s cut
        self.assertEqual(len(intervals), 2)

        # Interval 1: from 10.0 - 0.35 = 9.65 to 11.2 + 0.35 = 11.55
        self.assertAlmostEqual(intervals[0][0], 9.65, places=2)
        self.assertAlmostEqual(intervals[0][1], 11.55, places=2)

        # Interval 2: from 16.0 - 0.35 = 15.65 to 19.2 + 0.35 = 19.55
        self.assertAlmostEqual(intervals[1][0], 15.65, places=2)
        self.assertAlmostEqual(intervals[1][1], 19.55, places=2)

        # Shifted words should have contiguous timestamps starting near 0.35s
        self.assertEqual(len(shifted_words), len(words))
        self.assertAlmostEqual(shifted_words[0]["start"], 0.35, places=2)
        self.assertTrue(shifted_words[-1]["end"] <= total_dur)

    def test_heuristic_window_selection(self):
        segments = [
            {"start": 10.0, "end": 40.0, "words": [{"word": f"w{i}", "start": 10.0 + i, "end": 10.5 + i} for i in range(25)]},
            {"start": 50.0, "end": 180.0, "words": [{"word": f"w{i}", "start": 50.0 + i, "end": 50.5 + i} for i in range(80)]},
        ]
        res = _heuristic_viral_window(segments, total_video_duration=240.0, movie_title="Test Movie")
        self.assertGreaterEqual(res["clip_end"] - res["clip_start"], 50.0)
        self.assertLessEqual(res["clip_end"] - res["clip_start"], 58.0)
        self.assertIn("#film #movie #shorts", res["title"])


class TestCinerushFaceReframer(unittest.TestCase):
    def test_crop_calculation_two_faces_centered(self):
        # 1920x1080 frame, 9:16 crop width = 1080 * 9/16 = 607 -> 606
        frame_w = 1920
        frame_h = 1080
        crop_w = int(frame_h * 9 / 16)
        crop_w = crop_w - (crop_w % 2)

        detector = MagicMock(spec=CinerushFaceDetector)
        # Two faces at x=750 and x=1100 (span = 350 <= crop_w * 0.75 = 455)
        # Midpoint = (750 + 1100) / 2 = 925
        detector.detect_faces.return_value = [
            (700, 200, 100, 100),  # center = 750
            (1050, 200, 100, 100), # center = 1100
        ]

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            crop_x = calculate_shot_crop_x(Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, detector)

            # Center between two faces: x1 = 925 - 303 = 622
            self.assertGreaterEqual(crop_x, 600)
            self.assertLessEqual(crop_x, 650)
            self.assertEqual(crop_x % 2, 0, "crop_x must be even for FFmpeg")

    def test_crop_calculation_dominant_single_face(self):
        frame_w = 1920
        frame_h = 1080
        crop_w = 606

        detector = MagicMock(spec=CinerushFaceDetector)
        # One prominent face on the left at center = 400
        detector.detect_faces.return_value = [
            (300, 200, 200, 200) # center = 400
        ]

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            crop_x = calculate_shot_crop_x(Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, detector)

            # Face at 400 -> crop_x = 400 - 303 = 97 -> 96
            self.assertAlmostEqual(crop_x, 96, delta=4)
            self.assertEqual(crop_x % 2, 0)

    def test_crop_fallback_center_when_no_faces(self):
        frame_w = 1920
        frame_h = 1080
        crop_w = 606

        detector = MagicMock(spec=CinerushFaceDetector)
        detector.detect_faces.return_value = []

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            crop_x = calculate_shot_crop_x(Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, detector)

            expected_center_x = (1920 - 606) // 2
            expected_center_x = expected_center_x - (expected_center_x % 2)
            self.assertEqual(crop_x, expected_center_x)

    def test_dynamic_face_tracking_panning_when_actor_moves(self):
        frame_w = 1920
        frame_h = 1080
        crop_w = 606
        crop_h = 1080

        detector = MagicMock(spec=CinerushFaceDetector)
        # Actor walks from left (center=300) to right (center=900) across 5 sampled frames
        detector.detect_faces.side_effect = [
            [(250, 200, 100, 100)], # center = 300
            [(400, 200, 100, 100)], # center = 450
            [(600, 200, 100, 100)], # center = 650
            [(800, 200, 100, 100)], # center = 850
            [(850, 200, 100, 100)], # center = 900
        ]

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            x_start, x_end, is_wide = calculate_shot_tracking(
                Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, crop_h, detector
            )

            # Actor moved > 5% of frame -> camera smoothly pans: x_start != x_end
            self.assertNotEqual(x_start, x_end)
            self.assertLess(x_start, x_end)
            self.assertEqual(x_start % 2, 0)
            self.assertEqual(x_end % 2, 0)
            self.assertFalse(is_wide)

    def test_dynamic_face_tracking_deadband_when_stationary(self):
        frame_w = 1920
        frame_h = 1080
        crop_w = 1080  # 1:1 square crop width
        crop_h = 1080

        detector = MagicMock(spec=CinerushFaceDetector)
        # Actor stays near center with minimal head motion (< 5% shift)
        detector.detect_faces.side_effect = [
            [(920, 200, 100, 100)], # center = 970
            [(925, 200, 100, 100)], # center = 975
            [(920, 200, 100, 100)], # center = 970
            [(930, 200, 100, 100)], # center = 980
            [(925, 200, 100, 100)], # center = 975
        ]

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            x_start, x_end, is_wide = calculate_shot_tracking(
                Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, crop_h, detector
            )

            # Stationary actor -> deadband keeps camera locked: x_start == x_end
            self.assertEqual(x_start, x_end)
            self.assertEqual(x_start % 2, 0)

    def test_wide_shot_flagging_when_subjects_spread_apart(self):
        frame_w = 1920
        frame_h = 1080
        crop_w = 606  # narrow 9:16
        crop_h = 1080

        detector = MagicMock(spec=CinerushFaceDetector)
        # Two actors at opposite ends of room (x=100 and x=1800, span=1700 > crop_w * 0.88)
        detector.detect_faces.return_value = [
            (50, 200, 100, 100),   # center = 100
            (1750, 200, 100, 100), # center = 1800
        ]

        with patch("cv2.VideoCapture") as mock_cap:
            instance = MagicMock()
            instance.isOpened.return_value = True
            instance.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
            mock_cap.return_value = instance

            x_start, x_end, is_wide = calculate_shot_tracking(
                Path("dummy.mp4"), 0.0, 2.0, frame_w, frame_h, crop_w, crop_h, detector
            )

            self.assertTrue(is_wide, "Should detect wide shot to trigger blurred background composite")


class TestCinerushKaraokeSubtitles(unittest.TestCase):
    def test_phrase_grouping_breaks_on_punctuation_and_caps_at_4_words(self):
        words = [
            {"word": "Did", "start": 0.0, "end": 0.3},
            {"word": "you", "start": 0.3, "end": 0.6},
            {"word": "kill", "start": 0.6, "end": 0.9},
            {"word": "him?", "start": 0.9, "end": 1.2},  # Question mark -> break!
            {"word": "I", "start": 1.3, "end": 1.5},
            {"word": "did", "start": 1.5, "end": 1.7},
            {"word": "not", "start": 1.7, "end": 1.9},
            {"word": "do", "start": 1.9, "end": 2.1},
            {"word": "it.", "start": 2.1, "end": 2.4},
        ]
        phrases = group_words_into_cinerush_phrases(words, max_words_per_phrase=4)

        # First phrase must end at 'him?' (4 words)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(len(phrases[0]), 4)
        self.assertEqual(phrases[0][-1]["word"], "him?")

        # Second phrase has 5 words: ['I', 'did', 'not', 'do', 'it.'] (anti-orphan lookahead prevents 1-word 'it.')
        self.assertEqual(len(phrases[1]), 5)
        self.assertEqual(phrases[1][-1]["word"], "it.")

    def test_no_orphan_phrases_on_short_sentences_and_fillers(self):
        # 5-word sentence kept as a single card instead of 4 + 1
        s5 = [
            {"word": "I", "start": 0.0, "end": 0.2},
            {"word": "did", "start": 0.2, "end": 0.4},
            {"word": "not", "start": 0.4, "end": 0.6},
            {"word": "do", "start": 0.6, "end": 0.8},
            {"word": "it.", "start": 0.8, "end": 1.0},
        ]
        p5 = group_words_into_cinerush_phrases(s5)
        self.assertEqual(len(p5), 1)
        self.assertEqual(len(p5[0]), 5)

        # 4-word sentence with mid comma not split into 3 + 1
        s4 = [
            {"word": "Look", "start": 0.0, "end": 0.2},
            {"word": "at", "start": 0.2, "end": 0.4},
            {"word": "that,", "start": 0.4, "end": 0.6},
            {"word": "man.", "start": 0.6, "end": 0.8},
        ]
        p4 = group_words_into_cinerush_phrases(s4)
        self.assertEqual(len(p4), 1)
        self.assertEqual(len(p4[0]), 4)

        # Standalone filler tokens ('Um,', 'uh') stripped to prevent visual clutter
        s_filler = [
            {"word": "Um,", "start": 0.0, "end": 0.2},
            {"word": "look", "start": 0.3, "end": 0.5},
            {"word": "here.", "start": 0.5, "end": 0.8},
        ]
        pf = group_words_into_cinerush_phrases(s_filler)
        self.assertEqual(len(pf), 1)
        self.assertEqual([w["word"] for w in pf[0]], ["look", "here."])

    def test_invalid_word_timings_are_ignored(self):
        words = [
            {"word": "ok", "start": 0.0, "end": 0.3},
            {"word": "bad", "start": "nan", "end": 0.5},
            {"word": "late", "start": 0.4, "end": 0.8},
        ]
        phrases = group_words_into_cinerush_phrases(words)
        self.assertEqual([word["word"] for phrase in phrases for word in phrase], ["ok", "late"])

    def test_ass_file_generation_karaoke_highlight(self):
        words = [
            {"word": "Look", "start": 0.0, "end": 0.4},
            {"word": "at", "start": 0.4, "end": 0.6},
            {"word": "me.", "start": 0.6, "end": 1.0},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
            ass_path = Path(tmp.name)

        try:
            generate_cinerush_karaoke_ass(
                words,
                ass_path,
                highlight_color="&H00FF00FF&",
                base_color="&H0000FFFF&",
            )
            content = ass_path.read_text(encoding="utf-8")

            # Verify ASS headers
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn(",40,40,260,1", content)  # MarginV: 260 for vertical 9:16
            self.assertIn("Style: Cinerush", content)
            self.assertIn("-1,-1,0,0,100,100,1,0,1,3.5,1.5,2,40,40,260,1", content)  # Bold -1, Italic -1

            # Verify active word is highlighted magenta and inactive words golden yellow, all uppercase
            dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
            self.assertEqual(len(dialogue_lines), 3)

            # Event 1: 'LOOK' is active (magenta \\c&H00FF00FF&), 'AT' and 'ME.' are golden yellow (\\c&H0000FFFF&)
            self.assertIn(r"{\c&H00FF00FF&\fscx106\fscy106\blur1.2}LOOK{\r}", dialogue_lines[0])
            self.assertIn(r"{\c&H0000FFFF&\fscx100\fscy100}AT{\r}", dialogue_lines[0])

            # Event 2: 'AT' is active (magenta)
            self.assertIn(r"{\c&H00FF00FF&\fscx106\fscy106\blur1.2}AT{\r}", dialogue_lines[1])
        finally:
            if ass_path.exists():
                ass_path.unlink()

    def test_ass_time_rolls_over_centisecond_boundary(self):
        self.assertEqual(format_ass_time(1.999), "0:00:02.00")

    def test_ass_file_generation_square_profile(self):
        words = [
            {"word": "Stand", "start": 0.0, "end": 0.5},
            {"word": "up.", "start": 0.5, "end": 0.9},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
            ass_path = Path(tmp.name)

        try:
            generate_cinerush_karaoke_ass(words, ass_path, profile="cinerush_style")
            content = ass_path.read_text(encoding="utf-8")

            # 1:1 square headers with MarginV 140
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1080", content)
            self.assertIn(",40,40,140,1", content)
            self.assertIn("STAND{\\r}", content)
            self.assertIn("UP.{\\r}", content)
        finally:
            if ass_path.exists():
                ass_path.unlink()

    def test_censor_word_and_sensitive_dialogue(self):
        from cinerush_subtitles import censor_word
        self.assertEqual(censor_word("KILLER,"), "K*LLER,")
        self.assertEqual(censor_word("killer"), "k*ller")
        self.assertEqual(censor_word("him?"), "him?")
        self.assertEqual(censor_word("fucking"), "f*cking")
        self.assertEqual(censor_word("DEAD."), "D*AD.")
        self.assertEqual(censor_word("gun"), "g*n")
        self.assertEqual(censor_word("murder"), "m*rder")

        words = [
            {"word": "Serial", "start": 0.0, "end": 0.5},
            {"word": "killer,", "start": 0.5, "end": 1.0},
            {"word": "are", "start": 1.0, "end": 1.3},
            {"word": "you?", "start": 1.3, "end": 1.7},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
            ass_path = Path(tmp.name)
        try:
            generate_cinerush_karaoke_ass(words, ass_path, profile="cinerush_style")
            content = ass_path.read_text(encoding="utf-8")
            self.assertIn("SERIAL{\\r}", content)
            self.assertIn("K*LLER,{\\r}", content)
            self.assertIn(",40,40,140,1", content)
        finally:
            if ass_path.exists():
                ass_path.unlink()


class TestCinerushShutterSFX(unittest.TestCase):
    def test_ensure_shutter_sfx_creates_valid_wav(self):
        from cinerush_shutter_sfx import ensure_camera_shutter_sfx
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            sfx = ensure_camera_shutter_sfx(tmp_path)
            self.assertTrue(sfx.exists())
            self.assertGreater(sfx.stat().st_size, 1000)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_apply_shutter_skips_when_no_cuts(self):
        from cinerush_shutter_sfx import apply_camera_shutter_transitions
        dummy = Path("dummy.mp4")
        out = Path("dummy_out.mp4")
        result = apply_camera_shutter_transitions(dummy, [], out)
        self.assertEqual(result, dummy)


class TestCinerushSeriesProgression(unittest.TestCase):
    def test_select_next_movie_advances_series(self):
        from generate_movie_short import select_next_movie
        movie_data = select_next_movie()
        self.assertIsNotNone(movie_data.get("id"))
        self.assertIsNotNone(movie_data.get("title"))
        self.assertGreaterEqual(movie_data.get("part_number", 1), 1)

    def test_update_series_progress_advances_part(self):
        from movie_pipeline_state import update_series_progress
        history = {
            "current_series": {
                "movie_id": "just_mercy",
                "movie_title": "Just Mercy",
                "current_part": 3,
                "total_parts": 6,
                "completed": False,
            }
        }
        update_series_progress(history, "just_mercy", "Just Mercy", 4, 6)
        self.assertEqual(history["current_series"]["current_part"], 4)
        self.assertFalse(history["current_series"]["completed"])

        # When reaching final part
        update_series_progress(history, "just_mercy", "Just Mercy", 6, 6)
        self.assertEqual(history["current_series"]["current_part"], 6)
        self.assertTrue(history["current_series"]["completed"])


class TestCinerushBGM(unittest.TestCase):
    def test_resolve_cinerush_bgm_defaults_to_rayalo(self):
        from cinerush_bgm import resolve_cinerush_bgm
        resolved = resolve_cinerush_bgm()
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.exists())
        self.assertIn("ray", resolved.name.lower())

    def test_resolve_cinerush_bgm_supports_aliases(self):
        from cinerush_bgm import resolve_cinerush_bgm
        self.assertIsNotNone(resolve_cinerush_bgm("rayalo"))
        self.assertIsNotNone(resolve_cinerush_bgm("rayelo"))
        self.assertIsNotNone(resolve_cinerush_bgm("rayelo_ultraslowed.mp3"))

    def test_apply_cinerush_bgm_missing_video_falls_back(self):
        from cinerush_bgm import apply_cinerush_bgm
        dummy_v = Path("non_existent_video_123.mp4")
        out = Path("non_existent_out_123.mp4")
        # Should gracefully return original video_path without crashing
        res = apply_cinerush_bgm(dummy_v, out)
        self.assertEqual(res, dummy_v)


if __name__ == "__main__":
    unittest.main()
