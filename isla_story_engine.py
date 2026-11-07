#!/usr/bin/env python3
"""
Isla Looped Arc Story Engine.
Transforms long-form creator transcripts into high-retention 45-55s YouTube Shorts
following Jack Craig's 3 viral criteria:
1. Universal Relatability (accessible to any casual viewer)
2. Looped Arc Structure (Hook -> Rising Action -> Conflict -> Comeback -> Payoff & Loop)
3. Audio Integration (Voiceover + Original Creator reaction cut-ins + Dynamic silence)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional


def log(msg: str) -> None:
    print(f"[IslaStoryEngine] {msg}", flush=True)


@dataclass
class AudioCutSegment:
    start_sec: float
    end_sec: float
    speaker: str  # "narrator" or "creator"
    text: str
    bgm_action: str  # "normal", "ducked", "silence"


@dataclass
class LoopedArcScript:
    title: str
    concept: str
    hook_text: str
    loop_connector: str
    segments: list[AudioCutSegment]
    raw_clip_start: float
    raw_clip_end: float
    estimated_duration: float
    keywords: list[str]


SYSTEM_PROMPT = """You are an elite YouTube Shorts viral retention editor trained on the Jack Craig & Isla framework.
You transform long-form creator videos (science experiments, tech builds, high-stakes stories) into 45-55 second viral Shorts.

STRICT EDITING RULES:
1. UNIVERSAL RELATABILITY: Focus on the most relatable, tangible human element (food, danger, money, curiosity, failure).
2. THE LOOPED ARC STRUCTURE:
   - BEAT 1 (0-3s): OPEN-LOOP HOOK. Must plant an urgent, unanswered question. NEVER give away the answer or ending!
     Example: "What would actually happen if you boiled 100 cans of Red Bull into solid powder?" (NOT "He made Red Bull powder").
   - BEAT 2 (3-15s): RISING ACTION. Rapid process, building curiosity.
   - BEAT 3 (15-30s): CONFLICT / ROADBLOCK. Something goes wrong, fails, smokes, or creates genuine danger.
   - BEAT 4 (30-42s): COMEBACK. The creator's counter-intuitive pivot or clever fix.
   - BEAT 5 (42-52s): PAYOFF & SEAMLESS LOOP. The satisfying result, ending with a sentence that naturally loops directly back into the opening hook word!
3. AUDIO DYNAMICS:
   - Alternate between crisp third-person narrator voiceover and authentic CREATOR REACTION sound bites (e.g. screams, gasps, realization).
   - Mark bgm_action: "silence" right before the climax or conflict revelation to create intense auditory drama.

OUTPUT FORMAT:
Return strictly a valid JSON object matching this schema:
{
  "title": "Short title with curiosity hook and #shorts",
  "concept": "1-sentence summary of the core loop",
  "hook_text": "Opening hook sentence (0-3s)",
  "loop_connector": "Ending phrase that connects back into hook_text",
  "raw_clip_start": 120.0,
  "raw_clip_end": 175.0,
  "segments": [
    {
      "start_sec": 0.0,
      "end_sec": 3.5,
      "speaker": "narrator",
      "text": "Opening open-loop question...",
      "bgm_action": "normal"
    },
    {
      "start_sec": 3.5,
      "end_sec": 12.0,
      "speaker": "narrator",
      "text": "The setup begins...",
      "bgm_action": "normal"
    },
    {
      "start_sec": 12.0,
      "end_sec": 16.0,
      "speaker": "creator",
      "text": "Creator original dialogue reaction",
      "bgm_action": "ducked"
    },
    {
      "start_sec": 16.0,
      "end_sec": 28.0,
      "speaker": "narrator",
      "text": "Suddenly, disaster strikes...",
      "bgm_action": "silence"
    },
    {
      "start_sec": 28.0,
      "end_sec": 42.0,
      "speaker": "narrator",
      "text": "The genius comeback solution...",
      "bgm_action": "normal"
    },
    {
      "start_sec": 42.0,
      "end_sec": 50.0,
      "speaker": "narrator",
      "text": "The final astonishing result, which is why everyone wonders...",
      "bgm_action": "normal"
    }
  ],
  "keywords": ["key1", "key2", "key3"]
}
"""


def _call_groq_llm(prompt: str, api_key: str) -> Optional[str]:
    """Queries Groq API (llama-3.3-70b-versatile)."""
    try:
        import urllib.request
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Antigravity/1.0",
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.6,
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"⚠️ Groq LLM query failed: {e}")
        return None


def _call_openrouter_llm(prompt: str, api_key: str) -> Optional[str]:
    """Queries OpenRouter API (nemotron / free models)."""
    try:
        import urllib.request
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/loobah18-arch/auto-clipper-shorts",
            "X-Title": "Isla Clipper",
        }
        payload = {
            "model": "nvidia/nemotron-3-super-120b-a12b:free",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.6,
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # Clean possible markdown fencing
            match = re.search(r"\{.*\}", content, re.DOTALL)
            return match.group(0) if match else content
    except Exception as e:
        log(f"⚠️ OpenRouter LLM query failed: {e}")
        return None


def generate_deterministic_fallback_script(
    title: str,
    channel_name: str,
    duration_sec: float,
) -> LoopedArcScript:
    """Deterministic fallback script matching the Looped Arc criteria."""
    start_time = max(10.0, min(120.0, duration_sec * 0.2))
    end_time = min(duration_sec, start_time + 50.0)

    hook = f"Have you ever wondered what actually happens when {channel_name} pushes an experiment to its absolute limit?"
    loop = "And that is exactly why everyone still asks..."

    segments = [
        AudioCutSegment(
            start_sec=0.0, end_sec=3.5, speaker="narrator",
            text=hook, bgm_action="normal"
        ),
        AudioCutSegment(
            start_sec=3.5, end_sec=14.0, speaker="narrator",
            text="The setup looked completely straightforward at first, but things escalated instantly.",
            bgm_action="normal"
        ),
        AudioCutSegment(
            start_sec=14.0, end_sec=18.0, speaker="creator",
            text="Wait, that's not supposed to happen at all!",
            bgm_action="ducked"
        ),
        AudioCutSegment(
            start_sec=18.0, end_sec=32.0, speaker="narrator",
            text="Suddenly, an unexpected chemical reaction completely threatened to destroy the entire apparatus.",
            bgm_action="silence"
        ),
        AudioCutSegment(
            start_sec=32.0, end_sec=44.0, speaker="narrator",
            text="Instead of quitting, a counter-intuitive workaround was deployed to rescue the process.",
            bgm_action="normal"
        ),
        AudioCutSegment(
            start_sec=44.0, end_sec=51.0, speaker="narrator",
            text=f"The final transformation succeeded beyond expectation. {loop}",
            bgm_action="normal"
        ),
    ]

    return LoopedArcScript(
        title=f"What happens when you push science too far? 🧪🔥 #shorts #science",
        concept="High-stakes experiment pushed to the brink and salvaged with a genius pivot.",
        hook_text=hook,
        loop_connector=loop,
        segments=segments,
        raw_clip_start=start_time,
        raw_clip_end=end_time,
        estimated_duration=51.0,
        keywords=["experiment", "science", "insane", "reaction", "breakthrough"],
    )


def generate_looped_arc_script(
    video_title: str,
    channel_name: str,
    transcript_text: str,
    video_duration: float,
) -> LoopedArcScript:
    """Generates an Isla-style Looped Arc script using LLM with deterministic fallback."""
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    prompt = f"""VIDEO INFORMATION:
Channel: {channel_name}
Title: {video_title}
Total Duration: {video_duration:.1f}s

TRANSCRIPT EXCERPT:
{transcript_text[:3500]}

Generate the viral 45-55 second Looped Arc Short script following all rules.
Ensure raw_clip_start and raw_clip_end span an exciting 45-55s continuous window from the video.
"""

    raw_json = None
    if groq_key:
        log("🧠 Generating Looped Arc script via Groq...")
        raw_json = _call_groq_llm(prompt, groq_key)

    if not raw_json and openrouter_key:
        log("🧠 Generating Looped Arc script via OpenRouter...")
        raw_json = _call_openrouter_llm(prompt, openrouter_key)

    if raw_json:
        try:
            data = json.loads(raw_json)
            segments = [
                AudioCutSegment(
                    start_sec=float(s.get("start_sec", 0.0)),
                    end_sec=float(s.get("end_sec", 0.0)),
                    speaker=s.get("speaker", "narrator"),
                    text=s.get("text", ""),
                    bgm_action=s.get("bgm_action", "normal"),
                )
                for s in data.get("segments", [])
            ]
            clip_start = float(data.get("raw_clip_start") or 10.0)
            clip_end = float(data.get("raw_clip_end") or min(video_duration, clip_start + 50.0))
            duration = sum(s.end_sec - s.start_sec for s in segments) or (clip_end - clip_start)

            return LoopedArcScript(
                title=data.get("title", f"What happens next? 🎬🔥 #shorts"),
                concept=data.get("concept", "Viral looped arc"),
                hook_text=data.get("hook_text", ""),
                loop_connector=data.get("loop_connector", ""),
                segments=segments,
                raw_clip_start=clip_start,
                raw_clip_end=clip_end,
                estimated_duration=duration,
                keywords=data.get("keywords", ["viral", "shorts"]),
            )
        except Exception as e:
            log(f"⚠️ Failed to parse LLM JSON ({e}); utilizing deterministic Looped Arc generator.")

    log("ℹ️ Falling back to deterministic Looped Arc script generator.")
    return generate_deterministic_fallback_script(video_title, channel_name, video_duration)
