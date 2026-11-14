#!/usr/bin/env python3
"""Unit tests for narrated_recap.py (pure logic only: no network, no ffmpeg)."""

import json
import unittest

import narrated_recap as nr


def _timed(texts, start=0.0, each=4.0):
    out, t = [], start
    for i, text in enumerate(texts):
        out.append({"text": text, "start": i * 20.0, "end": i * 20.0 + 15.0,
                    "out_start": t, "out_end": t + each})
        t += each
    return out


class TestTitleHook(unittest.TestCase):
    def test_accepts_declarative(self):
        self.assertEqual(nr.clean_title_hook("He Compressed 100 RedBulls into 1 Drink"),
                         "He Compressed 100 RedBulls into 1 Drink")

    def test_strips_emoji_and_handle(self):
        self.assertEqual(nr.clean_title_hook("This Guy Turned Cotton into Candy 😳 (@NileRed/YT)"),
                         "This Guy Turned Cotton into Candy")

    def test_rejects_question_and_junk(self):
        self.assertIsNone(nr.clean_title_hook("Can you turn cotton into candy?"))
        self.assertIsNone(nr.clean_title_hook(""))
        self.assertIsNone(nr.clean_title_hook(None))
        self.assertIsNone(nr.clean_title_hook("He " + "x" * 90))

    def test_package_title(self):
        info = {"channel_handle": "@NileRed", "packaging": {"shock_emojis": "😳"}}
        self.assertEqual(nr.package_title("He Made Pure Cookie", "fallback", info),
                         "He Made Pure Cookie 😳 (@NileRed/YT)")
        self.assertEqual(nr.package_title(None, "fallback", info), "fallback")


class TestScriptValidation(unittest.TestCase):
    def _raw(self, n=8, words=14):
        return {
            "title_hook": "He Turned Air Into Alcohol",
            "description": "He pulled CO2 out of the air and turned it into vodka. #shorts",
            "lines": [{"text": " ".join(["word"] * words), "start": i * 30, "end": i * 30 + 10} for i in range(n)],
        }

    def test_valid_script(self):
        s = nr.validate_script(self._raw(), 600)
        self.assertEqual(len(s["lines"]), 8)
        self.assertEqual(s["word_count"], 112)
        self.assertNotIn("#", s["description"])

    def test_clamps_ranges_to_video(self):
        raw = self._raw()
        raw["lines"][-1]["start"] = 5000
        raw["lines"][-1]["end"] = 6000
        s = nr.validate_script(raw, 600)
        self.assertLessEqual(s["lines"][-1]["end"], 600)
        self.assertLessEqual(s["lines"][-1]["start"], 598)

    def test_blocks_wild_backtrack(self):
        raw = self._raw()
        raw["lines"][5]["start"] = 0
        s = nr.validate_script(raw, 600)
        self.assertGreaterEqual(s["lines"][5]["start"], s["lines"][4]["start"])

    def test_rejects_thin_script(self):
        with self.assertRaises(ValueError):
            nr.validate_script(self._raw(n=3), 600)

    def test_extract_json_from_fenced_output(self):
        text = "<think>hmm</think>\n```json\n" + json.dumps({"a": 1}) + "\n```"
        self.assertEqual(nr._extract_json(text), {"a": 1})


class TestTranscriptCondense(unittest.TestCase):
    def test_blocks_and_budget(self):
        segs = [{"start": i * 5.0, "end": i * 5 + 4, "text": "alpha beta gamma delta " * 10} for i in range(400)]
        out = nr.condense_transcript(segs, block_seconds=20, max_words=1000)
        self.assertTrue(out.startswith("[0-"))
        self.assertLess(len(out.split()), 1600)


class TestCutsAndCaptions(unittest.TestCase):
    def test_cuts_cover_voiceover(self):
        timed = _timed(["one two three"] * 5, each=5.0)
        cuts = nr.plan_cuts(timed, source_duration=200)
        self.assertAlmostEqual(sum(c["duration"] for c in cuts), 25.0, places=2)
        for c in cuts:
            self.assertGreaterEqual(c["src_start"], 0)
            self.assertLessEqual(c["src_start"] + c["duration"], 200)

    def test_cuts_follow_script_windows(self):
        timed = _timed(["a b c", "d e f"], each=2.0)
        cuts = nr.plan_cuts(timed, source_duration=500)
        self.assertEqual(cuts[0]["src_start"], 0.0)
        self.assertEqual(cuts[-1]["src_start"], 20.0)

    def test_caption_chunks_span_line(self):
        timed = _timed(["What would happen if you compressed one hundred cans"], each=3.0)
        cues = nr.caption_chunks(timed)
        self.assertEqual(cues[0][0], 0.0)
        self.assertAlmostEqual(cues[-1][1], 3.0, places=2)
        self.assertEqual(" ".join(c[2] for c in cues), timed[0]["text"])
        self.assertTrue(all(len(c[2].split()) >= 2 for c in cues))

    def test_ass_output(self):
        ass = nr.build_ass([(0.0, 1.2, "hello {world}")])
        self.assertIn(f"PlayResY: {nr.OUT_H}", ass)
        self.assertEqual(nr.OUT_W, 1080)
        self.assertIn("HELLO (WORLD)", ass)
        self.assertIn("0:00:01.20", ass)


class TestBgmAndVoice(unittest.TestCase):
    def test_default_bgm_resolution(self):
        self.assertEqual(nr.DEFAULT_BGM, "main_attraction_phonk.mp3")
        path = nr.pick_bgm({})
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertIn("phonk", path.name.lower())

    def test_bgm_volume_is_quiet_and_narrator_balanced(self):
        self.assertLessEqual(nr.BGM_VOLUME, 0.08)
        self.assertGreater(nr.BGM_VOLUME, 0.0)
        self.assertEqual(nr.VOICE_VOLUME, 1.6)

    def test_audio_filter_constant_bgm_no_ducking(self):
        af = nr.build_audio_filter(50.0, has_bgm=True)
        # BGM must stay constant: no sidechain ducking / pumping
        self.assertNotIn("sidechaincompress", af)
        self.assertNotIn("asplit=2", af)
        self.assertIn("amix=inputs=2:normalize=0", af)
        self.assertIn("loudnorm=I=-14:TP=-2.0:LRA=11[vo]", af)
        self.assertIn(f"volume={nr.BGM_VOLUME:.2f}[bg]", af)
        self.assertIn("alimiter=limit=0.95", af)

    def test_audio_filter_fallback_without_bgm(self):
        af = nr.build_audio_filter(50.0, has_bgm=False)
        self.assertNotIn("amix", af)
        self.assertIn("loudnorm=I=-14:TP=-1.5:LRA=11[a]", af)

    def test_human_voice_selection(self):
        self.assertIn("MultilingualNeural", nr.DEFAULT_VOICE)
        # Default fallback
        self.assertEqual(nr.pick_voice({}), nr.DEFAULT_VOICE)
        # Filters out robotic legacy GuyNeural and upgrades to modern human voice
        self.assertEqual(nr.pick_voice({"narrator_voice": "en-US-GuyNeural"}), nr.DEFAULT_VOICE)
        # Keeps authentic human multilingual voices
        self.assertEqual(nr.pick_voice({"narrator_voice": "en-US-BrianMultilingualNeural"}),
                         "en-US-BrianMultilingualNeural")


if __name__ == "__main__":
    unittest.main(verbosity=2)
