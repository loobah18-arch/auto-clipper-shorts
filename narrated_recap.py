#!/usr/bin/env python3
"""
Narrated Recap Renderer (@TestedNProved format)
================================================

Turns one long source video into a ~60 second narrated vertical Short:

  1. Transcribe the source audio (Groq Whisper, free tier; local faster-whisper fallback).
  2. An LLM writes a ~60s third-person voiceover script in the reference
     channel's beat structure (question hook -> goal -> failure -> twist ->
     final test -> payoff), and for every line points at the timestamp range in
     the source where that moment is shown. It also writes the title hook and
     the upload description.
  3. Each line is voiced with edge-tts, so the timing of every line is exact.
  4. Footage for each line is cut from the source window the script named,
     cropped to 1080x1080 (square), with the original audio muted.
  5. Voiceover + ducked background music + captions synced to the voice.

Free providers, in order:
  * GROQ_API_KEY       -> whisper-large-v3-turbo (transcript) + gpt-oss-120b / qwen (script)
  * OPENROUTER_API_KEY -> free model for the script (RECAP_OPENROUTER_MODEL to override)

Exit code 0 = Short rendered; anything else = caller should fall back to the
older hypit_engine render.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent
FONTS_DIR = ROOT_DIR / "assets" / "fonts"
BGM_DIR = ROOT_DIR / "assets" / "bgm"

# Square by default (user's choice, 2026-11-14). RECAP_ASPECT=9:16 switches to full vertical.
_ASPECTS = {"1:1": (1080, 1080), "9:16": (1080, 1920), "4:5": (1080, 1350)}
OUT_W, OUT_H = _ASPECTS.get(os.environ.get("RECAP_ASPECT", "1:1"), (1080, 1080))
ASPECT_LABEL = next((k for k, v in _ASPECTS.items() if v == (OUT_W, OUT_H)), "1:1")
FPS = 30
MAX_SECONDS = 58.0          # Shorts must stay under 60s
TARGET_WORDS = (150, 185)   # reference channel runs ~190-240 words per minute of narration
MAX_TRANSCRIPT_WORDS = 4500  # keeps the prompt inside Groq's free-tier token budget
SUBCLIP_SECONDS = 2.6       # fast cutting: a new shot roughly every 2.6s

GROQ_BASE = "https://api.groq.com/openai/v1"
# Groq retires models often, so try a preference list (RECAP_GROQ_MODEL first when set)
GROQ_CHAT_MODELS = [m for m in [os.environ.get("RECAP_GROQ_MODEL", "").strip(),
                    "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile",
                    "openai/gpt-oss-20b"] if m]
GROQ_WHISPER_MODEL = os.environ.get("RECAP_WHISPER_MODEL", "whisper-large-v3-turbo")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.environ.get("RECAP_OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")

DEFAULT_BGM = os.environ.get("RECAP_BGM", "main_attraction_phonk.mp3")
DEFAULT_VOICE = os.environ.get("RECAP_VOICE", "en-US-BrianMultilingualNeural")
BGM_VOLUME = float(os.environ.get("RECAP_BGM_VOLUME", "0.02"))
VOICE_VOLUME = float(os.environ.get("RECAP_VOICE_VOLUME", "1.6"))
ORIGINAL_AUDIO_VOLUME = float(os.environ.get("RECAP_ORIGINAL_AUDIO_VOLUME", "1.05"))


def log(msg: str) -> None:
    print(f"[recap] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------

def run(cmd: List[str]) -> None:
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{res.stderr[-1500:]}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise RuntimeError(f"could not read duration of {path}")


# ---------------------------------------------------------------------------
# 1. Transcription
# ---------------------------------------------------------------------------

def extract_audio(video: Path, out_dir: Path, chunk_seconds: int = 1200) -> List[Tuple[float, Path]]:
    """Low-bitrate mono mp3 chunks (each well under Groq's 25 MB upload cap)."""
    total = probe_duration(video)
    chunks: List[Tuple[float, Path]] = []
    start = 0.0
    idx = 0
    while start < total - 0.5:
        out = out_dir / f"audio_{idx:02d}.mp3"
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", str(chunk_seconds),
            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", str(out),
        ])
        chunks.append((start, out))
        start += chunk_seconds
        idx += 1
    return chunks


def _http_post(url: str, headers: Dict[str, str], **kwargs: Any) -> Dict[str, Any]:
    import requests

    for attempt in range(3):
        resp = requests.post(url, headers=headers, timeout=300, **kwargs)
        if resp.status_code == 429 and attempt < 2:
            wait = float(resp.headers.get("retry-after", "20") or 20)
            log(f"rate limited, waiting {wait:.0f}s")
            import time
            time.sleep(min(wait, 90))
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} from {url}: {resp.text[:400]}")
        return resp.json()
    raise RuntimeError(f"gave up on {url}")


def transcribe_groq(chunks: List[Tuple[float, Path]], api_key: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for offset, path in chunks:
        with open(path, "rb") as fh:
            data = _http_post(
                f"{GROQ_BASE}/audio/transcriptions",
                {"Authorization": f"Bearer {api_key}"},
                files={"file": (path.name, fh, "audio/mpeg")},
                data={"model": GROQ_WHISPER_MODEL, "response_format": "verbose_json"},  # auto language (Ruhi Cenet is Turkish)
            )
        for seg in data.get("segments", []):
            text = (seg.get("text") or "").strip()
            if text:
                segments.append({
                    "start": round(float(seg["start"]) + offset, 2),
                    "end": round(float(seg["end"]) + offset, 2),
                    "text": text,
                })
    return segments


def transcribe_local(chunks: List[Tuple[float, Path]]) -> List[Dict[str, Any]]:
    from faster_whisper import WhisperModel  # optional dependency

    model = WhisperModel(os.environ.get("RECAP_LOCAL_WHISPER", "base.en"), device="cpu", compute_type="int8")
    segments: List[Dict[str, Any]] = []
    for offset, path in chunks:
        result, _info = model.transcribe(str(path), vad_filter=True)
        for seg in result:
            text = seg.text.strip()
            if text:
                segments.append({"start": round(seg.start + offset, 2), "end": round(seg.end + offset, 2), "text": text})
    return segments


def transcribe(video: Path, work: Path) -> List[Dict[str, Any]]:
    chunks = extract_audio(video, work)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            log("transcribing with Groq Whisper")
            segs = transcribe_groq(chunks, groq_key)
            if segs:
                return segs
        except Exception as exc:  # noqa: BLE001
            log(f"Groq transcription failed: {exc}")
    log("transcribing locally with faster-whisper")
    return transcribe_local(chunks)


# ---------------------------------------------------------------------------
# 2. Script writing
# ---------------------------------------------------------------------------

def condense_transcript(segments: List[Dict[str, Any]], block_seconds: float = 20.0,
                        max_words: int = MAX_TRANSCRIPT_WORDS) -> str:
    """Groups segments into ~20s blocks tagged with their start second, trimmed to a word budget."""
    blocks: List[Tuple[float, float, List[str]]] = []
    for seg in segments:
        if blocks and seg["start"] - blocks[-1][0] < block_seconds:
            start, _end, words = blocks[-1]
            blocks[-1] = (start, seg["end"], words + seg["text"].split())
        else:
            blocks.append((seg["start"], seg["end"], seg["text"].split()))

    total = sum(len(b[2]) for b in blocks) or 1
    ratio = min(1.0, max_words / total)
    lines = []
    for start, end, words in blocks:
        keep = max(6, int(len(words) * ratio)) if ratio < 1.0 else len(words)
        text = " ".join(words[:keep])
        if keep < len(words):
            text += " …"
        lines.append(f"[{int(start)}-{int(end)}] {text}")
    return "\n".join(lines)


SCRIPT_SYSTEM = """You write voiceovers for a YouTube Shorts channel that retells long YouTube videos (science experiments, challenges, engineering builds, stunts, documentaries) in under 60 seconds, in the exact style of the channel @TestedNProved.

CRITICAL TITLE HOOK RULE (MANDATORY):
- Line 1 MUST state the exact title of the video as the opening spoken line! NEVER start with "What would happen if...", "Can this guy...", or speculative questions. The narrator immediately states the title hook: e.g. "He compressed 100 Red Bulls into one drink." or "He stole a car in 15 seconds with a dark-web gadget."
- The title saying and the narration are voiced as ONE seamless continuous voiceover: Line 1 states the title hook, and Line 2 immediately jumps into the story without any weird cuts or disconnect.

STYLE (copy it closely):
- Third person, casual, energetic: "This guy...", "He...". Never "I" or "we".
- Line 1: The title of the video, spoken naturally as the opening sentence.
- Then: the goal ("the goal was simple: ..."), the method, a setback or failure, a twist ("But here's where it gets crazy."), the final test, and a punchy payoff/result in the last line.
- Each item in `lines` MUST be exactly one clear spoken sentence.
- Short spoken sentences, concrete numbers and details from the transcript, zero filler, no hashtags, no emojis, no stage directions.
- Only state things that actually happen in the transcript. Always write in English, even if the transcript is in another language.
- Keep the energy high: use at least one twist line like "But here's where it gets crazy." and end on a punchy payoff sentence.
- Hit the word count: aim for about 160-185 words total. Too short sounds rushed and flat.

EXAMPLE (another video):
"He compressed 100 cans of Red Bull into one drink. This guy bought 100 cans of sugar-free Red Bull and poured every single one into a giant bucket. That's 25 liters of energy drink, and the goal was simple: concentrate all of it into the size of one can. To do that, he used this insane machine..."

OUTPUT: strict JSON, no markdown:
{
  "title_hook": "He Compressed 100 RedBulls into 1 Drink",
  "description": "One or two plain sentences telling the story, like: This scientist took 100 cans of sugar-free Red Bull and ran them through a machine that removes all the water.",
  "lines": [
    {"text": "He compressed 100 cans of Red Bull into one drink.", "start": 0, "end": 15},
    {"text": "This guy bought 100 cans of sugar-free Red Bull and poured every single one into a giant bucket.", "start": 15, "end": 35}
  ]
}
- title_hook: starts with "He", "She", "They" or "This Guy", states the result as a completed act, Title Case, max 60 characters, no emoji, no @handle.
- lines[0]: MUST be the exact spoken version of title_hook.
- lines: 9 to 14 items, in story order, together """ + f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]}" + """ words. Each item is ONE complete spoken sentence.
- start/end: seconds in the SOURCE video (use the [start-end] tags of the transcript) where the footage for that line is shown. Each range should be 4-40 seconds long and inside the video. Ranges must move forward through the video (non-decreasing start), and the final line must point at the actual final result near the end."""


def build_prompt(source_title: str, creator_name: str, transcript: str, duration: float) -> str:
    return (
        f"SOURCE VIDEO: \"{source_title}\" by {creator_name} (length {int(duration)} seconds)\n\n"
        f"TIMESTAMPED TRANSCRIPT ([start-end] in seconds):\n{transcript}\n\n"
        "Write the JSON now."
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    # Reasoning models sometimes prefix <think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


def chat_completion(system: str, user: str) -> str:
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    errors = []
    if groq_key:
        for model in GROQ_CHAT_MODELS:
            try:
                data = _http_post(
                    f"{GROQ_BASE}/chat/completions",
                    {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": model, "temperature": 0.7, "max_completion_tokens": 6000,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    },
                )
                content = data["choices"][0]["message"].get("content") or ""
                if content.strip():
                    log(f"script written by groq:{model}")
                    return content
                errors.append(f"groq:{model}: empty reply")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"groq:{model}: {str(exc)[:200]}")
    if or_key:
        try:
            data = _http_post(
                OPENROUTER_URL,
                {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/loobah18-arch/auto-clipper-shorts"},
                json={
                    "model": OPENROUTER_MODEL, "temperature": 0.7, "max_tokens": 1500,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
            )
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"openrouter: {exc}")
    raise RuntimeError("no LLM produced a script: " + ("; ".join(errors) or "no GROQ_API_KEY / OPENROUTER_API_KEY set"))


_HOOK_SUBJECT_RE = re.compile(r"^(He|She|They|This Guy|This Man|This Scientist)\b", re.IGNORECASE)


def clean_title_hook(hook: Any) -> Optional[str]:
    """Accepts the LLM title hook only when it matches the channel's declarative form."""
    if not isinstance(hook, str):
        return None
    hook = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]", "", hook)
    hook = re.sub(r"\s*\(@[^)]*\)", "", hook)
    hook = re.sub(r"\s+", " ", hook).strip(" \"'.!-")
    if "@" in hook or "#" in hook or not (12 <= len(hook) <= 70):
        return None
    if not _HOOK_SUBJECT_RE.match(hook):
        return None
    return hook


def validate_script(raw: Dict[str, Any], source_duration: float) -> Dict[str, Any]:
    """Normalises the LLM script: clamps ranges, enforces order, drops junk lines."""
    lines_in = raw.get("lines") or []
    lines: List[Dict[str, Any]] = []
    last_start = 0.0
    for item in lines_in:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        text = re.sub(r"[\U00010000-\U0010ffff]", "", text).strip()
        if len(text.split()) < 2:
            continue
        try:
            start = float(item.get("start", last_start))
            end = float(item.get("end", start + 8))
        except (TypeError, ValueError):
            start, end = last_start, last_start + 8
        start = min(max(0.0, start), max(0.0, source_duration - 2.0))
        end = min(max(end, start + 2.0), source_duration)
        if start < last_start - 30:  # allow small backtracks, not wild jumps to the start
            start = last_start
            end = min(max(end, start + 4.0), source_duration)
        last_start = start
        lines.append({"text": text, "start": round(start, 2), "end": round(end, 2)})

    clean_hook = clean_title_hook(raw.get("title_hook"))
    if clean_hook and lines:
        first_text = lines[0].get("text", "")
        # Enforce that Line 1 speaks the title of the video instead of "what would happen"
        if not first_text or re.match(r"^(what\s+(would\s+)?happen|can\s+|is\s+it\s+possible|have\s+you\s+ever)", first_text, re.IGNORECASE):
            hook_sentence = clean_hook
            if not hook_sentence.endswith((".", "!", "?")):
                hook_sentence += "."
            lines[0]["text"] = hook_sentence

    # Ensure all lines end with punctuation for clean prosody and sentence separation
    for l in lines:
        t = l["text"].strip()
        if not t.endswith((".", "!", "?")):
            l["text"] = t + "."

    words = sum(len(l["text"].split()) for l in lines)
    if len(lines) < 5 or words < 80:
        raise ValueError(f"script too thin ({len(lines)} lines, {words} words)")

    description = re.sub(r"\s+", " ", str(raw.get("description", ""))).strip()
    description = re.sub(r"#\w+", "", description).strip()
    return {
        "title_hook": clean_hook,
        "description": description[:600],
        "lines": lines,
        "word_count": words,
    }


def write_script(segments: List[Dict[str, Any]], source_title: str, creator_name: str,
                 source_duration: float) -> Dict[str, Any]:
    transcript = condense_transcript(segments)
    prompt = build_prompt(source_title, creator_name, transcript, source_duration)
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            raw = _extract_json(chat_completion(SCRIPT_SYSTEM, prompt))
            return validate_script(raw, source_duration)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"script attempt {attempt + 1} rejected: {exc}")
    raise RuntimeError(f"could not get a usable script: {last_err}")


# ---------------------------------------------------------------------------
# 3. Voiceover
# ---------------------------------------------------------------------------

async def _tts_line(text: str, voice: str, rate: str, out: Path, pitch: str = "+0Hz") -> None:
    import edge_tts

    voices_to_try = [voice]
    for alt in ["en-US-BrianMultilingualNeural", "en-US-ChristopherNeural", "en-US-GuyNeural"]:
        if alt not in voices_to_try:
            voices_to_try.append(alt)

    # Sanitize speakable text: strip non-ASCII/emojis, asterisks, and quotes that can break TTS
    clean_text = re.sub(r"[^\x00-\x7F]+", " ", text).replace("*", "").replace('"', "").strip()
    clean_text = re.sub(r"\s+", " ", clean_text)
    if not clean_text:
        clean_text = text.strip()

    last_exc = None
    for v in voices_to_try:
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(clean_text, v, rate=rate, pitch=pitch)
                await communicate.save(str(out))
                if out.exists() and out.stat().st_size > 200:
                    return
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.8 * (attempt + 1))
    if last_exc:
        raise last_exc


async def _synthesize_continuous_tts(full_text: str, voice: str, rate: str, out_mp3: Path,
                                     pitch: str = "+0Hz") -> List[Tuple[float, float, str]]:
    import edge_tts

    voices_to_try = [voice]
    for alt in ["en-US-BrianMultilingualNeural", "en-US-ChristopherNeural", "en-US-GuyNeural"]:
        if alt not in voices_to_try:
            voices_to_try.append(alt)

    clean_text = re.sub(r"[^\x00-\x7F]+", " ", full_text).replace("*", "").replace('"', "").strip()
    clean_text = re.sub(r"\s+", " ", clean_text)
    if not clean_text:
        clean_text = full_text.strip()

    last_exc = None
    for v in voices_to_try:
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(clean_text, v, rate=rate, pitch=pitch)
                sentences: List[Tuple[float, float, str]] = []
                audio_chunks: List[bytes] = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])
                    elif chunk["type"] == "SentenceBoundary":
                        sentences.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7, chunk["text"]))
                total_audio = b"".join(audio_chunks)
                if len(total_audio) > 1000:
                    out_mp3.write_bytes(total_audio)
                    return sentences
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await asyncio.sleep(0.8 * (attempt + 1))
    if last_exc:
        raise last_exc
    return []


def map_sentences_to_lines(lines: List[Dict[str, Any]], sentences: List[Tuple[float, float, str]],
                           total_duration: float) -> List[Dict[str, Any]]:
    """Maps continuous TTS sentence boundaries onto script lines."""
    if not sentences:
        timed = []
        cursor = 0.0
        total_chars = sum(len(l["text"]) for l in lines) or 1
        for line in lines:
            dur = total_duration * (len(line["text"]) / total_chars)
            timed.append({**line, "out_start": round(cursor, 3), "out_end": round(cursor + dur, 3)})
            cursor += dur
        return timed

    timed: List[Dict[str, Any]] = []
    s_idx = 0
    for line in lines:
        text = line["text"].strip()
        matched = []
        accum = ""
        while s_idx < len(sentences):
            s_start, s_dur, s_text = sentences[s_idx]
            accum = (accum + " " + s_text).strip()
            matched.append(sentences[s_idx])
            s_idx += 1
            if len(accum) >= len(text) - 5:
                break
        if matched:
            out_start = matched[0][0]
            out_end = matched[-1][0] + matched[-1][1]
            timed.append({**line, "out_start": round(out_start, 3), "out_end": round(out_end, 3)})

    if len(timed) < len(lines):
        last_end = timed[-1]["out_end"] if timed else 0.0
        remaining_dur = max(total_duration - last_end, 1.0)
        unmatched = lines[len(timed):]
        chars = sum(len(l["text"]) for l in unmatched) or 1
        cursor = last_end
        for line in unmatched:
            dur = remaining_dur * (len(line["text"]) / chars)
            timed.append({**line, "out_start": round(cursor, 3), "out_end": round(cursor + dur, 3)})
            cursor += dur

    return timed


def _synthesize_lines_fallback(lines: List[Dict[str, Any]], voice: str, work: Path,
                               rate: str = "+12%", gap: float = 0.06) -> Tuple[Path, List[Dict[str, Any]]]:
    """Per-line synthesis fallback if continuous stream fails."""
    import time

    parts: List[Path] = []
    timed: List[Dict[str, Any]] = []
    cursor = 0.0
    for i, line in enumerate(lines):
        if i > 0:
            time.sleep(0.35)
        mp3 = work / f"vo_{i:02d}.mp3"
        wav = work / f"vo_{i:02d}.wav"
        is_title = (i == 0 and len(lines) > 1)
        line_rate = "+2%" if is_title else rate
        line_pitch = "-2Hz" if is_title else "+0Hz"
        line_gap = 0.45 if is_title else gap
        asyncio.run(_tts_line(line["text"], voice, line_rate, mp3, pitch=line_pitch))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             f"apad=pad_dur={line_gap}", "-ar", "44100", "-ac", "2", str(wav)])
        dur = probe_duration(wav)
        timed.append({**line, "out_start": round(cursor, 3), "out_end": round(cursor + dur, 3), "is_title": is_title})
        cursor += dur
        parts.append(wav)

    concat_list = work / "vo_list.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    voice_wav = work / "voice_full.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(voice_wav)])
    return voice_wav, timed


def synthesize_lines(lines: List[Dict[str, Any]], voice: str, work: Path,
                     rate: str = "+12%") -> Tuple[Path, List[Dict[str, Any]]]:
    """Voices the narration with the title hook delivered in authoritative trailer cadence,
    followed by a 0.45s dramatic breath pause, followed by continuous storytelling narration,
    voiced seamlessly by the exact same narrator with zero weird cuts.
    """
    clean_lines = []
    for l in lines:
        t = l["text"].strip()
        if not t.endswith((".", "!", "?")):
            t += "."
        clean_lines.append({**l, "text": t})

    if not clean_lines:
        raise ValueError("no lines to synthesize")

    if len(clean_lines) == 1:
        line = clean_lines[0]
        mp3 = work / "voice_00.mp3"
        wav = work / "voice_full.wav"
        asyncio.run(_tts_line(line["text"], voice, rate=rate, pitch="-2Hz", out=mp3))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "2", str(wav)])
        dur = probe_duration(wav)
        return wav, [{**line, "out_start": 0.0, "out_end": round(dur, 3), "is_title": True}]

    title_line = clean_lines[0]
    story_lines = clean_lines[1:]

    title_mp3 = work / "title.mp3"
    title_wav = work / "title.wav"
    story_mp3 = work / "story.mp3"
    story_wav = work / "story.wav"
    voice_wav = work / "voice_full.wav"

    try:
        # Title: authoritative cadence (pitch=-2Hz, rate=+2%), silence trimmed, 0.45s breath beat
        asyncio.run(_tts_line(title_line["text"], voice, rate="+2%", pitch="-2Hz", out=title_mp3))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(title_mp3), "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "apad=pad_dur=0.45", "-ar", "44100", "-ac", "2", str(title_wav)])
        title_dur = probe_duration(title_wav)
        timed_title = {**title_line, "out_start": 0.0, "out_end": round(title_dur, 3), "is_title": True}

        # Story: continuous storytelling pace (pitch=+0Hz, rate=rate)
        story_text = " ".join(l["text"] for l in story_lines)
        sentences = asyncio.run(_synthesize_continuous_tts(story_text, voice, rate=rate, pitch="+0Hz", out_mp3=story_mp3))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(story_mp3), "-ar", "44100", "-ac", "2", str(story_wav)])
        story_dur = probe_duration(story_wav)
        story_timed = map_sentences_to_lines(story_lines, sentences, story_dur)
        for st in story_timed:
            st["out_start"] = round(st["out_start"] + title_dur, 3)
            st["out_end"] = round(st["out_end"] + title_dur, 3)
            st["is_title"] = False

        concat_list = work / "vo_full_list.txt"
        concat_list.write_text(f"file '{title_wav.as_posix()}'\nfile '{story_wav.as_posix()}'\n", encoding="utf-8")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(voice_wav)])
        timed = [timed_title] + story_timed
        total = probe_duration(voice_wav)
    except Exception as exc:  # noqa: BLE001
        log(f"continuous title+story synthesis failed ({exc}); falling back to line-by-line synthesis")
        voice_wav, timed = _synthesize_lines_fallback(clean_lines, voice, work, rate=rate)
        total = probe_duration(voice_wav)

    if total > MAX_SECONDS:
        tempo = min(total / MAX_SECONDS, 1.35)
        log(f"voiceover {total:.1f}s > {MAX_SECONDS}s, speeding up x{tempo:.3f}")
        fast = work / "voice_fast.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice_wav), "-filter:a", f"atempo={tempo:.4f}", str(fast)])
        voice_wav = fast
        for t in timed:
            t["out_start"] = round(t["out_start"] / tempo, 3)
            t["out_end"] = round(t["out_end"] / tempo, 3)
        total = probe_duration(voice_wav)
        if total > 59.5:
            raise RuntimeError(f"voiceover still {total:.1f}s after speed-up; script too long")
    return voice_wav, timed


# ---------------------------------------------------------------------------
# 4. Cut plan
# ---------------------------------------------------------------------------

def plan_cuts(timed_lines: List[Dict[str, Any]], source_duration: float,
              shot_seconds: float = SUBCLIP_SECONDS) -> List[Dict[str, float]]:
    """For each voiced line, spreads short shots across the source window it names."""
    cuts: List[Dict[str, float]] = []
    for line in timed_lines:
        need = line["out_end"] - line["out_start"]
        if need <= 0.05:
            continue
        n = max(1, int(round(need / shot_seconds)))
        shot = need / n
        window = max(line["end"] - line["start"], need)
        step = window / n
        for k in range(n):
            src = line["start"] + k * step
            src = min(max(0.0, src), max(0.0, source_duration - shot - 0.05))
            cuts.append({"src_start": round(src, 3), "duration": round(shot, 3)})
    return cuts


def plan_original_audio_cuts(
    lines: List[Dict[str, Any]],
    source_duration: float,
    target_total: float = 56.0,
) -> Tuple[List[Dict[str, float]], List[Dict[str, Any]], float]:
    """Plans continuous cuts with original audio directly from the source video moments."""
    if not lines:
        raise ValueError("Cannot plan cuts from empty script lines")

    raw_durs = [min(max(2.5, float(l.get("end", 0)) - float(l.get("start", 0))), 12.0) for l in lines]
    tot = sum(raw_durs)
    target = min(target_total, max(10.0, source_duration - 1.0))
    scale = target / tot if tot > 0 else 1.0

    cuts: List[Dict[str, float]] = []
    timed: List[Dict[str, Any]] = []
    t = 0.0

    for i, line in enumerate(lines):
        dur = max(2.0, round(raw_durs[i] * scale, 2))
        src_start = max(0.0, float(line.get("start", 0.0)))
        if src_start + dur > source_duration:
            src_start = max(0.0, source_duration - dur - 0.05)

        cuts.append({"src_start": round(src_start, 3), "duration": round(dur, 3)})
        timed_item = dict(line)
        timed_item["out_start"] = round(t, 2)
        timed_item["out_end"] = round(t + dur, 2)
        timed_item["is_title"] = (i == 0)
        timed.append(timed_item)
        t += dur

    total_duration = round(t, 2)
    return cuts, timed, total_duration


# ---------------------------------------------------------------------------
# 5. Captions
# ---------------------------------------------------------------------------

def caption_chunks(timed_lines: List[Dict[str, Any]], words_per_chunk: int = 3) -> List[Any]:
    """Splits voiced lines into captions. Line 0 (title hook) becomes a prominent TitleCard cue;
    subsequent story lines are split into 2-3 word chunks for dynamic popping captions.
    """
    cues = []
    for line in timed_lines:
        if line.get("is_title"):
            words = line["text"].strip().rstrip(".!?").split()
            if len(words) > 4:
                mid = len(words) // 2
                title_fmt = " ".join(words[:mid]) + r"\N" + " ".join(words[mid:])
            else:
                title_fmt = " ".join(words)
            cues.append((line["out_start"], line["out_end"], title_fmt, "TitleCard"))
            continue

        words = line["text"].split()
        groups = [words[i:i + words_per_chunk] for i in range(0, len(words), words_per_chunk)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2].extend(groups.pop())
        weights = [sum(len(w) + 1 for w in g) for g in groups]
        total_w = float(sum(weights)) or 1.0
        span = line["out_end"] - line["out_start"]
        cursor = line["out_start"]
        for g, w in zip(groups, weights):
            dur = span * w / total_w
            cues.append((round(cursor, 3), round(cursor + dur, 3), " ".join(g), "Cap"))
            cursor += dur
    return cues


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(cues: List[Any], font: str = "Montserrat") -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{84 if OUT_H <= 1350 else 92},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,7,3,2,70,70,{int(OUT_H * 0.16) if OUT_H <= 1350 else 620},1\n"
        f"Style: TitleCard,{font},{92 if OUT_H <= 1350 else 104},&H0000E5FF,&H0000FFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,8,4,5,60,60,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    cap_idx = 0
    for item in cues:
        if len(item) == 4:
            start, end, text, style = item
        else:
            start, end, text = item
            style = "Cap"

        safe = text.upper().replace("{", "(").replace("}", ")").replace("\\", "/")
        if style == "TitleCard":
            safe = safe.replace("/N", r"\N")
            pop = r"\fscx95\fscy95\t(0,120,\fscx100\fscy100)"
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},TitleCard,,0,0,0,,{{{pop}}}{safe}")
        else:
            # every other chunk in yellow for the punchy two-tone look; quick pop-in
            colour = r"\c&H0000E5FF&" if cap_idx % 2 else ""
            pop = r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)"
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{{{pop}{colour}}}{safe}")
            cap_idx += 1
    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------------------
# 6. Render
# ---------------------------------------------------------------------------

def render_shots(source: Path, cuts: List[Dict[str, float]], work: Path,
                 include_audio: bool = False) -> Path:
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
          f"setsar=1,fps={FPS},eq=contrast=1.06:saturation=1.12")
    parts: List[Path] = []
    for i, cut in enumerate(cuts):
        out = work / f"shot_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{cut['src_start']:.3f}", "-i", str(source),
            "-t", f"{cut['duration']:.3f}", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        ]
        if include_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd.append(str(out))
        run(cmd)
        parts.append(out)
    lst = work / "shots.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    joined = work / "shots_joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    return joined


def pick_bgm(creator_info: Dict[str, Any]) -> Optional[Path]:
    name = (creator_info.get("recap") or {}).get("bgm") or DEFAULT_BGM
    path = BGM_DIR / name
    return path if path.exists() else None


def pick_voice(creator_info: Dict[str, Any]) -> str:
    env_voice = os.environ.get("RECAP_VOICE", "").strip()
    if env_voice:
        return env_voice
    recap_voice = (creator_info.get("recap") or {}).get("voice", "").strip()
    if recap_voice:
        return recap_voice
    cat_voice = creator_info.get("narrator_voice", "").strip()
    if cat_voice and "Guy" not in cat_voice and "Christopher" not in cat_voice:
        return cat_voice
    return DEFAULT_VOICE


def build_audio_filter(duration: float, has_bgm: bool, keep_original_audio: bool = False) -> str:
    """Builds the FFmpeg audio filtergraph.

    If keep_original_audio is True, normalizes the source video audio from [0:a]
    and optionally mixes subtle BGM at constant volume.
    Otherwise, mixes narrator voiceover [1:a] and constant BGM [2:a].
    """
    if keep_original_audio:
        if has_bgm:
            return (
                f"[0:a]aresample=44100,volume={ORIGINAL_AUDIO_VOLUME:.2f},loudnorm=I=-14:TP=-1.5:LRA=11[orig];"
                f"[1:a]aresample=44100,volume={BGM_VOLUME:.2f}[bg];"
                f"[orig][bg]amix=inputs=2:normalize=0:duration=first,atrim=0:{duration:.3f},alimiter=limit=0.95[a]"
            )
        return f"[0:a]aresample=44100,volume={ORIGINAL_AUDIO_VOLUME:.2f},atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[a]"

    if has_bgm:
        return (
            f"[1:a]aresample=44100,volume={VOICE_VOLUME:.2f},loudnorm=I=-14:TP=-2.0:LRA=11[vo];"
            f"[2:a]aresample=44100,volume={BGM_VOLUME:.2f}[bg];"
            f"[vo][bg]amix=inputs=2:normalize=0:duration=first,atrim=0:{duration:.3f},alimiter=limit=0.95[a]"
        )
    return f"[1:a]aresample=44100,volume={VOICE_VOLUME:.2f},atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[a]"


def build_original_audio_filter(duration: float, has_bgm: bool) -> str:
    return build_audio_filter(duration, has_bgm=has_bgm, keep_original_audio=True)


def final_mux(video: Path, voice: Optional[Path], ass: Path, bgm: Optional[Path], output: Path, duration: float,
              keep_original_audio: bool = False) -> None:
    ass_arg = ass.as_posix().replace(":", r"\:")
    fonts_arg = FONTS_DIR.as_posix().replace(":", r"\:")
    vf = f"[0:v]subtitles='{ass_arg}':fontsdir='{fonts_arg}',tpad=stop_mode=clone:stop_duration=2,trim=0:{duration:.3f},setpts=PTS-STARTPTS[v]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    if not keep_original_audio:
        if voice:
            cmd += ["-i", str(voice)]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
    af = build_audio_filter(duration, has_bgm=bool(bgm), keep_original_audio=keep_original_audio)
    cmd += [
        "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{duration:.3f}", str(output),
    ]
    run(cmd)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------

def package_title(hook: Optional[str], fallback_title: str, creator_info: Dict[str, Any]) -> str:
    if not hook:
        return fallback_title
    emoji_src = (creator_info.get("packaging") or {}).get("shock_emojis", "😳")
    emoji = emoji_src[0] if emoji_src else ""
    handle = creator_info.get("channel_handle", "@YouTube")
    title = f"{hook} {emoji} ({handle}/YT)".replace("  ", " ")
    return title if len(title) <= 100 else fallback_title


def update_metadata(metadata_path: Path, title: str, script: Dict[str, Any], timed: List[Dict[str, Any]],
                    creator_info: Dict[str, Any], duration: float, voice: Optional[str] = None,
                    original_audio: bool = False) -> None:
    meta: Dict[str, Any] = {}
    if metadata_path.exists():
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    description = script["description"] or f"{script['lines'][0]['text']}"
    meta.pop("stages", None)
    meta.update({
        "title": title,
        "description": description,
        "narrated_recap": not original_audio,
        "original_audio": original_audio,
        "aspect_ratio": ASPECT_LABEL,
        "resolution": f"{OUT_W}x{OUT_H}",
        "duration_seconds": round(duration, 2),
    })
    if original_audio:
        meta["audio_format"] = "original_video_audio"
        meta["scenes"] = [{"text": t["text"], "source": [t["start"], t["end"]],
                           "at": [t["out_start"], t["out_end"]]} for t in timed]
    else:
        meta["voiceover"] = {"voice": voice or DEFAULT_VOICE, "words": script["word_count"],
                             "script": [{"text": t["text"], "source": [t["start"], t["end"]],
                                         "at": [t["out_start"], t["out_end"]]} for t in timed]}
    metadata_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render a Short with original audio or @TestedNProved recap")
    ap.add_argument("--raw-video", required=True, type=Path)
    ap.add_argument("--creator", required=True)
    ap.add_argument("--source-title", required=True)
    ap.add_argument("--fallback-title", required=True, help="already-packaged title used if the LLM hook is unusable")
    ap.add_argument("--catalog", type=Path, default=ROOT_DIR / "creator_catalog.json")
    ap.add_argument("--metadata", type=Path, default=ROOT_DIR / "metadata.json")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--title-out", type=Path, default=ROOT_DIR / ".recap_title.txt")
    ap.add_argument("--transcript-json", type=Path, help="reuse a saved transcript instead of transcribing")
    ap.add_argument("--original-audio", action="store_true", default=True,
                    help="keep original audio from the video instead of narrating (default: True)")
    ap.add_argument("--narrate", dest="original_audio", action="store_false",
                    help="synthesize AI voiceover narration instead of keeping original audio")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    creator_info = catalog["creators"][args.creator]
    voice = pick_voice(creator_info)
    rate = (creator_info.get("recap") or {}).get("rate", "+6%")

    work = Path(tempfile.mkdtemp(prefix="recap_"))
    try:
        source_duration = probe_duration(args.raw_video)
        log(f"source: {args.raw_video} ({source_duration:.0f}s)")
        if source_duration < 240:
            # an excerpt (e.g. a 90s --download-sections clip) would make the script
            # retell only the intro and invent a wrong ending
            raise RuntimeError(f"source is only {source_duration:.0f}s; a recap needs the full video")

        if args.transcript_json and args.transcript_json.exists():
            segments = json.loads(args.transcript_json.read_text(encoding="utf-8"))
        else:
            segments = transcribe(args.raw_video, work)
        log(f"transcript: {len(segments)} segments")
        if len(segments) < 10:
            raise RuntimeError("transcript too short to write a recap")
        (ROOT_DIR / ".recap_transcript.json").write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")

        script = write_script(segments, args.source_title, creator_info.get("name", args.creator), source_duration)
        log(f"script: {len(script['lines'])} lines, {script['word_count']} words; hook={script['title_hook']!r}")

        if args.original_audio:
            cuts, timed, duration = plan_original_audio_cuts(script["lines"], source_duration)
            log(f"original audio mode: {duration:.1f}s, {len(cuts)} continuous scenes (zero narration)")
            shots = render_shots(args.raw_video, cuts, work, include_audio=True)
            ass = work / "captions.ass"
            ass.write_text(build_ass(caption_chunks(timed)), encoding="utf-8")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            final_mux(shots, None, ass, pick_bgm(creator_info), args.output, duration, keep_original_audio=True)
            title = package_title(script["title_hook"], args.fallback_title, creator_info)
            update_metadata(args.metadata, title, script, timed, creator_info, duration, voice=None, original_audio=True)
            args.title_out.write_text(title, encoding="utf-8")
            log(f"done: {args.output} | title: {title}")
            return 0

        voice_wav, timed = synthesize_lines(script["lines"], voice, work, rate=rate)
        duration = probe_duration(voice_wav) + 0.25
        cuts = plan_cuts(timed, source_duration)
        log(f"voiceover {duration:.1f}s, {len(cuts)} shots")

        shots = render_shots(args.raw_video, cuts, work, include_audio=False)
        ass = work / "captions.ass"
        ass.write_text(build_ass(caption_chunks(timed)), encoding="utf-8")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        final_mux(shots, voice_wav, ass, pick_bgm(creator_info), args.output, duration, keep_original_audio=False)

        title = package_title(script["title_hook"], args.fallback_title, creator_info)
        update_metadata(args.metadata, title, script, timed, creator_info, duration, voice, original_audio=False)
        args.title_out.write_text(title, encoding="utf-8")
        log(f"done: {args.output} | title: {title}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED: {exc}")
        return 1
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
