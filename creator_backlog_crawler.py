#!/usr/bin/env python3
"""
Creator Backlog Crawler & Fresh Upload Prioritization Engine.

Implements the user-defined algorithmic upload selection rule:
1. Examine the target creator's YouTube channel uploads in reverse chronological order (newest to oldest).
2. If the creator uploaded a fresh latest video that has not yet been clipped, clip that latest video first!
3. Otherwise, if the latest video was already clipped, continue marching backwards towards older videos
   in the catalog, clipping the next unclipped older release.
4. Record all clipped source video IDs in upload_history.json to ensure zero duplicate uploads.
"""

import sys
import json
import re
import argparse
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Set, Tuple

from hypit_engine import ARC_INTRO_SECONDS, ARC_STAGES

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_FILE = ROOT_DIR / "upload_history.json"
DEFAULT_CATALOG_FILE = ROOT_DIR / "creator_catalog.json"


def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [BacklogCrawler] {msg}", flush=True)


def load_creator_catalog(catalog_file: Path = DEFAULT_CATALOG_FILE) -> Dict[str, Any]:
    if not catalog_file.exists():
        raise FileNotFoundError(f"Creator catalog not found: {catalog_file}")
    return json.loads(catalog_file.read_text(encoding="utf-8"))


def get_clipped_video_ids(history_file: Path = DEFAULT_HISTORY_FILE) -> Set[str]:
    """Retrieves all source video IDs that have already been clipped."""
    clipped = set()
    if not history_file.exists():
        return clipped
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for item in data:
                # Track source_video_id if recorded, or fallback to video_id
                src_id = item.get("source_video_id")
                if src_id:
                    clipped.add(src_id)
                # Also track any direct video_id recorded
                vid_id = item.get("video_id")
                if vid_id and not str(vid_id).startswith("dry_run"):
                    clipped.add(vid_id)
    except Exception as e:
        log(f"Warning: Failed to parse history file {history_file}: {e}")
    return clipped


def fetch_creator_uploads_ytdlp(channel_handle: str, max_results: int = 10, timeout: int = 15) -> List[Dict[str, Any]]:
    """
    Fetches real uploads using yt-dlp flat-playlist extraction.
    Fast, highly reliable, and returns genuine YouTube video IDs.
    """
    import subprocess
    clean_handle = channel_handle.strip().lstrip("@")
    url = f"https://www.youtube.com/@{clean_handle}/videos"
    cmd = [
        "yt-dlp", "--flat-playlist",
        "--print", "%(id)s|%(title)s|%(upload_date)s",
        url,
        "-I", f":{max_results}"
    ]
    uploads = []
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.strip().splitlines():
                parts = line.split("|")
                if len(parts) >= 2:
                    vid_id = parts[0].strip()
                    title = parts[1].strip()
                    pub_str = parts[2].strip() if len(parts) > 2 else ""
                    if vid_id and title:
                        uploads.append({
                            "video_id": vid_id,
                            "title": title,
                            "published_at": pub_str,
                            "url": f"https://www.youtube.com/watch?v={vid_id}",
                        })
    except Exception as e:
        log(f"Warning: yt-dlp flat playlist fetch failed for @{clean_handle}: {e}")
    return uploads


def fetch_creator_uploads_rss(channel_id: str, timeout: int = 8) -> List[Dict[str, Any]]:
    """
    Fetches the 15 latest uploads for a channel via YouTube XML RSS feed.
    Zero API quota, zero credentials required, ordered strictly newest-to-oldest.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    uploads = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
            }
            for entry in root.findall("atom:entry", ns):
                vid_elem = entry.find("yt:videoId", ns)
                title_elem = entry.find("atom:title", ns)
                pub_elem = entry.find("atom:published", ns)
                if vid_elem is not None and title_elem is not None:
                    vid_id = vid_elem.text
                    title = title_elem.text
                    published_at = pub_elem.text if pub_elem is not None else ""
                    uploads.append({
                        "video_id": vid_id,
                        "title": title,
                        "published_at": published_at,
                        "url": f"https://www.youtube.com/watch?v={vid_id}",
                    })
    except Exception as e:
        log(f"Warning: RSS fetch failed for channel {channel_id}: {e}")
    return uploads


def format_jack_craig_hook(raw_title: str, creator_info: Dict[str, Any]) -> str:
    """Formats title into Jack Craig's Question Hook + Shock Emojis + Handle."""
    base_title = raw_title.strip()
    # Strip any existing parenthetical handles, hashtags, or emojis
    clean_title = re.sub(r"\s*\(@[^)]+\)", "", base_title)
    clean_title = re.sub(r"[\U00010000-\U0010ffff]", "", clean_title)
    clean_title = clean_title.replace("#shorts", "").strip()

    # Formulate question hook if not already phrased as question
    creator_display_name = creator_info.get("name", "They").split("/")[0].strip()
    if not clean_title.endswith("?"):
        if any(clean_title.lower().startswith(q) for q in ["can ", "did ", "how ", "why ", "what ", "who ", "will ", "is "]):
            hook = f"{clean_title}?"
        elif re.match(r"^[A-Z][a-z]+ing\b", clean_title):
            # Gerund titles read badly as "Can {name} Making...?" — keep the
            # gerund and make the creator the subject: "NileBlue Turning Milk Into Plastic?"
            hook = f"{creator_display_name} {clean_title}?"
        elif re.match(r"^I\s", clean_title):
            # First-person titles: "I Made a Working Lightsaber" -> "NileBlue Made a Working Lightsaber?"
            hook = f"{creator_display_name} {clean_title[1:].strip()}?"
        else:
            hook = f"Can {creator_display_name} {clean_title}?"
    else:
        hook = clean_title

    packaging = creator_info.get("packaging", {})
    emojis = packaging.get("shock_emojis", "😳😳")
    handle = creator_info.get("channel_handle", "@YouTube")

    candidate = f"{hook} {emojis} ({handle}/YT)"
    if len(candidate) > 95:
        if len(hook) > 70:
            # Truncate at a word boundary, never mid-word
            cut = hook[:70]
            if " " in cut:
                cut = cut[: cut.rfind(" ")]
            hook = cut + "…"
        candidate = f"{hook} {emojis} ({handle}/YT)"
    return candidate


# ---------------------------------------------------------------------------
# @TestedNProved packaging: declarative transformation hooks
# ---------------------------------------------------------------------------
# The reference channel never opens with a question. Every title states the
# transformation payoff as a completed act and credits the source creator:
#   "He Turned Air Into Drinkable Alcohol 😳 (@NileRed/YT)"
#   "This Guy Turned Cotton Balls into Cotton Candy 😳 (@NileRed/YT)"
#   "He Condensed 20 beers into 1 Super Beer 😲 (@NileRed/YT)"
# Question hooks stay available as a fallback for titles that are not a
# transformation, so no source video is left without packaging.

_PAST_TENSE = {
    "turn": "Turned", "make": "Made", "build": "Built", "create": "Created",
    "condense": "Condensed", "compress": "Compressed", "distill": "Distilled",
    "extract": "Extracted", "refine": "Refined", "convert": "Converted",
    "grow": "Grew", "cook": "Cooked", "brew": "Brewed", "melt": "Melted",
    "freeze": "Froze", "dissolve": "Dissolved", "purify": "Purified",
    "separate": "Separated", "combine": "Combined", "mix": "Mixed",
    "burn": "Burned", "explode": "Exploded", "engineer": "Engineered",
    "invent": "Invented", "reverse": "Reversed", "transform": "Transformed",
    "recycle": "Recycled", "harvest": "Harvested", "synthesize": "Synthesized",
}
_PAST_VERB_FORMS = {v.lower(): v for v in _PAST_TENSE.values()}
_EMOJI_RE = re.compile("[\U00010000-\U0010ffff]")


def _strip_packaging(raw_title: str) -> str:
    """Removes creator handles, hashtags, emojis and parentheticals from a title."""
    clean = re.sub(r"\s*\(@[^)]*\)", " ", raw_title or "")
    clean = re.sub(r"[\(\[]([^\)\]]*)[\)\]]", " ", clean)
    clean = re.sub(r"#\w+", " ", clean)
    clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—|:;,.")
    return clean


def _gerund_base(word: str) -> str:
    """Reduces an -ing verb to its dictionary form (turning -> turn, making -> make)."""
    stem = word[:-3].lower()
    candidates = [stem, stem + "e"]
    if len(stem) > 2 and stem[-1] == stem[-2]:
        candidates.append(stem[:-1])
    for cand in candidates:
        if cand in _PAST_TENSE:
            return cand
    return stem


def _past_verb(word: str) -> Optional[str]:
    """Resolves a present, gerund or already-past verb to its past-tense form."""
    w = word.lower().strip("!,.")
    if w in _PAST_VERB_FORMS:
        return _PAST_VERB_FORMS[w]
    if w in _PAST_TENSE:
        return _PAST_TENSE[w]
    if w.endswith("ing") and len(w) > 4:
        return _PAST_TENSE.get(_gerund_base(w))
    return None


def _declarative_hook(clean_title: str) -> Optional[str]:
    """Rewrites a source title into the channel's 'He <verbed> ...' hook.

    Returns None when the title does not describe a transformation, letting the
    caller fall back to the question-hook packaging.
    """
    if not clean_title:
        return None

    # "Turning X into Y" / "Making X" -> "He Turned X into Y" / "He Made X"
    m = re.match(r"^([A-Za-z]+ing)\b(.*)$", clean_title)
    if m:
        past = _past_verb(m.group(1))
        return f"He {past}{m.group(2)}" if past else None

    # "I Made a Working Lightsaber" -> "He Made a Working Lightsaber"
    m = re.match(r"^(?:I)\s+([A-Za-z]+)\b(.*)$", clean_title)
    if m:
        past = _past_verb(m.group(1))
        return f"He {past}{m.group(2)}" if past else None

    m = re.match(r"^We\s+([A-Za-z]+)\b(.*)$", clean_title)
    if m:
        past = _past_verb(m.group(1))
        return f"They {past}{m.group(2)}" if past else None

    # Already declarative ("He Condensed 20 beers into 1 Super Beer")
    m = re.match(r"^(He|She|They|This guy|This man)\s+([A-Za-z]+)\b(.*)$", clean_title, re.IGNORECASE)
    if m:
        subject = m.group(1).lower()
        pronoun = {"she": "She", "they": "They"}.get(subject, "He")
        past = _past_verb(m.group(2))
        return f"{pronoun} {past}{m.group(3)}" if past else None

    return None


def _first_emoji(emojis: str) -> str:
    found = _EMOJI_RE.findall(emojis or "")
    return found[0] if found else ""


def format_tested_proved_hook(raw_title: str, creator_info: Dict[str, Any]) -> str:
    """Packages a source title the way @TestedNProved does.

    Declarative transformation -> "He Turned X Into Y 😳 (@NileRed/YT)".
    Anything else keeps the question hook + shock emojis + attribution.
    """
    clean_title = _strip_packaging(raw_title)
    hook = _declarative_hook(clean_title)
    if not hook:
        return format_jack_craig_hook(raw_title, creator_info)

    emoji = _first_emoji(creator_info.get("packaging", {}).get("shock_emojis", "😳"))
    handle = creator_info.get("channel_handle", "@YouTube")
    suffix = f" {emoji} ({handle}/YT)" if emoji else f" ({handle}/YT)"

    max_hook = max(95 - len(suffix), 20)
    if len(hook) > max_hook:
        cut = hook[:max_hook]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        hook = cut.rstrip() + "…"
    return f"{hook}{suffix}"


def hook_text(raw_title: str) -> str:
    """Spoken/plain hook without emojis or handle — safe for TTS narration."""
    clean_title = _strip_packaging(raw_title)
    return _declarative_hook(clean_title) or clean_title


# ---------------------------------------------------------------------------
# Hook -> Payoff six-stage script
# ---------------------------------------------------------------------------
# Every deliverable walks the full Completion Compulsion curve in order, so the
# on-screen captions, HUD badges and upload description all describe the same
# arc that the renderer actually cuts: HOOK, RISING ACTION, CONFLICT, COMEBACK,
# RISING ACTION, PAYOFF. Stage copy is derived from the real source title, so a
# Kibble clip never carries Red Bull captions.

_TRANSFORM_RE = re.compile(
    r"^(?P<lead>.*?)\b(?:turn|turned|turning|make|made|making|condense|condensed|"
    r"condensing|compress|compressed|compressing|convert|converted|converting|"
    r"build|built|building)\b\s+(?P<x>.+?)\s+into\s+(?P<y>.+)$",
    re.IGNORECASE,
)


def _split_transformation(clean_title: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (X, Y) from 'Turning X into Y' style titles."""
    m = _TRANSFORM_RE.match(clean_title or "")
    if not m:
        return None, None
    return (m.group("x").strip() or None), (m.group("y").strip() or None)


def _arc_windows(total: float = 60.0, has_narration: bool = True) -> List[Tuple[float, float]]:
    """Output-timeline window for each arc stage, mirroring plan_arc_cuts()."""
    if has_narration:
        remaining = total - ARC_INTRO_SECONDS
        rest_weight = sum(w for _, w in ARC_STAGES[1:])
        durs = [ARC_INTRO_SECONDS] + [remaining * w / rest_weight for _, w in ARC_STAGES[1:]]
    else:
        total_weight = sum(w for _, w in ARC_STAGES)
        durs = [total * w / total_weight for _, w in ARC_STAGES]

    windows: List[Tuple[float, float]] = []
    cursor = 0.0
    for dur in durs:
        windows.append((cursor, cursor + dur))
        cursor += dur
    return windows


def _split_caption(text: str, max_chars: int = 34) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _distribute(lines: List[str], start: float, end: float) -> List[List[Any]]:
    if not lines:
        return []
    step = (end - start) / len(lines)
    cues: List[List[Any]] = []
    for i, line in enumerate(lines):
        cue_start = start + i * step + 0.10
        cue_end = start + (i + 1) * step - 0.08
        if cue_end - cue_start < 0.30:
            cue_start, cue_end = start + i * step, start + (i + 1) * step
        cues.append([round(cue_start, 2), round(cue_end, 2), line])
    return cues


def _stage_captions(hook: str, subject: Optional[str], result: Optional[str]) -> List[str]:
    return [
        hook.upper(),
        "STEP ONE: THE SETUP",
        "THE HARD PART",
        "HERE'S THE FIX",
        "FINAL STEP",
        f"THE RESULT: {result.upper()}" if result else "THE RESULT",
    ]


def _stage_badges(prefix: str, subject: Optional[str], result: Optional[str]) -> List[str]:
    route = (
        f"{prefix} {subject.upper()} → {result.upper()}"
        if subject and result
        else f"{prefix} TRANSFORMATION"
    )
    return [
        route,
        f"{prefix} STEP 1 | SETUP",
        f"{prefix} STEP 2 | REACTION",
        f"{prefix} STEP 3 | MIDWAY",
        f"{prefix} STEP 4 | FINAL RUN",
        f"{prefix} RESULT",
    ]


def build_stage_script(
    source_title: str,
    formatted_title: str,
    creator_info: Optional[Dict[str, Any]] = None,
    total: float = 60.0,
    has_narration: bool = True,
) -> Dict[str, Any]:
    """Builds the per-video hook->payoff script consumed by the renderer."""
    creator_info = creator_info or {}
    clean_title = _strip_packaging(source_title)
    hook = _declarative_hook(clean_title) or clean_title or _strip_packaging(formatted_title)
    subject, result = _split_transformation(clean_title)
    prefix = (creator_info.get("hud_theme", {}) or {}).get("badge_prefix", "🧪")

    captions = _stage_captions(hook, subject, result)
    badge_texts = _stage_badges(prefix, subject, result)
    windows = _arc_windows(total=total, has_narration=has_narration)

    cues: List[List[Any]] = []
    badges: List[List[Any]] = []
    stages: List[Dict[str, Any]] = []
    for i, ((stage, _weight), (start, end)) in enumerate(zip(ARC_STAGES, windows)):
        caption, badge = captions[i], badge_texts[i]
        cues.extend(_distribute(_split_caption(caption), start, end))
        badges.append([round(start, 2), round(end, 2), badge])
        stages.append({
            "stage": stage,
            "start": round(start, 2),
            "end": round(end, 2),
            "caption": caption,
            "badge": badge,
        })

    return {
        "title": formatted_title,
        "hook": hook,
        "subject": subject,
        "result": result,
        "cues": cues,
        "badges": badges,
        "stages": stages,
    }


def build_metadata(
    source_title: str,
    formatted_title: str,
    creator_info: Dict[str, Any],
    script: Dict[str, Any],
) -> Dict[str, Any]:
    """Builds the per-run metadata.json (title, description, arc breakdown)."""
    hook = script["hook"]
    creator_name = creator_info.get("name", "the creator")
    packaging = creator_info.get("packaging", {}) or {}
    hashtags = packaging.get("hashtags", ["#shorts"])
    handle = creator_info.get("channel_handle", "@YouTube")

    stages: Dict[str, str] = {}
    for i, stage in enumerate(script["stages"]):
        key = "intro_title" if i == 0 else f"stage_{i}_{stage['stage'].lower().replace(' ', '_')}"
        stages[key] = f"{stage['start']:.2f}s - {stage['end']:.2f}s: {stage['caption']}"

    return {
        "title": formatted_title,
        "description": (
            f"{hook}. Watch {creator_name}'s full transformation cut down to the hook, "
            f"the hard part and the payoff."
        ),
        "tags": [tag.lstrip("#") for tag in hashtags],
        "madeForKids": False,
        "creator_attribution": f"{handle}/YT",
        "source_title": source_title,
        "aspect_ratio": "3:4",
        "resolution": "1080x1440",
        "duration_seconds": 60.0,
        "arc_type": (
            "Tested&Proved 6-Stage Completion Compulsion Curve "
            "(Hook -> Rising Action -> Conflict -> Comeback -> Rising Action -> Payoff)"
        ),
        "stages": stages,
        "hook": hook,
        "loop_trigger": (
            f"{script['stages'][-1]['caption']} lands at the end of the Short and "
            f"loops seamlessly back to '{hook}'."
        ),
        "intro_narration": {
            "enabled": True,
            "voice": creator_info.get("narrator_voice", "en-US-BrianMultilingualNeural"),
            "spoken_text": hook,
            "duration_seconds": ARC_INTRO_SECONDS,
        },
    }


def write_run_artifacts(
    selection: Dict[str, Any],
    creator_info: Dict[str, Any],
    script_path: Path = Path(".run_script.json"),
    metadata_path: Path = Path("metadata.json"),
    total: float = 60.0,
    has_narration: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Writes the renderer script and upload metadata for the selected video."""
    source_title = selection["source_video"]["title"]
    formatted_title = selection["formatted_title"]

    script = build_stage_script(
        source_title, formatted_title, creator_info, total=total, has_narration=has_narration
    )
    metadata = build_metadata(source_title, formatted_title, creator_info, script)

    Path(script_path).write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(metadata_path).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"🎯 Hook->Payoff script written: {script_path} | Metadata: {metadata_path}")
    return script, metadata


def select_target_upload(
    creator_id: str,
    history_file: Path = DEFAULT_HISTORY_FILE,
    catalog_file: Path = DEFAULT_CATALOG_FILE,
    mock_uploads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Selects the next video for a creator following the algorithm:
    1. If the latest video is unclipped -> selects it (fresh release priority).
    2. Else -> crawls backwards through older videos to find the first unclipped older video.
    """
    catalog = load_creator_catalog(catalog_file)
    creators = catalog.get("creators", {})
    if creator_id not in creators:
        raise ValueError(f"Creator '{creator_id}' not found in catalog {catalog_file}")

    creator_info = creators[creator_id]
    channel_id = creator_info.get("channel_id")
    creator_name = creator_info.get("name", creator_id)

    # 1. Fetch channel uploads (newest to oldest): yt-dlp first, then RSS fallback
    handle = creator_info.get("channel_handle", f"@{creator_id}")
    if mock_uploads is not None:
        uploads = mock_uploads
    else:
        uploads = fetch_creator_uploads_ytdlp(handle)
        if not uploads and channel_id:
            uploads = fetch_creator_uploads_rss(channel_id)

    # Fallback to sample topics from catalog if network fetch yields no results
    if not uploads:
        sample_topics = creator_info.get("sample_topics", ["Viral Challenge"])
        uploads = [
            {
                "video_id": f"catalog_sample_{i}",
                "title": topic,
                "published_at": datetime.now().isoformat(),
                "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(topic)}",
            }
            for i, topic in enumerate(sample_topics)
        ]

    clipped_ids = get_clipped_video_ids(history_file)

    # 2. Check latest video (index 0)
    latest_video = uploads[0]
    is_latest_unclipped = latest_video["video_id"] not in clipped_ids

    if is_latest_unclipped:
        selected_video = latest_video
        selection_mode = "fresh_latest_upload"
        backlog_index = 0
        reason = (
            f"🔥 Fresh latest upload detected for {creator_name}! "
            f"'{selected_video['title']}' ({selected_video['video_id']}) is brand new. "
            f"Clipping latest release with highest priority!"
        )
    else:
        # 3. Crawl backwards towards older videos
        selected_video = None
        selection_mode = "backlog_older_video"
        backlog_index = -1
        for idx, vid in enumerate(uploads[1:], start=1):
            if vid["video_id"] not in clipped_ids:
                selected_video = vid
                backlog_index = idx
                reason = (
                    f"📼 Backlog crawl for {creator_name}: Latest video ({latest_video['video_id']}) already clipped. "
                    f"Marching backwards to unclipped older video #{idx}: '{selected_video['title']}' ({selected_video['video_id']})."
                )
                break

        # If all scanned uploads have been clipped, cycle back to the earliest or latest
        if selected_video is None:
            selected_video = uploads[0]
            selection_mode = "all_clipped_recycle"
            backlog_index = 0
            reason = (
                f"🔄 All {len(uploads)} scanned uploads for {creator_name} have been previously clipped. "
                f"Recycling latest upload '{selected_video['title']}' with fresh packaging variation."
            )

    packaged_title = format_tested_proved_hook(selected_video["title"], creator_info)
    narration_text = hook_text(selected_video["title"])
    svml_source = creator_info.get("svml_source", "sources/mrbeast_challenge.svml")
    output_filename = f"hypit_{creator_id}_{selected_video['video_id'][:8]}.mp4"

    result = {
        "creator_id": creator_id,
        "creator_name": creator_name,
        "channel_id": channel_id,
        "channel_handle": creator_info.get("channel_handle"),
        "selection_mode": selection_mode,
        "is_latest_unclipped": is_latest_unclipped,
        "backlog_index": backlog_index,
        "total_uploads_scanned": len(uploads),
        "source_video": selected_video,
        "formatted_title": packaged_title,
        "narration_text": narration_text,
        "svml_source": svml_source,
        "output_filename": output_filename,
        "hud_theme": creator_info.get("hud_theme", {}),
        "packaging": creator_info.get("packaging", {}),
        "reason": reason,
    }

    log(reason)
    log(f"🎬 Deliverable Title: '{packaged_title}' | Output: {output_filename}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Creator Backlog Crawler & Fresh Upload Prioritization Engine")
    parser.add_argument("--creator", default="mrbeast", help="Target creator identifier (e.g. mrbeast, nileblue)")
    parser.add_argument("--history", default=str(DEFAULT_HISTORY_FILE), help="Path to upload_history.json")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_FILE), help="Path to creator_catalog.json")
    parser.add_argument("--json", action="store_true", help="Output full selection result as JSON")
    args = parser.parse_args()

    history_path = Path(args.history).resolve()
    catalog_path = Path(args.catalog).resolve()

    res = select_target_upload(
        creator_id=args.creator,
        history_file=history_path,
        catalog_file=catalog_path,
    )

    if args.json:
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
