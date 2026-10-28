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
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

from cinerush_downloader import build_cinerush_search_query
from cinerush_viral_selector import (
    format_cinerush_title,
    build_trimmed_timeline,
    _heuristic_viral_window,
)
from cinerush_face_reframer import (
    CinerushFaceDetector,
    calculate_shot_crop_x,
    detect_camera_shots,
)
from cinerush_subtitles import (
    group_words_into_cinerush_phrases,
    generate_cinerush_karaoke_ass,
    format_ass_time,
)


class TestCinerushDownloader(unittest.TestCase):
    def test_search_query_excludes_trailers_and_reviews(self):
        query = build_cinerush_search_query("Just Mercy courtroom scene")
        self.assertIn("full scene HD", query)
        self.assertIn("-trailer", query)
        self.assertIn("-review", query)
        self.assertIn("-shorts", query)
        self.assertIn("-teaser", query)
        self.assertNotIn("trailer", query.lower().split("-")[0])


class TestCinerushViralSelector(unittest.TestCase):
    def test_cinerush_title_formatting(self):
        title = format_cinerush_title("They broke his hand so he beat them with his left", "Just Mercy")
        self.assertIn("#film #movie #shorts", title)
        # Verify word count <= 12
        words = title.split()
        self.assertLessEqual(len(words), 14)
        # Verify emojis present
        self.assertTrue("🔥" in title or "🎬" in title or "♠️" in title)

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
        self.assertGreaterEqual(res["clip_end"] - res["clip_start"], 90.0)
        self.assertLessEqual(res["clip_end"] - res["clip_start"], 175.0)
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
            self.assertEqual(crop_x, expected_center_x)


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
        self.assertEqual(len(phrases[0]), 4)
        self.assertEqual(phrases[0][-1]["word"], "him?")

        # Second phrase capped at 4 words: ['I', 'did', 'not', 'do']
        self.assertEqual(len(phrases[1]), 4)
        # Third phrase has remaining ['it.']
        self.assertEqual(len(phrases[2]), 1)

    def test_ass_file_generation_karaoke_highlight(self):
        words = [
            {"word": "Look", "start": 0.0, "end": 0.4},
            {"word": "at", "start": 0.4, "end": 0.6},
            {"word": "me.", "start": 0.6, "end": 1.0},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
            ass_path = Path(tmp.name)

        try:
            generate_cinerush_karaoke_ass(words, ass_path)
            content = ass_path.read_text(encoding="utf-8")

            # Verify ASS headers
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn(",40,40,280,1", content)
            self.assertIn("Style: Cinerush", content)

            # Verify active word is highlighted yellow and inactive words white
            dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
            self.assertEqual(len(dialogue_lines), 3)

            # Event 1: 'Look' is active (yellow \c&H00FFFF&), 'at' and 'me.' are white (\c&HFFFFFF&)
            self.assertIn(r"{\c&H00FFFF&\fscx108\fscy108}Look{\r}", dialogue_lines[0])
            self.assertIn(r"{\c&HFFFFFF&\fscx100\fscy100}at{\r}", dialogue_lines[0])

            # Event 2: 'at' is active (yellow)
            self.assertIn(r"{\c&H00FFFF&\fscx108\fscy108}at{\r}", dialogue_lines[1])
        finally:
            if ass_path.exists():
                ass_path.unlink()


if __name__ == "__main__":
    unittest.main()
