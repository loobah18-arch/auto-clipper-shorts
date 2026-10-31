#!/usr/bin/env python3
"""
Cinerush Karaoke Subtitles Generator (.ass)
Burns punchy, synced subtitles of actual movie dialogue matching viral Cinerush Shorts:
- Short readable chunks of 3 to 5 words (natural phrasing, lookahead anti-orphan protection)
- Typography: Bold Italic display font (Montserrat ExtraBold / DejaVu Serif, Bold -1, Italic -1)
- Casing: High-impact ALL CAPS dialogue
- Vertical Lift: MarginV 240 for 1:1 square (lower third ~78%, completely clear of faces/action),
  MarginV 420 for 9:16 vertical, MarginV 120 for landscape
- Color Palette: Cinematic Golden Yellow base (&H0000FFFF&) with Vibrant Magenta/Pink active spoken
  word highlight (&H00FF00FF&) with subtle anti-aliased edge glow (\\blur1.2) and punchy scale (\\fscx106\\fscy106)
- Outline: 3.5 (Deep Black), Shadow: 1.5, BorderStyle: 1
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[CinerushSubtitles] {msg}", flush=True)


FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

STRONG_PUNCT = re.compile(r"[\.!\?]$|--$|—$")
SOFT_PUNCT = re.compile(r"[,;:]$")
FILLER_WORDS = {"um", "uh", "ah", "er", "mm", "hmm"}


def format_ass_time(seconds: float) -> str:
    """Converts seconds into ASS time format: H:MM:SS.cs"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    total_cs = int(round(seconds * 100))
    h, remainder = divmod(total_cs, 360000)
    m, remainder = divmod(remainder, 6000)
    s, cs = divmod(remainder, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def normalize_word_timings(raw_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize Whisper timings so ASS events are ordered, finite, and non-overlapping."""
    normalized: list[dict[str, Any]] = []
    for raw in raw_words:
        text = str(raw.get("word", "")).strip()
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError):
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, start)
        end = max(start, end)
        normalized.append({**raw, "word": text, "start": start, "end": end})
    normalized.sort(key=lambda word: (word["start"], word["end"]))
    for index in range(1, len(normalized)):
        normalized[index]["start"] = max(normalized[index]["start"], normalized[index - 1]["start"])
        normalized[index - 1]["end"] = min(normalized[index - 1]["end"], normalized[index]["start"]) if normalized[index]["start"] < normalized[index - 1]["end"] else normalized[index - 1]["end"]
    return [word for word in normalized if word["end"] > word["start"] or word["word"]]


def _clean_word_tokens(raw_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strips whitespace, cleans orphan hyphens, and filters standalone filler tokens."""
    cleaned: list[dict[str, Any]] = []
    for w in raw_words:
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue

        # Filter out standalone filler tokens (e.g. 'um', 'uh')
        base_alpha = txt.lower().strip(".,?!:;-\"'")
        if base_alpha in FILLER_WORDS and len(txt) <= 3:
            continue

        # Skip orphan hyphens
        if txt in {"-", "--", "—"}:
            continue

        cleaned.append(w)
    return cleaned


def _split_cluster_into_phrases(
    cluster: list[dict[str, Any]],
    max_words: int = 4,
) -> list[list[dict[str, Any]]]:
    """
    Recursively splits a contiguous speech cluster into balanced 3 to 5 word phrases.
    Guarantees that no 1-word orphan phrase is created.
    """
    n = len(cluster)
    if n == 0:
        return []

    # If cluster has up to 5 words, keep as a single unified card
    if n <= max_words + 1:
        return [cluster]

    # Look for a natural mid-sentence punctuation break (comma, semicolon)
    # that leaves at least 2 words on both sides
    for idx in range(2, n - 2):
        word_text = str(cluster[idx].get("word", "")).strip()
        if SOFT_PUNCT.search(word_text) and (idx + 1) <= 5 and (n - (idx + 1)) <= 5:
            return [cluster[: idx + 1]] + _split_cluster_into_phrases(cluster[idx + 1 :], max_words)

    # Balance chunk size to avoid trailing 1-word orphan
    if n % max_words == 1:
        chunk_size = 3
    else:
        chunk_size = max_words

    return [cluster[:chunk_size]] + _split_cluster_into_phrases(cluster[chunk_size:], max_words)


def group_words_into_cinerush_phrases(
    words: list[dict[str, Any]],
    max_words_per_phrase: int = 4,
    pause_threshold: float = 0.40,
) -> list[list[dict[str, Any]]]:
    """
    Groups spoken words into 3 to 5 word punchy, readable phrases:
    - Filters filler tokens and orphan punctuation
    - Breaks on dialogue pauses (> pause_threshold) and terminal sentence marks (. ? ! --)
    - Enforces lookahead anti-orphan balancing: never leaves a single trailing word
    """
    cleaned = _clean_word_tokens(normalize_word_timings(words))
    if not cleaned:
        return []

    # Partition into speech clusters by pause or strong sentence terminal punctuation
    clusters: list[list[dict[str, Any]]] = []
    current_cluster: list[dict[str, Any]] = []

    for i, w in enumerate(cleaned):
        current_cluster.append(w)
        word_text = str(w.get("word", "")).strip()

        is_strong_punct = bool(STRONG_PUNCT.search(word_text))
        is_pause = False
        if i < len(cleaned) - 1:
            next_start = float(cleaned[i + 1].get("start", 0.0))
            curr_end = float(w.get("end", 0.0))
            if next_start - curr_end > pause_threshold:
                is_pause = True

        if is_strong_punct or is_pause:
            clusters.append(current_cluster)
            current_cluster = []

    if current_cluster:
        clusters.append(current_cluster)

    # Split each cluster into balanced phrases
    all_phrases: list[list[dict[str, Any]]] = []
    for c in clusters:
        phrases = _split_cluster_into_phrases(c, max_words=max_words_per_phrase)
        all_phrases.extend(phrases)

    # Post-pass: merge any trailing 1-word orphan if adjacent to previous phrase within 0.8s
    final_phrases: list[list[dict[str, Any]]] = []
    for p in all_phrases:
        if len(p) == 1 and final_phrases:
            prev = final_phrases[-1]
            gap = float(p[0].get("start", 0.0)) - float(prev[-1].get("end", 0.0))
            if gap <= 0.80 and len(prev) < 6:
                prev.extend(p)
                continue
        final_phrases.append(p)

    return final_phrases


def generate_cinerush_karaoke_ass(
    words: list[dict[str, Any]],
    output_ass_path: Path,
    font_name: str = "Montserrat ExtraBold",
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    margin_v: int = 240,
    profile: str | None = None,
    highlight_color: str = "&H00FF00FF&",  # Vibrant Magenta / Pink active spoken word
    base_color: str = "&H0000FFFF&",       # Golden Yellow base text
) -> Path:
    """
    Generates ASS subtitle file with cinematic word-by-word karaoke highlighting:
    - Uppercase bold italic typography
    - Active word is vibrant magenta/pink (\\c&H00FF00FF&\\fscx106\\fscy106\\blur1.2)
    - Inactive base words are golden yellow (\\c&H0000FFFF&)
    - MarginV is placed in the lower third to keep video action and faces completely visible:
        - 1:1 square profile: MarginV 240 (~22% from bottom, baseline at y=840)
        - 9:16 vertical profile: MarginV 420 (~22% from bottom, baseline at y=1500)
        - Landscape profile: MarginV 120
    """
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    phrases = group_words_into_cinerush_phrases(words, max_words_per_phrase=4)

    # Adapt resolution & vertical margin for layout profile
    if profile == "cinerush_style" or profile == "square":
        play_res_x = 1080
        play_res_y = 1080
        if margin_v == 240:
            margin_v = 240
    elif profile == "landscape":
        play_res_x = 1920
        play_res_y = 1080
        if margin_v == 240:
            margin_v = 120
    else:
        # 9:16 vertical (1080x1920)
        play_res_x = 1080
        play_res_y = 1920
        if margin_v == 240:
            margin_v = 420

    # ASS Header with Bold -1, Italic -1, Fontsize 64, Outline 3.5, Shadow 1.5
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cinerush,{font_name},64,{base_color},{highlight_color},&H00000000&,&H80000000,-1,-1,0,0,100,100,1,0,1,3.5,1.5,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_lines: list[str] = []

    for phrase_idx, phrase in enumerate(phrases):
        if not phrase:
            continue

        for active_idx, active_word in enumerate(phrase):
            w_start_sec = max(0.0, float(active_word.get("start", 0.0)))
            w_end_sec = max(w_start_sec + 0.08, float(active_word.get("end", 0.0)))

            # Seamless within-phrase continuity to eliminate micro-flicker between words
            if active_idx < len(phrase) - 1:
                next_start = float(phrase[active_idx + 1].get("start", 0.0))
                if next_start > w_start_sec and w_end_sec < next_start and (next_start - w_end_sec) <= 0.12:
                    w_end_sec = next_start
            else:
                # Keep the final word readable without extending far beyond speech.
                w_end_sec = max(w_end_sec, w_start_sec + 0.12)
                # If next phrase starts very soon (<= 0.25s), extend to avoid a 50ms dark blink
                if phrase_idx < len(phrases) - 1 and phrases[phrase_idx + 1]:
                    next_phrase_start = float(phrases[phrase_idx + 1][0].get("start", 0.0))
                    if next_phrase_start > w_start_sec and (next_phrase_start - w_end_sec) <= 0.25:
                        w_end_sec = max(w_end_sec, next_phrase_start)

            w_start = format_ass_time(w_start_sec)
            w_end = format_ass_time(w_end_sec)

            # Build line text: all uppercase, active word highlighted with scale pop and blur glow
            tokens: list[str] = []
            for idx, w in enumerate(phrase):
                clean_w = str(w.get("word", "")).strip().upper()
                # Sanitize ASS special characters
                clean_w = clean_w.replace("{", "(").replace("}", ")").replace("\\", "/")

                if idx == active_idx:
                    # Active spoken word: Magenta/Pink highlight with subtle glow and 6% scale
                    tokens.append(rf"{{\c{highlight_color}\fscx106\fscy106\blur1.2}}{clean_w}{{\r}}")
                else:
                    # Inactive words: Golden Yellow base
                    tokens.append(rf"{{\c{base_color}\fscx100\fscy100}}{clean_w}{{\r}}")

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
    Burns .ass subtitles into video with FFmpeg.
    Uses fontsdir if local Montserrat font is available in assets/fonts/.
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

    log("🔥 Burning dynamic karaoke subtitles into video...")
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
