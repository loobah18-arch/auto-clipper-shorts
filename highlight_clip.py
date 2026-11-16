#!/usr/bin/env python3
"""
Highlight Clip Renderer (woo's clips benchmark format, Short GKz-NtMJFg8)
========================================================================

The format of the channel's best Short: a narrator reads a curiosity hook over
a teaser shot, then the creator's ORIGINAL footage and audio plays, cut down to
the best moments of the long video in story order, with captions that follow
what is actually said and a small HUD badge naming each beat.

The hook is read the way Short YDOHG7UgzNc reads it: one question in the
Tested&Proved style ("What would happen if Speed went undercover as Spider-Man
for Halloween?"), BrianMultilingualNeural at +6%.

  1. Transcribe the full source (Groq Whisper, word timestamps).
  2. An LLM picks the spoken hook, the upload title hook, a teaser moment and
     4-7 clips that start and end on sentence boundaries.
  3. Teaser shot (original audio ducked) + hook voice, then the clips with
     their original audio, captions from the real words, badges top left.

Reuses the transcription, LLM and TTS helpers in narrated_recap.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import narrated_recap as nr

ROOT_DIR = Path(__file__).resolve().parent
FONTS_DIR = ROOT_DIR / "assets" / "fonts"

_ASPECTS = {"3:4": (1080, 1440), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
ASPECT = os.environ.get("HIGHLIGHT_ASPECT", "3:4").strip() or "3:4"
OUT_W, OUT_H = _ASPECTS.get(ASPECT, _ASPECTS["3:4"])
FPS = 30
MAX_SECONDS = 58.0
CLIP_BUDGET = (44.0, 52.0)
HOOK_VOICE = os.environ.get("HIGHLIGHT_VOICE", "en-US-BrianMultilingualNeural")
HOOK_RATE = os.environ.get("HIGHLIGHT_RATE", "+6%")
HOOK_VOLUME = float(os.environ.get("HIGHLIGHT_HOOK_VOLUME", "1.6"))
TEASER_AMBIENCE = 0.18

log = nr.log
run = nr.run


# ---------------------------------------------------------------------------
# 1. Transcription with word timestamps
# ---------------------------------------------------------------------------

def transcribe_with_words(video: Path, work: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (segments, words). Words may be empty when only segment timing is available."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    chunks = nr.extract_audio(video, work)
    if key:
        try:
            segments: List[Dict[str, Any]] = []
            words: List[Dict[str, Any]] = []
            for offset, path in chunks:
                with open(path, "rb") as fh:
                    data = nr._http_post(
                        f"{nr.GROQ_BASE}/audio/transcriptions",
                        {"Authorization": f"Bearer {key}"},
                        files={"file": (path.name, fh, "audio/mpeg")},
                        data={"model": nr.GROQ_WHISPER_MODEL, "response_format": "verbose_json",
                              "timestamp_granularities[]": ["word", "segment"]},
                    )
                for seg in data.get("segments", []) or []:
                    text = (seg.get("text") or "").strip()
                    if text:
                        segments.append({"start": round(float(seg["start"]) + offset, 2),
                                         "end": round(float(seg["end"]) + offset, 2), "text": text})
                for w in data.get("words", []) or []:
                    token = (w.get("word") or "").strip()
                    if token:
                        words.append({"start": round(float(w["start"]) + offset, 2),
                                      "end": round(float(w["end"]) + offset, 2), "word": token})
            if segments:
                return segments, words
        except Exception as exc:  # noqa: BLE001
            log(f"Groq word transcription failed: {exc}")
    return nr.transcribe(video, work), []


def words_from_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Spreads each segment's words across its span by character length (fallback timing)."""
    out: List[Dict[str, Any]] = []
    for seg in segments:
        toks = seg["text"].split()
        if not toks:
            continue
        span = max(0.1, seg["end"] - seg["start"])
        weights = [len(t) + 1 for t in toks]
        total = float(sum(weights))
        cur = seg["start"]
        for t, w in zip(toks, weights):
            d = span * w / total
            out.append({"start": round(cur, 2), "end": round(cur + d, 2), "word": t})
            cur += d
    return out


# ---------------------------------------------------------------------------
# 2. Picking the hook and the moments
# ---------------------------------------------------------------------------

PICK_SYSTEM = """You cut one long YouTube video into a single highlight Short of about 55 seconds for the channel "woo's clips".

The Short opens with a narrator reading ONE curiosity hook over a teaser shot, then plays the creator's ORIGINAL footage and audio, cut down to the best moments in story order. Viewers must understand the whole story from the clips alone.

SPOKEN HOOK (read aloud by the narrator, this matters most):
- One question, 7-16 words, in the Tested&Proved style. Examples:
  "What would happen if Speed went undercover as Spider-Man for Halloween?"
  "Can he escape 100 real cops for $500,000?"
  "What would happen if you compressed 100 cans of Red Bull into one drink?"
- Name the creator by the short name fans use (Speed, MrBeast, NileRed, Mark Rober, Ruhi) or say "he"/"this guy". Plain English, no emoji, no hashtags.

TITLE HOOK (upload title): true to how the video ends, starts with He, She, They or This Guy, states what happened as a completed act, Title Case, max 60 characters, no emoji, no @handle. Example: "He Went Undercover as Spider-Man for Halloween".

CLIPS:
- Only state what really happens in the transcript; if the attempt fails, the title must not claim success.
- 4 to 7 clips in chronological order, each 5-14 seconds, together 44-52 seconds.
- Each clip starts at the START of a transcript line and ends at the END of a line, so nobody is cut mid-sentence. Use the [start-end] numbers exactly.
- First clip: the creator setting up the challenge or premise. Middle clips: the key turning points. Last clip: the final result or reaction.
- Never pick sponsor reads, intros, outros or "subscribe" requests.
- badge: a 2-5 word HUD tag in capitals for that beat, e.g. "$500,000 ESCAPE CHALLENGE", "CORNERED | DEAD END", "OUTCOME: COPS WIN $500,000".

TEASER: the second of the most visually striking moment, shown silently for ~4 seconds under the hook. It may come from anywhere in the video.

DESCRIPTION: one or two plain sentences telling the story.

OUTPUT strict JSON, no markdown:
{"spoken_hook": "...?", "title_hook": "...", "description": "...", "teaser_start": 123.0,
 "clips": [{"start": 12.3, "end": 24.0, "badge": "..."}]}"""


def build_pick_prompt(source_title: str, creator_name: str, segments: List[Dict[str, Any]], duration: float) -> str:
    transcript = nr.condense_transcript(segments, block_seconds=6.0)
    return (f"SOURCE VIDEO: \"{source_title}\" by {creator_name} (length {int(duration)} seconds)\n\n"
            f"TRANSCRIPT (each line tagged [start-end] in seconds):\n{transcript}\n\n"
            "Return the JSON now.")


def clean_spoken_hook(text: Any) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f#@*\"]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" '")
    words = text.split()
    if not (5 <= len(words) <= 22):
        return None
    if not text.endswith(("?", ".", "!")):
        text += "?"
    return text


def fallback_spoken_hook(source_title: str, creator_name: str) -> str:
    """Turns a raw title into a question when the LLM gives nothing usable."""
    t = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]", "", source_title)
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t).strip(" !.?-|")
    if not t:
        return f"What did {creator_name} just pull off?"
    if t.endswith("?") or re.match(r"^(can|could|will|would|what|how|is|did|does)\b", t, re.I):
        return t.rstrip("?") + "?"
    low = t[0].lower() + t[1:]
    low = re.sub(r"^(i|we)\b", "he", low, flags=re.I)
    return f"What happens when {creator_name} tries {low}?" if re.match(r"^\w+ing\b", low) else f"Can {creator_name} pull this off: {low}?"


def _snap(t: float, edges: List[float]) -> float:
    return min(edges, key=lambda e: abs(e - t)) if edges else t


def validate_clips(raw_clips: Any, segments: List[Dict[str, Any]], source_duration: float,
                   budget: float) -> List[Dict[str, Any]]:
    """Snaps clips to sentence edges, keeps them in order without overlap, and fits the time budget."""
    starts = [s["start"] for s in segments]
    ends = [s["end"] for s in segments]
    clips: List[Dict[str, Any]] = []
    for c in raw_clips if isinstance(raw_clips, list) else []:
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(0.0, _snap(s, starts))
        later_ends = [x for x in ends if x >= s + 3.0]
        e = _snap(e, later_ends) if later_ends else min(source_duration, s + 8.0)
        e = min(e, source_duration - 0.05, s + 16.0)
        if e - s < 2.5:
            continue
        badge = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f{}\\]", "", str(c.get("badge", ""))).strip().upper()
        clips.append({"start": round(s, 2), "end": round(e, 2), "badge": badge[:40]})
    clips.sort(key=lambda c: c["start"])
    merged: List[Dict[str, Any]] = []
    for c in clips:
        if merged and c["start"] < merged[-1]["end"]:
            if c["end"] - merged[-1]["end"] < 2.5:
                continue
            c["start"] = merged[-1]["end"]
        merged.append(c)
    if not merged:
        raise RuntimeError("LLM returned no usable clips")
    # Fit the budget: trim the longest clip back to an earlier sentence end, else hard trim.
    while sum(c["end"] - c["start"] for c in merged) > budget:
        over = sum(c["end"] - c["start"] for c in merged) - budget
        longest = max(merged, key=lambda c: c["end"] - c["start"])
        if longest["end"] - longest["start"] <= 4.0:
            merged.pop(len(merged) // 2)
            continue
        target = longest["end"] - over
        inner = [x for x in ends if longest["start"] + 4.0 <= x < longest["end"] and x >= target]
        longest["end"] = round(min(inner) if inner else max(longest["start"] + 4.0, target), 2)
    return merged


def pick_moments(segments: List[Dict[str, Any]], source_title: str, creator_name: str,
                 source_duration: float, budget: float) -> Dict[str, Any]:
    prompt = build_pick_prompt(source_title, creator_name, segments, source_duration)
    last_exc: Optional[Exception] = None
    for _ in range(2):
        try:
            raw = nr._extract_json(nr.chat_completion(PICK_SYSTEM, prompt))
            clips = validate_clips(raw.get("clips"), segments, source_duration, budget)
            teaser = raw.get("teaser_start")
            try:
                teaser = float(teaser)
            except (TypeError, ValueError):
                teaser = clips[-1]["start"]
            return {
                "spoken_hook": clean_spoken_hook(raw.get("spoken_hook")) or fallback_spoken_hook(source_title, creator_name),
                "title_hook": nr.clean_title_hook(raw.get("title_hook")),
                "description": str(raw.get("description") or "").strip(),
                "teaser_start": max(0.0, min(teaser, source_duration - 5.0)),
                "clips": clips,
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log(f"moment picking failed, retrying: {exc}")
    raise RuntimeError(f"could not pick moments: {last_exc}")


# ---------------------------------------------------------------------------
# 3. Hook voice, captions, render
# ---------------------------------------------------------------------------

def synth_hook(text: str, work: Path) -> Path:
    mp3 = work / "hook.mp3"
    asyncio.run(nr._tts_line(text, HOOK_VOICE, HOOK_RATE, mp3))
    wav = work / "hook.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "2", str(wav)])
    return wav


def caption_cues(clips: List[Dict[str, Any]], words: List[Dict[str, Any]], intro: float,
                 per_chunk: int = 3) -> List[Tuple[float, float, str]]:
    """Maps the real spoken words of each clip onto the output timeline in 2-3 word chunks."""
    cues: List[Tuple[float, float, str]] = []
    cursor = intro
    for c in clips:
        inside = [w for w in words if w["start"] >= c["start"] - 0.05 and w["end"] <= c["end"] + 0.15]
        chunk: List[Dict[str, Any]] = []
        for w in inside:
            chunk.append(w)
            if len(chunk) >= per_chunk or re.search(r"[.!?,]$", w["word"]):
                cues.append(_cue(chunk, c, cursor))
                chunk = []
        if chunk:
            cues.append(_cue(chunk, c, cursor))
        cursor += c["end"] - c["start"]
    # no cue may run into the next one
    fixed = []
    for i, (s, e, t) in enumerate(cues):
        if i + 1 < len(cues):
            e = min(e, cues[i + 1][0])
        if e - s >= 0.08:
            fixed.append((s, e, t))
    return fixed


def _cue(chunk: List[Dict[str, Any]], clip: Dict[str, Any], cursor: float) -> Tuple[float, float, str]:
    s = cursor + max(0.0, chunk[0]["start"] - clip["start"])
    e = cursor + min(clip["end"], chunk[-1]["end"] + 0.12) - clip["start"]
    text = " ".join(w["word"] for w in chunk).strip()
    return (round(s, 3), round(max(e, s + 0.25), 3), text)


def _t(t: float) -> str:
    return nr._ass_time(t)


def build_ass(hook: str, hook_end: float, cues: List[Tuple[float, float, str]],
              badges: List[Tuple[float, float, str]], font: str = "Montserrat") -> str:
    cap_size = 54 if OUT_W <= 1080 else 60
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hook,{font},{cap_size + 4},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,5,80,80,0,1\n"
        f"Style: Cap,{font},{cap_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,2,80,80,{int(OUT_H * 0.20)},1\n"
        f"Style: Badge,{font},30,&H0000D5FF,&H0000D5FF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,1,0,1,3,1,7,40,40,40,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    clean = lambda s: s.upper().replace("{", "(").replace("}", ")").replace("\\", "/")  # noqa: E731
    ev = []
    # hook captions follow the narrator: 4-5 word chunks timed by length over the voice
    hw = hook.split()
    groups = [hw[i:i + 5] for i in range(0, len(hw), 5)]
    if len(groups) > 1 and len(groups[-1]) <= 2:
        groups[-2].extend(groups.pop())
    weights = [sum(len(w) + 1 for w in g) for g in groups]
    total_w = float(sum(weights)) or 1.0
    cur, span = 0.15, max(0.5, hook_end - 0.45)
    for g, w in zip(groups, weights):
        d = span * w / total_w
        end = hook_end if g is groups[-1] else cur + d
        ev.append(f"Dialogue: 1,{_t(cur)},{_t(end)},Hook,,0,0,0,,{clean(' '.join(g))}")
        cur += d
    for s, e, t in cues:
        ev.append(f"Dialogue: 1,{_t(s)},{_t(e)},Cap,,0,0,0,,{clean(t)}")
    for s, e, t in badges:
        if t:
            ev.append(f"Dialogue: 0,{_t(s)},{_t(e)},Badge,,0,0,0,,{clean(t)}")
    return header + "\n".join(ev) + "\n"


VF = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
      f"setsar=1,fps={FPS}")


def render_teaser(source: Path, start: float, hook_wav: Path, dur: float, out: Path) -> None:
    af = (f"[0:a]aresample=44100,volume={TEASER_AMBIENCE},lowpass=f=1400[amb];"
          f"[1:a]adelay=150|150,volume={HOOK_VOLUME}[vo];"
          f"[amb][vo]amix=inputs=2:normalize=0:duration=longest,apad,atrim=0:{dur:.3f}[a]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
         "-i", str(hook_wav), "-filter_complex",
         f"[0:v]{VF},eq=contrast=1.12:brightness=-0.05:saturation=1.2,tpad=stop_mode=clone:stop_duration=2,"
         f"trim=0:{dur:.3f}[v];{af}",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.3f}", str(out)])


def render_clip(source: Path, clip: Dict[str, Any], out: Path) -> None:
    dur = clip["end"] - clip["start"]
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{clip['start']:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
         "-vf", f"{VF},eq=contrast=1.06:saturation=1.12", "-af", "aresample=44100,apad", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(out)])


def finish(parts: List[Path], ass: Path, out: Path, work: Path, duration: float) -> None:
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    ass_arg = ass.as_posix().replace(":", r"\:")
    fonts_arg = FONTS_DIR.as_posix().replace(":", r"\:")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined),
         "-vf", f"subtitles='{ass_arg}':fontsdir='{fonts_arg}'",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{duration:.3f}", str(out)])


def update_metadata(path: Path, title: str, plan: Dict[str, Any], duration: float) -> None:
    meta: Dict[str, Any] = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta.pop("stages", None)
    meta.update({
        "title": title,
        "description": plan["description"] or plan["spoken_hook"],
        "highlight_clip": True,
        "narrated_recap": False,
        "original_audio": True,
        "spoken_hook": plan["spoken_hook"],
        "aspect_ratio": ASPECT,
        "resolution": f"{OUT_W}x{OUT_H}",
        "duration_seconds": round(duration, 2),
        "clips": plan["clips"],
    })
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render a woo's clips highlight Short with a narrated hook")
    ap.add_argument("--raw-video", required=True, type=Path)
    ap.add_argument("--creator", required=True)
    ap.add_argument("--source-title", required=True)
    ap.add_argument("--fallback-title", required=True)
    ap.add_argument("--catalog", type=Path, default=ROOT_DIR / "creator_catalog.json")
    ap.add_argument("--metadata", type=Path, default=ROOT_DIR / "metadata.json")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--title-out", type=Path, default=ROOT_DIR / ".recap_title.txt")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    creator_info = json.loads(args.catalog.read_text(encoding="utf-8"))["creators"][args.creator]
    creator_name = creator_info.get("name", args.creator)
    work = Path(tempfile.mkdtemp(prefix="highlight_"))
    try:
        source_duration = nr.probe_duration(args.raw_video)
        log(f"source: {args.raw_video} ({source_duration:.0f}s)")
        if source_duration < 120:
            raise RuntimeError(f"source is only {source_duration:.0f}s; need the full video to pick highlights")

        segments, words = transcribe_with_words(args.raw_video, work)
        log(f"transcript: {len(segments)} segments, {len(words)} timed words")
        if len(segments) < 8:
            raise RuntimeError("transcript too short to pick highlights")
        if not words:
            words = words_from_segments(segments)

        # hook first, so the clip budget knows how long the intro is
        draft_hook = fallback_spoken_hook(args.source_title, creator_name)
        plan = pick_moments(segments, args.source_title, creator_name, source_duration, CLIP_BUDGET[1])
        hook_wav = synth_hook(plan["spoken_hook"] or draft_hook, work)
        intro = round(nr.probe_duration(hook_wav) + 0.45, 2)
        budget = MAX_SECONDS - intro
        if sum(c["end"] - c["start"] for c in plan["clips"]) > budget:
            plan["clips"] = validate_clips(plan["clips"], segments, source_duration, budget)
        log(f"hook ({intro:.1f}s): {plan['spoken_hook']}")
        for c in plan["clips"]:
            log(f"  clip {c['start']:.1f}-{c['end']:.1f}s  [{c['badge']}]")

        parts = [work / "p00_teaser.mp4"]
        render_teaser(args.raw_video, plan["teaser_start"], hook_wav, intro, parts[0])
        badges: List[Tuple[float, float, str]] = []
        cursor = intro
        for i, c in enumerate(plan["clips"], start=1):
            p = work / f"p{i:02d}.mp4"
            render_clip(args.raw_video, c, p)
            parts.append(p)
            dur = c["end"] - c["start"]
            badges.append((cursor, cursor + dur, c["badge"]))
            cursor += dur
        duration = round(cursor, 2)

        ass = work / "captions.ass"
        ass.write_text(build_ass(plan["spoken_hook"], intro, caption_cues(plan["clips"], words, intro), badges),
                       encoding="utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        finish(parts, ass, args.output, work, duration)

        title = nr.package_title(plan["title_hook"], args.fallback_title, creator_info)
        update_metadata(args.metadata, title, plan, duration)
        args.title_out.write_text(title, encoding="utf-8")
        log(f"done: {args.output} ({duration:.1f}s, {OUT_W}x{OUT_H}) | title: {title}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED: {exc}")
        return 1
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
