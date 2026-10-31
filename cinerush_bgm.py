#!/usr/bin/env python3
"""
Cinerush BGM Engine
Mixes atmospheric background music (Rayalo / Rayelo Slowed Phonk) under movie dialogue.
- Preserves 100% original dialogue clarity with a subtle bed level (volume ~0.12)
- Loops seamlessly across the entire clip duration
- Stream-copies video (-c:v copy) for instantaneous, zero-re-encoding performance
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def log(msg: str) -> None:
    print(f"[CinerushBGM] {msg}", flush=True)


ASSETS_DIR = Path(__file__).resolve().parent / "assets"
BGM_DIR = ASSETS_DIR / "bgm"

# Candidate filenames for Rayalo / Rayelo
RAYALO_TRACK_NAMES = (
    "rayelo_ultraslowed.mp3",
    "rayalo.mp3",
    "rayelo.mp3",
)


def resolve_cinerush_bgm(bgm_path: Path | str | None = None) -> Path | None:
    """Resolves the background music track, defaulting to Rayalo / Rayelo."""
    if bgm_path:
        p = Path(bgm_path)
        if p.exists():
            return p
        # Check by name inside BGM_DIR
        candidate = BGM_DIR / p.name
        if candidate.exists():
            return candidate

    env_bgm = os.environ.get("CINERUSH_BGM_PATH") or os.environ.get("MOVIE_BGM_PATH")
    if env_bgm:
        p = Path(env_bgm)
        if p.exists():
            return p
        candidate = BGM_DIR / p.name
        if candidate.exists():
            return candidate

    # Search for Rayalo / Rayelo
    for name in RAYALO_TRACK_NAMES:
        candidate = BGM_DIR / name
        if candidate.exists():
            return candidate

    # Fallback to any available BGM track
    fallback = BGM_DIR / "aerohead_fragments_recap.mp3"
    if fallback.exists():
        return fallback

    return None


def apply_cinerush_bgm(
    video_path: Path,
    output_path: Path,
    bgm_path: Path | str | None = None,
    bgm_volume: float = 0.12,
) -> Path:
    """Mixes background music underneath the video's existing audio.

    Uses FFmpeg amix with video stream-copying for high performance.
    Returns output_path on success, or falls back to video_path on error.
    """
    resolved_bgm = resolve_cinerush_bgm(bgm_path)
    if not resolved_bgm or not resolved_bgm.exists():
        log(f"⚠️ No BGM track available; proceeding without BGM overlay.")
        return video_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"🎵 Mixing BGM track '{resolved_bgm.name}' (vol={bgm_volume:.3f}) under dialogue...")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(resolved_bgm),
        "-filter_complex",
        f"[1:a]volume={bgm_volume:.3f}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        log(f"⚠️ FFmpeg BGM mixing failed (code {res.returncode}): {res.stderr[:200]}; keeping original audio.")
        return video_path

    log(f"✅ BGM mixed successfully -> {output_path.name}")
    return output_path
