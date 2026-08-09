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
CATALOG_PATH = WORKSPACE_DIR / "podcast_catalog.json"
HISTORY_PATH = WORKSPACE_DIR / "clip_history.json"
OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
FALLBACK_GROQ_MODEL = "llama-3.1-8b-instant"

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
    podcasts = catalog.get("podcasts", [])
    if not podcasts:
        raise ValueError("No podcasts found in podcast_catalog.json!")

    if force_channel_id:
        for idx, p in enumerate(podcasts):
            if p["id"] == force_channel_id:
                return p, idx

    last_idx = history.get("last_channel_index", -1)
    next_idx = (last_idx + 1) % len(podcasts)
    return podcasts[next_idx], next_idx


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
        if words and end_sec > words[-1]["start"]:
            words[-1]["end"] = round(end_sec, 2)
            
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


def download_audio_via_rss(rss_feed_url: str, output_audio_path: Path) -> dict:
    """
    Downloads audio directly from the podcast's official RSS feed and extracts real episode metadata.
    Completely immune to YouTube BotGuard blocks and requires 0 cookies.
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
        item = root.find(".//item")
        if item is None:
            log("⚠️ No item found in RSS feed.")
            return {"success": False}
            
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            log("⚠️ No audio enclosure found in RSS feed.")
            return {"success": False}

        title_el = item.find("title")
        ep_title = title_el.text.strip() if (title_el is not None and title_el.text) else "Podcast Episode"
        
        # Look for itunes:author
        author_el = item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}author")
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
    1. Download audio via official Podcast RSS feed (100% BotGuard immune, 0 cookies needed) -> Whisper AI.
    2. Download instant YouTube captions (manual/auto-generated json3/vtt).
    3. Fallback to yt-dlp audio download -> Whisper AI.
    """
    # 1. Try Podcast RSS feed audio first (Zero BotGuard / Zero Captchas)
    if podcast_entry and podcast_entry.get("rss_feed"):
        rss_audio_path = output_base / "rss_podcast_audio.mp3"
        rss_info = download_audio_via_rss(podcast_entry["rss_feed"], rss_audio_path)
        if rss_info.get("success"):
            if video_meta is not None and rss_info.get("title"):
                video_meta["title"] = rss_info["title"]
                if rss_info.get("author"):
                    video_meta["uploader"] = rss_info["author"]
            try:
                log("Transcribing clean RSS podcast audio with Whisper AI...")
                return transcribe_audio_with_whisper(rss_audio_path, model_size="base.en")
            except Exception as we:
                log(f"⚠️ Whisper AI notice: {we}. Trying instant YouTube captions...")

    log(f"Checking for instant YouTube captions for {video_url}...")
    sub_prefix = output_base / "subs_temp"
    
    # Try downloading JSON3 / VTT English subtitles
    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--write-subs",
        "--sub-lang", "en.*,en",
        "--sub-format", "json3/vtt/srt",
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        "--extractor-args", "youtube:player_client=mweb,web,default",
    ] + ytdlp_cookies_args() + [
        "-o", f"{sub_prefix}.%(ext)s",
        video_url
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Check if json3 or vtt file was created
        json3_files = list(output_base.glob("subs_temp*.json3"))
        if json3_files:
            log(f"Found YouTube JSON3 captions: {json3_files[0].name}")
            with open(json3_files[0], "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = parse_json3_subtitles(data)
            if segments and len(segments) > 5:
                log(f"Parsed {len(segments)} segments with word timestamps from YouTube captions.")
                # Clean up
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
    except Exception as e:
        log(f"YouTube caption extraction notice: {e}")

    # Fallback to Whisper AI audio transcription
    log("Falling back to Whisper AI audio transcription via yt-dlp...")
    audio_path = output_base / "whisper_audio.m4a"
    try:
        download_audio_for_transcription(video_url, audio_path)
        return transcribe_audio_with_whisper(audio_path, model_size="base.en")
    except Exception as fe:
        log(f"⚠️ Audio transcription fallback error: {fe}")
        return []


def download_audio_for_transcription(video_url: str, output_audio_path: Path) -> Path:
    """Fast audio download (low bitrate m4a) for quick transcription."""
    if output_audio_path.exists():
        output_audio_path.unlink()
        
    log(f"Downloading audio track for transcription: {video_url}")
    cmd = [
        "yt-dlp",
        "-f", "ba/b",
        "-x",
        "--audio-format", "m4a",
        "--audio-quality", "7",
        "--no-playlist",
        "--no-warnings",
        "--extractor-args", "youtube:player_client=mweb,web,default",
    ] + ytdlp_cookies_args() + [
        "-o", str(output_audio_path),
        video_url
    ]
    subprocess.run(cmd, check=True)
    return output_audio_path


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
            "Identify the exact timestamp where the speaker begins introducing a brand new, high-impact topic, secret, principle, or insight (Part 1).\n"
            "The clip MUST start right where they begin sharing this specific information or topic (skip any small talk/intro). Duration: 38-55 seconds."
        )

    # 1. Check if NVIDIA Nemotron 3 Ultra is configured
    if nvidia_api_key:
        for attempt in range(1, 3):
            try:
                log(f"🧠 Querying NVIDIA Nemotron 3 Ultra (550B MoE) for viral highlight (Part {part_number}, attempt {attempt})...")
                n_sys = "You are an expert viral YouTube Shorts content strategist. You MUST return ONLY a single JSON object. Absolutely NO conversational text, reasoning explanation, or markdown wrapper."
                n_user = (
                    f"Podcast: {podcast_entry.get('name', 'Podcast')}\n"
                    f"Episode: {video_meta.get('title', 'Episode')}\n"
                    f"Goal: {goal_instruction}\n\n"
                    f"Transcript:\n{formatted_transcript}\n\n"
                    f"Output ONLY valid raw JSON with this exact schema:\n"
                    f"{{\"start_seconds\": <float>, \"end_seconds\": <float>, \"topic_title\": \"<Core 3-5 word topic title>\", "
                    f"\"viral_title\": \"<Punchy title under 60 chars with 1 emoji and #Shorts>\", \"hook_reason\": \"<string>\", "
                    f"\"tags\": [\"tag1\", \"tag2\"], \"speaker_badge\": \"<Speaker Name>\"}}"
                )
                
                n_payload = json.dumps({
                    "model": "nvidia/nemotron-3-ultra-550b-a55b",
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
                with urllib.request.urlopen(n_req, timeout=75) as n_resp:
                    n_data = json.loads(n_resp.read().decode("utf-8"))
                    content = n_data["choices"][0]["message"]["content"]
                    parsed = parse_llm_json(content)
                    if parsed and "start_seconds" in parsed and "end_seconds" in parsed:
                        min_start = max(0.0, continuation_start_sec + 0.5 if (continuation_start_sec and part_number > 1) else 0.0)
                        start_sec = max(min_start, float(parsed.get("start_seconds", min_start)))
                        raw_end = float(parsed.get("end_seconds", start_sec + 45.0))
                        clip_dur = max(38.0, min(55.0, raw_end - start_sec))
                        end_sec = start_sec + clip_dur
                        parsed["start_seconds"] = round(start_sec, 2)
                        parsed["end_seconds"] = round(end_sec, 2)
                        parsed["duration"] = round(clip_dur, 2)
                        log(f"✅ NVIDIA Nemotron selected clip (Part {part_number}): {parsed['start_seconds']}s -> {parsed['end_seconds']}s ({clip_dur:.1f}s)")
                        return parsed
            except Exception as ne:
                log(f"⚠️ NVIDIA Nemotron notice (attempt {attempt}): {ne}")
                if attempt < 2:
                    time.sleep(2)
                else:
                    log("Falling back to Groq...")

    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        default_start = continuation_start_sec + 1.0 if continuation_start_sec else 30.0
        return {
            "start_seconds": default_start,
            "end_seconds": default_start + 45.0,
            "duration": 45.0,
            "topic_title": f"{podcast_entry.get('name', 'Podcast')} Masterclass",
            "viral_title": f"{podcast_entry.get('name', 'Podcast')} Insight [Part {part_number}] 💡 #Shorts",
            "hook_reason": "Curated insightful discussion",
            "tags": ["podcast", "wisdom", "mindset", "success", "shorts"],
            "speaker_badge": podcast_entry.get("name", "Podcast")
        }
        
    client = Groq(api_key=groq_api_key)
    system_prompt = (
        "You are an expert viral YouTube Shorts content strategist specializing in "
        "podcasts, philosophy, science, and high-impact discussions."
    )
    user_prompt = f"""
Podcast: {podcast_entry.get('name', 'Podcast')}
Episode Title: {video_meta.get('title', 'Episode')}
Goal: {goal_instruction}

Transcript:
{formatted_transcript}

JSON Schema:
{{
  "start_seconds": <float>,
  "end_seconds": <float>,
  "topic_title": "<Core 3-5 word topic title>",
  "viral_title": "<Punchy title under 60 chars with 1 emoji and #Shorts>",
  "hook_reason": "<Why this clip has high viewer retention>",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "speaker_badge": "<Short speaker name, e.g. 'Andrew Huberman' or 'Tom Bilyeu'>"
}}
"""
    log(f"Sending transcript to Groq for highlight detection (Part {part_number})...")
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
            
            min_start = max(0.0, continuation_start_sec + 0.5 if (continuation_start_sec and part_number > 1) else 0.0)
            start_sec = max(min_start, float(clip_data.get("start_seconds", min_start)))
            raw_end = float(clip_data.get("end_seconds", start_sec + 45.0))
            clip_dur = max(38.0, min(55.0, raw_end - start_sec))
            end_sec = start_sec + clip_dur
            
            clip_data["start_seconds"] = round(start_sec, 2)
            clip_data["end_seconds"] = round(end_sec, 2)
            clip_data["duration"] = round(clip_dur, 2)
            log(f"✅ Groq selected clip (Part {part_number}): {clip_data['start_seconds']}s -> {clip_data['end_seconds']}s ({clip_dur:.1f}s)")
            return clip_data
        except Exception as ge:
            log(f"⚠️ Groq highlight detection notice: {ge}")
            
    # Fallback to deterministic sequential slice
    default_start = continuation_start_sec + 1.0 if continuation_start_sec else 30.0
    return {
        "start_seconds": round(default_start, 2),
        "end_seconds": round(default_start + 45.0, 2),
        "duration": 45.0,
        "topic_title": f"{podcast_entry.get('name', 'Podcast')} Masterclass",
        "viral_title": f"{podcast_entry.get('name', 'Podcast')} Insight [Part {part_number}] 💡 #Shorts",
        "hook_reason": "Curated insightful discussion",
        "tags": ["podcast", "wisdom", "mindset", "success", "shorts"],
        "speaker_badge": podcast_entry.get("name", "Insight")
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


def generate_karaoke_ass_subtitles(segments: list, start_sec: float, end_sec: float, output_ass_path: Path):
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,6,3,2,40,40,700,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
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
    GROUP_SIZE = 3
    for i in range(0, len(clip_words), GROUP_SIZE):
        group = clip_words[i:i + GROUP_SIZE]
        if not group:
            continue
        
        for active_idx, target_word in enumerate(group):
            w_start = target_word["start"]
            w_end = target_word["end"]
            
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
            
    log(f"Generated dynamic karaoke subtitles with {len(events)} events.")


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

    # Generate high-impact thumbnail from 5s timestamp
    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", "00:00:05",
            "-i", str(output_final_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if thumb_path.exists():
            log(f"📸 Thumbnail generated: {thumb_path.name} ({thumb_path.stat().st_size // 1024} KB)")
    except Exception as te:
        log(f"Thumbnail frame notice: {te}")


def get_cute_animal_image_info() -> Path:
    """
    Finds one of the cute AI-generated animal images in assets/images/animals/.
    Returns Path if found, else None.
    """
    img_dir = Path(__file__).resolve().parent / "assets" / "images" / "animals"
    if img_dir.exists():
        candidates = sorted(list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png")))
        if candidates:
            return random.choice(candidates)
    return None


def get_background_video_info() -> tuple:
    """
    Finds one of the 3 one-minute Subway Surfers gameplay background videos in assets/backgrounds/.
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


def render_studio_visualizer_short(
    audio_full_path: Path,
    start_sec: float,
    end_sec: float,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = ""
):
    """
    Renders a 1080x1920 split-screen short:
    - Top half (1080x960): Cute AI-generated animal image
    - Bottom half (1080x960): Subway Surfers gameplay footage
    - Audio: Sample-accurate speech + subtle royalty-free BGM with 0 fadeout
    - Overlays: Floating speaker capsule badge, glowing center divider, kinetic karaoke subtitles, bottom retention bar
    """
    if output_final_path.exists():
        output_final_path.unlink()
        
    duration = max(10.0, end_sec - start_sec)
    dur_str = f"{duration:.2f}"
    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}.{int((start_sec % 1) * 100):02d}"
    log(f"🎨 Rendering Split-Screen Short ({duration:.1f}s) with cute animal + Subway Surfers...")
    
    # 1. Slice audio segment to uncompressed PCM WAV for 100% sample accuracy (avoids MP3 frame padding delays)
    audio_slice_path = output_final_path.with_name(f"audio_slice_{output_final_path.stem}.wav")
    
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(audio_full_path),
        "-ss", start_str,
        "-t", dur_str,
        "-c:a", "pcm_s16le",
        str(audio_slice_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 2. Safe ASS escaping
    ass_filter_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    badge_text = (speaker_badge or "PODCAST INSIGHT").replace(":", " ").replace("'", "").replace("%", "").replace("\\", "").upper()
    
    font_file = find_system_font()
    if os.path.exists(font_file):
        font_opt = f"fontfile='{font_file}'"
    else:
        font_opt = "font='DejaVu Sans'"

    bg_path, bg_dur = get_background_video_info()
    animal_path = get_cute_animal_image_info()
    bgm_path = get_background_music_info()
    
    if bg_path and bg_path.exists() and animal_path and animal_path.exists():
        bg_start = random.uniform(0.0, max(0.0, bg_dur - duration - 1.0))
        log(f"🐱 Using cute animal: {animal_path.name} & gameplay: {bg_path.name} (offset {bg_start:.1f}s)...")
        
        # Split-screen: Top half Cute Animal, Bottom half Subway Surfers with divider line
        v_filter = (
            f"[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,loop=loop=-1:size=1:start=0[top];"
            f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,eq=contrast=1.04:brightness=-0.04[bot];"
            f"[top][bot]vstack[stacked];"
            f"[stacked]drawbox=y=956:color=#00D2FF@0.9:width=iw:height=8:t=fill,"
            f"drawbox=y=80:color=black@0.75:width=iw:height=90:t=fill,"
            f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:{font_opt}:x=(w-text_w)/2:y=102,"
            f"drawbox=y=1905:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
            f"ass='{ass_filter_path}'[v]"
        )
        
        if bgm_path and bgm_path.exists():
            log(f"🎵 Mixing royalty-free BGM: {bgm_path.name}...")
            a_filter = (
                "[2:a]loudnorm=I=-14:LRA=7:TP=-1.5[voice];"
                "[3:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
            filtergraph = f"{v_filter};{a_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{bg_start:.2f}",
                "-i", str(bg_path),
                "-i", str(animal_path),
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
            filtergraph = f"{v_filter};[2:a]loudnorm=I=-14:LRA=7:TP=-1.5[aout]"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{bg_start:.2f}",
                "-i", str(bg_path),
                "-i", str(animal_path),
                "-i", str(audio_slice_path),
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
    elif bg_path and bg_path.exists():
        bg_start = random.uniform(0.0, max(0.0, bg_dur - duration - 1.0))
        log(f"🎮 Using full-screen Subway Surfers from {bg_path.name} (offset {bg_start:.1f}s)...")
        
        v_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,eq=contrast=1.04:brightness=-0.04[bg];"
            f"[bg]drawbox=y=160:color=black@0.75:width=iw:height=90:t=fill,"
            f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:{font_opt}:x=(w-text_w)/2:y=182,"
            f"drawbox=y=1905:color=#00D2FF@0.9:width='iw*(t/{dur_str})':height=10:t=fill,"
            f"ass='{ass_filter_path}'[v]"
        )
        
        if bgm_path and bgm_path.exists():
            log(f"🎵 Mixing royalty-free BGM: {bgm_path.name}...")
            a_filter = (
                "[1:a]loudnorm=I=-14:LRA=7:TP=-1.5[voice];"
                "[2:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )
            filtergraph = f"{v_filter};{a_filter}"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{bg_start:.2f}",
                "-i", str(bg_path),
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
                "-i", str(bg_path),
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
        
    log(f"✅ Short render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")

    # Thumbnail generation
    thumb_path = output_final_path.with_name(f"thumb_{output_final_path.stem}.jpg")
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", "00:00:03",
            "-i", str(output_final_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if thumb_path.exists():
            log(f"📸 Thumbnail generated: {thumb_path.name}")
    except Exception:
        pass


# =====================================================================
# 6. YouTube Upload (YouTube Data API v3)
# =====================================================================

def upload_to_youtube(video_path: Path, clip_info: dict, podcast_entry: dict, original_video_url: str):
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    refresh_token = os.environ.get("REFRESH_TOKEN")
    
    if not (client_id and client_secret and refresh_token):
        log("⚠️ Notice: YouTube OAuth secrets not fully set. Completed dry-run render successfully.")
        return None
        
    if Credentials is None or build is None:
        log("⚠️ Notice: Google API client not installed. Completed dry-run render.")
        return None

    log("Authenticating with YouTube Data API...")
    try:
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )
        youtube = build("youtube", "v3", credentials=creds)

        title = clip_info.get("viral_title", "Unbelievable Wisdom 💡 #shorts")
        if "#shorts" not in title.lower():
            title = f"{title} #shorts"

        attribution = podcast_entry.get("attribution_template", "Full episode: {video_url}").format(
            guest_or_title=clip_info.get("speaker_badge", podcast_entry.get("name")),
            video_url=original_video_url
        )

        description = f"""{title}

🎙️ {attribution}
💡 Clip curated automatically for educational & commentary insights.

#shorts #podcast #wisdom #mindset #learning #growth
"""

        tags = clip_info.get("tags", []) + podcast_entry.get("default_tags", [])
        tags = list(set(tags))[:15]

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

        log(f"Uploading to YouTube as '{body['status']['privacyStatus']}': {title}...")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        log(f"🎉 Successfully uploaded! Video URL: https://youtube.com/shorts/{video_id}")

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


# =====================================================================
# 7. Main Pipeline Orchestrator
# =====================================================================

def run_pipeline(force_url: str = None, force_channel: str = None, dry_run: bool = False):
    log("=======================================================")
    log(" Starting Auto-Clipper Shorts Pipeline (Episodic Series)")
    log("=======================================================")
    
    catalog = load_json(CATALOG_PATH)
    history = load_json(HISTORY_PATH, {"last_channel_index": 0, "processed_videos": [], "processed_clips": [], "active_series": None})

    active_series = history.get("active_series")
    
    # 1. Determine if we are continuing an episodic series or starting a fresh topic
    if active_series and active_series.get("current_part", 1) < active_series.get("max_parts", 3) and not force_url and not force_channel:
        podcast_entry = next((p for p in catalog["podcasts"] if p["id"] == active_series.get("podcast_id")), None)
        if not podcast_entry:
            podcast_entry = catalog["podcasts"][0]
            
        target_video = {
            "id": active_series["video_id"],
            "url": f"https://www.youtube.com/watch?v={active_series['video_id']}",
            "title": active_series.get("episode_title", "Podcast Episode"),
            "uploader": podcast_entry["name"]
        }
        part_number = active_series["current_part"] + 1
        continuation_start = active_series.get("last_clip_end_sec", 0.0)
        topic_title = active_series.get("topic_title", "Podcast Insight")
        log(f"🎬 Continuing Episodic Series: Part {part_number}/{active_series.get('max_parts', 3)} on '{topic_title}' from {continuation_start:.1f}s...")
    else:
        podcast_entry, next_idx = pick_next_channel(catalog, history, force_channel)
        log(f"Selected Podcast Channel: {podcast_entry['name']} ({podcast_entry['category']})")
        
        target_video = select_target_video(podcast_entry, history, direct_url=force_url)
        log(f"Selected Video: {target_video['title']} [{target_video['id']}]")
        part_number = 1
        continuation_start = None
        topic_title = None
        log(f"🎬 Starting New Episodic Topic Series: Part 1 for {podcast_entry['name']}...")

    # 2. Extract Subtitles/Transcript (Direct RSS Audio + Whisper AI / Instant Subs)
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
    
    # 4. Generate Karaoke ASS Subtitles (Safe Zone MarginV = 290)
    ass_sub_path = OUTPUT_DIR / f"subtitles_{target_video['id']}.ass"
    generate_karaoke_ass_subtitles(transcript_segments, start_sec, end_sec, ass_sub_path)
    
    # 5. Render Studio Visualizer Short (100% immune to YouTube BotGuard)
    audio_full_path = OUTPUT_DIR / "rss_podcast_audio.mp3"
    final_render_path = OUTPUT_DIR / f"clip_{target_video['id']}_final.mp4"
    
    if audio_full_path.exists() and audio_full_path.stat().st_size > 500000:
        render_studio_visualizer_short(
            audio_full_path=audio_full_path,
            start_sec=start_sec,
            end_sec=end_sec,
            ass_subtitle_path=ass_sub_path,
            output_final_path=final_render_path,
            speaker_badge=composed_badge
        )
    else:
        raw_slice_path = OUTPUT_DIR / f"raw_slice_{target_video['id']}.mp4"
        download_video_clip_segment(target_video["url"], start_sec, end_sec, raw_slice_path)
        render_vertical_916_short(
            raw_slice_path,
            ass_sub_path,
            final_render_path,
            speaker_badge=composed_badge
        )

    # 6. Upload to YouTube
    uploaded_id = None
    if not dry_run:
        uploaded_id = upload_to_youtube(final_render_path, clip_info, podcast_entry, target_video["url"])
    else:
        log("Dry run active: Skipping YouTube upload.")

    # 7. Update History & Multi-Part Series State
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
        log(f"📌 Multi-Part Series Progressed: Part {part_number}/{MAX_SERIES_PARTS} completed. Next run will clip Part {part_number + 1}.")
    else:
        history["active_series"] = None
        if target_video["id"] not in history.get("processed_videos", []):
            history.setdefault("processed_videos", []).append(target_video["id"])
        history["last_channel_index"] = (history.get("last_channel_index", 0) + 1) % len(catalog["podcasts"])
        log(f"🎉 Multi-Part Series Finished! (All {MAX_SERIES_PARTS} parts completed). Rotated to next podcast channel.")
        
    history.setdefault("processed_clips", []).append({
        "video_id": target_video["id"],
        "title": clip_info.get("viral_title"),
        "channel": podcast_entry["name"],
        "part": part_number,
        "start": start_sec,
        "end": end_sec,
        "uploaded_youtube_id": uploaded_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    save_json(HISTORY_PATH, history)
    
    # Clean up intermediate temporary files
    if ass_sub_path.exists():
        try:
            ass_sub_path.unlink()
        except Exception:
            pass
                
    log("=======================================================")
    log(" Pipeline Finished Successfully! 🎉")
    log("=======================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto Clipper Shorts - Podcast & Talk 9:16 Video Generator")
    parser.add_argument("--url", type=str, help="Direct YouTube video URL to clip")
    parser.add_argument("--channel", type=str, help="Specific channel ID from podcast_catalog.json (e.g. lex_fridman, big_think)")
    parser.add_argument("--dry-run", action="store_true", help="Run full pipeline without uploading to YouTube")
    args = parser.parse_args()

    run_pipeline(force_url=args.url, force_channel=args.channel, dry_run=args.dry_run)
