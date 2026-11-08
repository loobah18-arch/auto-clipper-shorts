#!/usr/bin/env python3
"""
Unit tests for Creator Catalog & Multi-Creator SVML specifications.
Validates MrBeast, NileBlue, Mark Rober, IShowSpeed, and Ruhi Çenet.
"""

import json
import unittest
from pathlib import Path
from hypit_engine import HypitSVMLParser

ROOT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = ROOT_DIR / "creator_catalog.json"


class TestCreatorCatalog(unittest.TestCase):

    def setUp(self):
        self.assertTrue(CATALOG_PATH.exists(), "creator_catalog.json must exist")
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def test_catalog_structure(self):
        self.assertIn("version", self.catalog)
        self.assertIn("creators", self.catalog)
        creators = self.catalog["creators"]
        expected_creators = ["mrbeast", "nileblue", "mark_rober", "ishowspeed", "ruhi_cenet"]
        for cid in expected_creators:
            self.assertIn(cid, creators, f"Creator '{cid}' must be present in catalog")

    def test_creator_svml_specs_exist_and_parse(self):
        creators = self.catalog["creators"]
        for cid, info in creators.items():
            svml_path = ROOT_DIR / info["svml_source"]
            self.assertTrue(svml_path.exists(), f"SVML source for {cid} ({svml_path}) must exist")

            # Parse SVML to ensure validity
            parser = HypitSVMLParser(svml_path).parse()
            self.assertEqual(parser.canvas_width, 1080)
            self.assertEqual(parser.canvas_height, 1440)
            self.assertEqual(parser.timeline_duration, 60.0)
            self.assertIn("intro", parser.script_segments)
            self.assertIn("opening", parser.script_segments)
            self.assertIn("payoff", parser.script_segments)

    def test_creator_packaging_formula(self):
        creators = self.catalog["creators"]
        for cid, info in creators.items():
            packaging = info.get("packaging", {})
            self.assertIn("shock_emojis", packaging)
            self.assertIn("hashtags", packaging)
            self.assertGreaterEqual(len(packaging["hashtags"]), 3)
            self.assertIn("#shorts", packaging["hashtags"])
            self.assertFalse(packaging.get("madeForKids", True))


if __name__ == "__main__":
    unittest.main()
