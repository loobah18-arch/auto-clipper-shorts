#!/usr/bin/env python3
"""
Cinerush BGM Engine with Intelligent Dialogue Auto-Ducking
Mixes atmospheric background music (Rayalo / Rayelo Slowed Phonk) under movie dialogue.
- Dialogue Active: Music volume automatically drops down (ducked ~0.07) so speech is 100% crisp and clear.
- Dialogue Finished / Pauses: Music volume smoothly swells up to full presence (vol ~0.28) for high cinematic impact.
- Sidechain Compression: Attack (40ms) and release (350ms) prevent clicks, distortion, or pumping.
- Zero-loss performance: Uses video stream-copying (-c:v copy) to mix audio in milliseconds without video re-compression.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import wave
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

    for name in RAYALO_TRACK_NAMES:
        candidate = BGM_DIR / name
        if candidate.exists():
            return candidate

    fallback = BGM_DIR / "aerohead_fragments_recap.mp3"
    if fallback.exists():
        return fallback

    return None


def generate_speech_key_signal(
    output_wav: Path,
    duration_sec: float,
    speech_intervals: list[tuple[float, float]],
    sample_rate: int = 16000,
) -> Path:
    """Synthesizes a lightweight 1kHz sidechain key signal from speech intervals.

    Active speech intervals are rendered with a 1kHz tone at 0.9 amplitude.
    Non-speech periods are 0.0 (silence).
    Includes a 60ms pre-speech lead-in and 120ms release tail for organic ducking response.
    """
    total_frames = int(max(0.5, duration_sec) * sample_rate)
    data = bytearray(total_frames * 2)

    for s, e in speech_intervals:
        start_sec = max(0.0, s - 0.06)
        end_sec = min(duration_sec, e + 0.12)
        start_frame = max(0, int(start_sec * sample_rate))
        end_frame = min(total_frames, int(end_sec * sample_rate))

        for f in range(start_frame, end_frame):
            sample = int(28000 * math.sin(2.0 * math.pi * 1000.0 * f / sample_rate))
            struct.pack_into("<h", data, f * 2, sample)

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)

    return output_wav


def apply_cinerush_bgm(
    video_path: Path,
    output_path: Path,
    bgm_path: Path | str | None = None,
    bgm_volume: float = 0.28,
    speech_intervals: list[tuple[float, float]] | None = None,
) -> Path:
    """Mixes background music underneath the video's audio with automatic speech ducking.

    - When dialogue is spoken: BGM automatically ducks down (~0.06-0.08) so speech is crystal clear.
    - When dialogue stops / during pauses: BGM smoothly swells back up to full presence (bgm_volume ~0.28).
    - Uses sidechain compression with video stream-copying for instantaneous, zero-loss performance.
    """
    resolved_bgm = resolve_cinerush_bgm(bgm_path)
    if not resolved_bgm or not resolved_bgm.exists():
        log("⚠️ No BGM track available; proceeding without BGM overlay.")
        return video_path

    # Measure video duration
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video_path),
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
    try:
        video_dur = float(probe.stdout.strip())
    except (TypeError, ValueError):
        video_dur = 60.0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    key_wav: Path | None = None

    if speech_intervals:
        key_wav = output_path.parent / f"duck_key_{output_path.stem}.wav"
        generate_speech_key_signal(key_wav, video_dur, speech_intervals)
        log(f"🎵 Applying auto-ducking BGM '{resolved_bgm.name}' (full={bgm_volume:.2f}, auto-ducked during speech)...")

        # Sidechain compress using the speech key tone (input 2)
        fc = (
            f"[1:a]volume={bgm_volume:.3f}[bgm_full];"
            f"[bgm_full][2:a]sidechaincompress=threshold=0.08:ratio=12:attack=40:release=350[bgm_ducked];"
            f"[0:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(resolved_bgm),
            "-i", str(key_wav),
            "-filter_complex", fc,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        # Acoustic sidechain ducking triggered by vocal frequency range in dialogue audio
        log(f"🎵 Applying acoustic auto-ducking BGM '{resolved_bgm.name}' (vol={bgm_volume:.2f})...")
        fc = (
            f"[0:a]asplit=2[dia_out][dia_sc];"
            f"[dia_sc]highpass=f=150,lowpass=f=3800[voice_sc];"
            f"[1:a]volume={bgm_volume:.3f}[bgm_full];"
            f"[bgm_full][voice_sc]sidechaincompress=threshold=0.02:ratio=8:attack=40:release=350[bgm_ducked];"
            f"[dia_out][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(resolved_bgm),
            "-filter_complex", fc,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

    if key_wav and key_wav.exists():
        key_wav.unlink(missing_ok=True)

    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        log(f"⚠️ FFmpeg BGM mixing failed (code {res.returncode}): {res.stderr[:200]}; keeping original audio.")
        return video_path

    log(f"✅ Auto-ducked BGM mixed successfully -> {output_path.name}")
    return output_path
