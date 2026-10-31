#!/usr/bin/env python3
"""
Cinerush Camera Shutter Sound Effect Module
Overlays a crisp, mechanical camera shutter sound at each jump-cut transition boundary:
- Centers shutter sound right at each cut timestamp
- Mixes with genuine movie audio using FFmpeg amix
- Leaves video stream completely untouched (-c:v copy) for instantaneous processing and zero quality loss
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np


def log(msg: str) -> None:
    print(f"[CinerushShutter] {msg}", flush=True)


SFX_DIR = Path(__file__).resolve().parent / "assets" / "sfx"
DEFAULT_SHUTTER_SFX = SFX_DIR / "camera_shutter.wav"


def ensure_camera_shutter_sfx(sfx_path: Path = DEFAULT_SHUTTER_SFX) -> Path:
    """Ensures a high-quality mechanical camera shutter sound effect exists on disk."""
    if sfx_path.exists() and sfx_path.stat().st_size > 1000:
        return sfx_path

    sfx_path.parent.mkdir(parents=True, exist_ok=True)
    sr = 48000
    duration = 0.18
    num_samples = int(sr * duration)
    t = np.linspace(0, duration, num_samples, endpoint=False)

    np.random.seed(42)
    noise = np.random.uniform(-1, 1, num_samples)

    # Shutter click 1 (mirror flip up / front curtain) at t=0.01s
    click1_env = np.exp(-np.maximum(0, t - 0.01) * 350) * (t >= 0.01)
    click1 = np.sin(2 * np.pi * 2400 * t) * click1_env + noise * click1_env * 0.7

    # Shutter click 2 (rear curtain / mirror return) at t=0.065s
    click2_env = np.exp(-np.maximum(0, t - 0.065) * 220) * (t >= 0.065)
    click2 = np.sin(2 * np.pi * 1400 * t) * click2_env * 1.2 + noise * click2_env * 0.9

    # Mechanical snap body (low thump)
    thump_env = np.exp(-np.maximum(0, t - 0.065) * 80) * (t >= 0.065)
    thump = np.sin(2 * np.pi * 180 * t) * thump_env * 0.5

    audio = click1 + click2 + thump
    audio = audio / (np.max(np.abs(audio)) + 1e-6) * 0.85
    audio_int16 = (audio * 32767).astype(np.int16)

    with wave.open(str(sfx_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())

    log(f"📸 Synthesized crisp camera shutter SFX -> {sfx_path.name}")
    return sfx_path


def apply_camera_shutter_transitions(
    video_path: Path,
    cut_timestamps: list[float],
    output_path: Path,
    shutter_sfx_path: Path | None = None,
    sfx_volume: float = 0.85,
) -> Path:
    """
    Overlays camera shutter sound effect at each jump-cut boundary.
    If cut_timestamps is empty, copies or returns video_path.
    """
    if not cut_timestamps:
        log("ℹ️ No jump cuts in video; skipping camera shutter SFX.")
        return video_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sfx_file = ensure_camera_shutter_sfx(shutter_sfx_path or DEFAULT_SHUTTER_SFX)

    # Probe duration of input video
    cmd_probe = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(video_path),
    ]
    res_probe = subprocess.run(cmd_probe, capture_output=True, text=True, check=False)
    try:
        total_dur = float(res_probe.stdout.strip())
    except (TypeError, ValueError):
        total_dur = max(cut_timestamps) + 2.0

    # Load SFX audio data
    with wave.open(str(sfx_file), "rb") as wf:
        sfx_sr = wf.getframerate()
        sfx_data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    # Build full-length track with shutter sounds placed at cut timestamps
    total_samples = int(total_dur * sfx_sr) + sfx_sr
    shutter_track = np.zeros(total_samples, dtype=np.float32)

    for cut_ts in cut_timestamps:
        if cut_ts <= 0 or cut_ts >= total_dur:
            continue
        # Center shutter sound right on the cut
        start_idx = max(0, int((cut_ts - 0.04) * sfx_sr))
        end_idx = min(total_samples, start_idx + len(sfx_data))
        fit_len = end_idx - start_idx
        shutter_track[start_idx:end_idx] += sfx_data[:fit_len]

    shutter_track_int16 = np.clip(shutter_track, -32768, 32767).astype(np.int16)

    temp_shutter_wav = output_path.parent / f"temp_shutter_track_{output_path.stem}.wav"
    with wave.open(str(temp_shutter_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sfx_sr)
        wf.writeframes(shutter_track_int16.tobytes())

    # Mix using FFmpeg amix with video stream copied
    mix_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(temp_shutter_wav),
        "-filter_complex", f"[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 {sfx_volume:.2f}[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ]

    log(f"📸 Mixing camera shutter sound at {len(cut_timestamps)} jump-cut boundaries: {cut_timestamps}...")
    res = subprocess.run(mix_cmd, capture_output=True, text=True, check=False)
    temp_shutter_wav.unlink(missing_ok=True)

    if res.returncode == 0 and output_path.exists():
        log(f"✅ Camera shutter SFX applied successfully: {output_path.name}")
        return output_path

    log(f"⚠️ Shutter SFX mixing failed: {res.stderr[:200]}; falling back to original video.")
    return video_path
