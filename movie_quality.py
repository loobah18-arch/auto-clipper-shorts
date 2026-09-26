from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_SHORT_DURATION = 58.0
EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920

# Source types that carry no licensed footage. A render built from these is
# placeholder content and must not reach the channel by accident.
PLACEHOLDER_SOURCE_TYPES = frozenset({"neutral_fallback"})


class MediaValidationError(Exception):
    """Raised when a generated Short does not meet the render contract."""


def validate_upload_source(source_type: str, allow_fallback: bool = False) -> None:
    """Refuse to publish renders that contain no licensed footage.

    Callers pass ``allow_fallback`` only when the operator opted in explicitly
    (ALLOW_FALLBACK_UPLOAD=true), because a placeholder Short is a publishing
    decision, not a technical fallback.
    """
    if source_type in PLACEHOLDER_SOURCE_TYPES and not allow_fallback:
        raise MediaValidationError(
            f"Refusing to upload: source_type={source_type!r} means the Short is only the "
            "neutral motion background. Supply licensed clips in raw_clips/, point "
            "trailer_url at footage you have rights to, or set ALLOW_FALLBACK_UPLOAD=true "
            "to publish placeholder renders deliberately."
        )


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_sec: float
    width: int
    height: int
    has_audio: bool


def _number(value: str | int | float | bool | None, field: str) -> float:
    """Coerce an ffprobe field to float.

    ffprobe's JSON writer emits scalars as strings (``"duration": "53.541000"``)
    even when the value is numeric, so string coercion is required here.
    """
    if isinstance(value, bool):
        raise MediaValidationError(f"ffprobe did not return a numeric {field}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise MediaValidationError(f"ffprobe did not return a numeric {field}")


def _integer(value: str | int | float | bool | None, field: str) -> int:
    """Coerce an ffprobe field to int, tolerating numeric string output."""
    if isinstance(value, bool):
        raise MediaValidationError(f"ffprobe did not return an integer {field}")
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            parsed = float(value.strip() if isinstance(value, str) else value)
        except ValueError:
            raise MediaValidationError(f"ffprobe did not return an integer {field}") from None
        if parsed.is_integer():
            return int(parsed)
    raise MediaValidationError(f"ffprobe did not return an integer {field}")


def validate_media_info(
    info: MediaInfo,
    max_duration: float = MAX_SHORT_DURATION,
) -> None:
    """Validate already-probed media without invoking external tools."""
    if info.duration_sec <= 0:
        raise MediaValidationError("Rendered media duration must be positive")
    if info.duration_sec > max_duration + 0.05:
        raise MediaValidationError(
            f"Rendered media is {info.duration_sec:.2f}s; maximum is {max_duration:.2f}s"
        )
    if info.width != EXPECTED_WIDTH or info.height != EXPECTED_HEIGHT:
        raise MediaValidationError(
            f"Rendered media must be {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}, "
            f"got {info.width}x{info.height}"
        )
    if not info.has_audio:
        raise MediaValidationError("Rendered media must contain an audio stream")


def probe_media(path: Path) -> MediaInfo:
    """Probe a rendered file and return normalized media metadata."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
    except FileNotFoundError as error:
        raise MediaValidationError("ffprobe is required for render validation") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip()[-300:]
        raise MediaValidationError(f"ffprobe failed for {path.name}: {detail}") from error
    except json.JSONDecodeError as error:
        raise MediaValidationError(f"ffprobe returned invalid JSON for {path.name}") from error

    if not isinstance(payload, dict):
        raise MediaValidationError("ffprobe returned a non-object payload")
    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise MediaValidationError("ffprobe payload is missing streams or format")

    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video_stream, dict):
        raise MediaValidationError("Rendered media has no video stream")

    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )
    duration_value = format_data.get("duration")
    if duration_value is None:
        # Some containers omit format-level duration; the video stream carries it.
        duration_value = video_stream.get("duration")
    info = MediaInfo(
        duration_sec=_number(duration_value, "duration"),
        width=_integer(video_stream.get("width"), "video width"),
        height=_integer(video_stream.get("height"), "video height"),
        has_audio=has_audio,
    )
    validate_media_info(info)
    return info
