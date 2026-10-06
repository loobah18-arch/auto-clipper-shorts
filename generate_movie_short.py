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

from movie_ai_script import (
    generate_movie_script_ai,
    load_catalog,
    get_movie_from_catalog,
    is_stale_critic_analysis_script,
)
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
    confirmed_part_durations,
    entry_is_uploaded,
    write_manifest,
)
from movie_quality import MAX_SHORT_DURATION, MediaValidationError, probe_media, validate_upload_source
from movie_video_engine import (
    generate_speech_audio,
    create_word_timestamps_from_sentences,
    generate_moviegyan_subtitles,
    get_audio_duration,
    get_bgm_offset_for_part,
    download_movie_from_gdrive,
    resolve_gdrive_source,
    resolve_part_window,
    slice_movie_timeline_scenes,
    download_movie_trailer,
    slice_trailer_dynamic_scenes,
    create_cinematic_movie_visual_fallback,
    render_movie_explanation_short,
    generate_thumbnail,
    resolve_non_copyright_bgm,
    detect_character_gender,
    resolve_character_voice,
    is_indian_movie,
    stitch_full_movie_video,
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
            if not matched:
                # Silently falling back to part 1 would re-publish an episode that
                # may already be live. Fail loudly and let auto-ingest catch up.
                raise EpisodeNotReadyError(
                    f"Part {part_num} of '{movie.get('title')}' has no generated script yet "
                    f"(only parts {[p.get('part_number') for p in parts]} are ready). "
                    "Auto-ingest adds more each run; re-run after it completes."
                )
            part = matched[0]

        p_num = part.get("part_number", 1)
        # Auto-ingested series are generated in batches, so len(parts) is only the
        # ready count. parts_available is the true series length and must drive
        # completion, or a long series would be marked finished after one batch.
        available = movie.get("parts_available")
        tot_parts = int(available) if isinstance(available, int) and available > 0 else len(parts)
        merged = dict(movie)
        # Inherit all part-specific attributes (gdrive_file_id, gdrive_file_name,
        # window_start_frac, window_end_frac, season, episode_number) so episodic
        # and auto-cataloged titles resolve their media source and timeline correctly.
        merged.update(part)
        merged.update({
            "part_number": p_num,
            "total_parts": tot_parts,
            "series_title": movie.get("title"),
            "title": part.get("title", f"{movie.get('title')} - Part {p_num}"),
            "badge": part.get("badge", f"{movie.get('badge', movie.get('title'))} • PART {p_num}"),
            "hook": part.get("hook", movie.get("hook", "")),
            "script": part.get("script", movie.get("script", "")),
            "tags": list(set(part.get("tags", []) + movie.get("tags", []))),
        })
        if "timeline_start" not in merged:
            merged["timeline_start"] = "00:01:00"
        if "timeline_end" not in merged:
            merged["timeline_end"] = "00:26:00"
        log(f"🎬 Resolved episodic part: '{merged['title']}' (Part {p_num}/{tot_parts}) [{merged['timeline_start']} -> {merged['timeline_end']}]")
        return merged

    # Case 1: Custom movie requested
    if requested_movie:
        log(f"🎯 Custom movie requested: '{requested_movie}'")
        cat_match = get_movie_from_catalog(requested_movie)
        if cat_match:
            return resolve_part(cat_match, requested_part)
        if lang == "en" and is_indian_movie(movie_data=cat_match, title=requested_movie):
            lang = "hi"
            log(f"🇮🇳 Indian movie requested ('{requested_movie}') ➔ Defaulted language to Hindi ('{lang}')")
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





class EpisodeNotReadyError(RuntimeError):
    """Raised when a requested episode exists on Drive but has no script yet."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadFailedError(RuntimeError):
    """Raised when publishing was requested but YouTube returned no video ID."""

    def __init__(self, title: str) -> None:
        self.title = title
        super().__init__(f"YouTube upload did not return a video ID for {title}")


def upload_movie_to_youtube(
    video_path: Path,
    thumb_path: Path,
    movie_data: dict,
    dry_run: bool = False,
    is_short: bool = True,
) -> str | None:
    """Uploads the movie explanation short or normal long video to YouTube."""
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
    series_display = movie_data.get("series_title", title_clean)
    year_str = f" ({movie_data.get('year')})" if movie_data.get("year") and str(movie_data.get("year")) not in title_clean else ""

    if not is_short:
        youtube_title = f"{series_display}{year_str} Full Movie Explained | Complete Story Recap"[:100]
        next_teaser = "👉 Comment which movie or web series you want explained next!"
        tags = list(dict.fromkeys(movie_data.get("tags", []) + [
            "movieexplained", "movierecap", "fullmovieexplained", "moviegyan", "movieinsighthindi", "endingexplained", "plottwist", "cinema", "storyrecap"
        ]))[:15]
        video_kind = "FULL MOVIE EXPLANATION & COMPLETE RECAP"
        hashtags = "#movieexplained #movierecap #fullmovieexplained #moviegyan #movieinsighthindi #endingexplained #plottwist #cinema #hollywood"
    elif part_num:
        youtube_title = f"{title_clean} Ending Explained 😱 #Shorts #MovieExplained"[:100]
        next_teaser = f"👉 Part {part_num + 1} coming next! Like & Subscribe so you don't miss it!" if (tot_parts and part_num < tot_parts) else "👉 Comment which movie you want explained next!"
        tags = list(dict.fromkeys(movie_data.get("tags", []) + [
            "shorts", "movieexplained", "movierecap", "moviegyan", "movieinsighthindi", "plottwist", "cinema"
        ]))[:15]
        video_kind = "MOVIE EXPLANATION & EPISODIC RECAP"
        hashtags = "#shorts #movieexplained #movierecap #moviegyan #movieinsighthindi #endingexplained #plottwist #cinema #hollywood"
    else:
        youtube_title = f"{title_clean}{year_str} Ending Explained 😱 #Shorts #MovieExplained"[:100]
        next_teaser = "👉 Comment which movie you want explained next!"
        tags = list(dict.fromkeys(movie_data.get("tags", []) + [
            "shorts", "movieexplained", "movierecap", "moviegyan", "movieinsighthindi", "plottwist", "cinema"
        ]))[:15]
        video_kind = "MOVIE EXPLANATION & STORY RECAP"
        hashtags = "#shorts #movieexplained #movierecap #moviegyan #movieinsighthindi #endingexplained #plottwist #cinema #hollywood"

    description = f"""{youtube_title}

🎬 {video_kind}
Film: {series_display}
Genre: {movie_data.get('genre', 'Action / Sci-Fi / Thriller')}

💡 Hook: {movie_data.get('hook', '')}

{next_teaser}

📌 Source and rights notice:
Narration and commentary are original to this channel. Visuals are sourced only from the configured authorized trailer or media source. Any underlying film and footage rights remain with their respective owners.

🔔 Subscribe to the channel for daily mind-blowing movie explanations, plot twists, and episodic recaps!

{hashtags}
"""

    upload_type_str = "Normal Video (16:9 / Full)" if not is_short else "Short (9:16)"
    log(f"🚀 Uploading {upload_type_str} to YouTube channel @woosclips: '{youtube_title}'...")
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
                "privacyStatus": os.environ.get("PRIVACY_STATUS") or "public",
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
        live_url = f"https://youtube.com/watch?v={vid_id}" if not is_short else f"https://youtube.com/shorts/{vid_id}"
        log(f"🎉 Successfully uploaded {upload_type_str}! URL: {live_url}")

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
    # Clean any leftover film-critic badges from older catalog entries
    if "CRITICAL" in badge_text.upper() or "ANALYSIS" in badge_text.upper():
        badge_text = f"{title.upper()[:14]} • P{part_number}" if part_number else "MOVIE RECAP"

    # Auto-detect Indian movie: automatically switch language to Hindi ('hi') if movie/series is Indian
    if lang == "en" and is_indian_movie(movie_data=movie_data, title=title, script_text=script_text):
        lang = "hi"
        log(f"🇮🇳 Indian movie detected ('{title}') ➔ Automatically activated Hindi narration ('{lang}') & MovieGyan style!")

    # Enforce pure story recap: if script is missing or is an academic film critique, generate a real recap via AI
    if not script_text or is_stale_critic_analysis_script(script_text):
        if is_stale_critic_analysis_script(script_text):
            log(f"⚠️ Stale film-critic analysis script detected for '{title}'. Discarding and generating pure story recap via AI...")
        else:
            log(f"🎙️ No pre-baked script for '{title}'. Generating pure story recap via AI...")

        ai_data = generate_movie_script_ai(
            movie_name=title,
            part=part_number,
            total_parts=total_parts,
            language=lang,
        )
        script_text = str(ai_data.get("script", ""))
        if ai_data.get("hook"):
            movie_data["hook"] = ai_data["hook"]
        if ai_data.get("badge") and ("CRITICAL" in badge_text.upper() or "ANALYSIS" in badge_text.upper()):
            badge_text = str(ai_data["badge"])

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
        log(f"🎙️ Story Recap Script ({len(script_text.split())} words): \"{script_text[:85]}...\"")

    # Character-aware voice selection: male or female voice matching protagonist
    character_gender = detect_character_gender(script_text, movie_data)
    if not voice:
        voice = os.environ.get("DEFAULT_VOICE") or resolve_character_voice(character_gender, language=lang)
    log(f"🎭 Character gender: '{character_gender.upper()}' ➔ Selected neural voice: '{voice}'")

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

    # Source priority:
    #   1. explicit --video-file (deliberate per-invocation override)
    #   2. Google Drive raw movie (highest automatic priority)
    #   3. configured trailer_url
    #   4. neutral motion background
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
    else:
        private_allowed = os.environ.get("ALLOW_PRIVATE_MEDIA_SOURCES", "false").lower() == "true"
        gdrive_source = resolve_gdrive_source(movie_id, movie_data)
        if private_allowed and gdrive_source:
            source_type = "private_media"
            target_movie_path = cache_dir / Path(gdrive_source["file_name"]).name
            log(f"📀 Sourcing raw movie from Google Drive (highest priority): {gdrive_source['file_name']}...")
            gdrive_ok = download_movie_from_gdrive(gdrive_source["file_id"], target_movie_path)
            if gdrive_ok and target_movie_path.exists():
                timeline_start = str(gdrive_source.get("timeline_start") or "")
                timeline_end = str(gdrive_source.get("timeline_end") or "")
                if not (timeline_start and timeline_end):
                    # Auto-generated entries carry fractional windows that only
                    # become real timestamps once the file is on disk.
                    timeline_start, timeline_end = resolve_part_window(
                        movie_data, target_movie_path
                    )
                sliced_ok = slice_movie_timeline_scenes(
                    target_movie_path,
                    timeline_start,
                    timeline_end,
                    duration,
                    sliced_video_path,
                    seed=render_seed,
                )
        elif gdrive_source and not private_allowed:
            log(
                "⚠️ Google Drive source found but ALLOW_PRIVATE_MEDIA_SOURCES is not 'true'; "
                "skipping Drive. Set the repo secret to enable it."
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
    bgm_file = resolve_non_copyright_bgm(configured_bgm if configured_bgm else None)

    # Continuous soundtrack: resume where the previous part's music ended.
    bgm_start_offset = 0.0
    if bgm_file is not None and part_number:
        # Only confirmed uploads advance the soundtrack; failed or dry-run
        # entries were never published and would desync the music timeline.
        prior = confirmed_part_durations(history, movie_id, part_number)
        track_duration = None
        try:
            track_duration = get_audio_duration(bgm_file)
        except Exception as error:  # noqa: BLE001
            log(f"⚠️ Could not probe BGM duration ({error}); using unwrapped offset.")
        bgm_start_offset = get_bgm_offset_for_part(
            part_number,
            prior_part_durations=prior,
            track_duration=track_duration,
        )
        log(
            f"🎵 Non-copyright BGM ({bgm_file.name}) resumes at {bgm_start_offset:.1f}s for part {part_number} "
            f"(continuous across {len(prior)} confirmed part(s))."
        )

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
        bgm_volume=0.28,
        bgm_start_offset=bgm_start_offset,
    )
    media_info = probe_media(final_short_path)
    duration = media_info.duration_sec
    generate_thumbnail(final_short_path, thumb_path, title)

    youtube_id = None
    if upload_requested:
        youtube_id = upload_movie_to_youtube(final_short_path, thumb_path, movie_data, is_short=True)
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

    # Cache part video so that once all parts are finished, they can be stitched into a full long video
    parts_cache_dir = OUTPUT_DIR / "parts_cache" / movie_id
    parts_cache_dir.mkdir(parents=True, exist_ok=True)
    if part_number is not None:
        cached_part_path = parts_cache_dir / f"part_{part_number}.mp4"
        shutil.copyfile(final_short_path, cached_part_path)
        log(f"💾 Cached Part {part_number} video for full series stitching: {cached_part_path.name}")

    # After all parts of a movie or web series have been explained, stitch and upload as a normal long video!
    if part_number is not None and total_parts is not None and part_number >= total_parts:
        log(f"🎉 Series '{movie_data.get('series_title', title)}' has reached its final part ({part_number}/{total_parts})!")
        stitch_and_upload_full_series(
            movie_id=movie_id,
            movie_data=movie_data,
            history=history,
            upload_requested=upload_requested,
            dry_run=dry_run,
        )

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


def stitch_and_upload_full_series(
    movie_id: str,
    movie_data: dict,
    history: dict,
    upload_requested: bool = False,
    dry_run: bool = False,
) -> Path | None:
    """Stitch all completed parts of a movie or webseries into a normal long video and upload it."""
    parts_cache_dir = OUTPUT_DIR / "parts_cache" / movie_id
    total_parts = movie_data.get("total_parts", 1)
    series_title = movie_data.get("series_title", movie_data.get("title", "Movie Explained"))

    available_parts = []
    for p_i in range(1, total_parts + 1):
        p_path = parts_cache_dir / f"part_{p_i}.mp4"
        if p_path.exists():
            available_parts.append(p_path)

    if len(available_parts) < total_parts:
        log(f"⚠️ Cannot stitch full video yet: Found {len(available_parts)}/{total_parts} parts in {parts_cache_dir}.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_video_path = OUTPUT_DIR / f"{movie_id}_full_explained_{timestamp}.mp4"
    full_thumb_path = OUTPUT_DIR / f"thumb_{movie_id}_full_{timestamp}.jpg"

    log(f"🎬 Stitching all {total_parts} parts into Full Long Video: {full_video_path.name}...")
    stitch_ok = stitch_full_movie_video(available_parts, full_video_path)
    if not stitch_ok or not full_video_path.exists():
        log("❌ Stitching full video failed.")
        return None

    generate_thumbnail(full_video_path, full_thumb_path, f"{series_title} Full Movie Explained")
    media_info = probe_media(full_video_path, is_long_video=True)

    full_movie_data = dict(movie_data)
    full_movie_data["title"] = f"{series_title} Full Movie Explained | Complete Story Recap"
    full_movie_data["part_number"] = None
    full_movie_data["is_full_video"] = True

    full_youtube_id = None
    if upload_requested:
        log(f"🚀 Uploading Normal Long Video to YouTube channel @woosclips: '{full_movie_data['title']}'...")
        full_youtube_id = upload_movie_to_youtube(
            full_video_path,
            full_thumb_path,
            full_movie_data,
            dry_run=dry_run,
            is_short=False,
        )

    full_status = RunStatus.UPLOADED.value if full_youtube_id else (
        RunStatus.RENDERED.value if not upload_requested else RunStatus.FAILED.value
    )
    full_entry: dict[str, JsonValue] = {
        "movie_id": movie_id,
        "title": full_movie_data["title"],
        "part_number": None,
        "total_parts": total_parts,
        "is_full_video": True,
        "youtube_id": full_youtube_id,
        "timestamp": utc_now(),
        "video_path": full_video_path.name,
        "duration_sec": round(media_info.duration_sec, 2),
        "status": full_status,
        "content_hash": sha256_file(full_video_path),
        "source_type": "stitched_series",
    }
    append_history_entry(history, full_entry)
    save_movie_history(HISTORY_FILE, history)
    log(f"🌟 Full Normal Long Video completed successfully: {full_video_path.name} ({media_info.duration_sec:.1f}s)")
    if full_youtube_id:
        log(f"🔗 YouTube Normal Video URL: https://youtube.com/watch?v={full_youtube_id}")
    return full_video_path


def main():
    parser = argparse.ArgumentParser(description="Movie Explanation YouTube Shorts Automation Pipeline")
    parser.add_argument("--movie", type=str, default=None, help="Name of movie to explain (e.g. 'The Avengers (2012)')")
    parser.add_argument("--part", type=int, default=None, help="Part number of multi-part movie series (e.g. 1, 2, 3)")
    parser.add_argument("--voice", type=str, default=None, help="Edge-TTS voice (default: en-US-AvaNeural natural conversational female)")
    parser.add_argument("--lang", type=str, choices=["en", "hi"], default="en", help="Language: 'en' or 'hi'")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="Render in the cloud without uploading to YouTube")
    mode_group.add_argument("--upload", action="store_true", help="Force upload to YouTube")
    parser.add_argument("--list", action="store_true", help="List catalog movies, multi-part series, and upload history")
    parser.add_argument("--video-file", type=str, default=None, help="Path to local high-res movie or scene pack MP4/MKV")
    parser.add_argument("--audio-file", type=str, default=None, help="Path to custom human-recorded voiceover MP3/WAV")
    parser.add_argument("--watermark", type=str, default=None, help="Channel watermark text (e.g. '@CinemaInsights')")
    parser.add_argument("--stitch-series", type=str, default=None, help="Movie ID to stitch all cached parts into a Full Long Video")
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

    if args.stitch_series:
        cat_match = get_movie_from_catalog(args.stitch_series)
        hist = load_movie_history(HISTORY_FILE)
        if not cat_match:
            log(f"❌ Movie '{args.stitch_series}' not found in catalog.")
            return
        m_id = str(cat_match.get("id", args.stitch_series))
        stitch_and_upload_full_series(
            movie_id=m_id,
            movie_data=cat_match,
            history=hist,
            upload_requested=args.upload,
            dry_run=args.dry_run,
        )
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
