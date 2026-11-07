#!/usr/bin/env python3
"""
Creator Source Fetcher for Isla Value-Add Clipper.
Uses two-phase fetching via yt-dlp:
1. Metadata & Subtitles extraction (fast, lightweight, no media download)
2. Precision Section Download (--download-sections) for only the targeted 45-55s clip.
Bypasses datacenter bot blocks using Android/iOS client spoofing.
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
        # Strip inline tags like <00:00:00.000><c>
        clean_line = re.sub(r"<[^>]+>", "", line).strip()
        if clean_line and clean_line not in seen:
            seen.add(clean_line)
            cleaned.append(clean_line)
    return " ".join(cleaned)


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

    sub_output_template = str(output_dir / "sub_%(id)s")
    cmd = [
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

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        log(f"Notice: Primary yt-dlp call failed; retrying with basic android client...")
        cmd_alt = [
            "yt-dlp",
            "--dump-json",
            "--skip-download",
            "--extractor-args", "youtube:player_client=android",
            url,
        ]
        res = subprocess.run(cmd_alt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
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

    log(f"📥 Downloading targeted {duration_sec:.1f}s section [{_format_time_sec(start_sec)} -> {_format_time_sec(end_sec)}] -> {output_path.name}...")

    cmd = [
        "yt-dlp",
        "--download-sections", section_arg,
        "-f", "best[ext=mp4]/b/bv*+ba",
        "--merge-output-format", "mp4",
        "-o", str(output_path),
        "--extractor-args", "youtube:player_client=android,ios",
        "--no-playlist",
        url,
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        log(f"Notice: Section download failed ({res.stderr[:200]}); trying general format...")
        cmd_fb = [
            "yt-dlp",
            "--download-sections", section_arg,
            "-o", str(output_path),
            "--extractor-args", "youtube:player_client=android",
            url,
        ]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Section download failed: {res_fb.stderr[:300]}")

    if not output_path.exists() or output_path.stat().st_size < 50_000:
        # Check if yt-dlp added a part suffix
        parts = list(output_path.parent.glob(f"{output_path.stem}*"))
        mp4_parts = [p for p in parts if p.suffix.lower() == ".mp4"]
        if mp4_parts:
            import shutil
            shutil.move(mp4_parts[0], output_path)
        else:
            raise RuntimeError(f"Downloaded section file not found at {output_path}")

    log(f"✅ Targeted section downloaded successfully: {output_path.name} ({output_path.stat().st_size} bytes)")
    return output_path
