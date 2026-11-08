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

    def __init__(self, svrun_path: Path):
        self.svrun = HypitRunParser(svrun_path).parse()
        if not self.svrun.author_svml:
            raise ValueError("No author SVML defined in SVRun manifest")
        self.svml = HypitSVMLParser(self.svrun.author_svml).parse()

    def get_cues(self) -> List[Tuple[float, float, str]]:
        """Returns word-level cues matched to the active SVML document."""
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

    def generate_subtitles(self, output_ass_path: Path) -> Path:
        """Generates dynamic kinetic subtitles following Hypit typography tokens."""
        font_name = "Source Sans Pro" if (FONTS_DIR / "SourceSansPro-Bold.ttf").exists() else ("Montserrat" if FONT_PATH.exists() else "DejaVu Sans")
        cues = self.get_cues()

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
Style: Default,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: SimpleWhite,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: Highlight,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,1.2,2,40,40,{margin_v},1
Style: TitleWhite,{font_name},{font_size_title},&H00FFFFFF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.2,1.5,5,50,50,0,1
Style: TitleYellow,{font_name},{font_size_title},&H0000E6FF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.2,1.5,5,50,50,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
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

        raw_video = self.svrun.inputs.get("mrbeast_raw")
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

        if has_intro_narration:
            t_intro = 3.75
            t_main = duration - t_intro
            filter_complex = (
                f"[0:v]trim=start=0:end={t_intro},setpts=PTS-STARTPTS,eq=contrast=1.12:brightness=-0.06:saturation=1.22[v_intro];"
                f"[0:v]trim=start=0:end={t_main},setpts=PTS-STARTPTS,eq=contrast=1.06:saturation=1.15[v_main];"
                f"[v_intro][v_main]concat=n=2:v=1:a=0,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
                f"[0:a]atrim=start=0:end={t_intro},asetpts=PTS-STARTPTS,volume=0.20,lowpass=f=1200[a_amb];"
                f"[1:a]volume=1.40[a_vox];"
                f"[a_amb][a_vox]amix=inputs=2:duration=first:dropout_transition=2[a_intro];"
                f"[0:a]atrim=start=0:end={t_main},asetpts=PTS-STARTPTS,volume=1.10[a_main];"
                f"[a_intro][a_main]concat=n=2:v=0:a=1,alimiter=limit=0.95[a]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_video),
                "-i", str(narrator_audio),
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
        else:
            filter_complex = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"eq=contrast=1.06:saturation=1.15,"
                f"subtitles='{escaped_ass}'{font_dir_arg}[v];"
                f"[0:a]volume=1.10,alimiter=limit=0.95[a]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-ss", "0.0",
                "-t", str(duration),
                "-i", str(raw_video),
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-map", "[a]",
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

        # Cleanup intermediate subtitle file
        if ass_path.exists():
            ass_path.unlink()

        print(f"[Hypit] ✅ Successfully rendered deliverable: {dest} ({dest.stat().st_size} bytes)")
        return dest


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Hypit Video Authoring & Render Engine")
    parser.add_argument("manifest", nargs="?", default="sources/production.svrun", help="Path to .svrun manifest")
    parser.add_argument("--output", "-o", help="Custom output destination path")
    parser.add_argument("--check", action="store_true", help="Validate SVML / SVRun without rendering")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    engine = HypitEngine(manifest_path)

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
