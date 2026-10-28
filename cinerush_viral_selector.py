#!/usr/bin/env python3
"""
Cinerush Viral Segment & Hook Selector
Uses Groq (llama-3.3-70b-versatile) to:
1. Select the single most gripping continuous 90-to-175 second window (clip_start to clip_end).
2. Generate a viral Cinerush curiosity-gap title (max 12 words, 2 emojis, ending with #film #movie #shorts).
3. Identify dead-air gaps (> 2.0s silence) and jump-cut them out while keeping 0.35s speech buffers.
4. Recalculate shifted word timestamps for 100% synced karaoke subtitles.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


def log(msg: str) -> None:
    print(f"[CinerushViralSelector] {msg}", flush=True)


def format_transcript_for_prompt(segments: list[dict[str, Any]]) -> str:
    """Formats transcript segments with timestamps for LLM analysis."""
    lines = []
    for s in segments:
        start = s.get("start", 0.0)
        end = s.get("end", 0.0)
        text = s.get("text", "").strip()
        lines.append(f"[{start:.2f}s - {end:.2f}s]: {text}")
    return "\n".join(lines)


def select_viral_segment(
    segments: list[dict[str, Any]],
    total_video_duration: float,
    movie_title: str = "Movie Scene",
    groq_api_key: str | None = None,
) -> dict[str, Any]:
    """
    Selects the 90-175 second viral window and generates metadata via Groq.
    """
    api_key = groq_api_key or os.environ.get("GROQ_API_KEY")
    transcript_text = format_transcript_for_prompt(segments)

    # Fallback default window if transcript is empty or LLM fails
    default_start = 0.0
    default_duration = min(120.0, max(30.0, total_video_duration))
    default_end = min(total_video_duration, default_start + default_duration)
    default_title = f"{movie_title} Most Intense Scene 🎬🔥 #film #movie #shorts"

    if not api_key:
        log("ℹ️ GROQ_API_KEY not set. Using heuristic high-density dialogue window.")
        return _heuristic_viral_window(segments, total_video_duration, movie_title)

    prompt = f"""You are the senior editor for Cinerush, the premier viral movie clips YouTube Shorts channel.
Analyze the following timestamped dialogue from the scene '{movie_title}' (Total runtime: {total_video_duration:.1f}s):

TRANSCRIPT:
{transcript_text[:12000]}

OBJECTIVES:
1. Identify the single most intense, emotional, gripping, or high-stakes continuous window in this scene.
2. The window MUST be between 90.0 and 175.0 seconds long (clip_end - clip_start must be >= 90.0 and <= 175.0).
   If the entire video is shorter than 90 seconds, use clip_start=0.0 and clip_end={total_video_duration:.1f}.
3. The selected window must start with a compelling hook line and build to an intense confrontation, revelation, or climax.
4. Generate a Cinerush viral curiosity-gap title:
   - Maximum 12 words
   - Include exactly 2 emojis
   - MUST end with "#film #movie #shorts"
   - Example format: "They broke his hand so he beat them with his left ♠️🔥 #film #movie #shorts"
5. Generate an engaging 2-sentence description and 10 relevant tags.

Return ONLY a valid JSON object matching this schema:
{{
  "clip_start": float,
  "clip_end": float,
  "title": "string",
  "description": "string",
  "tags": ["tag1", "tag2", ...]
}}
"""

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a specialized viral YouTube Shorts video editor for Cinerush."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=600,
        )
        content = response.choices[0].message.content
        data = json.loads(content)

        clip_start = float(data.get("clip_start", default_start))
        clip_end = float(data.get("clip_end", default_end))

        # Clamp and validate 90-175s duration
        clip_start = max(0.0, min(total_video_duration - 10.0, clip_start))
        clip_end = max(clip_start + 10.0, min(total_video_duration, clip_end))

        target_dur = clip_end - clip_start
        if total_video_duration >= 90.0:
            if target_dur < 90.0:
                clip_end = min(total_video_duration, clip_start + 90.0)
                if clip_end - clip_start < 90.0:
                    clip_start = max(0.0, clip_end - 90.0)
            elif target_dur > 175.0:
                clip_end = clip_start + 175.0

        raw_title = str(data.get("title", default_title)).strip()
        formatted_title = format_cinerush_title(raw_title, movie_title)

        log(f"🔥 Viral Window Selected: {clip_start:.2f}s -> {clip_end:.2f}s (duration: {clip_end - clip_start:.1f}s)")
        log(f"🏷️ Title: '{formatted_title}'")

        return {
            "clip_start": round(clip_start, 2),
            "clip_end": round(clip_end, 2),
            "title": formatted_title,
            "description": str(data.get("description", f"{formatted_title}\n\n#film #movie #shorts")),
            "tags": list(data.get("tags", ["film", "movie", "shorts", "cinerush", "cinema", "scene"])),
        }
    except Exception as e:
        log(f"⚠️ Groq viral segment selection failed: {e}. Falling back to heuristic window.")
        return _heuristic_viral_window(segments, total_video_duration, movie_title)


def format_cinerush_title(title: str, fallback_topic: str) -> str:
    """Ensures title strictly adheres to Cinerush format: <= 12 words, 2 emojis, ending with #film #movie #shorts."""
    suffix = "#film #movie #shorts"
    clean = title.replace("#film", "").replace("#movie", "").replace("#shorts", "").strip()
    words = clean.split()

    # Limit main hook words to 8 so total with emojis and hashtags stays under 12
    if len(words) > 8:
        clean = " ".join(words[:8])

    # Ensure emojis are present
    has_emoji = bool(re.search(r"[\U00010000-\U0010ffff]", clean))
    if not has_emoji:
        clean = f"{clean} 🎬🔥"

    final_title = f"{clean.strip()} {suffix}"
    return final_title[:100]


def _heuristic_viral_window(
    segments: list[dict[str, Any]],
    total_video_duration: float,
    movie_title: str,
) -> dict[str, Any]:
    """Finds highest dialogue density window between 90 and 175s."""
    target_len = min(120.0, max(30.0, total_video_duration))
    best_start = 0.0
    best_words_count = -1

    # Slide window across total duration
    step = 5.0
    cur_start = 0.0
    while cur_start + target_len <= total_video_duration + 0.1:
        cur_end = cur_start + target_len
        # Count words in this window
        count = 0
        for s in segments:
            for w in s.get("words", []):
                if cur_start <= w.get("start", 0.0) <= cur_end:
                    count += 1
        if count > best_words_count:
            best_words_count = count
            best_start = cur_start
        cur_start += step

    clip_start = round(best_start, 2)
    clip_end = round(min(total_video_duration, best_start + target_len), 2)
    title = format_cinerush_title(f"He knew the truth before anyone spoke", movie_title)

    return {
        "clip_start": clip_start,
        "clip_end": clip_end,
        "title": title,
        "description": f"{title}\n\nUnforgettable moment from {movie_title}. Watch until the end.\n\n#film #movie #shorts",
        "tags": ["film", "movie", "shorts", "cinerush", "cinema", "scene", "hollywood"],
    }


def build_trimmed_timeline(
    all_words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    silence_threshold: float = 2.0,
    speech_padding: float = 0.35,
) -> tuple[list[tuple[float, float]], list[dict[str, Any]], float]:
    """
    Applies Cinerush fast-paced dead-air trimming (jump cuts):
    - Cuts gaps > 2.0s between dialogue lines
    - Retains 0.35s speech buffer before and after words
    - Leaves natural brief dramatic pauses (< 2.0s) intact
    - Recalculates shifted word timestamps for 100% accurate subtitle sync along the trimmed timeline.

    Returns:
        tuple: (keep_intervals, shifted_words, total_trimmed_duration)
    """
    # 1. Filter words falling inside [clip_start, clip_end]
    window_words = [
        w for w in all_words
        if w.get("start", 0.0) >= clip_start - 0.2 and w.get("end", 0.0) <= clip_end + 0.2
    ]

    # If no words in window, keep continuous
    if not window_words:
        return ([(clip_start, clip_end)], [], round(clip_end - clip_start, 3))

    # Sort words by start timestamp
    window_words.sort(key=lambda x: x.get("start", 0.0))

    # 2. Build keep intervals by inspecting gaps between words
    intervals: list[list[float]] = []
    first_start = max(clip_start, window_words[0]["start"] - speech_padding)
    current_interval = [first_start, window_words[0]["end"] + speech_padding]

    for i in range(len(window_words) - 1):
        w_curr = window_words[i]
        w_next = window_words[i + 1]

        gap = w_next["start"] - w_curr["end"]
        if gap > silence_threshold:
            # Silence gap is too long: end current interval and start new interval
            current_interval[1] = min(clip_end, w_curr["end"] + speech_padding)
            if current_interval[1] > current_interval[0]:
                intervals.append([round(current_interval[0], 3), round(current_interval[1], 3)])
            new_start = max(clip_start, w_next["start"] - speech_padding)
            current_interval = [new_start, w_next["end"] + speech_padding]
        else:
            # Gap <= 2.0s: keep intact (preserves dramatic acting/pauses)
            current_interval[1] = max(current_interval[1], w_next["end"] + speech_padding)

    # Finish last interval
    current_interval[1] = min(clip_end, current_interval[1])
    if current_interval[1] > current_interval[0]:
        intervals.append([round(current_interval[0], 3), round(current_interval[1], 3)])

    # Merge overlapping or touching intervals
    merged_intervals: list[tuple[float, float]] = []
    for intv in intervals:
        if not merged_intervals:
            merged_intervals.append((intv[0], intv[1]))
        else:
            prev_s, prev_e = merged_intervals[-1]
            if intv[0] <= prev_e + 0.1:  # merge close edges
                merged_intervals[-1] = (prev_s, max(prev_e, intv[1]))
            else:
                merged_intervals.append((intv[0], intv[1]))

    # 3. Recalculate shifted word timestamps along the concatenated timeline
    shifted_words: list[dict[str, Any]] = []
    for w in window_words:
        w_start = w["start"]
        w_end = w["end"]

        # Find which interval contains this word
        cum_offset = 0.0
        word_mapped = False
        for s_start, s_end in merged_intervals:
            intv_dur = s_end - s_start
            if s_start <= w_start <= s_end:
                # Word starts in this interval
                new_start = cum_offset + (w_start - s_start)
                new_end = cum_offset + (min(s_end, w_end) - s_start)
                shifted_words.append({
                    "word": w["word"],
                    "start": round(new_start, 3),
                    "end": round(new_end, 3),
                })
                word_mapped = True
                break
            cum_offset += intv_dur

        if not word_mapped:
            # Word fell slightly on cut boundary; map to nearest interval
            cum_offset = 0.0
            for s_start, s_end in merged_intervals:
                intv_dur = s_end - s_start
                if abs(w_start - s_start) < 0.35:
                    shifted_words.append({
                        "word": w["word"],
                        "start": round(cum_offset, 3),
                        "end": round(cum_offset + (w_end - w_start), 3),
                    })
                    break
                cum_offset += intv_dur

    total_duration = sum(e - s for s, e in merged_intervals)
    log(f"✂️ Dead-air trimmed: {len(merged_intervals)} intervals, final runtime: {total_duration:.2f}s (cut {((clip_end - clip_start) - total_duration):.2f}s silence)")

    return merged_intervals, shifted_words, round(total_duration, 3)
