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
    download_movie_from_gdrive,
    slice_movie_timeline_scenes,
    download_movie_trailer,
    slice_trailer_dynamic_scenes,
    create_cinematic_movie_visual_fallback,
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


def select_next_movie(requested_movie: str = None, requested_part: int = None, lang: str = "en") -> dict:
    """
    Selects a movie and specific part from user request or automatically
    continues the episodic series sequence (Part 1 -> Part 2 -> ... -> Part 5 -> next movie).
    """
    catalog = load_catalog()
    movies = catalog.get("movies", [])
    if not movies:
        raise RuntimeError("No movies found in movie_catalog.json!")

    history = load_history()
    uploaded_entries = history.get("uploaded_movies", [])
    current_series = history.get("current_series")

    def resolve_part(movie: dict, part_num: int = None) -> dict:
        parts = movie.get("parts", [])
        if not parts:
            return movie

        if not part_num:
            uploaded_parts = {
                entry.get("part_number") for entry in uploaded_entries
                if entry.get("movie_id") == movie.get("id") and entry.get("part_number") is not None
            }
            unuploaded = [p for p in parts if p.get("part_number") not in uploaded_parts]
            part = unuploaded[0] if unuploaded else parts[0]
        else:
            matched = [p for p in parts if p.get("part_number") == part_num]
            part = matched[0] if matched else parts[0]

        p_num = part.get("part_number", 1)
        tot_parts = len(parts)
        merged = dict(movie)
        merged.update({
            "part_number": p_num,
            "total_parts": tot_parts,
            "series_title": movie.get("title"),
            "title": part.get("title", f"{movie.get('title')} - Part {p_num}"),
            "badge": part.get("badge", f"{movie.get('badge', movie.get('title'))} • PART {p_num}"),
            "timeline_start": part.get("timeline_start", "00:01:00"),
            "timeline_end": part.get("timeline_end", "00:26:00"),
            "hook": part.get("hook", movie.get("hook", "")),
            "script": part.get("script", movie.get("script", "")),
            "tags": list(set(part.get("tags", []) + movie.get("tags", [])))
        })
        log(f"🎬 Resolved episodic part: '{merged['title']}' (Part {p_num}/{tot_parts}) [{merged['timeline_start']} -> {merged['timeline_end']}]")
        return merged

    # Case 1: Custom movie requested
    if requested_movie:
        log(f"🎯 Custom movie requested: '{requested_movie}'")
        cat_match = get_movie_from_catalog(requested_movie)
        if cat_match:
            return resolve_part(cat_match, requested_part)
        ai_movie = generate_movie_script_ai(requested_movie, language=lang)
        if requested_part:
            ai_movie["part_number"] = requested_part
            ai_movie["title"] = f"{ai_movie.get('title')} - Part {requested_part}"
        return ai_movie

    # Case 2: Continue active in-progress multi-part series
    if current_series and not current_series.get("completed", False):
        s_id = current_series.get("movie_id")
        cur_p = current_series.get("current_part", 0)
        tot_p = current_series.get("total_parts", 1)
        if cur_p < tot_p:
            next_part = cur_p + 1
            matched = [m for m in movies if m.get("id") == s_id]
            if matched:
                log(f"⏩ Continuing series sequence for '{matched[0].get('title')}': Part {next_part}/{tot_p}")
                return resolve_part(matched[0], next_part)

    # Case 3: Find first multi-part movie with uncompleted parts
    for m in movies:
        if m.get("parts"):
            parts = m["parts"]
            uploaded_parts = {
                entry.get("part_number") for entry in uploaded_entries
                if entry.get("movie_id") == m.get("id") and entry.get("part_number") is not None
            }
            if len(uploaded_parts) < len(parts):
                log(f"🎬 Starting new series from catalog: '{m.get('title')}'")
                return resolve_part(m)

    # Case 4: Standalone un-uploaded movies
    uploaded_ids = {entry.get("movie_id") for entry in uploaded_entries if not entry.get("part_number")}
    unseen = [m for m in movies if not m.get("parts") and m.get("id") not in uploaded_ids]
    if unseen:
        log(f"🎬 Selected standalone movie from catalog: '{unseen[0].get('title')}'")
        return unseen[0]

    # Case 5: All movies uploaded, rotate to first multi-part movie
    selected = movies[0]
    log(f"🔄 All catalog movies uploaded. Rotating back to: '{selected.get('title')}' (Part 1)")
    return resolve_part(selected, 1)





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
    part_num = movie_data.get("part_number")
    tot_parts = movie_data.get("total_parts")

    if part_num:
        youtube_title = f"{title_clean} Ending Explained 😱 #Shorts #MovieExplained"[:100]
        next_teaser = f"👉 Part {part_num + 1} coming next! Like & Subscribe so you don't miss it!" if (tot_parts and part_num < tot_parts) else "👉 Comment which movie you want explained next!"
    else:
        year_str = f" ({movie_data.get('year')})" if movie_data.get("year") and str(movie_data.get("year")) not in title_clean else ""
        youtube_title = f"{title_clean}{year_str} Ending Explained in 60s 😱 #Shorts #MovieExplained"[:100]
        next_teaser = "👉 Comment which movie you want explained next!"

    tags = list(set(movie_data.get("tags", []) + [
        "shorts", "movieexplained", "movierecap", "moviegyan", "movieinsighthindi", "plottwist", "cinema"
    ]))[:15]

    series_display = movie_data.get("series_title", title_clean)
    description = f"""{youtube_title}

🎬 MOVIE EXPLANATION & EPISODIC RECAP
Film: {series_display}
Genre: {movie_data.get('genre', 'Action / Sci-Fi / Thriller')}

💡 Hook: {movie_data.get('hook', '')}

{next_teaser}

📌 Transformative Fair Use Notice:
This video is a critical analysis, educational breakdown, and commentary on the movie '{series_display}'.
All original movie audio was 100% stripped and replaced with original narration and custom background music.
Visual clips are heavily transformed, color-graded, reframed in 9:16 vertical composition, and strictly limited to rapid snippets under Fair Use (Section 107 of the US Copyright Act).
All rights belong to their respective film studios and copyright holders.

🔔 Subscribe to the channel for daily mind-blowing movie explanations, plot twists, and episodic recaps!

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


def run_pipeline(
    movie_name: str = None,
    part: int = None,
    voice: str = None,
    lang: str = "en",
    dry_run: bool = False,
    force_upload: bool = False,
    video_file: str = None,
    audio_file: str = None,
    watermark: str = None
):
    """Executes the complete Movie Explanation Short creation workflow."""
    start_time = datetime.now(timezone.utc)
    log("=" * 65)
    log("🎬 STARTING MOVIE EXPLANATION SHORTS GENERATOR (MovieGyan Style)")
    log("=" * 65)

    # 1. Movie Selection & Script
    movie_data = select_next_movie(movie_name, requested_part=part, lang=lang)
    movie_id = movie_data.get("id", re.sub(r"[^\w]", "_", movie_data.get("title", "movie")).lower())
    title = movie_data.get("title", "Movie Explained")
    script_text = movie_data.get("script", "")
    badge_text = movie_data.get("badge", title)
    search_query = movie_data.get("search_query", f"{title} official trailer")

    log(f"📖 Movie: {title}")
    if script_text:
        log(f"🎙️ Script ({len(script_text.split())} words): \"{script_text[:85]}...\"")

    # Determine TTS Voice (Default: highly natural human female narrator)
    if not voice:
        voice = "hi-IN-SwaraNeural" if lang == "hi" else "en-US-AvaNeural"

    # Paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"{movie_id}_narration_{timestamp}.mp3"
    subtitles_path = OUTPUT_DIR / f"{movie_id}_subtitles_{timestamp}.ass"
    raw_trailer_path = OUTPUT_DIR / f"{movie_id}_trailer_raw.mp4"
    sliced_video_path = OUTPUT_DIR / f"{movie_id}_sliced_{timestamp}.mp4"
    final_short_path = OUTPUT_DIR / f"movie_short_{movie_id}_{timestamp}.mp4"
    thumb_path = OUTPUT_DIR / f"thumb_{movie_id}_{timestamp}.jpg"

    # 2. Audio Generation (or use custom human voice)
    if audio_file and Path(audio_file).exists():
        log(f"🎙️ Using custom human voiceover: '{audio_file}'")
        shutil.copyfile(audio_file, audio_path)
        duration = get_audio_duration(audio_path)
        sim_words = [{"text": script_text, "start": 0.0, "end": duration}]
        words = create_word_timestamps_from_sentences(sim_words)
    else:
        sentences = asyncio.run(generate_speech_audio(script_text, audio_path, voice=voice))
        duration = get_audio_duration(audio_path)
        words = create_word_timestamps_from_sentences(sentences)

    log(f"⏱️ Audio narration duration: {duration:.2f} seconds")

    # 3. Generate Subtitles (MovieGyan Style)
    generate_moviegyan_subtitles(words, subtitles_path, group_size=3)

    # 4. Sourcing Video Clips (Genuine GDrive Movie, Local File, or YouTube Trailer)
    CACHE_DIR = WORKSPACE_DIR / "cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sliced_ok = False

    if video_file and Path(video_file).exists():
        log(f"🎞️ Sourcing scenes from local video file: '{video_file}'")
        if movie_data.get("timeline_start") and movie_data.get("timeline_end"):
            sliced_ok = slice_movie_timeline_scenes(
                Path(video_file),
                movie_data["timeline_start"],
                movie_data["timeline_end"],
                duration,
                sliced_video_path
            )
        else:
            sliced_ok = slice_trailer_dynamic_scenes(Path(video_file), duration, sliced_video_path)

    elif movie_data.get("gdrive_file_id"):
        gdrive_id = movie_data["gdrive_file_id"]
        movie_filename = movie_data.get("gdrive_file_name", f"{movie_id}_movie.mkv")
        target_movie_path = CACHE_DIR / movie_filename
        log(f"🎬 Sourcing genuine movie file from Google Drive: {movie_filename}...")
        gdrive_ok = download_movie_from_gdrive(gdrive_id, target_movie_path)
        if gdrive_ok and target_movie_path.exists():
            t_start = movie_data.get("timeline_start", "00:01:00")
            t_end = movie_data.get("timeline_end", "00:26:00")
            log(f"🎞️ Slicing timeline scenes from BluRay movie [{t_start} -> {t_end}]...")
            sliced_ok = slice_movie_timeline_scenes(
                target_movie_path,
                t_start,
                t_end,
                duration,
                sliced_video_path
            )

    if not sliced_ok:
        trailer_url = movie_data.get("trailer_url")
        log(f"🔍 Sourcing YouTube trailer for '{title}'...")
        trailer_ok = download_movie_trailer(search_query, raw_trailer_path, trailer_url=trailer_url)
        if trailer_ok:
            sliced_ok = slice_trailer_dynamic_scenes(raw_trailer_path, duration, sliced_video_path)

    if not sliced_ok or not sliced_video_path.exists():
        log("⚠️ Video footage unavailable, generating authentic movie visual fallback...")
        create_cinematic_movie_visual_fallback(duration, title, sliced_video_path)

    # 5. Render Final 9:16 Short (with Color Grade & Optional Watermark)
    bgm_file = BGM_DIR / "cinematic_suspense_thriller.mp3"
    render_movie_explanation_short(
        sliced_video_path=sliced_video_path,
        narration_audio_path=audio_path,
        ass_subtitle_path=subtitles_path,
        movie_title=title,
        badge_text=badge_text,
        output_final_path=final_short_path,
        bgm_path=bgm_file,
        watermark_text=watermark
    )

    # 6. Generate Thumbnail
    generate_thumbnail(final_short_path, thumb_path, title)

    # 7. Upload to YouTube
    should_upload = (not dry_run) or force_upload
    yt_id = upload_movie_to_youtube(final_short_path, thumb_path, movie_data, dry_run=(not should_upload))

    # 8. Record in History
    history = load_history()
    if "uploaded_movies" not in history:
        history["uploaded_movies"] = []

    is_multipart = "part_number" in movie_data and "total_parts" in movie_data
    if is_multipart:
        p_num = movie_data["part_number"]
        tot_p = movie_data["total_parts"]
        history["current_series"] = {
            "movie_id": movie_id,
            "movie_title": movie_data.get("series_title", title),
            "current_part": p_num,
            "total_parts": tot_p,
            "completed": p_num >= tot_p,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    history["uploaded_movies"].append({
        "movie_id": movie_id,
        "title": title,
        "part_number": movie_data.get("part_number"),
        "total_parts": movie_data.get("total_parts"),
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
    parser.add_argument("--movie", type=str, default=None, help="Name of movie to explain (e.g. 'The Avengers (2012)')")
    parser.add_argument("--part", type=int, default=None, help="Part number of multi-part movie series (e.g. 1, 2, 3)")
    parser.add_argument("--voice", type=str, default=None, help="Edge-TTS voice (default: en-US-ChristopherNeural)")
    parser.add_argument("--lang", type=str, choices=["en", "hi"], default="en", help="Language: 'en' or 'hi'")
    parser.add_argument("--dry-run", action="store_true", help="Render video locally without uploading to YouTube")
    parser.add_argument("--upload", action="store_true", help="Force upload to YouTube")
    parser.add_argument("--list", action="store_true", help="List catalog movies, multi-part series, and upload history")
    parser.add_argument("--video-file", type=str, default=None, help="Path to local high-res movie or scene pack MP4/MKV")
    parser.add_argument("--audio-file", type=str, default=None, help="Path to custom human-recorded voiceover MP3/WAV")
    parser.add_argument("--watermark", type=str, default=None, help="Channel watermark text (e.g. '@CinemaInsights')")
    args = parser.parse_args()

    if args.list:
        cat = load_catalog()
        hist = load_history()
        cur_series = hist.get("current_series")
        print("\n🎬 MOVIE CATALOG:")
        for m in cat.get("movies", []):
            parts = m.get("parts", [])
            if parts:
                print(f"  • {m.get('title')} [{len(parts)} Parts] ({m.get('genre')})")
                for p in parts:
                    print(f"      - Part {p.get('part_number')}: {p.get('title')} [{p.get('timeline_start')} -> {p.get('timeline_end')}]")
            else:
                print(f"  • {m.get('title')} [Single Clip] ({m.get('genre')})")

        if cur_series:
            status_str = "COMPLETED" if cur_series.get("completed") else f"Part {cur_series.get('current_part')}/{cur_series.get('total_parts')}"
            print(f"\n⚡ ACTIVE SERIES PROGRESS: {cur_series.get('movie_title')} ({status_str})")

        print(f"\n📜 UPLOAD HISTORY ({len(hist.get('uploaded_movies', []))} items):")
        for h in hist.get("uploaded_movies", [])[-5:]:
            p_str = f" [Part {h.get('part_number')}]" if h.get("part_number") else ""
            print(f"  • {h.get('title')}{p_str} -> {h.get('youtube_id')} ({h.get('timestamp')})")
        return

    run_pipeline(
        movie_name=args.movie,
        part=args.part,
        voice=args.voice,
        lang=args.lang,
        dry_run=args.dry_run,
        force_upload=args.upload,
        video_file=args.video_file,
        audio_file=args.audio_file,
        watermark=args.watermark
    )


if __name__ == "__main__":
    main()
