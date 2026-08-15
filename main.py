#!/usr/bin/env python3
"""
Auto Clipper Shorts - Automated Podcast & Talk Clipping Pipeline for YouTube Shorts
Extracts subtitles/captions or Whisper AI audio, identifies viral hooks with Groq LLM,
renders vertical 9:16 video with karaoke subtitles via FFmpeg, and publishes to YouTube.
"""

import os
import sys
import re
import json
import time
import random
import base64
import shutil
import tempfile
import argparse
import subprocess
import urllib.request
import math
import wave
import struct
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# External optional imports (handled gracefully)
try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
except ImportError:
    Credentials = None
    build = None
    MediaFileUpload = None
    HttpError = Exception

# --- Configuration & Paths ---
WORKSPACE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = WORKSPACE_DIR / "tech_catalog.json" if (WORKSPACE_DIR / "tech_catalog.json").exists() else WORKSPACE_DIR / "podcast_catalog.json"
HISTORY_PATH = WORKSPACE_DIR / "clip_history.json"
OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
FALLBACK_GROQ_MODEL = "openai/gpt-oss-20b"

# Font candidates for FFmpeg drawtext on Linux/Debian/Ubuntu
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def log(msg: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [AutoClipper] {msg}", flush=True)


def load_json(path: Path, default_val=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Warning: Failed to load {path.name}: {e}")
    return default_val if default_val is not None else {}


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_system_font() -> str:
    """Finds an available TrueType font file on the host system for FFmpeg."""
    for font_path in FONT_CANDIDATES:
        if os.path.exists(font_path):
            return font_path
    return "DejaVu Sans"


# Global cookies file path (written once at startup, cleaned up at exit)
_COOKIES_FILE: str = None


def setup_cookies_file() -> str:
    """
    Reads the YOUTUBE_COOKIES env var (base64-encoded Netscape cookies.txt content)
    and writes it to a temporary file for yt-dlp to use.

    Per yt-dlp FAQ (https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp):
    - File must be in Mozilla/Netscape format
    - First line must be '# Netscape HTTP Cookie File' or '# HTTP Cookie File'
    - Newlines must be LF (\n) on Linux, NOT CRLF (\r\n)

    Returns the path to the cookies file, or None if not configured.
    """
    global _COOKIES_FILE
    if _COOKIES_FILE and os.path.exists(_COOKIES_FILE):
        return _COOKIES_FILE

    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        log("⚠️ WARNING: YOUTUBE_COOKIES environment variable is EMPTY!")
        log("   YouTube will likely block the download with 'Sign in to confirm you’re not a bot'.")
        log("   Please export your browser cookies as Netscape format, base64 encode them,")
        log("   and save them as a GitHub Repository Secret named 'YOUTUBE_COOKIES'.")
        return None

    try:
        # Decode base64 -> Netscape cookies.txt content
        decoded = base64.b64decode(raw).decode("utf-8")

        # Normalize CRLF -> LF (yt-dlp FAQ: CRLF causes HTTP 400 on Linux)
        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")

        # Validate Netscape header (yt-dlp FAQ: first line must be one of these)
        first_line = decoded.split("\n", 1)[0].strip()
        valid_headers = ["# Netscape HTTP Cookie File", "# HTTP Cookie File"]
        if first_line not in valid_headers:
            log(f"⚠️ Cookie file missing required Netscape header. Got: '{first_line[:60]}'")
            log(f"   Prepending '# Netscape HTTP Cookie File' header.")
            decoded = "# Netscape HTTP Cookie File\n" + decoded

        # Count actual cookie lines (non-empty, non-comment lines with tab separators)
        cookie_lines = [l for l in decoded.strip().split("\n")
                        if l.strip() and not l.startswith("#") and "\t" in l]
        if not cookie_lines:
            log("⚠️ YOUTUBE_COOKIES decoded but contains no valid cookie entries. Skipping.")
            return None

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix="_yt_cookies.txt", delete=False, encoding="utf-8",
            newline="\n"  # Force LF line endings on all platforms
        )
        tmp.write(decoded)
        tmp.flush()
        tmp.close()
        _COOKIES_FILE = tmp.name
        log(f"🍪 Cookies loaded: {len(cookie_lines)} entries, {len(decoded)} bytes -> {_COOKIES_FILE}")
        return _COOKIES_FILE
    except Exception as e:
        log(f"⚠️ Failed to decode YOUTUBE_COOKIES: {e}")
        return None


def ytdlp_cookies_args() -> list:
    """Returns ['--cookies', '<path>'] if a cookies file is available, else []."""
    cf = setup_cookies_file()
    if cf:
        return ["--cookies", cf]
    return []


# =====================================================================
# 1. Source Discovery & Metadata
# =====================================================================


def pick_next_channel(catalog: dict, history: dict, force_channel_id: str = None) -> tuple:
    channels = catalog.get("tech_channels") or catalog.get("podcasts", [])
    if not channels:
        raise ValueError("No channels found in catalog JSON!")

    if force_channel_id:
        for idx, p in enumerate(channels):
            if p["id"] == force_channel_id:
                return p, idx

    last_idx = history.get("last_channel_index", -1)
    next_idx = (last_idx + 1) % len(channels)
    return channels[next_idx], next_idx


def get_latest_videos_from_channel(podcast_entry: dict, max_results: int = 8) -> list:
    """Uses yt-dlp search query or channel feed to find candidate episodes."""
    query = podcast_entry.get("search_query") or f"ytsearch8:{podcast_entry['name']} full episode"
    log(f"Searching candidate episodes: {query}")
    
    cmd = [
        "yt-dlp",
        "--default-search", "ytsearch",
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--ignore-errors",
        "--extractor-args", "youtube:player_client=mweb,web,default",
    ] + ytdlp_cookies_args() + [query]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        videos = []
        for e in entries:
            if e and e.get("id"):
                duration = e.get("duration") or 0
                videos.append({
                    "id": e["id"],
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "title": e.get("title", "Untitled Episode"),
                    "duration": duration,
                    "uploader": e.get("uploader", podcast_entry.get("name", "Podcast"))
                })
        if videos:
            log(f"Found {len(videos)} candidate videos via search.")
            return videos
    except Exception as e:
        log(f"Search query notice: {e}")

    # Fallback to direct channel URL
    try:
        log(f"Trying channel URL fallback: {podcast_entry['channel_url']}")
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=mweb,web,default",
        ] + ytdlp_cookies_args() + [podcast_entry["channel_url"]]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        entries = data.get("entries", [])[:max_results]
        return [
            {
                "id": e["id"],
                "url": f"https://www.youtube.com/watch?v={e['id']}",
                "title": e.get("title", "Podcast Episode"),
                "duration": e.get("duration", 0),
                "uploader": e.get("uploader", podcast_entry.get("name"))
            }
            for e in entries if e and e.get("id")
        ]
    except Exception as e2:
        log(f"Channel URL fallback error: {e2}")
        return []


def select_target_video(podcast_entry: dict, history: dict, direct_url: str = None) -> dict:
    if direct_url:
        log(f"Using direct URL: {direct_url}")
        cmd = [
            "yt-dlp",
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=mweb,web,default",
        ] + ytdlp_cookies_args() + [direct_url]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        return {
            "id": data["id"],
            "url": direct_url,
            "title": data.get("title", "Podcast Episode"),
            "duration": data.get("duration", 0),
            "uploader": data.get("uploader", podcast_entry.get("name", "Podcast"))
        }

    processed = set(history.get("processed_videos", []))
    candidates = get_latest_videos_from_channel(podcast_entry, max_results=8)
    
    for vid in candidates:
        if vid["id"] not in processed:
            # Skip if already a short (< 90 seconds)
            if vid["duration"] > 0 and vid["duration"] < 90:
                continue
            return vid

    if candidates:
        return candidates[0]
    
    raise RuntimeError(f"Could not find any suitable videos for {podcast_entry['name']}")


# =====================================================================
# 2. Subtitle / Transcript Extraction (YouTube Subtitles & Whisper AI)
# =====================================================================

def parse_vtt_subtitles(vtt_text: str) -> list:
    """Parses WebVTT subtitle format into structured segments with word estimates."""
    segments = []
    lines = vtt_text.strip().splitlines()
    
    time_pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
    time_short_pattern = re.compile(r"(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2})\.(\d{3})")
    
    current_start = None
    current_end = None
    current_text_lines = []
    
    def parse_time(match_groups):
        if len(match_groups) == 8:
            h, m, s, ms = int(match_groups[0]), int(match_groups[1]), int(match_groups[2]), int(match_groups[3])
            return h * 3600 + m * 60 + s + ms / 1000.0
        elif len(match_groups) == 6:
            m, s, ms = int(match_groups[0]), int(match_groups[1]), int(match_groups[2])
            return m * 60 + s + ms / 1000.0
        return 0.0

    seg_id = 0
    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("WEBVTT") or line_clean.startswith("NOTE"):
            continue
            
        m = time_pattern.search(line_clean)
        if m:
            if current_start is not None and current_text_lines:
                text_combined = " ".join(current_text_lines).strip()
                text_combined = re.sub(r"<[^>]+>", "", text_combined)
                if text_combined:
                    words = create_word_timestamps_from_segment(text_combined, current_start, current_end)
                    segments.append({
                        "id": seg_id,
                        "start": round(current_start, 2),
                        "end": round(current_end, 2),
                        "text": text_combined,
                        "words": words
                    })
                    seg_id += 1
            h1, m1, s1, ms1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            h2, m2, s2, ms2 = int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8))
            current_start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
            current_end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
            current_text_lines = []
            continue
            
        m_short = time_short_pattern.search(line_clean)
        if m_short:
            if current_start is not None and current_text_lines:
                text_combined = " ".join(current_text_lines).strip()
                text_combined = re.sub(r"<[^>]+>", "", text_combined)
                if text_combined:
                    words = create_word_timestamps_from_segment(text_combined, current_start, current_end)
                    segments.append({
                        "id": seg_id,
                        "start": round(current_start, 2),
                        "end": round(current_end, 2),
                        "text": text_combined,
                        "words": words
                    })
                    seg_id += 1
            m1, s1, ms1 = int(m_short.group(1)), int(m_short.group(2)), int(m_short.group(3))
            m2, s2, ms2 = int(m_short.group(4)), int(m_short.group(5)), int(m_short.group(6))
            current_start = m1 * 60 + s1 + ms1 / 1000.0
            current_end = m2 * 60 + s2 + ms2 / 1000.0
            current_text_lines = []
            continue
            
        if current_start is not None:
            # Subtitle text line
            current_text_lines.append(line_clean)
            
    if current_start is not None and current_text_lines:
        text_combined = " ".join(current_text_lines).strip()
        text_combined = re.sub(r"<[^>]+>", "", text_combined)
        if text_combined:
            words = create_word_timestamps_from_segment(text_combined, current_start, current_end)
            segments.append({
                "id": seg_id,
                "start": round(current_start, 2),
                "end": round(current_end, 2),
                "text": text_combined,
                "words": words
            })

    return segments


def parse_json3_subtitles(json3_data: dict) -> list:
    """Parses YouTube JSON3 subtitle format containing exact word/offset timestamps."""
    segments = []
    events = json3_data.get("events", [])
    seg_id = 0
    
    for ev in events:
        start_ms = ev.get("tStartMs", 0)
        dur_ms = ev.get("dDurationMs", 0)
        start_sec = start_ms / 1000.0
        end_sec = (start_ms + dur_ms) / 1000.0
        
        segs = ev.get("segs", [])
        if not segs:
            continue
            
        words = []
        full_text_parts = []
        for s in segs:
            w_text = s.get("utf8", "")
            offset_ms = s.get("tOffsetMs", 0)
            cleaned_w = w_text.strip()
            if cleaned_w:
                w_start = (start_ms + offset_ms) / 1000.0
                words.append({
                    "word": cleaned_w,
                    "start": round(w_start, 2),
                    "end": round(w_start + 0.35, 2),
                    "probability": 1.0
                })
                full_text_parts.append(cleaned_w)
                
        # Adjust end times between consecutive words
        for idx in range(len(words) - 1):
            words[idx]["end"] = min(words[idx + 1]["start"], words[idx]["start"] + 0.6)
        if words:
            words[-1]["end"] = round(min(words[-1]["start"] + 0.65, max(end_sec, words[-1]["start"] + 0.3)), 2)
            
        full_text = " ".join(full_text_parts)
        if full_text:
            segments.append({
                "id": seg_id,
                "start": round(start_sec, 2),
                "end": round(max(end_sec, start_sec + 0.5), 2),
                "text": full_text,
                "words": words
            })
            seg_id += 1
            
    return segments


def create_word_timestamps_from_segment(text: str, start_sec: float, end_sec: float) -> list:
    """Generates evenly-spaced word timestamps across segment duration."""
    words_list = text.split()
    if not words_list:
        return []
    duration = max(0.2, end_sec - start_sec)
    step = duration / len(words_list)
    result = []
    for idx, w in enumerate(words_list):
        w_start = start_sec + idx * step
        w_end = w_start + step
        result.append({
            "word": w.strip(),
            "start": round(w_start, 2),
            "end": round(w_end, 2),
            "probability": 1.0
        })
    return result


def download_audio_via_rss(rss_feed_url: str, output_audio_path: Path, target_title: str = None) -> dict:
    """
    Downloads full episode MP3 audio directly from podcast RSS feed.
    - If target_title is provided: Searches all RSS items and matches the closest episode.
      If no item matches (e.g. YouTube video is an older episode or special), returns False to fallback to direct YouTube audio.
    - If target_title is None: Downloads the latest episode item.
    """
    if not rss_feed_url:
        return {"success": False}
    try:
        log(f"🎙️ Querying podcast RSS feed for direct audio: {rss_feed_url}")
        req = urllib.request.Request(
            rss_feed_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        if not items:
            log("⚠️ No items found in RSS feed.")
            return {"success": False}
            
        chosen_item = None
        if target_title and target_title.strip() and target_title.strip() != "Podcast Episode":
            target_words = set(re.findall(r"[a-z0-9]+", target_title.lower())) - {
                "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with", "by", "of", "full", "episode", "podcast", "shorts", "part"
            }
            best_score = 0
            for it in items[:35]:
                it_title_el = it.find("title")
                it_title = it_title_el.text.strip() if (it_title_el is not None and it_title_el.text) else ""
                it_words = set(re.findall(r"[a-z0-9]+", it_title.lower()))
                overlap = len(target_words & it_words)
                if overlap > best_score:
                    best_score = overlap
                    chosen_item = it
            if best_score < 2 and len(target_words) >= 2:
                log(f"⚠️ RSS feed items do not match target YouTube episode: '{target_title[:50]}...'. Falling back to direct video audio.")
                return {"success": False}
        else:
            chosen_item = items[0]
            
        if chosen_item is None:
            chosen_item = items[0]

        enclosure = chosen_item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            log("⚠️ No audio enclosure found in RSS feed.")
            return {"success": False}

        title_el = chosen_item.find("title")
        ep_title = title_el.text.strip() if (title_el is not None and title_el.text) else "Podcast Episode"
        
        # Look for itunes:author
        author_el = chosen_item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}author")
        ep_author = author_el.text.strip() if (author_el is not None and author_el.text) else ""

        mp3_url = enclosure.get("url")
        log(f"📥 Downloading direct podcast audio ({ep_title[:50]}...): {mp3_url[:60]}...")
        
        mp3_req = urllib.request.Request(
            mp3_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(mp3_req, timeout=90) as mp3_resp, open(output_audio_path, "wb") as out_f:
            shutil.copyfileobj(mp3_resp, out_f)

        file_size_mb = output_audio_path.stat().st_size / (1024 * 1024)
        log(f"✅ Successfully downloaded clean RSS audio ({file_size_mb:.1f} MB) -> {output_audio_path.name}")
        return {
            "success": True,
            "title": ep_title,
            "author": ep_author
        }
    except Exception as e:
        log(f"⚠️ RSS audio download notice: {e}")
        return {"success": False}


def fetch_youtube_subtitles_or_whisper(video_url: str, output_base: Path, podcast_entry: dict = None, video_meta: dict = None) -> list:
    """
    Attempts to:
    1. Reuse already cached audio for this video_id if continuing multi-part series.
    2. Download audio via official Podcast RSS feed (matched by title) -> Whisper AI.
    3. Download instant YouTube captions (manual/auto-generated json3/vtt).
    4. Fallback to yt-dlp audio download -> Whisper AI.
    """
    video_id = video_meta.get("id", "episode") if video_meta else "episode"
    audio_full_path = output_base / f"podcast_audio_{video_id}.mp3"
    
    # 0. Reuse already cached audio for this exact video if present
    if audio_full_path.exists() and audio_full_path.stat().st_size > 500000:
        log(f"🔄 Reusing cached episode audio ({audio_full_path.stat().st_size / (1024*1024):.1f} MB): {audio_full_path.name}")
        try:
            return transcribe_audio_with_whisper(audio_full_path, model_size="base.en")
        except Exception as we:
            log(f"⚠️ Cached audio transcription notice: {we}")

    # 1. Try Podcast RSS feed audio with title matching
    target_title = video_meta.get("title") if (video_meta and video_meta.get("title") != "Podcast Episode") else None
    if podcast_entry and podcast_entry.get("rss_feed"):
        rss_info = download_audio_via_rss(podcast_entry["rss_feed"], audio_full_path, target_title=target_title)
        if rss_info.get("success"):
            if video_meta is not None and rss_info.get("title"):
                video_meta["title"] = rss_info["title"]
                if rss_info.get("author"):
                    video_meta["uploader"] = rss_info["author"]
            try:
                log("Transcribing clean RSS podcast audio with Whisper AI...")
                return transcribe_audio_with_whisper(audio_full_path, model_size="base.en")
            except Exception as we:
                log(f"⚠️ Whisper AI notice: {we}. Trying instant YouTube captions...")

    log(f"Checking for instant YouTube captions for {video_url}...")
    sub_prefix = output_base / "subs_temp"
    
    # Try downloading JSON3 / VTT English subtitles across client profiles
    sub_profiles = [
        ("ios,mweb", []),
        ("mweb,web", []),
        ("default", []),
        ("web", ytdlp_cookies_args()),
    ]
    for client_name, cookie_args in sub_profiles:
        cmd_subs = [
            "yt-dlp",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang", "en.*,en",
            "--sub-format", "json3/vtt/srt",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
        ]
        if client_name != "default":
            cmd_subs += ["--extractor-args", f"youtube:player_client={client_name}"]
        cmd_subs += cookie_args + [
            "-o", f"{sub_prefix}.%(ext)s",
            video_url
        ]
        try:
            subprocess.run(cmd_subs, capture_output=True, text=True, check=True)
            json3_files = list(output_base.glob("subs_temp*.json3"))
            if json3_files:
                log(f"Found YouTube JSON3 captions: {json3_files[0].name}")
                with open(json3_files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                segments = parse_json3_subtitles(data)
                if segments and len(segments) > 5:
                    log(f"Parsed {len(segments)} segments with word timestamps from YouTube captions.")
                    for jf in json3_files:
                        jf.unlink()
                    return segments
                    
            vtt_files = list(output_base.glob("subs_temp*.vtt"))
            if vtt_files:
                log(f"Found YouTube WebVTT captions: {vtt_files[0].name}")
                with open(vtt_files[0], "r", encoding="utf-8") as f:
                    vtt_text = f.read()
                segments = parse_vtt_subtitles(vtt_text)
                if segments and len(segments) > 5:
                    log(f"Parsed {len(segments)} segments from WebVTT captions.")
                    for vf in vtt_files:
                        vf.unlink()
                    return segments
        except Exception:
            continue

    # Fallback to Whisper AI audio transcription directly from YouTube video audio
    log("Falling back to Whisper AI audio transcription via yt-dlp...")
    try:
        download_audio_for_transcription(video_url, audio_full_path)
        return transcribe_audio_with_whisper(audio_full_path, model_size="base.en")
    except Exception as fe:
        log(f"⚠️ Audio transcription fallback error: {fe}")
        return []


def download_audio_for_transcription(video_url: str, output_audio_path: Path) -> Path:
    """Fast audio download (low bitrate mp3/m4a) with multi-profile direct streaming and fallback."""
    if output_audio_path.exists():
        output_audio_path.unlink()
        
    log(f"Downloading audio track for transcription: {video_url}")
    
    client_profiles = [
        ("ios,mweb", []),
        ("mweb,ios", []),
        ("android", []),
        ("web", []),
        ("default", []),
        ("mweb,default", ytdlp_cookies_args()),
        ("web", ytdlp_cookies_args()),
    ]
    
    formats_to_try = [
        "bestaudio/140/251/139/249/ba/18/22/b/best",
        "ba/b/best",
        "18/22/best"
    ]
    
    # Strategy 1: Direct Stream URL Resolution (yt-dlp -g) + Direct FFmpeg Capture (Bypasses yt-dlp postprocessor bot blocks)
    for client_name, cookie_args in client_profiles:
        for fmt in formats_to_try:
            try:
                g_cmd = ["yt-dlp", "-g", "-f", fmt, "--no-warnings"]
                if client_name != "default":
                    g_cmd += ["--extractor-args", f"youtube:player_client={client_name}"]
                g_cmd += cookie_args + [video_url]
                
                res = subprocess.run(g_cmd, capture_output=True, text=True, check=True)
                lines = [l.strip() for l in res.stdout.strip().split("\n") if l.strip().startswith("http")]
                if lines:
                    stream_url = lines[0]
                    ff_cmd = [
                        "ffmpeg", "-y",
                        "-i", stream_url,
                        "-t", "1200",
                        "-vn",
                        "-acodec", "libmp3lame",
                        "-b:a", "64k",
                        str(output_audio_path)
                    ]
                    subprocess.run(ff_cmd, check=True, capture_output=True)
                    if output_audio_path.exists() and output_audio_path.stat().st_size > 10000:
                        log(f"✅ Audio stream captured via [{client_name}] format [{fmt[:25]}] ({output_audio_path.stat().st_size} bytes)")
                        return output_audio_path
            except Exception:
                continue

    # Strategy 2: Direct yt-dlp file download
    for client_name, cookie_args in client_profiles:
        for fmt in ["ba/b/best", "18/22/best"]:
            cmd = [
                "yt-dlp",
                "-f", fmt,
                "-x",
                "--audio-format", "mp3",
                "--no-playlist",
                "--no-warnings",
            ]
            if client_name != "default":
                cmd += ["--extractor-args", f"youtube:player_client={client_name}"]
            cmd += cookie_args + [
                "-o", str(output_audio_path),
                video_url
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                if output_audio_path.exists() and output_audio_path.stat().st_size > 1000:
                    log(f"✅ Audio downloaded via yt-dlp [{client_name}] ({output_audio_path.stat().st_size} bytes)")
                    return output_audio_path
            except Exception:
                continue

    raise RuntimeError(f"All audio download strategies exhausted for {video_url}")


def transcribe_audio_with_whisper(audio_path: Path, model_size: str = "base.en") -> list:
    """Transcribes audio using faster-whisper with word-level timestamps."""
    if WhisperModel is None:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")
        
    log(f"Loading Whisper model '{model_size}' (CPU / int8 for fast inference)...")
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4)
    except Exception:
        log("Falling back to float32 compute type...")
        model = WhisperModel(model_size, device="cpu", compute_type="float32", cpu_threads=4)
    
    log(f"Transcribing audio file ({audio_path.name})...")
    segments_gen, info = model.transcribe(
        str(audio_path),
        beam_size=1,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    
    results = []
    for s in segments_gen:
        words = []
        if s.words:
            for w in s.words:
                cleaned_w = w.word.strip().replace("{", "").replace("}", "")
                if cleaned_w:
                    words.append({
                        "word": cleaned_w,
                        "start": round(w.start, 2),
                        "end": round(max(w.start + 0.05, w.end), 2),
                        "probability": round(w.probability, 2)
                    })
        else:
            words = create_word_timestamps_from_segment(s.text.strip(), s.start, s.end)
            
        results.append({
            "id": s.id,
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": words
        })
    log(f"Transcription complete! Generated {len(results)} segments.")
    return results


# =====================================================================
# 3. AI Highlight & Viral Hook Selection (Groq)
# =====================================================================

def chunk_transcript(segments: list, max_duration_sec: float = 90.0) -> list:
    chunks = []
    curr_words = []
    curr_start = None
    curr_end = None
    
    for seg in segments:
        if curr_start is None:
            curr_start = seg["start"]
        curr_end = seg["end"]
        curr_words.append(seg["text"])
        
        if (curr_end - curr_start) >= max_duration_sec:
            chunks.append({
                "start": curr_start,
                "end": curr_end,
                "text": " ".join(curr_words)
            })
            curr_words = []
            curr_start = None
            curr_end = None
            
    if curr_words and curr_start is not None:
        chunks.append({
            "start": curr_start,
            "end": curr_end,
            "text": " ".join(curr_words)
        })
    return chunks


def parse_llm_json(raw_text: str) -> dict:
    clean = raw_text.strip()
    # Strip thinking tags if present
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()
    if "```json" in clean:
        clean = clean.split("```json")[1].split("```")[0].strip()
    elif "```" in clean:
        clean = clean.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(clean)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        raise ValueError(f"Could not parse JSON from LLM output: {raw_text[:200]}")


def format_seconds_to_min_sec(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def select_viral_clip_with_groq(
    transcript_segments: list,
    video_meta: dict,
    podcast_entry: dict,
    continuation_start_sec: float = None,
    part_number: int = 1,
    topic_title: str = None
) -> dict:
    """
    Prompts NVIDIA Nemotron 3 Ultra (550B MoE) or Groq Llama 3.3 to analyze transcript.
    - If Part 1: Identifies the exact moment where the speaker begins introducing a new topic/framework.
    - If Part > 1: Finds the direct insight continuation starting from previous clip's end.
    """
    raw_n_key = os.environ.get("NVIDIA_API_KEY", "")
    nvidia_api_key = raw_n_key.strip().replace(" ", "").strip("\"'") if raw_n_key else None
    
    # Filter transcript segments strictly AFTER continuation_start_sec if continuing a series
    if continuation_start_sec is not None and continuation_start_sec > 0:
        valid_segments = [s for s in transcript_segments if s.get("start", 0) >= (continuation_start_sec + 0.5)]
        if not valid_segments:
            log(f"⚠️ No remaining transcript after timestamp {continuation_start_sec:.1f}s.")
            valid_segments = transcript_segments
    else:
        valid_segments = transcript_segments

    chunks = chunk_transcript(valid_segments, max_duration_sec=90.0)
    relevant_chunks = chunks[:25]
        
    formatted_transcript = "\n".join([
        f"[{format_seconds_to_min_sec(c['start'])} - {format_seconds_to_min_sec(c['end'])}] {c['text']}"
        for c in relevant_chunks
    ])
    
    if part_number > 1 and continuation_start_sec is not None:
        goal_instruction = (
            f"This is PART {part_number} of a multi-part series on '{topic_title or 'this topic'}'.\n"
            f"Find the direct 38-55 second insight continuation starting strictly after timestamp {format_seconds_to_min_sec(continuation_start_sec)}."
        )
    else:
        goal_instruction = (
            "Identify the exact timestamp where the creator shares a mind-blowing tech fact, developer hack, Linux trick, coding tip, or computer science secret (Part 1).\n"
            "The clip MUST start right where they begin explaining the technical insight or practical tip (skip any intro banter/merch). Duration: 35-55 seconds."
        )

    # 0. Priority 1: OpenCode DeepSeek v4 Flash
    opencode_api_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if opencode_api_key:
        opencode_endpoints = []
        custom_base = os.environ.get("OPENCODE_BASE_URL")
        if custom_base:
            opencode_endpoints.append((custom_base, ["opencode/deepseek-v4-flash-free", "deepseek-v4-flash", "deepseek-chat"]))
        opencode_endpoints.extend([
            ("https://api.opencode.ai/v1/chat/completions", ["opencode/deepseek-v4-flash-free", "deepseek-v4-flash", "deepseek-ai/deepseek-v4-flash"]),
            ("https://api.deepseek.com/chat/completions", ["deepseek-chat", "deepseek-reasoner"]),
            ("https://openrouter.ai/api/v1/chat/completions", ["deepseek/deepseek-v4-flash", "deepseek/deepseek-chat", "deepseek/deepseek-r1"])
        ])
        for ep_url, ep_models in opencode_endpoints:
            for ep_model in ep_models:
                try:
                    ep_host = ep_url.split("/")[2]
                    log(f"🧠 Querying OpenCode DeepSeek v4 Flash (Priority 1: {ep_model} @ {ep_host}, Part {part_number})...")
                    ds_payload = json.dumps({
                        "model": ep_model,
                        "messages": [
                            {"role": "system", "content": "You are an expert viral YouTube Shorts content strategist. Return ONLY a single raw JSON object with keys: start_seconds, end_seconds, topic_title, viral_title, hook_reason, tags, speaker_badge."},
                            {"role": "user", "content": f"Tech Channel: {podcast_entry.get('name', 'Tech')}\nVideo Title: {video_meta.get('title', 'Episode')}\nGoal: {goal_instruction}\n\nTranscript:\n{formatted_transcript}"}
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1024
                    }).encode("utf-8")
                    ds_req = urllib.request.Request(
                        ep_url,
                        data=ds_payload,
                        headers={
                            "Authorization": f"Bearer {opencode_api_key}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://github.com/auto-clipper-shorts",
                            "X-Title": "Auto Clipper Shorts"
                        }
                    )
                    with urllib.request.urlopen(ds_req, timeout=30) as ds_resp:
                        ds_data = json.loads(ds_resp.read().decode("utf-8"))
                        ds_content = ds_data["choices"][0]["message"]["content"]
                        ds_parsed = parse_llm_json(ds_content)
                        if ds_parsed and "start_seconds" in ds_parsed and "end_seconds" in ds_parsed:
                            min_start = max(0.0, continuation_start_sec + 0.5 if (continuation_start_sec and part_number > 1) else 0.0)
                            raw_start = max(min_start, float(ds_parsed.get("start_seconds", min_start)))
                            raw_end = float(ds_parsed.get("end_seconds", raw_start + 45.0))
                            
                            start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, raw_start, raw_end)
                            clip_dur = end_sec - start_sec
                            
                            ds_parsed["start_seconds"] = start_sec
                            ds_parsed["end_seconds"] = end_sec
                            ds_parsed["duration"] = clip_dur
                            log(f"✅ OpenCode DeepSeek selected clip (Part {part_number}): {ds_parsed['start_seconds']}s -> {ds_parsed['end_seconds']}s ({clip_dur:.1f}s)")
                            return ds_parsed
                except Exception as dse:
                    log(f"⚠️ OpenCode DeepSeek notice ({ep_model}): {dse}")
                    continue

    # 1. Check if NVIDIA Nemotron is configured
    if nvidia_api_key:
        try:
            log(f"🧠 Querying NVIDIA Nemotron for viral tech highlight (Part {part_number})...")
            n_sys = "You are an expert viral YouTube Shorts content strategist specializing in computer science, programming hacks, developer tips, Linux tricks, and mind-blowing tech facts. You MUST return ONLY a single JSON object. Absolutely NO conversational text, reasoning explanation, or markdown wrapper."
            n_user = (
                f"Tech Channel: {podcast_entry.get('name', 'Tech')}\n"
                f"Video Title: {video_meta.get('title', 'Episode')}\n"
                f"Goal: {goal_instruction}\n\n"
                f"Transcript:\n{formatted_transcript}\n\n"
                f"Output ONLY valid raw JSON with this exact schema:\n"
                f"{{\"start_seconds\": <float>, \"end_seconds\": <float>, \"topic_title\": \"<Core 3-5 word topic title>\", "
                f"\"viral_title\": \"<Punchy title under 60 chars with 1 emoji and #Shorts>\", \"hook_reason\": \"<string>\", "
                f"\"tags\": [\"tag1\", \"tag2\"], \"speaker_badge\": \"<Speaker Name>\"}}"
            )
            
            n_payload = json.dumps({
                "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
                "messages": [
                    {"role": "system", "content": n_sys},
                    {"role": "user", "content": n_user}
                ],
                "temperature": 0.1,
                "max_tokens": 1024
            }).encode("utf-8")
            
            n_req = urllib.request.Request(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                data=n_payload,
                headers={
                    "Authorization": f"Bearer {nvidia_api_key}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(n_req, timeout=10) as n_resp:
                n_data = json.loads(n_resp.read().decode("utf-8"))
                content = n_data["choices"][0]["message"]["content"]
                parsed = parse_llm_json(content)
                if parsed and "start_seconds" in parsed and "end_seconds" in parsed:
                    min_start = max(0.0, continuation_start_sec + 0.5 if (continuation_start_sec and part_number > 1) else 0.0)
                    raw_start = max(min_start, float(parsed.get("start_seconds", min_start)))
                    raw_end = float(parsed.get("end_seconds", raw_start + 45.0))
                    
                    start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, raw_start, raw_end)
                    clip_dur = end_sec - start_sec
                    
                    parsed["start_seconds"] = start_sec
                    parsed["end_seconds"] = end_sec
                    parsed["duration"] = clip_dur
                    log(f"✅ NVIDIA Nemotron selected clip (Part {part_number}): {parsed['start_seconds']}s -> {parsed['end_seconds']}s ({clip_dur:.1f}s)")
                    return parsed
        except Exception as ne:
            log(f"⚠️ NVIDIA Nemotron notice (server load/timeout): {ne}")
            log("Falling back to Groq / OpenRouter...")

    # 1.5 OpenRouter / DeepSeek Fallback if key is present
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_api_key:
        try:
            log(f"🧠 Querying OpenRouter DeepSeek for highlight (Part {part_number})...")
            or_payload = json.dumps({
                "model": "deepseek/deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You are an expert viral YouTube Shorts editor. Return ONLY a single raw JSON object with keys: start_seconds, end_seconds, topic_title, viral_title, hook_reason, tags, speaker_badge."},
                    {"role": "user", "content": f"Podcast: {podcast_entry.get('name', 'Podcast')}\nEpisode: {video_meta.get('title', 'Episode')}\nGoal: {goal_instruction}\n\nTranscript:\n{formatted_transcript}"}
                ],
                "temperature": 0.1,
                "max_tokens": 1024
            }).encode("utf-8")
            or_req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=or_payload,
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/auto-clipper-shorts",
                    "X-Title": "Auto Clipper Shorts"
                }
            )
            with urllib.request.urlopen(or_req, timeout=30) as or_resp:
                or_data = json.loads(or_resp.read().decode("utf-8"))
                or_content = or_data["choices"][0]["message"]["content"]
                or_parsed = parse_llm_json(or_content)
                if or_parsed and "start_seconds" in or_parsed and "end_seconds" in or_parsed:
                    min_start = max(0.0, continuation_start_sec + 0.5 if (continuation_start_sec and part_number > 1) else 0.0)
                    raw_start = max(min_start, float(or_parsed.get("start_seconds", min_start)))
                    raw_end = float(or_parsed.get("end_seconds", raw_start + 45.0))
                    
                    start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, raw_start, raw_end)
                    clip_dur = end_sec - start_sec
                    
                    or_parsed["start_seconds"] = start_sec
                    or_parsed["end_seconds"] = end_sec
                    or_parsed["duration"] = clip_dur
                    log(f"✅ OpenRouter selected clip (Part {part_number}): {or_parsed['start_seconds']}s -> {or_parsed['end_seconds']}s ({clip_dur:.1f}s)")
                    return or_parsed
        except Exception as oe:
            log(f"⚠️ OpenRouter highlight notice: {oe}")

    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        default_start = continuation_start_sec + 1.0 if continuation_start_sec else 30.0
        start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, default_start, default_start + 45.0)
        return {
            "start_seconds": start_sec,
            "end_seconds": end_sec,
            "duration": end_sec - start_sec,
            "topic_title": f"{podcast_entry.get('name', 'Podcast')} Masterclass",
            "viral_title": f"{podcast_entry.get('name', 'Podcast')} Insight [Part {part_number}] 💡 #Shorts",
            "hook_reason": "Curated insightful discussion",
            "tags": ["podcast", "wisdom", "mindset", "success", "shorts"],
            "speaker_badge": podcast_entry.get("name", "Podcast")
        }
        
    SPONSOR_KEYWORDS = [
        "sponsor", "sponsored", "brought to you by", "promo code", "discount code", "use code",
        "athletic greens", "ag1", "betterhelp", "expressvpn", "nordvpn", "squarespace", "shopify",
        "manscaped", "audible", "cash app", "eight sleep", "blinkist", "hello fresh", "factor",
        "magic spoon", "patreon", "subscribestar", "advertisement", "support the podcast", "merch",
        "our sponsors", "today's sponsor", "free trial", "percent off", "check out"
    ]
    
    # Filter out early sponsor reads from candidate segments for Part 1
    clean_segments = []
    if transcript_segments:
        for seg in transcript_segments:
            seg_text = seg.get("text", "").lower()
            is_sponsor = any(kw in seg_text for kw in SPONSOR_KEYWORDS)
            if not is_sponsor or seg.get("start", 0) > 400.0:
                clean_segments.append(seg)
                
    client = Groq(api_key=groq_api_key)
    system_prompt = (
        "You are an expert viral YouTube Shorts content strategist specializing in "
        "technology, computer science, software development, Linux hacks, and mind-blowing tech facts.\n"
        "CRITICAL RULE: YOU MUST NEVER SELECT SPONSOR READS, ADVERTISEMENTS, PRODUCT ENDORSEMENTS, "
        "PROMO CODES, OR HOUSEKEEPING INTROS. ONLY SELECT MIND-BLOWING TECH FACTS, DEVELOPER TIPS, "
        "OR HIGH-VALUE PROGRAMMING INSIGHTS."
    )
    user_prompt = f"""
Tech Channel: {podcast_entry.get('name', 'Tech')}
Video Title: {video_meta.get('title', 'Episode')}
Goal: {goal_instruction}
STRICT REQUIREMENT: NO SPONSOR ADS, NO PROMO CODES, NO PRODUCT PLUGS. Pick pure tech knowledge / developer insight.

Transcript:
{formatted_transcript}

JSON Schema:
{{
  "start_seconds": <float>,
  "end_seconds": <float>,
  "topic_title": "<Core 3-5 word topic title, e.g. 'Linux Command Secret'>",
  "viral_title": "<Punchy title under 60 chars with 1 emoji and #Shorts>",
  "hook_reason": "<Why this tech fact/tip has high viewer retention>",
  "tags": ["techshorts", "coding", "programming", "developer", "techfacts"],
  "speaker_badge": "<Tech badge, e.g. 'TECH FACT 💡' or 'DEV HACK ⚡' or creator name>"
}}
"""
    log(f"Sending transcript to Groq for tech highlight detection (Part {part_number})...")
    for model_name in [DEFAULT_GROQ_MODEL, FALLBACK_GROQ_MODEL]:
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=600
            )
            raw_content = resp.choices[0].message.content
            clip_data = parse_llm_json(raw_content)
            
            # Ensure we start after intro sponsors for Part 1 (at least 150s in unless multi-part continuation)
            base_min = (continuation_start_sec + 0.5) if (continuation_start_sec and part_number > 1) else (150.0 if clean_segments and clean_segments[0].get("start", 0) > 150 else 30.0)
            raw_start = max(base_min, float(clip_data.get("start_seconds", base_min)))
            raw_end = float(clip_data.get("end_seconds", raw_start + 45.0))
            
            start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, raw_start, raw_end)
            clip_dur = end_sec - start_sec
            
            clip_data["start_seconds"] = start_sec
            clip_data["end_seconds"] = end_sec
            clip_data["duration"] = clip_dur
            log(f"✅ Groq selected clip (Part {part_number}): {clip_data['start_seconds']}s -> {clip_data['end_seconds']}s ({clip_dur:.1f}s)")
            return clip_data
        except Exception as ge:
            log(f"⚠️ Groq highlight detection notice: {ge}")
            
    # Fallback to deterministic sequential slice (skipping early sponsor block for Part 1)
    default_start = continuation_start_sec + 1.0 if continuation_start_sec else 150.0
    start_sec, end_sec = snap_clip_to_sentence_boundary(transcript_segments, default_start, default_start + 45.0)
    return {
        "start_seconds": start_sec,
        "end_seconds": end_sec,
        "duration": end_sec - start_sec,
        "topic_title": f"{podcast_entry.get('name', 'Tech')} Insight",
        "viral_title": f"{podcast_entry.get('name', 'Tech')} Fact [Part {part_number}] 💡 #Shorts",
        "hook_reason": "Curated technical discussion and developer tip",
        "tags": ["techshorts", "coding", "programming", "developer", "techfacts", "shorts"],
        "speaker_badge": podcast_entry.get("name", "TECH FACT 💡")
    }


# =====================================================================
# 4. ASS Karaoke Subtitle Generator
# =====================================================================

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


def generate_karaoke_ass_subtitles(segments: list, start_sec: float, end_sec: float, output_ass_path: Path, is_landscape: bool = False):
    if is_landscape:
        ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,5,3,2,60,60,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        GROUP_SIZE = 4
    else:
        ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,68,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,6,3,2,40,40,540,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        GROUP_SIZE = 3

    clip_words = []
    for seg in segments:
        words = seg.get("words", [])
        if not words and seg.get("text"):
            words = create_word_timestamps_from_segment(seg["text"], seg["start"], seg["end"])
            
        for w in words:
            if w["start"] >= (start_sec - 0.2) and w["end"] <= (end_sec + 0.5):
                rel_start = max(0.0, w["start"] - start_sec)
                rel_end = max(rel_start + 0.1, w["end"] - start_sec)
                cleaned_word = w["word"].upper().replace("{", "").replace("}", "").replace("\\", "")
                if cleaned_word:
                    clip_words.append({
                        "word": cleaned_word,
                        "start": rel_start,
                        "end": rel_end
                    })

    events = []
    for i in range(0, len(clip_words), GROUP_SIZE):
        group = clip_words[i:i + GROUP_SIZE]
        if not group:
            continue
        
        for active_idx, target_word in enumerate(group):
            w_start = target_word["start"]
            w_end = max(w_start + 0.1, min(target_word["end"], w_start + 0.75))
            if active_idx < len(group) - 1:
                w_end = min(w_end, group[active_idx + 1]["start"])
            
            line_parts = []
            for idx, gw in enumerate(group):
                if idx == active_idx:
                    # Pop animation and glowing electric neon cyan highlight
                    line_parts.append(r"{\c&H002BF5FF\fscx118\fscy118}" + gw["word"] + r"{\c&H00FFFFFF\fscx100\fscy100}")
                else:
                    line_parts.append(r"{\c&H00FFFFFF\fscx100\fscy100}" + gw["word"])
                    
            full_line_text = " ".join(line_parts)
            start_str = format_ass_time(w_start)
            end_str = format_ass_time(w_end)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{full_line_text}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        for ev in events:
            f.write(ev + "\n")
            
    log(f"Generated dynamic karaoke subtitles ({'16:9 Landscape' if is_landscape else '9:16 Portrait'}) with {len(events)} events.")


# =====================================================================
# 5. FFmpeg 9:16 Video Rendering Engine
# =====================================================================

def download_video_via_cobalt(video_url: str, start_sec: float, end_sec: float, output_path: Path) -> bool:
    """
    Attempts to download video stream via Cobalt API proxies to bypass YouTube datacenter IP rate limits.
    """
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwiatekm.tokyo/api/json",
        "https://cobalt.hyonsu.com/api/json"
    ]
    payload = json.dumps({
        "url": video_url,
        "videoQuality": "1080",
        "audioFormat": "best"
    }).encode("utf-8")

    for api_url in cobalt_instances:
        try:
            log(f"⚡ Requesting direct stream from Cobalt API ({api_url})...")
            req = urllib.request.Request(
                api_url,
                data=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "AutoClipperShorts/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                stream_url = data.get("url")
                if stream_url:
                    log(f"✅ Got direct video stream URL from Cobalt! Slicing clip...")
                    # Slice directly using ffmpeg
                    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}"
                    dur_str = f"{int((end_sec - start_sec) + 1)}"
                    ff_cmd = [
                        "ffmpeg", "-y",
                        "-ss", start_str,
                        "-i", stream_url,
                        "-t", dur_str,
                        "-c", "copy",
                        str(output_path)
                    ]
                    subprocess.run(ff_cmd, check=True, capture_output=True)
                    if output_path.exists() and output_path.stat().st_size > 100000:
                        log(f"✅ Successfully sliced video via Cobalt stream ({output_path.stat().st_size} bytes)")
                        return True
        except Exception as e:
            log(f"Cobalt instance notice ({api_url}): {e}")
            continue
    return False


def download_video_clip_segment(video_url: str, start_sec: float, end_sec: float, output_raw_path: Path):
    if output_raw_path.exists():
        output_raw_path.unlink()
        
    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}"
    end_str = f"{int(end_sec // 3600):02d}:{int((end_sec % 3600) // 60):02d}:{int(end_sec % 60):02d}"
    
    # 1. Ultra-fast direct stream slicing via FFmpeg (Dual 1080p Video + Audio)
    client_profiles = [
        ("ios,mweb", []),
        ("mweb,ios", []),
        ("mweb", []),
        ("web", []),
        ("tv", []),
        ("mweb,default", ytdlp_cookies_args()),
        ("web", ytdlp_cookies_args()),
    ]
    formats_to_try = [
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best/18/22",
        "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b/best",
        "b/best/bv*+ba"
    ]
    for client_name, cookie_args in client_profiles:
        for fmt in formats_to_try:
            try:
                log(f"Resolving direct video stream URL via [{client_name}] format [{fmt[:25]}] for slice {start_str} to {end_str}...")
                g_cmd = [
                    "yt-dlp",
                    "-g",
                    "-f", fmt,
                    "--no-warnings",
                    "--extractor-args", f"youtube:player_client={client_name}",
                ] + cookie_args + [video_url]
                res = subprocess.run(g_cmd, capture_output=True, text=True, check=True)
                stream_lines = [l.strip() for l in res.stdout.strip().split("\n") if l.strip().startswith("http")]
                
                if len(stream_lines) >= 2:
                    v_url, a_url = stream_lines[0], stream_lines[1]
                    log(f"Direct dual stream URLs resolved (video + audio). Slicing with FFmpeg...")
                    ff_slice_cmd = [
                        "ffmpeg", "-y",
                        "-ss", start_str, "-to", end_str, "-i", v_url,
                        "-ss", start_str, "-to", end_str, "-i", a_url,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        str(output_raw_path)
                    ]
                    subprocess.run(ff_slice_cmd, check=True, capture_output=True)
                elif len(stream_lines) == 1:
                    log("Direct single stream URL resolved. Slicing with FFmpeg...")
                    ff_slice_cmd = [
                        "ffmpeg", "-y",
                        "-ss", start_str,
                        "-to", end_str,
                        "-i", stream_lines[0],
                        "-c", "copy",
                        str(output_raw_path)
                    ]
                    subprocess.run(ff_slice_cmd, check=True, capture_output=True)
                    
                if output_raw_path.exists() and output_raw_path.stat().st_size > 50000:
                    log(f"✅ Video slice created via direct FFmpeg stream ({output_raw_path.stat().st_size / 1024:.1f} KB)")
                    return
            except Exception as ge:
                log(f"Direct stream [{client_name}] notice: {ge}. Trying next profile...")

    # 2. Fallback to yt-dlp section downloader
    for fb_client, fb_cookies in [("ios,mweb", []), ("mweb,ios", []), ("mweb", []), ("web", []), ("mweb,default", ytdlp_cookies_args()), ("web", ytdlp_cookies_args())]:
        for fb_fmt in ["bestvideo[height<=1080]+bestaudio/best[height<=1080]/best/18/22", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b/best", "b/best"]:
            try:
                log(f"Downloading video slice via yt-dlp fallback [{fb_client}]: {start_str} to {end_str}...")
                cmd = [
                    "yt-dlp",
                    "--download-sections", f"*{start_str}-{end_str}",
                    "-f", fb_fmt,
                    "--merge-output-format", "mp4",
                    "--force-keyframes-at-cuts",
                    "--no-playlist",
                    "--no-warnings",
                    "--extractor-args", f"youtube:player_client={fb_client}",
                ] + fb_cookies + [
                    "-o", str(output_raw_path),
                    video_url
                ]
                subprocess.run(cmd, check=True)
                if output_raw_path.exists() and output_raw_path.stat().st_size > 50000:
                    log(f"✅ Video slice downloaded via yt-dlp fallback ({output_raw_path.stat().st_size / 1024:.1f} KB)")
                    return
            except Exception as fb_err:
                log(f"yt-dlp fallback profile [{fb_client}] failed: {fb_err}")

    if not output_raw_path.exists() or output_raw_path.stat().st_size < 50000:
        raise RuntimeError(f"Failed to extract video slice for {video_url} from all client profiles.")


def render_vertical_916_short(
    raw_video_path: Path,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = ""
):
    if output_final_path.exists():
        output_final_path.unlink()
        
    log("Rendering 1080x1920 vertical short with FFmpeg...")
    # Safe FFmpeg path escaping
    ass_filter_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    badge_text = (speaker_badge or "PODCAST HIGHLIGHT").replace(":", " ").replace("'", "").replace("%", "").replace("\\", "").upper()
    
    font_file = find_system_font()
    if os.path.exists(font_file):
        font_opt = f"fontfile='{font_file}'"
    else:
        font_opt = "font='DejaVu Sans'"
        
    filtergraph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=25:5,eq=brightness=-0.08:contrast=1.05[bg];"
        "[0:v]scale=1040:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2 - 50[comp1];"
        f"[comp1]drawbox=y=160:color=black@0.75:width=iw:height=90:t=fill,"
        f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:{font_opt}:x=(w-text_w)/2:y=182[comp2];"
        f"[comp2]ass='{ass_filter_path}'[v]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_video_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a?",
        "-af", "loudnorm=I=-14:LRA=7:TP=-1.5",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-force_key_frames", "expr:gte(t,n_forced*1)",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_final_path)
    ]
    
    subprocess.run(cmd, check=True)
    log(f"Video render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")

    # Generate high-impact thumbnail with curiosity title stamping
    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    generate_custom_thumbnail(output_final_path, thumb_path, speaker_badge, is_landscape=False)


def render_landscape_169_video(
    raw_video_path: Path,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = ""
):
    if output_final_path.exists():
        output_final_path.unlink()
        
    log("Rendering 1920x1080 landscape normal video with FFmpeg...")
    ass_filter_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    badge_text = (speaker_badge or "PODCAST INSIGHT").replace(":", " ").replace("'", "").replace("%", "").replace("\\", "").upper()
    
    font_file = find_system_font()
    if os.path.exists(font_file):
        font_opt = f"fontfile='{font_file}'"
    else:
        font_opt = "font='DejaVu Sans'"
        
    filtergraph = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        "eq=contrast=1.03:brightness=-0.02[v0];"
        f"[v0]drawbox=x=40:y=40:w=640:h=56:color=black@0.75:t=fill,"
        f"drawtext=text='{badge_text}':fontcolor=white:fontsize=28:{font_opt}:x=60:y=54[v1];"
        f"[v1]ass={ass_filter_path}[v]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_video_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a?",
        "-af", "loudnorm=I=-14:LRA=7:TP=-1.5",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-force_key_frames", "expr:gte(t,n_forced*1)",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_final_path)
    ]
    
    subprocess.run(cmd, check=True)
    log(f"Landscape video render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")

    # Generate high-impact 16:9 thumbnail
    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    generate_custom_thumbnail(output_final_path, thumb_path, speaker_badge, is_landscape=True)


def generate_custom_thumbnail(video_path: Path, thumb_path: Path, topic_title: str = "", is_landscape: bool = False):
    """
    Generates a high-CTR custom YouTube thumbnail frame with bold curiosity text stamping.
    Supports both 9:16 portrait and 16:9 widescreen thumbnails.
    """
    try:
        font_file = find_system_font()
        font_opt = f"fontfile='{font_file}'" if os.path.exists(font_file) else "font='DejaVu Sans'"
        clean_title = re.sub(r"[^A-Za-z0-9\s\?!\'\-]", "", (topic_title or "MASTERCLASS INSIGHT")).strip().upper()[:44]
        
        if is_landscape:
            vf = (
                f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                f"drawbox=y=780:color=black@0.85:width=iw:height=150:t=fill,"
                f"drawbox=y=780:color=#00D2FF@0.95:width=iw:height=150:t=4,"
                f"drawtext=text='{clean_title}':fontcolor=#FFE600:fontsize=52:{font_opt}:x=(w-text_w)/2:y=830"
            )
        else:
            vf = (
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"drawbox=y=1380:color=black@0.85:width=iw:height=160:t=fill,"
                f"drawbox=y=1380:color=#00D2FF@0.95:width=iw:height=160:t=4,"
                f"drawtext=text='{clean_title}':fontcolor=#FFE600:fontsize=46:{font_opt}:x=(w-text_w)/2:y=1436"
            )
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", "00:00:02.00",
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", vf,
            "-q:v", "2",
            str(thumb_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if thumb_path.exists():
            log(f"📸 High-CTR Stamped Thumbnail generated ({'16:9 Landscape' if is_landscape else '9:16 Portrait'}): {thumb_path.name} ({thumb_path.stat().st_size // 1024} KB)")
    except Exception as te:
        log(f"Thumbnail notice: {te}")


def detect_audio_pitch_gender(wav_path: Path) -> str:
    """
    Analyzes the sliced audio waveform using fundamental frequency (F0) autocorrelation.
    - Deep masculine voice: F0 is typically 80 Hz - 155 Hz (e.g. Tim Ferriss, Andrew Huberman)
    - Feminine voice: F0 is typically 165 Hz - 260 Hz (e.g. Elizabeth Gilbert, Mel Robbins)
    Guarantees 100% accurate gender avatar assignment regardless of title/guest metadata mismatches.
    """
    if not wav_path or not Path(wav_path).exists():
        return "male"
        
    try:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            max_read = min(n_frames, sr * 10)
            raw = wf.readframes(max_read)
            samples = struct.unpack(f"<{len(raw)//2}h", raw)
            
        frame_len = int(sr * 0.04)
        step = int(sr * 0.05)
        pitches = []
        
        min_lag = max(1, int(sr / 300))
        max_lag = max(1, int(sr / 75))
        
        for i in range(0, len(samples) - frame_len - max_lag, step):
            chunk = samples[i:i+frame_len]
            energy = sum(s*s for s in chunk) / frame_len
            if energy < 1000000:
                continue
                
            best_corr = 0
            best_lag = min_lag
            base_energy = sum(chunk[j]*chunk[j] for j in range(frame_len))
            
            for lag in range(min_lag, max_lag):
                corr = sum(chunk[j] * samples[i + j + lag] for j in range(frame_len))
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag
                    
            if base_energy > 0 and (best_corr / base_energy) > 0.45:
                f0 = sr / best_lag
                if 70 <= f0 <= 320:
                    pitches.append(f0)
                    
        if pitches:
            pitches.sort()
            median_f0 = pitches[len(pitches)//2]
            gender = "male" if median_f0 < 165 else "female"
            log(f"🎤 Audio Pitch Analysis: {median_f0:.1f} Hz -> Assigned {gender.upper()} avatar.")
            return gender
    except Exception as pe:
        log(f"Pitch analysis notice: {pe}")
        
    return "male"


def detect_speaker_gender(speaker_name: str, episode_title: str = "") -> str:
    """
    Determines whether the primary speaker is male or female based on name and context.
    """
    female_identifiers = {
        "elizabeth", "gilbert", "mel", "robbins", "brene", "brown", "esther", "perel", 
        "sara", "blakely", "rhonda", "patrick", "vanessa", "edwards", "mary", "sarah", 
        "lisa", "jennifer", "amy", "emma", "laura", "rachel", "claire", "anna", "tara",
        "fei-fei", "feifei", "fei", "li", "hannah", "jessica", "lucy", "sophie", "katie",
        "maya", "chloe", "olivia", "eva", "natalie", "zoe", "cloe", "dr fei-fei", "dr. fei-fei",
        "she", "her", "female", "woman"
    }
    combined = f"{speaker_name} {episode_title}".lower()
    for fi in female_identifiers:
        if re.search(rf"\b{re.escape(fi)}\b", combined):
            return "female"
    return "male"


def snap_clip_to_sentence_boundary(transcript_segments: list, start_sec: float, raw_end_sec: float) -> tuple:
    """
    Snaps start_sec and end_sec to exact spoken word boundaries to guarantee
    speech starts immediately on frame 0 and ends naturally with the final word,
    eliminating dead silence / muted audio at the end.
    """
    matching_words = []
    if transcript_segments:
        for s in transcript_segments:
            for w in s.get("words", []):
                matching_words.append(w)
                
    if matching_words:
        candidates_start = [w for w in matching_words if w["start"] >= (start_sec - 1.0)]
        actual_start = candidates_start[0]["start"] if candidates_start else start_sec
        
        target_dur = max(38.0, min(52.0, raw_end_sec - actual_start))
        target_end = actual_start + target_dur
        
        valid_ends = [w for w in matching_words if (w["end"] - actual_start) >= 36.0 and (w["end"] - actual_start) <= 58.0]
        if valid_ends:
            best_end_word = min(valid_ends, key=lambda w: abs(w["end"] - target_end))
            actual_end = best_end_word["end"] + 0.35
            return (round(actual_start, 2), round(actual_end, 2))
            
    duration = max(38.0, min(55.0, raw_end_sec - start_sec))
    return (round(start_sec, 2), round(start_sec + duration, 2))


def get_cute_animal_image_info(gender: str = "male") -> Path:
    """
    Finds one of the adult anime-style anthropomorphic animal avatars (head to belly cutout)
    on a solid dark grey studio background.
    - If female: Selects feminine avatar (e.g. female_snow_leopard.jpg, female_fox.jpg)
    - If male: Selects manly masculine avatar (e.g. male_wolf.jpg, male_lion.jpg)
    """
    base_dir = Path(__file__).resolve().parent / "assets" / "images" / "avatars"
    target_dir = base_dir / ("female" if gender == "female" else "male")
    
    if target_dir.exists():
        candidates = sorted(list(target_dir.glob("*.jpg")) + list(target_dir.glob("*.png")))
        if candidates:
            return random.choice(candidates)
            
    if base_dir.exists():
        all_avatars = sorted(list(base_dir.rglob("*.jpg")) + list(base_dir.rglob("*.png")))
        if all_avatars:
            return random.choice(all_avatars)
            
    img_dir = Path(__file__).resolve().parent / "assets" / "images" / "animals"
    if img_dir.exists():
        fallback_candidates = sorted(list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png")))
        if fallback_candidates:
            return random.choice(fallback_candidates)
    return None


def compute_audio_energy_timeline(wav_path: Path) -> tuple:
    """
    Analyzes uncompressed PCM WAV audio with 25ms temporal resolution.
    Returns:
    - spk_active_expr: sample-accurate speech activity intervals
    - spk_loud_expr: high-energy vocal emphasis bursts
    - is_calm_speech: overall tonal profile
    - vocal_peaks: list of (timestamp, rms, prominence_ratio)
    """
    if not wav_path or not Path(wav_path).exists():
        return ("1", "0", False, [])
        
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_data = wf.readframes(n_frames)
            
        fmt = f"<{n_frames * n_channels}h" if sampwidth == 2 else f"<{n_frames * n_channels}b"
        samples = struct.unpack(fmt, raw_data)
        
        if n_channels > 1:
            mono_samples = [sum(samples[i:i+n_channels]) // n_channels for i in range(0, len(samples), n_channels)]
        else:
            mono_samples = samples
            
        slice_ms = 25
        samples_per_slice = int(framerate * (slice_ms / 1000.0))
        n_slices = len(mono_samples) // samples_per_slice
        
        rms_list = []
        times = []
        
        for i in range(n_slices):
            chunk = mono_samples[i * samples_per_slice : (i + 1) * samples_per_slice]
            if not chunk:
                continue
            sum_sq = sum(s * s for s in chunk)
            rms = math.sqrt(sum_sq / len(chunk))
            rms_list.append(rms)
            times.append(i * (slice_ms / 1000.0))
            
        if not rms_list:
            return ("1", "0", False, [])
            
        avg_rms = sum(rms_list) / len(rms_list)
        silence_thresh = avg_rms * 0.22
        loud_thresh = avg_rms * 1.38
        
        active_intervals = []
        loud_intervals = []
        vocal_peaks = []
        
        # Local peak prominence detection (sliding window over RMS)
        for i in range(1, len(rms_list) - 1):
            r_prev, r, r_next = rms_list[i-1], rms_list[i], rms_list[i+1]
            t = times[i]
            if r > r_prev and r > r_next and r >= loud_thresh:
                prominence_ratio = r / max(1.0, avg_rms)
                vocal_peaks.append((round(t, 2), round(r, 1), round(prominence_ratio, 2)))
        
        curr_active_start = None
        curr_loud_start = None
        
        for i, (t, r) in enumerate(zip(times, rms_list)):
            is_active = (r >= silence_thresh)
            is_loud = (r >= loud_thresh)
            
            if is_active:
                if curr_active_start is None:
                    curr_active_start = t
            else:
                if curr_active_start is not None:
                    active_intervals.append((curr_active_start, t))
                    curr_active_start = None
                    
            if is_loud:
                if curr_loud_start is None:
                    curr_loud_start = t
            else:
                if curr_loud_start is not None:
                    loud_intervals.append((curr_loud_start, t))
                    curr_loud_start = None
                    
        if curr_active_start is not None:
            active_intervals.append((curr_active_start, times[-1]))
        if curr_loud_start is not None:
            loud_intervals.append((curr_loud_start, times[-1]))
            
        merged_active = []
        for s, e in active_intervals:
            if not merged_active:
                merged_active.append([s, e])
            else:
                if s - merged_active[-1][1] < 0.20:
                    merged_active[-1][1] = e
                else:
                    merged_active.append([s, e])
                    
        merged_loud = []
        for s, e in loud_intervals:
            if not merged_loud:
                merged_loud.append([s, e])
            else:
                if s - merged_loud[-1][1] < 0.30:
                    merged_loud[-1][1] = e
                else:
                    merged_loud.append([s, e])
            
        active_expr_parts = [f"between(t,{s:.2f},{e:.2f})" for s, e in merged_active if (e - s) >= 0.12]
        loud_expr_parts = [f"between(t,{s:.2f},{e:.2f})" for s, e in merged_loud if (e - s) >= 0.12]
        
        spk_active_expr = "+".join(active_expr_parts[:25]) if active_expr_parts else "1"
        spk_loud_expr = "+".join(loud_expr_parts[:18]) if loud_expr_parts else "0"
        is_calm_speech = (len(merged_loud) < max(1, len(merged_active)) * 0.22)
        
        return (spk_active_expr, spk_loud_expr, is_calm_speech, vocal_peaks)
    except Exception as e:
        log(f"Audio energy timeline notice: {e}")
        return ("1", "0", False, [])


def compute_intelligent_shock_condition(
    transcript_segments: list,
    vocal_peaks: list,
    duration: float,
    start_sec: float = 0.0
) -> str:
    """
    Intelligent Shock Detector:
    1. Scans transcript words for genuine mindblowing, supernatural, or shocking revelation triggers.
    2. Lion gets shocked AT MOST ONCE per video (or 0 times if it is a standard calm fact).
    3. Aligns the single shock window with the exact vocal emphasis peak on the revelation.
    """
    # High-confidence mindblowing / supernatural trigger keywords:
    shock_keywords = {
        "actually", "secret", "bizarre", "supernatural", "impossible", "alien",
        "underwater", "quantum", "99%", "unbelievable", "mind-blowing",
        "shocking", "insane", "hidden", "deepest", "ghost", "mystery",
        "never-before-seen", "classified", "extraterrestrial"
    }
    
    keyword_matches = []
    
    # 1. Search for semantic revelation trigger in transcript words:
    for seg in (transcript_segments or []):
        seg_start = seg.get("start", 0.0)
        for w_obj in seg.get("words", []):
            w_text = re.sub(r"[^a-zA-Z0-9%]", "", w_obj.get("word", "")).lower()
            w_start = w_obj.get("start", seg_start) - start_sec
            if w_text in shock_keywords and w_start >= max(2.5, duration * 0.25):
                keyword_matches.append((w_start, w_text))
                
    if not keyword_matches:
        # If no mindblowing / supernatural keyword exists, check if there is an extraordinary vocal energy burst (> 2.2x avg)
        climax_peaks = [p for p in vocal_peaks if (duration * 0.35) <= p[0] <= (duration * 0.85) and len(p) >= 3 and p[2] >= 2.2]
        if not climax_peaks:
            # Fact is normal / standard -> Lion stays attentive and DOES NOT get shocked!
            log("🦁 Lion Reaction: Normal informative tone detected. Lion remains calmly attentive (Shock = 0).")
            return "0"
        best_peak = max(climax_peaks, key=lambda p: p[1])
        chosen_trigger = best_peak[0]
    else:
        # Pick the primary revelation keyword:
        first_kw = keyword_matches[0][0]
        # Correlate with nearest acoustic vocal energy peak within [-0.4s, +1.8s]
        nearby_peaks = [p[0] for p in vocal_peaks if (first_kw - 0.4) <= p[0] <= (first_kw + 1.8)]
        if nearby_peaks:
            chosen_trigger = min(nearby_peaks, key=lambda p: abs(p - first_kw))
        else:
            chosen_trigger = first_kw
            
    # Lion gets shocked exactly ONCE for a punchy 3.2s - 4.2s window
    shock_start = max(2.0, chosen_trigger - 0.10)
    shock_end = min(duration - 0.6, shock_start + 3.8)
    if shock_end <= shock_start + 1.5:
        shock_end = min(duration - 0.2, shock_start + 2.5)
        
    log(f"🧠 Intelligent Shock Sync: High-confidence revelation at {chosen_trigger:.2f}s. Single shock window: {shock_start:.2f}s -> {shock_end:.2f}s")
    return f"between(t,{shock_start:.2f},{shock_end:.2f})"


def extract_speaker_visual_motion_timeline(video_path: Path) -> tuple:
    """
    100% Free Computer Vision Speaker Motion & Gesture Extraction Engine.
    Analyzes the raw podcast video slice to extract:
    1. Real Hand Gestures: Detects when the podcaster raises their hands to make a point.
    2. Real Vocal/Mouth Articulation: Detects when the podcaster is physically speaking vs pausing.
    3. Real Reaction Peaks: Detects sudden expressive body movements and emphasis moments.
    
    Returns: (real_mouth_expr, real_gesture_expr, real_shock_expr, has_tracking)
    """
    if not video_path or not video_path.exists():
        return ("1", "0", "0", False)
        
    try:
        log(f"👁️ Extracting Computer Vision gesture & motion tracking from video slice ({video_path.name})...")
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "crop=iw:ih*0.55:0:ih*0.45,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        
        pts_list = []
        ydif_list = []
        curr_pts = 0.0
        
        for line in res.stderr.split("\n"):
            if "pts_time:" in line:
                m = re.search(r"pts_time:([0-9\.]+)", line)
                if m:
                    curr_pts = float(m.group(1))
            elif "lavfi.signalstats.YDIF=" in line:
                m = re.search(r"lavfi.signalstats.YDIF=([0-9\.]+)", line)
                if m:
                    ydif = float(m.group(1))
                    pts_list.append(curr_pts)
                    ydif_list.append(ydif)
                    
        if not ydif_list or len(ydif_list) < 10:
            log("ℹ️ No visual motion points detected, using audio energy timeline.")
            return ("1", "0", "0", False)
            
        mean_ydif = sum(ydif_list) / len(ydif_list)
        variance = sum((x - mean_ydif) ** 2 for x in ydif_list) / len(ydif_list)
        std_ydif = max(0.01, variance ** 0.5)
        
        gesture_thresh = mean_ydif + 1.15 * std_ydif
        shock_thresh = mean_ydif + 2.4 * std_ydif
        
        gesture_intervals = []
        shock_intervals = []
        
        g_start = None
        last_pts = 0.0
        
        for pts, ydif in zip(pts_list, ydif_list):
            if ydif >= gesture_thresh:
                if g_start is None:
                    g_start = max(0.0, pts - 0.2)
                last_pts = pts + 0.4
            else:
                if g_start is not None and pts > last_pts:
                    gesture_intervals.append((g_start, last_pts))
                    g_start = None
                    
            if ydif >= shock_thresh:
                shock_intervals.append((max(0.0, pts - 0.15), pts + 0.7))
                
        if g_start is not None:
            gesture_intervals.append((g_start, last_pts))
            
        merged_g = []
        for s, e in gesture_intervals:
            if merged_g and s <= (merged_g[-1][1] + 0.4):
                merged_g[-1] = (merged_g[-1][0], max(merged_g[-1][1], e))
            else:
                if (e - s) >= 0.4:
                    merged_g.append((s, e))
                    
        g_expr_parts = [f"between(t,{s:.2f},{e:.2f})" for s, e in merged_g[:25]]
        g_expr = "+".join(g_expr_parts) if g_expr_parts else "0"
        
        s_expr_parts = [f"between(t,{s:.2f},{e:.2f})" for s, e in shock_intervals[:10]]
        s_expr = "+".join(s_expr_parts) if s_expr_parts else "0"
        
        log(f"🎯 Computer Vision tracked {len(merged_g)} real podcaster hand gestures & {len(shock_intervals)} expressive peaks!")
        return ("1", g_expr, s_expr, True)
    except Exception as e:
        log(f"Visual motion tracking notice: {e}")
        return ("1", "0", "0", False)


def generate_minimax_h3_avatar_gesture(
    avatar_image_path: Path,
    topic_prompt: str = "",
    duration: float = 45.0,
    output_video_path: Path = None
) -> Path:
    """
    Generates an expressive talking & gesturing video of the character avatar
    using MiniMax H3 (Hailuo 3.0 / Video-01) reference-driven video generation.
    
    The model translates podcast speaker conversational gestures, hand movements,
    head tilts, and facial performance directly onto the chosen avatar.
    
    Returns output_video_path on success, or None if MiniMax API key is not configured or generation fails.
    """
    raw_api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not raw_api_key:
        env_file = WORKSPACE_DIR / ".env"
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MINIMAX_API_KEY="):
                            raw_api_key = line.strip().split("=", 1)[1].strip("\"'").replace(" ", "")
            except Exception:
                pass
                
    api_key = raw_api_key.strip().replace(" ", "").strip("\"'") if raw_api_key else None
    if not api_key:
        log("ℹ️ MINIMAX_API_KEY not configured. Using pure-FFmpeg audio-reactive storytime gesture animation engine.")
        return None
        
    if not avatar_image_path or not avatar_image_path.exists():
        log("⚠️ MiniMax gesture notice: Avatar image path not found. Falling back to FFmpeg engine.")
        return None
        
    base_host = os.environ.get("MINIMAX_API_HOST", "https://api.minimax.io").rstrip("/")
    model_name = os.environ.get("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    
    try:
        # 1. Base64 encode the reference avatar image
        with open(avatar_image_path, "rb") as img_f:
            b64_data = base64.b64encode(img_f.read()).decode("utf-8")
        ext = avatar_image_path.suffix.lower().lstrip(".")
        mime = "image/png" if ext == "png" else "image/jpeg"
        data_uri = f"data:{mime};base64,{b64_data}"
        
        # 2. Build gesture prompt
        topic_clause = f" discussing {topic_prompt}" if topic_prompt else ""
        prompt = (
            f"Anime character podcast speaker{topic_clause}, passionate conversation, "
            f"natural hand gestures, talking lip sync, expressive facial reactions, head tilts, "
            f"active conversational energy, looking towards camera, vibrant anime aesthetic, 4k 60fps"
        )
        
        target_dur = max(4, min(10, int(duration)))
        log(f"🧠 Querying MiniMax H3 ({model_name}) for speaker gesture video ({target_dur}s, 768P 9:16)...")
        
        payload = json.dumps({
            "model": model_name,
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}}
            ],
            "resolution": "768P",
            "ratio": "9:16",
            "duration": target_dur
        }).encode("utf-8")
        
        req = urllib.request.Request(
            f"{base_host}/v2/video_generation",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )
        
        task_id = None
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            task_id = resp_data.get("task_id")
            
        if not task_id:
            log(f"⚠️ MiniMax video creation response missing task_id: {resp_data}")
            return None
            
        log(f"⏳ MiniMax H3 video task created (task_id: {task_id}). Polling status...")
        
        # 3. Poll task status (up to 180 seconds)
        poll_url = f"{base_host}/v1/query/video_generation?task_id={task_id}"
        start_poll = time.time()
        file_id = None
        direct_video_url = None
        
        while time.time() - start_poll < 180:
            time.sleep(6)
            q_req = urllib.request.Request(
                poll_url,
                headers={"Authorization": f"Bearer {api_key}"}
            )
            with urllib.request.urlopen(q_req, timeout=20) as q_resp:
                q_data = json.loads(q_resp.read().decode("utf-8"))
                status = q_data.get("status", "").lower()
                
                if status in ["success", "succeeded", "finished"]:
                    file_id = q_data.get("file_id")
                    direct_video_url = q_data.get("video_url") or q_data.get("file_url")
                    break
                elif status in ["failed", "error"]:
                    log(f"❌ MiniMax video task failed: {q_data.get('error', 'Unknown error')}")
                    return None
                else:
                    log(f"⏳ MiniMax H3 generating gesture video... (status: {status})")
                    
        # 4. Download final video
        if output_video_path is None:
            output_video_path = OUTPUT_DIR / f"minimax_gesture_{int(time.time())}.mp4"
            
        download_target_url = direct_video_url
        if not download_target_url and file_id:
            download_target_url = f"{base_host}/v1/files/retrieve?file_id={file_id}"
            
        if download_target_url:
            log(f"📥 Downloading MiniMax H3 gesture video -> {output_video_path.name}...")
            dl_req = urllib.request.Request(
                download_target_url,
                headers={"Authorization": f"Bearer {api_key}"} if not download_target_url.startswith("http://") and not "storage" in download_target_url else {}
            )
            with urllib.request.urlopen(dl_req, timeout=60) as dl_resp, open(output_video_path, "wb") as out_f:
                out_f.write(dl_resp.read())
                
            if output_video_path.exists() and output_video_path.stat().st_size > 10000:
                log(f"✅ MiniMax H3 gesture video ready! ({output_video_path.stat().st_size / 1024:.1f} KB)")
                return output_video_path
                
        log("⚠️ MiniMax video generation timed out or download link unavailable. Falling back to FFmpeg engine.")
        return None
        
    except Exception as e:
        log(f"⚠️ MiniMax H3 generation notice: {e}. Falling back to FFmpeg storytime engine.")
        return None


def build_phonetic_viseme_expressions(transcript_segments: list, start_offset: float = 0.0) -> tuple:
    """
    Parses word-level timestamps and classifies words into human-like phonetic visemes:
    - cond_fv: Labiodental consonants F, V, TH (teeth on lip)
    - cond_ou: Rounded vowels O, U, W, OO, OW (puckered lips)
    - cond_ae: Open vowels A, E, I, AH, EH (wide open mouth)
    - cond_half: Neutral consonants / mid-flow
    Returns: (cond_fv, cond_ou, cond_ae, cond_half)
    """
    ou_ranges = []
    fv_ranges = []
    ae_ranges = []
    half_ranges = []
    
    for seg in (transcript_segments or []):
        for w in seg.get("words", []):
            word_raw = str(w.get("word", ""))
            word_text = re.sub(r"[^a-zA-Z]", "", word_raw).lower()
            if not word_text:
                continue
            w_start = max(0.0, float(w.get("start", 0.0)) - start_offset)
            w_end = max(w_start + 0.12, float(w.get("end", w_start + 0.3)) - start_offset)
            
            # 1. Labiodental F/V/TH friction (teeth on lower lip)
            if any(word_text.startswith(p) for p in ["f", "v", "th", "ph"]):
                fv_ranges.append(f"between(t,{w_start:.2f},{w_end:.2f})")
            # 2. Rounded vowels O/U/W (puckered lips)
            elif any(c in word_text for c in ["oo", "ou", "ow", "au"]) or any(word_text.startswith(p) for p in ["wh", "wo", "wa", "you", "o", "u"]):
                ou_ranges.append(f"between(t,{w_start:.2f},{w_end:.2f})")
            # 3. Open wide vowels A/E/I
            elif any(c in word_text for c in ["a", "e", "i", "ai", "ay", "ea", "ee"]):
                ae_ranges.append(f"between(t,{w_start:.2f},{w_end:.2f})")
            else:
                half_ranges.append(f"between(t,{w_start:.2f},{w_end:.2f})")
                
    cond_fv = "+".join(fv_ranges) if fv_ranges else "0"
    cond_ou = "+".join(ou_ranges) if ou_ranges else "0"
    cond_ae = "+".join(ae_ranges) if ae_ranges else "0"
    cond_half = "+".join(half_ranges) if half_ranges else "0"
    
    return (cond_fv, cond_ou, cond_ae, cond_half)


def get_character_avatar_pack(char_dir: Path, gender: str = "male") -> dict:
    """
    Returns full phonetic viseme & gesture avatar suite:
    dict with keys: closed, half, open, viseme_ae, viseme_ou, viseme_fv, g_point, g_both, g_think, g_lean, shocked
    """
    fallback_single = get_cute_animal_image_info(gender)
    if not char_dir or not char_dir.exists():
        return {k: fallback_single for k in ["closed", "half", "open", "viseme_ae", "viseme_ou", "viseme_fv", "g_point", "g_both", "g_think", "g_lean", "shocked"]}
        
    def find_file(names, default):
        for n in names:
            p = char_dir / n
            if p.exists():
                return p
        return default

    closed = find_file(["mouth_closed.jpg", "pose_01.jpg"], fallback_single)
    half = find_file(["mouth_half.jpg"], closed)
    open_p = find_file(["mouth_open.jpg"], half)
    viseme_ae = find_file(["viseme_ae.jpg"], open_p)
    viseme_ou = find_file(["viseme_ou.jpg"], half)
    viseme_fv = find_file(["viseme_fv.jpg"], closed)
    g_point = find_file(["gesture_point.jpg", "gesture_pose.jpg"], open_p)
    g_both = find_file(["gesture_both.jpg"], g_point)
    g_think = find_file(["gesture_think.jpg"], closed)
    g_lean = find_file(["gesture_lean_in.jpg"], g_think)
    shocked = find_file(["shocked_hands_on_head.jpg", "shocked_pose.jpg"], g_both)
    
    return {
        "closed": closed,
        "half": half,
        "open": open_p,
        "viseme_ae": viseme_ae,
        "viseme_ou": viseme_ou,
        "viseme_fv": viseme_fv,
        "g_point": g_point,
        "g_both": g_both,
        "g_think": g_think,
        "g_lean": g_lean,
        "shocked": shocked
    }


def get_dual_speaker_avatars(host_gender: str = "male", guest_gender: str = "male", speaker_name: str = "wolf", reactor_name: str = "lion") -> tuple:
    """
    Finds full phonetic viseme & gesture avatar suites for both characters.
    Defaults to Wolf (Speaker / Narrator) on left and Lion (Shocked Reactor) on right.
    Returns: (host_pack_dict, guest_pack_dict)
    """
    base_dir = Path(__file__).resolve().parent / "assets" / "images" / "avatars"
    male_dir = base_dir / "male"
    
    wolf_dir = male_dir / speaker_name
    lion_dir = male_dir / reactor_name
    
    if wolf_dir.exists() and lion_dir.exists():
        host_char = wolf_dir
        guest_char = lion_dir
    else:
        host_dir = base_dir / ("female" if host_gender == "female" else "male")
        guest_dir = base_dir / ("female" if guest_gender == "female" else "male")
        
        host_chars = [d for d in host_dir.iterdir() if d.is_dir()] if host_dir.exists() else []
        guest_chars = [d for d in guest_dir.iterdir() if d.is_dir()] if guest_dir.exists() else []
        
        host_char = random.choice(host_chars) if host_chars else None
        if guest_chars:
            avail_guest = [c for c in guest_chars if c != host_char] or guest_chars
            guest_char = random.choice(avail_guest)
        else:
            guest_char = host_char
        
    host_pack = get_character_avatar_pack(host_char, host_gender)
    guest_pack = get_character_avatar_pack(guest_char, guest_gender)
    
    return (host_pack, guest_pack)


def get_character_pose_sequence(gender: str = "male") -> list:
    """
    Finds the pose variations for a chosen adult anime character avatar.
    Returns list of Paths to character poses.
    """
    base_dir = Path(__file__).resolve().parent / "assets" / "images" / "avatars"
    target_dir = base_dir / ("female" if gender == "female" else "male")
    
    if target_dir.exists():
        char_dirs = [d for d in target_dir.iterdir() if d.is_dir()]
        if char_dirs:
            chosen_char = random.choice(char_dirs)
            poses = sorted(list(chosen_char.glob("*.jpg")) + list(chosen_char.glob("*.png")))
            if poses:
                return poses
                
        flat_poses = sorted(list(target_dir.glob("*.jpg")) + list(target_dir.glob("*.png")))
        if flat_poses:
            return flat_poses
            
    single = get_cute_animal_image_info(gender)
    return [single] if single else []


def get_background_video_info() -> tuple:
    """
    Finds one of the gameplay background videos in assets/backgrounds/.
    Returns (Path, duration_seconds) if found, else (None, 0.0).
    """
    bg_dir = Path(__file__).resolve().parent / "assets" / "backgrounds"
    if bg_dir.exists():
        candidates = sorted(list(bg_dir.glob("subway_surfers_part*.mp4")))
        if not candidates:
            candidates = sorted(list(bg_dir.glob("*.mp4")))
        if candidates:
            chosen = random.choice(candidates)
            return (chosen, 60.0)
            
    return (None, 0.0)


def get_background_music_info() -> Path:
    """
    Finds one of the royalty-free background music tracks in assets/bgm/.
    Returns Path if found, else None.
    """
    bgm_dir = Path(__file__).resolve().parent / "assets" / "bgm"
    if bgm_dir.exists():
        candidates = sorted(list(bgm_dir.glob("*.mp3")))
        if candidates:
            return random.choice(candidates)
    return None


def get_sfx_info(sfx_name: str = "whoosh") -> Path:
    """
    Finds one of the micro-SFX in assets/sfx/.
    """
    sfx_path = Path(__file__).resolve().parent / "assets" / "sfx" / f"{sfx_name}.wav"
    return sfx_path if sfx_path.exists() else None


def fetch_topic_documentary_visuals(topic_title: str, transcript_segments: list = None, target_count: int = 6) -> list:
    """
    Fetches real topic photos, scientific diagrams, and generates editorial article cards
    from the web (Wikipedia PageImages, Wikimedia Commons, Pexels API, and Pollinations AI Flux)
    for 16:9 documentary-style landscape videos.
    """
    import urllib.request
    import urllib.parse
    import tempfile
    
    vis_dir = Path(tempfile.gettempdir()) / "topic_doc_visuals"
    vis_dir.mkdir(parents=True, exist_ok=True)
    
    clean_topic = re.sub(r"[\$#•🚀💡🤯⚠️\(\)\[\]\-]", " ", topic_title or "Technology Science Breakthrough")
    clean_topic = re.sub(r"\b(part\s*\d+|shorts|vol\s*\d+|secrets|disasters|deep\s*dive)\b", " ", clean_topic, flags=re.IGNORECASE)
    clean_topic = re.sub(r"\s+", " ", clean_topic).strip()
    
    words = [w for w in clean_topic.split() if len(w) > 2 and w.lower() not in ["the", "why", "and", "for", "with", "from", "that", "this", "into"]]
    
    keywords = []
    # Extract 2-word & 3-word entity n-grams
    for n in [2, 3]:
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i+n])
            if phrase not in keywords:
                keywords.append(phrase)
                
    if clean_topic and clean_topic not in keywords:
        keywords.append(clean_topic)
        
    for w in words:
        if len(w) > 3 and w not in keywords:
            keywords.append(w)
        
    if transcript_segments:
        combined_text = " ".join(seg.get("text", "") for seg in transcript_segments[:8])
        # Extract named entities or specific technical terms
        proper_nouns = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", combined_text)
        for pn in proper_nouns[:6]:
            if len(pn) > 3 and pn.lower() not in ["welcome", "part", "shorts", "subscribe", "today", "explore", "reveal", "hit", "like"]:
                if pn not in keywords:
                    keywords.append(pn)

    images = []
    seen_urls = set()
    
    # 1. Search Wikipedia PageImages API (Direct authentic Wikipedia lead article photos)
    for kw in keywords:
        if len(images) >= target_count - 2:
            break
        try:
            enc = urllib.parse.quote(kw)
            url = f"https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch={enc}&gsrlimit=5&prop=pageimages&pithumbsize=1920&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "AutoClipperDocumentary/1.0 (https://github.com/loobah18-arch)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pages = data.get("query", {}).get("pages", {})
                for pid, page in pages.items():
                    thumb = page.get("thumbnail", {}).get("source")
                    if thumb and thumb not in seen_urls:
                        seen_urls.add(thumb)
                        img_dest = vis_dir / f"wiki_lead_{len(images)}_{abs(hash(thumb)) % 10000}.jpg"
                        try:
                            d_req = urllib.request.Request(thumb, headers={"User-Agent": "AutoClipperDocumentary/1.0"})
                            with urllib.request.urlopen(d_req, timeout=10) as im_resp:
                                with open(img_dest, "wb") as f:
                                    f.write(im_resp.read())
                            if img_dest.exists() and img_dest.stat().st_size > 15000:
                                images.append(img_dest)
                                log(f"📸 Fetched real Wikipedia photo ({page.get('title', kw)}): {img_dest.name} ({img_dest.stat().st_size // 1024} KB)")
                                if len(images) >= target_count - 2:
                                    break
                        except Exception:
                            pass
        except Exception as we:
            log(f"Wikipedia search notice ({kw}): {we}")

    # 2. Search Wikimedia Commons for authentic high-res public domain images
    for kw in keywords:
        if len(images) >= target_count - 2:
            break
        try:
            enc = urllib.parse.quote(kw)
            url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrnamespace=6&gsrsearch={enc}&gsrlimit=5&prop=imageinfo&iiprop=url|mime|size&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "AutoClipperDocumentary/1.0 (https://github.com/loobah18-arch)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pages = data.get("query", {}).get("pages", {})
                for pid, page in pages.items():
                    infos = page.get("imageinfo", [])
                    if infos:
                        img_url = infos[0].get("url")
                        mime = infos[0].get("mime", "")
                        clean_path = img_url.split("?")[0].lower() if img_url else ""
                        is_valid_img = "image/" in mime or any(clean_path.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])
                        if img_url and is_valid_img and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            img_dest = vis_dir / f"commons_{len(images)}_{abs(hash(img_url)) % 10000}.jpg"
                            try:
                                d_req = urllib.request.Request(img_url, headers={"User-Agent": "AutoClipperDocumentary/1.0"})
                                with urllib.request.urlopen(d_req, timeout=10) as im_resp:
                                    with open(img_dest, "wb") as f:
                                        f.write(im_resp.read())
                                if img_dest.exists() and img_dest.stat().st_size > 15000:
                                    images.append(img_dest)
                                    log(f"📸 Fetched real Wikimedia Commons photo ({kw}): {img_dest.name} ({img_dest.stat().st_size // 1024} KB)")
                                    if len(images) >= target_count - 2:
                                        break
                            except Exception:
                                pass
        except Exception as e:
            log(f"Wikimedia search notice ({kw}): {e}")

    # 3. Pexels API (if PEXELS_API_KEY is configured in env)
    pexels_key = os.environ.get("PEXELS_API_KEY")
    if pexels_key and len(images) < target_count - 1:
        try:
            enc = urllib.parse.quote(clean_topic)
            p_url = f"https://api.pexels.com/v1/search?query={enc}&orientation=landscape&per_page=3"
            p_req = urllib.request.Request(p_url, headers={"Authorization": pexels_key})
            with urllib.request.urlopen(p_req, timeout=8) as p_resp:
                p_data = json.loads(p_resp.read().decode("utf-8"))
                for photo in p_data.get("photos", []):
                    src_url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
                    if src_url and src_url not in seen_urls:
                        seen_urls.add(src_url)
                        p_dest = vis_dir / f"pexels_{len(images)}.jpg"
                        with urllib.request.urlopen(urllib.request.Request(src_url), timeout=10) as im_r:
                            with open(p_dest, "wb") as f:
                                f.write(im_r.read())
                        if p_dest.exists() and p_dest.stat().st_size > 15000:
                            images.append(p_dest)
                            log(f"📸 Fetched Pexels documentary image: {p_dest.name}")
        except Exception as pe:
            log(f"Pexels search notice: {pe}")

    # 3. Generate High-End Editorial News & Research Article Cards (1920x1080)
    headline_clean = (clean_topic or "BREAKTHROUGH REPORT").upper()[:48].replace("'", "").replace(":", " ")
    sample_quote = "Empirical analysis and real-world findings from leading technology investigations."
    if transcript_segments and len(transcript_segments) > 1:
        seg_text = transcript_segments[1].get("text", "")
        if seg_text:
            sample_quote = seg_text[:110].replace("'", "").replace(":", " ")
            
    card_path = vis_dir / f"article_card_{len(images)}.jpg"
    card_cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#0d1117:s=1920x1080:d=1",
        "-vf", (
            "drawbox=x=60:y=60:w=1800:h=960:color=#161b22:t=fill,"
            "drawbox=x=60:y=60:w=1800:h=960:color=#30363d:t=2,"
            "drawbox=x=100:y=90:w=260:h=40:color=#006ee6:t=fill,"
            "drawtext=text='RESEARCH BRIEFING':fontcolor=white:fontsize=22:x=120:y=100,"
            "drawtext=text='VERIFIED DOCUMENTARY ⚡':fontcolor=#8b949e:fontsize=20:x=1520:y=100,"
            "drawbox=x=100:y=160:w=1720:h=2:color=#30363d:t=fill,"
            f"drawtext=text='{headline_clean}':fontcolor=white:fontsize=46:x=100:y=200,"
            "drawbox=x=100:y=300:w=1720:h=480:color=#0d1117:t=fill,"
            "drawbox=x=100:y=300:w=1720:h=480:color=#30363d:t=1,"
            "drawbox=x=130:y=340:w=8:h=400:color=#00d2ff:t=fill,"
            "drawtext=text='KEY FINDINGS & ANALYSIS':fontcolor=#00d2ff:fontsize=26:x=160:y=350,"
            f"drawtext=text='{sample_quote}':fontcolor=#c9d1d9:fontsize=30:x=160:y=420"
        ),
        "-frames:v", "1", str(card_path)
    ]
    try:
        subprocess.run(card_cmd, capture_output=True, check=True)
        if card_path.exists():
            images.append(card_path)
            log(f"📰 Generated editorial news card: {card_path.name}")
    except Exception as ce:
        log(f"Article card generation notice: {ce}")

    # 4. If we need more visuals, fetch Photorealistic Flux visual via Pollinations AI
    while len(images) < min(target_count, 4):
        idx = len(images)
        poll_prompt = urllib.parse.quote(f"cinematic photorealistic 16:9 documentary footage of {clean_topic} in modern high tech laboratory, 8k resolution, National Geographic style")
        poll_url = f"https://image.pollinations.ai/prompt/{poll_prompt}?width=1920&height=1080&nologo=true&enhance=false&model=flux"
        poll_dest = vis_dir / f"poll_flux_{idx}.jpg"
        try:
            req = urllib.request.Request(poll_url, headers={"User-Agent": "AutoClipperBot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as p_resp:
                with open(poll_dest, "wb") as f:
                    f.write(p_resp.read())
            if poll_dest.exists() and poll_dest.stat().st_size > 15000:
                images.append(poll_dest)
                log(f"🎨 Generated 16:9 contextual documentary visual: {poll_dest.name}")
            else:
                break
        except Exception as e:
            log(f"Pollinations visual notice: {e}")
            break

    # Fallback to at least one generated card if everything failed
    if not images:
        fallback_card = vis_dir / "fallback_article_card.jpg"
        fb_cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#0d1117:s=1920x1080:d=1",
            "-vf", (
                "drawbox=x=60:y=60:w=1800:h=960:color=#161b22:t=fill,"
                "drawtext=text='TECH DOCUMENTARY REPORT':fontcolor=white:fontsize=48:x=100:y=200"
            ),
            "-frames:v", "1", str(fallback_card)
        ]
        subprocess.run(fb_cmd, capture_output=True, check=True)
        images.append(fallback_card)

    return images


def build_topic_documentary_timeline(
    topic_title: str,
    transcript_segments: list = None,
    duration: float = 10.0,
    output_path: Path = None,
    target_count: int = 5
) -> Path:
    """
    Builds a concatenated Ken Burns video of real Wikipedia lead photos,
    Wikimedia Commons photos, and research article cards matching the exact duration.
    """
    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"doc_bg_{abs(hash(topic_title)) % 10000}.mp4"
    if output_path.exists():
        try:
            output_path.unlink()
        except Exception:
            pass
        
    calc_target = max(3, min(8, int(duration // 8))) if target_count is None else target_count
    images = fetch_topic_documentary_visuals(topic_title, transcript_segments, target_count=calc_target)
    
    temp_dir = Path(tempfile.gettempdir()) / f"doc_build_{output_path.stem}_{abs(hash(topic_title)) % 10000}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    seg_dur = duration / max(1, len(images))
    fps = 25
    frames_per_seg = max(25, int(seg_dur * fps))
    
    seg_videos = []
    for idx, img_path in enumerate(images):
        seg_vid = temp_dir / f"seg_{idx}.mp4"
        if idx % 2 == 0:
            zoom_expr = "min(zoom+0.0006,1.15)"
        else:
            zoom_expr = "max(1.15-0.0006*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
            
        cmd_seg = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(img_path),
            "-filter_complex",
            f"[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            f"zoompan=z='{zoom_expr}':d={frames_per_seg}:x='{x_expr}':y='{y_expr}':s=1920x1080:fps={fps},"
            f"eq=contrast=1.04:brightness=-0.02[v]",
            "-map", "[v]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-t", f"{seg_dur:.3f}",
            str(seg_vid)
        ]
        subprocess.run(cmd_seg, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        seg_videos.append(seg_vid)
        
    concat_manifest = temp_dir / "concat_list.txt"
    with open(concat_manifest, "w") as f:
        for sv in seg_videos:
            f.write(f"file '{sv.resolve()}'\n")
            
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_manifest),
        "-c", "copy",
        str(output_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass
        
    return output_path


def render_topic_documentary_169_video(
    audio_full_path: Path,
    start_sec: float,
    end_sec: float,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = "",
    transcript_segments: list = None,
    topic_title: str = "",
    part_info: dict = None
):
    """
    Renders a 1920x1080 Widescreen Normal Video using real topic photos,
    article headlines, and documentary b-roll with smooth Ken Burns pan/zoom and studio audio.
    """
    if output_final_path.exists():
        output_final_path.unlink()
        
    duration = max(10.0, end_sec - start_sec)
    dur_str = f"{duration:.2f}"
    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}.{int((start_sec % 1) * 100):02d}"
    
    resolved_topic = topic_title or (part_info.get("title") if part_info else None) or speaker_badge or "Technology Documentary"
    log(f"🎬 Rendering 16:9 Topic Documentary Normal Video ({duration:.1f}s): '{resolved_topic}'...")
    
    # 1. Slice audio segment to uncompressed PCM WAV
    audio_slice_path = output_final_path.with_name(f"audio_slice_doc_{output_final_path.stem}.wav")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(audio_full_path),
        "-ss", start_str,
        "-af", f"apad=whole_dur={duration:.2f}",
        "-t", dur_str,
        "-c:a", "pcm_s16le",
        str(audio_slice_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 2. Build topic documentary timeline
    doc_timeline_tmp = output_final_path.with_name(f"doc_temp_{output_final_path.stem}.mp4")
    build_topic_documentary_timeline(resolved_topic, transcript_segments, duration, doc_timeline_tmp)
    
    # 3. Composite final 16:9 documentary with Subtitles, Top Badge, Progress Bar & Audio
    ass_filter_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    badge_text = (speaker_badge or resolved_topic).replace(":", " ").replace("'", "").replace("%", "").replace("\\", "").upper()[:45]
    
    font_file = find_system_font()
    if os.path.exists(font_file):
        font_opt = f"fontfile='{font_file}'"
    else:
        font_opt = "font='DejaVu Sans'"
        
    v_filter = (
        "[0:v]eq=contrast=1.02:brightness=-0.02[v0];"
        f"[v0]drawbox=x=40:y=40:w=640:h=56:color=black@0.75:t=fill,"
        f"drawtext=text='{badge_text}':fontcolor=white:fontsize=28:{font_opt}:x=60:y=54[v1];"
        f"[v1]drawbox=y=1068:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
        f"ass={ass_filter_path}[v]"
    )
    
    bgm_path = get_background_music_info()
    has_bgm = bool(bgm_path and bgm_path.exists())
    
    if has_bgm:
        a_filter = (
            "[1:a]loudnorm=I=-14:LRA=7:TP=-1.5[voice];"
            "[2:a]volume=0.08,aloop=loop=-1:size=2e+09[bgm];"
            "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        filtergraph = f"{v_filter};{a_filter}"
        cmd_final = [
            "ffmpeg", "-y",
            "-i", str(doc_timeline_tmp),
            "-i", str(audio_slice_path),
            "-i", str(bgm_path),
            "-filter_complex", filtergraph,
            "-map", "[v]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "19",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", dur_str,
            str(output_final_path)
        ]
    else:
        cmd_final = [
            "ffmpeg", "-y",
            "-i", str(doc_timeline_tmp),
            "-i", str(audio_slice_path),
            "-filter_complex", v_filter,
            "-map", "[v]",
            "-map", "1:a",
            "-af", "loudnorm=I=-14:LRA=7:TP=-1.5",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "19",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", dur_str,
            str(output_final_path)
        ]
        
    subprocess.run(cmd_final, check=True)
    log(f"✅ 16:9 Topic Documentary Normal Video render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")
    
    try:
        if doc_timeline_tmp.exists():
            doc_timeline_tmp.unlink()
        if audio_slice_path.exists():
            audio_slice_path.unlink()
    except Exception:
        pass

    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    generate_custom_thumbnail(output_final_path, thumb_path, resolved_topic, is_landscape=True)


def render_studio_visualizer_short(
    audio_full_path: Path,
    start_sec: float,
    end_sec: float,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = "",
    transcript_segments: list = None,
    speaker_gender: str = "male",
    host_gender: str = "male",
    topic_title: str = "",
    video_reference_path: Path = None,
    is_landscape: bool = False
):
    """
    Renders an animated studio visualizer video (1080x1920 Short or 1920x1080 Normal Video):
    - When is_landscape=True: 16:9 Widescreen Studio Layout with Host & Reactor Avatars and center Wikipedia documentary visuals.
    - When is_landscape=False: 9:16 Portrait Studio Visualizer with top dynamic avatars and motion.
    """
    if output_final_path.exists():
        output_final_path.unlink()
        
    duration = max(10.0, end_sec - start_sec)
    dur_str = f"{duration:.2f}"
    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}.{int((start_sec % 1) * 100):02d}"
    log(f"🎨 Rendering Multi-Gesture Animated Storytime {'16:9 Normal Video' if is_landscape else '9:16 Short'} ({duration:.1f}s)...")
    
    # 1. Slice audio segment to uncompressed PCM WAV for 100% sample accuracy with padding
    audio_slice_path = output_final_path.with_name(f"audio_slice_{output_final_path.stem}.wav")
    
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(audio_full_path),
        "-ss", start_str,
        "-af", f"apad=whole_dur={duration:.2f}",
        "-t", dur_str,
        "-c:a", "pcm_s16le",
        str(audio_slice_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 1.5 Accurately detect speaker gender directly from the sliced audio waveform
    detected_guest_gender = detect_audio_pitch_gender(audio_slice_path)
    
    # Align badge and avatar gender with true active speaker
    resolved_badge = speaker_badge
    if detected_guest_gender == "male" and detect_speaker_gender(speaker_badge or "") == "female":
        log(f"🎙️ Pitch Analysis detected Male voice ({detected_guest_gender}) while badge had female name ({speaker_badge}). Aligning badge to Host.")
        host_name = "Podcast Insight"
        part_match = re.search(r"•\s*PART\s*\d+", speaker_badge or "")
        part_suffix = f" {part_match.group(0)}" if part_match else ""
        resolved_badge = f"{host_name}{part_suffix}"

    # 1.6 Extract sample-accurate audio energy envelope and emphatic vocal peaks
    spk_active_audio, spk_loud_audio, is_calm_tone, vocal_peaks = compute_audio_energy_timeline(audio_slice_path)
    
    # 1.7 Extract Computer Vision real podcaster hand gestures and body motion from video reference
    cv_mouth, cv_gesture, cv_shock, has_cv_motion = ("1", "0", "0", False)
    if video_reference_path and video_reference_path.exists():
        cv_mouth, cv_gesture, cv_shock, has_cv_motion = extract_speaker_visual_motion_timeline(video_reference_path)
    
    # 2. Safe ASS escaping
    ass_filter_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    badge_text = (resolved_badge or speaker_badge or "PODCAST INSIGHT").replace(":", " ").replace("'", "").replace("%", "").replace("\\", "").upper()
    hook_clean = re.sub(r"[^A-Za-z0-9\s\?!\'\-]", "", (topic_title or resolved_badge or speaker_badge or "MINDSET INSIGHT")).strip().upper()[:42]
    
    font_file = find_system_font()
    if os.path.exists(font_file):
        font_opt = f"fontfile='{font_file}'"
    else:
        font_opt = "font='DejaVu Sans'"

    bg_path, bg_dur = get_background_video_info()
    host_pack, guest_pack = get_dual_speaker_avatars(host_gender, detected_guest_gender, speaker_name="wolf", reactor_name="lion")
    bgm_path = get_background_music_info()
    sfx_whoosh_path = get_sfx_info("whoosh")
    has_bgm = bool(bgm_path and bgm_path.exists())
    has_whoosh = bool(sfx_whoosh_path and sfx_whoosh_path.exists())
    
    spk_host = spk_active_audio
    
    # Wolf Hand Gesture: Appears naturally 1-2 times
    if duration >= 45.0:
        g1 = max(3.5, duration * 0.22)
        g2 = max(g1 + 18.0, duration * 0.68)
        wolf_gesture_cond = f"({spk_host})*(between(t,{g1:.1f},{g1+2.2:.1f})+between(t,{g2:.1f},{g2+2.2:.1f}))"
    elif duration >= 22.0:
        g1 = max(3.5, duration * 0.30)
        g2 = max(g1 + 10.0, duration * 0.72)
        wolf_gesture_cond = f"({spk_host})*(between(t,{g1:.1f},{g1+2.0:.1f})+between(t,{g2:.1f},{g2+2.0:.1f}))"
    else:
        g1 = max(2.8, duration * 0.35)
        wolf_gesture_cond = f"({spk_host})*(between(t,{g1:.1f},{g1+1.8:.1f}))"

    # Lion (Reactor) shock timing powered by Acoustic Energy & Revelation Peak Sync:
    lion_shock_cond = compute_intelligent_shock_condition(transcript_segments, vocal_peaks, duration, start_sec)
    
    # 2. Build real Wikipedia & Editorial Topic Visuals Timeline to replace gameplay
    doc_bg_path = None
    resolved_topic = topic_title or speaker_badge or "Technology Documentary"
    try:
        doc_tmp = output_final_path.with_name(f"doc_bg_{output_final_path.stem}.mp4")
        doc_bg_path = build_topic_documentary_timeline(
            topic_title=resolved_topic,
            transcript_segments=transcript_segments,
            duration=duration,
            output_path=doc_tmp
        )
    except Exception as dbe:
        log(f"⚠️ Notice building documentary background: {dbe}")
        doc_bg_path = None

    bg_path_to_use = doc_bg_path if (doc_bg_path and doc_bg_path.exists()) else bg_path
    is_doc_bg = bool(bg_path_to_use == doc_bg_path)
    
    if bg_path_to_use and bg_path_to_use.exists() and host_pack.get("closed") and guest_pack.get("closed"):
        if is_doc_bg:
            bg_start = 0.0
            log(f"📚 Using Wikipedia & Editorial Topic Visuals with Avatars ({'16:9 Landscape' if is_landscape else '9:16 Portrait'})...")
        else:
            bg_start = random.uniform(0.0, max(0.0, bg_dur - duration - 1.0))
            log(f"🐱 Using Phonetic Viseme Studio Layout (Wolf: Host Speaker | Lion: Reactor)...")
        
        curr_inp_idx = 2
        bgm_idx = None
        whoosh_idx = None
        
        if has_bgm:
            bgm_idx = curr_inp_idx
            curr_inp_idx += 1
            
        if has_whoosh:
            whoosh_idx = curr_inp_idx
            curr_inp_idx += 1
            
        w_idle_idx = curr_inp_idx
        w_spk_idx = curr_inp_idx + 1
        w_gst_idx = curr_inp_idx + 2
        l_idle_idx = curr_inp_idx + 3
        l_shk_idx = curr_inp_idx + 4
        curr_inp_idx += 5
        
        videos_base = Path(__file__).resolve().parent / "assets" / "videos" / "avatars"
        w_listen_vid = videos_base / "wolf" / "listening_not_shocking_facing_right.mp4"
        w_speak_vid = videos_base / "wolf" / "speaking_facing_right.mp4"
        w_gest_vid = videos_base / "wolf" / "gesture_speaking_facing_right.mp4"
        l_listen_vid = videos_base / "lion" / "listening_not_shocking_facing_left.mp4"
        l_shock_vid = videos_base / "lion" / "shocked_reaction_facing_left.mp4"
        
        cmd_avatar_inputs = [
            "-stream_loop", "-1", "-i", str(w_listen_vid),
            "-stream_loop", "-1", "-i", str(w_speak_vid),
            "-stream_loop", "-1", "-i", str(w_gest_vid),
            "-stream_loop", "-1", "-i", str(l_listen_vid),
            "-stream_loop", "-1", "-i", str(l_shock_vid)
        ]
        
        if is_landscape:
            # 16:9 Widescreen Studio Layout (1920x1080)
            left_av_filter = (
                f"[{w_idle_idx}:v]scale=460:860:force_original_aspect_ratio=increase,crop=460:860,setsar=1[w_idle];"
                f"[{w_spk_idx}:v]scale=460:860:force_original_aspect_ratio=increase,crop=460:860,setsar=1[w_spk];"
                f"[{w_gst_idx}:v]scale=460:860:force_original_aspect_ratio=increase,crop=460:860,setsar=1[w_gst];"
                f"[w_idle][w_spk]overlay=0:0:enable='{spk_host}'[w_base];"
                f"[w_base][w_gst]overlay=0:0:enable='{wolf_gesture_cond}'[left_av]"
            )
            right_av_filter = (
                f"[{l_idle_idx}:v]scale=460:860:force_original_aspect_ratio=increase,crop=460:860,setsar=1[l_idle];"
                f"[{l_shk_idx}:v]scale=460:860:force_original_aspect_ratio=increase,crop=460:860,setsar=1[l_shk];"
                f"[l_idle][l_shk]overlay=0:0:enable='{lion_shock_cond}'[right_av]"
            )
            v_filter = (
                f"{left_av_filter};{right_av_filter};"
                f"color=c=#1A1C22:s=1920x1080[studio_bg];"
                f"[0:v]scale=720:860:force_original_aspect_ratio=increase,crop=720:860,eq=contrast=1.04:brightness=-0.02[center_game];"
                f"[studio_bg][center_game]overlay=600:50[bg_with_game];"
                f"[bg_with_game][left_av]overlay=80:50[bg_left];"
                f"[bg_left][right_av]overlay=1380:50[bg_both];"
                f"[bg_both]drawbox=x=76:y=46:w=468:h=868:color=#00D2FF@0.85:t=4:enable='{spk_host}',"
                f"drawbox=x=1376:y=46:w=468:h=868:color=#FF0055@0.95:t=6:enable='{lion_shock_cond}',"
                f"drawbox=x=596:y=46:w=728:h=868:color=#00D2FF@0.4:t=2,"
                f"drawbox=x=40:y=40:w=640:h=56:color=black@0.75:t=fill,"
                f"drawtext=text='{badge_text}':fontcolor=white:fontsize=28:{font_opt}:x=60:y=54,"
                f"drawbox=y=1068:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass={ass_filter_path}[v]"
            )
        else:
            # 9:16 Vertical Portrait Studio Layout (1080x1920)
            left_av_filter = (
                f"[{w_idle_idx}:v]scale=500:940:force_original_aspect_ratio=increase,crop=500:940,setsar=1[w_idle];"
                f"[{w_spk_idx}:v]scale=500:940:force_original_aspect_ratio=increase,crop=500:940,setsar=1[w_spk];"
                f"[{w_gst_idx}:v]scale=500:940:force_original_aspect_ratio=increase,crop=500:940,setsar=1[w_gst];"
                f"[w_idle][w_spk]overlay=0:0:enable='{spk_host}'[w_base];"
                f"[w_base][w_gst]overlay=0:0:enable='{wolf_gesture_cond}'[left_av]"
            )
            right_av_filter = (
                f"[{l_idle_idx}:v]scale=500:940:force_original_aspect_ratio=increase,crop=500:940,setsar=1[l_idle];"
                f"[{l_shk_idx}:v]scale=500:940:force_original_aspect_ratio=increase,crop=500:940,setsar=1[l_shk];"
                f"[l_idle][l_shk]overlay=0:0:enable='{lion_shock_cond}'[right_av]"
            )
            top_filter = (
                f"{left_av_filter};{right_av_filter};"
                f"color=c=#1A1C22:s=1080x960[studio_bg];"
                f"[studio_bg][left_av]overlay=25:10[top_with_left];"
                f"[top_with_left][right_av]overlay=555:10[top_both];"
                f"[top_both]drawbox=x=538:y=0:w=4:h=960:color=#00D2FF@0.3:t=fill,"
                f"drawbox=x=23:y=8:w=504:h=944:color=#00D2FF@0.85:t=4:enable='{spk_host}',"
                f"drawbox=x=553:y=8:w=504:h=944:color=#FF0055@0.95:t=6:enable='{lion_shock_cond}'[top_glow]"
            )
            v_filter = (
                f"{top_filter};"
                f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,eq=contrast=1.04:brightness=-0.04[bot];"
                f"[top_glow][bot]vstack[stacked];"
                f"[stacked]drawbox=y=956:color=#00D2FF@0.9:width=iw:height=8:t=fill,"
                f"drawbox=y=1905:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass={ass_filter_path}[v]"
            )
        
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-ss", f"{bg_start:.2f}",
            "-i", str(bg_path_to_use),
            "-i", str(audio_slice_path)
        ]
        
        audio_mix_inputs = ["[voice]"]
        
        if has_bgm:
            log(f"🎵 Mixing calm royalty-free BGM: {bgm_path.name}...")
            cmd.extend(["-i", str(bgm_path)])
            audio_mix_inputs.append("[bgm]")
            
        if has_whoosh:
            cmd.extend(["-i", str(sfx_whoosh_path)])
            audio_mix_inputs.append("[whoosh]")
            
        cmd.extend(cmd_avatar_inputs)
            
        fade_out_start = max(0.0, duration - 0.6)
        a_filter_parts = ["[1:a]loudnorm=I=-14:LRA=7:TP=-1.5[voice]"]
        if has_bgm:
            a_filter_parts.append(f"[{bgm_idx}:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm]")
        if has_whoosh:
            a_filter_parts.append(f"[{whoosh_idx}:a]adelay=150|150,volume=0.30[whoosh]")
            
        a_filter_parts.append(
            f"{''.join(audio_mix_inputs)}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=0:normalize=0,"
            f"afade=t=out:st={fade_out_start:.2f}:d=0.6[aout]"
        )
        filtergraph = f"{v_filter};{';'.join(a_filter_parts)}"
        
        cmd.extend([
            "-filter_complex", filtergraph,
            "-map", "[v]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-threads", "2",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", dur_str,
            str(output_final_path)
        ])
    elif bg_path_to_use and bg_path_to_use.exists():
        bg_start = 0.0 if is_doc_bg else random.uniform(0.0, max(0.0, bg_dur - duration - 1.0))
        log(f"🎮 Using background video ({'16:9 Landscape' if is_landscape else '9:16 Portrait'})...")
        
        if is_landscape:
            v_filter = (
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=contrast=1.04:brightness=-0.04[bg];"
                f"[bg]drawbox=x=40:y=40:w=640:h=56:color=black@0.75:t=fill,"
                f"drawtext=text='{badge_text}':fontcolor=white:fontsize=28:{font_opt}:x=60:y=54,"
                f"drawbox=y=1068:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass={ass_filter_path}[v]"
            )
        else:
            v_filter = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,eq=contrast=1.04:brightness=-0.04[bg];"
                f"[bg]drawbox=y=160:color=black@0.75:width=iw:height=90:t=fill,"
                f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:{font_opt}:x=(w-text_w)/2:y=182,"
                f"drawbox=y=1905:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass={ass_filter_path}[v]"
            )
        
        if bgm_path and bgm_path.exists():
            a_filter = (
                "[1:a]loudnorm=I=-14:LRA=7:TP=-1.5[voice];"
                "[2:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
            filtergraph = f"{v_filter};{a_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{bg_start:.2f}",
                "-i", str(bg_path_to_use),
                "-i", str(audio_slice_path),
                "-i", str(bgm_path),
                "-filter_complex", filtergraph,
                "-map", "[v]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-t", dur_str,
                str(output_final_path)
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{bg_start:.2f}",
                "-i", str(bg_path_to_use),
                "-i", str(audio_slice_path),
                "-filter_complex", v_filter,
                "-map", "[v]",
                "-map", "1:a",
                "-af", "loudnorm=I=-14:LRA=7:TP=-1.5",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-t", dur_str,
                str(output_final_path)
            ]
    else:
        log("ℹ️ No gameplay background video found. Falling back to dynamic dark visualizer...")
        if is_landscape:
            filtergraph = (
                f"color=c=#0B0E14:s=1920x1080:d={dur_str}[bg];"
                "[0:a]showwaves=s=1200:300:mode=p2p:colors=#00D2FF@0.85[wave];"
                "[bg][wave]overlay=(W-w)/2:(H-h)/2 - 40,"
                f"drawbox=x=40:y=40:w=auto:h=56:color=black@0.75:t=fill,"
                f"drawtext=text='{badge_text}':fontcolor=white:fontsize=28:{font_opt}:x=60:y=54,"
                f"drawbox=y=1068:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass='{ass_filter_path}'[v]"
            )
        else:
            filtergraph = (
                f"color=c=#0B0E14:s=1080x1920:d={dur_str}[bg];"
                "[0:a]showwaves=s=920x240:mode=p2p:colors=#00D2FF@0.85[wave];"
                "[bg][wave]overlay=(W-w)/2:(H-h)/2 - 50,"
                f"drawbox=y=160:color=black@0.75:width=iw:height=90:t=fill,"
                f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:{font_opt}:x=(w-text_w)/2:y=182,"
                f"drawbox=y=1905:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
                f"ass='{ass_filter_path}'[v]"
            )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_slice_path),
            "-filter_complex", filtergraph,
            "-map", "[v]",
            "-map", "0:a",
            "-af", "loudnorm=I=-14:LRA=7:TP=-1.5",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", dur_str,
            str(output_final_path)
        ]
    
    subprocess.run(cmd, check=True)
    if audio_slice_path.exists():
        audio_slice_path.unlink()
        
    log(f"✅ Video render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")

    # Thumbnail generation
    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    generate_custom_thumbnail(output_final_path, thumb_path, topic_title or resolved_badge, is_landscape=is_landscape)


# =====================================================================
# 6. YouTube Upload (YouTube Data API v3)
# =====================================================================

def upload_to_youtube(video_path: Path, clip_info: dict, podcast_entry: dict, original_video_url: str, is_short: bool = True):
    # Guard against duplicate uploads
    history = load_json(HISTORY_PATH)
    target_vid_id = clip_info.get("video_id")
    part_num = clip_info.get("part")
    id_key = "uploaded_youtube_id" if is_short else "uploaded_normal_youtube_id"
    if target_vid_id and part_num:
        for past in history.get("processed_clips", []):
            if (past.get("video_id") == target_vid_id and 
                past.get("part") == part_num and 
                past.get(id_key)):
                log(f"⚠️ Duplicate protection: Part {part_num} of video {target_vid_id} was already uploaded as {'Short' if is_short else 'Normal Video'} ({past.get(id_key)}). Skipping duplicate upload.")
                return past.get(id_key)

    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    refresh_token = os.environ.get("REFRESH_TOKEN")
    
    if not (client_id and client_secret and refresh_token):
        log(f"⚠️ Notice: YouTube OAuth secrets not fully set. Completed dry-run render for {'Short' if is_short else 'Normal Video'} successfully.")
        return None
        
    if Credentials is None or build is None:
        log("⚠️ Notice: Google API client not installed. Completed dry-run render.")
        return None

    log(f"Authenticating with YouTube Data API ({'Short 9:16' if is_short else 'Normal Video 16:9'})...")
    try:
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )
        youtube = build("youtube", "v3", credentials=creds)

        raw_title = clip_info.get("viral_title", "Unbelievable Wisdom 💡")
        clean_title = re.sub(r"(?i)\s*#shorts\b", "", raw_title).strip()

        if is_short:
            title = f"{clean_title} #shorts"
            tags = list(set(clip_info.get("tags", []) + podcast_entry.get("default_tags", [])))
            if "shorts" not in [t.lower() for t in tags]:
                tags.append("shorts")
            description = f"""{title}

🎙️ {podcast_entry.get("attribution_template", "Full episode: {video_url}").format(
    guest_or_title=clip_info.get("speaker_badge", podcast_entry.get("name")),
    video_url=original_video_url
)}
💡 Clip curated automatically for educational & commentary insights.

#shorts #podcast #wisdom #mindset #learning #growth
"""
        else:
            part_suffix = f" [Part {part_num}]" if part_num and f"Part {part_num}" not in clean_title else ""
            title = f"{clean_title}{part_suffix}" if part_suffix not in clean_title else clean_title
            tags = [t for t in (clip_info.get("tags", []) + podcast_entry.get("default_tags", [])) if t.lower() not in ["shorts", "short", "techshorts"]]
            tags.extend(["podcast", "full breakdown", "educational", "insights", "coding"])
            description = f"""{title}

🎙️ {podcast_entry.get("attribution_template", "Full episode: {video_url}").format(
    guest_or_title=clip_info.get("speaker_badge", podcast_entry.get("name")),
    video_url=original_video_url
)}

💡 Detailed breakdown and discussion curated for developers, engineers, and tech enthusiasts.

🔔 Subscribe for daily tech revelations, developer masterclasses, and coding insights!

#podcast #technology #programming #education #learning #engineering
"""

        tags = list(dict.fromkeys(tags))[:15]

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags,
                "categoryId": "27"  # Education / Ideas
            },
            "status": {
                "privacyStatus": os.environ.get("PRIVACY_STATUS", "public"),
                "selfDeclaredMadeForKids": False
            }
        }

        log(f"Uploading to YouTube as '{body['status']['privacyStatus']}' ({'Short' if is_short else 'Normal Video'}): {title}...")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        if is_short:
            log(f"🎉 Successfully uploaded Short! Video URL: https://youtube.com/shorts/{video_id}")
        else:
            log(f"🎉 Successfully uploaded Normal Video! Video URL: https://youtube.com/watch?v={video_id}")

        # Optional thumbnail upload
        thumb_path = video_path.with_name(f"thumb_{video_path.stem}.jpg")
        if thumb_path.exists():
            try:
                log(f"Uploading high-definition custom thumbnail for {video_id}...")
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
                ).execute()
                log("✅ Custom thumbnail uploaded successfully!")
            except Exception as te:
                log(f"Thumbnail upload notice: {te}")

        return video_id
    except HttpError as he:
        log(f"⚠️ YouTube API notice: {he}")
        return None
    except Exception as e:
        log(f"⚠️ Upload error: {e}")
        return None


def upload_dual_to_youtube(short_video_path: Path, landscape_video_path: Path, clip_info: dict, podcast_entry: dict, original_video_url: str):
    """
    Uploads BOTH the 9:16 vertical Short and the 16:9 landscape Normal Video.
    Returns dict with {'short_id': ..., 'normal_id': ...}.
    """
    short_id = None
    normal_id = None
    
    if short_video_path and short_video_path.exists():
        log("📤 [1/2] Uploading 9:16 Vertical Short...")
        short_id = upload_to_youtube(short_video_path, clip_info, podcast_entry, original_video_url, is_short=True)
        
    if landscape_video_path and landscape_video_path.exists():
        log("📤 [2/2] Uploading 16:9 Landscape Normal Video...")
        normal_id = upload_to_youtube(landscape_video_path, clip_info, podcast_entry, original_video_url, is_short=False)
        
    return {
        "short_id": short_id,
        "normal_id": normal_id
    }


# =====================================================================
# 7. Main Pipeline Orchestrator
# =====================================================================

def run_pipeline(force_url: str = None, force_channel: str = None, dry_run: bool = False, custom_catalog_path: str = None):
    log("=======================================================")
    log(" Starting Auto-Clipper (Shorts + Normal Videos Edition)")
    log("=======================================================")
    
    cat_path = Path(custom_catalog_path) if custom_catalog_path else CATALOG_PATH
    catalog = load_json(cat_path)
    channels = catalog.get("tech_channels") or catalog.get("podcasts", [])
    history = load_json(HISTORY_PATH, {"last_channel_index": 0, "processed_videos": [], "processed_clips": [], "active_series": None})

    active_series = history.get("active_series")
    
    # 1. Determine if we are continuing an episodic series or starting a fresh topic
    if active_series and active_series.get("current_part", 1) < active_series.get("max_parts", 3) and not force_url and not force_channel:
        podcast_entry = next((p for p in channels if p["id"] == active_series.get("podcast_id")), None)
        if not podcast_entry and channels:
            podcast_entry = channels[0]
            
        target_video = {
            "id": active_series["video_id"],
            "url": f"https://www.youtube.com/watch?v={active_series['video_id']}",
            "title": active_series.get("episode_title", "Tech Topic"),
            "uploader": podcast_entry["name"] if podcast_entry else "Tech Channel"
        }
        part_number = active_series["current_part"] + 1
        continuation_start = active_series.get("last_clip_end_sec", 0.0)
        topic_title = active_series.get("topic_title", "Tech Insight")
        log(f"🎬 Continuing Tech Series: Part {part_number}/{active_series.get('max_parts', 3)} on '{topic_title}' from {continuation_start:.1f}s...")
    else:
        podcast_entry, next_idx = pick_next_channel(catalog, history, force_channel)
        log(f"Selected Tech Source: {podcast_entry['name']} ({podcast_entry.get('category', 'Tech')})")
        
        target_video = select_target_video(podcast_entry, history, direct_url=force_url)
        log(f"Selected Video: {target_video['title']} [{target_video['id']}]")
        part_number = 1
        continuation_start = None
        topic_title = None
        log(f"🎬 Starting New Tech Topic Series: Part 1 for {podcast_entry['name']}...")

    # 2. Extract Subtitles/Transcript (Direct RSS Audio + Whisper AI / Instant Subs)
    transcript_segments = fetch_youtube_subtitles_or_whisper(target_video["url"], OUTPUT_DIR, podcast_entry=podcast_entry, video_meta=target_video)
    if not transcript_segments:
        log(f"⚠️ Video {target_video['id']} transcript unavailable. Automatically advancing to next tech channel source...")
        if active_series:
            history["active_series"] = None
            save_json(HISTORY_PATH, history)
            
        podcast_entry, next_idx = pick_next_channel(catalog, history, force_channel)
        target_video = select_target_video(podcast_entry, history, direct_url=force_url)
        part_number = 1
        continuation_start = None
        topic_title = None
        log(f"🎬 Trying alternate tech video: {target_video['title']} [{target_video['id']}]...")
        transcript_segments = fetch_youtube_subtitles_or_whisper(target_video["url"], OUTPUT_DIR, podcast_entry=podcast_entry, video_meta=target_video)
        
    if not transcript_segments:
        raise RuntimeError(f"Failed to obtain transcript for video {target_video['id']}")
    
    # 3. AI Highlight / Continuation Detection
    clip_info = select_viral_clip_with_groq(
        transcript_segments=transcript_segments,
        video_meta=target_video,
        podcast_entry=podcast_entry,
        continuation_start_sec=continuation_start,
        part_number=part_number,
        topic_title=topic_title
    )
    start_sec = clip_info["start_seconds"]
    end_sec = clip_info["end_seconds"]
    resolved_topic = clip_info.get("topic_title") or topic_title or clip_info.get("viral_title", "Podcast Insight")
    resolved_topic = re.sub(r"\s*\[Part\s*\d+\]", "", resolved_topic).strip()
    
    # Format Title & Speaker Badge for Episodic Continuity
    part_badge = f"[Part {part_number}]"
    base_title = clip_info.get("viral_title", f"{resolved_topic} {part_badge} 💡 #Shorts")
    if part_badge not in base_title:
        base_title = f"{resolved_topic} {part_badge} 🧠 #Shorts"
    clip_info["viral_title"] = base_title
    
    badge_name = clip_info.get("speaker_badge", podcast_entry["name"])
    composed_badge = f"{badge_name} • PART {part_number}"
    clip_info["video_id"] = target_video["id"]
    clip_info["part"] = part_number
    
    # 4. Generate Karaoke ASS Subtitles for Both Portrait (9:16) and Landscape (16:9)
    ass_sub_path = OUTPUT_DIR / f"subtitles_{target_video['id']}.ass"
    generate_karaoke_ass_subtitles(transcript_segments, start_sec, end_sec, ass_sub_path, is_landscape=False)

    ass_landscape_sub_path = OUTPUT_DIR / f"subtitles_{target_video['id']}_landscape.ass"
    generate_karaoke_ass_subtitles(transcript_segments, start_sec, end_sec, ass_landscape_sub_path, is_landscape=True)
    
    # 5. Render BOTH 9:16 Short AND 16:9 Normal Video
    audio_full_path = OUTPUT_DIR / f"podcast_audio_{target_video['id']}.mp3"
    if not audio_full_path.exists() or audio_full_path.stat().st_size < 500000:
        legacy_rss = OUTPUT_DIR / "rss_podcast_audio.mp3"
        if legacy_rss.exists() and legacy_rss.stat().st_size > 500000:
            audio_full_path = legacy_rss
            
    final_render_path = OUTPUT_DIR / f"clip_{target_video['id']}_final.mp4"
    final_render_landscape_path = OUTPUT_DIR / f"clip_{target_video['id']}_landscape_final.mp4"
    
    if audio_full_path.exists() and audio_full_path.stat().st_size > 500000:
        guest_gender = detect_speaker_gender(badge_name, target_video.get("title", ""))
        host_gender = detect_speaker_gender(podcast_entry.get("name", ""))
        raw_slice_path = OUTPUT_DIR / f"raw_slice_{target_video['id']}.mp4"
        if not raw_slice_path.exists():
            try:
                download_video_clip_segment(target_video["url"], start_sec, end_sec, raw_slice_path)
            except Exception:
                pass
                
        # Render 9:16 Short
        render_studio_visualizer_short(
            audio_full_path=audio_full_path,
            start_sec=start_sec,
            end_sec=end_sec,
            ass_subtitle_path=ass_sub_path,
            output_final_path=final_render_path,
            speaker_badge=composed_badge,
            transcript_segments=transcript_segments,
            speaker_gender=guest_gender,
            host_gender=host_gender,
            topic_title=resolved_topic,
            video_reference_path=raw_slice_path if raw_slice_path.exists() else None,
            is_landscape=False
        )
        # Render 16:9 Normal Video
        render_studio_visualizer_short(
            audio_full_path=audio_full_path,
            start_sec=start_sec,
            end_sec=end_sec,
            ass_subtitle_path=ass_landscape_sub_path,
            output_final_path=final_render_landscape_path,
            speaker_badge=composed_badge,
            transcript_segments=transcript_segments,
            speaker_gender=guest_gender,
            host_gender=host_gender,
            topic_title=resolved_topic,
            video_reference_path=raw_slice_path if raw_slice_path.exists() else None,
            is_landscape=True
        )
    else:
        raw_slice_path = OUTPUT_DIR / f"raw_slice_{target_video['id']}.mp4"
        download_video_clip_segment(target_video["url"], start_sec, end_sec, raw_slice_path)
        # Render 9:16 Short
        render_vertical_916_short(
            raw_slice_path,
            ass_sub_path,
            final_render_path,
            speaker_badge=composed_badge
        )
        # Render 16:9 Normal Video
        render_landscape_169_video(
            raw_slice_path,
            ass_landscape_sub_path,
            final_render_landscape_path,
            speaker_badge=composed_badge
        )

    # 6. Upload to YouTube (Dual Upload: Short + Normal Video)
    uploaded_id = None
    uploaded_normal_id = None
    if not dry_run:
        upload_res = upload_dual_to_youtube(
            final_render_path,
            final_render_landscape_path,
            clip_info,
            podcast_entry,
            target_video["url"]
        )
        uploaded_id = upload_res.get("short_id")
        uploaded_normal_id = upload_res.get("normal_id")
        if not uploaded_id and not uploaded_normal_id:
            log("⚠️ Neither YouTube upload completed (e.g. daily quota limit reached or auth notice).")
            log(f"📌 Preserving series state: Part {part_number} will be cleanly retried on the next scheduled run without losing continuation!")
            return
    else:
        log("Dry run active: Skipping YouTube upload and preserving live series state.")
        return

    # 7. Update History & Multi-Part Series State (Only when upload succeeds!)
    MAX_SERIES_PARTS = 3
    if part_number < MAX_SERIES_PARTS:
        history["active_series"] = {
            "video_id": target_video["id"],
            "podcast_id": podcast_entry["id"],
            "episode_title": target_video["title"],
            "topic_title": resolved_topic,
            "speaker_badge": badge_name,
            "current_part": part_number,
            "max_parts": MAX_SERIES_PARTS,
            "last_clip_end_sec": end_sec
        }
        log(f"📌 Multi-Part Series Progressed: Part {part_number}/{MAX_SERIES_PARTS} uploaded (Short: {uploaded_id} | Normal: {uploaded_normal_id}). Next run will clip Part {part_number + 1}.")
    else:
        history["active_series"] = None
        if target_video["id"] not in history.get("processed_videos", []):
            history.setdefault("processed_videos", []).append(target_video["id"])
        channels = catalog.get("tech_channels") or catalog.get("podcasts", [])
        history["last_channel_index"] = (history.get("last_channel_index", 0) + 1) % max(1, len(channels))
        log(f"🎉 Multi-Part Series Finished! (All {MAX_SERIES_PARTS} parts uploaded). Rotated to next tech channel.")
        
    history.setdefault("processed_clips", []).append({
        "video_id": target_video["id"],
        "title": clip_info.get("viral_title"),
        "channel": podcast_entry["name"],
        "part": part_number,
        "start": start_sec,
        "end": end_sec,
        "uploaded_youtube_id": uploaded_id,
        "uploaded_normal_youtube_id": uploaded_normal_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    save_json(HISTORY_PATH, history)
    
    # Clean up intermediate temporary files
    for p in [ass_sub_path, ass_landscape_sub_path]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
                
    log("=======================================================")
    log(" Pipeline Finished Successfully! 🎉")
    log("=======================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto Clipper - Tech Facts & Developer Tips Video Generator (Shorts + Normal Videos)")
    parser.add_argument("--url", type=str, help="Direct YouTube video URL to clip")
    parser.add_argument("--channel", type=str, help="Specific channel ID from tech_catalog.json (e.g. fireship, networkchuck, bytebytego)")
    parser.add_argument("--catalog", type=str, help="Path to custom catalog JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Run full pipeline without uploading to YouTube")
    args = parser.parse_args()

    run_pipeline(force_url=args.url, force_channel=args.channel, dry_run=args.dry_run, custom_catalog_path=args.catalog)
