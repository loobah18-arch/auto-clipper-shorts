#!/usr/bin/env python3
"""
Creator Source Fetcher for Isla Value-Add Clipper.
Uses two-phase fetching via yt-dlp:
1. Metadata & Subtitles extraction (fast, lightweight, no media download)
2. Precision Section Download (--download-sections) for only the targeted 45-55s clip.
Handles YOUTUBE_COOKIES with base64 decoding, CRLF normalization, and Netscape validation.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_COOKIES_FILE: Optional[str] = None


def log(msg: str) -> None:
    print(f"[CreatorFetcher] {msg}", flush=True)


@dataclass
class CreatorVideoMetadata:
    video_id: str
    title: str
    channel_name: str
    duration_sec: float
    source_url: str
    transcript_text: str = ""
    subtitles_path: Optional[Path] = None


def _format_time_sec(sec: float) -> str:
    """Formats seconds into HH:MM:SS for yt-dlp --download-sections."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _clean_vtt_text(vtt_path: Path) -> str:
    """Cleans a .vtt subtitle file into plain text without timestamps or duplicates."""
    if not vtt_path.exists():
        return ""
    lines = vtt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    seen = set()
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line or "-->" in line or line.startswith("WEBVTT") or line.isdigit():
            continue
        clean_line = re.sub(r"<[^>]+>", "", line).strip()
        if clean_line and clean_line not in seen:
            seen.add(clean_line)
            cleaned.append(clean_line)
    return " ".join(cleaned)


def setup_cookies_file() -> Optional[str]:
    """
    Reads the YOUTUBE_COOKIES env var (base64-encoded Netscape cookies.txt content)
    and writes it to a temporary file for yt-dlp.
    """
    global _COOKIES_FILE
    if _COOKIES_FILE and os.path.exists(_COOKIES_FILE):
        return _COOKIES_FILE

    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        log("Notice: YOUTUBE_COOKIES env var is not set; proceeding without cookies.")
        return None

    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")

        first_line = decoded.split("\n", 1)[0].strip()
        valid_headers = ["# Netscape HTTP Cookie File", "# HTTP Cookie File"]
        if first_line not in valid_headers:
            decoded = "# Netscape HTTP Cookie File\n" + decoded

        cookie_lines = [
            l for l in decoded.strip().split("\n")
            if l.strip() and not l.startswith("#") and "\t" in l
        ]
        if not cookie_lines:
            log("⚠️ YOUTUBE_COOKIES decoded but contains no valid tab-separated cookie entries.")
            return None

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix="_yt_cookies.txt", delete=False, encoding="utf-8", newline="\n"
        )
        tmp.write(decoded)
        tmp.flush()
        tmp.close()
        _COOKIES_FILE = tmp.name
        os.chmod(_COOKIES_FILE, 0o600)
        log(f"🍪 Authenticated YouTube cookies loaded: {len(cookie_lines)} entries -> {_COOKIES_FILE}")
        return _COOKIES_FILE
    except Exception as e:
        log(f"⚠️ Failed to decode YOUTUBE_COOKIES: {e}")
        return None


def get_ytdlp_cookie_args() -> list[str]:
    """Returns ['--cookies', '<path>'] if cookies are available, else []."""
    cf = setup_cookies_file()
    if cf:
        return ["--cookies", cf]
    return []


def fetch_creator_metadata(
    url: str,
    output_dir: Path = Path("output"),
) -> CreatorVideoMetadata:
    """
    Phase 1: Extracts video metadata and English subtitles without downloading video.
    Runs in <2 seconds.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"Extracting metadata and subtitles for: {url}")

    cookie_args = get_ytdlp_cookie_args()
    sub_output_template = str(output_dir / "sub_%(id)s")

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--dump-json",
        "--write-auto-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "-o", sub_output_template,
        *cookie_args,
        "--extractor-args", "youtube:player_client=android,ios,web",
        "--no-playlist",
        url,
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        log(f"Notice: Primary call with cookies failed (code {res.returncode}): {res.stderr[:400]}; retrying...")
        cmd_fallback = [
            "yt-dlp",
            "--skip-download",
            "--dump-json",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "-o", sub_output_template,
            "--extractor-args", "youtube:player_client=android,ios",
            "--no-playlist",
            url,
        ]
        res = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to extract creator video metadata: {res.stderr[:300]}")

    meta = {}
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                meta = json.loads(line)
                break
            except Exception:
                pass

    if not meta:
        raise RuntimeError("Could not parse JSON metadata from yt-dlp output.")

    video_id = meta.get("id", "video")
    title = meta.get("title", "Untitled Video")
    channel_name = meta.get("uploader") or meta.get("channel") or "Creator"
    duration = float(meta.get("duration") or 0.0)

    # Look for downloaded vtt subtitle file
    vtt_candidates = list(output_dir.glob(f"sub_{video_id}*.vtt"))
    transcript = ""
    sub_path = None
    if vtt_candidates:
        sub_path = vtt_candidates[0]
        transcript = _clean_vtt_text(sub_path)
        log(f"📝 Extracted transcript ({len(transcript)} chars) from auto-subtitles.")
    else:
        transcript = meta.get("description", "")[:2000]

    return CreatorVideoMetadata(
        video_id=video_id,
        title=title,
        channel_name=channel_name,
        duration_sec=duration,
        source_url=url,
        transcript_text=transcript,
        subtitles_path=sub_path,
    )


def download_creator_section(
    url: str,
    start_sec: float,
    duration_sec: float,
    output_path: Path,
) -> Path:
    """
    Phase 2: Downloads ONLY the targeted 45-55s video section using --download-sections.
    Runs in 2-5 seconds instead of downloading entire gigabytes of video.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    end_sec = start_sec + duration_sec
    section_arg = f"*{_format_time_sec(start_sec)}-{_format_time_sec(end_sec)}"
    cookie_args = get_ytdlp_cookie_args()

    log(f"📥 Downloading targeted {duration_sec:.1f}s section [{_format_time_sec(start_sec)} -> {_format_time_sec(end_sec)}] -> {output_path.name}...")

    cmd = [
        "yt-dlp",
        "--download-sections", section_arg,
        "-f", "best[ext=mp4]/b/bv*+ba",
        "--merge-output-format", "mp4",
        "-o", str(output_path),
        *cookie_args,
        "--extractor-args", "youtube:player_client=android,ios,web",
        "--no-playlist",
        url,
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        log(f"Notice: Section download with cookies failed; retrying without cookies...")
        cmd_fb = [
            "yt-dlp",
            "--download-sections", section_arg,
            "-o", str(output_path),
            "--extractor-args", "youtube:player_client=android,ios",
            url,
        ]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Section download failed: {res_fb.stderr[:300]}")

    if not output_path.exists() or output_path.stat().st_size < 50_000:
        parts = list(output_path.parent.glob(f"{output_path.stem}*"))
        mp4_parts = [p for p in parts if p.suffix.lower() == ".mp4"]
        if mp4_parts:
            import shutil
            shutil.move(mp4_parts[0], output_path)
        else:
            raise RuntimeError(f"Downloaded section file not found at {output_path}")

    log(f"✅ Targeted section downloaded successfully: {output_path.name} ({output_path.stat().st_size} bytes)")
    return output_path
