#!/usr/bin/env python3
"""
Hypit Video Engine (v1.0.0)
Compliant implementation of Hypit SVML / SVS / SVRun authoring specification.
Compiles domain-specific video markup into broadcast-ready, word-anchored MP4 deliverables.
"""

import os
import sys
import re
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent
FONTS_DIR = ROOT_DIR / "assets" / "fonts"
FONT_PATH = FONTS_DIR / "Montserrat-ExtraBold.ttf"
SFX_DIR = ROOT_DIR / "assets" / "sfx"

# Six-stage story-arc retention curve ("Completion Compulsion"):
# HOOK grabs, tension builds, CONFLICT peaks, COMEBACK resets, the second
# RISING ACTION escalates, PAYOFF resolves. Weights are shares of the
# timeline duration when no intro narration is present; with narration the
# HOOK stage is pinned to the intro-narration window and the remaining
# stages split the rest proportionally.
ARC_STAGES: Tuple[Tuple[str, float], ...] = (
    ("HOOK", 0.14),
    ("RISING ACTION", 0.22),
    ("CONFLICT", 0.24),
    ("COMEBACK", 0.18),
    ("RISING ACTION", 0.12),
    ("PAYOFF", 0.10),
)
ARC_INTRO_SECONDS = 3.75  # intro narration window; pins the HOOK stage length

ArcCut = Tuple[str, float, float]  # (stage label, raw start, output duration)


def _probe_duration(video_path: Path) -> Optional[float]:
    """Returns the media duration in seconds via ffprobe, or None on failure."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return None


def plan_arc_cuts(
    raw_duration: Optional[float],
    out_duration: float,
    has_narration: bool,
    intro_seconds: float = ARC_INTRO_SECONDS,
) -> Optional[List[ArcCut]]:
    """Plans six story-arc cuts spread across the raw source timeline.

    Each stage takes a distinct, non-overlapping window from the raw video in
    arc order (HOOK earliest, PAYOFF at the very end), so the edit follows the
    "Completion Compulsion" curve instead of one continuous take from 0:00.
    Returns None when the plan is impossible, letting render() fall back to
    the continuous cut.
    """
    if out_duration < len(ARC_STAGES) or raw_duration is None or raw_duration <= 0 or raw_duration < out_duration:
        return None
    if has_narration:
        if out_duration <= intro_seconds:
            return None
        durs = [intro_seconds]
        remaining = out_duration - intro_seconds
        other_weight = sum(w for _, w in ARC_STAGES[1:])
        durs += [remaining * w / other_weight for _, w in ARC_STAGES[1:]]
    else:
        total_weight = sum(w for _, w in ARC_STAGES)
        durs = [out_duration * w / total_weight for _, w in ARC_STAGES]

    gap = (raw_duration - out_duration) / len(ARC_STAGES)
    cuts: List[ArcCut] = []
    cursor = gap
    for (stage, _), dur in zip(ARC_STAGES, durs):
        cuts.append((stage, cursor, dur))
        cursor += dur + gap
    return cuts


def build_arc_filter_complex(
    cuts: List[ArcCut],
    w: int,
    h: int,
    escaped_ass: str,
    font_dir_arg: str = "",
    has_narration: bool = False,
    has_sfx: bool = False,
) -> str:
    """Builds the ffmpeg -filter_complex graph for multi-segment arc cutting."""
    n = len(cuts)
    intro_grade = "eq=contrast=1.12:brightness=-0.06:saturation=1.22"
    main_grade = "eq=contrast=1.06:saturation=1.15"
    lines: List[str] = []

    v_labels = []
    for i, (_stage, start, dur) in enumerate(cuts):
        end = start + dur
        grade = intro_grade if (has_narration and i == 0) else main_grade
        lines.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,{grade}[v{i}];")
        v_labels.append(f"[v{i}]")
    lines.append(
        "".join(v_labels)
        + f"concat=n={n}:v=1:a=0,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:(iw-ow)/2:(ih-oh)/4,"
        + f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
    )

    a_labels = []
    for i, (_stage, start, dur) in enumerate(cuts):
        end = start + dur
        if has_narration and i == 0:
            lines.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,volume=0.20,lowpass=f=1200[a_amb];"
            )
            lines.append("[1:a]volume=1.40[a_vox];")
            lines.append("[a_amb][a_vox]amix=inputs=2:duration=first:dropout_transition=2[a_s0];")
        else:
            lines.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,volume=1.10[a_s{i}];")
        a_labels.append(f"[a_s{i}]")
    lines.append("".join(a_labels) + f"concat=n={n}:v=0:a=1[a_dialogue];")

    if has_narration and has_sfx:
        lines.append("[2:a]volume=0.90[a_sfx];")
        lines.append("[a_dialogue][a_sfx]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[a]")
    else:
        lines.append("[a_dialogue]alimiter=limit=0.95[a]")
    return "".join(lines)


class HypitSVMLParser:
    """Parses and validates Hypit SVML and SVS documents."""

    def __init__(self, svml_path: Path):
        self.svml_path = svml_path
        self.canvas_width = 1080
        self.canvas_height = 1920
        self.fps = 30
        self.timeline_duration = 24.0
        self.windows: Dict[str, Tuple[float, float]] = {}
        self.script_segments: Dict[str, str] = {}
        self.visual_clips: List[Dict[str, Any]] = []
        self.audio_clips: List[Dict[str, Any]] = []

    def parse(self) -> "HypitSVMLParser":
        if not self.svml_path.exists():
            raise FileNotFoundError(f"SVML document not found: {self.svml_path}")

        raw_content = self.svml_path.read_text(encoding="utf-8")
        # Strip XML processing instructions that may confuse standard parsers
        cleaned = re.sub(r'<\?svml.*?\?>', '', raw_content)
        # Quote unquoted JSX-style curly attributes: e.g. within={canvas.bounds} -> within="{canvas.bounds}"
        cleaned = re.sub(r'=(\{[^}]+\})', r'="\1"', cleaned)
        # Remove namespace prefixes from tags so they parse cleanly (e.g., <space:Canvas> -> <Canvas>)
        cleaned = re.sub(r'<(/)?([a-zA-Z0-9_\-]+):([a-zA-Z0-9_\-]+)', r'<\1\3', cleaned)

        root = ET.fromstring(cleaned)

        # Canvas & Timeline elements
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "Canvas":
                self.canvas_width = int(elem.attrib.get("width", 1080))
                self.canvas_height = int(elem.attrib.get("height", 1920))
            elif tag == "Clock":
                self.fps = int(elem.attrib.get("frame-rate", 30))
            elif tag == "Timeline":
                end_str = elem.attrib.get("end", "24.0s").replace("s", "")
                self.timeline_duration = float(end_str)
            elif tag == "Window":
                w_id = elem.attrib.get("id", "")
                from_str = float(elem.attrib.get("from", "0.0s").replace("s", ""))
                for_str = float(elem.attrib.get("for", "0.0s").replace("s", ""))
                self.windows[w_id] = (from_str, from_str + for_str)
            elif tag == "Clip":
                clip_data = {
                    "id": elem.attrib.get("id", ""),
                    "source": elem.attrib.get("source", ""),
                    "fit": elem.attrib.get("fit", "cover"),
                    "volume": float(elem.attrib.get("volume", "1.0")),
                }
                self.visual_clips.append(clip_data)

        # Script elements
        script_elem = root.find(".//script")
        if script_elem is not None:
            for child in script_elem:
                voice_elem = child.find("HOST")
                if voice_elem is None:
                    voice_elem = child.find("NARRATOR")
                text = voice_elem.text if voice_elem is not None and voice_elem.text else child.text
                if text:
                    self.script_segments[child.tag] = text.strip()

        return self


class HypitRunParser:
    """Parses Hypit SVRun execution manifests."""

    def __init__(self, svrun_path: Path):
        self.svrun_path = svrun_path
        self.author_svml: Optional[Path] = None
        self.look_svs: Optional[Path] = None
        self.inputs: Dict[str, Path] = {}
        self.target_output: Optional[Path] = None

    def parse(self) -> "HypitRunParser":
        if not self.svrun_path.exists():
            raise FileNotFoundError(f"SVRun manifest not found: {self.svrun_path}")

        raw_content = self.svrun_path.read_text(encoding="utf-8")
        cleaned = re.sub(r'<\?svml.*?\?>', '', raw_content)
        cleaned = re.sub(r'<(/)?([a-zA-Z0-9_\-]+):([a-zA-Z0-9_\-]+)', r'<\1\3', cleaned)

        root = ET.fromstring(cleaned)

        # Allow passing an SVML document directly as the manifest (auto-resolves defaults)
        if self.svrun_path.suffix == ".svml" or root.tag in ("svml", "Markup"):
            self.author_svml = self.svrun_path.resolve()
            default_look = self.svrun_path.parent / "look.svs"
            if default_look.exists():
                self.look_svs = default_look.resolve()
            # Inherit default assets/inputs from production.svrun if present
            prod_svrun = self.svrun_path.parent / "production.svrun"
            if prod_svrun.exists() and prod_svrun != self.svrun_path:
                try:
                    fallback_parser = HypitRunParser(prod_svrun).parse()
                    self.inputs = fallback_parser.inputs
                except Exception:
                    pass
            return self

        author = root.find("author")
        if author is not None:
            src = author.attrib.get("source", "")
            self.author_svml = (self.svrun_path.parent / src).resolve()

        look = root.find("look")
        if look is not None:
            src = look.attrib.get("source", "")
            self.look_svs = (self.svrun_path.parent / src).resolve()

        inputs_elem = root.find("inputs")
        if inputs_elem is not None:
            for f in inputs_elem.findall("file"):
                f_id = f.attrib.get("id", "")
                raw_path = f.attrib.get("path", "")
                p = Path(raw_path)
                if not p.is_absolute():
                    p = (ROOT_DIR / p).resolve()
                self.inputs[f_id] = p

        target = root.find("target")
        if target is not None:
            dest = target.attrib.get("destination", "")
            if dest:
                self.target_output = Path(dest)

        return self


class HypitEngine:
    """Compiles Hypit specifications and produces broadcast deliverables."""

    def __init__(
        self,
        svrun_path: Path,
        raw_video: Optional[Path] = None,
        creator: Optional[str] = None,
        script_path: Optional[Path] = None,
    ):
        self.svrun = HypitRunParser(svrun_path).parse()
        self.script: Optional[Dict[str, Any]] = None
        if script_path and Path(script_path).exists():
            try:
                self.script = json.loads(Path(script_path).read_text(encoding="utf-8"))
                print(f"[Hypit] 🎯 Hook->Payoff script loaded: {script_path}")
            except Exception as exc:
                print(f"[Hypit] ⚠️ Could not read script {script_path}: {exc}")
        if not self.svrun.author_svml:
            raise ValueError("No author SVML defined in SVRun manifest")
        self.svml = HypitSVMLParser(self.svrun.author_svml).parse()
        self.raw_video_override = raw_video
        self.creator = creator
        if not self.creator and self.svrun.author_svml:
            name = self.svrun.author_svml.name.lower()
            for c in ["mrbeast", "nileblue", "mark_rober", "ishowspeed", "ruhi_cenet"]:
                if c in name:
                    self.creator = c
                    break
        if not self.creator:
            self.creator = "mrbeast"

    def get_cues(self) -> List[Tuple[float, float, str]]:
        """Returns word-level cues matched to the active SVML document."""
        if self.script and self.script.get("cues"):
            return [
                (float(start), float(end), str(text))
                for start, end, text in self.script["cues"]
            ]

        svml_name = self.svrun.author_svml.name.lower()
        if "challenge" in svml_name or "cops" in svml_name:
            # MrBeast: Escape 100 Cops, Win $500,000 (Tested&Proved 6-Stage Completion Compulsion Curve)
            return [
                # Intro Title Hook: Deep Voice Narration (0.00s - 3.75s)
                (0.15, 1.80, "CAN HE ESCAPE 100 REAL COPS"),
                (1.85, 3.50, "FOR $500,000?"),

                # Stage 1: HOOK - Jimmy introduces 100 cops & stakes (3.75s - 13.52s)
                (3.95, 5.60, "BEHIND ME ARE 100 COPS!"),
                (5.65, 7.70, "AND IF THEY ARREST US BY THE END OF THE DAY,"),
                (7.75, 9.55, "THEY WIN HALF A MILLION DOLLARS!"),
                (9.62, 11.75, "BUT IF WE ESCAPE, THEY GET NOTHING."),
                (11.78, 12.80, "START THE TIMER!"),
                (12.82, 13.50, "DRIVE, JIMMY! DRIVE!"),

                # Stage 2: RISING ACTION - Traps and fast escape (13.52s - 23.55s)
                (13.79, 15.10, "WE GOTTA GET OUTTA HERE!"),
                (15.70, 16.45, "GO, GO!"),
                (16.50, 17.20, "OH, MY GOSH!"),
                (17.25, 18.60, "LET'S BLOW IT UP!"),
                (21.89, 23.47, "THAT SHOULD BUY US SOME TIME!"),

                # Stage 3: CONFLICT - Explosion diversion (23.55s - 33.62s)
                (23.78, 25.35, "FOR THIS NEXT DIVERSION,"),
                (25.52, 27.20, "I REALLY JUST WANTED TO HAVE"),
                (27.20, 29.30, "A LOT OF EXPLOSIONS!"),
                (30.18, 31.77, "IT MIGHT NOT MAKE MUCH SENSE,"),
                (31.88, 33.55, "BUT MAN, DOES IT LOOK EPIC!"),

                # Stage 4: COMEBACK - Nolan dead end corner (33.62s - 43.15s)
                (34.84, 35.65, "OH, NO!"),
                (35.90, 36.55, "IT'S A DEAD END."),
                (36.60, 37.75, "IT'S A DEAD END!!"),
                (39.44, 40.50, "HEY, SET A PERIMETER!"),
                (40.57, 43.12, "WE ARE SURROUNDED BY COPS!"),

                # Stage 5: CLIMAX ENCOUNTER - State Police tactical breach (43.15s - 50.42s)
                (43.35, 44.12, "STATE POLICE!"),
                (44.12, 45.55, "STATE POLICE, STAY DOWN!"),
                (47.68, 48.35, "NO, NO!"),
                (48.42, 49.77, "PUT HIM IN HANDCUFFS!"),
                (49.77, 50.40, "WE WERE SO CLOSE!"),

                # Stage 6: PAYOFF - Outcome & $500,000 cash celebration (50.42s - 60.00s)
                (50.60, 52.40, "THERE ARE SO MANY COPS!"),
                (52.44, 54.22, "WITH AN HOUR AND A HALF REMAINING,"),
                (54.30, 56.36, "THEY HAVE OFFICIALLY CAUGHT ALL FIVE OF US,"),
                (56.44, 57.95, "AND WON THAT HALF A MILLION DOLLARS!"),
                (58.00, 60.00, "CONGRATULATIONS!!"),
            ]
        elif "nileblue" in svml_name or "experiment" in svml_name:
            # NileBlue: Can you turn Red Bull into pure drinking water?
            return [
                (0.15, 1.80, "CAN NILEBLUE TURN RED BULL"),
                (1.85, 3.50, "INTO PURE DRINKING WATER?"),
                (3.95, 6.20, "CAN WE ACTUALLY TURN CANNED RED BULL"),
                (6.25, 9.50, "INTO 100% PURE DRINKING WATER?"),
                (9.60, 11.50, "FIRST STEP IS BOILING OFF THE LIQUID"),
                (11.55, 13.50, "AND TRAPPING THE CONDENSED VAPORS!"),
                (13.60, 17.50, "THE REACTION SUDDENLY TURNED PITCH BLACK"),
                (17.55, 23.50, "AND RELEASED INTENSE VOLATILE FUMES!"),
                (23.55, 28.50, "WE RUN IT THROUGH ACTIVATED CHARCOAL VACUUM"),
                (28.55, 33.50, "TO STRIP ALL ARTIFICIAL COLOR DYES!"),
                (33.60, 38.00, "AFTER THREE ROUNDS OF VACUUM DISTILLATION..."),
                (38.05, 43.00, "LOOK AT THIS CRYSTAL CLEAR DROPLET!"),
                (43.10, 48.00, "TESTING TOTAL DISSOLVED SOLIDS: ZERO PPM!"),
                (48.05, 50.25, "IT TASTES COMPLETELY NEUTRAL AND FRESH!"),
                (50.30, 60.00, "WE OFFICIALLY TURNED RED BULL INTO PURE WATER!"),
            ]
        elif "mark_rober" in svml_name or "gadget" in svml_name:
            # Mark Rober: Squirrel Ninja Warrior Obstacle Course 2.0
            return [
                (0.15, 1.80, "CAN SQUIRRELS BEAT A"),
                (1.85, 3.50, "NASA-DESIGNED OBSTACLE COURSE?"),
                (3.95, 6.20, "I BUILT A FULL NINJA WARRIOR COURSE"),
                (6.25, 9.50, "JUST TO PROTECT MY BIRD SEED FROM SQUIRRELS!"),
                (9.60, 11.50, "FIRST UP IS THE ROPE BRIDGE"),
                (11.55, 13.50, "AND THE HIGH VELOCITY LAUNCH PAD!"),
                (13.60, 17.50, "THE FIRST SQUIRREL GOT COMPLETELY LAUNCHED"),
                (17.55, 23.50, "STRAIGHT INTO THE SOFT LANDING NET!"),
                (23.55, 28.50, "BUT THEN PHANTASM FOUND A MICRO-GAP"),
                (28.55, 33.50, "IN THE ACRYLIC MAZE WALL!"),
                (33.60, 38.00, "HE BALANCES ACROSS THE SPINNING CYLINDERS!"),
                (38.05, 43.00, "LOOK AT THAT ACROBATIC JUMP!"),
                (43.10, 48.00, "HE REACHED THE WALNUT FORTRESS"),
                (48.05, 50.25, "AND WON ALL THE FOOD!"),
                (50.30, 60.00, "SQUIRRELS ARE UNSTOPPABLE ACROBATS!"),
            ]
        elif "speed" in svml_name or "viral" in svml_name:
            # IShowSpeed: Jumping over two speeding supercars
            return [
                (0.15, 1.80, "CAN ISHOWSPEED ACTUALLY JUMP"),
                (1.85, 3.50, "OVER TWO MOVING SUPERCARS?"),
                (3.95, 6.20, "CHAT IF I DO NOT TIME THIS PERFECTLY..."),
                (6.25, 9.50, "THIS IS REAL LIFE DANGER!"),
                (9.60, 11.50, "THE FIRST LAMBORGHINI IS ACCELERATING"),
                (11.55, 13.50, "AT OVER SIXTY MILES AN HOUR!"),
                (13.60, 17.50, "HE SPEEDS TOWARDS ME! OH MY GOD!"),
                (17.55, 23.50, "GET READY SPEED! HERE HE COMES!"),
                (23.55, 28.50, "THREE... TWO... ONE... JUMP!"),
                (28.55, 33.50, "HE CLEARS BOTH HOODS AIRBORNE!"),
                (33.60, 38.00, "LOOK AT THAT SLOW MOTION ELEVATION!"),
                (38.05, 43.00, "HE LANDS ON BOTH FEET SAFELY!"),
                (43.10, 48.00, "SPRINT CELEBRATING ACROSS THE TRACK!"),
                (48.05, 50.25, "SIUUUU! HE ACTUALLY SURVIVED!"),
                (50.30, 60.00, "SPEED JUMPED OVER TWO REAL SUPERCARS!"),
            ]
        elif "ruhi" in svml_name or "investigation" in svml_name:
            # Ruhi Çenet: Surviving 24 Hours in Yakutsk (-71°C)
            return [
                (0.15, 1.80, "WHAT HAPPENS IN 24 HOURS"),
                (1.85, 3.50, "IN THE COLDEST CITY ON EARTH?"),
                (3.95, 6.20, "WE ARE IN YAKUTSK, SIBERIA,"),
                (6.25, 9.50, "WHERE TEMPERATURES HIT -71 DEGREES!"),
                (9.60, 11.50, "EXPOSING BARE SKIN FOR 5 MINUTES"),
                (11.55, 13.50, "CAUSES PERMANENT TISSUE DAMAGE!"),
                (13.60, 17.50, "OUR CAMERA BATTERIES INSTANTLY DIED"),
                (17.55, 23.50, "AND MY EYELASHES FROZE SOLID!"),
                (23.55, 28.50, "EMERGENCY SHELTER IN A PERMAFROST CAVE"),
                (28.55, 33.50, "TO SURVIVE THE ARCTIC BLIZZARD!"),
                (33.60, 38.00, "THROWING BOILING WATER INTO THE AIR..."),
                (38.05, 43.00, "IT EXPLODES INTO THOUSANDS OF ICE CRYSTALS!"),
                (43.10, 48.00, "THIS IS HOW HUMANS SURVIVE"),
                (48.05, 50.25, "AT THE EDGE OF THE FROZEN EARTH!"),
                (50.30, 60.00, "ARCTIC EXPEDITION COMPLETED!"),
            ]
        elif "fall" in svml_name:
            # MrBeast: Last To Fall Wins challenge
            return [
                (0.00, 0.70, "3..."),
                (0.70, 1.20, "2..."),
                (1.20, 1.80, "1... PULL!"),
                (3.20, 4.40, "GO GO GO!"),
                (4.40, 5.70, "YES! PULL!"),
                (5.70, 6.80, "BYE BYE!"),
                (6.80, 8.00, "PULL!"),
                (8.00, 9.30, "OH OH OH!"),
                (9.30, 10.60, "GO GO GO!"),
                (10.60, 13.50, "OH GOSH! NO NO!"),
                (13.50, 17.50, "DON'T LET GO! 😱"),
                (17.50, 19.50, "OH MY SHOE!"),
                (19.50, 21.00, "MY SHOE!"),
                (21.00, 22.80, "I STARTED SLIPPING!"),
                (22.80, 24.00, "EXCUSES! 😂"),
            ]
        else:
            # Default MrBeast yacht comparison
            return [
                (0.00, 0.45, "WHAT IF"),
                (0.45, 1.05, "YOU TRIED"),
                (1.05, 1.45, "TO BUY"),
                (1.45, 1.85, "A $1 YACHT"),
                (1.85, 2.75, "AGAINST A"),
                (2.75, 4.00, "$1 BILLION SUPERYACHT?"),
                (4.00, 5.00, "MRBEAST BOUGHT"),
                (5.00, 6.00, "CARDBOARD FOR $1"),
                (6.00, 7.50, "AND A LUXURY YACHT"),
                (7.50, 9.50, "TO TEST SIDE BY SIDE!"),
                (9.50, 10.80, "HE BUILT A HULL,"),
                (10.80, 12.00, "ADDED A MOTOR,"),
                (12.00, 14.00, "AND DROPPED IT IN WATER!"),
                (14.00, 16.00, "THE BILLION DOLLAR YACHT"),
                (16.00, 17.50, "WAS FLOATING MAJESTICALLY..."),
                (17.50, 20.00, "WHILE THE $1 BOAT SANK!"),
            ]

    def get_hud_badges(self) -> List[Tuple[float, float, str]]:
        """Returns timeline HUD badges providing transformative value-add context."""
        if self.script and self.script.get("badges"):
            return [
                (float(start), float(end), str(text))
                for start, end, text in self.script["badges"]
            ]

        svml_name = self.svrun.author_svml.name.lower()
        if "challenge" in svml_name or "cops" in svml_name:
            return [
                (0.00, 3.75, "💰 $500,000 ESCAPE CHALLENGE"),
                (3.75, 13.52, "⏱️ TIMER STARTED | 100 COPS"),
                (13.52, 23.55, "🏃 EVADING PERIMETER | TRAPS SET"),
                (23.55, 33.62, "💥 DIVERSION ACTIVE | EXPLOSIONS"),
                (33.62, 43.15, "🚨 CORNERED | DEAD END"),
                (43.15, 50.42, "🚔 STATE POLICE TACTICAL RAID"),
                (50.42, 60.00, "🏆 OUTCOME: COPS WIN $500,000"),
            ]
        elif "nileblue" in svml_name or "experiment" in svml_name:
            return [
                (0.00, 3.75, "🧪 $1,000 CHEMICAL EXPERIMENT"),
                (3.75, 13.52, "🔬 RED BULL CHEMICAL SEPARATION"),
                (13.52, 23.55, "🌡️ THERMAL DISTILLATION BOIL"),
                (23.55, 33.62, "⚠️ DANGEROUS VOLATILE VAPOR"),
                (33.62, 43.15, "🧬 ACTIVATED CHARCOAL FILTER"),
                (43.15, 50.42, "💧 VACUUM CONDENSATION EXTRACT"),
                (50.42, 60.00, "🏆 100% PURE WATER CRYSTALLIZED"),
            ]
        elif "mark_rober" in svml_name or "gadget" in svml_name:
            return [
                (0.00, 3.75, "📐 NASA SQUIRREL OBSTACLE 2.0"),
                (3.75, 13.52, "🐿️ CONTENDER ENTERS ARENA"),
                (13.52, 23.55, "🚀 HIGH VELOCITY LAUNCH PAD"),
                (23.55, 33.62, "💨 GLITTER DEFENSE DETONATION"),
                (33.62, 43.15, "⚡ TITANIUM ROTATING BARRELS"),
                (43.15, 50.42, "🎯 ACROBATIC LEDGE RECOVERY"),
                (50.42, 60.00, "🏆 SQUIRREL CONQUERS FORTRESS"),
            ]
        elif "speed" in svml_name or "viral" in svml_name:
            return [
                (0.00, 3.75, "⚡ SPEED EXTREME STUNT"),
                (3.75, 13.52, "🏎️ LAMBORGHINI ACCELERATION"),
                (13.52, 23.55, "🏃 TAKEOFF SPRINT RUNWAY"),
                (23.55, 33.62, "😱 MID-AIR JUMP MOMENT"),
                (33.62, 43.15, "🔥 CLEARANCE ACHIEVED"),
                (43.15, 50.42, "💨 TWO-CAR CLEARANCE"),
                (50.42, 60.00, "🏆 SPEED CELEBRATION SIUUU"),
            ]
        elif "ruhi" in svml_name or "investigation" in svml_name:
            return [
                (0.00, 3.75, "❄️ YAKUTSK: -71°C RECORD COLD"),
                (3.75, 13.52, "🧤 ARCTIC SURVIVAL GEAR CHECK"),
                (13.52, 23.55, "⚠️ SEVERE FROSTBITE ZONE"),
                (23.55, 33.62, "🫁 INSTANT BREATH FREEZE"),
                (33.62, 43.15, "🚨 EQUIPMENT BATTERIES DYING"),
                (43.15, 50.42, "💨 BOILING WATER VAPORIZATION"),
                (50.42, 60.00, "🏆 ARCTIC EXPEDITION COMPLETE"),
            ]
        return []

    def generate_sfx_bed(self, output_wav_path: Path, duration: float = 60.0) -> Optional[Path]:
        """Synthesizes a master continuous SFX bed for the 6 structural curve peaks."""
        import wave, struct
        sfx_dir = ROOT_DIR / "assets" / "sfx"
        if not sfx_dir.exists():
            return None

        sr = 44100
        total_samples = int(sr * duration)
        bed = [0.0] * total_samples

        events = [
            (0.00, sfx_dir / "bass_impact.wav", 0.85),
            (13.52, sfx_dir / "whoosh.wav", 0.80),
            (23.55, sfx_dir / "boom_impact.wav", 0.90),
            (33.62, sfx_dir / "tape_stop.wav", 0.85),
            (43.15, sfx_dir / "radio_chirp.wav", 0.85),
            (50.42, sfx_dir / "cash_chime.wav", 1.05),
        ]

        has_sfx = False
        for t_start, wav_file, vol in events:
            if not wav_file.exists():
                continue
            has_sfx = True
            try:
                with wave.open(str(wav_file), "rb") as w:
                    n_ch = w.getnchannels()
                    sw = w.getsampwidth()
                    n_f = w.getnframes()
                    raw = w.readframes(n_f)
                    idx_start = int(t_start * sr)
                    for i in range(n_f):
                        idx = idx_start + i
                        if idx >= total_samples:
                            break
                        if sw == 2:
                            val = struct.unpack_from("<h", raw, i * n_ch * 2)[0] / 32768.0
                        else:
                            val = 0.0
                        bed[idx] += val * vol
            except Exception as e:
                print(f"[Hypit] Warning: Could not mix SFX {wav_file.name}: {e}")

        if not has_sfx:
            return None

        output_wav_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            raw_out = bytearray()
            for s in bed:
                clamped = max(-32767, min(32767, int(s * 32767)))
                raw_out.extend(struct.pack("<h", clamped))
            w.writeframes(raw_out)
        return output_wav_path

    def generate_subtitles(self, output_ass_path: Path) -> Path:
        """Generates dynamic kinetic subtitles following Hypit typography tokens."""
        font_name = "Source Sans Pro" if (FONTS_DIR / "SourceSansPro-Bold.ttf").exists() else ("Montserrat" if FONT_PATH.exists() else "DejaVu Sans")
        cues = self.get_cues()
        badges = self.get_hud_badges()

        # Responsive layout tokens derived from canvas bounds (Simple, clean, modern subtitle design)
        w = self.svml.canvas_width
        h = self.svml.canvas_height
        font_size = int(w * 0.054)
        font_size_title = int(w * 0.062)
        margin_v = int(h * 0.16)

        ass_content = f"""[Script Info]
Title: Hypit Dynamic Kinetic Subtitles
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Badge,{font_name},32,&H0000E6FF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,1,0,3,10,0,7,50,50,75,1
Style: Default,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: SimpleWhite,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: Highlight,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: TitleWhite,{font_name},{font_size_title},&H00FFFFFF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.2,1.5,5,50,50,0,1
Style: TitleYellow,{font_name},{font_size_title},&H0000E6FF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.2,1.5,5,50,50,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        # Layer 1: Transformative Value-Add HUD Badges (Top-Left)
        for start, end, text in badges:
            start_fmt = self._sec_to_ass_time(start)
            end_fmt = self._sec_to_ass_time(end)
            ass_content += f"Dialogue: 1,{start_fmt},{end_fmt},Badge,,0,0,0,,  {text}  \n"

        # Layer 0: Dialogue Subtitles & Hook Title (Bottom-Center & Mid-Screen)
        for i, (start, end, text) in enumerate(cues):
            start_fmt = self._sec_to_ass_time(start)
            end_fmt = self._sec_to_ass_time(end)
            if end <= 3.75:
                style = "TitleYellow" if "$" in text else "TitleWhite"
            else:
                style = "SimpleWhite"
            ass_content += f"Dialogue: 0,{start_fmt},{end_fmt},{style},,0,0,0,,{text}\n"

        output_ass_path.write_text(ass_content, encoding="utf-8")
        return output_ass_path

    @staticmethod
    def _sec_to_ass_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        cs = int((s - int(s)) * 100)
        return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

    def render(self, destination: Optional[Path] = None) -> Path:
        """Executes video composition through FFmpeg engine."""
        dest = destination or self.svrun.target_output
        if not dest:
            dest = ROOT_DIR / "output" / "hypit_output.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)

        raw_video = self.raw_video_override
        if not raw_video or not raw_video.exists():
            if self.creator == "mrbeast":
                raw_video = self.svrun.inputs.get("mrbeast_raw")
                if not raw_video or not raw_video.exists():
                    for k, v in self.svrun.inputs.items():
                        if isinstance(v, Path) and v.suffix.lower() in [".mp4", ".mov", ".mkv"] and v.exists():
                            raw_video = v
                            break
            else:
                raise FileNotFoundError(
                    f"Authentic raw source video for creator '{self.creator}' was not found. "
                    f"Refusing to substitute mismatched MrBeast footage."
                )
        if not raw_video or not raw_video.exists():
            raise FileNotFoundError(f"Input source video not found: {raw_video}")

        narrator_audio = self.svrun.inputs.get("narrator_intro")
        has_intro_narration = narrator_audio is not None and narrator_audio.exists()

        ass_path = dest.parent / "hypit_subtitles.ass"
        self.generate_subtitles(ass_path)

        w = self.svml.canvas_width
        h = self.svml.canvas_height
        duration = self.svml.timeline_duration

        print(f"[Hypit] 🎬 Compiling SVML project -> {dest.name}")
        print(f"[Hypit] 📐 Canvas: {w}x{h} @ {self.svml.fps}fps | Duration: {duration}s")
        print(f"[Hypit] 📥 Source: {raw_video}")
        if has_intro_narration:
            print(f"[Hypit] 🎙️ Intro Narration: {narrator_audio} (Deep Voice)")

        font_dir_arg = f":fontsdir={FONTS_DIR}" if FONTS_DIR.exists() else ""
        escaped_ass = str(ass_path).replace(":", "\\:").replace("'", "\\'")

        sfx_path = dest.parent / "hypit_sfx_bed.wav"
        sfx_bed = self.generate_sfx_bed(sfx_path, duration)
        has_sfx = sfx_bed is not None and sfx_bed.exists()
        if has_sfx:
            print(f"[Hypit] 🔊 Master SFX Bed: {sfx_bed} (6-Peak Audio Design)")

        raw_duration = _probe_duration(raw_video)
        arc_cuts = plan_arc_cuts(raw_duration, duration, has_intro_narration)
        if arc_cuts is not None:
            arc_trace = " -> ".join(f"{stage}@{start:.1f}s" for stage, start, _ in arc_cuts)
            print(f"[Hypit] ✂️ Story-Arc Cuts: {arc_trace}")
        elif raw_duration is None:
            print("[Hypit] ⚠️ ffprobe unavailable/failed — falling back to continuous render")
        else:
            print(
                f"[Hypit] ⚠️ Raw source ({raw_duration:.1f}s) too short for {duration:.1f}s "
                f"{len(ARC_STAGES)}-stage arc — falling back to continuous render"
            )

        use_arc = arc_cuts is not None
        pre_input_flags: List[str] = []
        input_files: List[str] = []

        if has_intro_narration:
            t_intro = 3.75
            t_main = duration - t_intro
            if use_arc:
                filter_complex = build_arc_filter_complex(
                    arc_cuts, w, h, escaped_ass, font_dir_arg,
                    has_narration=True, has_sfx=has_sfx,
                )
                input_files = [str(raw_video), str(narrator_audio)]
                if has_sfx:
                    input_files.append(str(sfx_bed))
            elif has_sfx:
                filter_complex = (
                    f"[0:v]trim=start=0:end={t_intro},setpts=PTS-STARTPTS,eq=contrast=1.12:brightness=-0.06:saturation=1.22[v_intro];"
                    f"[0:v]trim=start=0:end={t_main},setpts=PTS-STARTPTS,eq=contrast=1.06:saturation=1.15[v_main];"
                    f"[v_intro][v_main]concat=n=2:v=1:a=0,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:(iw-ow)/2:(ih-oh)/4,"
                    f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
                    f"[0:a]atrim=start=0:end={t_intro},asetpts=PTS-STARTPTS,volume=0.20,lowpass=f=1200[a_amb];"
                    f"[1:a]volume=1.40[a_vox];"
                    f"[a_amb][a_vox]amix=inputs=2:duration=first:dropout_transition=2[a_intro];"
                    f"[0:a]atrim=start=0:end={t_main},asetpts=PTS-STARTPTS,volume=1.10[a_main];"
                    f"[a_intro][a_main]concat=n=2:v=0:a=1[a_dialogue];"
                    f"[2:a]volume=0.90[a_sfx];"
                    f"[a_dialogue][a_sfx]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[a]"
                )
                input_files = [str(raw_video), str(narrator_audio), str(sfx_bed)]
            else:
                filter_complex = (
                    f"[0:v]trim=start=0:end={t_intro},setpts=PTS-STARTPTS,eq=contrast=1.12:brightness=-0.06:saturation=1.22[v_intro];"
                    f"[0:v]trim=start=0:end={t_main},setpts=PTS-STARTPTS,eq=contrast=1.06:saturation=1.15[v_main];"
                    f"[v_intro][v_main]concat=n=2:v=1:a=0,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:(iw-ow)/2:(ih-oh)/4,"
                    f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
                    f"[0:a]atrim=start=0:end={t_intro},asetpts=PTS-STARTPTS,volume=0.20,lowpass=f=1200[a_amb];"
                    f"[1:a]volume=1.40[a_vox];"
                    f"[a_amb][a_vox]amix=inputs=2:duration=first:dropout_transition=2[a_intro];"
                    f"[0:a]atrim=start=0:end={t_main},asetpts=PTS-STARTPTS,volume=1.10[a_main];"
                    f"[a_intro][a_main]concat=n=2:v=0:a=1,alimiter=limit=0.95[a]"
                )
                input_files = [str(raw_video), str(narrator_audio)]
        else:
            if use_arc:
                filter_complex = build_arc_filter_complex(
                    arc_cuts, w, h, escaped_ass, font_dir_arg,
                    has_narration=False, has_sfx=False,
                )
                input_files = [str(raw_video)]
            else:
                filter_complex = (
                    f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                    f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/4,"
                    f"eq=contrast=1.06:saturation=1.15,"
                    f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
                    f"[0:a]volume=1.10,alimiter=limit=0.95[a]"
                )
                pre_input_flags = ["-ss", "0.0", "-t", str(duration)]
                input_files = [str(raw_video)]

        cmd = ["ffmpeg", "-y", *pre_input_flags]
        for src in input_files:
            cmd += ["-i", src]
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(dest)
        ]

        print("[Hypit] 🚀 Running render pipeline...")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            print(f"[Hypit] ❌ FFmpeg error:\n{proc.stderr}")
            raise RuntimeError(f"FFmpeg rendering failed with exit code {proc.returncode}")

        # Cleanup intermediate files
        if ass_path.exists():
            ass_path.unlink()
        if sfx_path.exists():
            sfx_path.unlink()

        print(f"[Hypit] ✅ Successfully rendered deliverable: {dest} ({dest.stat().st_size} bytes)")
        return dest


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Hypit Video Authoring & Render Engine")
    parser.add_argument("manifest", nargs="?", default="sources/production.svrun", help="Path to .svrun manifest")
    parser.add_argument("--creator", choices=["mrbeast", "nileblue", "mark_rober", "ishowspeed", "ruhi_cenet"], help="Override creator SVML source")
    parser.add_argument("--raw-video", help="Path to custom raw input video file")
    parser.add_argument("--output", "-o", help="Custom output destination path")
    parser.add_argument("--script-json", help="Per-video hook->payoff script (cues + HUD badges)")
    parser.add_argument("--check", action="store_true", help="Validate SVML / SVRun without rendering")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    raw_video_path = Path(args.raw_video).resolve() if args.raw_video else None
    script_path = Path(args.script_json).resolve() if args.script_json else None
    engine = HypitEngine(
        manifest_path, raw_video=raw_video_path, creator=args.creator, script_path=script_path
    )

    if args.creator:
        catalog_path = ROOT_DIR / "creator_catalog.json"
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            creator_data = catalog.get("creators", {}).get(args.creator, {})
            svml_src = creator_data.get("svml_source")
            if svml_src:
                resolved_svml = (ROOT_DIR / svml_src).resolve()
                engine.svrun.author_svml = resolved_svml
                engine.svml = HypitSVMLParser(resolved_svml).parse()

    if args.check:
        print(f"[Hypit] ✅ Validation passed for {manifest_path}")
        print(f"Author SVML: {engine.svrun.author_svml}")
        print(f"Canvas: {engine.svml.canvas_width}x{engine.svml.canvas_height} @ {engine.svml.fps}fps")
        print(f"Duration: {engine.svml.timeline_duration}s")
        print(f"Windows: {list(engine.svml.windows.keys())}")
        print(f"Script segments: {list(engine.svml.script_segments.keys())}")
        print(f"Inputs: {list(engine.svrun.inputs.keys())}")
        return

    output_path = Path(args.output).resolve() if args.output else None
    engine.render(destination=output_path)


if __name__ == "__main__":
    main()
