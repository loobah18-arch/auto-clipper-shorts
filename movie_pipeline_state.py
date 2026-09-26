from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class RunStatus(StrEnum):
    RENDERED = "rendered"
    UPLOADED = "uploaded"
    FAILED = "failed"


class HistoryError(Exception):
    """Base error for persisted movie state failures."""


class HistoryWriteError(HistoryError):
    """Raised when movie state cannot be persisted safely."""


class DuplicateEpisodeError(HistoryError):
    """Raised when an already-uploaded episode would be published again."""

    def __init__(self, episode_key: str) -> None:
        self.episode_key = episode_key
        super().__init__(f"Episode already uploaded: {episode_key}")


@dataclass(frozen=True, slots=True)
class RenderManifest:
    """Immutable metadata written alongside every cloud render."""

    movie_id: str
    title: str
    part_number: int | None
    total_parts: int | None
    language: str
    status: str
    duration_sec: float
    youtube_id: str | None
    video_file: str
    thumbnail_file: str | None
    subtitle_file: str | None
    script_sha256: str
    video_sha256: str | None
    source_type: str
    generated_at: str

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible manifest."""
        return dict(asdict(self))


def utc_now() -> str:
    """Return an RFC 3339-compatible UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def episode_key(movie_id: str, part_number: int | None) -> str:
    """Build the stable identity used for duplicate protection."""
    return f"{movie_id}:p{part_number}" if part_number is not None else f"{movie_id}:standalone"


def stable_seed(*parts: str) -> int:
    """Derive a deterministic seed without Python's randomized hash."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def sha256_file(path: Path) -> str:
    """Hash a generated file without loading it entirely into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_dict(value: JsonValue) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def load_history(path: Path) -> dict[str, JsonValue]:
    """Load and normalize history, failing closed on malformed state."""
    if not path.exists():
        return {"uploaded_movies": []}

    try:
        with path.open("r", encoding="utf-8") as file_handle:
            raw: JsonValue = json.load(file_handle)
    except (OSError, json.JSONDecodeError) as error:
        raise HistoryError(f"Unable to read movie history at {path}") from error

    root = _as_dict(raw)
    if root is None:
        raise HistoryError(f"Movie history must contain a JSON object: {path}")

    raw_entries = root.get("uploaded_movies", [])
    if not isinstance(raw_entries, list):
        raise HistoryError(f"Movie history uploaded_movies must be a list: {path}")

    normalized: list[JsonValue] = []
    for raw_entry in raw_entries:
        entry = _as_dict(raw_entry)
        if entry is None:
            continue
        normalized_entry = dict(entry)
        if not isinstance(normalized_entry.get("status"), str):
            youtube_id = normalized_entry.get("youtube_id")
            normalized_entry["status"] = (
                RunStatus.UPLOADED.value
                if isinstance(youtube_id, str) and youtube_id
                else RunStatus.RENDERED.value
            )
        normalized.append(normalized_entry)

    result = dict(root)
    result["uploaded_movies"] = normalized
    return result


def save_history(path: Path, history: dict[str, JsonValue]) -> None:
    """Persist history atomically so a failed write cannot corrupt state."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file_handle:
            json.dump(history, file_handle, indent=2, ensure_ascii=False)
            file_handle.write("\n")
        temporary_path.replace(path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise HistoryWriteError(f"Unable to save movie history at {path}") from error


def entry_is_uploaded(entry: JsonValue) -> bool:
    """Return whether a history entry represents a confirmed upload."""
    record = _as_dict(entry)
    if record is None:
        return False
    youtube_id = record.get("youtube_id")
    status = record.get("status")
    return (
        status == RunStatus.UPLOADED.value
        and isinstance(youtube_id, str)
        and bool(youtube_id.strip())
    )


def uploaded_part_numbers(history: dict[str, JsonValue], movie_id: str) -> set[int]:
    """Return only parts with a confirmed YouTube video ID."""
    entries = history.get("uploaded_movies", [])
    if not isinstance(entries, list):
        return set()
    parts: set[int] = set()
    for entry in entries:
        record = _as_dict(entry)
        if record is None or not entry_is_uploaded(entry):
            continue
        if record.get("movie_id") != movie_id:
            continue
        part_number = record.get("part_number")
        if isinstance(part_number, int):
            parts.add(part_number)
    return parts


def uploaded_movie_ids(history: dict[str, JsonValue]) -> set[str]:
    """Return standalone movies with confirmed uploads."""
    entries = history.get("uploaded_movies", [])
    if not isinstance(entries, list):
        return set()
    return {
        str(record["movie_id"])
        for entry in entries
        if entry_is_uploaded(entry)
        and (record := _as_dict(entry)) is not None
        and isinstance(record.get("movie_id"), str)
        and record.get("part_number") is None
    }


def append_history_entry(history: dict[str, JsonValue], entry: dict[str, JsonValue]) -> None:
    """Append a run event unless the episode is already confirmed uploaded."""
    entries_value = history.get("uploaded_movies", [])
    if not isinstance(entries_value, list):
        raise HistoryError("Movie history uploaded_movies must be a list")

    movie_id = entry.get("movie_id")
    part_number = entry.get("part_number")
    if not isinstance(movie_id, str) or not movie_id:
        raise HistoryError("History entry requires movie_id")
    if part_number is not None and not isinstance(part_number, int):
        raise HistoryError("History entry part_number must be an integer or null")

    key = episode_key(movie_id, part_number)
    if not entry_is_uploaded(entry):
        history["uploaded_movies"] = [*entries_value, dict(entry)]
        return

    for existing in entries_value:
        existing_record = _as_dict(existing)
        if existing_record is None or not entry_is_uploaded(existing):
            continue
        existing_key = episode_key(
            str(existing_record.get("movie_id", "")),
            existing_record.get("part_number") if isinstance(existing_record.get("part_number"), int) else None,
        )
        if existing_key == key:
            raise DuplicateEpisodeError(key)

    history["uploaded_movies"] = [*entries_value, dict(entry)]


def update_series_progress(
    history: dict[str, JsonValue],
    movie_id: str,
    movie_title: str,
    part_number: int,
    total_parts: int,
) -> None:
    """Advance series state only after a confirmed upload."""
    history["current_series"] = {
        "movie_id": movie_id,
        "movie_title": movie_title,
        "current_part": part_number,
        "total_parts": total_parts,
        "completed": part_number >= total_parts,
        "last_updated": utc_now(),
    }


def write_manifest(path: Path, manifest: RenderManifest) -> None:
    """Write a render manifest atomically for artifact review."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file_handle:
            json.dump(manifest.to_dict(), file_handle, indent=2, ensure_ascii=False)
            file_handle.write("\n")
        temporary_path.replace(path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise HistoryWriteError(f"Unable to write render manifest at {path}") from error
