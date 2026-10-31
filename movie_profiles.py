#!/usr/bin/env python3
"""
Central Profile & Configuration System for Movie Explanation Engine.
Supports multi-format cinematic rendering:
1. CINERUSH_STYLE: 1920x1920 (1:1 square), movie footage dominates frame, cinematic subtitle placement
2. SHORTS_9X16: 1080x1920 (9:16 vertical), intelligent subject-aware reframing, large movie presentation
3. LANDSCAPE: 1920x1080 (16:9 widescreen), theatrical wide format
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ProfileType(str, Enum):
    CINERUSH_STYLE = "cinerush_style"
    SHORTS_9X16 = "shorts_9x16"
    LANDSCAPE = "landscape"


class EffectIntensity(str, Enum):
    SUBTLE = "SUBTLE"
    NORMAL = "NORMAL"
    DRAMATIC = "DRAMATIC"


class VisualPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class StoryRole(str, Enum):
    HOOK = "HOOK"
    SETUP = "SETUP"
    CHARACTER_INTRO = "CHARACTER_INTRO"
    CONFLICT = "CONFLICT"
    DISCOVERY = "DISCOVERY"
    ESCALATION = "ESCALATION"
    REACTION = "REACTION"
    REVEAL = "REVEAL"
    TURNING_POINT = "TURNING_POINT"
    CONSEQUENCE = "CONSEQUENCE"
    PAYOFF = "PAYOFF"
    ENDING = "ENDING"


@dataclass
class CaptionStyle:
    font_family: str = "DejaVu Sans"
    font_size: int = 48
    font_weight: str = "bold"
    line_spacing: int = 4
    letter_spacing: int = 1
    max_width_ratio: float = 0.85
    alignment: int = 2  # ASS bottom-center
    fill_color: str = "&H00FFFFFF&"      # Clean crisp white
    outline_color: str = "&H00000000&"   # Subtle black outline
    outline_width: float = 2.8
    shadow: float = 1.5
    shadow_opacity: float = 0.65
    opacity: float = 1.0
    highlight_color_primary: str = "&H0000D4FF&"   # Warm cinematic gold
    highlight_color_accent: str = "&H0038B6FF&"    # Intense electric accent
    animation_style: str = "fade_up"
    animation_duration_ms: int = 140
    margin_v: int = 220                   # Safe vertical offset from bottom

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProfileConfig:
    name: str
    profile_type: ProfileType
    width: int
    height: int
    aspect_ratio: str
    crop_mode: str                        # "subject_aware", "cinematic_fill", "widescreen_fit"
    caption_style: CaptionStyle
    default_max_duration: float = 58.0
    target_editorial_score: float = 85.0
    max_revision_passes: int = 2
    effect_intensity_default: EffectIntensity = EffectIntensity.NORMAL
    max_zoom_factor: float = 1.08        # Restrained cinematic motion: 100% -> 103-108%
    enable_micro_cuts: bool = True
    relevance_weights: dict[str, float] = field(default_factory=lambda: {
        "visual_relevance": 0.30,
        "character_match": 0.15,
        "action_match": 0.15,
        "emotion_match": 0.10,
        "object_match": 0.10,
        "location_match": 0.05,
        "visual_quality": 0.05,
        "story_importance": 0.05,
        "continuity": 0.05,
        "repetition_penalty": 0.10,
    })

    @property
    def is_square(self) -> bool:
        return self.width == self.height

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["profile_type"] = self.profile_type.value
        data["effect_intensity_default"] = self.effect_intensity_default.value
        return data


# Central Profile Registry
PROFILES: dict[str, ProfileConfig] = {
    ProfileType.CINERUSH_STYLE.value: ProfileConfig(
        name="CineRush 1:1 Cinematic",
        profile_type=ProfileType.CINERUSH_STYLE,
        width=1920,
        height=1920,
        aspect_ratio="1:1",
        crop_mode="cinematic_fill",
        caption_style=CaptionStyle(
            font_size=52,
            outline_width=3.0,
            margin_v=140,
        ),
        default_max_duration=58.0,
        max_zoom_factor=1.06,
    ),
    ProfileType.SHORTS_9X16.value: ProfileConfig(
        name="Cinematic Shorts 9:16",
        profile_type=ProfileType.SHORTS_9X16,
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        crop_mode="subject_aware",
        caption_style=CaptionStyle(
            font_size=46,
            outline_width=2.8,
            margin_v=340,
        ),
        default_max_duration=58.0,
        max_zoom_factor=1.08,
    ),
    ProfileType.LANDSCAPE.value: ProfileConfig(
        name="Landscape Theatrical 16:9",
        profile_type=ProfileType.LANDSCAPE,
        width=1920,
        height=1080,
        aspect_ratio="16:9",
        crop_mode="widescreen_fit",
        caption_style=CaptionStyle(
            font_size=42,
            outline_width=2.5,
            margin_v=120,
        ),
        default_max_duration=180.0,
        max_zoom_factor=1.05,
    ),
}

# Alias mapping for CLI convenience
_ALIASES = {
    "cinerush": ProfileType.CINERUSH_STYLE.value,
    "cinerush_style": ProfileType.CINERUSH_STYLE.value,
    "1:1": ProfileType.CINERUSH_STYLE.value,
    "square": ProfileType.CINERUSH_STYLE.value,
    "shorts": ProfileType.SHORTS_9X16.value,
    "shorts_9x16": ProfileType.SHORTS_9X16.value,
    "9:16": ProfileType.SHORTS_9X16.value,
    "vertical": ProfileType.SHORTS_9X16.value,
    "landscape": ProfileType.LANDSCAPE.value,
    "16:9": ProfileType.LANDSCAPE.value,
    "long_video": ProfileType.LANDSCAPE.value,
}


def resolve_profile(name_or_alias: str | None) -> ProfileConfig:
    """Resolves a profile name or alias to its full ProfileConfig, defaulting to CINERUSH_STYLE."""
    if not name_or_alias:
        return PROFILES[ProfileType.CINERUSH_STYLE.value]

    key = str(name_or_alias).strip().lower()
    canonical = _ALIASES.get(key, key)
    if canonical in PROFILES:
        return PROFILES[canonical]
    # Default fallback
    return PROFILES[ProfileType.CINERUSH_STYLE.value]
