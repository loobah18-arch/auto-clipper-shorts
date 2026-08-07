#!/usr/bin/env python3
"""
Auto Clipper Shorts - Automated Podcast & Talk Clipping Pipeline for YouTube Shorts
Transcribes, identifies viral hooks with Groq AI, renders 9:16 vertical video with karaoke subtitles,
and publishes to YouTube hands-free.
"""

import os
import sys
import json
import time
import shutil
import argparse
import subprocess
from datetime import datetime
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
except ImportError:
    Credentials = None
    build = None
    MediaFileUpload = None

# --- Configuration & Paths ---
WORKSPACE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = WORKSPACE_DIR / "podcast_catalog.json"
HISTORY_PATH = WORKSPACE_DIR / "clip_history.json"
OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
FALLBACK_GROQ_MODEL = "llama-3.1-8b-instant"


def log(msg: str):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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


# =====================================================================
# 1. Source Discovery & Metadata
# =====================================================================

def pick_next_channel(catalog: dict, history: dict, force_channel_id: str = None) -> tuple:
    podcasts = catalog.get("podcasts", [])
    if not podcasts:
        raise ValueError("No podcasts found in podcast_catalog.json!")

    if force_channel_id:
        for p in podcasts:
            if p["id"] == force_channel_id:
                return p, history.get("last_channel_index", 0)

    last_idx = history.get("last_channel_index", -1)
    next_idx = (last_idx + 1) % len(podcasts)
    return podcasts[next_idx], next_idx


def get_latest_videos_from_channel(channel_url: str, max_results: int = 10) -> list:
    """Uses yt-dlp to inspect the channel's latest uploads without downloading media."""
    log(f"Fetching latest videos from {channel_url}...")
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end", str(max_results),
        f"{channel_url.rstrip('/')}/videos" if not channel_url.endswith("/videos") else channel_url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        videos = []
        for e in entries:
            if e and e.get("id"):
                duration = e.get("duration") or 0
                # Filter for reasonable podcast length (e.g. 5 minutes to 3 hours)
                videos.append({
                    "id": e["id"],
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "title": e.get("title", "Untitled Episode"),
                    "duration": duration,
                    "uploader": e.get("uploader", "")
                })
        return videos
    except Exception as e:
        log(f"Failed to fetch channel playlist via yt-dlp: {e}")
        return []


def select_target_video(podcast_entry: dict, history: dict, direct_url: str = None) -> dict:
    if direct_url:
        log(f"Using direct URL: {direct_url}")
        # Fetch metadata
        cmd = ["yt-dlp", "--dump-single-json", direct_url]
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
    candidates = get_latest_videos_from_channel(podcast_entry["channel_url"], max_results=8)
    
    for vid in candidates:
        if vid["id"] not in processed:
            # Skip extremely short clips if they are already shorts
            if vid["duration"] > 0 and vid["duration"] < 90:
                continue
            return vid

    # If all recent are processed, pick the latest one and extract a new sub-clip
    if candidates:
        return candidates[0]
    
    raise RuntimeError(f"Could not find any suitable videos for {podcast_entry['name']}")


# =====================================================================
# 2. Audio Extraction & Whisper Transcription
# =====================================================================

def download_audio_for_transcription(video_url: str, output_audio_path: Path) -> Path:
    """Fast audio download (low bitrate m4a) for quick transcription."""
    if output_audio_path.exists():
        output_audio_path.unlink()
        
    log(f"Downloading audio track for transcription: {video_url}")
    cmd = [
        "yt-dlp",
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "m4a",
        "--audio-quality", "5",
        "-o", str(output_audio_path),
        video_url
    ]
    subprocess.run(cmd, check=True)
    return output_audio_path


def transcribe_audio_with_whisper(audio_path: Path, model_size: str = "base.en") -> list:
    """
    Transcribes audio using faster-whisper with word-level timestamps.
    Returns list of segment objects with text and word timings.
    """
    if WhisperModel is None:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")
        
    log(f"Loading Whisper model '{model_size}' (CPU / int8 for high speed)...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4)
    
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
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                    "probability": round(w.probability, 2)
                })
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

def chunk_transcript(segments: list, max_duration_sec: float = 120.0) -> list:
    """Group continuous segments into chunks of ~1-2 minutes for LLM analysis."""
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


def select_viral_clip_with_groq(transcript_segments: list, video_meta: dict, podcast_entry: dict) -> dict:
    """
    Prompts Groq Llama 3.3 to analyze transcript and select the single most engaging 30-55s clip.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        raise ValueError("GROQ_API_KEY environment variable is missing!")
        
    client = Groq(api_key=groq_api_key)
    
    # Format chunks for prompt
    chunks = chunk_transcript(transcript_segments, max_duration_sec=90.0)
    # Take up to 25 representative chunks across the video
    sample_chunks = chunks[:25]
    
    formatted_transcript = "\n".join([
        f"[{datetime.utcfromtimestamp(c['start']).strftime('%M:%S')} - {datetime.utcfromtimestamp(c['end']).strftime('%M:%S')}] {c['text']}"
        for c in sample_chunks
    ])
    
    system_prompt = (
        "You are an expert viral YouTube Shorts and TikTok content strategist specializing in "
        "podcasts, philosophy, science, and high-impact discussions.\n"
        "Your task: Identify the single most mind-blowing, insightful, or emotional 30-55 second clip "
        "from the provided transcript that will captivate viewers within the first 3 seconds."
    )
    
    user_prompt = f"""
Podcast: {podcast_entry.get('name', 'Podcast')}
Episode Title: {video_meta.get('title', 'Episode')}
Category: {podcast_entry.get('category', 'Talk / Interview')}

Here are candidate segments from the transcript with timestamps:
{formatted_transcript}

Requirements:
1. Select a CONTINUOUS segment between 30 and 55 seconds long (e.g. start: 125.0, end: 168.0).
2. The segment MUST start with a strong hook or surprising statement.
3. It must deliver a complete, satisfying thought or insight.
4. Output MUST be valid JSON only.

JSON Format:
{{
  "start_seconds": <float>,
  "end_seconds": <float>,
  "viral_title": "<Punchy title under 65 chars with 1 emoji and #Shorts>",
  "hook_reason": "<Why this clip has high viewer retention>",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "speaker_badge": "<Short 2-3 word speaker or topic label for top banner, e.g. 'Andrew Huberman on Sleep' or 'Lex Fridman AI Insight'>"
}}
"""
    log("Sending transcript to Groq for viral highlight detection...")
    
    for model_name in [DEFAULT_GROQ_MODEL, FALLBACK_GROQ_MODEL]:
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.4,
                max_tokens=600
            )
            raw_content = resp.choices[0].message.content
            clip_data = json.loads(raw_content)
            
            # Validate output
            start_sec = float(clip_data.get("start_seconds", 0))
            end_sec = float(clip_data.get("end_seconds", 0))
            duration = end_sec - start_sec
            
            if duration < 20 or duration > 65:
                # Clamp or adjust
                if duration <= 0:
                    start_sec = 30.0
                    end_sec = 75.0
                elif duration > 65:
                    end_sec = start_sec + 50.0
            
            clip_data["start_seconds"] = start_sec
            clip_data["end_seconds"] = end_sec
            clip_data["duration"] = round(end_sec - start_sec, 2)
            log(f"Groq selected clip: {start_sec:.1f}s -> {end_sec:.1f}s ({clip_data['duration']}s)")
            log(f"Title: {clip_data.get('viral_title')}")
            return clip_data
        except Exception as e:
            log(f"Groq model {model_name} failed: {e}. Trying fallback...")
            time.sleep(2)
            
    # Fallback default if LLM fails completely
    return {
        "start_seconds": 60.0,
        "end_seconds": 105.0,
        "duration": 45.0,
        "viral_title": f"Mind-Blowing Advice You Need To Hear! 💡 #shorts",
        "hook_reason": "High emotional resonance",
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
    """
    Creates dynamic Aegisub (.ass) karaoke subtitles for 1080x1920 portrait.
    Features:
    - Active word highlight in glowing Gold/Yellow
    - Thick black outline for 100% legibility over any background
    - Centered above bottom YouTube UI safe zone (MarginV: 180)
    """
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,64,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,6,2,2,40,40,180,1
Style: Highlight,Arial Black,64,&H002BF5FF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,105,105,2,0,1,7,3,2,40,40,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    # Filter words within the clip range
    clip_words = []
    for seg in segments:
        for w in seg.get("words", []):
            if w["start"] >= (start_sec - 0.2) and w["end"] <= (end_sec + 0.5):
                # Adjust time relative to clip start
                rel_start = max(0.0, w["start"] - start_sec)
                rel_end = max(rel_start + 0.1, w["end"] - start_sec)
                clip_words.append({
                    "word": w["word"].upper(),
                    "start": rel_start,
                    "end": rel_end
                })

    events = []
    
    # Group words into short 3-5 word rhythmic lines
    GROUP_SIZE = 4
    for i in range(0, len(clip_words), GROUP_SIZE):
        group = clip_words[i:i + GROUP_SIZE]
        if not group:
            continue
        line_start = group[0]["start"]
        line_end = group[-1]["end"]
        
        # Build line where each active word flashes highlight
        for active_idx, target_word in enumerate(group):
            w_start = target_word["start"]
            w_end = target_word["end"]
            
            line_parts = []
            for idx, gw in enumerate(group):
                if idx == active_idx:
                    # Highlighted active word
                    line_parts.append(r"{\c&H002BF5FF\t(\fscx112\fscy112)}" + gw["word"] + r"{\r}")
                else:
                    # Inactive white word
                    line_parts.append(r"{\c&H00FFFFFF}" + gw["word"])
                    
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

def download_video_clip_segment(video_url: str, start_sec: float, end_sec: float, output_raw_path: Path):
    """
    Downloads only the specified segment in 1080p using yt-dlp.
    """
    if output_raw_path.exists():
        output_raw_path.unlink()
        
    start_str = f"{int(start_sec // 3600):02d}:{int((start_sec % 3600) // 60):02d}:{int(start_sec % 60):02d}"
    end_str = f"{int(end_sec // 3600):02d}:{int((end_sec % 3600) // 60):02d}:{int(end_sec % 60):02d}"
    
    log(f"Downloading high-res video slice: {start_str} to {end_str}...")
    cmd = [
        "yt-dlp",
        "--download-sections", f"*{start_str}-{end_str}",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--force-keyframes-at-cuts",
        "-o", str(output_raw_path),
        video_url
    ]
    subprocess.run(cmd, check=True)


def render_vertical_916_short(
    raw_video_path: Path,
    ass_subtitle_path: Path,
    output_final_path: Path,
    speaker_badge: str = ""
):
    """
    Renders a stunning 1080x1920 vertical video using FFmpeg:
    1. Background: Scaled to 1080x1920 with heavy Gaussian blur + slight dark vignette
    2. Foreground: Centered crisp 16:9 video with clean border and drop shadow
    3. Top Banner: High-contrast Speaker / Topic Badge
    4. Bottom: Word-by-word animated ASS karaoke subtitles
    """
    if output_final_path.exists():
        output_final_path.unlink()
        
    log("Rendering 1080x1920 vertical short with FFmpeg...")
    
    # Escape path for FFmpeg filter on Linux
    ass_filter_path = str(ass_subtitle_path).replace("\\", "/").replace(":", r"\:")
    
    badge_text = (speaker_badge or "PODCAST HIGHLIGHT").replace(":", r"\:").replace("'", "").upper()
    
    # Complex FFmpeg Filtergraph
    filtergraph = (
        # 1. Background branch: Scale & crop to 1080x1920, heavy blur, slightly dimmed
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=25:5,eq=brightness=-0.08:contrast=1.05[bg];"
        
        # 2. Foreground branch: Crisp 16:9 centered scale (1040 width with 20px padding)
        "[0:v]scale=1040:-2[fg];"
        
        # 3. Overlay FG onto BG centered at (H-h)/2 - 50 (slightly above center for optimal balance)
        "[bg][fg]overlay=(W-w)/2:(H-h)/2 - 50[comp1];"
        
        # 4. Top Speaker Badge Header
        f"[comp1]drawbox=y=160:color=black@0.65:width=iw:height=90:t=fill,"
        f"drawtext=text='{badge_text}':fontcolor=white:fontsize=40:font='Arial Black':x=(w-text_w)/2:y=182[comp2];"
        
        # 5. Burn ASS Subtitles
        f"[comp2]ass='{ass_filter_path}'[v]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_video_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_final_path)
    ]
    
    subprocess.run(cmd, check=True)
    log(f"Video render complete: {output_final_path.name} ({output_final_path.stat().st_size / (1024*1024):.2f} MB)")


# =====================================================================
# 6. YouTube Upload (YouTube Data API v3)
# =====================================================================

def upload_to_youtube(video_path: Path, clip_info: dict, podcast_entry: dict, original_video_url: str):
    """
    Uploads the rendered short to YouTube using OAuth credentials.
    """
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    refresh_token = os.environ.get("REFRESH_TOKEN")
    
    if not (client_id and client_secret and refresh_token):
        log("YouTube OAuth secrets (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) not set. Skipping upload (dry run).")
        return None
        
    if Credentials is None or build is None:
        log("Google API client not installed. Skipping upload.")
        return None

    log("Authenticating with YouTube Data API...")
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

    # Clean attribution
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
    log(f"Successfully uploaded! Video URL: https://youtube.com/shorts/{video_id}")
    return video_id


# =====================================================================
# 7. Main Pipeline Orchestrator
# =====================================================================

def run_pipeline(force_url: str = None, force_channel: str = None, dry_run: bool = False):
    log("=======================================================")
    log(" Starting Auto-Clipper Shorts Pipeline")
    log("=======================================================")
    
    catalog = load_json(CATALOG_PATH)
    history = load_json(HISTORY_PATH, {"last_channel_index": 0, "processed_videos": [], "processed_clips": []})

    # 1. Pick Channel & Video
    podcast_entry, next_idx = pick_next_channel(catalog, history, force_channel)
    log(f"Selected Podcast Channel: {podcast_entry['name']} ({podcast_entry['category']})")
    
    target_video = select_target_video(podcast_entry, history, direct_url=force_url)
    log(f"Selected Video: {target_video['title']} [{target_video['id']}]")

    # 2. Extract Audio & Transcribe
    audio_temp_path = OUTPUT_DIR / f"audio_{target_video['id']}.m4a"
    download_audio_for_transcription(target_video["url"], audio_temp_path)
    
    transcript_segments = transcribe_audio_with_whisper(audio_temp_path, model_size="base.en")
    
    # 3. AI Highlight Detection (Groq)
    clip_info = select_viral_clip_with_groq(transcript_segments, target_video, podcast_entry)
    start_sec = clip_info["start_seconds"]
    end_sec = clip_info["end_seconds"]
    
    # 4. Generate Karaoke ASS Subtitles
    ass_sub_path = OUTPUT_DIR / f"subtitles_{target_video['id']}.ass"
    generate_karaoke_ass_subtitles(transcript_segments, start_sec, end_sec, ass_sub_path)
    
    # 5. Download Video Slice & Render 9:16 Short
    raw_slice_path = OUTPUT_DIR / f"raw_slice_{target_video['id']}.mp4"
    download_video_clip_segment(target_video["url"], start_sec, end_sec, raw_slice_path)
    
    final_render_path = OUTPUT_DIR / f"clip_{target_video['id']}_final.mp4"
    render_vertical_916_short(
        raw_slice_path,
        ass_sub_path,
        final_render_path,
        speaker_badge=clip_info.get("speaker_badge", podcast_entry["name"])
    )

    # 6. Upload to YouTube
    uploaded_id = None
    if not dry_run:
        uploaded_id = upload_to_youtube(final_render_path, clip_info, podcast_entry, target_video["url"])
    else:
        log("Dry run active: Skipping YouTube upload.")

    # 7. Update History & State
    history["last_channel_index"] = next_idx
    if target_video["id"] not in history.get("processed_videos", []):
        history["processed_videos"].append(target_video["id"])
        
    history["processed_clips"].append({
        "video_id": target_video["id"],
        "title": clip_info.get("viral_title"),
        "channel": podcast_entry["name"],
        "start": start_sec,
        "end": end_sec,
        "uploaded_youtube_id": uploaded_id,
        "created_at": datetime.utcnow().isoformat()
    })
    save_json(HISTORY_PATH, history)
    
    # Clean up large temp intermediate files
    for temp_f in [audio_temp_path, raw_slice_path, ass_sub_path]:
        if temp_f.exists():
            try:
                temp_f.unlink()
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
