#!/usr/bin/env python3
"""
Generate Tech Fact & Developer Tip Shorts for Auto Clipper Shorts.
Uses Neural Text-to-Speech (edge-tts) for high-energy tech narration,
generates dynamic neon karaoke subtitles, tech badges, and audio visualizers.
"""

import os
import sys
import json
import random
import shutil
import asyncio
from pathlib import Path

# Add project root to sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

try:
    import edge_tts
except ImportError:
    edge_tts = None

from main import (
    create_word_timestamps_from_segment,
    generate_karaoke_ass_subtitles,
    render_studio_visualizer_short,
    upload_to_youtube,
    load_json,
    save_json,
    HISTORY_PATH,
    log,
    OUTPUT_DIR
)

TECH_FACTS_DATABASE = [
    {
        "topic": "UNDERWATER INTERNET CABLES",
        "badge": "TECH FACT 💡",
        "title": "How 99% of the Internet Travels Under the Ocean 🌊 #TechShorts",
        "voice": "en-US-ChristopherNeural",
        "tags": ["techfacts", "technology", "internet", "cables", "networking", "shorts"],
        "script": (
            "Did you know that over ninety nine percent of all international internet traffic "
            "travels through underwater fiber optic cables? "
            "These deep sea cables span over eight hundred thousand miles across the ocean floor. "
            "They are as thin as a garden hose, but transfer petabits of data every second!"
        )
    },
    {
        "topic": "LINUX TERMINAL SUPERPOWER",
        "badge": "DEV TRICK ⚡",
        "title": "The Secret Linux Shortcut Every Developer Needs 🐧 #Shorts",
        "voice": "en-US-GuyNeural",
        "tags": ["linux", "terminal", "bash", "developer", "codingtips", "shorts"],
        "script": (
            "Stop scrolling up to find your old terminal commands! "
            "Just press Control plus R in any bash or zsh shell to trigger reverse history search. "
            "Type a single keyword like docker or git, and it instantly brings back your longest commands!"
        )
    },
    {
        "topic": "FLOATING POINT SECRETS",
        "badge": "CS FACT 💻",
        "title": "Why 0.1 + 0.2 Is NOT 0.3 In Computer Science 🤯 #Shorts",
        "voice": "en-US-EricNeural",
        "tags": ["programming", "coding", "computerscience", "math", "python", "shorts"],
        "script": (
            "If you open your browser console or Python terminal and type zero point one plus zero point two, "
            "you won't get zero point three! You get zero point three zero zero zero zero zero zero four! "
            "This happens because computers use base two binary floating point math, where some decimals cannot be stored precisely."
        )
    },
    {
        "topic": "HOW SSH KEYS WORK",
        "badge": "CYBERSECURITY 🛡️",
        "title": "How SSH Logs You In Without Sending Your Password 🔐 #Shorts",
        "voice": "en-US-ChristopherNeural",
        "tags": ["cybersecurity", "ssh", "encryption", "infosec", "linux", "shorts"],
        "script": (
            "How do SSH keys log you into remote servers without sending your password? "
            "The server generates a random cryptographic puzzle and encrypts it with your public key. "
            "Only your local private key can solve it, proving your identity without ever leaking your secrets!"
        )
    },
    {
        "topic": "THE FIRST COMPUTER BUG",
        "badge": "TECH HISTORY 📜",
        "title": "The Crazy True Story of the First Computer Bug 🪲 #TechShorts",
        "voice": "en-US-GuyNeural",
        "tags": ["techhistory", "programming", "debugging", "computerscience", "shorts"],
        "script": (
            "In nineteen forty seven, computer pioneer Grace Hopper found an actual live moth trapped in the Harvard Mark Two relay. "
            "They taped the dead moth into their logbook with the note: First actual case of bug being found. "
            "And that is why we call fixing code debugging!"
        )
    }
]


async def synthesize_tech_audio(script_text: str, output_mp3: Path, voice: str = "en-US-ChristopherNeural", rate: str = "+12%"):
    """Synthesizes high quality voice narration and returns word-timed segments."""
    if not edge_tts:
        raise RuntimeError("edge-tts is not installed. Run `pip install edge-tts`")

    log(f"🎙️ Synthesizing tech narration voice using {voice} (rate: {rate})...")
    comm = edge_tts.Communicate(script_text, voice=voice, rate=rate)
    sentences = []
    
    with open(output_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                s = chunk["offset"] / 10_000_000
                d = chunk["duration"] / 10_000_000
                sentences.append({"text": chunk["text"], "start": s, "end": s + d})

    # If sentence boundaries weren't returned, fallback to total audio duration estimation
    if not sentences:
        import subprocess
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_mp3)]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_dur = float(res.stdout.strip() or 15.0)
        sentences = [{"text": script_text, "start": 0.0, "end": total_dur}]

    segments = []
    for idx, s in enumerate(sentences):
        words = create_word_timestamps_from_segment(s["text"], s["start"], s["end"])
        segments.append({
            "id": idx,
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
            "words": words
        })

    return segments


def render_tech_short(fact_index: int = None, dry_run: bool = False):
    log("=======================================================")
    log(" 🎬 Generating Tech Fact & Developer Tip Short (TTS Narration)")
    log("=======================================================")

    history = load_json(HISTORY_PATH, {"last_fact_index": -1, "processed_clips": []})
    
    if fact_index is not None and 0 <= fact_index < len(TECH_FACTS_DATABASE):
        chosen_idx = fact_index
    else:
        last_idx = history.get("last_fact_index", -1)
        chosen_idx = (last_idx + 1) % len(TECH_FACTS_DATABASE)

    fact = TECH_FACTS_DATABASE[chosen_idx]
    log(f"Selected Tech Topic [{chosen_idx + 1}/{len(TECH_FACTS_DATABASE)}]: {fact['topic']} ({fact['badge']})")

    # Output directories
    home_downloads = Path.home() / "downloads" / "auto_clipper_output"
    phone_downloads = Path.home() / "storage" / "downloads" / "auto_clipper_output"
    home_downloads.mkdir(parents=True, exist_ok=True)
    try:
        phone_downloads.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    voice_mp3 = OUTPUT_DIR / "tech_narration.mp3"
    
    # 1. Synthesize Audio Narration with exact word timestamps
    segments = asyncio.run(synthesize_tech_audio(fact["script"], voice_mp3, voice=fact.get("voice", "en-US-ChristopherNeural")))
    start_sec = 0.0
    end_sec = segments[-1]["end"] + 0.6  # slight buffer at end
    duration = end_sec - start_sec

    log(f"✅ Tech voice synthesis complete ({duration:.1f}s narration across {len(segments)} segments).")

    # 2. Generate Neon Karaoke ASS Subtitles
    ass_path = OUTPUT_DIR / "tech_fact_subtitles.ass"
    generate_karaoke_ass_subtitles(segments, start_sec, end_sec, ass_path)

    # 3. Render 1080x1920 Short via Studio Visualizer
    out_video = OUTPUT_DIR / "tech_fact_short.mp4"
    log("🎨 Rendering 1080x1920 portrait Short with Studio Layout...")
    render_studio_visualizer_short(
        audio_full_path=voice_mp3,
        start_sec=start_sec,
        end_sec=end_sec,
        ass_subtitle_path=ass_path,
        output_final_path=out_video,
        speaker_badge=fact["badge"],
        transcript_segments=segments,
        speaker_gender="male",
        host_gender="male",
        topic_title=fact["topic"],
        video_reference_path=None
    )

    if out_video.exists() and out_video.stat().st_size > 50000:
        size_mb = out_video.stat().st_size / (1024 * 1024)
        log(f"✅ Tech Fact Short generated successfully! ({size_mb:.2f} MB)")

        dest_1 = home_downloads / "tech_fact_short.mp4"
        shutil.copy2(out_video, dest_1)
        log(f"📁 Copied to: {dest_1}")

        if phone_downloads.exists():
            try:
                dest_2 = phone_downloads / "tech_fact_short.mp4"
                shutil.copy2(out_video, dest_2)
                log(f"📱 Copied to Phone Downloads: {dest_2}")
            except Exception as e:
                log(f"Phone copy notice: {e}")

        # 4. Upload to YouTube if not dry-run
        if not dry_run:
            clip_info = {
                "viral_title": fact["title"],
                "speaker_badge": fact["badge"],
                "tags": fact.get("tags", ["techshorts", "coding", "techfacts", "shorts"])
            }
            podcast_dummy = {
                "name": "Tech Facts & Tips",
                "default_tags": ["techshorts", "technology", "programming", "shorts"],
                "attribution_template": "Original Tech Facts Series 💡"
            }
            log(f"🚀 Uploading Short to YouTube: {fact['title']}...")
            upload_to_youtube(out_video, clip_info, podcast_dummy, original_video_url="https://youtube.com/@techfacts")

        # Update History Rotation
        history["last_fact_index"] = chosen_idx
        save_json(HISTORY_PATH, history)

        log("=======================================================")
        log(f" 🎉 Video Ready: {fact['title']}")
        log("=======================================================")
    else:
        log("❌ Video generation failed or output file is empty.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate and upload Tech Fact Shorts.")
    parser.add_argument("index", nargs="?", type=int, default=None, help="Index of fact to render")
    parser.add_argument("--dry-run", action="store_true", help="Generate video without uploading")
    parser.add_argument("--index", dest="fact_idx", type=int, default=None, help="Explicit fact index")
    args = parser.parse_args()

    chosen_index = args.fact_idx if args.fact_idx is not None else args.index
    render_tech_short(chosen_index, dry_run=args.dry_run)
