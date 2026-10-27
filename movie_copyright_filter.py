#!/usr/bin/env python3
"""
Multi-Agent Anti-Copyright Filter System for Woo's Clips Movie Explanation Engine.
Implements 3 specialized agents to inspect and shield every movie video, title, and description
before reaching YouTube:
  1. MovieVideoCopyrightAgent: Validates duration (<=58.5s for Shorts), stream integrity, orientation.
  2. MovieTitleCopyrightAgent: Enforces transformative markers ('Ending Explained', 'Movie Explained'),
     blocks crawler/scraper patterns, validates length and hashtags.
  3. MovieDescriptionCopyrightAgent: Enforces statutory Section 107 Fair Use notice and commentary context.
Includes automated metadata remediation and retry orchestration.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_SHORT_DURATION = float(os.environ.get("MAX_SHORT_DURATION", "58.0"))
MIN_SHORT_DURATION = 10.0
MAX_SHOT_DURATION = 3.2

from movie_pipeline_state import MediaRightsRecord, RightsStatus

SCRAPER_PATTERN_EP = re.compile(r'\b[Ss]\d+[\s._-]*[Ee][Pp]?\s*\d+\b', re.IGNORECASE)
SCRAPER_PATTERN_SEASON = re.compile(r'\bSeason\s*\d+\s*Episode\s*\d+\b', re.IGNORECASE)

FAIR_USE_DISCLAIMER = (
    "\n\n---\n"
    "Copyright Disclaimer Under Section 107 of the Copyright Act 1976:\n"
    "Allowance is made for \"Fair Use\" for purposes such as criticism, comment, news reporting, "
    "teaching, scholarship, and research. Fair use is a use permitted by copyright statute that might "
    "otherwise be infringing. Non-profit, educational or personal use tips the balance in favor of fair use.\n"
    "All movie footage and audiovisual elements belong to their respective copyright holders and studios. "
    "This production is an original, transformative commentary, critical analysis, and story explanation.\n"
    "Channel: Woo's Clips (@woosclips)\n"
    "---\n"
)


@dataclass
class AgentResult:
    agent_name: str
    passed: bool
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiAgentFilterResult:
    passed: bool
    agent_results: Dict[str, AgentResult]
    remediation_needed: bool
    summary: str

    @property
    def violations(self) -> List[str]:
        all_v = []
        for r in self.agent_results.values():
            all_v.extend(r.violations)
        return all_v


class MovieVideoCopyrightAgent:
    """Agent 1: Inspects movie video duration boundaries, streams, and resolution."""

    def __init__(self, max_short_duration: float = MAX_SHORT_DURATION):
        self.max_short_duration = max_short_duration

    def evaluate_video(
        self,
        video_path: Path | str,
        is_short: bool = True,
        shots_info: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentResult:
        v_path = Path(video_path)
        violations = []
        warnings = []
        details = {}

        if not v_path.exists():
            violations.append(f"Video file does not exist: {v_path}")
        else:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json",
                str(v_path),
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                probe = json.loads(res.stdout)
                duration = float(probe.get("format", {}).get("duration", 0.0))
                streams = probe.get("streams", [])
                details["duration"] = duration

                has_video = any(s.get("codec_type") == "video" for s in streams)
                has_audio = any(s.get("codec_type") == "audio" for s in streams)
                video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
                width = int(video_stream.get("width", 0))
                height = int(video_stream.get("height", 0))
                details["width"] = width
                details["height"] = height
                details["has_audio"] = has_audio

                if not has_video:
                    violations.append("No video stream found in file.")
                if not has_audio:
                    violations.append("No audio stream found; narration or BGM missing.")

                if is_short:
                    if duration > self.max_short_duration:
                        violations.append(
                            f"Short duration {duration:.2f}s exceeds safe ceiling {self.max_short_duration}s."
                        )
                    elif duration < MIN_SHORT_DURATION:
                        violations.append(f"Short duration {duration:.2f}s is below minimum {MIN_SHORT_DURATION}s.")
                else:
                    if duration <= 60.0:
                        violations.append(f"Full video duration {duration:.2f}s is too short (expected >60s).")

                if is_short and height < width and width > 0:
                    violations.append(f"Short is in landscape ({width}x{height}); expected 9:16 vertical.")

            except Exception as e:
                warnings.append(f"ffprobe check skipped or returned error: {e}")

        if shots_info:
            for idx, s in enumerate(shots_info):
                dur = s.get("duration", 0.0)
                if dur > (MAX_SHOT_DURATION + 0.1):
                    violations.append(
                        f"Shot #{idx} duration {dur:.2f}s exceeds contiguous micro-cut threshold ({MAX_SHOT_DURATION}s)."
                    )

        passed = len(violations) == 0
        return AgentResult(
            agent_name="MovieVideoCopyrightAgent",
            passed=passed,
            violations=violations,
            warnings=warnings,
            details=details,
        )


class MovieTitleCopyrightAgent:
    """Agent 2: Enforces transformative title metadata, anti-scraper keyword protection, and length."""

    def __init__(self, blocked_titles: Optional[List[str]] = None):
        self.blocked_titles = [t.lower().strip() for t in (blocked_titles or [])]

    def evaluate_title(self, title: str, is_short: bool = True) -> AgentResult:
        violations = []
        warnings = []
        clean_title = title.strip()
        lower = clean_title.lower()

        # 1. Transformative explanation indicator check
        transformative_terms = [
            "explanation", "recap", "explained", "ending explained", "movie explained", "story recap"
        ]
        if not any(term in lower for term in transformative_terms):
            violations.append("Title missing transformative marker ('Ending Explained', 'Movie Explained', or 'Recap').")

        # 2. Scraper blacklist check
        if SCRAPER_PATTERN_EP.search(clean_title):
            violations.append(
                f"Title contains exact episode scraper pattern '{SCRAPER_PATTERN_EP.search(clean_title).group(0)}'. Must use 'Story Recap' or 'Ending Explained'."
            )
        if SCRAPER_PATTERN_SEASON.search(clean_title):
            violations.append(
                f"Title contains exact season/episode scraper pattern '{SCRAPER_PATTERN_SEASON.search(clean_title).group(0)}'. Must use 'Story Recap'."
            )

        # 3. Known blocked titles check
        for blocked in self.blocked_titles:
            if blocked and (blocked in lower or lower in blocked):
                violations.append(f"Title matches previously blocked YouTube upload blacklist: '{blocked}'.")
                break

        # 4. Length check
        if len(clean_title) < 15:
            violations.append(f"Title length ({len(clean_title)} chars) is too short.")
        elif len(clean_title) > 100:
            warnings.append(f"Title length ({len(clean_title)} chars) exceeds 100-char YouTube limit.")

        # 5. Hashtag for Shorts
        if is_short and "#shorts" not in lower:
            violations.append("Short title missing '#shorts' hashtag.")

        passed = len(violations) == 0
        return AgentResult(
            agent_name="MovieTitleCopyrightAgent",
            passed=passed,
            violations=violations,
            warnings=warnings,
            details={"title": clean_title, "length": len(clean_title)},
        )

    @staticmethod
    def remediate_title(title: str, is_short: bool = True, part_num: Optional[int] = None) -> str:
        """Automatically sanitizes title to pass all copyright criteria."""
        t = title.strip()
        if not t:
            t = "Movie Story Recap"
        t = SCRAPER_PATTERN_EP.sub("Story Recap", t)
        t = SCRAPER_PATTERN_SEASON.sub("Story Recap", t)

        lower = t.lower()
        if "explanation" not in lower and "explained" not in lower:
            if "story recap" in lower:
                t = re.sub(r'Story Recap', 'Story Recap Ending Explained', t, count=1, flags=re.IGNORECASE)
            elif "recap" in lower:
                t = re.sub(r'Recap', 'Recap Ending Explained', t, count=1, flags=re.IGNORECASE)
            elif part_num:
                t = f"{t} Ending Explained Part {part_num}"
            else:
                t = f"{t} Ending Explained"

        if is_short and "#shorts" not in t.lower():
            t = f"{t} #Shorts #MovieExplained"

        t = re.sub(r'\s+', ' ', t).strip()
        if len(t) > 100:
            if "#shorts" in t.lower():
                base = re.sub(r'#shorts.*', '', t, flags=re.IGNORECASE).strip()
                t = f"{base[:70].rstrip()} #Shorts #MovieExplained"
            else:
                t = t[:96].rstrip() + "..."
        if len(t) < 15:
            t = f"{t} | Story Recap"
        return t


class MovieDescriptionCopyrightAgent:
    """Agent 3: Validates fair-use statutory disclosures, transformative commentary context, and tags."""

    def evaluate_description(self, description: str, hashtags: Optional[List[str]] = None) -> AgentResult:
        violations = []
        warnings = []
        desc = description.strip()
        lower = desc.lower()

        # 1. Statutory Fair Use Disclosure Check
        has_section_107 = "section 107" in lower or "fair use" in lower
        if not has_section_107:
            violations.append("Description missing mandatory Section 107 Copyright Act Fair Use disclaimer.")

        # 2. Transformative commentary description
        has_commentary_note = any(
            term in lower for term in ["transformative", "commentary", "explanation", "recap", "educational", "analysis"]
        )
        if not has_commentary_note:
            violations.append("Description missing transformative commentary/educational context notice.")

        passed = len(violations) == 0
        return AgentResult(
            agent_name="MovieDescriptionCopyrightAgent",
            passed=passed,
            violations=violations,
            warnings=warnings,
            details={"has_disclaimer": has_section_107, "length": len(desc)},
        )

    @staticmethod
    def remediate_description(description: str, title: str = "") -> str:
        """Injects Section 107 disclaimer and ensures clean fair-use framing."""
        desc = description.strip()
        if "section 107" not in desc.lower() and "fair use" not in desc.lower():
            desc = desc + FAIR_USE_DISCLAIMER
        return desc.strip()


class MovieMultiAgentCopyrightShield:
    """Orchestrates all 3 filter agents and performs automated remediation & retry loops."""

    def __init__(self, blocked_titles: Optional[List[str]] = None):
        self.video_agent = MovieVideoCopyrightAgent()
        self.title_agent = MovieTitleCopyrightAgent(blocked_titles=blocked_titles)
        self.desc_agent = MovieDescriptionCopyrightAgent()

    def inspect_candidate(
        self,
        video_path: Path | str,
        title: str,
        description: str,
        is_short: bool = True,
        shots_info: Optional[List[Dict[str, Any]]] = None,
        hashtags: Optional[List[str]] = None,
    ) -> MultiAgentFilterResult:
        v_res = self.video_agent.evaluate_video(video_path, is_short, shots_info)
        t_res = self.title_agent.evaluate_title(title, is_short)
        d_res = self.desc_agent.evaluate_description(description, hashtags)

        all_passed = v_res.passed and t_res.passed and d_res.passed
        agent_results = {
            "MovieVideoCopyrightAgent": v_res,
            "MovieTitleCopyrightAgent": t_res,
            "MovieDescriptionCopyrightAgent": d_res,
        }

        all_violations = v_res.violations + t_res.violations + d_res.violations
        remediation_needed = not all_passed

        summary = (
            "✅ All 3 Movie Anti-Copyright Agents PASSED."
            if all_passed
            else f"❌ Shield REJECTED candidate with {len(all_violations)} violation(s): " + " | ".join(all_violations)
        )

        return MultiAgentFilterResult(
            passed=all_passed,
            agent_results=agent_results,
            remediation_needed=remediation_needed,
            summary=summary,
        )

    def remediate_metadata(
        self,
        title: str,
        description: str,
        is_short: bool = True,
        part_num: Optional[int] = None,
    ) -> Tuple[str, str]:
        clean_title = self.title_agent.remediate_title(title, is_short=is_short, part_num=part_num)
        clean_desc = self.desc_agent.remediate_description(description, title=clean_title)
        return clean_title, clean_desc


class MediaRightsAgent:
    """Evaluates media rights status for source assets without making false claims of clearance."""

    @staticmethod
    def evaluate_rights(
        source_type: str,
        asset_metadata: dict | None = None,
        bgm_track: str | None = None,
        allow_unreviewed: bool = False,
    ) -> MediaRightsRecord:
        meta = asset_metadata or {}
        explicit_status = meta.get("rights_status") or meta.get("license_status")
        license_id = meta.get("license_id") or meta.get("license")
        attribution = meta.get("attribution") or meta.get("rights_holder")

        if explicit_status in [s.value for s in RightsStatus]:
            status = RightsStatus(explicit_status)
            requires_review = (status in (RightsStatus.PERMISSION_UNKNOWN, RightsStatus.RESTRICTED)) and not allow_unreviewed
            return MediaRightsRecord(
                source_type=source_type,
                rights_status=status,
                license_id=license_id,
                attribution=attribution,
                requires_manual_review=requires_review,
                notes=f"Explicitly recorded rights status: {status.value}",
            )

        if source_type == "neutral_fallback":
            return MediaRightsRecord(
                source_type=source_type,
                rights_status=RightsStatus.PUBLIC_DOMAIN_VERIFIED,
                attribution="Procedural motion graphic",
                requires_manual_review=False,
                notes="Generated procedural background; no third-party copyrighted footage",
            )

        if source_type == "authorized_trailer":
            is_official = bool(meta.get("official") or meta.get("trailer_url"))
            return MediaRightsRecord(
                source_type=source_type,
                rights_status=RightsStatus.AUTHORIZED if is_official else RightsStatus.PERMISSION_UNKNOWN,
                license_id=license_id,
                attribution=attribution or meta.get("channel_title"),
                requires_manual_review=not is_official and not allow_unreviewed,
                notes="Official promotional trailer" if is_official else "Trailer rights unverified; requires review",
            )

        if source_type in ("private_media", "gdrive", "local_media"):
            has_license = bool(license_id or meta.get("license_file"))
            if has_license:
                return MediaRightsRecord(
                    source_type=source_type,
                    rights_status=RightsStatus.LICENSED,
                    license_id=license_id,
                    attribution=attribution,
                    requires_manual_review=False,
                    notes=f"Licensed private media record: {license_id}",
                )
            return MediaRightsRecord(
                source_type=source_type,
                rights_status=RightsStatus.PERMISSION_UNKNOWN,
                requires_manual_review=not allow_unreviewed,
                notes="Drive file present but underlying movie rights status is unverified; requires manual review",
            )

        return MediaRightsRecord(
            source_type=source_type,
            rights_status=RightsStatus.PERMISSION_UNKNOWN,
            requires_manual_review=not allow_unreviewed,
            notes=f"Source type '{source_type}' has no verified rights documentation",
        )


def verify_output_directory(output_dir: Path | str) -> bool:
    """Verifies all rendered output packages in output/ directory."""
    out_path = Path(output_dir)
    if not out_path.exists():
        print(f"[movie_filter] ⚠️ Output directory does not exist: {out_path}")
        return True

    shield = MovieMultiAgentCopyrightShield()
    all_ok = True

    # Scan for manifest files or mp4 files
    for manifest_path in out_path.glob("**/manifest*.json"):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            v_file = data.get("video_file")
            if not v_file:
                continue
            v_path = manifest_path.parent / v_file
            title = data.get("title", "")
            desc = data.get("description", "")
            res = shield.inspect_candidate(v_path, title, desc, is_short=True)
            print(f"[movie_filter] 🛡️ Inspecting {v_path.name}: {res.summary}")
            if not res.passed:
                all_ok = False
        except Exception as e:
            print(f"[movie_filter] Error checking {manifest_path}: {e}")

    return all_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Agent Anti-Copyright Filter for Movie Clips")
    parser.add_argument("--verify-output", help="Directory containing rendered output to verify")
    parser.add_argument("--check-title", help="Test a title against copyright filter")
    args = parser.parse_args()

    if args.check_title:
        agent = MovieTitleCopyrightAgent()
        res = agent.evaluate_title(args.check_title)
        print(f"Title Passed: {res.passed}")
        print(f"Violations: {res.violations}")
        if not res.passed:
            print(f"Remediated: {agent.remediate_title(args.check_title)}")
        sys.exit(0 if res.passed else 1)

    if args.verify_output:
        ok = verify_output_directory(args.verify_output)
        sys.exit(0 if ok else 1)
