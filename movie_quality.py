from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_SHORT_DURATION = 58.0
EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920


class MediaValidationError(Exception):
    """Raised when a generated Short does not meet the render contract."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_sec: float
    width: int
    height: int
    has_audio: bool


def _number(value: str | int | float | bool | None, field: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise MediaValidationError(f"ffprobe did not return a numeric {field}")


def _integer(value: str | int | float | bool | None, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
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
    info = MediaInfo(
        duration_sec=_number(duration_value, "duration"),
        width=_integer(video_stream.get("width"), "video width"),
        height=_integer(video_stream.get("height"), "video height"),
        has_audio=has_audio,
    )
    validate_media_info(info)
    return info
