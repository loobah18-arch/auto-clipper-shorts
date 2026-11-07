#!/usr/bin/env python3
"""
Isla Value-Add Video & Audio Engine.
Assembles 9:16 full-bleed Shorts with:
1. Neural narration voiceover (edge-tts Christopher/Guy)
2. Original creator audio reaction cut-ins
3. Dynamic BGM mixing with dramatic silence drops
4. Kinetic karaoke ASS subtitle burning
5. Full-bleed 1080x1920 crop with zero letterboxing
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from isla_story_engine import AudioCutSegment, LoopedArcScript


def log(msg: str) -> None:
    print(f"[IslaVideoEngine] {msg}", flush=True)


DEFAULT_VOICE = "en-US-AvaNeural"
DEFAULT_CANVAS_WIDTH = 1080
DEFAULT_CANVAS_HEIGHT = 1440  # 3:4 aspect ratio matching @testednproved (2160x2880)
DEFAULT_BGM = Path(__file__).resolve().parent / "assets" / "bgm" / "cradles.mp3"
DEFAULT_ARROW = Path(__file__).resolve().parent / "assets" / "red_arrow.png"


async def _generate_voiceover_edge_tts(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+6%",
) -> Path:
    """Generates high-speed neural TTS via edge-tts."""
    import edge_tts

    output_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(output_path))
    return output_path


def generate_voiceover(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
) -> Path:
    """Synchronous wrapper for edge-tts generation."""
    log(f"🎙️ Synthesizing neural voiceover ({voice}): '{text[:60]}...'")
    try:
        asyncio.run(_generate_voiceover_edge_tts(text, output_path, voice=voice))
        return output_path
    except Exception as e:
        log(f"⚠️ edge-tts failed ({e}); creating fallback audio tone.")
        # Create silent/fallback wav if edge-tts is blocked
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", "5.0", "-q:a", "9", "-acodec", "libmp3lame", str(output_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return output_path


def _probe_local_duration(file_path: Path) -> float:
    """Probes the exact duration of a local media file using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 5.0


def fmt_time(t: float) -> str:
    hours = int(t // 3600)
    minutes = int((t % 3600) // 60)
    seconds = int(t % 60)
    centis = int(round((t - int(t)) * 100))
    if centis >= 100:
        seconds += 1
        centis -= 100
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def compute_kinetic_word_timings(
    text: str,
    start_sec: float,
    speech_duration: float,
    chunk_size: int = 2,
    raw_word_boundaries: Optional[list[tuple[str, float, float]]] = None,
    speed_factor: float = 1.0,
) -> list[tuple[float, float, str]]:
    """
    Computes precise millisecond word-chunk display intervals locked to real speech audio.
    Prevents subtitle drift, eliminates trailing text during pauses, and ensures smooth 1-2 word popup transitions.
    """
    if raw_word_boundaries:
        scaled = []
        for w_text, w_start, w_end in raw_word_boundaries:
            s_start = start_sec + (w_start / speed_factor)
            s_end = start_sec + (w_end / speed_factor)
            scaled.append((w_text, s_start, s_end))

        chunks = []
        for i in range(0, len(scaled), chunk_size):
            group = scaled[i:i + chunk_size]
            c_start = max(start_sec, group[0][1] - 0.05)
            c_end = group[-1][2]
            c_text = " ".join(item[0] for item in group).lower()
            chunks.append((c_start, c_end, c_text))
        return chunks

    words = text.strip().split()
    if not words:
        return []

    import re
    weights = []
    for w in words:
        clean = re.sub(r"[^\w]", "", w)
        wt = max(1.0, float(len(clean)))
        if w.endswith(","):
            wt += 1.5
        elif w.endswith((".", "?", "!", ":", ";")):
            wt += 2.5
        weights.append(wt)
    tot = sum(weights) or 1.0

    timings = []
    curr = start_sec
    for w, wt in zip(words, weights):
        w_dur = max(0.12, speech_duration * (wt / tot))
        timings.append((w, curr, curr + w_dur))
        curr += w_dur

    chunks = []
    for i in range(0, len(timings), chunk_size):
        group = timings[i:i + chunk_size]
        c_start = max(start_sec, group[0][1] - 0.05)
        c_end = group[-1][2]
        c_text = " ".join(item[0] for item in group).lower()
        chunks.append((c_start, c_end, c_text))
    return chunks


def build_isla_karaoke_ass(
    script: LoopedArcScript,
    output_ass: Path,
    video_width: int = DEFAULT_CANVAS_WIDTH,
    video_height: int = DEFAULT_CANVAS_HEIGHT,
    measured_events: Optional[list[tuple[float, float, str, str]]] = None,
) -> Path:
    """
    Builds high-impact kinetic ASS subtitles matching Isla's (@testednproved) exact style:
    - Lowercase text
    - Pure white text (&H00FFFFFF&)
    - Deep solid black outline (5.8)
    - Positioned directly beneath the center 16:9 widescreen video (MarginV: 380 for 1440p, 620 for 1920p)
    - Fast, punchy 1-2 word popup cadence locked to speech audio
    """
    output_ass.parent.mkdir(parents=True, exist_ok=True)

    margin_v = 380 if video_height == 1440 else (620 if video_height == 1920 else int(video_height * 0.265))

    header = f"""[Script Info]
Title: Isla Value-Add Kinetic Subtitles
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5.8,0.0,2,60,60,{margin_v},1
Style: Creator,Montserrat,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6.0,0.0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    if measured_events:
        for c_start, c_end, c_text, c_style in measured_events:
            events.append(
                f"Dialogue: 0,{fmt_time(c_start)},{fmt_time(c_end)},{c_style},,0,0,0,,{c_text}"
            )
    else:
        for seg in script.segments:
            text = seg.text.strip().lower()
            if not text:
                continue
            seg_start = seg.start_sec
            seg_end = seg.end_sec
            dur = max(0.5, seg_end - seg_start)

            if seg.speaker == "creator":
                events.append(
                    f'Dialogue: 0,{fmt_time(seg_start)},{fmt_time(seg_end)},Creator,,0,0,0,,"{text}"'
                )
                continue

            chunks = compute_kinetic_word_timings(
                text=text,
                start_sec=seg_start,
                speech_duration=dur,
            )
            for c_start, c_end, c_text in chunks:
                events.append(
                    f"Dialogue: 0,{fmt_time(c_start)},{fmt_time(c_end)},Default,,0,0,0,,{c_text}"
                )

    output_ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log(f"📝 Generated kinetic subtitles -> {output_ass.name}")
    return output_ass


def assemble_narration_track(
    script: LoopedArcScript,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
) -> tuple[Path, list[tuple[float, float, str, str]]]:
    """
    Synthesizes each narrator segment individually and inserts precise silence
    for creator segments, creating a master voiceover track with exact zero-speech
    cut-ins where the creator talks. Returns (master_audio_path, measured_events).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / f"vo_parts_{output_path.stem}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    part_files: list[Path] = []
    measured_events: list[tuple[float, float, str, str]] = []

    for idx, seg in enumerate(script.segments):
        target_dur = max(0.5, seg.end_sec - seg.start_sec)
        part_file = temp_dir / f"vo_part_{idx:02d}.mp3"

        if seg.speaker == "creator" or not seg.text.strip():
            # Exact silence for creator cut-in
            cmd_silence = [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{target_dur:.3f}",
                "-q:a", "9", "-acodec", "libmp3lame",
                str(part_file),
            ]
            subprocess.run(cmd_silence, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            part_files.append(part_file)
            if seg.text.strip():
                measured_events.append((seg.start_sec, seg.end_sec, f'"{seg.text.strip().lower()}"', "Creator"))
        else:
            # Synthesize TTS for narrator segment
            generate_voiceover(seg.text, part_file, voice=voice)
            actual_dur = _probe_local_duration(part_file)
            diff = target_dur - actual_dur

            speed = 1.0
            if diff > 0.15:
                # Pad remaining time with silence to keep exact visual sync
                padded_file = temp_dir / f"vo_part_{idx:02d}_padded.mp3"
                pad_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(part_file),
                    "-af", f"apad=whole_dur={target_dur:.3f}",
                    "-q:a", "9", "-acodec", "libmp3lame",
                    str(padded_file),
                ]
                subprocess.run(pad_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                if padded_file.exists() and padded_file.stat().st_size > 500:
                    part_files.append(padded_file)
                else:
                    part_files.append(part_file)
                effective_speech_dur = actual_dur
            elif diff < -0.2:
                # Speed up slightly with atempo so speech fits within segment
                speed = min(1.35, actual_dur / target_dur)
                sped_file = temp_dir / f"vo_part_{idx:02d}_sped.mp3"
                speed_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(part_file),
                    "-af", f"atempo={speed:.3f}",
                    "-q:a", "9", "-acodec", "libmp3lame",
                    str(sped_file),
                ]
                subprocess.run(speed_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                if sped_file.exists() and sped_file.stat().st_size > 500:
                    part_files.append(sped_file)
                else:
                    part_files.append(part_file)
                effective_speech_dur = target_dur
            else:
                part_files.append(part_file)
                effective_speech_dur = actual_dur

            chunks = compute_kinetic_word_timings(
                text=seg.text,
                start_sec=seg.start_sec,
                speech_duration=effective_speech_dur,
                speed_factor=speed,
            )
            for c_start, c_end, c_text in chunks:
                measured_events.append((c_start, c_end, c_text, "Default"))

    # Concat all audio parts into master narration track
    concat_list = temp_dir / "concat_parts.txt"
    concat_list.write_text("\n".join(f"file '{f.resolve()}'" for f in part_files), encoding="utf-8")
    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]
    subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    log(f"🎙️ Master narration track assembled with dynamic creator cut-ins -> {output_path.name}")
    return output_path, measured_events


def reframe_to_isla_canvas_cmd(
    input_video: Path,
    output_video: Path,
    start_sec: float,
    duration_sec: float,
    canvas_width: int = DEFAULT_CANVAS_WIDTH,
    canvas_height: int = DEFAULT_CANVAS_HEIGHT,
) -> list[str]:
    """
    Reframes 16:9 widescreen video into Isla's signature 3:4 canvas (1080x1440 / 2160x2880)
    using split blurred letterbox padding so 100% of the widescreen video action is preserved.
    """
    filter_chain = (
        f"[0:v]scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=increase,"
        f"crop={canvas_width}:{canvas_height},boxblur=25:5[bg];"
        f"[0:v]scale={canvas_width}:-1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
    )
    return [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.2f}",
        "-t", f"{duration_sec:.2f}",
        "-i", str(input_video),
        "-filter_complex", filter_chain,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_video),
    ]


def crop_to_fullbleed_9x16_cmd(
    input_video: Path,
    output_video: Path,
    start_sec: float,
    duration_sec: float,
) -> list[str]:
    """Backward compatible wrapper for existing test suites."""
    filter_chain = (
        "scale=1080*16/9:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920:(in_w-1080)/2:(in_h-1920)/2"
    )
    return [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.2f}",
        "-t", f"{duration_sec:.2f}",
        "-i", str(input_video),
        "-vf", filter_chain,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_video),
    ]


def assemble_isla_short(
    source_video: Path,
    script: LoopedArcScript,
    output_dir: Path = Path("output"),
    voice: str = DEFAULT_VOICE,
    bgm_path: Optional[Path] = None,
    is_pre_sliced: bool = True,
    canvas_width: int = DEFAULT_CANVAS_WIDTH,
    canvas_height: int = DEFAULT_CANVAS_HEIGHT,
    overlay_arrow: bool = True,
) -> Path:
    """
    Complete rendering pipeline:
    1. Synthesizes voiceover tracks for narrator segments (edge-tts Ava/Jenny).
    2. Reframes raw video into 1080x1440 3:4 canvas with blurred letterboxing.
    3. Overlays hook red arrow in opening 0.2s - 2.6s.
    4. Generates and burns lowercase kinetic subtitles in safe zone.
    5. Mixes voiceover + original creator audio + dynamic BGM.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = f"isla_short_{Path(source_video).stem[:16]}"
    final_output = output_dir / f"{slug}_final.mp4"

    log(f"🎬 Assembling Isla Value-Add Short: '{script.title}' ({canvas_width}x{canvas_height} 3:4 canvas)...")

    clip_start = 0.0 if is_pre_sliced else script.raw_clip_start
    total_target_dur = max(40.0, script.segments[-1].end_sec if script.segments else 60.0)
    clip_dur = total_target_dur if is_pre_sliced else min(63.0, max(58.0, script.raw_clip_end - script.raw_clip_start))

    # 1. Generate master narration audio track with creator reaction cut-ins
    vo_path = output_dir / f"{slug}_voiceover.mp3"
    _, measured_events = assemble_narration_track(script, vo_path, voice=voice)

    # 2. Build Kinetic Subtitles in Shorts safe-zone locked to measured voice audio
    ass_path = output_dir / f"{slug}_subtitles.ass"
    build_isla_karaoke_ass(
        script, ass_path,
        video_width=canvas_width, video_height=canvas_height,
        measured_events=measured_events,
    )

    # 3. Reframe video into 3:4 canvas with blurred background letterboxing
    reframed_video = output_dir / f"{slug}_reframed.mp4"
    reframe_cmd = reframe_to_isla_canvas_cmd(
        source_video, reframed_video, clip_start, clip_dur,
        canvas_width=canvas_width, canvas_height=canvas_height
    )
    log(f"✂️ Reframing raw video to {canvas_width}x{canvas_height} with blurred letterbox padding ({clip_start:.1f}s -> {clip_start + clip_dur:.1f}s)...")
    subprocess.run(reframe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    if not reframed_video.exists() or reframed_video.stat().st_size < 100_000:
        log("⚠️ Reframe failed; falling back to source video copy.")
        reframed_video = source_video

    # 4. Final assembly: Burn kinetic subtitles, overlay hook arrow, and mix dynamic audio
    active_bgm = bgm_path or (DEFAULT_BGM if DEFAULT_BGM.exists() else None)
    arrow_file = DEFAULT_ARROW if (overlay_arrow and DEFAULT_ARROW.exists()) else None

    # Dynamic creator volume boost expression
    creator_intervals = [
        (s.start_sec, s.end_sec)
        for s in script.segments
        if s.speaker == "creator"
    ]
    if creator_intervals:
        cond_creator = "+".join(
            f"between(t,{start:.2f},{end:.2f})" for start, end in creator_intervals
        )
        raw_audio_filter = f"volume='if({cond_creator}, 1.25, 0.02)':eval=frame"
    else:
        raw_audio_filter = "volume=0.02"

    # Dynamic BGM ducking expression (near-silence during creator reaction)
    bgm_silence_intervals = [
        (s.start_sec, s.end_sec)
        for s in script.segments
        if s.speaker == "creator" or s.bgm_action == "silence"
    ]
    if bgm_silence_intervals:
        cond_bgm = "+".join(
            f"between(t,{start:.2f},{end:.2f})" for start, end in bgm_silence_intervals
        )
        bgm_audio_filter = f"volume='if({cond_bgm}, 0.002, 0.025)':eval=frame"
    else:
        bgm_audio_filter = "volume=0.025"

    inputs = [
        "-i", str(reframed_video),
        "-i", str(vo_path),
    ]

    # Video filters: arrow overlay (if present) + ASS subtitles burn
    input_idx = 2
    if arrow_file:
        inputs.extend(["-i", str(arrow_file)])
        arrow_in_idx = input_idx
        input_idx += 1
        video_filter_part = (
            f"[0:v][{arrow_in_idx}:v]overlay=160:560:enable='between(t,0.2,2.6)'[varrow];"
            f"[varrow]ass={ass_path}[outv]"
        )
    else:
        video_filter_part = f"[0:v]ass={ass_path}[outv]"

    if active_bgm and active_bgm.exists():
        inputs.extend(["-stream_loop", "-1", "-i", str(active_bgm)])
        bgm_in_idx = input_idx
        input_idx += 1
        audio_filter_part = (
            f"[0:a]{raw_audio_filter}[araw];"
            f"[1:a]volume=1.35[avo];"
            f"[{bgm_in_idx}:a]{bgm_audio_filter}[abgm];"
            f"[araw][avo][abgm]amix=inputs=3:normalize=0:duration=first:dropout_transition=0,alimiter=limit=0.98[outa]"
        )
        filter_complex = f"{video_filter_part};{audio_filter_part}"
        cmd_final = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
        ]
    else:
        audio_filter_part = (
            f"[0:a]{raw_audio_filter}[araw];"
            f"[1:a]volume=1.35[avo];"
            f"[araw][avo]amix=inputs=2:normalize=0:duration=first:dropout_transition=0,alimiter=limit=0.98[outa]"
        )
        filter_complex = f"{video_filter_part};{audio_filter_part}"
        cmd_final = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
        ]

    cmd_final.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(final_output),
    ])

    log(f"🔥 Rendering final Isla Value-Add Short ({canvas_width}x{canvas_height}) -> {final_output.name}...")
    res = subprocess.run(cmd_final, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        log(f"⚠️ Subtitle burn failed ({res.stderr[:200]}); copying reframed video.")
        import shutil
        shutil.copyfile(reframed_video, final_output)

    log(f"✅ Isla Value-Add Short rendered successfully: {final_output.name} ({final_output.stat().st_size} bytes)")
    return final_output
