#!/usr/bin/env python3
"""Unit tests for highlight_clip.py (no network, no ffmpeg)."""

import unittest

import highlight_clip as hc

SEGMENTS = [{"start": float(i * 10), "end": float(i * 10 + 8), "text": f"Line {i} words here."} for i in range(40)]


class TestHook(unittest.TestCase):
    def test_clean_spoken_hook_keeps_question(self):
        self.assertEqual(hc.clean_spoken_hook("Can he escape 100 real cops for $500,000?"),
                         "Can he escape 100 real cops for $500,000?")

    def test_clean_spoken_hook_strips_emoji_and_adds_mark(self):
        self.assertEqual(hc.clean_spoken_hook("What would happen if Speed went undercover 😳"),
                         "What would happen if Speed went undercover?")

    def test_clean_spoken_hook_rejects_junk(self):
        self.assertIsNone(hc.clean_spoken_hook("Wow"))
        self.assertIsNone(hc.clean_spoken_hook(None))

    def test_fallback_hook_is_question(self):
        h = hc.fallback_spoken_hook("Making an atomic trampoline", "NileRed")
        self.assertTrue(h.endswith("?"))
        self.assertIn("NileRed", h)
        self.assertEqual(hc.fallback_spoken_hook("Can He Escape 100 Real Cops For $500,000?", "MrBeast"),
                         "Can He Escape 100 Real Cops For $500,000?")


class TestClips(unittest.TestCase):
    def test_snaps_to_sentence_edges_and_orders(self):
        raw = [{"start": 51, "end": 69, "badge": "twist"}, {"start": 1, "end": 19, "badge": "start 😳"}]
        clips = hc.validate_clips(raw, SEGMENTS, 400, 50)
        self.assertEqual([c["start"] for c in clips], [0.0, 50.0])
        self.assertEqual([c["end"] for c in clips], [16.0, 66.0])  # snapped to 18/68, capped at 16s
        self.assertEqual(clips[0]["badge"], "START")

    def test_fits_budget(self):
        raw = [{"start": i * 50, "end": i * 50 + 14} for i in range(7)]
        clips = hc.validate_clips(raw, SEGMENTS, 400, 40)
        self.assertLessEqual(sum(c["end"] - c["start"] for c in clips), 40.01)

    def test_removes_overlap(self):
        raw = [{"start": 0, "end": 28}, {"start": 10, "end": 18}, {"start": 20, "end": 48}]
        clips = hc.validate_clips(raw, SEGMENTS, 400, 60)
        for a, b in zip(clips, clips[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_no_clips_raises(self):
        with self.assertRaises(RuntimeError):
            hc.validate_clips([], SEGMENTS, 400, 50)


class TestCaptions(unittest.TestCase):
    def test_cues_follow_real_words_on_output_timeline(self):
        words = hc.words_from_segments([{"start": 100.0, "end": 102.0, "text": "Behind me are 100 cops."}])
        cues = hc.caption_cues([{"start": 100.0, "end": 104.0}], words, intro=4.0)
        self.assertEqual(" ".join(c[2] for c in cues), "Behind me are 100 cops.")
        self.assertGreaterEqual(cues[0][0], 4.0)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a[1], b[0] + 1e-6)

    def test_ass_has_hook_caps_and_badges(self):
        ass = hc.build_ass("Can he escape 100 real cops for $500,000?", 4.0,
                           [(4.2, 5.0, "behind me")], [(4.0, 10.0, "ESCAPE CHALLENGE")])
        self.assertIn("Style: Hook", ass)
        self.assertIn("BEHIND ME", ass)
        self.assertIn("ESCAPE CHALLENGE", ass)
        self.assertIn(f"PlayResY: {hc.OUT_H}", ass)


if __name__ == "__main__":
    unittest.main()
