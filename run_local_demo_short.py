#!/usr/bin/env python3
"""
Local Demo Video Generator for Auto Clipper Shorts.
Extracts a 15-second high-energy highlight from local podcast audio & subtitles,
tests MiniMax H3 gesture video generation, renders the 1080x1920 Short via pure FFmpeg,
and copies the final video directly to the user's Downloads directory.
"""

import os
import sys
import json
import shutil
from pathlib import Path

# Add project root to sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

from main import (
    parse_json3_subtitles,
    generate_karaoke_ass_subtitles,
    render_studio_visualizer_short,
    log,
    OUTPUT_DIR
)

def main():
    log("=======================================================")
    log(" 🎬 Generating Local Test Short with MiniMax H3 Gestures")
    log("=======================================================")

    # 1. Target output directories
    home_downloads = Path.home() / "downloads" / "auto_clipper_output"
    phone_downloads = Path.home() / "storage" / "downloads" / "auto_clipper_output"
    home_downloads.mkdir(parents=True, exist_ok=True)
    try:
        phone_downloads.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # 2. Source Audio & Subtitles
    audio_source = OUTPUT_DIR / "rss_podcast_audio.mp3"
    subs_source = OUTPUT_DIR / "subs_temp.en.json3"

    if not audio_source.exists():
        log(f"❌ Error: Audio file {audio_source.name} not found.")
        sys.exit(1)

    # 3. Parse Subtitles or Build Segments
    start_sec = 11.0
    end_sec = 26.0  # 15 seconds test clip for fast rendering
    duration = end_sec - start_sec

    segments = []
    if subs_source.exists():
        try:
            with open(subs_source, "r", encoding="utf-8") as f:
                json3_data = json.load(f)
            segments = parse_json3_subtitles(json3_data)
        except Exception as e:
            log(f"Notice: Parsing json3: {e}")

    if not segments:
        segments = [
            {
                "id": 0,
                "start": 11.0,
                "end": 15.0,
                "text": "NVIDIA is the engine powering the AI revolution",
                "words": [
                    {"word": "NVIDIA", "start": 11.0, "end": 11.8, "probability": 1.0},
                    {"word": "IS", "start": 11.9, "end": 12.3, "probability": 1.0},
                    {"word": "THE", "start": 12.4, "end": 12.7, "probability": 1.0},
                    {"word": "ENGINE", "start": 12.8, "end": 13.5, "probability": 1.0},
                    {"word": "POWERING", "start": 13.6, "end": 14.3, "probability": 1.0},
                    {"word": "AI", "start": 14.4, "end": 15.0, "probability": 1.0}
                ]
            },
            {
                "id": 1,
                "start": 15.1,
                "end": 20.5,
                "text": "and its success is driven by sheer force of innovation",
                "words": [
                    {"word": "AND", "start": 15.1, "end": 15.5, "probability": 1.0},
                    {"word": "ITS", "start": 15.6, "end": 16.0, "probability": 1.0},
                    {"word": "SUCCESS", "start": 16.1, "end": 17.2, "probability": 1.0},
                    {"word": "IS", "start": 17.3, "end": 17.6, "probability": 1.0},
                    {"word": "DRIVEN", "start": 17.7, "end": 18.5, "probability": 1.0},
                    {"word": "BY", "start": 18.6, "end": 19.0, "probability": 1.0},
                    {"word": "INNOVATION", "start": 19.1, "end": 20.5, "probability": 1.0}
                ]
            },
            {
                "id": 2,
                "start": 20.6,
                "end": 26.0,
                "text": "building the future of intelligent computing systems",
                "words": [
                    {"word": "BUILDING", "start": 20.6, "end": 21.5, "probability": 1.0},
                    {"word": "THE", "start": 21.6, "end": 22.0, "probability": 1.0},
                    {"word": "FUTURE", "start": 22.1, "end": 23.2, "probability": 1.0},
                    {"word": "OF", "start": 23.3, "end": 23.6, "probability": 1.0},
                    {"word": "INTELLIGENT", "start": 23.7, "end": 24.8, "probability": 1.0},
                    {"word": "SYSTEMS", "start": 24.9, "end": 26.0, "probability": 1.0}
                ]
            }
        ]

    # 4. Generate Karaoke Subtitles
    ass_path = OUTPUT_DIR / "local_test_subtitles.ass"
    generate_karaoke_ass_subtitles(segments, start_sec, end_sec, ass_path)

    # 5. Render Video
    output_video = OUTPUT_DIR / "local_test_short.mp4"
    raw_video_ref = OUTPUT_DIR / "raw_slice_Pmd6knanPKw.mp4"
    log("🎨 Starting render of 1080x1920 portrait Short with Computer Vision Gesture Tracking...")
    render_studio_visualizer_short(
        audio_full_path=audio_source,
        start_sec=start_sec,
        end_sec=end_sec,
        ass_subtitle_path=ass_path,
        output_final_path=output_video,
        speaker_badge="JENSEN HUANG & LEX FRIDMAN",
        transcript_segments=segments,
        speaker_gender="male",
        host_gender="male",
        topic_title="AI REVOLUTION & NVIDIA",
        video_reference_path=raw_video_ref if raw_video_ref.exists() else None
    )

    if output_video.exists() and output_video.stat().st_size > 100000:
        file_size_mb = output_video.stat().st_size / (1024 * 1024)
        log(f"✅ Video render complete! ({file_size_mb:.2f} MB)")

        # Copy to Downloads
        dest_1 = home_downloads / "test_short_minimax.mp4"
        shutil.copy2(output_video, dest_1)
        log(f"📁 Copied to: {dest_1}")

        if phone_downloads.exists():
            try:
                dest_2 = phone_downloads / "test_short_minimax.mp4"
                shutil.copy2(output_video, dest_2)
                log(f"📱 Copied to Phone Downloads: {dest_2}")
            except Exception as e:
                log(f"Phone storage copy notice: {e}")

        log("=======================================================")
        log(" 🎉 Test Short Successfully Generated & Ready to View!")
        log("=======================================================")
    else:
        log("❌ Video rendering failed or produced empty file.")

if __name__ == "__main__":
    main()
