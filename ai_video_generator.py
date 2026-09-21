#!/usr/bin/env python3
"""
🎬 AI Video Generator for Automated Shorts
Generates high-retention 9:16 vertical video scenes directed by the AI script model.

Multi-Tier Visual Sourcing Architecture:
1. Tier 1: Free Cloud AI Generation (Pollinations Flux / SDXL).
2. Tier 2: Wikimedia Commons Open Media High-Res Photography (Zero rate-limits, instant authentic visuals).
3. Tier 3: Procedural Cinematic Motion Backdrop with Typography (100% offline resilience).

Animation & Compositing:
- Turns still visual scenes into dynamic 9:16 vertical 30fps video clips with 4 cinematic camera motions:
  * Slow Push-In (zoom 1.0 -> 1.18)
  * Slow Pull-Out (zoom 1.18 -> 1.0)
  * Vertical Tilt / Pan Down
  * Horizontal Cinematic Tracking Drift
- Concat-stitches all scenes into a seamless 1080x1920 video track matching the audio length.
"""

import os
import re
import sys
import time
import json
import random
import shutil
import urllib.parse
import urllib.request
import subprocess
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = WORKSPACE_DIR / ".cache" / "ai_scenes"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(f"[ai_video_generator] {msg}", flush=True)


def clean_prompt_for_image_gen(prompt: str) -> str:
    """Cleans prompt for photorealistic 9:16 vertical generation."""
    cleaned = re.sub(r"^(scene\s*\d*[\s:\-–—\(\)\d\w]*:)", "", prompt, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'^["\']|["\']$', "", cleaned).strip()
    if not cleaned:
        cleaned = "cinematic atmospheric scene, 8k photorealistic"
    return cleaned


def create_gradient_fallback_image(text_label: str, output_path: Path, width: int = 720, height: int = 1280) -> bool:
    """Generates an attractive dark gradient backdrop with subtle typography as a zero-network fallback."""
    colors = [
        ("0x0d1117", "0x161b22"),
        ("0x0b132b", "0x1c2541"),
        ("0x1a0933", "0x2d124d"),
        ("0x1f1d1d", "0x2e282a"),
        ("0x0f2027", "0x203a43")
    ]
    c1, c2 = random.choice(colors)
    safe_text = re.sub(r"[^A-Za-z0-9\s]", "", text_label).strip()[:35] or "CINEMATIC SCENE"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={c1}:s={width}x{height}:d=1",
        "-vf", (
            f"drawbox=x=0:y=0:w={width}:h={height}:color={c2}@0.4:t=fill,"
            f"drawbox=x=40:y={height//2 - 50}:w={width - 80}:h=100:color=white@0.08:t=fill,"
            f"drawtext=text='{safe_text}':fontsize=30:fontcolor=white@0.85:x=(w-text_w)/2:y=(h-text_h)/2"
        ),
        "-vframes", "1",
        "-update", "1",
        str(output_path)
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path.exists()
    except Exception as e:
        log(f"⚠️ Fallback gradient generation notice: {e}")
        return False


def fetch_wikimedia_visual(query: str, output_path: Path) -> bool:
    """Fetches authentic high-resolution photography from Wikimedia Commons with zero rate limits."""
    stop_words = {"vertical", "photorealistic", "cinematic", "scene", "shot", "detailed", "lighting", "ultra", "high", "view", "master", "resolution", "giant"}
    words = [w for w in re.sub(r"[^A-Za-z0-9\s]", "", query).split() if len(w) > 3 and w.lower() not in stop_words]
    search_terms = words[:2] if words else ["landscape"]
    query_str = " ".join(search_terms)

    search_url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&list=search"
        f"&srsearch={urllib.parse.quote(query_str)}&srnamespace=6&srlimit=4&format=json"
    )

    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0 (AutomatedShortsBot/1.0)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        results = data.get("query", {}).get("search", [])

        for item in results:
            title = item.get("title")
            if not title:
                continue
            info_url = (
                f"https://commons.wikimedia.org/w/api.php?action=query&titles={urllib.parse.quote(title)}"
                f"&prop=imageinfo&iiprop=url|mime&format=json"
            )
            i_req = urllib.request.Request(info_url, headers={"User-Agent": "Mozilla/5.0 (AutomatedShortsBot/1.0)"})
            with urllib.request.urlopen(i_req, timeout=8) as ir:
                i_data = json.loads(ir.read())
            pages = i_data.get("query", {}).get("pages", {})
            for _, p in pages.items():
                for info in p.get("imageinfo", []):
                    img_url = info.get("url")
                    mime = info.get("mime", "")
                    if img_url and ("image/jpeg" in mime or "image/png" in mime or "image/webp" in mime):
                        d_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0 (AutomatedShortsBot/1.0)"})
                        with urllib.request.urlopen(d_req, timeout=12) as d_resp:
                            img_bytes = d_resp.read()
                            if len(img_bytes) > 10000:
                                with open(output_path, "wb") as f:
                                    f.write(img_bytes)
                                return True
    except Exception as e:
        log(f"   ↳ Wikimedia fetch notice: {e}")
    return False


def download_ai_image(
    prompt: str,
    output_path: Path,
    width: int = 720,
    height: int = 1280,
    seed: int = None
) -> bool:
    """
    Downloads scene visual using resilient multi-tier fallback:
    Tier 1: Free Cloud AI generation (Pollinations).
    Tier 2: Wikimedia Commons authentic high-res open media.
    Tier 3: Procedural studio gradient with scene typography.
    """
    cleaned_prompt = clean_prompt_for_image_gen(prompt)
    if seed is None:
        seed = random.randint(1000, 999999)

    # Tier 1: Pollinations Cloud AI
    try:
        encoded_prompt = urllib.parse.quote(f"{cleaned_prompt}, 9:16 vertical, photorealistic, 8k")
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&nologo=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = response.read()
                if len(data) > 8000:
                    with open(output_path, "wb") as f:
                        f.write(data)
                    return True
    except Exception as pe:
        log(f"   ↳ Tier 1 (Pollinations) unavailable ({pe}), routing to Tier 2...")

    # Tier 2: Wikimedia Commons High-Res Photography
    if fetch_wikimedia_visual(cleaned_prompt, output_path):
        return True

    # Tier 3: Procedural Cinematic Studio Backdrop
    log(f"   ↳ Tier 2 unavailable, generating Tier 3 procedural cinematic backdrop...")
    return create_gradient_fallback_image(prompt, output_path, width=width, height=height)


def animate_scene_clip(
    image_path: Path,
    duration: float,
    output_clip_path: Path,
    motion_type: int = 0
) -> bool:
    """
    Turns a still visual into an animated 9:16 (1080x1920) 30fps video clip.
    Cycles through 4 camera dynamics:
    0: Smooth Push-In (slow zoom in)
    1: Smooth Pull-Out (slow zoom out)
    2: Vertical Tilt Down
    3: Horizontal Tracking Drift
    """
    fps = 30
    total_frames = max(30, int(duration * fps))

    # Configure zoompan camera dynamics
    if motion_type % 4 == 0:
        zoom_step = 0.15 / max(1, total_frames)
        z_expr = f"min(zoom+{zoom_step:.6f},1.18)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type % 4 == 1:
        zoom_step = 0.15 / max(1, total_frames)
        z_expr = f"max(1.18-{zoom_step:.6f}*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type % 4 == 2:
        z_expr = "1.12"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*(on/{total_frames})"
    else:
        z_expr = "1.12"
        x_expr = f"(iw-iw/zoom)*(on/{total_frames})"
        y_expr = "ih/2-(ih/zoom/2)"

    filter_str = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1080x1920:fps={fps}[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-filter_complex", filter_str,
        "-map", "[v]",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(output_clip_path)
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and output_clip_path.exists() and output_clip_path.stat().st_size > 1000:
        return True

    # Robust fallback scale if zoompan filter experiences any parser issue
    fallback_cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
        "-t", f"{duration:.2f}",
        "-r", "30",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(output_clip_path)
    ]
    sub_res = subprocess.run(fallback_cmd, capture_output=True)
    return sub_res.returncode == 0 and output_clip_path.exists()


def derive_visual_prompts_from_script(script_text: str, title: str, count: int = 5) -> list:
    """Generates visual scene prompts from script sentences if the AI model did not provide them."""
    sentences = [s.strip() for s in re.split(r"[.!?]+", script_text) if len(s.strip()) > 10]
    prompts = []

    for idx, sentence in enumerate(sentences[:count]):
        clean_s = re.sub(r"[^A-Za-z0-9\s]", "", sentence).strip()
        prompts.append(
            f"Cinematic photorealistic scene of {title}: {clean_s}. Dramatic lighting, 9:16 vertical composition"
        )

    while len(prompts) < max(3, count):
        prompts.append(
            f"Dramatic cinematic visual of {title}, moody atmosphere, 9:16 vertical"
        )

    return prompts[:count]


def generate_ai_video_track(
    visual_prompts: list,
    total_duration: float,
    output_video_path: Path,
    title: str = "Video Short",
    script_text: str = ""
) -> bool:
    """
    Main entry point:
    1. Receives visual scene prompts directed by the AI model.
    2. Slices and generates visual scenes.
    3. Animates each scene into a 9:16 video clip.
    4. Concat-stitches scenes into the complete visual video track matching audio duration.
    """
    log("=" * 60)
    log(f"🎬 AI VIDEO GENERATOR: Synthesizing {total_duration:.1f}s video track for '{title}'")
    log("=" * 60)

    # Ensure clean visual prompts
    if not visual_prompts or len(visual_prompts) == 0:
        log("ℹ️ No visual prompts supplied, synthesizing from script...")
        target_scene_count = max(3, min(8, int(total_duration // 4.0)))
        visual_prompts = derive_visual_prompts_from_script(script_text, title, count=target_scene_count)

    num_scenes = len(visual_prompts)
    scene_duration = round(total_duration / max(1, num_scenes), 2)
    if scene_duration < 2.5 and num_scenes > 3:
        num_scenes = max(3, int(total_duration // 3.2))
        visual_prompts = visual_prompts[:num_scenes]
        scene_duration = round(total_duration / num_scenes, 2)

    log(f"📽️ Generating {num_scenes} AI-directed scenes (~{scene_duration:.2f}s each)...")

    scene_clips = []
    temp_files = []

    try:
        for idx, prompt_item in enumerate(visual_prompts):
            prompt_text = prompt_item if isinstance(prompt_item, str) else prompt_item.get("prompt", str(prompt_item))
            scene_num = idx + 1
            log(f"🎨 Scene {scene_num}/{num_scenes}: \"{prompt_text[:65]}...\"")

            # Calculate duration for last scene to match total length exactly
            current_scene_dur = scene_duration
            if scene_num == num_scenes:
                current_scene_dur = max(1.0, total_duration - (scene_duration * (num_scenes - 1)))

            img_path = CACHE_DIR / f"scene_{scene_num}_{int(time.time())}_{random.randint(100,999)}.jpg"
            clip_path = CACHE_DIR / f"clip_{scene_num}_{int(time.time())}_{random.randint(100,999)}.mp4"
            temp_files.extend([img_path, clip_path])

            # 1. Fetch visual (AI -> Wikimedia -> Studio Gradient)
            ok_img = download_ai_image(prompt_text, img_path, width=720, height=1280)
            if not ok_img or not img_path.exists():
                create_gradient_fallback_image(title, img_path, width=720, height=1280)

            # 2. Animate into video clip
            ok_clip = animate_scene_clip(img_path, current_scene_dur, clip_path, motion_type=idx)
            if ok_clip and clip_path.exists():
                scene_clips.append(clip_path)
            else:
                log(f"   ❌ Failed to animate scene {scene_num}")

        if not scene_clips:
            log("❌ No scene clips could be generated.")
            return False

        log(f"🎞️ Concatenating {len(scene_clips)} animated scene clips into final track...")

        # Build FFmpeg concat list
        concat_list_file = CACHE_DIR / f"concat_list_{int(time.time())}_{random.randint(100,999)}.txt"
        temp_files.append(concat_list_file)
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for c in scene_clips:
                f.write(f"file '{c.resolve()}'\n")

        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-t", f"{total_duration:.2f}",
            "-an",
            str(output_video_path)
        ]

        res = subprocess.run(concat_cmd, capture_output=True, text=True)
        if res.returncode == 0 and output_video_path.exists() and output_video_path.stat().st_size > 10000:
            log(f"✅ AI Video Track assembled successfully: {output_video_path} ({output_video_path.stat().st_size / 1024 / 1024:.2f} MB)")
            return True
        else:
            log(f"⚠️ Concat demuxer failed, falling back to filter concat...")
            inputs = []
            filter_chunks = []
            for i, c in enumerate(scene_clips):
                inputs.extend(["-i", str(c)])
                filter_chunks.append(f"[{i}:v]")
            filter_str = f"{''.join(filter_chunks)}concat=n={len(scene_clips)}:v=1:a=0[v]"

            fb_cmd = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[v]",
                "-t", f"{total_duration:.2f}",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-an",
                str(output_video_path)
            ]
            fb_res = subprocess.run(fb_cmd, capture_output=True)
            return fb_res.returncode == 0 and output_video_path.exists()

    finally:
        for tf in temp_files:
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    test_prompts = [
        "Giant Caterpillar excavator digging rock, 9:16 vertical",
        "Golden retriever puppy playing with toddler, 9:16 vertical"
    ]
    test_out = OUTPUT_DIR / "test_ai_video_track.mp4"
    log("Running multi-tier AI Video Generator test...")
    success = generate_ai_video_track(test_prompts, total_duration=6.0, output_video_path=test_out, title="Multi-Tier Test")
    print(f"Test Result: {'SUCCESS' if success else 'FAILED'} -> {test_out}")
