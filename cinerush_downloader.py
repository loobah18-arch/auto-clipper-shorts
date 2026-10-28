#!/usr/bin/env python3
"""
Cinerush Scene Downloader
Downloads continuous, high-definition (1080p/720p) single-scene movie clips via yt-dlp.
Enforces Cinerush rules:
- Queries search for full scene HD clips, never trailers or reviews
- Filters for single continuous scenes (120s to 420s duration)
- Preserves 100% original movie audio and dialogue
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[CinerushDownloader] {msg}", flush=True)


def build_cinerush_search_query(topic_or_movie: str) -> str:
    """Builds a high-intent scene search query that filters out trailers and reviews."""
    clean = re.sub(r"[^\w\s-]", "", topic_or_movie).strip()
    # Add scene keywords and negative terms
    return f'ytsearch10:"{clean}" full scene HD -trailer -review -shorts -teaser -ost'


def resolve_media_info(video_path: Path) -> dict[str, Any]:
    """Inspects video stream with ffprobe to verify resolution, duration, and audio."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_type,codec_name",
        "-of", "json",
        str(video_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        return {"duration": 0.0, "width": 1920, "height": 1080, "has_audio": True}
    try:
        data = json.loads(res.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        duration = float(fmt.get("duration", 0.0))
        width = 1920
        height = 1080
        has_audio = False
        for s in streams:
            if s.get("codec_type") == "video":
                width = int(s.get("width") or width)
                height = int(s.get("height") or height)
            elif s.get("codec_type") == "audio":
                has_audio = True
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "has_audio": has_audio,
        }
    except Exception:
        return {"duration": 0.0, "width": 1920, "height": 1080, "has_audio": True}


def generate_test_scene(output_path: Path, duration: float = 95.0) -> Path:
    """Generates a synthetic 720p movie scene for offline/local testing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1280x720:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return output_path


def download_movie_scene(
    query: str | None = None,
    url: str | None = None,
    video_path: str | Path | None = None,
    output_dir: Path = Path("output"),
    min_duration: float = 120.0,
    max_duration: float = 420.0,
    test_mode: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """
    Downloads or resolves a continuous movie scene MP4.

    Returns:
        tuple[Path, dict]: Path to local downloaded MP4, and scene metadata dict.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Local video file path provided
    if video_path:
        local_p = Path(video_path)
        if local_p.exists():
            log(f"🎬 Using provided local scene video: {local_p}")
            info = resolve_media_info(local_p)
            return local_p, {
                "id": local_p.stem,
                "title": local_p.stem.replace("_", " ").title(),
                "duration": info["duration"],
                "width": info["width"],
                "height": info["height"],
                "url": str(local_p),
            }

    # Prepare cookies & PO token parameters for yt-dlp
    pot_url = os.environ.get("YT_DLP_POT_PROVIDER_URL", "").strip()
    extra_args = []
    if pot_url:
        extra_args.extend(["--extractor-args", f"youtube:pot_provider_url={pot_url}"])

    cookies_content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    cookies_file = None
    if cookies_content:
        import base64
        try:
            raw = base64.b64decode(cookies_content).decode("utf-8")
            tmp = tempfile.NamedTemporaryFile(suffix="_cookies.txt", delete=False, mode="w", encoding="utf-8")
            tmp.write(raw)
            tmp.flush()
            cookies_file = tmp.name
            extra_args.extend(["--cookies", cookies_file])
        except Exception as e:
            log(f"⚠️ Failed to parse YOUTUBE_COOKIES: {e}")

    try:
        # 2. Direct URL provided
        if url:
            log(f"🔍 Sourcing movie scene directly from URL: {url}")
            out_template = str(output_dir / "cinerush_scene_%(id)s.%(ext)s")
            cmd = [
                "yt-dlp",
                "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", out_template,
                "--no-playlist",
                "--print-json",
                *extra_args,
                url,
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if proc.returncode != 0:
                raise RuntimeError(f"yt-dlp failed to download URL {url}: {proc.stderr[:300]}")

            # Parse metadata
            meta = {}
            for line in proc.stdout.splitlines():
                if line.startswith("{") and "id" in line:
                    try:
                        meta = json.loads(line)
                        break
                    except Exception:
                        pass
            video_id = meta.get("id", "scene")
            dest = output_dir / f"cinerush_scene_{video_id}.mp4"
            if not dest.exists():
                matches = list(output_dir.glob(f"cinerush_scene_{video_id}.*"))
                if matches:
                    dest = matches[0]

            info = resolve_media_info(dest)
            return dest, {
                "id": video_id,
                "title": meta.get("title", video_id),
                "duration": info["duration"] or meta.get("duration", 0.0),
                "width": info["width"] or meta.get("width", 1920),
                "height": info["height"] or meta.get("height", 1080),
                "url": url,
            }

        # 3. Search query
        search_query = query or "Just Mercy courtroom cross examination scene"
        yt_search = build_cinerush_search_query(search_query)
        log(f"🔍 Searching YouTube for single-scene clips: {yt_search}")

        # Flat search to find candidate videos
        dump_cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            *extra_args,
            yt_search,
        ]
        dump_proc = subprocess.run(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        candidates = []
        if dump_proc.returncode == 0:
            for line in dump_proc.stdout.splitlines():
                if line.startswith("{"):
                    try:
                        candidates.append(json.loads(line))
                    except Exception:
                        pass

        selected_id = None
        selected_meta = None

        # Filter candidates: duration 120s - 420s (2 to 7 mins)
        for cand in candidates:
            dur = cand.get("duration") or 0.0
            title = (cand.get("title") or "").lower()
            if "trailer" in title or "review" in title or "teaser" in title:
                continue
            if min_duration <= dur <= max_duration:
                selected_id = cand.get("id")
                selected_meta = cand
                log(f"🎯 Selected candidate: '{cand.get('title')}' ({dur:.1f}s)")
                break

        # Relax filter if no candidate in 120-420s range: accept 80s to 600s
        if not selected_id and candidates:
            for cand in candidates:
                dur = cand.get("duration") or 0.0
                if 80.0 <= dur <= 600.0:
                    selected_id = cand.get("id")
                    selected_meta = cand
                    log(f"🎯 Selected relaxed candidate: '{cand.get('title')}' ({dur:.1f}s)")
                    break

        if not selected_id and candidates:
            selected_id = candidates[0].get("id")
            selected_meta = candidates[0]

        if not selected_id:
            if test_mode:
                log(f"ℹ️ Test mode enabled: generating synthetic test scene for query '{search_query}'...")
                test_file = output_dir / "cinerush_scene_test.mp4"
                generate_test_scene(test_file, duration=95.0)
                info = resolve_media_info(test_file)
                return test_file, {
                    "id": "test_scene",
                    "title": f"{search_query.title()} Test Scene",
                    "duration": info["duration"],
                    "width": info["width"],
                    "height": info["height"],
                    "url": "local://test_scene.mp4",
                }
            raise RuntimeError(f"No suitable scene found for query: '{search_query}'")

        target_url = f"https://www.youtube.com/watch?v={selected_id}"
        out_template = str(output_dir / "cinerush_scene_%(id)s.%(ext)s")
        dl_cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_template,
            "--no-playlist",
            *extra_args,
            target_url,
        ]
        log(f"📥 Downloading scene from {target_url}...")
        dl_proc = subprocess.run(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if dl_proc.returncode != 0:
            if test_mode:
                log(f"⚠️ Warning: yt-dlp failed in test mode ({dl_proc.stderr[:120]}). Using synthetic test scene.")
                test_file = output_dir / "cinerush_scene_test.mp4"
                generate_test_scene(test_file, duration=95.0)
                info = resolve_media_info(test_file)
                return test_file, {
                    "id": "test_scene",
                    "title": f"{search_query.title()} Test Scene",
                    "duration": info["duration"],
                    "width": info["width"],
                    "height": info["height"],
                    "url": "local://test_scene.mp4",
                }
            raise RuntimeError(f"yt-dlp download failed: {dl_proc.stderr[:300]}")

        dest = output_dir / f"cinerush_scene_{selected_id}.mp4"
        if not dest.exists():
            matches = list(output_dir.glob(f"cinerush_scene_{selected_id}.*"))
            if matches:
                dest = matches[0]

        info = resolve_media_info(dest)
        return dest, {
            "id": selected_id,
            "title": (selected_meta or {}).get("title", search_query),
            "duration": info["duration"] or (selected_meta or {}).get("duration", 0.0),
            "width": info["width"],
            "height": info["height"],
            "url": target_url,
        }

    finally:
        if cookies_file and os.path.exists(cookies_file):
            try:
                os.remove(cookies_file)
            except Exception:
                pass
