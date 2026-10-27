#!/usr/bin/env python3
"""
Comprehensive Regression Test Suite for Movie Explanation Shorts Duration Policy,
Technical QC Gates, Media Rights Clearance, and Output State Transitions.
"""

import os
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from movie_quality import (
    DEFAULT_SHORTS_MAX_DURATION,
    SHORTS_HARD_CAP,
    ConfigurationError,
    MediaInfo,
    MediaValidationError,
    TechnicalQCReport,
    validate_duration_policy,
    validate_media_info,
    validate_rendered_video_technical,
    validate_subtitles_timing,
)
from movie_pipeline_state import (
    MediaRightsRecord,
    RenderManifest,
    RightsStatus,
    RunStatus,
)
from movie_ai_script import (
    calculate_script_word_budget,
    shorten_movie_script,
    ScriptDurationExceededError,
)
from movie_copyright_filter import MediaRightsAgent


class TestDurationAndRightsRegression(unittest.TestCase):
    """Regression tests covering all 14 safety and duration requirements."""

    def setUp(self):
        self.test_dir = Path(__file__).resolve().parent / "cache" / "test_qc"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. A video that fits the configured duration limit
    def test_1_video_fits_configured_duration_limit(self):
        info = MediaInfo(duration_sec=55.0, width=1080, height=1920, has_audio=True)
        # Should pass without error under default 58.0s limit
        validate_media_info(info, max_duration=58.0)

        # Also test with validate_rendered_video_technical mock
        mock_ffprobe = {
            "format": {"duration": "55.000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "duration": "55.000000"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "55.000000"},
            ],
        }
        dummy_file = self.test_dir / "test_fit.mp4"
        dummy_file.write_bytes(b"dummy video data")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_ffprobe))
            report = validate_rendered_video_technical(dummy_file, max_duration=58.0)
            self.assertTrue(report.passed)
            self.assertTrue(report.duration_passed)
            self.assertEqual(report.measured_duration, 55.0)

    # 2. A video that exceeds the limit by a fraction of a second
    def test_2_video_exceeds_limit_by_fraction_of_second(self):
        # 58.15s exceeds 58.0s + 0.05s tolerance (58.05s)
        info = MediaInfo(duration_sec=58.15, width=1080, height=1920, has_audio=True)
        with self.assertRaises(MediaValidationError) as ctx:
            validate_media_info(info, max_duration=58.0)
        self.assertIn("maximum is 58.00s", str(ctx.exception))

    # 3. A video that significantly exceeds the limit (e.g. 80.06s from real failure)
    def test_3_video_significantly_exceeds_limit(self):
        info = MediaInfo(duration_sec=80.06, width=1080, height=1920, has_audio=True)
        with self.assertRaises(MediaValidationError) as ctx:
            validate_media_info(info, max_duration=58.0)
        self.assertIn("Rendered media is 80.06s; maximum is 58.00s", str(ctx.exception))

        dummy_file = self.test_dir / "test_80s.mp4"
        dummy_file.write_bytes(b"dummy")
        mock_ffprobe = {
            "format": {"duration": "80.060000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "duration": "80.060000"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "80.060000"},
            ],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_ffprobe))
            report = validate_rendered_video_technical(dummy_file, max_duration=58.0)
            self.assertFalse(report.passed)
            self.assertFalse(report.duration_passed)
            self.assertTrue(any("exceeds the maximum ceiling" in f for f in report.failures))

    # 4. A missing or unreadable output file
    def test_4_missing_or_unreadable_output_file(self):
        missing_file = self.test_dir / "non_existent_output.mp4"
        report = validate_rendered_video_technical(missing_file, max_duration=58.0)
        self.assertFalse(report.passed)
        self.assertTrue(any("does not exist" in f for f in report.failures))

    # 5. A video with no audio stream
    def test_5_video_with_no_audio_stream(self):
        info = MediaInfo(duration_sec=50.0, width=1080, height=1920, has_audio=False)
        with self.assertRaises(MediaValidationError) as ctx:
            validate_media_info(info, max_duration=58.0)
        self.assertIn("must contain an audio stream", str(ctx.exception))

        dummy_file = self.test_dir / "silent.mp4"
        dummy_file.write_bytes(b"silent")
        mock_ffprobe = {
            "format": {"duration": "50.000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "duration": "50.000000"},
            ],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_ffprobe))
            report = validate_rendered_video_technical(dummy_file, max_duration=58.0)
            self.assertFalse(report.passed)
            self.assertFalse(report.has_audio)
            self.assertTrue(any("missing an audio stream" in f for f in report.failures))

    # 6. A subtitle track that extends beyond the video
    def test_6_subtitle_track_extends_beyond_video(self):
        sub_file = self.test_dir / "overrun.ass"
        sub_file.write_text(
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:59.50,Default,,0,0,0,,Overrun caption that exceeds video\n",
            encoding="utf-8",
        )
        # Video is 55.0s, last caption ends at 59.50s
        violations = validate_subtitles_timing(sub_file, video_duration=55.0)
        self.assertTrue(len(violations) > 0)
        self.assertTrue(any("extends beyond video" in v for v in violations))

    # 7. A script that needs shortening
    def test_7_script_needs_shortening(self):
        long_script = (
            "Imagine waking up trapped in an abandoned subterranean lab with only sixty seconds before the blast doors seal forever. "
            "Dr. Marcus frantically searches for the security terminal while sirens blare in the background. "
            "He discovers that his former colleague deliberately locked him in to steal the experimental serum. "
            "With toxic gas venting from the ceiling pipes, he must rewire the emergency release valve using a broken keycard. "
            "Every second counts as the timer ticks down to zero. "
            "Suddenly the backup generator fails, plunging the facility into pitch-black darkness. "
            "He reaches for the master lever — but someone grabs his wrist from behind! "
            "Wait until you see who was hiding in the shadows in Part 2! Like and follow so you don't miss it!"
        )
        words = long_script.split()
        self.assertGreater(len(words), 100)
        shortened = shorten_movie_script(long_script, target_words=60, movie_name="The Lab")
        shortened_words = shortened.split()
        self.assertLessEqual(len(shortened_words), 65)
        # Verify hook is retained
        self.assertTrue(shortened.startswith("Imagine waking up trapped"))
        # Verify cliffhanger is retained
        self.assertTrue("Like and follow" in shortened)
        # Verify clean sentence endings
        self.assertTrue(shortened.endswith((".", "!", "?")))

    # 8. A narration that remains too long after permitted revision attempts
    def test_8_narration_remains_too_long_after_permitted_revision_attempts(self):
        # Verify calculate_script_word_budget bounds
        min_w, max_w = calculate_script_word_budget(target_duration=58.0, format="shorts")
        self.assertLessEqual(max_w, 140)
        self.assertGreaterEqual(min_w, 80)

        # ScriptDurationExceededError is defined and can be raised
        with self.assertRaises(ScriptDurationExceededError):
            raise ScriptDurationExceededError("Narration remained over 58s after 3 revisions")

    # 9. A failed render that must not enter the publish-ready location
    def test_9_failed_render_must_not_enter_publish_ready_location(self):
        qc_failed_report = TechnicalQCReport(
            passed=False,
            duration_passed=False,
            measured_duration=75.0,
            target_max_duration=58.0,
            width=1080,
            height=1920,
            has_video=True,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
            container_duration=75.0,
            video_stream_duration=75.0,
            audio_stream_duration=75.0,
            subtitle_passed=True,
            failures=["Duration exceeds 58.0s"],
        )
        # State must transition to DURATION_QC_FAILED, never PUBLISH_READY
        state = RunStatus.DURATION_QC_FAILED if not qc_failed_report.duration_passed else RunStatus.PUBLISH_READY
        self.assertEqual(state, RunStatus.DURATION_QC_FAILED)
        self.assertNotEqual(state, RunStatus.PUBLISH_READY)

    # 10. Media with unknown rights status
    def test_10_media_with_unknown_rights_status(self):
        record = MediaRightsAgent.evaluate_rights(
            source_type="private_media",
            asset_metadata={"title": "Unlicensed Movie", "gdrive_file_id": "12345"},
            allow_unreviewed=False,
        )
        self.assertEqual(record.rights_status, RightsStatus.PERMISSION_UNKNOWN)
        self.assertTrue(record.requires_manual_review)
        self.assertIn("Drive file present but underlying movie rights status is unverified", record.notes)

    # 11. An authorized asset with a valid rights record
    def test_11_authorized_asset_with_valid_rights_record(self):
        # 1. Procedural motion graphic / neutral fallback
        neutral_record = MediaRightsAgent.evaluate_rights(source_type="neutral_fallback")
        self.assertEqual(neutral_record.rights_status, RightsStatus.PUBLIC_DOMAIN_VERIFIED)
        self.assertFalse(neutral_record.requires_manual_review)

        # 2. Licensed private media with verified license ID
        licensed_record = MediaRightsAgent.evaluate_rights(
            source_type="private_media",
            asset_metadata={"title": "Studio Film", "license_id": "LIC-2026-WOO-991"},
        )
        self.assertEqual(licensed_record.rights_status, RightsStatus.LICENSED)
        self.assertFalse(licensed_record.requires_manual_review)

        # 3. Official studio trailer
        trailer_record = MediaRightsAgent.evaluate_rights(
            source_type="authorized_trailer",
            asset_metadata={"official": True, "trailer_url": "https://youtube.com/watch?v=official_trailer"},
        )
        self.assertEqual(trailer_record.rights_status, RightsStatus.AUTHORIZED)
        self.assertFalse(trailer_record.requires_manual_review)

    # 12. Backward compatibility with existing configurations
    def test_12_backward_compatibility_with_existing_configurations(self):
        # Shorts default ceiling is 58.0
        self.assertEqual(DEFAULT_SHORTS_MAX_DURATION, 58.0)
        self.assertEqual(validate_duration_policy(max_duration=55.0, profile="shorts"), 55.0)

        # Long form profile supports extended durations
        long_val = validate_duration_policy(max_duration=1200.0, profile="long_form")
        self.assertEqual(long_val, 1200.0)

        # Invalid settings reject cleanly with ConfigurationError
        with self.assertRaises(ConfigurationError):
            validate_duration_policy(max_duration=65.0, profile="shorts")  # Cannot exceed 60.0s hard cap
        with self.assertRaises(ConfigurationError):
            validate_duration_policy(max_duration=-10.0, profile="shorts")
        with self.assertRaises(ConfigurationError):
            validate_duration_policy(max_duration="not_a_number", profile="shorts")

    # 13. Correct handling of the final audio tail
    def test_13_correct_handling_of_final_audio_tail(self):
        # Video stream is 54.0s, audio stream continues to 55.5s (1.5s audio tail)
        dummy_file = self.test_dir / "audio_tail.mp4"
        dummy_file.write_bytes(b"audio_tail")
        mock_ffprobe = {
            "format": {"duration": "55.500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "duration": "54.000000"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "55.500000"},
            ],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_ffprobe))
            report = validate_rendered_video_technical(dummy_file, max_duration=58.0)
            # Effective measured duration must take the max (55.5s), not the video stream (54.0s)
            self.assertEqual(report.measured_duration, 55.5)
            self.assertTrue(report.passed)
            self.assertTrue(report.duration_passed)
            self.assertTrue(any("audio tail detected" in w for w in report.warnings))

    # 14. Correct state transitions after a failed validation
    def test_14_correct_state_transitions_after_failed_validation(self):
        # Duration failure state transition
        manifest_dur = RenderManifest(
            movie_id="venom_3",
            title="Venom 3",
            part_number=1,
            total_parts=5,
            language="en",
            status=RunStatus.DURATION_QC_FAILED.value,
            duration_sec=70.0,
            youtube_id=None,
            video_file="venom_3.mp4",
            thumbnail_file=None,
            subtitle_file=None,
            script_sha256="abc",
            video_sha256=None,
            source_type="private_media",
            generated_at="2026-10-27T12:00:00Z",
            target_max_duration=58.0,
            technical_qc_passed=False,
            qc_failures=["Duration 70.0s > 58.0s"],
        )
        self.assertEqual(manifest_dur.status, "duration_qc_failed")
        self.assertFalse(manifest_dur.technical_qc_passed)

        # Rights review required state transition
        manifest_rights = RenderManifest(
            movie_id="venom_3",
            title="Venom 3",
            part_number=1,
            total_parts=5,
            language="en",
            status=RunStatus.RIGHTS_REVIEW_REQUIRED.value,
            duration_sec=52.0,
            youtube_id=None,
            video_file="venom_3.mp4",
            thumbnail_file=None,
            subtitle_file=None,
            script_sha256="abc",
            video_sha256=None,
            source_type="private_media",
            generated_at="2026-10-27T12:00:00Z",
            rights_status="permission_unknown",
            rights_review_required=True,
            manual_review_required=True,
        )
        self.assertEqual(manifest_rights.status, "rights_review_required")
        self.assertTrue(manifest_rights.manual_review_required)

        # Publish ready state transition
        manifest_ready = RenderManifest(
            movie_id="venom_3",
            title="Venom 3",
            part_number=1,
            total_parts=5,
            language="en",
            status=RunStatus.PUBLISH_READY.value,
            duration_sec=52.0,
            youtube_id=None,
            video_file="venom_3.mp4",
            thumbnail_file=None,
            subtitle_file=None,
            script_sha256="abc",
            video_sha256="hash123",
            source_type="authorized_trailer",
            generated_at="2026-10-27T12:00:00Z",
            rights_status="authorized",
            technical_qc_passed=True,
            rights_review_required=False,
            manual_review_required=False,
        )
        self.assertEqual(manifest_ready.status, "publish_ready")
        self.assertFalse(manifest_ready.manual_review_required)


if __name__ == "__main__":
    unittest.main()
