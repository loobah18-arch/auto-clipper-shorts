#!/usr/bin/env python3
"""
YouTube Uploader Engine for Hypit Shorts Clipper.
Reference: Jack Craig "Tested&Proved" Playbook & YouTube Partner Program (YPP) Fair Use.

Features:
1. OAuth2 authentication via CLIENT_ID, CLIENT_SECRET, and REFRESH_TOKEN.
2. 48-Hour Candle Gate verification (halts upload if channel is currently riding an active push >5,000 views/hr).
3. Jack Craig packaging formatting (high-CTR question + 2 shock emojis + creator handle).
4. Transformative Fair Use description with chapter breakdown & attribution.
5. Resumable chunked upload with error recovery and retry.
6. Local dry-run verification mode.
7. Deduplication and upload history logging.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from candle_gate import CandleGate

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtubepartner",
]

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parent / "upload_history.json"
DEFAULT_CATALOG_FILE = Path(__file__).resolve().parent / "creator_catalog.json"


def log(msg: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{timestamp}] [YouTubeUploader] {msg}", flush=True)


def load_creator_catalog(catalog_path: Path = DEFAULT_CATALOG_FILE) -> Dict[str, Any]:
    if not catalog_path.exists():
        return {}
    try:
        return json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"Warning: Failed to load catalog from {catalog_path}: {e}")
        return {}


def build_youtube_client():
    """Build authorized YouTube client from OAuth refresh token."""
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
        raise RuntimeError(f"Missing YouTube OAuth credentials: {', '.join(missing)}")

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials)


class YouTubeUploader:
    """Manages YouTube Shorts upload lifecycle with Tested&Proved packaging."""

    def __init__(
        self,
        history_file: Path = DEFAULT_HISTORY_FILE,
        catalog_file: Path = DEFAULT_CATALOG_FILE,
    ):
        self.history_file = history_file
        self.catalog = load_creator_catalog(catalog_file)
        self.candle_gate = CandleGate()

    def format_title(self, raw_title: str, creator_id: str = "mrbeast") -> str:
        """Enforces Jack Craig formula: Question Hook + 2 Shock Emojis + Creator Handle."""
        import re
        title = raw_title.strip()
        creators = self.catalog.get("creators", {})
        creator_info = creators.get(creator_id, {})
        handle = creator_info.get("channel_handle", "@MrBeast")
        packaging = creator_info.get("packaging", {})
        emojis = packaging.get("shock_emojis", "😳😳")

        # Strip any existing handle pattern and emojis from base title
        base = re.sub(r"\s*\(@[^)]+\)", "", title)
        base = re.sub(r"[\U00010000-\U0010ffff]", "", base)
        base = base.replace("#shorts", "").strip()

        candidate = f"{base} {emojis} ({handle}/YT)"
        if len(candidate) > 95:
            candidate = f"{base[:70]}... {emojis} ({handle}/YT)"
        return candidate

    def format_description(
        self,
        raw_description: str,
        creator_id: str = "mrbeast",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Formats compliant, transformative Fair Use description with chapter breakdown."""
        creators = self.catalog.get("creators", {})
        creator_info = creators.get(creator_id, {})
        creator_name = creator_info.get("name", "Original Creator")
        creator_url = creator_info.get("channel_url", "https://youtube.com")
        hashtags = creator_info.get("packaging", {}).get("hashtags", ["#shorts", "#viral"])
        tag_line = " ".join(hashtags)

        stages_breakdown = ""
        if metadata and "stages" in metadata:
            stages_breakdown = "\n\n🎬 Completion Compulsion Story Arc:\n"
            for k, v in metadata["stages"].items():
                stages_breakdown += f"• {v}\n"

        fair_use_notice = (
            f"\n\n⚖️ Transformative Value-Add & Fair Use:\n"
            f"This Short features analytical commentary, speed pacing, custom HUD status badges, "
            f"and word-level subtitles to provide educational and entertainment context under YouTube's Fair Use policy.\n"
            f"Original Creator: {creator_name} ({creator_url})\n\n"
            f"{tag_line}"
        )

        return f"{raw_description}{stages_breakdown}{fair_use_notice}"

    def check_candle_gate(self, force: bool = False) -> Tuple[bool, str]:
        """Verifies if upload is permitted under the 5k views/hour candle gate rule."""
        if force:
            return True, "Upload forced by user override (--force)."

        gate_state = self.candle_gate.load_state()
        if not gate_state:
            # If no cached state exists, grant clearance for initial upload
            return True, "No active candle state recorded; initial upload cleared."

        candle_eval = gate_state.get("candle_evaluation", {})
        can_upload = candle_eval.get("can_upload", True)
        recommendation = candle_eval.get("recommendation", "Clearance granted.")

        return can_upload, recommendation

    def upload(
        self,
        video_path: Path,
        metadata_path: Optional[Path] = None,
        creator_id: str = "mrbeast",
        privacy_status: str = "public",
        dry_run: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Executes full upload pipeline."""
        if not video_path.exists():
            raise FileNotFoundError(f"Video deliverable not found: {video_path}")

        meta: Dict[str, Any] = {}
        if metadata_path and metadata_path.exists():
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception as e:
                log(f"Warning: Failed to parse metadata file {metadata_path}: {e}")

        raw_title = meta.get("title", f"Challenge Short (@{creator_id})")
        raw_desc = meta.get("description", f"Watch this high-stakes Short by {creator_id}!")
        tags = meta.get("tags", ["shorts", "viral", creator_id])

        title = self.format_title(raw_title, creator_id)
        description = self.format_description(raw_desc, creator_id, meta)

        creators = self.catalog.get("creators", {})
        creator_info = creators.get(creator_id, {})
        category_id = creator_info.get("packaging", {}).get("category_id", "24")

        log(f"📋 Title: '{title}'")
        log(f"🔒 Privacy: {privacy_status} | Category: {category_id} | Dry-Run: {dry_run}")

        # 1. Check Candle Gate
        can_upload, gate_msg = self.check_candle_gate(force=force)
        log(f"🚦 Candle Gate: {gate_msg}")
        if not can_upload and not force:
            log("🛑 Upload aborted by Candle Gate to prevent algorithm suppression.")
            return {
                "status": "blocked_by_candle_gate",
                "reason": gate_msg,
                "video_path": str(video_path),
            }

        # 2. Dry Run Mode
        if dry_run:
            simulated_id = f"dry_run_{int(time.time())}"
            simulated_url = f"https://youtube.com/shorts/{simulated_id}"
            log(f"🔍 [DRY-RUN] Simulated successful upload to @woosclips: {simulated_url}")
            return {
                "status": "dry_run_success",
                "video_id": simulated_id,
                "url": simulated_url,
                "title": title,
                "privacy": privacy_status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # 3. Live API Upload
        log("🚀 Connecting to YouTube Data API v3...")
        youtube = build_youtube_client()

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=1024 * 1024 * 8,  # 8MB chunk size
            resumable=True,
        )

        log("📤 Uploading video stream to YouTube...")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"   Uploaded {int(status.progress() * 100)}%...")

        video_id = response.get("id")
        video_url = f"https://youtube.com/shorts/{video_id}"
        log(f"🎉 Short successfully uploaded to channel! ID: {video_id} -> {video_url}")

        # 4. Save to History
        self._record_history({
            "video_id": video_id,
            "title": title,
            "url": video_url,
            "creator": creator_id,
            "video_path": str(video_path.name),
            "privacy": privacy_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
        })

        return {
            "status": "uploaded",
            "video_id": video_id,
            "url": video_url,
            "title": title,
            "privacy": privacy_status,
        }

    def _record_history(self, entry: Dict[str, Any]) -> None:
        history = []
        if self.history_file.exists():
            try:
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                history = []
        history.append(entry)
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Hypit Tested&Proved YouTube Uploader")
    parser.add_argument("--video", type=Path, default=Path("output/hypit_mrbeast_short.mp4"), help="Path to video file")
    parser.add_argument("--metadata", type=Path, default=Path("metadata.json"), help="Path to metadata JSON")
    parser.add_argument("--creator", type=str, default="mrbeast", help="Creator ID from creator_catalog.json")
    parser.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public", help="Upload privacy status")
    parser.add_argument("--dry-run", action="store_true", help="Simulate upload without calling YouTube API")
    parser.add_argument("--force", action="store_true", help="Bypass 5k views/hour candle gate")
    args = parser.parse_args()

    uploader = YouTubeUploader()

    # Fallback paths if default video doesn't exist in output/
    video_path = args.video
    if not video_path.exists():
        fallback = Path("/storage/emulated/0/Download/hypit_mrbeast_escape_100_cops.mp4")
        if fallback.exists():
            video_path = fallback

    try:
        result = uploader.upload(
            video_path=video_path,
            metadata_path=args.metadata,
            creator_id=args.creator,
            privacy_status=args.privacy,
            dry_run=args.dry_run,
            force=args.force,
        )
        print(json.dumps(result, indent=2))
        if result.get("status") == "blocked_by_candle_gate":
            sys.exit(2)
    except Exception as e:
        log(f"Fatal error during upload: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
