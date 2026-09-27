#!/usr/bin/env python3
"""
Change the visibility of already-uploaded YouTube videos.

Uploads default to whatever PRIVACY_STATUS says, so this is only needed when
that default changes after the fact, or when a specific video should be pulled
back. Uses the same OAuth refresh token as the pipeline; it is read-only
apart from the visibility field.
"""

from __future__ import annotations

import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
VALID = ("public", "unlisted", "private")


def build_youtube():
    """Authenticate from the standard pipeline environment variables."""
    client_id = os.environ.get("CLIENT_ID", "").strip()
    client_secret = os.environ.get("CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("REFRESH_TOKEN", "").strip()
    missing = [
        name for name, value in
        (("CLIENT_ID", client_id), ("CLIENT_SECRET", client_secret), ("REFRESH_TOKEN", refresh_token))
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing credentials: {', '.join(missing)}")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials)


def set_visibility(youtube, video_ids: list[str], status: str) -> int:
    """Apply `status` to each video. Returns the number that changed."""
    changed = 0
    for video_id in video_ids:
        try:
            response = (
                youtube.videos()
                .list(part="id,status", id=video_id, fields="items(id,status privacyStatus)")
                .execute()
            )
        except HttpError as error:
            print(f"  {video_id}: lookup failed ({error.resp.status}) - skipping")
            continue

        items = response.get("items") or []
        if not items:
            print(f"  {video_id}: not found or not owned by this channel")
            continue

        current = items[0].get("status", {}).get("privacyStatus")
        if current == status:
            print(f"  {video_id}: already {status}")
            continue

        youtube.videos().update(
            part="status",
            body={"status": {"privacyStatus": status}},
            id=video_id,
        ).execute()
        print(f"  {video_id}: {current} -> {status}")
        changed += 1
    return changed


def main() -> int:
    raw_ids = os.environ.get("SET_VISIBILITY_IDS", "").strip()
    status = os.environ.get("SET_VISIBILITY_STATUS", "public").strip().lower()
    if not raw_ids:
        print("SET_VISIBILITY_IDS is empty; nothing to do.")
        return 0
    if status not in VALID:
        print(f"SET_VISIBILITY_STATUS must be one of {VALID}, got {status!r}")
        return 1

    video_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    print(f"Setting {len(video_ids)} video(s) to '{status}'...")
    changed = set_visibility(build_youtube(), video_ids, status)
    print(f"Done. {changed} video(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
