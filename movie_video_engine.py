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


async def generate_speech_audio(script_text: str, output_audio_path: Path, voice: str = "en-US-AvaNeural", rate: str = "+3%") -> list:
    """
    Generates TTS audio and extracts sentence boundaries using edge-tts.
    Returns sentence timing segments.
    """
    if not edge_tts:
        raise RuntimeError("edge-tts is required for speech generation.")

    log(f"🎙️ Synthesizing voiceover with voice: '{voice}' (rate={rate})...")
    communicate = edge_tts.Communicate(script_text, voice, rate=rate)
    
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


def parse_timestamp_to_seconds(ts: str) -> float:
    """Parses 'HH:MM:SS' or 'MM:SS' into float seconds."""
    if not ts:
        return 0.0
    parts = [float(p) for p in str(ts).strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60.0 + parts[1]
    elif len(parts) == 1:
        return parts[0]
    return 0.0


def download_movie_from_gdrive(file_id: str, output_path: Path) -> bool:
    """
    Downloads the genuine BluRay movie file from Google Drive using gdown.
    Caches the file locally to prevent redundant downloads across parts.
    """
    if output_path.exists() and output_path.stat().st_size > 10_000_000:
        log(f"🎬 Movie already exists in cache: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return True

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"📥 Sourcing genuine movie file from Google Drive (ID: {file_id}) -> {output_path.name}...")
    try:
        import gdown
        gdown.download(id=file_id, output=str(output_path), quiet=False)
        if output_path.exists() and output_path.stat().st_size > 10_000_000:
            log(f"✅ Movie file downloaded from Google Drive: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
            return True
        else:
            log(f"⚠️ Google Drive download completed but file is missing or too small: {output_path}")
    except Exception as e:
        log(f"⚠️ Google Drive download error via gdown: {e}")

    # Fallback to gdown CLI
    try:
        cmd = ["gdown", file_id, "-O", str(output_path)]
        subprocess.run(cmd, check=True, timeout=600)
        if output_path.exists() and output_path.stat().st_size > 10_000_000:
            log(f"✅ Movie file downloaded via gdown CLI: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
            return True
    except Exception as e2:
        log(f"⚠️ gdown CLI fallback failed: {e2}")

    return False


def slice_movie_timeline_scenes(
    movie_path: Path,
    timeline_start_str: str,
    timeline_end_str: str,
    target_duration: float,
    output_sliced_path: Path
) -> bool:
    """
    Slices 18-24 dynamic 2.2-2.8s scenes strictly within the specified movie timeline window
    (e.g. 00:01:30 to 00:26:00) so the visuals chronologically and accurately match the
    narrative of that specific part. Strips 100% of original movie audio.
    """
    start_sec = parse_timestamp_to_seconds(timeline_start_str)
    end_sec = parse_timestamp_to_seconds(timeline_end_str)

    if end_sec <= start_sec or end_sec <= 0:
        start_sec = 60.0
        end_sec = 1800.0

    usable_span = max(10.0, end_sec - start_sec)
    num_cuts = int(target_duration // 1.6) + 1
    cut_duration = round(target_duration / max(1, num_cuts), 2)
    cut_duration = max(1.3, min(2.0, cut_duration))

    step = usable_span / max(1, num_cuts)
    segments = []
    for i in range(num_cuts):
        seg_start = start_sec + (i * step) + random.uniform(-1.0, 1.0)
        seg_start = max(start_sec, min(end_sec - cut_duration, seg_start))
        segments.append((seg_start, cut_duration))

    log(f"🎬 Slicing {len(segments)} narrative scenes (~{cut_duration:.1f}s each) between {timeline_start_str} and {timeline_end_str}...")

    temp_dir = output_sliced_path.parent / f"temp_slices_{output_sliced_path.stem}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    slice_files = []

    try:
        for idx, (s_time, c_dur) in enumerate(segments):
            seg_file = temp_dir / f"slice_{idx:03d}.mp4"
            cmd_slice = [
                "ffmpeg", "-y",
                "-ss", f"{s_time:.2f}",
                "-i", str(movie_path),
                "-t", f"{c_dur:.2f}",
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-an",
                str(seg_file)
            ]
            res = subprocess.run(cmd_slice, capture_output=True, text=True, timeout=45)
            if res.returncode == 0 and seg_file.exists() and seg_file.stat().st_size > 5000:
                slice_files.append(seg_file)
            else:
                log(f"   ↳ Slice {idx} at {s_time:.1f}s notice: {res.stderr[-100:] if res.stderr else 'failed'}")

        if len(slice_files) >= 3:
            concat_list_file = temp_dir / "concat_list.txt"
            with open(concat_list_file, "w", encoding="utf-8") as f:
                for sf in slice_files:
                    f.write(f"file '{sf.resolve()}'\n")

            cmd_concat = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_file),
                "-t", f"{target_duration:.2f}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-an",
                str(output_sliced_path)
            ]
            subprocess.run(cmd_concat, check=True, timeout=120)
            if output_sliced_path.exists() and output_sliced_path.stat().st_size > 100_000:
                log(f"✅ Successfully assembled {len(slice_files)} timeline scenes from movie into {output_sliced_path.name}")
                return True
    except Exception as e:
        log(f"⚠️ Slicing scenes failed: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Fallback to single seek slice if multi-slice had issues
    try:
        fb_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_sec:.2f}",
            "-i", str(movie_path),
            "-t", f"{target_duration:.2f}",
            "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-an",
            str(output_sliced_path)
        ]
        subprocess.run(fb_cmd, check=True, timeout=60)
        return output_sliced_path.exists() and output_sliced_path.stat().st_size > 100_000
    except Exception as e:
        log(f"⚠️ Fallback slice failed: {e}")
        return False


def download_movie_trailer(search_query: str, output_path: Path, trailer_url: str = None) -> bool:
    """
    Downloads the official movie trailer using direct URL or search query via yt-dlp.
    Applies multi-strategy fallback:
    1. Direct URL / Query with default yt-dlp client heuristics (NO cookies, avoids bot detection)
    2. Section download (00:00:10 to 00:01:40) to grab trailer footage quickly
    3. Direct stream URL resolution (yt-dlp -g) + FFmpeg streaming capture
    4. Client-specific fallbacks (android, tv, web)
    5. Cookies fallback as last resort
    """
    # Check local clips directory first (allows manual clip curation without network dependency)
    clean_stem = re.sub(r"_\d{8}_\d{6}$", "", output_path.stem)
    for folder in ["raw_clips", "clips", "videos"]:
        for cand in [WORKSPACE_DIR / folder / f"{output_path.stem}.mp4", WORKSPACE_DIR / folder / f"{clean_stem}.mp4"]:
            if cand.exists() and cand.stat().st_size > 300_000:
                log(f"🎬 Found local curated movie clip: {cand.name} ({cand.stat().st_size / 1024 / 1024:.1f} MB)")
                shutil.copy2(cand, output_path)
                return True

    try:
        from main import ytdlp_cookies_args
        cookies = ytdlp_cookies_args()
    except Exception:
        cookies = []

    pot_url = os.environ.get("YT_DLP_POT_PROVIDER_URL") or os.environ.get("POT_PROVIDER_URL") or "http://127.0.0.1:4416"

    candidate_targets = []
    if trailer_url and trailer_url.strip():
        candidate_targets.append(("direct_url", trailer_url.strip()))

    clean_query = re.sub(r"[^\w\s]", " ", search_query).strip()
    candidate_targets.extend([
        ("trailer_query", f"ytsearch2:{clean_query} official trailer 1080p"),
        ("direct_query", f"ytsearch2:{clean_query} official trailer"),
        ("clip_query", f"ytsearch2:{clean_query} movie scenes clips"),
    ])

    # Configurations designed to completely bypass YouTube datacenter IP bot detection
    client_configs = [
        # Android client uses native protobuf app API - immune to web bot check
        ("android", ["--extractor-args", "youtube:player_client=android"], "18/22/bv*[height<=720]+ba/b/best"),
        ("android_pot", ["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_url}", "--extractor-args", "youtube:player_client=android"], "18/22/bv*[height<=720]+ba/b/best"),
        ("tv", ["--extractor-args", "youtube:player_client=tv"], "b/best/18/22"),
        ("mweb_pot", ["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_url}", "--extractor-args", "youtube:player_client=mweb"], "18/22/b/best"),
        ("default", [], "bv*[height<=720]+ba/b/best/18/22")
    ]

    for tag, target in candidate_targets:
        log(f"🔍 Sourcing movie footage ({tag}): '{target}'...")

        for client_name, client_args, fmt in client_configs:
            # 1. Direct section download (fastest, extracts 45s core scenes)
            cmd_section = [
                "yt-dlp",
                target,
                "--download-sections", "*00:00:10-00:00:55",
                "--no-playlist",
                "--no-warnings",
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", str(output_path),
                "--no-check-certificates"
            ] + client_args

            try:
                res = subprocess.run(cmd_section, capture_output=True, text=True, timeout=90)
                if output_path.exists() and output_path.stat().st_size > 250_000:
                    log(f"✅ Sourced movie trailer [{client_name}] ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
                    return True
                elif res.returncode != 0 and res.stderr:
                    err_msg = res.stderr.strip().splitlines()[-1]
                    log(f"   ↳ [{client_name}] Section notice: {err_msg[:95]}")
            except subprocess.TimeoutExpired:
                log(f"   ↳ [{client_name}] Section download timed out after 90s")
            except Exception as e:
                log(f"   ↳ [{client_name}] Section error: {e}")

            # 2. Full trailer download with this client
            cmd_full = [
                "yt-dlp",
                target,
                "--no-playlist",
                "--no-warnings",
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", str(output_path),
                "--no-check-certificates"
            ] + client_args

            try:
                res = subprocess.run(cmd_full, capture_output=True, text=True, timeout=90)
                if output_path.exists() and output_path.stat().st_size > 250_000:
                    log(f"✅ Sourced movie trailer [{client_name}] ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
                    return True
                elif res.returncode != 0 and res.stderr:
                    err_msg = res.stderr.strip().splitlines()[-1]
                    log(f"   ↳ [{client_name}] Full notice: {err_msg[:95]}")
            except subprocess.TimeoutExpired:
                log(f"   ↳ [{client_name}] Full download timed out after 90s")
            except Exception as e:
                log(f"   ↳ [{client_name}] Full error: {e}")

            # 3. Direct stream resolution via yt-dlp -g + FFmpeg capture
            try:
                g_cmd = [
                    "yt-dlp", "-g",
                    "-f", fmt,
                    "--no-warnings",
                    "--no-check-certificates",
                ] + client_args + [target]
                g_res = subprocess.run(g_cmd, capture_output=True, text=True, timeout=30)
                urls = [l.strip() for l in g_res.stdout.strip().split("\n") if l.strip().startswith("http")]
                if urls:
                    log(f"   ↳ Direct stream resolved [{client_name}] ({len(urls)} URLs). Capturing with FFmpeg...")
                    if len(urls) >= 2:
                        ff_cmd = [
                            "ffmpeg", "-y",
                            "-ss", "00:00:10", "-t", "60", "-i", urls[0],
                            "-ss", "00:00:10", "-t", "60", "-i", urls[1],
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-c:a", "aac", "-b:a", "128k",
                            str(output_path)
                        ]
                    else:
                        ff_cmd = [
                            "ffmpeg", "-y",
                            "-ss", "00:00:10", "-t", "60", "-i", urls[0],
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-c:a", "aac", "-b:a", "128k",
                            str(output_path)
                        ]
                    subprocess.run(ff_cmd, capture_output=True, timeout=60)
                    if output_path.exists() and output_path.stat().st_size > 250_000:
                        log(f"✅ Sourced movie trailer stream [{client_name}] ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
                        return True
            except Exception as e:
                log(f"   ↳ [{client_name}] Stream error: {e}")

        # Optional last resort with cookies
        if cookies:
            try:
                cmd_cookies = [
                    "yt-dlp",
                    target,
                    "--no-playlist",
                    "-f", "18/22/b/best",
                    "--merge-output-format", "mp4",
                    "-o", str(output_path),
                    "--no-check-certificates"
                ] + cookies
                subprocess.run(cmd_cookies, capture_output=True, text=True, timeout=45)
                if output_path.exists() and output_path.stat().st_size > 250_000:
                    log(f"✅ Sourced movie trailer with cookies ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
                    return True
            except Exception:
                pass

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
        "-stream_loop", "-1",
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
            "-stream_loop", "-1",
            "-ss", f"{usable_start:.2f}",
            "-i", str(trailer_path),
            "-t", f"{target_duration:.2f}",
            "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
            "-c:v", "libx264", "-preset", "veryfast", "-an",
            str(output_sliced_path)
        ]
        subprocess.run(fb_cmd, check=True)
        return True


# Thematic start offsets for "Sukuna vs Mahoraga (Malevolent Shrine)" OST [182.6s total]:
# Different parts / shorts receive different movements of the song:
# Part 1 (0.0s):   Ominous Domain Expansion Prelude, Chimes & Eerie Ambience
# Part 2 (35.0s):  Rising Tension, Heavy Strings & Dark Heartbeat Percussion
# Part 3 (60.0s):  Full Orchestral Choral Explosion & Heavy Taiko Drums
# Part 4 (95.0s):  Dark Suspense Breakdown, Distorted Bass & Vocal Stems
# Part 5 (118.0s): Apocalyptic Climax & Rapid Brass Swells
DEFAULT_BGM_OFFSETS = [0.0, 35.0, 60.0, 95.0, 118.0]


def get_default_bgm_offset(part_number: int = None, movie_title: str = "") -> float:
    """Returns a tailored start offset into the BGM for variety across parts and shorts."""
    if part_number is not None and part_number > 0:
        return DEFAULT_BGM_OFFSETS[(part_number - 1) % len(DEFAULT_BGM_OFFSETS)]
    if movie_title:
        return DEFAULT_BGM_OFFSETS[abs(hash(movie_title)) % len(DEFAULT_BGM_OFFSETS)]
    return 0.0


def render_movie_explanation_short(
    sliced_video_path: Path,
    narration_audio_path: Path,
    ass_subtitle_path: Path,
    movie_title: str,
    badge_text: str,
    output_final_path: Path,
    bgm_path: Path = None,
    watermark_text: str = None,
    part_number: int = None,
    bgm_start_offset: float = None,
    bgm_volume: float = 0.065
) -> Path:
    """
    Renders the final 9:16 vertical Short (1080x1920) in the signature MovieGyan layout:
    - Fullscreen blurred background
    - 16:9 centered foreground clip with cinematic color grading & sharpness
    - Channel watermark & branding for YPP review verification
    - Sleek top badge pill with movie title
    - Bold animated karaoke subtitles in lower-third
    - Crystal clear voiceover with dark suspense BGM
    """
    duration = get_audio_duration(narration_audio_path)
    # YouTube Shorts strict copyright threshold: Movie recap content must stay strictly <= 58s
    MAX_SHORT_DURATION = 58.0
    if duration > MAX_SHORT_DURATION:
        log(f"⚠️ Audio duration ({duration:.1f}s) clamped to {MAX_SHORT_DURATION}s to stay strictly under 1 minute for YouTube Shorts copyright safety.")
        duration = MAX_SHORT_DURATION

    clean_badge = re.sub(r"[^A-Za-z0-9\s\(\)\-\.\,\!\?]", "", badge_text or movie_title).strip().upper()[:28]
    badge_display = f"MOVIE RECAP • {clean_badge}"

    font_path = find_system_font()
    if os.path.exists(font_path):
        font_opt = f"fontfile='{font_path}'"
    else:
        font_opt = "font='DejaVu Sans'"

    ass_esc = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    # Select BGM (Default: Jujutsu Kaisen Malevolent Shrine dark epic cover)
    if not bgm_path or not bgm_path.exists():
        bgm_path = BGM_DIR / "malevolent_shrine_sukuna.mp3"
        if not bgm_path.exists():
            bgm_path = BGM_DIR / "cinematic_suspense_thriller.mp3"
        if not bgm_path.exists():
            bgm_path = BGM_DIR / "cinematic_suspense_drone.mp3"

    has_bgm = bgm_path and bgm_path.exists()
    if has_bgm and bgm_start_offset is None:
        bgm_start_offset = get_default_bgm_offset(part_number, movie_title)

    log(f"🎨 Rendering Movie Explanation Short (1080x1920, {duration:.1f}s)...")
    if has_bgm:
        log(f"🎵 Using dark BGM: {bgm_path.name} (start_offset={bgm_start_offset:.1f}s, vol={bgm_volume:.3f})")

    # Optional channel watermark for YPP brand identity
    watermark_filter = ""
    if watermark_text:
        clean_wm = re.sub(r"[^A-Za-z0-9@\._\- ]", "", watermark_text).strip()
        watermark_filter = f",drawtext=text='{clean_wm}':fontsize=26:fontcolor=white@0.65:{font_opt}:x=w-text_w-50:y=h-text_h-240"

    # Multi-layered anti-copyright transformation filtergraph:
    # 1. Fullscreen blurred background (boxblur 28:6, darkened)
    # 2. Foreground 16:9 movie box: color grading (+14% contrast, +18% saturation, gamma 0.96),
    #    cinematic vignette, high-pass unsharp (5:5:0.8) modifying frequency coefficients,
    #    temporal film noise (7) preventing static hash matching
    # 3. Gold frame border outlining the movie box
    # 4. Top header badge pill and lower-third dynamic karaoke subtitles
    video_filters = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=28:6,eq=brightness=-0.14:contrast=1.06[bg];"
        "[0:v]scale=1040:-2,eq=contrast=1.14:brightness=-0.03:saturation=1.18:gamma=0.96,vignette=PI/4.2,unsharp=5:5:0.8:5:5:0.4,noise=alls=7:allf=t[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2 - 40[comp];"
        f"[comp]drawbox=x=18:y=626:w=1044:h=589:color=gold@0.45:t=2,"
        f"drawbox=x=40:y=120:w=1000:h=90:color=black@0.75:t=fill,"
        f"drawtext=text='{badge_display}':fontsize=36:fontcolor=white:{font_opt}:x=(w-text_w)/2:y=148"
        f"{watermark_filter},"
        f"ass='{ass_esc}'[vfinal]"
    )

    cmd = ["ffmpeg", "-y", "-i", str(sliced_video_path), "-i", str(narration_audio_path)]
    if has_bgm:
        cmd.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        # Subdued dark BGM mix:
        # 1. Speech boosted to 1.15x for crystal-clear narration
        # 2. BGM trimmed to thematic movement offset, smooth 1.5s fade-in, lowpass filtered (3200Hz) to prevent vocal clashes, and kept low (0.065)
        # 3. normalize=0 ensures voice volume is not cut in half, alimiter protects against clipping
        audio_filters = (
            f"[1:a]volume=1.15[voice];"
            f"[2:a]atrim=start={bgm_start_offset:.2f},asetpts=PTS-STARTPTS,afade=t=in:ss=0:d=1.5,volume={bgm_volume:.3f},lowpass=f=3200[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95[afinal]"
        )
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
