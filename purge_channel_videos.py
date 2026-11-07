#!/usr/bin/env python3
"""
Purge or private all videos on @woosclips for channel revamp.

Attempts permanent deletion (videos().delete). If the OAuth token lacks
full YouTube delete scope (HTTP 403), gracefully falls back to setting
video status to 'private' (videos().update) so the public channel is 100% wiped.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]


def build_youtube():
    """Authenticate with YouTube API using GitHub secrets / env vars."""
    client_id = os.environ.get("CLIENT_ID", "").strip()
    client_secret = os.environ.get("CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("REFRESH_TOKEN", "").strip()

    missing = [
        name
        for name, value in (
            ("CLIENT_ID", client_id),
            ("CLIENT_SECRET", client_secret),
            ("REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required YouTube credentials: {', '.join(missing)}")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials)


def get_channel_upload_playlist_ids(youtube: Any) -> list[str]:
    """Fetch all video IDs in the channel's uploads playlist directly from YouTube."""
    video_ids: list[str] = []
    try:
        channels_res = youtube.channels().list(mine=True, part="contentDetails").execute()
        items = channels_res.get("items", [])
        if not items:
            print("No channel items found for authenticated account.")
            return []

        uploads_id = (
            items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        )
        if not uploads_id:
            print("No uploads playlist found for channel.")
            return []

        print(f"Fetching uploads from channel playlist {uploads_id}...")
        next_page = None
        while True:
            res = (
                youtube.playlistItems()
                .list(
                    part="contentDetails",
                    playlistId=uploads_id,
                    maxResults=50,
                    pageToken=next_page,
                )
                .execute()
            )
            for item in res.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid and vid not in video_ids:
                    video_ids.append(vid)
            next_page = res.get("nextPageToken")
            if not next_page:
                break
        print(f"Found {len(video_ids)} video(s) via channel uploads playlist.")
    except Exception as e:
        print(f"Notice: Could not list channel uploads playlist ({e}). Relying on history files.")

    return video_ids


def get_history_video_ids(paths: list[str]) -> list[str]:
    """Collect all video IDs recorded in history JSON files."""
    video_ids: list[str] = []
    seen = set()
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for list_name in ["uploaded_movies", "superseded_uploads", "uploaded_episodes"]:
                for item in data.get(list_name, []):
                    y_id = item.get("youtube_id")
                    n_id = item.get("normal_video_id") or item.get("normal_youtube_id")
                    for vid in (y_id, n_id):
                        if vid and vid not in seen:
                            seen.add(vid)
                            video_ids.append(vid)
        except Exception as e:
            print(f"Warning: Failed to parse {path}: {e}")
    print(f"Found {len(video_ids)} video(s) from history files ({paths}).")
    return video_ids


def purge_or_private_videos(youtube: Any, video_ids: list[str]) -> dict[str, int]:
    """
    Attempt to delete each video. If delete fails with 403, fall back to private.
    """
    stats = {"deleted": 0, "privated": 0, "already_gone": 0, "failed": 0}
    total = len(video_ids)
    print(f"Starting purge/privating process for {total} video(s)...")

    for idx, video_id in enumerate(video_ids, 1):
        print(f"[{idx}/{total}] Processing video {video_id}...")
        try:
            # 1. Attempt permanent delete
            youtube.videos().delete(id=video_id).execute()
            print(f"  🗑️ Video {video_id} permanently deleted.")
            stats["deleted"] += 1
            time.sleep(0.3)
            continue
        except HttpError as err:
            status = getattr(err.resp, "status", None)
            if status == 404:
                print(f"  ℹ️ Video {video_id} not found on YouTube (already deleted).")
                stats["already_gone"] += 1
                continue
            elif status in (403, 401):
                print(f"  ⚠️ Delete scope not authorized (HTTP {status}). Falling back to 'private'...")
            else:
                print(f"  ⚠️ Delete returned HTTP {status}: {err}. Attempting fallback to 'private'...")

        # 2. Fallback: Update privacy status to private
        try:
            youtube.videos().update(
                part="status",
                body={"id": video_id, "status": {"privacyStatus": "private"}},
            ).execute()
            print(f"  🔒 Video {video_id} set to private.")
            stats["privated"] += 1
        except HttpError as update_err:
            up_status = getattr(update_err.resp, "status", None)
            if up_status == 404:
                print(f"  ℹ️ Video {video_id} not found on YouTube (already deleted).")
                stats["already_gone"] += 1
            else:
                print(f"  ❌ Failed to set video {video_id} to private: {update_err}")
                stats["failed"] += 1
        except Exception as e:
            print(f"  ❌ Unexpected error privating {video_id}: {e}")
            stats["failed"] += 1

        time.sleep(0.3)

    return stats


def main() -> int:
    history_files = [
        "movie_history.json",
        "movie_history.backup.json",
        "history.json",
    ]

    youtube = build_youtube()

    # Discover videos
    playlist_ids = get_channel_upload_playlist_ids(youtube)
    history_ids = get_history_video_ids(history_files)

    # Combine unique IDs preserving discovery order
    all_ids: list[str] = []
    seen = set()
    for vid in playlist_ids + history_ids:
        if vid and vid not in seen:
            seen.add(vid)
            all_ids.append(vid)

    if not all_ids:
        print("No videos found to remove. Channel is already empty.")
        return 0

    print(f"\n=======================================================")
    print(f"TOTAL VIDEOS TARGETED FOR REMOVAL: {len(all_ids)}")
    print(f"=======================================================\n")

    stats = purge_or_private_videos(youtube, all_ids)

    print(f"\n=======================================================")
    print(f"PURGE RESULTS:")
    print(f"  - Deleted permanently: {stats['deleted']}")
    print(f"  - Set to private:     {stats['privated']}")
    print(f"  - Already gone (404): {stats['already_gone']}")
    print(f"  - Failed:             {stats['failed']}")
    print(f"=======================================================\n")

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
