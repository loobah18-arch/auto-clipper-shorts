#!/usr/bin/env python3
"""Static contract tests for the cloud workflow and safety defaults."""

import subprocess
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
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertNotIn("malevolent_shrine_sukuna.mp3", orchestrator)
        self.assertNotIn("malevolent_shrine_sukuna.mp3", workflow)
        self.assertIn("cinematic_suspense_thriller.mp3", workflow)
        self.assertIn("MOVIE_BGM_PATH", orchestrator)
        self.assertIn("ALLOW_PRIVATE_MEDIA_SOURCES", orchestrator)

    def test_schedule_runs_twice_daily(self):
        """One part per run, twice a day, as requested."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertIn("\n  schedule:\n", workflow)
        self.assertIn("- cron: '15 8,20 * * *'", workflow)

    def test_scheduled_runs_publish_publicly_but_still_fail_closed(self):
        """Publishing is public by operator choice; the safety gates stay on."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        quality = (WORKSPACE_DIR / "movie_quality.py").read_text(encoding="utf-8")

        # Public is the configured default, in the workflow and the fallback.
        self.assertIn("default: 'public'", workflow)
        self.assertIn("inputs.privacy_status || 'public'", workflow)
        self.assertIn('os.environ.get("PRIVACY_STATUS") or "public"', orchestrator)
        # A footage-less render can never be published, public or not.
        self.assertIn("PLACEHOLDER_SOURCE_TYPES", quality)
        self.assertIn("validate_upload_source(", orchestrator)
        # Series only advances on a confirmed upload.
        self.assertIn("if status == RunStatus.UPLOADED.value", orchestrator)

    def test_existing_videos_can_be_republished(self):
        """The maintenance path for changing visibility of past uploads."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        script = (WORKSPACE_DIR / "set_video_visibility.py").read_text(encoding="utf-8")
        self.assertIn("set_visibility", workflow)
        self.assertIn("set_video_visibility.py", workflow)
        self.assertIn("privacyStatus", script)
        # It must not run the normal pipeline when only flipping visibility.
        self.assertIn("if: inputs.set_visibility == '' || inputs.set_visibility == null", workflow)

    def test_movie_step_does_not_receive_unused_cookie_secret(self):
        """YOUTUBE_COOKIES is only read by the podcast path in main.py."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertNotIn("YOUTUBE_COOKIES", workflow)

    def test_po_token_plugin_installed_and_gated(self):
        """A running PO Token server is useless without the yt-dlp plugin."""
        requirements = (WORKSPACE_DIR / "requirements.txt").read_text(encoding="utf-8")
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertIn("bgutil-ytdlp-pot-provider", requirements)
        self.assertIn("pip show bgutil-ytdlp-pot-provider", workflow)

    def test_po_token_plugin_version_matches_service_image(self):
        """Version skew between plugin and server breaks token negotiation."""
        requirements = (WORKSPACE_DIR / "requirements.txt").read_text(encoding="utf-8")
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")

        plugin_version = None
        for line in requirements.splitlines():
            line = line.strip()
            if line.startswith("bgutil-ytdlp-pot-provider"):
                plugin_version = line.split("==", 1)[1].strip()
                break

        image_version = None
        for line in workflow.splitlines():
            stripped = line.strip()
            if stripped.startswith("image:") and "bgutil-ytdlp-pot-provider" in stripped:
                image_version = stripped.rsplit(":", 1)[1].strip()
                break

        self.assertIsNotNone(plugin_version, "plugin must be version-pinned in requirements.txt")
        self.assertIsNotNone(image_version, "service image must be version-pinned in the workflow")
        self.assertEqual(plugin_version, image_version)

    def test_pot_provider_url_is_exported_to_the_pipeline(self):
        """The plugin needs the provider address; the engine turns it into an extractor arg."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        engine = (WORKSPACE_DIR / "movie_video_engine.py").read_text(encoding="utf-8")
        self.assertIn("YT_DLP_POT_PROVIDER_URL", workflow)
        self.assertIn("YT_DLP_POT_PROVIDER_URL", engine)
        self.assertIn("youtubepot-bgutilhttp:base_url=", engine)

    def test_real_gdrive_map_is_never_committed(self):
        """The map holds private Drive IDs, so it must be gitignored, not absent.

        A local movie_gdrive_map.json is a legitimate working state; what must
        never happen is it being tracked by git.
        """
        gitignore = (WORKSPACE_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("movie_gdrive_map.json", gitignore)
        self.assertTrue((WORKSPACE_DIR / "movie_gdrive_map.example.json").exists())

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "movie_gdrive_map.json"],
            cwd=WORKSPACE_DIR, capture_output=True, text=True,
        )
        self.assertNotEqual(
            tracked.returncode, 0,
            "movie_gdrive_map.json is gitignored but must never be git-tracked",
        )

    def test_catalog_still_carries_no_private_drive_ids(self):
        catalog = (WORKSPACE_DIR / "movie_catalog.json").read_text(encoding="utf-8")
        self.assertNotIn("gdrive_file_id", catalog)

    def test_workflow_decodes_drive_map_before_caching(self):
        """The map must exist on disk before hashFiles() can bust the cache."""
        workflow = (WORKSPACE_DIR / ".github/workflows/daily_clip.yml").read_text(encoding="utf-8")
        self.assertIn("GDRIVE_MAP_B64", workflow)
        map_at = workflow.index("GDRIVE_MAP_B64")
        cache_at = workflow.index("hashFiles('movie_catalog.json', 'movie_gdrive_map.json')")
        self.assertLess(map_at, cache_at, "map must be decoded before the cache key hashes it")

    def test_drive_sourcing_stays_behind_an_explicit_gate(self):
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        self.assertIn("ALLOW_PRIVATE_MEDIA_SOURCES", orchestrator)
        self.assertIn("resolve_gdrive_source", orchestrator)

    def test_trailer_sourcing_tries_multiple_player_clients(self):
        """GitHub runner IPs are bot-gated by YouTube's default web client."""
        engine = (WORKSPACE_DIR / "movie_video_engine.py").read_text(encoding="utf-8")
        for client in ("tv", "web_safari", "android", "ios", "mweb"):
            self.assertIn(f"youtube:player_client={client}", engine)
        self.assertIn("--extractor-args", engine)

    def test_trailer_sourcing_keeps_safety_boundaries(self):
        """Client fallback must not reintroduce cert bypasses or cookie scraping."""
        engine = (WORKSPACE_DIR / "movie_video_engine.py").read_text(encoding="utf-8")
        download_block = engine[engine.index("def download_movie_trailer"):]
        download_block = download_block[:download_block.index("\ndef ", 10)]
        self.assertNotIn("--no-check-certificates", download_block)
        self.assertNotIn("--cookies", download_block)
        self.assertNotIn("cookies-from-browser", download_block)


if __name__ == "__main__":
    unittest.main()
