#!/usr/bin/env python3
"""Google Drive-only source resolver for Cinerush."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[CinerushDownloader] {msg}", flush=True)


def resolve_media_info(video_path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_type,codec_name",
        "-of", "json", str(video_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Unable to probe Google Drive media: {res.stderr[:300]}")
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


def download_movie_scene(
    query: str | None = None,
    url: str | None = None,
    video_path: str | Path | None = None,
    output_dir: Path = Path("output"),
    min_duration: float = 120.0,
    max_duration: float = 420.0,
    test_mode: bool = False,
    gdrive_file_id: str | None = None,
    gdrive_file_name: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Resolve only an authorized Google Drive file; never search/download YouTube."""
    del query, url, min_duration, max_duration
    output_dir.mkdir(parents=True, exist_ok=True)
    file_id = (gdrive_file_id or "").strip()
    if file_id and video_path and Path(video_path).exists():
        destination = Path(video_path)
        info = resolve_media_info(destination)
        if not info["has_audio"]:
            raise RuntimeError("Google Drive-derived scene has no audio track")
        return destination, {
            "id": file_id,
            "title": destination.stem.replace("_", " ").title(),
            "duration": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "url": f"gdrive://{file_id}",
            "source_type": "private_media",
            "source_provider": "google_drive",
            "gdrive_file_id": file_id,
            "gdrive_file_name": gdrive_file_name or destination.name,
        }
    file_name = (gdrive_file_name or "").strip()
    if not file_id:
        raise RuntimeError("Google Drive file ID is required; raw clips cannot come from YouTube or local fallback files")
    try:
        import gdown
    except ImportError as exc:
        raise RuntimeError("gdown is required for Google Drive-only raw media") from exc
    destination = output_dir / Path(file_name or f"gdrive_{file_id}.mp4").name
    if not destination.exists() or destination.stat().st_size <= 10_000_000:
        log(f"📥 Downloading authorized Google Drive source: {destination.name}")
        gdown.download(id=file_id, output=str(destination), quiet=False)
    if not destination.exists() or destination.stat().st_size <= 10_000_000:
        raise RuntimeError("Google Drive download failed or produced an unusable file")
    info = resolve_media_info(destination)
    if not info["has_audio"]:
        raise RuntimeError("Google Drive source has no audio track")
    return destination, {
        "id": file_id,
        "title": destination.stem.replace("_", " ").title(),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "url": f"gdrive://{file_id}",
        "source_type": "private_media",
        "source_provider": "google_drive",
        "gdrive_file_id": file_id,
        "gdrive_file_name": destination.name,
        "test_mode": bool(test_mode),
    }
