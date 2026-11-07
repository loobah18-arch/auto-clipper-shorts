#!/usr/bin/env python3
"""
Isla YouTube Upload Guard & Metadata Formatter.
Enforces:
1. Standalone titles with open-loop hooks (NO 'Part 1/6' labels).
2. Proper creator credit and transformative Fair Use descriptions.
3. YouTube Data API v3 upload via existing OAuth credentials.
4. Safe history state tracking.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from isla_story_engine import LoopedArcScript

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def log(msg: str) -> None:
    print(f"[IslaUploadGuard] {msg}", flush=True)


def build_youtube_client():
    """Authenticate with YouTube API from environment variables."""
    client_id = os.environ.get("CLIENT_ID", "").strip()
    client_secret = os.environ.get("CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("REFRESH_TOKEN", "").strip()

    missing = [
        name for name, val in (
            ("CLIENT_ID", client_id),
            ("CLIENT_SECRET", client_secret),
            ("REFRESH_TOKEN", refresh_token),
        ) if not val
    ]
    if missing:
        raise RuntimeError(f"Missing YouTube credentials: {', '.join(missing)}")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials)


def format_isla_title(script: LoopedArcScript, channel_name: str) -> str:
    """Formats high-CTR title with open loop hook and zero episodic numbering."""
    base = script.title.replace("#shorts", "").strip()
    # Ensure no 'Part X' or 'Part X/Y'
    import re
    cleaned = re.sub(r"Part\s*\d+(\s*/\s*\d+)?", "", base, flags=re.IGNORECASE).strip()
    # Add hashtags
    candidate = f"{cleaned} 🧪🔥 #shorts #science"
    if len(candidate) > 95:
        candidate = f"{cleaned[:75]}... #shorts"
    return candidate


def format_isla_description(
    script: LoopedArcScript,
    channel_name: str,
    source_url: str,
) -> str:
    """Formats compliant, transformative description with creator credit."""
    return f"""{script.concept}

Curiosity breakdown inspired by {channel_name}.
Original Creator: {channel_name} ({source_url})

Transformative Value-Add:
This short video provides educational pacing, kinetic subtitles, and an analytical breakdown of the experiment's critical turning point under YouTube's Fair Use policy.

#shorts #science #experiment #{channel_name.lower().replace(' ', '')}
"""


def upload_isla_short(
    video_path: Path,
    script: LoopedArcScript,
    channel_name: str,
    source_url: str,
    privacy_status: str = "public",
    dry_run: bool = False,
    history_path: Path = Path("isla_history.json"),
) -> dict[str, Any]:
    """Uploads rendered Short to YouTube and records state."""
    title = format_isla_title(script, channel_name)
    description = format_isla_description(script, channel_name, source_url)

    if dry_run:
        log(f"🔍 [DRY-RUN] Would upload Short: '{title}' (privacy={privacy_status})")
        return {
            "status": "dry_run",
            "youtube_id": f"dry_run_{int(time.time())}",
            "title": title,
            "url": f"https://youtube.com/shorts/dry_run_preview",
        }

    log(f"🚀 Uploading Short to @woosclips: '{title}'...")
    youtube = build_youtube_client()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": script.keywords + ["shorts", "science", channel_name.lower()],
            "categoryId": "28",  # Science & Technology
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()

    video_id = response.get("id")
    video_url = f"https://youtube.com/shorts/{video_id}"
    log(f"🎉 Short uploaded successfully! ID: {video_id} -> {video_url}")

    # Record in history
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            history = []

    history.append({
        "youtube_id": video_id,
        "title": title,
        "creator": channel_name,
        "source_url": source_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "video_path": str(video_path.name),
        "status": "uploaded",
    })
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    return {
        "status": "uploaded",
        "youtube_id": video_id,
        "title": title,
        "url": video_url,
    }
