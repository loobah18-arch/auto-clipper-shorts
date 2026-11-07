#!/usr/bin/env python3
"""
Isla Value-Add YouTube Shorts Generator Orchestrator.
Main entry point for generating and publishing viral, looped-arc creator Shorts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

from creator_fetcher import (
    download_creator_section,
    download_creator_scenes,
    fetch_creator_metadata,
)
from isla_story_engine import generate_looped_arc_script
from isla_upload_guard import upload_isla_short
from isla_video_engine import DEFAULT_BGM, assemble_isla_short


def log(msg: str) -> None:
    print(f"[IslaOrchestrator] {msg}", flush=True)


CATALOG_PATH = Path(__file__).resolve().parent / "creator_catalog.json"


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"niches": {}}


def pick_target_url(catalog: dict, niche_key: str = "science_experiments") -> tuple[str, str]:
    """Picks a creator video from the catalog."""
    niche = catalog.get("niches", {}).get(niche_key, {})
    creators = niche.get("target_creators", [])
    if not creators:
        # Fallback default
        return "https://www.youtube.com/watch?v=x0SQ88bAjto", "NileBlue"

    creator = random.choice(creators)
    creator_name = creator.get("name", "Creator")
    sample_urls = creator.get("sample_urls", [])

    if sample_urls:
        return random.choice(sample_urls), creator_name

    return "https://www.youtube.com/watch?v=x0SQ88bAjto", creator_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Isla Value-Add YouTube Shorts Generator")
    parser.add_argument("--url", type=str, default=None, help="Target YouTube video URL")
    parser.add_argument("--niche", type=str, default="science_experiments", help="Niche from catalog")
    parser.add_argument("--voice", type=str, default="en-US-ChristopherNeural", help="TTS voice override")
    parser.add_argument("--dry-run", action="store_true", help="Render video without uploading")
    parser.add_argument("--privacy", type=str, default="public", choices=["public", "unlisted", "private"])
    args = parser.parse_args()

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog()

    if args.url:
        target_url = args.url
        creator_name = "Creator"
    else:
        target_url, creator_name = pick_target_url(catalog, args.niche)

    log(f"🚀 Starting Isla Value-Add Clipper Pipeline for: {target_url} ({creator_name})")

    # 1. Fetch Metadata & Subtitles (Fast Phase 1)
    log("Step 1/5: Extracting video metadata and speech transcript...")
    metadata = fetch_creator_metadata(target_url, output_dir=output_dir)
    creator_name = metadata.channel_name or creator_name
    log(f"📺 Channel: {creator_name} | Video: '{metadata.title}' ({metadata.duration_sec:.1f}s)")

    # 2. Generate Looped Arc Script (Phase 2)
    log("Step 2/5: Generating Looped Arc script (Hook -> Conflict -> Comeback -> Seamless Loop)...")
    script = generate_looped_arc_script(
        video_title=metadata.title,
        channel_name=creator_name,
        transcript_text=metadata.transcript_text,
        video_duration=metadata.duration_sec,
    )
    log(f"📝 Script generated: '{script.title}' (Hook: '{script.hook_text}')")

    # 3. Precision Section or Multi-Scene Download (Phase 3)
    if script.scenes and len(script.scenes) > 1:
        log(f"Step 3/5: Precision multi-scene download ({len(script.scenes)} narrative beats across video)...")
        section_file = output_dir / f"multi_scene_{metadata.video_id}.mp4"
        download_creator_scenes(
            url=target_url,
            scenes=script.scenes,
            output_path=section_file,
        )
    else:
        clip_dur = min(58.0, max(42.0, script.raw_clip_end - script.raw_clip_start))
        section_file = output_dir / f"clip_{metadata.video_id}_{int(script.raw_clip_start)}.mp4"
        log(f"Step 3/5: Precision download of viral window ({script.raw_clip_start:.1f}s -> {script.raw_clip_start + clip_dur:.1f}s)...")
        download_creator_section(
            url=target_url,
            start_sec=script.raw_clip_start,
            duration_sec=clip_dur,
            output_path=section_file,
        )

    # 4. Assemble 9:16 Full-Bleed Short with Kinetic Subtitles & Audio Mix
    log("Step 4/5: Rendering 1080x1920 full-bleed Short with subtitles & audio mix...")
    rendered_short = assemble_isla_short(
        source_video=section_file,
        script=script,
        output_dir=output_dir,
        voice=args.voice,
        bgm_path=DEFAULT_BGM if DEFAULT_BGM.exists() else None,
        is_pre_sliced=True,
    )

    # 5. Upload to YouTube
    log("Step 5/5: Uploading Short to YouTube @woosclips...")
    result = upload_isla_short(
        video_path=rendered_short,
        script=script,
        channel_name=creator_name,
        source_url=target_url,
        privacy_status=args.privacy,
        dry_run=args.dry_run,
    )

    log(f"🏁 Pipeline complete! Result: {result.get('url', 'Success')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
