#!/usr/bin/env python3
"""Title uniqueness / history and comment-sticker text selection (ccf.titles)."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from ccf import titles

WORDS = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in enumerate(
    "bro the cops are right behind me. No no no get off me!".split())]


class TestTitles(unittest.TestCase):
    def test_streamer_name_in_title(self):
        for kind in ("laugh", "shock", "hype", "game", None):
            t = titles.make(1, kind, "LMAOOO", "KaiCenat", "gta6", words=WORDS, seed=3)
            self.assertIn("KaiCenat", t["title"])

    def test_never_repeats_title_or_recent_template(self):
        hist, seen = [], set()
        for i in range(30):
            streamer = random.Random(i).choice(["xQc", "Jynxzi", "KaiCenat"])
            t = titles.make(i, "laugh", "NO WAY", streamer, "gta6", words=WORDS, chat_text="cops", history=hist, seed=i)
            self.assertNotIn(t["title"], seen)
            recent = {h["template"] for h in hist[-titles.HISTORY_WINDOW:]}
            if len(hist) < len(titles.TITLE_TEMPLATES):
                self.assertNotIn(t["template"], recent)
            seen.add(t["title"])
            hist.append({"title": t["title"], "template": t["template"]})

    def test_different_streamers_get_different_titles(self):
        a = titles.make(1, "laugh", "", "IShowSpeed", "gta6", seed=5)
        hist = [{"title": a["title"], "template": a["template"]}]
        b = titles.make(1, "laugh", "", "ohnePixel", "gta6", seed=5, history=hist)
        self.assertNotEqual(a["title"], b["title"])
        self.assertNotEqual(a["template"], b["template"])

    def test_title_uses_moment_details(self):
        t = titles.make(1, "shock", "NO WAY", "xQc", "gta6", words=WORDS, chat_text="", seed=0)
        self.assertTrue(any(x in t["title"] for x in ("NO WAY", "Cops", "Get off me", "No no no", "xQc")))
        self.assertIn(t["template"], {tid for tid, _n, _t in titles.TITLE_TEMPLATES})

    def test_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "title_history.json"
            titles.record_titles([{"title": "A", "template": "x", "streamer": "s"}], p)
            titles.record_titles([{"title": "B", "template": "y", "streamer": "s"}], p)
            h = titles.load_history(p)
            self.assertEqual([x["title"] for x in h], ["A", "B"])
            self.assertEqual(json.loads(p.read_text())[1]["template"], "y")


class TestStickerText(unittest.TestCase):
    MSGS = [
        {"t": 100.0, "text": "KEKW KEKW KEKW KEKW", "author": "spam1"},
        {"t": 101.0, "text": "LMAO", "author": "a"},
        {"t": 101.5, "text": "check my stream at twitch.tv/me", "author": "promo"},
        {"t": 102.0, "text": "!uptime", "author": "cmd"},
        {"t": 102.5, "text": "💀💀💀💀", "author": "emo"},
        {"t": 103.0, "text": "bro really thought the cops would let him go LMAO", "author": "ViceCityLurker_99"},
        {"t": 104.0, "text": "pepeLaugh pepeLaugh OMEGALUL", "author": "b"},
        {"t": 400.0, "text": "this message is way outside the spike window ok", "author": "late"},
    ]

    def test_picks_real_chat_message_and_chatter(self):
        c = titles.make_ig_comment(self.MSGS, 95, 115, peak_t=103, seed=1)
        self.assertEqual(c["source"], "chat")
        self.assertEqual(c["text"], "bro really thought the cops would let him go LMAO")
        self.assertEqual(c["username"], "vicecitylurker_99")
        self.assertTrue(8 <= len(c["text"]) <= 90)
        self.assertIn(c["time"], titles.IG_TIMES)
        self.assertTrue(c["likes"].endswith("K"))

    def test_filters_spam_links_emotes(self):
        cands = [t for t, _a, _t in titles.chat_phrase_candidates(self.MSGS, 95, 115)]
        self.assertEqual(cands, ["bro really thought the cops would let him go LMAO"])
        self.assertEqual(titles.clean_chat_text("go to www.site.com now please"), "")
        self.assertEqual(titles.clean_chat_text("no no no no no no"), "")

    def test_length_bounds(self):
        long = {"t": 100.0, "text": "word " * 30, "author": "x"}
        short = {"t": 100.0, "text": "so bad", "author": "y"}
        self.assertEqual(titles.chat_phrase_candidates([long, short], 90, 110), [])

    def test_falls_back_to_transcript_then_pool(self):
        c = titles.make_ig_comment([], 0, 10, words=WORDS, seed=2)
        self.assertEqual(c["source"], "transcript")
        self.assertIn("\"", c["text"])
        c2 = titles.make_ig_comment([], 0, 10, words=[], kind="laugh", seed=2)
        self.assertEqual(c2["source"], "pool")
        self.assertIn(c2["text"], titles.FALLBACK_COMMENTS["laugh"])

    def test_likes_vary_in_range(self):
        vals = {titles.random_likes(random.Random(i)) for i in range(40)}
        self.assertGreater(len(vals), 20)
        for v in vals:
            n = float(v[:-1])
            self.assertTrue(1.2 <= n <= 48.0, v)


if __name__ == "__main__":
    unittest.main()
