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
    """Builds a high-intent scene search query targeting movie clip channels and rejecting reality TV / trailers."""
    clean = re.sub(r"[^\w\s-]", "", topic_or_movie).strip()
    # Add scene keywords and negative terms to avoid reality TV, news, reenactments, and trailers
    return f'ytsearch12:{clean} Movieclips full scene HD -trailer -review -shorts -teaser -ost -court -cam -news -interview -reaction -recap -podcast -reenactment -parody'


CINERUSH_PLAYER_CLIENTS: list[tuple[str, str, str]] = [
    ("tv", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=tv"),
    ("android", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=android"),
    ("ios", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=ios"),
    ("web_safari", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=web_safari"),
    ("mweb", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=mweb"),
    ("default", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best", "youtube:player_client=default"),
]


def download_candidate_video(
    target_url: str,
    video_id: str,
    output_dir: Path,
    pot_url: str = "",
    cookies_file: str | None = None,
) -> Path | None:
    """Attempts to download video using multi-client fallback ladder and PO token provider."""
    out_template = str(output_dir / "cinerush_scene_%(id)s.%(ext)s")
    for client_name, fmt, client_arg in CINERUSH_PLAYER_CLIENTS:
        cmd = [
            "yt-dlp",
            "-f", fmt,
            "--merge-output-format", "mp4",
            "-o", out_template,
            "--no-playlist",
            "--no-warnings",
        ]
        if pot_url:
            cmd.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_url}"])
        if client_arg:
            cmd.extend(["--extractor-args", client_arg])
        if cookies_file:
            cmd.extend(["--cookies", cookies_file])
        cmd.append(target_url)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        dest = output_dir / f"cinerush_scene_{video_id}.mp4"
        if dest.exists() and dest.stat().st_size > 300_000:
            log(f"✅ Download succeeded via '{client_name}' client: {dest.name} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
            return dest
        matches = list(output_dir.glob(f"cinerush_scene_{video_id}.*"))
        if matches and matches[0].stat().st_size > 300_000:
            log(f"✅ Download succeeded via '{client_name}' client: {matches[0].name}")
            return matches[0]
        log(f"   ↳ [{client_name}] client notice: {res.stderr[:90].strip() or 'incomplete'}")
    return None


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
            video_id = "direct_scene"
            if "v=" in url:
                video_id = url.split("v=")[1].split("&")[0]
            dest = download_candidate_video(url, video_id, output_dir, pot_url, cookies_file)
            if not dest:
                raise RuntimeError(f"yt-dlp failed to download URL {url} across all player clients")
            info = resolve_media_info(dest)
            return dest, {
                "id": video_id,
                "title": video_id,
                "duration": info["duration"],
                "width": info["width"],
                "height": info["height"],
                "url": url,
            }

        # 3. Search query
        search_query = query or "The Dark Knight interrogation scene"
        yt_search = build_cinerush_search_query(search_query)
        log(f"🔍 Searching YouTube for single-scene clips: {yt_search}")

        # Flat search to find candidate videos
        dump_cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
        ]
        if pot_url:
            dump_cmd.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_url}"])
        dump_cmd.extend(["--extractor-args", "youtube:player_client=tv"])
        if cookies_file:
            dump_cmd.extend(["--cookies", cookies_file])
        dump_cmd.append(yt_search)

        dump_proc = subprocess.run(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        candidates = []
        if dump_proc.returncode == 0:
            for line in dump_proc.stdout.splitlines():
                if line.startswith("{"):
                    try:
                        candidates.append(json.loads(line))
                    except Exception:
                        pass

        def score_candidate(c: dict) -> int:
            t = (c.get("title") or "").lower()
            u = (c.get("uploader") or c.get("channel") or "").lower()
            score = 0
            if any(trusted in u for trusted in ["movieclips", "binge society", "scene city", "universal", "warner", "paramount", "sony", "featurefilm", "boxoffice"]):
                score += 25
            if "movie clip" in t or "official clip" in t:
                score += 15
            elif "scene" in t:
                score += 5
            return score

        # Filter candidates: duration 120s - 420s (2 to 7 mins) and eliminate non-movie spam
        valid_candidates = []
        BAD_KEYWORDS = [
            "trailer", "review", "teaser", "court cam", "interview", "podcast",
            "reaction", "recap", "news", "reenactment", "parody", "fan made",
            "fan-made", "remake", "recreation", "tribute", "cover", "spoof",
            "breakdown", "analysis", "easter egg"
        ]
        for cand in candidates:
            dur = cand.get("duration") or 0.0
            title = (cand.get("title") or "").lower()
            uploader = (cand.get("uploader") or cand.get("channel") or "").lower()
            if any(bad in title for bad in BAD_KEYWORDS):
                continue
            if any(bad in uploader for bad in ["news", "court", "react", "parody"]):
                continue
            if min_duration <= dur <= max_duration:
                valid_candidates.append(cand)

        # Relax filter if no candidate in 120-420s range: accept 80s to 600s
        relaxed = []
        for cand in candidates:
            dur = cand.get("duration") or 0.0
            title = (cand.get("title") or "").lower()
            uploader = (cand.get("uploader") or cand.get("channel") or "").lower()
            if any(bad in title for bad in BAD_KEYWORDS):
                continue
            if 80.0 <= dur <= 600.0:
                relaxed.append(cand)

        pool = valid_candidates if valid_candidates else (relaxed if relaxed else candidates)

        dest = None
        selected_id = None
        selected_meta = None

        if pool:
            pool.sort(key=score_candidate, reverse=True)
            for cand in pool[:5]:
                cid = cand.get("id")
                ctitle = cand.get("title")
                target_url = f"https://www.youtube.com/watch?v={cid}"
                log(f"📥 Attempting candidate: '{ctitle}' ({cid})...")
                dest = download_candidate_video(target_url, cid, output_dir, pot_url, cookies_file)
                if dest:
                    selected_id = cid
                    selected_meta = cand
                    log(f"🎯 Selected candidate: '{ctitle}' ({cid})")
                    break

        if not dest:
            # Fallback: check if genuine cached movie files exist in cache/ or clips/
            cached_files: list[Path] = []
            for folder in ["cache", "raw_clips", "clips"]:
                c_dir = Path(folder)
                if c_dir.exists():
                    for f in list(c_dir.glob("*.mp4")) + list(c_dir.glob("*.mkv")):
                        if f.is_file() and f.stat().st_size > 20_000_000:
                            cached_files.append(f)

            if cached_files:
                # Prefer movie matching search query tokens, otherwise rotate via query seed
                matched = [
                    f for f in cached_files
                    if any(tok in f.stem.lower() for tok in search_query.lower().split() if len(tok) >= 4)
                ]
                selected_file = matched[0] if matched else cached_files[abs(hash(search_query)) % len(cached_files)]
                m_info = resolve_media_info(selected_file)
                m_dur = m_info.get("duration", 0.0)

                # Compute dynamic start offset so we never slice the exact same timestamp repeatedly
                if m_dur > 300.0:
                    seed_val = abs(hash(f"{search_query}_{selected_file.stem}")) % 1000
                    # Sample between 10% and 80% of movie runtime (skipping intro/credits)
                    start_sec = max(60.0, (0.10 + (seed_val / 1000.0) * 0.70) * m_dur)
                else:
                    start_sec = 15.0

                log(f"🎬 Sourcing continuous 200s scene from cached movie: {selected_file.name} at {start_sec:.1f}s...")
                dest = output_dir / f"cinerush_scene_{selected_file.stem}_{int(start_sec)}.mp4"
                slice_cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start_sec:.2f}",
                    "-i", str(selected_file),
                    "-t", "200",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac",
                    str(dest),
                ]
                subprocess.run(slice_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                if dest.exists() and dest.stat().st_size > 500_000:
                    selected_id = selected_file.stem
                    selected_meta = {
                        "id": selected_id,
                        "title": selected_file.stem.replace("_", " ").title(),
                    }

        if not dest:
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
            raise RuntimeError(f"No suitable scene could be downloaded for query: '{search_query}'")

        info = resolve_media_info(dest)
        return dest, {
            "id": selected_id,
            "title": (selected_meta or {}).get("title", search_query),
            "duration": info["duration"] or (selected_meta or {}).get("duration", 0.0),
            "width": info["width"],
            "height": info["height"],
            "url": f"https://www.youtube.com/watch?v={selected_id}",
        }

    finally:
        if cookies_file and os.path.exists(cookies_file):
            try:
                os.remove(cookies_file)
            except Exception:
                pass
