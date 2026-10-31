from __future__ import annotations

import os
import re
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default safe duration ceiling for Shorts: 58.0 seconds (leaves a 2.0s buffer below YouTube's 60.0s hard cap)
DEFAULT_SHORTS_MAX_DURATION = 58.0
DEFAULT_CINERUSH_MIN_DURATION = 45.0
DEFAULT_CINERUSH_TARGET_DURATION = 54.0
CINERUSH_QUICK_TEST_DURATION = 12.0
SHORTS_HARD_CAP = 60.0
MIN_SHORT_DURATION = 10.0
DEFAULT_LONG_VIDEO_MAX_DURATION = 14400.0  # 4 hours

MAX_SHORT_DURATION = float(os.environ.get("MAX_SHORT_DURATION", str(DEFAULT_SHORTS_MAX_DURATION)))
EXPECTED_WIDTH = 1080


def validate_cinerush_duration_policy(
    max_duration: float | None = None,
    min_duration: float = DEFAULT_CINERUSH_MIN_DURATION,
    quick_test: bool = False,
) -> tuple[float, float]:
    """Return the normal Cinerush minimum/maximum duration contract."""
    if quick_test:
        return 0.0, CINERUSH_QUICK_TEST_DURATION
    ceiling = validate_duration_policy(max_duration=max_duration, profile="shorts")
    minimum = float(min_duration)
    if minimum < 1.0 or minimum > ceiling:
        raise ConfigurationError(
            f"Cinerush minimum duration must be between 1.0s and {ceiling:.1f}s, got {minimum:.2f}s"
        )
    return minimum, ceiling
EXPECTED_HEIGHT = 1920

# Source types that carry no licensed footage. A render built from these is
# placeholder content and must not reach the channel by accident.
PLACEHOLDER_SOURCE_TYPES = frozenset({"neutral_fallback"})


class MediaValidationError(Exception):
    """Raised when a generated Short does not meet the render contract."""


class ConfigurationError(MediaValidationError):
    """Raised when duration or pipeline settings are invalid or contradictory."""


def validate_duration_policy(
    max_duration: float | None = None,
    is_long_video: bool = False,
    profile: str = "shorts",
) -> float:
    """Validate and normalize the maximum duration setting for a profile.

    Rejects invalid, contradictory, or dangerous settings (e.g. non-numeric,
    negative, or Shorts durations exceeding the 60.0s YouTube hard cap).
    """
    if max_duration is None:
        if is_long_video or profile in ("long_form", "landscape", "long_video"):
            raw = os.environ.get("MAX_LONG_VIDEO_DURATION", str(DEFAULT_LONG_VIDEO_MAX_DURATION))
        else:
            raw = os.environ.get("MAX_SHORT_DURATION", str(DEFAULT_SHORTS_MAX_DURATION))
        try:
            max_duration = float(raw)
        except (ValueError, TypeError) as err:
            raise ConfigurationError(f"Duration configuration must be numeric: {raw!r}") from err

    if isinstance(max_duration, bool) or not isinstance(max_duration, (int, float)):
        raise ConfigurationError(f"Duration setting must be a number, got {type(max_duration).__name__}")

    val = float(max_duration)
    if val <= 0:
        raise ConfigurationError(f"Maximum duration must be positive, got {val:.2f}s")

    if is_long_video or profile in ("long_form", "landscape", "long_video"):
        if val <= SHORTS_HARD_CAP:
            raise ConfigurationError(
                f"Long-form video max duration must exceed {SHORTS_HARD_CAP:.0f}s, got {val:.2f}s"
            )
        return val

    # Dedicated Shorts profile checks
    if val > SHORTS_HARD_CAP:
        raise ConfigurationError(
            f"Configured Shorts max duration ({val:.2f}s) exceeds the YouTube Shorts hard cap of {SHORTS_HARD_CAP:.1f}s. "
            f"A Short longer than 60s is classified as long-form and subject to Content ID blocks. "
            f"Default safe ceiling is {DEFAULT_SHORTS_MAX_DURATION:.1f}s."
        )
    if val < MIN_SHORT_DURATION:
        raise ConfigurationError(
            f"Configured Shorts max duration ({val:.2f}s) is below minimum allowed duration ({MIN_SHORT_DURATION:.1f}s)."
        )
    return val


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
    max_duration: float | None = None,
    is_long_video: bool = False,
    profile: str = "shorts",
) -> None:
    """Validate already-probed media without invoking external tools."""
    if info.duration_sec <= 0:
        raise MediaValidationError("Rendered media duration must be positive")
    
    # If max_duration is not explicitly passed, evaluate through the duration policy
    if max_duration is None:
        effective_max = 14400.0 if is_long_video else validate_duration_policy(is_long_video=is_long_video, profile=profile)
    else:
        effective_max = 14400.0 if is_long_video else float(max_duration)

    if info.duration_sec > effective_max + 0.05:
        raise MediaValidationError(
            f"Rendered media is {info.duration_sec:.2f}s; maximum is {effective_max:.2f}s"
        )
    if is_long_video:
        valid_dimensions = (
            (info.width == EXPECTED_WIDTH and info.height == EXPECTED_HEIGHT) or
            (info.width == 1920 and info.height == 1080) or
            (info.width == 1280 and info.height == 720)
        )
        if not valid_dimensions:
            raise MediaValidationError(
                f"Rendered long video has unsupported dimensions: {info.width}x{info.height}"
            )
    else:
        if info.width != EXPECTED_WIDTH or info.height != EXPECTED_HEIGHT:
            raise MediaValidationError(
                f"Rendered media must be {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}, "
                f"got {info.width}x{info.height}"
            )
    if not info.has_audio:
        raise MediaValidationError("Rendered media must contain an audio stream")


def parse_ass_timestamp(timestamp_str: str) -> float:
    """Convert an ASS timestamp string (e.g. '0:01:23.45') to seconds."""
    parts = timestamp_str.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600.0 + float(m) * 60.0 + float(s)
    if len(parts) == 2:
        m, s = parts
        return float(m) * 60.0 + float(s)
    return float(parts[0])


def validate_subtitles_timing(subtitles_path: Path | str, video_duration: float) -> list[str]:
    """Validate that subtitle events are well-formed and do not extend past video duration."""
    sub_path = Path(subtitles_path)
    if not sub_path.exists():
        return ["Subtitle file does not exist"]

    violations: list[str] = []
    max_end_time = 0.0
    dialogue_count = 0

    try:
        content = sub_path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("Dialogue:"):
                dialogue_count += 1
                parts = line_str.split(",", 9)
                if len(parts) >= 3:
                    end_str = parts[2].strip()
                    try:
                        end_sec = parse_ass_timestamp(end_str)
                        if end_sec > max_end_time:
                            max_end_time = end_sec
                    except ValueError:
                        violations.append(f"Invalid subtitle end timestamp format: {end_str}")

        if dialogue_count == 0:
            violations.append("Subtitle file contains no dialogue events")

        # Allow 0.25s grace for player flush, but anything beyond is an overrun
        if max_end_time > video_duration + 0.25:
            violations.append(
                f"Subtitle track extends beyond video: last caption ends at {max_end_time:.2f}s, "
                f"but video duration is {video_duration:.2f}s"
            )
    except Exception as err:
        violations.append(f"Failed to inspect subtitle file: {err}")

    return violations


@dataclass(frozen=True, slots=True)
class TechnicalQCReport:
    """Detailed technical quality control inspection results."""
    passed: bool
    duration_passed: bool
    measured_duration: float
    target_max_duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    container_duration: float
    video_stream_duration: float | None
    audio_stream_duration: float | None
    subtitle_passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "duration_passed": self.duration_passed,
            "measured_duration": round(self.measured_duration, 2),
            "target_max_duration": round(self.target_max_duration, 2),
            "width": self.width,
            "height": self.height,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "container_duration": round(self.container_duration, 2),
            "video_stream_duration": round(self.video_stream_duration, 2) if self.video_stream_duration is not None else None,
            "audio_stream_duration": round(self.audio_stream_duration, 2) if self.audio_stream_duration is not None else None,
            "subtitle_passed": self.subtitle_passed,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }


def validate_rendered_video_technical(
    video_path: Path | str,
    subtitles_path: Path | str | None = None,
    max_duration: float | None = None,
    is_long_video: bool = False,
    profile: str = "shorts",
) -> TechnicalQCReport:
    """Execute deep technical quality checks on a rendered video file.

    Inspects actual container duration, stream durations (accounting for audio tail),
    resolution, codecs, stream presence, and subtitle synchronization.
    """
    v_path = Path(video_path)
    failures: list[str] = []
    warnings: list[str] = []

    target_ceiling = validate_duration_policy(max_duration=max_duration, is_long_video=is_long_video, profile=profile)

    if not v_path.exists():
        return TechnicalQCReport(
            passed=False,
            duration_passed=False,
            measured_duration=0.0,
            target_max_duration=target_ceiling,
            width=0,
            height=0,
            has_video=False,
            has_audio=False,
            video_codec=None,
            audio_codec=None,
            container_duration=0.0,
            video_stream_duration=None,
            audio_stream_duration=None,
            subtitle_passed=False,
            failures=[f"Rendered video file does not exist: {v_path}"],
            warnings=[],
        )

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(v_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe = json.loads(res.stdout)
    except FileNotFoundError:
        failures.append("ffprobe executable not found")
        return TechnicalQCReport(
            passed=False, duration_passed=False, measured_duration=0.0, target_max_duration=target_ceiling,
            width=0, height=0, has_video=False, has_audio=False, video_codec=None, audio_codec=None,
            container_duration=0.0, video_stream_duration=None, audio_stream_duration=None, subtitle_passed=False,
            failures=failures, warnings=warnings,
        )
    except subprocess.CalledProcessError as err:
        failures.append(f"ffprobe failed to inspect video: {err.stderr.strip()[-200:]}")
        return TechnicalQCReport(
            passed=False, duration_passed=False, measured_duration=0.0, target_max_duration=target_ceiling,
            width=0, height=0, has_video=False, has_audio=False, video_codec=None, audio_codec=None,
            container_duration=0.0, video_stream_duration=None, audio_stream_duration=None, subtitle_passed=False,
            failures=failures, warnings=warnings,
        )

    format_data = probe.get("format", {})
    streams = probe.get("streams", [])

    try:
        container_dur = float(format_data.get("duration", 0.0))
    except (ValueError, TypeError):
        container_dur = 0.0

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    has_video = video_stream is not None
    has_audio = audio_stream is not None
    video_codec = video_stream.get("codec_name") if video_stream else None
    audio_codec = audio_stream.get("codec_name") if audio_stream else None

    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0

    v_stream_dur = None
    if video_stream and "duration" in video_stream:
        try:
            v_stream_dur = float(video_stream["duration"])
        except (ValueError, TypeError):
            pass

    a_stream_dur = None
    if audio_stream and "duration" in audio_stream:
        try:
            a_stream_dur = float(audio_stream["duration"])
        except (ValueError, TypeError):
            pass

    # The effective container duration accounts for audio tail or container padding
    measured_duration = max(container_dur, v_stream_dur or 0.0, a_stream_dur or 0.0)

    # 1. Stream presence checks
    if not has_video:
        failures.append("Rendered output is missing a video stream")
    if not has_audio:
        failures.append("Rendered output is missing an audio stream (silent video rejected)")

    # 2. Resolution checks
    p_lower = str(profile).lower()
    if p_lower in ("cinerush", "cinerush_style", "square", "1:1"):
        if width != height or width < 720:
            failures.append(f"CineRush 1:1 resolution is {width}x{height}; required square (e.g. 1920x1920 or 1080x1080)")
    elif p_lower in ("landscape", "16:9") or is_long_video:
        valid_dims = (
            (width == 1920 and height == 1080) or
            (width == 1280 and height == 720) or
            (width == EXPECTED_WIDTH and height == EXPECTED_HEIGHT)
        )
        if not valid_dims:
            failures.append(f"Landscape/Long-form resolution {width}x{height} is not supported")
    elif p_lower in ("shorts", "shorts_9x16", "9:16"):
        if width != EXPECTED_WIDTH or height != EXPECTED_HEIGHT:
            failures.append(f"Short resolution is {width}x{height}; required 9:16 vertical ({EXPECTED_WIDTH}x{EXPECTED_HEIGHT})")

    # 3. Duration check against target ceiling (including 0.05s container tolerance)
    duration_passed = True
    if measured_duration <= 0:
        failures.append("Measured video duration is 0.0s")
        duration_passed = False
    elif measured_duration > target_ceiling + 0.05:
        failures.append(
            f"Rendered duration {measured_duration:.2f}s exceeds the maximum ceiling of {target_ceiling:.2f}s "
            f"(over by {measured_duration - target_ceiling:.2f}s)"
        )
        duration_passed = False

    # 4. Audio tail and synchronization drift check
    if v_stream_dur and a_stream_dur:
        drift = abs(v_stream_dur - a_stream_dur)
        if drift > 1.0:
            warnings.append(f"Audio/video stream duration difference is {drift:.2f}s (audio tail detected)")

    # 5. Subtitle synchronization check
    subtitle_passed = True
    if subtitles_path:
        sub_violations = validate_subtitles_timing(subtitles_path, measured_duration)
        if sub_violations:
            subtitle_passed = False
            failures.extend(sub_violations)

    passed = len(failures) == 0

    return TechnicalQCReport(
        passed=passed,
        duration_passed=duration_passed,
        measured_duration=measured_duration,
        target_max_duration=target_ceiling,
        width=width,
        height=height,
        has_video=has_video,
        has_audio=has_audio,
        video_codec=video_codec,
        audio_codec=audio_codec,
        container_duration=container_dur,
        video_stream_duration=v_stream_dur,
        audio_stream_duration=a_stream_dur,
        subtitle_passed=subtitle_passed,
        failures=failures,
        warnings=warnings,
    )


def probe_media(
    path: Path,
    max_duration: float | None = None,
    is_long_video: bool = False,
    profile: str = "shorts",
) -> MediaInfo:
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
    validate_media_info(info, max_duration=max_duration, is_long_video=is_long_video, profile=profile)
    return info
