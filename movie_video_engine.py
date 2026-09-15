#!/usr/bin/env python3
"""
Movie Video Engine for Movie Explanation Shorts.
Produces high-retention 9:16 vertical shorts in the exact style of
MovieGyan and Movie Insight Hindi:
- Centered 16:9 movie footage with blurred background mirror
- Rapid scene switches (every 2.5-3.5s) from official trailer
- Deep neural voiceover narration (edge-tts)
- Dynamic karaoke animated ASS subtitles (bright yellow active word)
- Subtle cinematic suspense BGM
"""

import os
import re
import sys
import json
import random
import shutil
import asyncio
import subprocess
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

try:
    import edge_tts
except ImportError:
    edge_tts = None

OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = WORKSPACE_DIR / "assets"
BGM_DIR = ASSETS_DIR / "bgm"

FONT_CANDIDATES = [
    "/system/fonts/Roboto-Bold.ttf",
    "/system/fonts/DroidSans-Bold.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
]


def log(msg: str):
    print(f"[movie_engine] {msg}", flush=True)


def find_system_font() -> str:
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    return "DejaVu Sans"


async def generate_speech_audio(script_text: str, output_audio_path: Path, voice: str = "en-US-ChristopherNeural") -> list:
    """
    Generates TTS audio and extracts sentence boundaries using edge-tts.
    Returns sentence timing segments.
    """
    if not edge_tts:
        raise RuntimeError("edge-tts is required for speech generation.")

    log(f"🎙️ Synthesizing voiceover with voice: '{voice}'...")
    communicate = edge_tts.Communicate(script_text, voice)
    
    sentences = []
    audio_data = bytearray()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            # offset and duration are in 100ns units (1 sec = 10,000,000 units)
            start_s = chunk["offset"] / 10_000_000.0
            dur_s = chunk["duration"] / 10_000_000.0
            sentences.append({
                "text": chunk["text"],
                "start": start_s,
                "end": start_s + dur_s
            })

    with open(output_audio_path, "wb") as f:
        f.write(audio_data)

    log(f"✅ Voiceover generated: {len(sentences)} sentence boundaries recorded.")
    return sentences


def create_word_timestamps_from_sentences(sentences: list) -> list:
    """Splits sentence boundaries into evenly spaced word timestamps."""
    words = []
    for s in sentences:
        s_words = s["text"].split()
        if not s_words:
            continue
        dur = max(0.15, s["end"] - s["start"])
        step = dur / len(s_words)
        for i, w in enumerate(s_words):
            w_clean = re.sub(r"[^\w\s\$\%\'\-]", "", w).strip()
            if not w_clean:
                continue
            w_start = s["start"] + (i * step)
            w_end = w_start + step
            words.append({
                "word": w_clean.upper(),
                "start": round(w_start, 2),
                "end": round(w_end, 2)
            })
    return words


def generate_moviegyan_subtitles(words: list, output_ass_path: Path, group_size: int = 3):
    """
    Generates high-retention karaoke ASS subtitles formatted in MovieGyan / Movie Insight Hindi style:
    - Bold sans-serif font
    - Bright neon yellow active word (&H0000FFFF&)
    - Crisp white inactive words (&H00FFFFFF&)
    - Thick black outline (&H00000000&)
    - Centered in lower-third
    """
    font_name = "DejaVu Sans"
    sys_font = find_system_font()
    if "Roboto" in sys_font:
        font_name = "Roboto"

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,5,3,2,40,40,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    def format_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        cs = int(round((sec % 1) * 100))
        if cs >= 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    for i in range(0, len(words), group_size):
        chunk = words[i:i + group_size]
        if not chunk:
            continue
        
        for active_idx, target in enumerate(chunk):
            t_start = target["start"]
            t_end = target["end"]
            if active_idx < len(chunk) - 1:
                t_end = min(t_end, chunk[active_idx + 1]["start"])

            line_parts = []
            for j, w in enumerate(chunk):
                if j == active_idx:
                    line_parts.append(r"{\c&H0000FFFF&\fscx112\fscy112}" + w["word"] + r"{\r}")
                else:
                    line_parts.append(r"{\c&H00FFFFFF&}" + w["word"] + r"{\r}")

            text_line = " ".join(line_parts)
            events.append(f"Dialogue: 0,{format_time(t_start)},{format_time(t_end)},Default,,0,0,0,,{text_line}\n")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content + "".join(events))

    log(f"📝 Subtitles written to: {output_ass_path.name} ({len(events)} dialogue events)")


def get_audio_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def download_movie_trailer(search_query: str, output_path: Path) -> bool:
    """
    Searches YouTube and downloads the official movie trailer using yt-dlp.
    """
    log(f"🔍 Searching YouTube for movie trailer: '{search_query}'...")
    cmd = [
        "yt-dlp",
        f"ytsearch1:{search_query}",
        "--no-playlist",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(output_path),
        "--no-check-certificates"
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if output_path.exists() and output_path.stat().st_size > 500_000:
            log(f"✅ Downloaded movie trailer ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
            return True
    except Exception as e:
        log(f"⚠️ Trailer download error: {e}")
    return False


def slice_trailer_dynamic_scenes(trailer_path: Path, target_duration: float, output_sliced_path: Path) -> bool:
    """
    Slices dynamic 2.5-3.2s cuts across the downloaded trailer and concatenates them
    to achieve the fast-paced MovieGyan / Movie Insight Hindi visual rhythm.
    """
    try:
        trailer_dur = get_audio_duration(trailer_path)
    except Exception:
        trailer_dur = 90.0

    # Avoid intro logos (first 10s) and credits/dates (last 15s)
    usable_start = min(12.0, trailer_dur * 0.1)
    usable_end = max(usable_start + 10.0, trailer_dur - 15.0)
    usable_span = max(10.0, usable_end - usable_start)

    num_cuts = int(target_duration // 2.8) + 2
    cut_duration = round(target_duration / max(1, num_cuts), 2)
    cut_duration = max(2.2, min(3.4, cut_duration))

    # Pick spaced timestamps across trailer
    step = usable_span / max(1, num_cuts)
    segments = []
    for i in range(num_cuts):
        seg_start = usable_start + (i * step) + random.uniform(-0.5, 0.5)
        seg_start = max(usable_start, min(usable_end - cut_duration, seg_start))
        segments.append((seg_start, cut_duration))

    log(f"🎬 Assembling {len(segments)} dynamic scenes (~{cut_duration:.1f}s each) from trailer...")

    # Build FFmpeg filter to slice and concat
    filter_parts = []
    concat_inputs = []
    for idx, (s_time, c_dur) in enumerate(segments):
        filter_parts.append(
            f"[0:v]trim=start={s_time:.2f}:duration={c_dur:.2f},setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1[v{idx}]"
        )
        concat_inputs.append(f"[v{idx}]")

    concat_filter = f"{';'.join(filter_parts)};{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=0[vconcat]"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(trailer_path),
        "-filter_complex", concat_filter,
        "-map", "[vconcat]",
        "-t", f"{target_duration:.2f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-an",
        str(output_sliced_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and output_sliced_path.exists():
        log("✅ Trailer scenes dynamically sliced & assembled.")
        return True
    else:
        log(f"⚠️ Slicing filter failed, falling back to direct trim: {res.stderr[:200]}")
        # Simple fallback trim
        fb_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{usable_start:.2f}",
            "-i", str(trailer_path),
            "-t", f"{target_duration:.2f}",
            "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
            "-c:v", "libx264", "-preset", "veryfast", "-an",
            str(output_sliced_path)
        ]
        subprocess.run(fb_cmd, check=True)
        return True


def render_movie_explanation_short(
    sliced_video_path: Path,
    narration_audio_path: Path,
    ass_subtitle_path: Path,
    movie_title: str,
    badge_text: str,
    output_final_path: Path,
    bgm_path: Path = None
) -> Path:
    """
    Renders the final 9:16 vertical Short (1080x1920) in the signature MovieGyan layout:
    - Fullscreen blurred background
    - 16:9 centered foreground clip
    - Sleek top badge pill with movie title
    - Bold animated karaoke subtitles in lower-third
    - Crystal clear voiceover with suspense BGM
    """
    duration = get_audio_duration(narration_audio_path)
    clean_badge = re.sub(r"[^A-Za-z0-9\s\(\)\-\.\,\!\?]", "", badge_text or movie_title).strip().upper()[:28]
    badge_display = f"🎬 MOVIE EXPLAINED • {clean_badge}"

    font_path = find_system_font()
    if os.path.exists(font_path):
        font_opt = f"fontfile='{font_path}'"
    else:
        font_opt = "font='DejaVu Sans'"

    ass_esc = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    # Select BGM
    if not bgm_path or not bgm_path.exists():
        bgm_path = BGM_DIR / "cinematic_suspense_thriller.mp3"
        if not bgm_path.exists():
            bgm_path = BGM_DIR / "cinematic_suspense_drone.mp3"

    has_bgm = bgm_path and bgm_path.exists()
    log(f"🎨 Rendering Movie Explanation Short (1080x1920, {duration:.1f}s)...")

    # Filtergraph:
    # 0:v = sliced movie scenes
    # 1:a = narration voiceover
    # 2:a = suspense BGM (if present)
    video_filters = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.12:contrast=1.05[bg];"
        "[0:v]scale=1040:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2 - 40[comp];"
        f"[comp]drawbox=x=40:y=120:w=1000:h=90:color=black@0.75:t=fill,"
        f"drawtext=text='{badge_display}':fontsize=36:fontcolor=white:{font_opt}:x=(w-text_w)/2:y=148,"
        f"ass='{ass_esc}'[vfinal]"
    )

    cmd = ["ffmpeg", "-y", "-i", str(sliced_video_path), "-i", str(narration_audio_path)]
    if has_bgm:
        cmd.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        audio_filters = "[1:a]volume=1.0[voice];[2:a]volume=0.15,lowpass=f=3000[bgm];[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[afinal]"
        filter_complex = f"{video_filters};{audio_filters}"
        map_args = ["-map", "[vfinal]", "-map", "[afinal]"]
    else:
        filter_complex = video_filters
        map_args = ["-map", "[vfinal]", "-map", "1:a"]

    cmd.extend([
        "-filter_complex", filter_complex,
        *map_args,
        "-t", f"{duration:.2f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        str(output_final_path)
    ])

    subprocess.run(cmd, check=True)
    log(f"🎉 Final Movie Explanation Short rendered: {output_final_path.name}")
    return output_final_path


def generate_thumbnail(video_path: Path, output_thumb_path: Path, title: str):
    """Generates an eye-catching YouTube Shorts thumbnail."""
    clean_title = re.sub(r"[^A-Za-z0-9\s]", "", title).strip().upper()[:22]
    font_path = find_system_font()
    font_opt = f"fontfile='{font_path}'" if os.path.exists(font_path) else "font='DejaVu Sans'"

    cmd = [
        "ffmpeg", "-y",
        "-ss", "4.0",
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", (
            f"drawbox=y=160:color=black@0.7:width=iw:height=140:t=fill,"
            f"drawtext=text='PLOT TWIST EXPLAINED':fontsize=64:fontcolor=yellow:{font_opt}:x=(w-text_w)/2:y=180,"
            f"drawbox=y=1550:color=black@0.7:width=iw:height=120:t=fill,"
            f"drawtext=text='{clean_title}':fontsize=52:fontcolor=white:{font_opt}:x=(w-text_w)/2:y=1580"
        ),
        str(output_thumb_path)
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"🖼️ Thumbnail generated: {output_thumb_path.name}")
    except Exception as e:
        log(f"⚠️ Thumbnail generation failed: {e}")
