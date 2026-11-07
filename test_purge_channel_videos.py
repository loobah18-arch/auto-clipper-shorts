#!/usr/bin/env python3
"""
Unit tests for purge_channel_videos.py.
Verifies:
1. Credential validation and YouTube client construction.
2. Channel uploads playlist extraction with pagination.
3. History file video ID extraction (Short and normal video IDs, deduplication).
4. Permanent deletion when authorized.
5. Graceful fallback to private when deletion scope is missing (HTTP 403).
6. Handling of 404 (already deleted/not found) without error.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError
from purge_channel_videos import (
    build_youtube,
    get_channel_upload_playlist_ids,
    get_history_video_ids,
    purge_or_private_videos,
)


class DummyResponse:
    def __init__(self, status: int, reason: str = ""):
        self.status = status
        self.reason = reason


class TestPurgeChannelVideos(unittest.TestCase):
    def test_build_youtube_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                build_youtube()
            self.assertIn("Missing required YouTube credentials", str(ctx.exception))

    def test_get_history_video_ids(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            json.dump(
                {
                    "uploaded_movies": [
                        {
                            "youtube_id": "vid_short_1",
                            "normal_video_id": "vid_normal_1",
                        },
                        {
                            "youtube_id": "vid_short_2",
                            "normal_youtube_id": "vid_normal_2",
                        },
                    ],
                    "superseded_uploads": [
                        {"youtube_id": "vid_short_1"},  # duplicate
                        {"youtube_id": "vid_short_3"},
                    ],
                },
                f,
            )
            temp_path = f.name

        try:
            ids = get_history_video_ids([temp_path])
            self.assertEqual(
                ids,
                ["vid_short_1", "vid_normal_1", "vid_short_2", "vid_normal_2", "vid_short_3"],
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_get_channel_upload_playlist_ids_pagination(self):
        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_channels.list.return_value.execute.return_value = {
            "items": [
                {"contentDetails": {"relatedPlaylists": {"uploads": "UU_test_channel"}}}
            ]
        }
        mock_youtube.channels.return_value = mock_channels

        mock_playlist_items = MagicMock()
        # Page 1: returns 2 items and nextPageToken
        # Page 2: returns 1 item and no nextPageToken
        mock_playlist_items.list.return_value.execute.side_effect = [
            {
                "items": [
                    {"contentDetails": {"videoId": "yt_1"}},
                    {"contentDetails": {"videoId": "yt_2"}},
                ],
                "nextPageToken": "token_page_2",
            },
            {
                "items": [
                    {"contentDetails": {"videoId": "yt_3"}},
                ],
            },
        ]
        mock_youtube.playlistItems.return_value = mock_playlist_items

        result = get_channel_upload_playlist_ids(mock_youtube)
        self.assertEqual(result, ["yt_1", "yt_2", "yt_3"])

    def test_purge_or_private_success_delete(self):
        mock_youtube = MagicMock()
        mock_delete = MagicMock()
        mock_delete.execute.return_value = {}
        mock_youtube.videos.return_value.delete.return_value = mock_delete

        stats = purge_or_private_videos(mock_youtube, ["vid_1", "vid_2"])
        self.assertEqual(stats["deleted"], 2)
        self.assertEqual(stats["privated"], 0)
        self.assertEqual(stats["already_gone"], 0)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(mock_youtube.videos.return_value.delete.call_count, 2)

    def test_purge_fallback_to_private_on_403(self):
        mock_youtube = MagicMock()
        # Delete raises 403 Forbidden
        err_403 = HttpError(resp=DummyResponse(403, "Forbidden"), content=b"Insufficient Permission")
        mock_delete = MagicMock()
        mock_delete.execute.side_effect = err_403
        mock_youtube.videos.return_value.delete.return_value = mock_delete

        # Update succeeds
        mock_update = MagicMock()
        mock_update.execute.return_value = {}
        mock_youtube.videos.return_value.update.return_value = mock_update

        stats = purge_or_private_videos(mock_youtube, ["vid_forbidden"])
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["privated"], 1)
        self.assertEqual(stats["already_gone"], 0)
        self.assertEqual(stats["failed"], 0)

        # Check update arguments
        mock_youtube.videos.return_value.update.assert_called_once_with(
            part="status",
            body={"id": "vid_forbidden", "status": {"privacyStatus": "private"}},
        )

    def test_purge_handles_404_already_gone(self):
        mock_youtube = MagicMock()
        err_404 = HttpError(resp=DummyResponse(404, "Not Found"), content=b"Video not found")
        mock_delete = MagicMock()
        mock_delete.execute.side_effect = err_404
        mock_youtube.videos.return_value.delete.return_value = mock_delete

        stats = purge_or_private_videos(mock_youtube, ["vid_nonexistent"])
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["privated"], 0)
        self.assertEqual(stats["already_gone"], 1)
        self.assertEqual(stats["failed"], 0)


if __name__ == "__main__":
    unittest.main()
