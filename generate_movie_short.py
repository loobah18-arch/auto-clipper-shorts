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
import shutil
import asyncio
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

from movie_ai_script import generate_movie_script_ai, load_catalog, get_movie_from_catalog
from movie_pipeline_state import (
    DuplicateEpisodeError,
    JsonValue,
    RenderManifest,
    RunStatus,
    append_history_entry,
    episode_key,
    load_history as load_movie_history,
    save_history as save_movie_history,
    sha256_file,
    stable_seed,
    update_series_progress,
    utc_now,
    uploaded_movie_ids,
    uploaded_part_numbers,
    write_manifest,
)
from movie_quality import MAX_SHORT_DURATION, MediaValidationError, probe_media, validate_upload_source
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
)

HISTORY_FILE = WORKSPACE_DIR / "movie_history.json"

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
except ImportError:
    Credentials = None
    build = None
    MediaFileUpload = None

    class HttpError(Exception):
        """Fallback when Google API client dependencies are unavailable."""


def select_next_movie(requested_movie: str = None, requested_part: int = None, lang: str = "en") -> dict:
    """
    Selects a movie and specific part from user request or automatically
    continues the episodic series sequence (Part 1 -> Part 2 -> ... -> Part 5 -> next movie).
    """
    catalog = load_catalog()
    movies = catalog.get("movies", [])
    if not movies:
        raise RuntimeError("No movies found in movie_catalog.json!")

    history = load_movie_history(HISTORY_FILE)
    current_series = history.get("current_series")

    def resolve_part(movie: dict, part_num: int = None) -> dict:
        parts = movie.get("parts", [])
        if not parts:
            return movie

        if not part_num:
            uploaded_parts = uploaded_part_numbers(history, str(movie.get("id", "")))
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
            uploaded_parts = uploaded_part_numbers(history, str(m.get("id", "")))
            if len(uploaded_parts) < len(parts):
                log(f"🎬 Starting new series from catalog: '{m.get('title')}'")
                return resolve_part(m)


    # Case 4: Standalone un-uploaded movies
    uploaded_ids = uploaded_movie_ids(history)
    unseen = [m for m in movies if not m.get("parts") and m.get("id") not in uploaded_ids]
    if unseen:
        log(f"🎬 Selected standalone movie from catalog: '{unseen[0].get('title')}'")
        return unseen[0]

    # Case 5: All movies uploaded, rotate to first multi-part movie
    selected = movies[0]
    log(f"🔄 All catalog movies uploaded. Rotating back to: '{selected.get('title')}' (Part 1)")
    return resolve_part(selected, 1)





class UploadFailedError(RuntimeError):
    """Raised when publishing was requested but YouTube returned no video ID."""

    def __init__(self, title: str) -> None:
        self.title = title
        super().__init__(f"YouTube upload did not return a video ID for {title}")


def upload_movie_to_youtube(video_path: Path, thumb_path: Path, movie_data: dict, dry_run: bool = False) -> str | None:
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

    tags = list(dict.fromkeys(movie_data.get("tags", []) + [
        "shorts", "movieexplained", "movierecap", "moviegyan", "movieinsighthindi", "plottwist", "cinema"
    ]))[:15]

    series_display = movie_data.get("series_title", title_clean)
    description = f"""{youtube_title}

🎬 MOVIE EXPLANATION & EPISODIC RECAP
Film: {series_display}
Genre: {movie_data.get('genre', 'Action / Sci-Fi / Thriller')}

💡 Hook: {movie_data.get('hook', '')}

{next_teaser}

📌 Source and rights notice:
Narration and commentary are original to this channel. Visuals are sourced only from the configured authorized trailer or media source. Any underlying film and footage rights remain with their respective owners.

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
                "privacyStatus": os.environ.get("PRIVACY_STATUS") or "unlisted",
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
    movie_name: str | None = None,
    part: int | None = None,
    voice: str | None = None,
    lang: str = "en",
    dry_run: bool = False,
    force_upload: bool = False,
    video_file: str | None = None,
    audio_file: str | None = None,
    watermark: str | None = None,
) -> Path:
    """Execute the cloud movie Short pipeline and persist its outcome."""
    log("=" * 65)
    log("🎬 STARTING MOVIE EXPLANATION SHORTS GENERATOR (MovieGyan Style)")
    log("=" * 65)

    movie_data = select_next_movie(movie_name, requested_part=part, lang=lang)
    movie_id = str(movie_data.get("id", re.sub(r"[^\w]", "_", str(movie_data.get("title", "movie"))).lower()))
    title = str(movie_data.get("title", "Movie Explained"))
    script_text = str(movie_data.get("script", ""))
    badge_text = str(movie_data.get("badge", title))
    search_query = str(movie_data.get("search_query", f"{title} official trailer"))
    part_number = movie_data.get("part_number") if isinstance(movie_data.get("part_number"), int) else None
    total_parts = movie_data.get("total_parts") if isinstance(movie_data.get("total_parts"), int) else None
    script_hash = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    render_seed = stable_seed(movie_id, str(part_number or "standalone"), script_hash)
    upload_requested = force_upload or not dry_run
    history = load_movie_history(HISTORY_FILE)

    if upload_requested:
        if part_number is not None and part_number in uploaded_part_numbers(history, movie_id):
            raise DuplicateEpisodeError(episode_key(movie_id, part_number))
        if part_number is None and movie_id in uploaded_movie_ids(history):
            raise DuplicateEpisodeError(episode_key(movie_id, None))

    log(f"📖 Movie: {title}")
    if script_text:
        log(f"🎙️ Script ({len(script_text.split())} words): \"{script_text[:85]}...\"")

    if not voice:
        voice = "hi-IN-SwaraNeural" if lang == "hi" else "en-US-AvaNeural"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"{movie_id}_narration_{timestamp}.mp3"
    subtitles_path = OUTPUT_DIR / f"{movie_id}_subtitles_{timestamp}.ass"
    raw_trailer_path = OUTPUT_DIR / f"{movie_id}_trailer_raw.mp4"
    sliced_video_path = OUTPUT_DIR / f"{movie_id}_sliced_{timestamp}.mp4"
    final_short_path = OUTPUT_DIR / f"movie_short_{movie_id}_{timestamp}.mp4"
    thumb_path = OUTPUT_DIR / f"thumb_{movie_id}_{timestamp}.jpg"
    manifest_path = OUTPUT_DIR / f"render_manifest_{movie_id}_{timestamp}.json"

    if audio_file and Path(audio_file).exists():
        log(f"🎙️ Using custom human voiceover: '{audio_file}'")
        shutil.copyfile(audio_file, audio_path)
        duration = get_audio_duration(audio_path)
        words = create_word_timestamps_from_sentences([
            {"text": script_text, "start": 0.0, "end": duration}
        ])
    else:
        sentences = asyncio.run(generate_speech_audio(script_text, audio_path, voice=voice))
        duration = get_audio_duration(audio_path)
        words = create_word_timestamps_from_sentences(sentences)

    if duration > MAX_SHORT_DURATION:
        raise MediaValidationError(
            f"Narration is {duration:.2f}s; shorten the script before rendering "
            f"the {MAX_SHORT_DURATION:.0f}s Short"
        )

    log(f"⏱️ Audio narration duration: {duration:.2f} seconds")
    generate_moviegyan_subtitles(words, subtitles_path, group_size=3, language=lang)

    cache_dir = WORKSPACE_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sliced_ok = False
    source_type = "neutral_fallback"

    if video_file and Path(video_file).exists():
        source_type = "local_media"
        log(f"🎞️ Sourcing scenes from local video file: '{video_file}'")
        if movie_data.get("timeline_start") and movie_data.get("timeline_end"):
            sliced_ok = slice_movie_timeline_scenes(
                Path(video_file),
                movie_data["timeline_start"],
                movie_data["timeline_end"],
                duration,
                sliced_video_path,
                seed=render_seed,
            )
        else:
            sliced_ok = slice_trailer_dynamic_scenes(
                Path(video_file), duration, sliced_video_path, seed=render_seed
            )
    elif os.environ.get("ALLOW_PRIVATE_MEDIA_SOURCES", "false").lower() == "true" and (
        os.environ.get("GDRIVE_FILE_ID") or movie_data.get("gdrive_file_id")
    ):
        source_type = "private_media"
        gdrive_id = str(os.environ.get("GDRIVE_FILE_ID") or movie_data["gdrive_file_id"])
        movie_filename = str(
            os.environ.get("GDRIVE_FILE_NAME")
            or movie_data.get("gdrive_file_name", f"{movie_id}_movie.mkv")
        )
        target_movie_path = cache_dir / movie_filename
        log(f"🎬 Sourcing authorized private media: {movie_filename}...")
        gdrive_ok = download_movie_from_gdrive(gdrive_id, target_movie_path)
        if gdrive_ok and target_movie_path.exists():
            sliced_ok = slice_movie_timeline_scenes(
                target_movie_path,
                str(movie_data.get("timeline_start", "00:01:00")),
                str(movie_data.get("timeline_end", "00:26:00")),
                duration,
                sliced_video_path,
                seed=render_seed,
            )

    if not sliced_ok:
        trailer_url = movie_data.get("trailer_url")
        if isinstance(trailer_url, str) and trailer_url.strip():
            source_type = "authorized_trailer"
            log(f"🔍 Sourcing configured trailer for '{title}'...")
            trailer_ok = download_movie_trailer(search_query, raw_trailer_path, trailer_url=trailer_url)
            if trailer_ok:
                sliced_ok = slice_trailer_dynamic_scenes(
                    raw_trailer_path, duration, sliced_video_path, seed=render_seed
                )

    if not sliced_ok or not sliced_video_path.exists():
        source_type = "neutral_fallback"
        log("⚠️ No authorized footage available; generating a neutral motion background...")
        create_cinematic_movie_visual_fallback(duration, title, sliced_video_path)

    if upload_requested:
        # Fail closed: a Short with no licensed footage is placeholder content
        # and must not reach the channel. Render output is discarded on purpose.
        validate_upload_source(
            source_type,
            allow_fallback=os.environ.get("ALLOW_FALLBACK_UPLOAD", "false").lower() == "true",
        )

    configured_bgm = os.environ.get("MOVIE_BGM_PATH", "").strip()
    bgm_file = Path(configured_bgm) if configured_bgm and Path(configured_bgm).exists() else None
    render_movie_explanation_short(
        sliced_video_path=sliced_video_path,
        narration_audio_path=audio_path,
        ass_subtitle_path=subtitles_path,
        movie_title=title,
        badge_text=badge_text,
        output_final_path=final_short_path,
        bgm_path=bgm_file,
        watermark_text=watermark,
        part_number=part_number,
        bgm_volume=0.065,
    )
    media_info = probe_media(final_short_path)
    duration = media_info.duration_sec
    generate_thumbnail(final_short_path, thumb_path, title)

    youtube_id = None
    if upload_requested:
        youtube_id = upload_movie_to_youtube(final_short_path, thumb_path, movie_data)
    status = RunStatus.UPLOADED.value if youtube_id else (
        RunStatus.RENDERED.value if not upload_requested else RunStatus.FAILED.value
    )
    video_hash = sha256_file(final_short_path)
    write_manifest(
        manifest_path,
        RenderManifest(
            movie_id=movie_id,
            title=title,
            part_number=part_number,
            total_parts=total_parts,
            language=lang,
            status=status,
            duration_sec=round(duration, 2),
            youtube_id=youtube_id,
            video_file=final_short_path.name,
            thumbnail_file=thumb_path.name,
            subtitle_file=subtitles_path.name,
            script_sha256=script_hash,
            video_sha256=video_hash,
            source_type=source_type,
            generated_at=utc_now(),
        ),
    )

    history_entry: dict[str, JsonValue] = {
        "movie_id": movie_id,
        "title": title,
        "part_number": part_number,
        "total_parts": total_parts,
        "youtube_id": youtube_id,
        "timestamp": utc_now(),
        "video_path": final_short_path.name,
        "duration_sec": round(duration, 2),
        "status": status,
        "content_hash": video_hash,
        "source_type": source_type,
    }
    append_history_entry(history, history_entry)
    if status == RunStatus.UPLOADED.value and part_number is not None and total_parts is not None:
        update_series_progress(
            history,
            movie_id,
            str(movie_data.get("series_title", title)),
            part_number,
            total_parts,
        )
    save_movie_history(HISTORY_FILE, history)

    for temporary_path in (audio_path, raw_trailer_path, sliced_video_path):
        temporary_path.unlink(missing_ok=True)

    log("=" * 65)
    log(f"🎬 PIPELINE COMPLETE! Rendered: {final_short_path}")
    if youtube_id:
        log(f"🔗 YouTube Short Live: https://youtube.com/shorts/{youtube_id}")
    log("=" * 65)

    if status == RunStatus.FAILED.value:
        raise UploadFailedError(title)
    return final_short_path


def main():
    parser = argparse.ArgumentParser(description="Movie Explanation YouTube Shorts Automation Pipeline")
    parser.add_argument("--movie", type=str, default=None, help="Name of movie to explain (e.g. 'The Avengers (2012)')")
    parser.add_argument("--part", type=int, default=None, help="Part number of multi-part movie series (e.g. 1, 2, 3)")
    parser.add_argument("--voice", type=str, default=None, help="Edge-TTS voice (default: en-US-ChristopherNeural)")
    parser.add_argument("--lang", type=str, choices=["en", "hi"], default="en", help="Language: 'en' or 'hi'")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="Render in the cloud without uploading to YouTube")
    mode_group.add_argument("--upload", action="store_true", help="Force upload to YouTube")
    parser.add_argument("--list", action="store_true", help="List catalog movies, multi-part series, and upload history")
    parser.add_argument("--video-file", type=str, default=None, help="Path to local high-res movie or scene pack MP4/MKV")
    parser.add_argument("--audio-file", type=str, default=None, help="Path to custom human-recorded voiceover MP3/WAV")
    parser.add_argument("--watermark", type=str, default=None, help="Channel watermark text (e.g. '@CinemaInsights')")
    args = parser.parse_args()

    if args.list:
        cat = load_catalog()
        hist = load_movie_history(HISTORY_FILE)
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
