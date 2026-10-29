#!/usr/bin/env python3
"""
Cinerush Movie Scene Clipper Pipeline Orchestrator
Coordinates:
1. Sourcing HD movie scenes (yt-dlp)
2. Transcribing 100% original movie dialogue with faster-whisper
3. Groq LLM selection of continuous 90-175s viral window and dead-air trimming
4. Shot-locked face-tracking 9:16 reframer (MediaPipe / YuNet)
5. Burn-in word-highlighted yellow/white karaoke subtitles
6. Automated YouTube Shorts upload
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cinerush_downloader import download_movie_scene
from cinerush_transcriber import transcribe_scene
from cinerush_viral_selector import select_viral_segment, build_trimmed_timeline
from cinerush_face_reframer import render_shot_locked_cinerush_video
from cinerush_subtitles import generate_cinerush_karaoke_ass, burn_karaoke_subtitles

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    Credentials = None
    build = None
    MediaFileUpload = None


def log(msg: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [Cinerush] {msg}", flush=True)


def generate_thumbnail(video_path: Path, output_thumb: Path, timestamp: float = 1.0) -> Path:
    """Extracts a crisp thumbnail frame from the vertical video."""
    output_thumb.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{timestamp:.2f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(output_thumb),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return output_thumb


def upload_cinerush_to_youtube(
    video_path: Path,
    thumb_path: Path,
    meta: dict[str, Any],
    dry_run: bool = False,
) -> str | None:
    """Uploads the Cinerush Short to YouTube channel."""
    if dry_run:
        log("ℹ️ Dry-run mode enabled: Skipping YouTube upload.")
        return None

    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    refresh_token = os.environ.get("REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        log("⚠️ YouTube OAuth credentials not found in environment (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN). Skipping upload.")
        return None

    if not Credentials or not build:
        log("⚠️ Google API client library not installed. Skipping upload.")
        return None

    try:
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )
        youtube = build("youtube", "v3", credentials=creds)

        title = meta.get("title", "Insane Movie Scene 🎬🔥 #film #movie #shorts")[:100]
        desc = meta.get("description", f"{title}\n\n#film #movie #shorts")
        tags = meta.get("tags", ["film", "movie", "shorts", "cinerush", "cinema"])

        body = {
            "snippet": {
                "title": title,
                "description": desc,
                "tags": tags[:15],
                "categoryId": "1",  # Film & Animation
            },
            "status": {
                "privacyStatus": os.environ.get("PRIVACY_STATUS") or "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        log(f"🚀 Uploading Short (9:16) to YouTube: '{title}'...")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log(f"   ↳ Upload progress: {int(status.progress() * 100)}%")

        youtube_id = resp.get("id")
        log(f"🎉 Successfully uploaded Cinerush Short! URL: https://youtube.com/shorts/{youtube_id}")

        # Upload thumbnail if available
        if thumb_path and thumb_path.exists() and youtube_id:
            try:
                youtube.thumbnails().set(videoId=youtube_id, media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg")).execute()
                log("🖼️ Custom thumbnail set successfully.")
            except Exception as te:
                log(f"⚠️ Thumbnail upload notice: {te}")

        return youtube_id
    except Exception as e:
        log(f"❌ YouTube upload failed: {e}")
        return None


def run_cinerush_pipeline(
    query: str | None = None,
    url: str | None = None,
    video_file: str | Path | None = None,
    dry_run: bool = False,
    test_mode: bool = False,
    output_dir: Path = Path("output"),
    profile: str = "cinerush_style",
    movie_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Executes the full end-to-end Cinerush pipeline with active face-tracking reframer and karaoke subtitles.
    """
    log("=" * 65)
    log(f"🎬 STARTING CINERUSH RAW MOVIE SCENE CLIPPER (profile={profile})")
    log("=" * 65)

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Step 1: Download or resolve continuous single-scene movie footage
    source_video, scene_meta = download_movie_scene(
        query=query,
        url=url,
        video_path=video_file,
        output_dir=output_dir,
        test_mode=test_mode,
    )
    total_dur = scene_meta.get("duration", 0.0)
    scene_title = (movie_meta or {}).get("title") or scene_meta.get("title", "Movie Scene")
    log(f"📀 Sourced Scene: '{scene_title}' ({total_dur:.1f}s)")

    # Step 2: Transcribe original movie dialogue
    segments = transcribe_scene(source_video, output_dir=output_dir)
    all_words = []
    for s in segments:
        all_words.extend(s.get("words", []))

    # Step 3: LLM Viral Segment Selection & Dead-Air Trimming
    viral_data = select_viral_segment(
        segments=segments,
        total_video_duration=total_dur,
        movie_title=scene_title,
    )
    clip_start = viral_data["clip_start"]
    clip_end = viral_data["clip_end"]
    if test_mode:
        clip_end = min(total_dur, clip_start + 12.0)
        log(f"⚡ Test mode: capped window to 12.0s ({clip_start:.2f}s -> {clip_end:.2f}s) for rapid local verification.")

    keep_intervals, shifted_words, trimmed_dur = build_trimmed_timeline(
        all_words=all_words,
        clip_start=clip_start,
        clip_end=clip_end,
    )

    # Step 4: Active Face-Tracking Reframer (1:1 square for cinerush_style, 9:16 for shorts_9x16)
    reframed_path = output_dir / f"cinerush_reframed_{timestamp_str}.mp4"
    render_shot_locked_cinerush_video(
        source_video=source_video,
        keep_intervals=keep_intervals,
        output_path=reframed_path,
        profile=profile,
    )

    # Step 5: Dynamic Karaoke Word-Highlighted Subtitles
    ass_path = output_dir / f"cinerush_subtitles_{timestamp_str}.ass"
    generate_cinerush_karaoke_ass(shifted_words, ass_path, profile=profile)

    final_short_path = output_dir / f"cinerush_short_{timestamp_str}.mp4"
    burn_karaoke_subtitles(reframed_path, ass_path, final_short_path)

    # Step 6: Generate Thumbnail
    thumb_path = output_dir / f"thumb_{timestamp_str}.jpg"
    generate_thumbnail(final_short_path, thumb_path, timestamp=min(1.5, trimmed_dur / 2.0))

    # Step 7: YouTube Upload
    youtube_id = None
    if not dry_run and not test_mode:
        if movie_meta and movie_meta.get("part_number"):
            viral_data.setdefault("tags", []).extend([
                f"part{movie_meta['part_number']}",
                str(movie_meta.get("id", "")),
                "cinema",
            ])
        youtube_id = upload_cinerush_to_youtube(
            video_path=final_short_path,
            thumb_path=thumb_path,
            meta=viral_data,
            dry_run=dry_run,
        )

    # Save manifest
    manifest = {
        "title": viral_data["title"],
        "scene_title": scene_title,
        "movie_id": (movie_meta or {}).get("id"),
        "part_number": (movie_meta or {}).get("part_number"),
        "total_parts": (movie_meta or {}).get("total_parts"),
        "clip_start": clip_start,
        "clip_end": clip_end,
        "trimmed_duration": trimmed_dur,
        "keep_intervals": keep_intervals,
        "words_count": len(shifted_words),
        "output_file": str(final_short_path.resolve()),
        "youtube_id": youtube_id,
        "profile": profile,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = output_dir / f"render_manifest_cinerush_{timestamp_str}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Clean intermediate raw reframed video to save space
    try:
        reframed_path.unlink()
    except Exception:
        pass

    log("=" * 65)
    log(f"🎬 CINERUSH PIPELINE COMPLETE! Rendered: {final_short_path.name}")
    if youtube_id:
        log(f"🔗 YouTube Short Live: https://youtube.com/shorts/{youtube_id}")
    log("=" * 65)

    return manifest
