#!/usr/bin/env python3
"""
Automatic catalog ingestion from a Google Drive folder.

Instead of hand-writing a catalog entry for every new movie, anime, or web
series, this module enumerates a Drive folder and registers anything it does not
already know about. Narration scripts are produced by the existing AI script
engine and cached, so each title is only ever "written" once.

Design notes:
- Curated entries always win. Auto-generated entries live in a separate file
  (movie_catalog_auto.json) and never touch movie_catalog.json, so hand-tuned
  series such as The Avengers keep their 8 scripted parts.
- Part windows are stored as FRACTIONS of the runtime, not HH:MM:SS. The real
  duration is unknown until the file is downloaded, so the fractions are
  resolved against the probed duration at slice time.
- Files that look episodic (E01, S01E02, " 01 ") are grouped into one series
  with one part per episode. Anything else is treated as a single movie and
  split into evenly spaced parts.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
AUTO_CATALOG_PATH = WORKSPACE_DIR / "movie_catalog_auto.json"

# Fraction of the runtime skipped at the head (logos/recap) and tail (credits).
WINDOW_HEAD_SKIP = 0.04
WINDOW_TAIL_SKIP = 0.96

# A standalone movie file is chopped into this many parts by default.
DEFAULT_MOVIE_PARTS = 6

# How many parts may gain a script in a single run. Bulk backfill (an anime with
# 50+ episodes) is done incrementally so one run cannot exhaust the job timeout
# or trip provider rate limits; later runs continue where this one stopped.
MAX_PARTS_PER_RUN = 6

VIDEO_SUFFIXES = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts")

_EPISODE_PATTERNS = (
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})(?!\d)"),                          # S01E02
    re.compile(r"(?:^|[^A-Za-z])[Ee][Pp]?[Ii]?[Ss]?[Oo]?[Dd]?(\d{1,3})(?!\d)"),  # EP01 / Ep105
    re.compile(r"(?:^|[\s._-])(\d{1,3})(?:v\d)?(?=[\s._-]|$)"),               # " 01 " / "_01."
)

_EPISODE_WORD = re.compile(r"\b(episode|ep|episodio|part)\b\s*\d*", re.I)

_NOISE_TOKENS = frozenset({
    "bluray", "blu", "ray", "x264", "x265", "h264", "h265", "hevc", "bdrip",
    "brrip", "webrip", "webdl", "hdrip", "dvdrip", "remux", "proper",
    "repack", "internal", "limited", "extended", "unrated", "remastered",
    "multi", "subs", "dubbed", "dual", "audio", "10bit", "8bit", "hdr",
    "dolby", "atmos", "www", "com", "org", "net", "hd", "to", "in",
    "hindi", "english", "eng", "hin", "tam", "tel", "mal", "kan",
    "1080p", "720p", "480p", "2160p", "4k", "esub", "sub",
    "web", "webdl", "dl", "ddp", "ddp5", "aac", "ac3", "eac3", "truehd",
    "hdtv", "nf", "amzn", "hulu", "disney", "max", "docu",
})


def _normalise_title(value: str) -> str:
    """Alphanumeric-only lowercase form, for tolerant title comparison."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def log(message: str) -> None:
    print(f"[auto_catalog] {message}", flush=True)


def is_video(filename: str) -> bool:
    return filename.lower().endswith(VIDEO_SUFFIXES)


def detect_episode_number(filename: str) -> int | None:
    """Return an episode number if the filename looks episodic, else None."""
    stem = Path(filename).stem
    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        groups = [group for group in match.groups() if group]
        if not groups:
            continue
        try:
            number = int(groups[-1])
        except ValueError:
            continue
        # Reject 4-digit years such as "Avengers.2012".
        if 0 < number < 1000:
            return number
    return None


_SEASON_EPISODE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})(?!\d)")

_COPY_PREFIX = re.compile(r"^\s*copy\s+of\s+", re.I)

# Watermark / tracker domains that survive splitting on dots.
_WATERMARK = re.compile(r"^[a-z0-9]+$")


def _strip_copy_prefix(filename: str) -> str:
    """Drop the 'Copy of ' prefix Google Drive adds to duplicated files."""
    return _COPY_PREFIX.sub("", Path(filename).name)


def _significant_tokens(text: str) -> list[str]:
    """Split text into meaningful words, dropping release/quality noise."""
    stem = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", text)
    stem = re.sub(r"[\s._\-]+", " ", stem)
    for pattern in _EPISODE_PATTERNS:
        stem = pattern.sub(" ", stem)
    stem = _EPISODE_WORD.sub(" ", stem)
    return [
        token for token in stem.split()
        if token.lower() not in _NOISE_TOKENS
        and not re.fullmatch(r"\d{1,3}", token)
        and not (len(token) > 3 and token.isalpha() and token.isupper() and "PIKAHD" in token)
    ] or stem.split()


def parse_season_episode(filename: str) -> tuple[int | None, int | None, str]:
    """Return (season, episode, series_prefix) for a release filename.

    The series name is taken from the text BEFORE the episode marker, so quality
    tags and watermarks that follow ("-720p (BDRip) PIKAHD.COM") cannot leak into
    the group identity. This is what keeps S01/S02/S03 of one anime together.
    """
    name = _strip_copy_prefix(filename)
    stem = Path(name).stem

    season_match = _SEASON_EPISODE.search(stem)
    if season_match:
        prefix = stem[: season_match.start()]
        return int(season_match.group(1)), int(season_match.group(2)), prefix

    for pattern in _EPISODE_PATTERNS[1:]:
        match = pattern.search(stem)
        if match:
            prefix = stem[: match.start()]
            groups = [group for group in match.groups() if group]
            if groups:
                try:
                    return None, int(groups[-1]), prefix
                except ValueError:
                    break
    return None, None, stem


def series_stem(filename: str) -> str:
    """Series identity for grouping; ignores everything after the episode tag."""
    _season, _episode, prefix = parse_season_episode(filename)
    return " ".join(_significant_tokens(prefix)).strip()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "untitled"


def humanize(filename: str) -> str:
    """Turn a release filename into a readable title."""
    _season, _episode, prefix = parse_season_episode(filename)
    return " ".join(_significant_tokens(prefix)).strip().title()


def plan_windows(part_count: int) -> list[tuple[float, float]]:
    """Split the usable runtime span into equal fractional windows."""
    span = WINDOW_TAIL_SKIP - WINDOW_HEAD_SKIP
    if part_count <= 1:
        return [(WINDOW_HEAD_SKIP, WINDOW_TAIL_SKIP)]
    step = span / part_count
    return [
        (
            round(WINDOW_HEAD_SKIP + index * step, 4),
            round(WINDOW_HEAD_SKIP + (index + 1) * step, 4),
        )
        for index in range(part_count)
    ]


def enumerate_drive_folder(folder_id: str) -> list[tuple[str, str]]:
    """Return [(filename, file_id)] for a publicly shared Drive folder."""
    try:
        import gdown
    except ImportError as error:
        log(f"gdown is not installed: {error}")
        return []

    try:
        entries = gdown.download_folder(
            id=folder_id,
            output=str(WORKSPACE_DIR / "cache" / "_gdrive_listing"),
            quiet=True,
            skip_download=True,
            use_cookies=False,
        )
    except Exception as error:  # noqa: BLE001 - network/permission errors vary
        log(f"Could not enumerate folder {folder_id}: {type(error).__name__}: {error}")
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries or []:
        raw_name = Path(str(getattr(entry, "path", "") or "")).name
        file_id = str(getattr(entry, "id", "") or "")
        # 'Copy of ' duplicates collapse onto the original name.
        name = _strip_copy_prefix(raw_name)
        if not name or not file_id or not is_video(name) or name in seen:
            continue
        seen.add(name)
        results.append((name, file_id))
    return results


def load_auto_catalog() -> dict:
    """Load the generated catalog, tolerating a missing or broken file."""
    if not AUTO_CATALOG_PATH.exists():
        return {"movies": []}
    try:
        with AUTO_CATALOG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        log(f"Ignoring unreadable {AUTO_CATALOG_PATH.name}: {error}")
        return {"movies": []}
    movies = data.get("movies")
    return {"movies": movies} if isinstance(movies, list) else {"movies": []}


def save_auto_catalog(catalog: dict) -> None:
    """Persist the generated catalog atomically."""
    temporary = AUTO_CATALOG_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(AUTO_CATALOG_PATH)


def _generate_script(title: str, language: str) -> dict:
    """Generate a narration script for one part, degrading to an empty dict."""
    try:
        from movie_ai_script import generate_movie_script_ai
    except Exception as error:  # noqa: BLE001
        log(f"Script engine unavailable: {error}")
        return {}
    try:
        return generate_movie_script_ai(title, language=language) or {}
    except Exception as error:  # noqa: BLE001
        log(f"Script generation failed for '{title}': {type(error).__name__}: {error}")
        return {}


def build_entry(
    files: list[tuple[str, str, int | None]],
    entry_id: str,
    language: str = "en",
    movie_parts: int = DEFAULT_MOVIE_PARTS,
    max_parts: int | None = None,
    skip: int = 0,
) -> dict | None:
    """Build a catalog entry from (filename, file_id, episode) triples.

    ``max_parts`` caps how many parts get a generated script in this call and
    ``skip`` resumes a partially generated series, so a long show is filled in
    over several runs instead of one enormous batch.
    """
    if not files:
        return None

    ordered = sorted(files, key=lambda item: (item[2] is None, item[2] or 0))
    episodes = [number for _, _, number in ordered if number is not None]

    display_title = humanize(ordered[0][0])
    if episodes:
        display_title = _EPISODE_WORD.sub(" ", display_title).strip() or "Series"

    if episodes:
        season_of = {name: parse_season_episode(name)[0] for name, _fid, _ep in ordered}
        ordered = sorted(
            files,
            key=lambda item: (season_of.get(item[0]) or 0, item[2] or 0),
        )
        plan: list[tuple[str, str, int | None, int | None, str, tuple[float, float]]] = []
        for filename, file_id, episode_number in ordered:
            season = season_of.get(filename)
            label = (
                f"Season {season} Episode {episode_number}"
                if season else f"Episode {episode_number}"
            )
            plan.append((
                f"{display_title} - {label}", filename, file_id, episode_number,
                season, plan_windows(1)[0],
            ))
    else:
        windows = plan_windows(movie_parts)
        filename, file_id, _ = ordered[0]
        plan = [
            (
                f"{display_title} - Part {index}/{len(windows)}",
                filename, file_id, None, None, window,
            )
            for index, window in enumerate(windows, start=1)
        ]

    total_available = len(plan)
    start = max(0, skip)
    cap = max_parts if max_parts is not None else MAX_PARTS_PER_RUN
    if cap > 0:
        plan = plan[start:start + cap]
    else:
        plan = plan[start:]

    parts = [
        _build_part(
            display_title, part_title, index, total_available,
            filename, file_id, episode_number, season, window, language,
        )
        for index, (part_title, filename, file_id, episode_number, season, window)
        in enumerate(plan, start=start + 1)
    ]

    return {
        "id": entry_id,
        "title": display_title,
        "auto_generated": True,
        "source": "gdrive_folder",
        "badge": display_title[:28].upper(),
        "hook": parts[0]["hook"] if parts else "",
        "script": parts[0]["script"] if parts else "",
        "tags": parts[0]["tags"] if parts else ["movieexplained", "shorts"],
        "parts": parts,
        "parts_available": total_available,
        "parts_pending": max(0, total_available - (start + len(parts))),
    }


def _max_parts_per_run() -> int:
    """Per-run cap on how many parts may consume an AI call."""
    raw = os.environ.get("AUTO_INGEST_MAX_PARTS_PER_RUN", "").strip()
    if not raw:
        return MAX_PARTS_PER_RUN
    try:
        return max(0, int(raw))
    except ValueError:
        log(f"Ignoring invalid AUTO_INGEST_MAX_PARTS_PER_RUN={raw!r}")
        return MAX_PARTS_PER_RUN


def _allow_template_scripts() -> bool:
    """Opt-in escape hatch for registering titles without working AI access."""
    return os.environ.get("ALLOW_TEMPLATE_SCRIPTS", "false").lower() == "true"


def _build_part(
    display_title: str,
    part_title: str,
    part_number: int,
    total_parts: int,
    filename: str,
    file_id: str,
    episode_number: int | None,
    season: int | None,
    window: tuple[float, float],
    language: str,
) -> dict:
    """Assemble a single part, generating its narration script."""
    generated = _generate_script(part_title, language)
    return {
        "part_number": part_number,
        "title": part_title,
        "badge": str(generated.get("badge") or display_title)[:28],
        "hook": str(generated.get("hook") or f"What really happened in {part_title}?"),
        "script": str(generated.get("script") or ""),
        "script_source": str(generated.get("script_source") or "template"),
        "tags": list(generated.get("tags") or ["movieexplained", "shorts"]),
        "window_start_frac": window[0],
        "window_end_frac": window[1],
        "gdrive_file_id": file_id,
        "gdrive_file_name": filename,
        "season": season,
        "episode_number": episode_number,
    }


def _has_real_scripts(entry: dict) -> bool:
    """True when every part carries genuine AI narration, not the template."""
    return all(
        part.get("script_source") not in (None, "", "template")
        for part in entry.get("parts", [])
    )


def _title_matches_curated(filename: str, curated: set[str]) -> bool:
    """True when a Drive file clearly refers to an already-curated movie.

    Release-group suffixes ("KatmovieHD") are unbounded, so an exact title
    comparison is unreliable. Comparing the leading significant words tolerates
    that trailing junk while still allowing genuinely new titles through.
    """
    tokens = _significant_tokens(filename)
    if not tokens or not curated:
        return False
    for depth in (len(tokens), 3, 2):
        if depth <= 0 or depth > len(tokens):
            continue
        if _normalise_title(" ".join(tokens[:depth])) in curated:
            return True
    return False


def _curated_titles() -> set[str]:
    """Normalised titles of hand-curated catalog entries.

    Reads movie_catalog.json ONLY, never the merged view: an auto-registered
    title must not become "curated" and then block itself on later runs.
    Auto-ingest dedupes its own entries by id, so this guard exists purely to
    stop a second entry appearing for a hand-tuned movie.
    """
    try:
        from movie_ai_script import CATALOG_PATH, _read_catalog_file
    except Exception as error:  # noqa: BLE001
        log(f"Curated catalog unavailable: {error}")
        return set()

    titles: set[str] = set()
    for movie in _read_catalog_file(CATALOG_PATH):
        for field in ("title", "id"):
            value = movie.get(field)
            if isinstance(value, str) and value.strip():
                titles.add(_normalise_title(value))
                break
    return titles


def sync_auto_catalog(
    folder_id: str | None = None,
    language: str = "en",
    movie_parts: int = DEFAULT_MOVIE_PARTS,
) -> dict:
    """Discover Drive files, register unknown ones, and persist the result."""
    folder_id = (folder_id or os.environ.get("GDRIVE_FOLDER_ID", "")).strip()
    if not folder_id:
        log("GDRIVE_FOLDER_ID is not set; skipping auto-ingest.")
        return load_auto_catalog()

    auto_catalog = load_auto_catalog()
    known = {str(movie.get("id")) for movie in auto_catalog["movies"] if movie.get("id")}

    discovered = enumerate_drive_folder(folder_id)
    if not discovered:
        log(f"No video files discovered in folder {folder_id}.")
        return auto_catalog

    groups: dict[str, list[tuple[str, str, int | None]]] = {}
    for filename, file_id in discovered:
        key = slugify(series_stem(filename))
        groups.setdefault(key, []).append((filename, file_id, detect_episode_number(filename)))

    added = 0
    curated = _curated_titles()
    cap = _max_parts_per_run()
    for key, members in sorted(groups.items()):
        existing = next((m for m in auto_catalog["movies"] if m.get("id") == key), None)
        if existing is not None and _has_real_scripts(existing):
            pending = int(existing.get("parts_pending") or 0)
            if pending <= 0:
                continue
            log(f"Continuing '{key}': {pending} part(s) still need scripts.")
        elif existing is not None:
            log(f"Regenerating '{key}': previous scripts were placeholders.")
        sample = members[0][0]
        if _title_matches_curated(sample, curated):
            log(f"Skipping '{humanize(sample)}': already present in the curated catalog.")
            continue

        already = len(existing.get("parts", [])) if existing and _has_real_scripts(existing) else 0
        entry = build_entry(
            members, key, language=language, movie_parts=movie_parts,
            max_parts=cap, skip=already,
        )
        if not entry or not entry["parts"]:
            continue
        if not _has_real_scripts(entry) and not _allow_template_scripts():
            log(
                f"Not registering '{entry['title']}': no AI script provider is available, "
                "so every part would be placeholder narration. Fix the API keys and re-run."
            )
            if existing is not None:
                auto_catalog["movies"] = [
                    m for m in auto_catalog["movies"] if m.get("id") != key
                ]
                known.discard(key)
                log(f"Removed unusable '{key}' from the generated catalog.")
                added += 1
            continue

        if existing is not None and _has_real_scripts(existing):
            merged = dict(entry)
            merged["parts"] = list(existing["parts"]) + list(entry["parts"])
            for position, part in enumerate(merged["parts"], start=1):
                part["part_number"] = position
            auto_catalog["movies"] = [
                merged if m.get("id") == key else m for m in auto_catalog["movies"]
            ]
            log(
                f"Extended '{entry['title']}': {len(merged['parts'])} part(s) ready, "
                f"{merged['parts_pending']} still pending."
            )
        else:
            if existing is not None:
                auto_catalog["movies"] = [
                    m for m in auto_catalog["movies"] if m.get("id") != key
                ]
            auto_catalog["movies"].append(entry)
            log(f"Registered '{entry['title']}' with {len(entry['parts'])} part(s).")
        known.add(key)
        added += 1

    if added:
        save_auto_catalog(auto_catalog)
        log(f"Auto-ingest added {added} new title(s).")
    else:
        log("Auto-ingest found nothing new.")
    return auto_catalog


if __name__ == "__main__":
    result = sync_auto_catalog(language=sys.argv[1] if len(sys.argv) > 1 else "en")
    print(json.dumps({"movies": [m["id"] for m in result["movies"]]}, indent=2))
