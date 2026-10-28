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
    SCRIPT_READY = "script_ready"
    MEDIA_VALIDATION_REQUIRED = "media_validation_required"
    RENDERING = "rendering"
    TECHNICAL_QC_FAILED = "technical_qc_failed"
    DURATION_QC_FAILED = "duration_qc_failed"
    RIGHTS_REVIEW_REQUIRED = "rights_review_required"
    READY_FOR_MANUAL_REVIEW = "ready_for_manual_review"
    PUBLISH_READY = "publish_ready"
    RENDERED = "rendered"
    UPLOADED = "uploaded"
    FAILED = "failed"


class RightsStatus(StrEnum):
    AUTHORIZED = "authorized"
    LICENSED = "licensed"
    PUBLIC_DOMAIN_VERIFIED = "public_domain_verified"
    PERMISSION_UNKNOWN = "permission_unknown"
    RESTRICTED = "restricted"


@dataclass(frozen=True, slots=True)
class MediaRightsRecord:
    """Explicit media-rights provenance and clearance status."""
    source_type: str
    rights_status: RightsStatus
    license_id: str | None = None
    attribution: str | None = None
    requires_manual_review: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source_type": self.source_type,
            "rights_status": self.rights_status.value,
            "license_id": self.license_id,
            "attribution": self.attribution,
            "requires_manual_review": self.requires_manual_review,
            "notes": self.notes,
        }


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
    quality_score: float | None = None
    editing_manifest_file: str | None = None
    profile: str = "shorts"
    target_max_duration: float = 58.0
    rights_status: str = "permission_unknown"
    rights_review_required: bool = False
    technical_qc_passed: bool = True
    qc_failures: list[str] | None = None
    manual_review_required: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible manifest."""
        data = dict(asdict(self))
        if data.get("qc_failures") is None:
            data["qc_failures"] = []
        return data


def utc_now() -> str:
    """Return an RFC 3339-compatible UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def episode_key(movie_id: str, part_number: int | None, is_full_video: bool = False) -> str:
    """Build the stable identity used for duplicate protection."""
    if is_full_video:
        return f"{movie_id}:full_video"
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


def confirmed_part_durations(
    history: dict[str, JsonValue],
    movie_id: str,
    before_part: int,
) -> list[float]:
    """Durations of confirmed prior uploads for a series, in part order.

    Only entries with a real YouTube ID count. Failed and dry-run renders were
    never published, so including them would desync anything cumulative that
    depends on the published timeline (such as a continuous soundtrack).
    """
    entries = history.get("uploaded_movies", [])
    if not isinstance(entries, list):
        return []

    by_part: dict[int, float] = {}
    for entry in entries:
        record = _as_dict(entry)
        if record is None or not entry_is_uploaded(entry):
            continue
        if record.get("movie_id") != movie_id:
            continue
        part_number = record.get("part_number")
        duration = record.get("duration_sec")
        if (
            isinstance(part_number, int)
            and part_number < before_part
            and isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and duration > 0
        ):
            by_part[part_number] = float(duration)

    return [by_part[key] for key in sorted(by_part)]


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


def append_history_entry(
    history: dict[str, JsonValue],
    entry: dict[str, JsonValue],
    allow_duplicate: bool = False,
) -> None:
    """Append a run event unless the episode is already confirmed uploaded."""
    entries_value = history.get("uploaded_movies", [])
    if not isinstance(entries_value, list):
        raise HistoryError("Movie history uploaded_movies must be a list")

    movie_id = entry.get("movie_id")
    part_number = entry.get("part_number")
    is_full_video = bool(entry.get("is_full_video", False))
    if not isinstance(movie_id, str) or not movie_id:
        raise HistoryError("History entry requires movie_id")
    if part_number is not None and not isinstance(part_number, int):
        raise HistoryError("History entry part_number must be an integer or null")

    key = episode_key(movie_id, part_number, is_full_video=is_full_video)
    if not entry_is_uploaded(entry):
        history["uploaded_movies"] = [*entries_value, dict(entry)]
        return

    if not allow_duplicate:
        for existing in entries_value:
            existing_record = _as_dict(existing)
            if existing_record is None or not entry_is_uploaded(existing):
                continue
            existing_key = episode_key(
                str(existing_record.get("movie_id", "")),
                existing_record.get("part_number") if isinstance(existing_record.get("part_number"), int) else None,
                is_full_video=bool(existing_record.get("is_full_video", False)),
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
