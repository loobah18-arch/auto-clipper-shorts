#!/usr/bin/env python3
"""
🎬 Auto Movie Explanation Shorts Generator (MovieGyan / Movie Insight Hindi Style)
Orchestrates the entire movie explanation pipeline:
1. Movie Selection & Script Generation (curated catalog or AI on-demand)
2. Neural Voiceover Synthesis (edge-tts)
3. Word-Level Dynamic Karaoke ASS Subtitles (bright yellow active words)
4. Automated YouTube Trailer Sourcing & Dynamic Scene Slicing (yt-dlp)
5. 9:16 Vertical Video Compositor (centered 16:9 movie with blurred mirror background)
6. Suspense/Thriller BGM Mixing (-18dB under crystal-clear narration)
7. YouTube Shorts Upload (with SEO title, tags, and Fair Use attribution)
"""

import os
import re
import sys
import json
import random
import shutil
import asyncio
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

from movie_ai_script import generate_movie_script_ai, load_catalog, get_movie_from_catalog
from movie_video_engine import (
    generate_speech_audio,
    create_word_timestamps_from_sentences,
    generate_moviegyan_subtitles,
    get_audio_duration,
    download_movie_trailer,
    slice_trailer_dynamic_scenes,
    render_movie_explanation_short,
    generate_thumbnail,
    log,
    OUTPUT_DIR,
    BGM_DIR,
    find_system_font
)

HISTORY_FILE = WORKSPACE_DIR / "movie_history.json"

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    Credentials = None
    build = None
    MediaFileUpload = None


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"uploaded_movies": []}


def save_history(data: dict):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log(f"⚠️ Failed to save history: {e}")


def select_next_movie(requested_movie: str = None, lang: str = "en") -> dict:
    """Selects a movie from request, or rotates through catalog."""
    if requested_movie:
        log(f"🎯 Custom movie requested: '{requested_movie}'")
        # Check catalog first
        cat_match = get_movie_from_catalog(requested_movie)
        if cat_match:
            return cat_match
        # Else generate via AI
        return generate_movie_script_ai(requested_movie, language=lang)

    catalog = load_catalog()
    movies = catalog.get("movies", [])
    if not movies:
        raise RuntimeError("No movies found in movie_catalog.json!")

    history = load_history()
    uploaded_ids = {item.get("movie_id") for item in history.get("uploaded_movies", [])}

    # Find first unuploaded movie
    unseen = [m for m in movies if m.get("id") not in uploaded_ids]
    if unseen:
        selected = unseen[0]
        log(f"🎬 Selected next un-uploaded movie from catalog: '{selected.get('title')}'")
        return selected

    # If all uploaded, pick random from catalog
    selected = random.choice(movies)
    log(f"🔄 All catalog movies uploaded at least once. Rotating: '{selected.get('title')}'")
    return selected


def create_fallback_procedural_video(duration: float, title: str, output_path: Path):
    """
    Creates a procedural cinematic dark video if trailer download fails,
    ensuring the video rendering never halts.
    """
    log("⚠️ Generating procedural cinematic background visual fallback...")
    font_path = find_system_font()
    font_opt = f"fontfile='{font_path}'" if os.path.exists(font_path) else "font='DejaVu Sans'"
    clean_title = re.sub(r"[^A-Za-z0-9\s]", "", title).strip().upper()[:24]

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#0f111a:s=1080x1920:d={duration:.2f}",
        "-f", "lavfi", "-i", f"color=c=#1a1f2c:s=1040x585:d={duration:.2f}",
        "-filter_complex", (
            f"[0:v][1:v]overlay=(W-w)/2:(H-h)/2 - 40[base];"
            f"[base]drawtext=text='{clean_title}':fontsize=48:fontcolor=white:{font_opt}:x=(w-text_w)/2:y=(h-text_h)/2 - 40[v]"
        ),
        "-map", "[v]",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(output_path)
    ]
    subprocess.run(cmd, check=True)


def upload_movie_to_youtube(video_path: Path, thumb_path: Path, movie_data: dict, dry_run: bool = False) -> str:
    """Uploads the movie explanation short to YouTube."""
    if dry_run:
        log("ℹ️ Dry-run mode enabled: Skipping YouTube upload.")
        return None

    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    refresh_token = os.environ.get("REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        log("⚠️ YouTube OAuth credentials not fully found in environment (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN). Skipping upload.")
        return None

    if not Credentials or not build:
        log("⚠️ Google API Client library not installed. Skipping upload.")
        return None

    title_clean = movie_data.get("title", "Movie Explained")
    year_str = f" ({movie_data.get('year')})" if movie_data.get("year") and str(movie_data.get("year")) not in title_clean else ""
    
    # YouTube Short title (max 100 chars, click-worthy MovieGyan style)
    youtube_title = f"{title_clean}{year_str} Ending Explained in 60s 😱 #Shorts #MovieExplained"[:100]

    tags = list(set(movie_data.get("tags", []) + [
        "shorts", "movieexplained", "movierecap", "moviegyan", "movieinsighthindi", "plottwist", "cinema"
    ]))[:15]

    description = f"""{youtube_title}

🎬 MOVIE EXPLANATION & PLOT BREAKDOWN
Film: {title_clean}{year_str}
Genre: {movie_data.get('genre', 'Thriller')}

💡 Hook: {movie_data.get('hook', '')}

📌 Fair Use Notice:
This video is an educational and transformative critical explanation of the movie '{title_clean}'. 
All visual snippets are used for commentary, analysis, and storytelling under Fair Use doctrine. 
All copyright belongs to the respective film studios and production companies.

🔔 Subscribe to the channel for daily mind-blowing movie explanations, plot twists, and hidden cinema secrets!

#shorts #movieexplained #movierecap #moviegyan #movieinsighthindi #endingexplained #plottwist #cinema #hollywood
"""

    log(f"🚀 Uploading Short to YouTube: '{youtube_title}'...")
    try:
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": youtube_title,
                "description": description,
                "tags": tags,
                "categoryId": "1"  # Film & Animation
            },
            "status": {
                "privacyStatus": os.environ.get("PRIVACY_STATUS", "public"),
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log(f"Upload progress: {int(status.progress() * 100)}%")

        vid_id = resp.get("id")
        log(f"🎉 Successfully uploaded Short! URL: https://youtube.com/shorts/{vid_id}")

        # Set thumbnail if available
        if thumb_path and thumb_path.exists():
            try:
                thumb_media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
                youtube.thumbnails().set(videoId=vid_id, media_body=thumb_media).execute()
                log("🖼️ Custom thumbnail uploaded.")
            except Exception as te:
                log(f"⚠️ Thumbnail upload notice: {te}")

        return vid_id

    except Exception as e:
        log(f"❌ YouTube upload failed: {e}")
        return None


def run_pipeline(movie_name: str = None, voice: str = None, lang: str = "en", dry_run: bool = False, force_upload: bool = False):
    """Executes the complete Movie Explanation Short creation workflow."""
    start_time = datetime.now(timezone.utc)
    log("=" * 65)
    log("🎬 STARTING MOVIE EXPLANATION SHORTS GENERATOR (MovieGyan Style)")
    log("=" * 65)

    # 1. Movie Selection & Script
    movie_data = select_next_movie(movie_name, lang=lang)
    movie_id = movie_data.get("id", re.sub(r"[^\w]", "_", movie_data.get("title", "movie")).lower())
    title = movie_data.get("title", "Movie Explained")
    script_text = movie_data.get("script")
    badge_text = movie_data.get("badge", title)
    search_query = movie_data.get("search_query", f"{title} official trailer")

    log(f"📖 Movie: {title}")
    log(f"🎙️ Script ({len(script_text.split())} words): \"{script_text[:85]}...\"")

    # Determine TTS Voice
    if not voice:
        voice = "hi-IN-MadhurNeural" if lang == "hi" else "en-US-ChristopherNeural"

    # Paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"{movie_id}_narration_{timestamp}.mp3"
    subtitles_path = OUTPUT_DIR / f"{movie_id}_subtitles_{timestamp}.ass"
    raw_trailer_path = OUTPUT_DIR / f"{movie_id}_trailer_raw.mp4"
    sliced_video_path = OUTPUT_DIR / f"{movie_id}_sliced_{timestamp}.mp4"
    final_short_path = OUTPUT_DIR / f"movie_short_{movie_id}_{timestamp}.mp4"
    thumb_path = OUTPUT_DIR / f"thumb_{movie_id}_{timestamp}.jpg"

    # 2. Synthesize Audio
    sentences = asyncio.run(generate_speech_audio(script_text, audio_path, voice=voice))
    duration = get_audio_duration(audio_path)
    log(f"⏱️ Audio narration duration: {duration:.2f} seconds")

    # 3. Generate Subtitles (MovieGyan Style)
    words = create_word_timestamps_from_sentences(sentences)
    generate_moviegyan_subtitles(words, subtitles_path, group_size=3)

    # 4. Sourcing Trailer Clips
    trailer_ok = download_movie_trailer(search_query, raw_trailer_path)
    if trailer_ok:
        sliced_ok = slice_trailer_dynamic_scenes(raw_trailer_path, duration, sliced_video_path)
    else:
        sliced_ok = False

    if not sliced_ok or not sliced_video_path.exists():
        log("⚠️ Trailer sourcing unavailable, generating procedural visuals...")
        create_fallback_procedural_video(duration, title, sliced_video_path)

    # 5. Render Final 9:16 Short
    bgm_file = BGM_DIR / "cinematic_suspense_thriller.mp3"
    render_movie_explanation_short(
        sliced_video_path=sliced_video_path,
        narration_audio_path=audio_path,
        ass_subtitle_path=subtitles_path,
        movie_title=title,
        badge_text=badge_text,
        output_final_path=final_short_path,
        bgm_path=bgm_file
    )

    # 6. Generate Thumbnail
    generate_thumbnail(final_short_path, thumb_path, title)

    # 7. Upload to YouTube
    should_upload = (not dry_run) or force_upload
    yt_id = upload_movie_to_youtube(final_short_path, thumb_path, movie_data, dry_run=(not should_upload))

    # 8. Record in History
    history = load_history()
    history["uploaded_movies"].append({
        "movie_id": movie_id,
        "title": title,
        "youtube_id": yt_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "video_path": str(final_short_path),
        "duration_sec": round(duration, 2)
    })
    save_history(history)

    # Cleanup temporary files (keep final video and thumb)
    for p in [audio_path, subtitles_path, sliced_video_path]:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    log("=" * 65)
    log(f"🎬 PIPELINE COMPLETE! Rendered: {final_short_path}")
    if yt_id:
        log(f"🔗 YouTube Short Live: https://youtube.com/shorts/{yt_id}")
    log("=" * 65)
    return final_short_path


def main():
    parser = argparse.ArgumentParser(description="Movie Explanation YouTube Shorts Automation Pipeline")
    parser.add_argument("--movie", type=str, default=None, help="Name of movie to explain (e.g. 'The Platform')")
    parser.add_argument("--voice", type=str, default=None, help="Edge-TTS voice (default: en-US-ChristopherNeural)")
    parser.add_argument("--lang", type=str, choices=["en", "hi"], default="en", help="Language: 'en' or 'hi'")
    parser.add_argument("--dry-run", action="store_true", help="Render video locally without uploading to YouTube")
    parser.add_argument("--upload", action="store_true", help="Force upload to YouTube")
    parser.add_argument("--list", action="store_true", help="List catalog movies and upload history")
    args = parser.parse_args()

    if args.list:
        cat = load_catalog()
        hist = load_history()
        print("\n🎬 MOVIE CATALOG:")
        for m in cat.get("movies", []):
            print(f"  • {m.get('title')} [{m.get('genre')}]")
        print(f"\n📜 UPLOAD HISTORY ({len(hist.get('uploaded_movies', []))} items):")
        for h in hist.get("uploaded_movies", [])[-5:]:
            print(f"  • {h.get('title')} -> {h.get('youtube_id')} ({h.get('timestamp')})")
        return

    run_pipeline(
        movie_name=args.movie,
        voice=args.voice,
        lang=args.lang,
        dry_run=args.dry_run,
        force_upload=args.upload
    )


if __name__ == "__main__":
    main()
