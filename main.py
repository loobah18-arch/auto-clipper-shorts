#!/usr/bin/env python3
"""
Auto Clipper Shorts - Cinerush-Style Raw Movie Scene Clipper
Main entry point for generating viral 9:16 movie scene YouTube Shorts:
1. 100% original movie dialogue and audio (NO AI voiceover, NO edge-tts)
2. Continuous single-scene sourcing (yt-dlp)
3. Word-level dialogue transcription (faster-whisper)
4. Groq viral window selector (llama-3.3-70b-versatile) + dead-air jump-cut trimming
5. Shot-locked face-tracking 9:16 reframer (MediaPipe / OpenCV YuNet ONNX)
6. Dynamic yellow/white word-by-word karaoke subtitles (.ass)
7. Automated YouTube Shorts publishing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cinerush_pipeline import run_cinerush_pipeline

WORKSPACE_DIR = Path(__file__).resolve().parent
HISTORY_PATH = WORKSPACE_DIR / "clip_history.json"
CATALOG_PATH = WORKSPACE_DIR / "movie_catalog_auto.json"

CURATED_CINERUSH_SCENES = [
    "The Dark Knight interrogation scene",
    "Good Will Hunting park bench scene",
    "Whiplash final drum solo scene",
    "Inglourious Basterds tavern scene",
    "A Few Good Men courtroom you cant handle the truth scene",
    "The Social Network lawyer deposition scene",
    "Interstellar docking scene",
    "No Country For Old Men gas station coin toss scene",
    "Training Day king kong aint got nothing on me scene",
    "The Godfather cannoli scene",
    "Glengarry Glen Ross always be closing scene",
    "Just Mercy Bryan speech courtroom scene",
]


def log(msg: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [AutoClipper] {msg}", flush=True)


def get_next_curated_scene() -> str:
    """Selects the next scene in rotation using clip_history.json."""
    history = {}
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass

    processed = history.get("processed_queries", [])
    for scene in CURATED_CINERUSH_SCENES:
        if scene not in processed:
            return scene

    # If all processed, pick from movie catalog or rotate
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                cat = json.load(f)
                movies = cat.get("movies", [])
                for m in movies:
                    q = f"{m.get('title')} intense scene"
                    if q not in processed:
                        return q
        except Exception:
            pass

    idx = history.get("last_index", 0) % len(CURATED_CINERUSH_SCENES)
    return CURATED_CINERUSH_SCENES[idx]


def record_history(scene_query: str, manifest: dict) -> None:
    """Persists processed scene information to history."""
    history = {}
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass

    history.setdefault("processed_queries", []).append(scene_query)
    history.setdefault("cinerush_shorts", []).append(manifest)
    history["last_updated"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"⚠️ Failed to update history: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Cinerush-Style Raw Movie Scene YouTube Shorts Clipper"
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Movie scene search query (e.g. 'Just Mercy courtroom scene')",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Direct YouTube video URL of a continuous movie scene",
    )
    parser.add_argument(
        "--video-file",
        type=str,
        default=None,
        help="Path to local high-resolution MP4/MKV scene file",
    )
    parser.add_argument(
        "--movie",
        type=str,
        default=None,
        help="Alias for --query for workflow backward compatibility",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: generate complete 9:16 Short locally without uploading",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render pipeline without publishing to YouTube",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="cinerush_style",
        help="Layout profile: cinerush_style (1:1 square), shorts_9x16 (9:16 vertical), or landscape",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory to save final rendered MP4 shorts",
    )

    args, unknown = parser.parse_known_args()

    scene_query = args.query or args.movie
    if not scene_query and not args.url and not args.video_file:
        scene_query = get_next_curated_scene()
        log(f"🎯 Auto-selected next movie scene from catalog: '{scene_query}'")

    out_dir = Path(args.output_dir)
    is_dry_run = args.dry_run or args.test

    manifest = run_cinerush_pipeline(
        query=scene_query,
        url=args.url,
        video_file=args.video_file,
        dry_run=is_dry_run,
        test_mode=args.test,
        profile=args.profile,
        output_dir=out_dir,
    )

    if scene_query:
        record_history(scene_query, manifest)

    print("\n" + "=" * 60)
    print("✨ CINERUSH SHORT EXECUTION COMPLETE!")
    print(f"🎬 Title:     {manifest.get('title')}")
    print(f"⏱️ Duration:  {manifest.get('trimmed_duration'):.2f}s")
    print(f"📁 Output:    {manifest.get('output_file')}")
    if manifest.get("youtube_id"):
        print(f"🔗 YouTube:   https://youtube.com/shorts/{manifest.get('youtube_id')}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
