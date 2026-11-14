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
DEFAULT_VOICE = os.environ.get("RECAP_VOICE", "en-US-AndrewMultilingualNeural")
BGM_VOLUME = float(os.environ.get("RECAP_BGM_VOLUME", "0.02"))
VOICE_VOLUME = float(os.environ.get("RECAP_VOICE_VOLUME", "1.6"))


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

STYLE (copy it closely):
- Third person, casual, energetic: "This guy...", "He...". Never "I" or "we".
- Line 1 is a curiosity hook, usually a question: "What would happen if you compressed 100 cans of Red Bull into one drink?"
- Then: the goal ("the goal was simple: ..."), the method, a setback or failure, a twist ("But here's where it gets crazy."), the final test, and a punchy payoff/result in the last line.
- Short spoken sentences, concrete numbers and details from the transcript, zero filler, no hashtags, no emojis, no stage directions.
- Only state things that actually happen in the transcript. Always write in English, even if the transcript is in another language.
- Keep the energy high: use at least one twist line like "But here's where it gets crazy." and end on a punchy payoff sentence.
- Hit the word count: aim for about 170 words total. Too short sounds rushed and flat.

EXAMPLE (another video):
"What would happen if you compressed 100 cans of Red Bull into one drink? This guy bought 100 cans of sugar-free Red Bull and poured every single one into a giant bucket. That's 25 liters of energy drink, and the goal was simple: concentrate all of it into the size of one can. To do that, he used this insane machine..."

OUTPUT: strict JSON, no markdown:
{
  "title_hook": "He Compressed 100 RedBulls into 1 Drink",
  "description": "One or two plain sentences telling the story, like: This scientist took 100 cans of sugar-free Red Bull and ran them through a machine that removes all the water.",
  "lines": [ {"text": "spoken sentence(s)", "start": 123, "end": 140}, ... ]
}
- title_hook: starts with "He", "She", "They" or "This Guy", states the result as a completed act, Title Case, max 60 characters, no emoji, no @handle.
- lines: 9 to 14 items, in story order, together """ + f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]}" + """ words.
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

    words = sum(len(l["text"].split()) for l in lines)
    if len(lines) < 5 or words < 80:
        raise ValueError(f"script too thin ({len(lines)} lines, {words} words)")

    description = re.sub(r"\s+", " ", str(raw.get("description", ""))).strip()
    description = re.sub(r"#\w+", "", description).strip()
    return {
        "title_hook": clean_title_hook(raw.get("title_hook")),
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

async def _tts_line(text: str, voice: str, rate: str, out: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def synthesize_lines(lines: List[Dict[str, Any]], voice: str, work: Path,
                     rate: str = "+12%", gap: float = 0.06) -> Tuple[Path, List[Dict[str, Any]]]:
    """Voices each line separately so every line's start/end on the output timeline is exact."""
    parts: List[Path] = []
    timed: List[Dict[str, Any]] = []
    cursor = 0.0
    for i, line in enumerate(lines):
        mp3 = work / f"vo_{i:02d}.mp3"
        wav = work / f"vo_{i:02d}.wav"
        asyncio.run(_tts_line(line["text"], voice, rate, mp3))
        # trim TTS lead/trail silence, add a tiny breath gap
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             f"apad=pad_dur={gap}", "-ar", "44100", "-ac", "2", str(wav)])
        dur = probe_duration(wav)
        timed.append({**line, "out_start": round(cursor, 3), "out_end": round(cursor + dur, 3)})
        cursor += dur
        parts.append(wav)

    concat_list = work / "vo_list.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    voice_wav = work / "voice_full.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(voice_wav)])

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


# ---------------------------------------------------------------------------
# 5. Captions
# ---------------------------------------------------------------------------

def caption_chunks(timed_lines: List[Dict[str, Any]], words_per_chunk: int = 3) -> List[Tuple[float, float, str]]:
    """Splits each voiced line into 2-3 word chunks, timed by character length."""
    cues: List[Tuple[float, float, str]] = []
    for line in timed_lines:
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
            cues.append((round(cursor, 3), round(cursor + dur, 3), " ".join(g)))
            cursor += dur
    return cues


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(cues: List[Tuple[float, float, str]], font: str = "Montserrat") -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{84 if OUT_H <= 1350 else 92},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,7,3,2,70,70,{int(OUT_H * 0.16) if OUT_H <= 1350 else 620},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for i, (start, end, text) in enumerate(cues):
        safe = text.upper().replace("{", "(").replace("}", ")").replace("\\", "/")
        # every other chunk in yellow for the punchy two-tone look; quick pop-in
        colour = r"\c&H0000E5FF&" if i % 2 else ""
        pop = r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)"
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{{{pop}{colour}}}{safe}")
    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------------------
# 6. Render
# ---------------------------------------------------------------------------

def render_shots(source: Path, cuts: List[Dict[str, float]], work: Path) -> Path:
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
          f"setsar=1,fps={FPS},eq=contrast=1.06:saturation=1.12")
    parts: List[Path] = []
    for i, cut in enumerate(cuts):
        out = work / f"shot_{i:03d}.mp4"
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{cut['src_start']:.3f}", "-i", str(source),
            "-t", f"{cut['duration']:.3f}", "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out),
        ])
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


def build_audio_filter(duration: float, has_bgm: bool) -> str:
    """Builds the FFmpeg audio filtergraph.

    BGM volume is held completely constant across the entire video (no sidechain ducking)
    so the track does not pump or swell during pauses in narration.
    """
    if has_bgm:
        return (
            f"[1:a]aresample=44100,volume={VOICE_VOLUME:.2f},loudnorm=I=-14:TP=-2.0:LRA=11[vo];"
            f"[2:a]aresample=44100,volume={BGM_VOLUME:.2f}[bg];"
            f"[vo][bg]amix=inputs=2:normalize=0:duration=first,atrim=0:{duration:.3f},alimiter=limit=0.95[a]"
        )
    return f"[1:a]aresample=44100,volume={VOICE_VOLUME:.2f},atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[a]"


def final_mux(video: Path, voice: Path, ass: Path, bgm: Optional[Path], output: Path, duration: float) -> None:
    ass_arg = ass.as_posix().replace(":", r"\:")
    fonts_arg = FONTS_DIR.as_posix().replace(":", r"\:")
    vf = f"[0:v]subtitles='{ass_arg}':fontsdir='{fonts_arg}',tpad=stop_mode=clone:stop_duration=2,trim=0:{duration:.3f},setpts=PTS-STARTPTS[v]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice)]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
    af = build_audio_filter(duration, has_bgm=bool(bgm))
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
                    creator_info: Dict[str, Any], duration: float, voice: str) -> None:
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
        "narrated_recap": True,
        "aspect_ratio": ASPECT_LABEL,
        "resolution": f"{OUT_W}x{OUT_H}",
        "duration_seconds": round(duration, 2),
        "voiceover": {"voice": voice, "words": script["word_count"],
                      "script": [{"text": t["text"], "source": [t["start"], t["end"]],
                                  "at": [t["out_start"], t["out_end"]]} for t in timed]},
    })
    metadata_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render a @TestedNProved-style narrated recap Short")
    ap.add_argument("--raw-video", required=True, type=Path)
    ap.add_argument("--creator", required=True)
    ap.add_argument("--source-title", required=True)
    ap.add_argument("--fallback-title", required=True, help="already-packaged title used if the LLM hook is unusable")
    ap.add_argument("--catalog", type=Path, default=ROOT_DIR / "creator_catalog.json")
    ap.add_argument("--metadata", type=Path, default=ROOT_DIR / "metadata.json")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--title-out", type=Path, default=ROOT_DIR / ".recap_title.txt")
    ap.add_argument("--transcript-json", type=Path, help="reuse a saved transcript instead of transcribing")
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

        voice_wav, timed = synthesize_lines(script["lines"], voice, work, rate=rate)
        duration = probe_duration(voice_wav) + 0.25
        cuts = plan_cuts(timed, source_duration)
        log(f"voiceover {duration:.1f}s, {len(cuts)} shots")

        shots = render_shots(args.raw_video, cuts, work)
        ass = work / "captions.ass"
        ass.write_text(build_ass(caption_chunks(timed)), encoding="utf-8")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        final_mux(shots, voice_wav, ass, pick_bgm(creator_info), args.output, duration)

        title = package_title(script["title_hook"], args.fallback_title, creator_info)
        update_metadata(args.metadata, title, script, timed, creator_info, duration, voice)
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
