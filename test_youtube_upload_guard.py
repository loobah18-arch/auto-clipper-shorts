#!/usr/bin/env python3
"""
Unit tests for YouTube Upload Guard & Auto-Remediation Retry Engine.
Verifies:
1. YouTube status verification (clean vs rejected vs region-restricted).
2. Automated deletion of blocked YouTube videos to maintain channel standing.
3. Metadata remediation (Fair Use framing, title sanitization, statutory disclaimers).
4. Video remediation FFmpeg filter construction (hflip, crop-zoom, color grade, PTS/atempo retiming).
5. Full upload-and-retry lifecycle (single attempt success, retry recovery after block, and max retry exhaustion).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from movie_upload_guard import (
    check_youtube_video_status,
    classify_block_reason,
    delete_blocked_youtube_video,
    pick_alternative_bgm,
    remediate_metadata_for_block,
    remediate_video_for_block,
    upload_video_with_remediation_retry,
)


class TestYouTubeUploadGuard(unittest.TestCase):

    def test_check_youtube_video_status_clean(self):
        mock_youtube = MagicMock()
        mock_youtube.videos().list().execute.return_value = {
            "items": [
                {
                    "status": {"uploadStatus": "uploaded"},
                    "processingDetails": {"processingStatus": "succeeded"},
                    "contentDetails": {},
                }
            ]
        }

        is_blocked, reason, details = check_youtube_video_status(
            mock_youtube, "clean_vid_123", timeout_sec=0.0
        )
        self.assertFalse(is_blocked)
        self.assertIsNone(reason)
        self.assertEqual(details["status"]["uploadStatus"], "uploaded")

    def test_check_youtube_video_status_rejected_copyright(self):
        mock_youtube = MagicMock()
        mock_youtube.videos().list().execute.return_value = {
            "items": [
                {
                    "status": {
                        "uploadStatus": "rejected",
                        "rejectionReason": "copyright",
                    },
                    "processingDetails": {},
                    "contentDetails": {},
                }
            ]
        }

        is_blocked, reason, details = check_youtube_video_status(
            mock_youtube, "blocked_vid_456", timeout_sec=0.0
        )
        self.assertTrue(is_blocked)
        self.assertEqual(reason, "copyright")

    def test_check_youtube_video_status_region_restricted(self):
        mock_youtube = MagicMock()
        mock_youtube.videos().list().execute.return_value = {
            "items": [
                {
                    "status": {"uploadStatus": "uploaded"},
                    "processingDetails": {"processingStatus": "succeeded"},
                    "contentDetails": {
                        "regionRestriction": {
                            "blocked": [f"COUNTRY_{i}" for i in range(60)]
                        }
                    },
                }
            ]
        }

        is_blocked, reason, details = check_youtube_video_status(
            mock_youtube, "restricted_vid_789", timeout_sec=0.0
        )
        self.assertTrue(is_blocked)
        self.assertEqual(reason, "region_restricted_block")

    def test_delete_blocked_youtube_video(self):
        mock_youtube = MagicMock()
        deleted = delete_blocked_youtube_video(mock_youtube, "vid_to_delete")
        self.assertTrue(deleted)
        mock_youtube.videos().delete.assert_called_once_with(id="vid_to_delete")

    def test_remediate_metadata_for_block(self):
        meta = {
            "title": "Avengers Infinity War Full Movie 1080p #shorts",
            "description": "Watch this clip!",
            "tags": ["avengers", "movie"],
        }

        # Level 1 remediation
        rem1 = remediate_metadata_for_block(meta, attempt=1, is_short=True)
        self.assertIn("Scene Breakdown", rem1["title"])
        self.assertNotIn("Full Movie", rem1["title"])
        self.assertIn("FAIR USE COMMENTARY NOTICE", rem1["description"])
        self.assertTrue(any("fair use" in t for t in rem1["tags"]))

        # Level 2 remediation (more transformative)
        rem2 = remediate_metadata_for_block(meta, attempt=2, is_short=True)
        self.assertIn("Cinematic Scene Analysis", rem2["title"])

    def test_remediate_video_for_block_ffmpeg_args(self):
        input_vid = Path("test_input.mp4")
        output_vid = Path("test_output.mp4")

        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat") as mock_stat, \
             patch("subprocess.run") as mock_run:

            mock_stat.return_value.st_size = 50000
            # Mock ffprobe
            probe_result = MagicMock(returncode=0, stdout='{"streams": [{"width": 1080, "height": 1920}]}')
            # Mock ffmpeg render
            render_result = MagicMock(returncode=0, stderr="")
            mock_run.side_effect = [probe_result, render_result]

            res_path = remediate_video_for_block(input_vid, output_vid, attempt=1, is_short=True)
            self.assertEqual(res_path, output_vid)

            # Inspect FFmpeg call arguments
            ffmpeg_call = mock_run.call_args_list[1]
            cmd = ffmpeg_call[0][0]
            cmd_str = " ".join(cmd)

            self.assertIn("hflip", cmd_str)
            self.assertIn("crop=", cmd_str)
            self.assertIn("eq=contrast=", cmd_str)
            self.assertIn("setpts=PTS/1.015", cmd_str)
            self.assertIn("atempo=1.015", cmd_str)

    def test_upload_with_remediation_retry_succeeds_first_attempt(self):
        mock_youtube = MagicMock()
        mock_req = MagicMock()
        mock_req.next_chunk.return_value = (None, {"id": "clean_yt_100"})
        mock_youtube.videos().insert.return_value = mock_req

        # List returns clean status
        mock_youtube.videos().list().execute.return_value = {
            "items": [
                {
                    "status": {"uploadStatus": "uploaded"},
                    "processingDetails": {"processingStatus": "succeeded"},
                    "contentDetails": {},
                }
            ]
        }

        with patch("movie_upload_guard.MediaFileUpload"):
            vid_id, info = upload_video_with_remediation_retry(
                youtube=mock_youtube,
                video_path=Path("dummy.mp4"),
                thumb_path=None,
                meta={"title": "Test Title", "description": "Test Desc", "tags": []},
                is_short=True,
                max_retries=2,
                verify_timeout_sec=0.0,
            )

            self.assertEqual(vid_id, "clean_yt_100")
            self.assertFalse(info["remediated"])
            self.assertEqual(info["attempts"], 1)
            mock_youtube.videos().delete.assert_not_called()

    def test_upload_with_remediation_retry_recovers_after_block(self):
        mock_youtube = MagicMock()

        # Two upload attempts:
        # Attempt 1: returns id "blocked_id_1"
        # Attempt 2: returns id "clean_id_2"
        mock_req_1 = MagicMock()
        mock_req_1.next_chunk.return_value = (None, {"id": "blocked_id_1"})
        mock_req_2 = MagicMock()
        mock_req_2.next_chunk.return_value = (None, {"id": "clean_id_2"})
        mock_youtube.videos().insert.side_effect = [mock_req_1, mock_req_2]

        # Status responses:
        # Check 1: Rejected with copyright
        # Check 2: Clean uploaded
        status_blocked = {
            "items": [
                {
                    "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"},
                    "processingDetails": {},
                    "contentDetails": {},
                }
            ]
        }
        status_clean = {
            "items": [
                {
                    "status": {"uploadStatus": "uploaded"},
                    "processingDetails": {"processingStatus": "succeeded"},
                    "contentDetails": {},
                }
            ]
        }
        mock_youtube.videos().list().execute.side_effect = [status_blocked, status_clean]

        with patch("movie_upload_guard.MediaFileUpload"), \
             patch("movie_upload_guard.remediate_video_for_block", return_value=Path("dummy_remediated.mp4")):

            vid_id, info = upload_video_with_remediation_retry(
                youtube=mock_youtube,
                video_path=Path("dummy.mp4"),
                thumb_path=None,
                meta={"title": "Test Title", "description": "Test Desc", "tags": []},
                is_short=True,
                max_retries=2,
                verify_timeout_sec=0.0,
            )

            self.assertEqual(vid_id, "clean_id_2")
            self.assertTrue(info["remediated"])
            self.assertEqual(info["attempts"], 2)
            # Verify the blocked video was deleted from YouTube
            mock_youtube.videos().delete.assert_called_once_with(id="blocked_id_1")

    def test_upload_with_remediation_retry_aborts_after_max_retries(self):
        mock_youtube = MagicMock()

        # Both attempts blocked
        mock_req_1 = MagicMock()
        mock_req_1.next_chunk.return_value = (None, {"id": "blocked_1"})
        mock_req_2 = MagicMock()
        mock_req_2.next_chunk.return_value = (None, {"id": "blocked_2"})
        mock_youtube.videos().insert.side_effect = [mock_req_1, mock_req_2]

        status_blocked = {
            "items": [
                {
                    "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"},
                    "processingDetails": {},
                    "contentDetails": {},
                }
            ]
        }
        mock_youtube.videos().list().execute.return_value = status_blocked

        with patch("movie_upload_guard.MediaFileUpload"), \
             patch("movie_upload_guard.remediate_video_for_block", return_value=Path("dummy_remediated.mp4")):

            vid_id, info = upload_video_with_remediation_retry(
                youtube=mock_youtube,
                video_path=Path("dummy.mp4"),
                thumb_path=None,
                meta={"title": "Test Title", "description": "Test Desc", "tags": []},
                is_short=True,
                max_retries=1,  # 1 retry = 2 attempts total
                verify_timeout_sec=0.0,
            )

            self.assertIsNone(vid_id)
            self.assertTrue(info["blocked"])
            self.assertEqual(info["rejection_reason"], "copyright")
            # Both blocked videos should have been deleted
            self.assertEqual(mock_youtube.videos().delete.call_count, 2)

    def test_cinerush_upload_return_guard_info_on_block(self):
        from cinerush_pipeline import upload_cinerush_to_youtube

        mock_guard_info = {"blocked": True, "rejection_reason": "copyright", "attempts": 2}
        with patch("cinerush_pipeline.Credentials"), \
             patch("cinerush_pipeline.build"), \
             patch("os.environ.get", side_effect=lambda k, d=None: "dummy_val" if "CLIENT" in k or "REFRESH" in k else d), \
             patch("movie_upload_guard.upload_video_with_remediation_retry", return_value=(None, mock_guard_info)):

            res = upload_cinerush_to_youtube(
                video_path=Path("test.mp4"),
                thumb_path=Path("thumb.jpg"),
                meta={"title": "Test Title"},
                dry_run=False,
                is_short=True,
                return_guard_info=True,
            )

            self.assertIsInstance(res, tuple)
            yt_id, g_info = res
            self.assertIsNone(yt_id)
            self.assertTrue(g_info["blocked"])
            self.assertEqual(g_info["rejection_reason"], "copyright")

    def test_classify_block_reason(self):
        # Audio signals -> audio
        self.assertEqual(
            classify_block_reason("copyright", {"details": "audio_fingerprint match"}, bgm_path="custom.mp3"),
            "audio"
        )
        self.assertEqual(
            classify_block_reason("song claim detected", {}, bgm_path="custom.mp3"),
            "audio"
        )

        # Video signals -> clip
        self.assertEqual(
            classify_block_reason("videomatch detected", {}, bgm_path="custom.mp3"),
            "clip"
        )
        self.assertEqual(
            classify_block_reason("visual_fingerprint match", {}, bgm_path="custom.mp3"),
            "clip"
        )

        # NCS track used -> audio cannot be the cause, so must be clip
        self.assertEqual(
            classify_block_reason("copyright", {}, bgm_path="assets/bgm/cradles.mp3"),
            "clip"
        )
        self.assertEqual(
            classify_block_reason("terms_of_service", {}, bgm_path="assets/bgm/aerohead_fragments_recap.mp3"),
            "clip"
        )

        # Both signals -> both
        self.assertEqual(
            classify_block_reason("videomatch and audio_fingerprint match", {}, bgm_path="custom.mp3"),
            "both"
        )

    def test_upload_with_remediation_retry_diagnoses_block_type(self):
        mock_youtube = MagicMock()
        mock_req = MagicMock()
        mock_req.next_chunk.return_value = (None, {"id": "blocked_1"})
        mock_youtube.videos().insert.return_value = mock_req

        status_blocked = {
            "items": [
                {
                    "status": {"uploadStatus": "rejected", "rejectionReason": "audio_fingerprint"},
                    "processingDetails": {},
                    "contentDetails": {},
                }
            ]
        }
        mock_youtube.videos().list().execute.return_value = status_blocked

        with patch("movie_upload_guard.MediaFileUpload"), \
             patch("movie_upload_guard.remediate_video_for_block", return_value=Path("dummy_remediated.mp4")):

            vid_id, info = upload_video_with_remediation_retry(
                youtube=mock_youtube,
                video_path=Path("dummy.mp4"),
                thumb_path=None,
                meta={"title": "Test Title", "description": "Test Desc", "tags": []},
                is_short=True,
                max_retries=0,
                verify_timeout_sec=0.0,
                bgm_path="custom_audio.mp3",
            )

            self.assertIsNone(vid_id)
            self.assertTrue(info["blocked"])
            self.assertEqual(info["block_type"], "audio")


if __name__ == "__main__":
    unittest.main()
