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
from movie_pipeline_state import assert_upload_cadence_allowed, load_history
from movie_quality import (
    CINERUSH_QUICK_TEST_DURATION,
    DEFAULT_CINERUSH_MIN_DURATION,
    DEFAULT_CINERUSH_TARGET_DURATION,
    validate_cinerush_duration_policy,
)

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
    is_short: bool = True,
) -> str | None:
    """Upload one rendered clip using Shorts or ordinary-video metadata."""
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
        if not is_short:
            title = meta.get("normal_title", f"{title} | Full Scene")[:100]
        desc = meta.get("description", f"{title}\n\n#film #movie #shorts")
        if not is_short:
            desc = meta.get("normal_description", f"{title}\n\nFull movie scene commentary.")
        tags = meta.get("tags", ["film", "movie", "shorts", "cinerush", "cinema"])
        if not is_short:
            tags = [tag for tag in tags if tag != "shorts"] + ["movie scene", "full scene"]

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

        upload_kind = "Short (9:16)" if is_short else "normal video"
        log(f"🚀 Uploading {upload_kind} to YouTube: '{title}'...")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log(f"   ↳ Upload progress: {int(status.progress() * 100)}%")

        youtube_id = resp.get("id")
        upload_url = f"https://youtube.com/shorts/{youtube_id}" if is_short else f"https://youtube.com/watch?v={youtube_id}"
        log(f"🎉 Successfully uploaded Cinerush {upload_kind}! URL: {upload_url}")

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


def _pending_short_id(history_path: Path, movie_meta: dict[str, Any] | None, upload_group_id: str) -> str | None:
    """Return an existing Short ID when a paired normal upload needs retrying."""
    if not movie_meta:
        return None
    try:
        history = load_history(history_path)
    except Exception as error:  # noqa: BLE001
        log(f"⚠️ Could not inspect upload history for retry recovery: {error}")
        return None
    entries = history.get("uploaded_movies", [])
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if not isinstance(entry, dict) or entry.get("upload_group_id") != upload_group_id:
            continue
        short_id = entry.get("youtube_id")
        normal_id = entry.get("normal_video_id")
        if isinstance(short_id, str) and short_id and not normal_id:
            return short_id
    return None


def run_cinerush_pipeline(
    query: str | None = None,
    url: str | None = None,
    video_file: str | Path | None = None,
    dry_run: bool = False,
    test_mode: bool = False,
    quick_test: bool = False,
    max_duration: float | None = None,
    output_dir: Path = Path("output"),
    profile: str = "cinerush_style",
    movie_meta: dict[str, Any] | None = None,
    gdrive_file_id: str | None = None,
    gdrive_file_name: str | None = None,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Executes the full end-to-end Cinerush pipeline with active face-tracking reframer and karaoke subtitles.
    """
    quick_test = bool(quick_test or test_mode)
    min_duration, duration_ceiling = validate_cinerush_duration_policy(
        max_duration=max_duration,
        min_duration=DEFAULT_CINERUSH_MIN_DURATION,
        quick_test=quick_test,
    )
    if quick_test:
        dry_run = True
    log("=" * 65)
    mode_label = "quick-test" if quick_test else "normal"
    log(f"🎬 STARTING CINERUSH RAW MOVIE SCENE CLIPPER (profile={profile}, mode={mode_label})")
    log(f"⏱️ Cinerush duration contract: {min_duration:.1f}s minimum, {duration_ceiling:.1f}s maximum")
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
        gdrive_file_id=gdrive_file_id,
        gdrive_file_name=gdrive_file_name,
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
        min_duration=min_duration if not quick_test else 1.0,
        max_duration=duration_ceiling,
        target_duration=DEFAULT_CINERUSH_TARGET_DURATION if not quick_test else CINERUSH_QUICK_TEST_DURATION,
    )
    clip_start = viral_data["clip_start"]
    clip_end = viral_data["clip_end"]
    if quick_test:
        clip_end = min(total_dur, clip_start + CINERUSH_QUICK_TEST_DURATION)
        log(f"⚡ Quick-test mode: capped window to {CINERUSH_QUICK_TEST_DURATION:.1f}s ({clip_start:.2f}s -> {clip_end:.2f}s) for rapid verification.")

    keep_intervals, shifted_words, trimmed_dur = build_trimmed_timeline(
        all_words=all_words,
        clip_start=clip_start,
        clip_end=clip_end,
        minimum_duration=min_duration if not quick_test else None,
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

    # Verify the encoded output before any upload attempt.
    measured_output_duration = 0.0
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(final_short_path),
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
    try:
        measured_output_duration = float(probe.stdout.strip())
    except (TypeError, ValueError):
        raise RuntimeError("Unable to measure rendered Cinerush output duration") from None
    if quick_test:
        if measured_output_duration <= 0 or measured_output_duration > duration_ceiling + 0.5:
            raise RuntimeError(f"Quick-test output duration {measured_output_duration:.2f}s exceeds the smoke-test limit")
    elif measured_output_duration < min_duration - 0.5 or measured_output_duration > duration_ceiling + 0.5:
        raise RuntimeError(
            f"Cinerush output duration {measured_output_duration:.2f}s is outside the normal "
            f"{min_duration:.1f}-{duration_ceiling:.1f}s contract"
        )

    # Step 6: Generate Thumbnail
    thumb_path = output_dir / f"thumb_{timestamp_str}.jpg"
    generate_thumbnail(final_short_path, thumb_path, timestamp=min(1.5, trimmed_dur / 2.0))

    # Step 7: YouTube Upload
    upload_group_id = f"{(movie_meta or {}).get('id', scene_meta.get('gdrive_file_id', 'scene'))}:p{(movie_meta or {}).get('part_number', 'standalone')}"
    youtube_id = None
    normal_video_id = None
    if not dry_run and not test_mode:
        history_path = Path(__file__).resolve().parent / "movie_history.json"
        assert_upload_cadence_allowed(load_history(history_path))
        if movie_meta and movie_meta.get("part_number"):
            viral_data.setdefault("tags", []).extend([
                f"part{movie_meta['part_number']}",
                str(movie_meta.get("id", "")),
                "cinema",
            ])
        youtube_id = _pending_short_id(history_path, movie_meta, upload_group_id)
        if youtube_id:
            log(f"♻️ Retrying the missing normal-video upload for group {upload_group_id}; keeping Short {youtube_id}.")
        else:
            youtube_id = upload_cinerush_to_youtube(
                video_path=final_short_path,
                thumb_path=thumb_path,
                meta=viral_data,
                dry_run=dry_run,
                is_short=True,
            )
        if youtube_id:
            normal_video_id = upload_cinerush_to_youtube(
                video_path=final_short_path,
                thumb_path=thumb_path,
                meta=viral_data,
                dry_run=dry_run,
                is_short=False,
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
        "measured_output_duration": round(measured_output_duration, 3),
        "duration_mode": "quick_test" if quick_test else "normal",
        "requested_min_duration": min_duration,
        "requested_target_duration": CINERUSH_QUICK_TEST_DURATION if quick_test else DEFAULT_CINERUSH_TARGET_DURATION,
        "requested_max_duration": duration_ceiling,
        "keep_intervals": keep_intervals,
        "words_count": len(shifted_words),
        "output_file": str(final_short_path.resolve()),
        "youtube_id": youtube_id,
        "normal_video_id": normal_video_id,
        "upload_group_id": upload_group_id,
        "source_type": (source_meta or scene_meta).get("source_type", "private_media"),
        "source_provider": (source_meta or scene_meta).get("source_provider", "google_drive"),
        "gdrive_file_id": (source_meta or scene_meta).get("gdrive_file_id", gdrive_file_id),
        "gdrive_file_name": (source_meta or scene_meta).get("gdrive_file_name", gdrive_file_name),
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
    elif dry_run:
        log("ℹ️ Dry-run mode enabled: YouTube upload is disabled.")
    log("=" * 65)

    return manifest
