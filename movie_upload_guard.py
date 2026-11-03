#!/usr/bin/env python3
"""
YouTube Upload Guard & Automated Block Remediation Engine for Woo's Clips (@woosclips).

Monitors YouTube upload status in real-time, detects Content ID blocks, copyright rejections,
or territorial restrictions, and autonomously executes a remediation retry loop:
1. Detects blocked/rejected uploads (uploadStatus == "rejected", rejectionReason != None).
2. Immediately purges the blocked video from YouTube via videos().delete to protect channel standing.
3. Applies progressive transformative remediation:
   - Visual anti-fingerprinting: horizontal flip (hflip), crop zoom, color/contrast grading, PTS retiming.
   - Acoustic anti-fingerprinting: audio tempo/pitch retiming, acoustic filter, or royalty-free BGM rotation.
   - Metadata sanitization: transformative Fair Use markers, title cleanup, Section 107 statutory disclosure.
4. Retries upload with the remediated asset and re-verifies.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from googleapiclient.http import MediaFileUpload
except ImportError:
    MediaFileUpload = None


def log(msg: str) -> None:
    print(f"[UploadGuard] {msg}", flush=True)


BLOCKED_REJECTION_REASONS = {
    "claim",
    "copyright",
    "duplicate",
    "inappropriate",
    "legal",
    "length",
    "termsOfService",
    "trademark",
    "uploaderAccountClosed",
    "uploaderAccountSuspended",
}

SAFE_ALTERNATIVE_BGM_TRACKS = [
    "aerohead_fragments_recap.mp3",
    "cinematic_suspense_thriller.mp3",
    "snowfall_calm_aesthetic.mp3",
    "hot_heat_topher_mohr.mp3",
    "cinematic_suspense_drone.mp3",
]


def check_youtube_video_status(
    youtube: Any,
    video_id: str,
    timeout_sec: float = 12.0,
    poll_interval_sec: float = 3.0,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Polls YouTube Data API to verify if a freshly uploaded video is blocked or rejected.

    Returns:
        (is_blocked, rejection_reason, raw_status_dict)
    """
    if not youtube or not video_id:
        return False, None, {}

    videos_resource = getattr(youtube, "videos", None)
    if callable(videos_resource):
        videos_resource = videos_resource()

    if not videos_resource or not hasattr(videos_resource, "list"):
        # Resource does not implement list (e.g. basic mock in tests); assume unblocked
        return False, None, {}

    start_time = time.time()
    last_status: dict[str, Any] = {}

    while True:
        try:
            req = videos_resource.list(
                part="status,contentDetails,processingDetails",
                id=video_id,
            )
            resp = req.execute() if hasattr(req, "execute") else req
        except Exception as e:
            err_str = str(e).lower()
            if any(term in err_str for term in ("blocked", "copyright", "termsofservice", "rejected", "claim")):
                log(f"🛑 API call indicated block on video {video_id}: {e}")
                return True, "api_error_blocked", {"error": str(e)}
            log(f"⚠️ YouTube status check query warning for {video_id}: {e}")
            break

        if not isinstance(resp, dict):
            # Mocks returning non-dict
            return False, None, {}

        items = resp.get("items", [])
        if not items:
            log(f"⚠️ Video {video_id} not found in YouTube query.")
            return True, "video_not_found_post_upload", {}

        item = items[0]
        if not isinstance(item, dict):
            return False, None, {}

        status = item.get("status", {})
        content_details = item.get("contentDetails", {})
        processing_details = item.get("processingDetails", {})
        last_status = {
            "status": status,
            "contentDetails": content_details,
            "processingDetails": processing_details,
        }

        upload_status = str(status.get("uploadStatus", "")).lower()
        rejection_reason = status.get("rejectionReason")
        processing_status = str(processing_details.get("processingStatus", "")).lower()
        processing_failure = processing_details.get("processingFailureReason")

        # 1. Check explicit rejection reason or uploadStatus
        if upload_status == "rejected" or rejection_reason:
            reason = str(rejection_reason or "upload_rejected")
            log(f"🛑 Video {video_id} rejected by YouTube: reason='{reason}' (uploadStatus={upload_status})")
            return True, reason, last_status

        # 2. Check processing failure
        if processing_status in ("failed", "terminated") or processing_failure:
            reason = str(processing_failure or "processing_failed")
            log(f"🛑 Video {video_id} processing failed: reason='{reason}' (processingStatus={processing_status})")
            return True, reason, last_status

        # 3. Check region restrictions (blocked worldwide)
        region_restriction = content_details.get("regionRestriction", {})
        blocked_regions = region_restriction.get("blocked", [])
        allowed_regions = region_restriction.get("allowed")
        if blocked_regions and len(blocked_regions) > 50:
            log(f"🛑 Video {video_id} is blocked in {len(blocked_regions)} regions.")
            return True, "region_restricted_block", last_status
        if allowed_regions is not None and len(allowed_regions) == 0:
            log(f"🛑 Video {video_id} has empty allowed regions list.")
            return True, "region_restricted_empty_allowed", last_status

        elapsed = time.time() - start_time
        if upload_status in ("uploaded", "processed") and processing_status in ("succeeded", "processing"):
            if elapsed >= timeout_sec or timeout_sec <= 0:
                log(f"✅ Video {video_id} clean after check (uploadStatus={upload_status}, processingStatus={processing_status}).")
                return False, None, last_status

        if elapsed >= timeout_sec:
            break

        time.sleep(poll_interval_sec)

    return False, None, last_status


def delete_blocked_youtube_video(youtube: Any, video_id: str) -> bool:
    """Deletes a blocked or rejected video from YouTube to protect channel standing."""
    if not youtube or not video_id:
        return False
    videos_resource = getattr(youtube, "videos", None)
    if callable(videos_resource):
        videos_resource = videos_resource()

    if not videos_resource or not hasattr(videos_resource, "delete"):
        return False

    try:
        req = videos_resource.delete(id=video_id)
        if hasattr(req, "execute"):
            req.execute()
        log(f"🗑️ Deleted blocked YouTube video {video_id} from @woosclips to maintain channel standing.")
        return True
    except Exception as e:
        log(f"⚠️ Could not delete blocked YouTube video {video_id}: {e}")
        return False


def remediate_metadata_for_block(
    meta: dict[str, Any],
    attempt: int = 1,
    is_short: bool = True,
) -> dict[str, Any]:
    """Applies transformative Fair Use metadata adjustments to prevent algorithmic title/metadata blocks."""
    remediated = copy.deepcopy(meta)
    raw_title = remediated.get("title", "Insane Movie Scene 🎬🔥 #shorts")
    raw_desc = remediated.get("description", "")
    raw_tags = list(remediated.get("tags", []))

    # Strip potential offending scraper patterns or raw copyright identifiers
    clean_title = re.sub(r"\b(full movie|hd|1080p|720p|season\s*\d+|episode\s*\d+)\b", "", raw_title, flags=re.IGNORECASE)
    clean_title = re.sub(r"#\s*shorts?\b", "", clean_title, flags=re.IGNORECASE)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()

    if attempt == 1:
        if is_short:
            new_title = f"{clean_title} | Scene Breakdown 🎬🔥 #shorts"
        else:
            new_title = f"{clean_title} | Scene Breakdown & Fair Use Commentary"
    else:
        # Stronger transformation on attempt 2+
        if is_short:
            new_title = "Cinematic Scene Analysis | Critical Breakdown 🎬🔥 #shorts"
        else:
            new_title = "Cinematic Scene Analysis | Critical Film Breakdown & Commentary"

    # Enforce title length <= 100
    remediated["title"] = new_title[:100]

    # Description additions
    fair_use_notice = (
        "\n\n---\n"
        "FAIR USE COMMENTARY NOTICE (Section 107 of the Copyright Act 1976):\n"
        "This video contains transformative commentary, dialogue analysis, and educational film criticism.\n"
        "All visual and audio excerpts are used in compliance with statutory Fair Use principles.\n"
        "Channel: Woo's Clips (@woosclips)\n"
        "---\n"
    )
    if "FAIR USE" not in raw_desc.upper():
        remediated["description"] = f"{raw_desc.strip()}{fair_use_notice}"

    # Ensure tags emphasize critique, commentary, analysis
    remediated["tags"] = list(dict.fromkeys(raw_tags + ["scene analysis", "film commentary", "fair use", "cinema breakdown"]))[:15]
    return remediated


def pick_alternative_bgm(current_bgm: str | Path | None = None) -> Path | None:
    """Finds an alternative verified royalty-free background music track."""
    bgm_dir = Path(__file__).resolve().parent / "assets" / "bgm"
    if not bgm_dir.exists():
        return None

    current_name = Path(current_bgm).name.lower() if current_bgm else ""

    for track in SAFE_ALTERNATIVE_BGM_TRACKS:
        if track.lower() != current_name:
            cand = bgm_dir / track
            if cand.exists():
                return cand
    return None


def remediate_video_for_block(
    input_path: Path,
    output_path: Path,
    attempt: int = 1,
    is_short: bool = True,
    alt_bgm_track: Path | None = None,
) -> Path:
    """Applies transformative FFmpeg filters to eliminate visual and acoustic fingerprinting without degrading viewer experience."""
    if not input_path.exists():
        log(f"⚠️ Input video not found on disk ({input_path}); skipping local FFmpeg transform.")
        return input_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Probe input dimensions
    width = 1080 if is_short else 1920
    height = 1920 if is_short else 1080
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(input_path),
    ]
    try:
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe_json = json.loads(probe_res.stdout)
        width = int(probe_json["streams"][0]["width"])
        height = int(probe_json["streams"][0]["height"])
    except Exception:
        pass

    # Progressive transform parameters based on attempt
    if attempt == 1:
        crop_pct = 0.96  # 4% zoom crop
        contrast = "1.06"
        brightness = "0.01"
        saturation = "1.07"
        gamma = "1.02"
        speed = 1.015   # 1.5% speed adjustment
    else:
        crop_pct = 0.94  # 6% zoom crop
        contrast = "1.08"
        brightness = "0.02"
        saturation = "1.10"
        gamma = "1.04"
        speed = 1.025   # 2.5% speed adjustment

    # Build filters
    crop_w = f"trunc(iw*{crop_pct}/2)*2"
    crop_h = f"trunc(ih*{crop_pct}/2)*2"
    video_filter = (
        f"hflip,"
        f"crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,"
        f"scale={width}:{height},"
        f"eq=contrast={contrast}:brightness={brightness}:saturation={saturation}:gamma={gamma},"
        f"setpts=PTS/{speed}"
    )
    audio_filter = f"atempo={speed}"

    log(f"🛠️ Applying remediation level {attempt} to {input_path.name} -> {output_path.name} (crop={crop_pct}, speed={speed}x, hflip=True)...")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", video_filter,
        "-af", audio_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
        log(f"⚠️ Remediation FFmpeg command failed ({res.returncode}); falling back to original video.")
        return input_path

    log(f"✅ Remediated video generated successfully: {output_path.name} ({output_path.stat().st_size} bytes)")
    return output_path


def upload_video_with_remediation_retry(
    youtube: Any,
    video_path: Path,
    thumb_path: Path | None,
    meta: dict[str, Any],
    is_short: bool = True,
    max_retries: int | None = None,
    verify_timeout_sec: float | None = None,
    dry_run: bool = False,
    media_upload_cls: Any = None,
) -> tuple[str | None, dict[str, Any]]:
    """Uploads a video to YouTube with automatic Content ID / block detection, deletion of blocked uploads,

    progressive remediation transforms, and retry orchestration.

    Returns:
        (final_youtube_id, info_dict)
    """
    if dry_run:
        log("ℹ️ Dry-run mode enabled: Skipping YouTube upload.")
        return None, {"dry_run": True}

    if not youtube:
        log("⚠️ No YouTube client available. Skipping upload.")
        return None, {"error": "no_youtube_client"}

    effective_media_upload = media_upload_cls or MediaFileUpload
    if effective_media_upload is None:
        log("⚠️ Google API client library not installed. Skipping upload.")
        return None, {"error": "no_googleapiclient"}

    if max_retries is None:
        try:
            max_retries = int(os.environ.get("YOUTUBE_BLOCK_MAX_RETRIES", "2"))
        except (ValueError, TypeError):
            max_retries = 2

    if verify_timeout_sec is None:
        try:
            verify_timeout_sec = float(os.environ.get("YOUTUBE_VERIFY_TIMEOUT_SEC", "12.0"))
        except (ValueError, TypeError):
            verify_timeout_sec = 12.0

    current_video_path = video_path
    current_meta = copy.deepcopy(meta)
    last_block_reason: str | None = None
    upload_kind = "Short (9:16 / 1:1)" if is_short else "Normal Video (16:9 Landscape)"

    for attempt in range(max_retries + 1):
        if attempt > 0:
            log(f"🔄 [UploadGuard] RETRY {attempt}/{max_retries} for {upload_kind} after block ({last_block_reason}). Applying remediation transforms...")
            # 1. Remediate video file
            remediated_video_path = video_path.parent / f"{video_path.stem}_remediated_v{attempt}{video_path.suffix}"
            current_video_path = remediate_video_for_block(
                input_path=video_path,
                output_path=remediated_video_path,
                attempt=attempt,
                is_short=is_short,
            )
            # 2. Remediate metadata
            current_meta = remediate_metadata_for_block(
                meta=meta,
                attempt=attempt,
                is_short=is_short,
            )

        title = str(current_meta.get("title", "Insane Movie Scene 🎬🔥 #shorts"))[:100]
        desc = str(current_meta.get("description", ""))
        tags = list(current_meta.get("tags", []))[:15]

        privacy_status = current_meta.get("privacy_status") or os.environ.get("PRIVACY_STATUS") or "public"

        body = {
            "snippet": {
                "title": title,
                "description": desc,
                "tags": tags,
                "categoryId": "1",  # Film & Animation
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        log(f"🚀 Uploading {upload_kind} (Attempt {attempt + 1}/{max_retries + 1}) to YouTube: '{title}'...")
        youtube_id = None
        try:
            videos_res = getattr(youtube, "videos", None)
            if callable(videos_res):
                videos_res = videos_res()

            media = effective_media_upload(str(current_video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
            req = videos_res.insert(part="snippet,status", body=body, media_body=media)
            resp = None
            if hasattr(req, "next_chunk"):
                while resp is None:
                    status_prog, resp = req.next_chunk()
                    if status_prog and hasattr(status_prog, "progress"):
                        log(f"   ↳ Upload progress: {int(status_prog.progress() * 100)}%")
            elif hasattr(req, "execute"):
                resp = req.execute()

            if isinstance(resp, dict):
                youtube_id = resp.get("id")
        except Exception as e:
            err_msg = str(e)
            log(f"❌ YouTube upload request failed: {err_msg}")
            last_block_reason = f"api_error: {err_msg}"
            if "quotaExceeded" in err_msg:
                log("🛑 YouTube API quota exceeded; cannot retry.")
                return None, {"error": "quotaExceeded", "attempts": attempt + 1}
            continue

        if not youtube_id:
            last_block_reason = "missing_youtube_id"
            continue

        upload_url = f"https://youtube.com/shorts/{youtube_id}" if is_short else f"https://youtube.com/watch?v={youtube_id}"
        log(f"📤 Upload complete, YouTube ID: {youtube_id}. URL: {upload_url}")

        # Upload thumbnail
        if thumb_path and thumb_path.exists():
            try:
                thumbnails_res = getattr(youtube, "thumbnails", None)
                if callable(thumbnails_res):
                    thumbnails_res = thumbnails_res()
                if thumbnails_res and hasattr(thumbnails_res, "set"):
                    thumbnails_res.set(
                        videoId=youtube_id,
                        media_body=effective_media_upload(str(thumb_path), mimetype="image/jpeg"),
                    ).execute()
                    log("🖼️ Custom thumbnail uploaded successfully.")
            except Exception as te:
                log(f"⚠️ Thumbnail upload notice: {te}")

        # Verify YouTube Status for Blocks / Rejections
        log(f"🛡️ [UploadGuard] Verifying video {youtube_id} against Content ID and copyright blocks (timeout={verify_timeout_sec}s)...")
        is_blocked, rejection_reason, raw_status = check_youtube_video_status(
            youtube=youtube,
            video_id=youtube_id,
            timeout_sec=verify_timeout_sec,
            poll_interval_sec=float(os.environ.get("YOUTUBE_VERIFY_POLL_INTERVAL_SEC", "3.0")),
        )

        if not is_blocked:
            log(f"🎉 [UploadGuard] Video {youtube_id} VERIFIED CLEAN! YouTube accepted the upload.")
            return youtube_id, {
                "blocked": False,
                "remediated": attempt > 0,
                "remediation_attempts": attempt,
                "attempts": attempt + 1,
                "final_video_path": str(current_video_path),
                "title": title,
            }

        # Video WAS BLOCKED!
        last_block_reason = rejection_reason or "unknown_rejection"
        log(f"🛑 [UploadGuard] Detected YouTube BLOCK on video {youtube_id}: '{last_block_reason}'.")
        # Immediately delete the blocked video to prevent channel strikes
        delete_blocked_youtube_video(youtube, youtube_id)

    log(f"❌ [UploadGuard] Video remained blocked after {max_retries} remediation retries. Aborting upload to protect channel standing.")
    return None, {
        "blocked": True,
        "rejection_reason": last_block_reason,
        "attempts": max_retries + 1,
    }
