#!/usr/bin/env python3
"""
Creator Source Fetcher for Isla Value-Add Clipper.
Downloads long-form creator content via yt-dlp (with YOUTUBE_COOKIES support)
or Google Drive fallback, extracting high-res video, audio, and subtitles.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def log(msg: str) -> None:
    print(f"[CreatorFetcher] {msg}", flush=True)


@dataclass
class CreatorVideoMetadata:
    video_id: str
    title: str
    channel_name: str
    video_path: Path
    audio_path: Optional[Path]
    duration_sec: float
    width: int
    height: int
    source_url: str
    transcript_text: str = ""
    subtitles_path: Optional[Path] = None


def resolve_media_info(video_path: Path) -> dict[str, Any]:
    """Probes video dimensions, duration, and audio presence via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=width,height,codec_type",
        "-of", "json", str(video_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Unable to probe video: {res.stderr[:300]}")
    data = json.loads(res.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float(fmt.get("duration") or 0.0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _prepare_cookies_arg() -> list[str]:
    """Decodes base64 YOUTUBE_COOKIES and creates a temporary cookie file."""
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        return []

    # Check if base64 encoded
    cookies_text = raw
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
        if "# Netscape HTTP Cookie File" in decoded or "google.com" in decoded or "youtube.com" in decoded:
            cookies_text = decoded
    except Exception:
        pass

    cookie_file = Path(tempfile.gettempdir()) / "yt_cookies.txt"
    cookie_file.write_text(cookies_text, encoding="utf-8")
    return ["--cookies", str(cookie_file)]


def fetch_from_youtube(
    url: str,
    output_dir: Path = Path("output"),
    max_duration: float = 3600.0,
) -> CreatorVideoMetadata:
    """Downloads a YouTube video with metadata and subtitles using yt-dlp."""
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"Fetching video metadata for: {url}")

    cookies_args = _prepare_cookies_arg()

    # 1. Extract metadata via yt-dlp JSON dump (try with cookies, fallback without cookies)
    def run_dump_json(args: list[str]) -> Optional[dict]:
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-playlist",
            "--extractor-args", "youtube:player_client=android,web",
            *args,
            url,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return json.loads(res.stdout)
            except Exception:
                pass
        return None

    meta = run_dump_json(cookies_args) if cookies_args else None
    active_cookie_args = cookies_args

    if not meta:
        log("Notice: Metadata lookup without cookies fallback...")
        meta = run_dump_json([])
        active_cookie_args = []

    if not meta:
        # Final retry with minimal flags
        cmd_fallback = ["yt-dlp", "--dump-json", "--no-playlist", url]
        res_fb = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res_fb.returncode == 0:
            meta = json.loads(res_fb.stdout)
        else:
            raise RuntimeError(f"yt-dlp metadata extraction failed: {res_fb.stderr[:400]}")

    video_id = meta.get("id", "video")
    title = meta.get("title", "Untitled Video")
    channel_name = meta.get("uploader") or meta.get("channel") or "Creator"
    duration = float(meta.get("duration") or 0.0)

    if duration > max_duration:
        log(f"⚠️ Video duration ({duration}s) exceeds limit ({max_duration}s); proceeding with care.")

    output_template = output_dir / f"source_{video_id}.%(ext)s"
    final_video_path = output_dir / f"source_{video_id}.mp4"

    # 2. Download high-quality 1080p/720p MP4
    if not final_video_path.exists() or final_video_path.stat().st_size < 1_000_000:
        log(f"📥 Downloading video {video_id} ('{title}') to {final_video_path.name}...")
        dl_cmd = [
            "yt-dlp",
            "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(output_template),
            "--no-playlist",
            "--extractor-args", "youtube:player_client=android,web",
            *active_cookie_args,
            url,
        ]
        dl_res = subprocess.run(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if dl_res.returncode != 0:
            log(f"Notice: Download with cookies failed; retrying without cookies...")
            dl_cmd_no_cookies = [
                "yt-dlp",
                "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", str(output_template),
                "--no-playlist",
                url,
            ]
            dl_res = subprocess.run(dl_cmd_no_cookies, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if dl_res.returncode != 0:
                raise RuntimeError(f"yt-dlp download failed: {dl_res.stderr[:400]}")

    # Check for actual file output
    candidates = list(output_dir.glob(f"source_{video_id}.*"))
    mp4_candidates = [c for c in candidates if c.suffix.lower() == ".mp4"]
    if mp4_candidates:
        final_video_path = mp4_candidates[0]
    elif candidates:
        final_video_path = candidates[0]
    else:
        raise RuntimeError(f"Downloaded source file not found in {output_dir}")

    info = resolve_media_info(final_video_path)

    # 3. Extract audio WAV for transcription
    audio_path = output_dir / f"audio_{video_id}.wav"
    if not audio_path.exists():
        log(f"🎵 Extracting audio to {audio_path.name}...")
        cmd_audio = [
            "ffmpeg", "-y", "-i", str(final_video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path),
        ]
        subprocess.run(cmd_audio, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    return CreatorVideoMetadata(
        video_id=video_id,
        title=title,
        channel_name=channel_name,
        video_path=final_video_path,
        audio_path=audio_path if audio_path.exists() else None,
        duration_sec=info["duration"],
        width=info["width"],
        height=info["height"],
        source_url=url,
    )
