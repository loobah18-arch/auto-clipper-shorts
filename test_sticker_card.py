import os
import tempfile
import unittest

from ccf import sticker


class StickerCard(unittest.TestCase):
    def _h(self, text):
        with tempfile.TemporaryDirectory() as d:
            return sticker.generate_instagram_sticker("user", "1h", text, "1.2K", os.path.join(d, "s.png"))["height"]

    def test_body_never_more_than_two_lines(self):
        two = self._h("he really bought the knife again and lost it all over again in one spray")
        self.assertEqual(self._h("word " * 60), two)            # ellipsized to two lines, card height stable
        self.assertLess(self._h("short line here"), two)

    def test_no_crash_on_unrenderable_symbols(self):
        self.assertGreater(self._h("this is wild \ufe0f\u200d \U0001FAE0 \U0001F3FB ok"), 0)


if __name__ == "__main__":
    unittest.main()
