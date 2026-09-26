#!/usr/bin/env python3
"""Static contract tests for the cloud workflow and safety defaults."""

import unittest
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent


class TestWorkflowContract(unittest.TestCase):
    def test_workflow_fails_closed_and_publishes_diagnostics(self):
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", workflow)
        self.assertIn("exit 1", workflow)
        self.assertNotIn("|| true", workflow)
        self.assertNotIn(":latest", workflow)
        self.assertIn("output/render_manifest_*.json", workflow)
        self.assertIn("pipeline_debug.log", workflow)
        self.assertIn("fonts-noto-core", workflow)

    def test_public_catalog_has_no_private_media_ids(self):
        catalog = (WORKSPACE_DIR / "movie_catalog.json").read_text(encoding="utf-8")
        self.assertNotIn("gdrive_file_id", catalog)
        self.assertNotIn("gdrive_folder_id", catalog)

    def test_default_pipeline_does_not_select_copyrighted_bgm(self):
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        self.assertNotIn("malevolent_shrine_sukuna.mp3", orchestrator)
        self.assertIn("MOVIE_BGM_PATH", orchestrator)
        self.assertIn("ALLOW_PRIVATE_MEDIA_SOURCES", orchestrator)

    def test_scheduled_auto_publish_is_disabled(self):
        """The twice-daily cron must stay off until a manual dry run is reviewed."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertIn("# schedule:", workflow)
        self.assertNotIn("\n  schedule:\n", workflow)

    def test_default_privacy_status_is_unlisted(self):
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        self.assertIn("default: 'unlisted'", workflow)
        self.assertIn("inputs.privacy_status || 'unlisted'", workflow)
        self.assertIn('os.environ.get("PRIVACY_STATUS") or "unlisted"', orchestrator)

    def test_movie_step_does_not_receive_unused_cookie_secret(self):
        """YOUTUBE_COOKIES is only read by the podcast path in main.py."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertNotIn("YOUTUBE_COOKIES", workflow)


if __name__ == "__main__":
    unittest.main()
