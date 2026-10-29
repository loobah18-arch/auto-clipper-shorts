#!/usr/bin/env python3
"""
Cinerush Karaoke Subtitles Generator (.ass)
Burns punchy, synced subtitles of actual movie dialogue:
- Short readable chunks of 3 to 4 words maximum (breaks on punctuation . ? ! , or pause > 0.4s)
- Font: Montserrat ExtraBold / DejaVu Sans Bold, Size 70, Bold -1
- Alignment: 2 (Bottom Center), MarginV: 280 (above Shorts UI)
- Active spoken word highlighted in bright yellow (\\c&H00FFFF&\\fscx108\\fscy108)
- Inactive words in pure white (\\c&HFFFFFF&\\fscx100\\fscy100)
- Outline 5 (Black), Shadow 2
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[CinerushSubtitles] {msg}", flush=True)


FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


def format_ass_time(seconds: float) -> str:
    """Converts seconds into ASS time format: H:MM:SS.cs"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def group_words_into_cinerush_phrases(
    words: list[dict[str, Any]],
    max_words_per_phrase: int = 4,
    pause_threshold: float = 0.40,
) -> list[list[dict[str, Any]]]:
    """
    Groups spoken words into 3 to 4 word punchy phrases:
    - Breaks early if word ends with punctuation (. , ? ! : ;)
    - Breaks early if gap between words > 0.4s
    - Caps phrase at max 4 words
    """
    phrases: list[list[dict[str, Any]]] = []
    current_phrase: list[dict[str, Any]] = []

    punctuation_regex = re.compile(r"[\.,\?!:;]$")

    for i, w in enumerate(words):
        word_text = str(w.get("word", "")).strip()
        if not word_text:
            continue

        current_phrase.append(w)

        is_punctuated = bool(punctuation_regex.search(word_text))
        is_max_length = len(current_phrase) >= max_words_per_phrase

        is_pause = False
        if i < len(words) - 1:
            next_start = words[i + 1].get("start", 0.0)
            curr_end = w.get("end", 0.0)
            if next_start - curr_end > pause_threshold:
                is_pause = True

        if is_punctuated or is_max_length or is_pause:
            phrases.append(current_phrase)
            current_phrase = []

    if current_phrase:
        phrases.append(current_phrase)

    return phrases


def generate_cinerush_karaoke_ass(
    words: list[dict[str, Any]],
    output_ass_path: Path,
    font_name: str = "Montserrat ExtraBold",
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    margin_v: int = 280,
    profile: str | None = None,
) -> Path:
    """
    Generates ASS subtitle file with word-by-word karaoke highlighting.
    Active word is bright yellow (\\c&H00FFFF&\\fscx108\\fscy108), others white.
    Supports both 1:1 square (1080x1080, margin_v=120) and 9:16 vertical (1080x1920, margin_v=280).
    """
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    phrases = group_words_into_cinerush_phrases(words, max_words_per_phrase=4)

    # Adapt resolution & vertical margin for layout profile
    if profile == "cinerush_style" or profile == "square":
        play_res_x = 1080
        play_res_y = 1080
        if margin_v == 280:
            margin_v = 120
    elif profile == "landscape":
        play_res_x = 1920
        play_res_y = 1080
        if margin_v == 280:
            margin_v = 100

    # PrimaryColour: &H00FFFFFF (White)
    # Highlight / Active: &H0000FFFF or &H00FFFF (Yellow in ASS BGR syntax: Blue=00, Green=FF, Red=FF)
    # OutlineColour: &H00000000 (Black)
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cinerush,{font_name},70,&H00FFFFFF&,&H0000FFFF&,&H00000000&,&H80000000,-1,0,0,0,100,100,1,0,1,5,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_lines: list[str] = []

    for phrase in phrases:
        if not phrase:
            continue

        for active_idx, active_word in enumerate(phrase):
            w_start = format_ass_time(active_word["start"])
            w_end = format_ass_time(active_word["end"])

            # If word duration is too small, give minimum visibility (0.15s)
            if active_word["end"] - active_word["start"] < 0.12:
                w_end = format_ass_time(active_word["start"] + 0.15)

            # Build line text where active word is highlighted in yellow & slightly scaled up
            tokens: list[str] = []
            for idx, w in enumerate(phrase):
                clean_w = str(w.get("word", "")).strip()
                if idx == active_idx:
                    # Active word: Yellow, slightly scaled up
                    tokens.append(r"{\c&H00FFFF&\fscx108\fscy108}" + clean_w + r"{\r}")
                else:
                    # Inactive word: White
                    tokens.append(r"{\c&HFFFFFF&\fscx100\fscy100}" + clean_w + r"{\r}")

            card_text = " ".join(tokens)
            dialogue_lines.append(f"Dialogue: 0,{w_start},{w_end},Cinerush,,0,0,0,,{card_text}")

    full_ass = ass_header + "\n".join(dialogue_lines) + "\n"
    output_ass_path.write_text(full_ass, encoding="utf-8")
    log(f"📝 Generated Cinerush karaoke subtitles: {len(dialogue_lines)} events -> {output_ass_path.name}")
    return output_ass_path


def burn_karaoke_subtitles(
    input_video: Path,
    subtitles_ass: Path,
    output_video: Path,
) -> Path:
    """
    Burns .ass subtitles into 1080x1920 video with FFmpeg.
    Uses fontsdir if local Montserrat font is available.
    """
    output_video.parent.mkdir(parents=True, exist_ok=True)
    ass_path_str = str(subtitles_ass.resolve()).replace("\\", "/")

    # Use fontsdir if Montserrat is in assets/fonts/
    if FONTS_DIR.exists() and list(FONTS_DIR.glob("*.ttf")):
        fontsdir_arg = f":fontsdir='{FONTS_DIR.resolve()}'"
    else:
        fontsdir_arg = ""

    vf = f"ass='{ass_path_str}'{fontsdir_arg}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "copy",
        str(output_video),
    ]

    log(f"🔥 Burning dynamic karaoke subtitles into video...")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode != 0:
        # Fallback if fontsdir failed
        cmd_fallback = [
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-vf", f"ass='{ass_path_str}'",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "copy",
            str(output_video),
        ]
        res_fb = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res_fb.returncode != 0:
            raise RuntimeError(f"FFmpeg subtitle burn failed: {res.stderr[:300]}")

    log(f"🎉 Final Cinerush Short rendered with burned karaoke subtitles: {output_video.name}")
    return output_video
