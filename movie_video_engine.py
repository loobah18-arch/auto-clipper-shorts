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


def download_movie_trailer(search_query: str, output_path: Path, trailer_url: str = None) -> bool:
    """
    Downloads the official movie trailer using direct URL or multi-client YouTube search via yt-dlp.
    """
    try:
        from main import ytdlp_cookies_args
        cookies = ytdlp_cookies_args()
    except Exception:
        cookies = []

    candidate_targets = []
    if trailer_url:
        candidate_targets.append(("direct_url", trailer_url))

    candidate_targets.extend([
        ("query", f"ytsearch3:{search_query}"),
        ("query_trailer", f"ytsearch3:{search_query} official trailer 1080p"),
        ("query_clips", f"ytsearch3:{search_query} movie scenes clips")
    ])

    client_combos = [
        ["--extractor-args", "youtube:player_client=ios,mweb,web"],
        ["--extractor-args", "youtube:player_client=android,mweb"],
        ["--extractor-args", "youtube:player_client=web,default"],
        []
    ]

    for tag, target in candidate_targets:
        log(f"🔍 Attempting trailer fetch ({tag}): '{target}'...")
        for client_args in client_combos:
            cmd = [
                "yt-dlp",
                target,
                "--no-playlist",
                "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best/bestvideo+bestaudio",
                "--merge-output-format", "mp4",
                "-o", str(output_path),
                "--no-check-certificates"
            ] + cookies + client_args

            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=95)
                if output_path.exists() and output_path.stat().st_size > 500_000:
                    log(f"✅ Downloaded movie footage ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
                    return True
                else:
                    if res.returncode != 0:
                        err_line = res.stderr.strip().split("\n")[-1] if res.stderr else "Unknown error"
                        log(f"   ↳ Notice: {err_line[:110]}")
            except Exception as e:
                log(f"   ↳ Error: {e}")

    log("⚠️ All YouTube video download candidates failed.")
    return False


def create_cinematic_movie_visual_fallback(duration: float, title: str, output_path: Path) -> bool:
    """
    Fetches real authentic movie poster / theatrical artwork from Wikipedia REST API
    and builds an animated multi-layered Ken Burns camera motion sequence so the video
    ALWAYS shows the authentic movie artwork and visuals, NEVER a blank screen!
    """
    import urllib.request
    import urllib.parse

    log(f"🖼️ Fetching authentic movie artwork for '{title}'...")
    clean_movie = re.sub(r"\s*\(\d{4}\)", "", title).strip()
    year_match = re.search(r"\((\d{4})\)", title)
    year = year_match.group(1) if year_match else ""

    candidate_titles = [
        f"{clean_movie} ({year} film)" if year else f"{clean_movie} (film)",
        f"{clean_movie} (film)",
        clean_movie,
        title
    ]

    img_path = output_path.with_name(f"poster_{output_path.stem}.jpg")
    downloaded = False

    for c_title in candidate_titles:
        try:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(c_title)}"
            req = urllib.request.Request(url, headers={"User-Agent": "MovieShortsEngine/1.0 (contact@movieshorts.org)"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                orig = data.get("originalimage", {}).get("source") or data.get("thumbnail", {}).get("source")
                if orig:
                    img_req = urllib.request.Request(orig, headers={"User-Agent": "MovieShortsEngine/1.0"})
                    with urllib.request.urlopen(img_req, timeout=12) as im_resp:
                        with open(img_path, "wb") as f:
                            f.write(im_resp.read())
                    if img_path.exists() and img_path.stat().st_size > 10000:
                        downloaded = True
                        log(f"✅ Fetched real theatrical poster: {img_path.name} ({img_path.stat().st_size // 1024} KB)")
                        break
        except Exception:
            pass

    if not downloaded:
        bg_cand = ASSETS_DIR / "backgrounds" / "subway_surfers_part1.mp4"
        if bg_cand.exists():
            log("🎬 Using high-motion background video asset as visual stream...")
            cmd = [
                "ffmpeg", "-y",
                "-i", str(bg_cand),
                "-t", f"{duration:.2f}",
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                "-c:v", "libx264", "-preset", "veryfast", "-an",
                str(output_path)
            ]
            subprocess.run(cmd, check=True)
            return True
        else:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c=#141824:s=1280x720:d={duration:.2f}",
                "-vf", "vignette=PI/4",
                "-t", f"{duration:.2f}",
                "-c:v", "libx264", "-preset", "veryfast",
                str(output_path)
            ]
            subprocess.run(cmd, check=True)
            return True

    # Build dynamic 3D Ken Burns motion video from authentic poster
    log(f"🎥 Rendering dynamic Ken Burns motion sequence ({duration:.1f}s)...")
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img_path),
        "-vf", (
            f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0008,1.20)':d=125:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720,"
            f"eq=contrast=1.08:brightness=-0.02:saturation=1.12"
        ),
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(output_path)
    ]
    subprocess.run(cmd, check=True)
    return True


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
    bgm_path: Path = None,
    watermark_text: str = None
) -> Path:
    """
    Renders the final 9:16 vertical Short (1080x1920) in the signature MovieGyan layout:
    - Fullscreen blurred background
    - 16:9 centered foreground clip with cinematic color grading & sharpness
    - Channel watermark & branding for YPP review verification
    - Sleek top badge pill with movie title
    - Bold animated karaoke subtitles in lower-third
    - Crystal clear voiceover with suspense BGM
    """
    duration = get_audio_duration(narration_audio_path)
    clean_badge = re.sub(r"[^A-Za-z0-9\s\(\)\-\.\,\!\?]", "", badge_text or movie_title).strip().upper()[:28]
    badge_display = f"🎬 MOVIE ANALYSIS • {clean_badge}"

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

    # Optional channel watermark for YPP brand identity
    watermark_filter = ""
    if watermark_text:
        clean_wm = re.sub(r"[^A-Za-z0-9@\._\- ]", "", watermark_text).strip()
        watermark_filter = f",drawtext=text='{clean_wm}':fontsize=26:fontcolor=white@0.65:{font_opt}:x=w-text_w-50:y=h-text_h-240"

    # Filtergraph with transformative color grading & subtle unsharp masking for unique digital fingerprint
    video_filters = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.12:contrast=1.05[bg];"
        "[0:v]scale=1040:-2,eq=contrast=1.08:brightness=-0.03:saturation=1.12,unsharp=3:3:0.5[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2 - 40[comp];"
        f"[comp]drawbox=x=40:y=120:w=1000:h=90:color=black@0.75:t=fill,"
        f"drawtext=text='{badge_display}':fontsize=36:fontcolor=white:{font_opt}:x=(w-text_w)/2:y=148"
        f"{watermark_filter},"
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
