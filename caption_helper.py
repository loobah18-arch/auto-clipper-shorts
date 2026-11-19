#!/usr/bin/env python3
"""
Caption Helper for Chat Clip Finder (Step 9 of the Guide)

Generates clean vertical-optimized subtitles (ASS kinetic neon or SRT/JSON cues) and burns them
onto vertical 9:16 clips using FFmpeg. Implements the narrator-v2 kinetic neon glow style from anidoc.

Usage:
    python3 caption_helper.py <clip.mp4> [options]

Options:
    --subtitles <file.json> Burn timed kinetic neon subtitles from JSON cues (word or phrase level)
    --ass <file.ass>        Burn an existing ASS subtitle file
    --srt <file.srt>        Burn an existing SRT subtitle file
    --text "Text here"      Burn a fixed hook text (e.g. "WAIT FOR IT...", "CHAT LOST IT HERE")
    --out <file.mp4>        Output file path (default: <input>_captioned.mp4)
    --style <style>         Subtitle styling preset: 'bold_yellow', 'clean_white', 'tiktok'
"""

import sys
import re
import json
import subprocess
from pathlib import Path


NEON_COLORS = ['#FF3B5C', '#FFC533', '#3DFF7A', '#33D6FF', '#B266FF']  # red/pink, gold, green, cyan, purple


def get_default_font():
    """Detect available font on Android Termux or Linux."""
    candidates = [
        "/system/fonts/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def ass_col(hexc, a='00'):
    """Convert HEX color #RRGGBB to ASS &HAABBGGRR& color string."""
    r, g_, b = hexc[1:3], hexc[3:5], hexc[5:7]
    return f'&H{a}{b}{g_}{r}&'


def ts(t):
    """Format seconds into ASS timestamp string (H:MM:SS.cs)."""
    t = max(0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f'{h}:{m:02d}:{s:05.2f}'


def build_narrator_ass(cues, out_path, y_pos=1574, font_name="Roboto"):
    """
    Builds a two-layer kinetic neon ASS subtitle file matching the anidoc narrator-v2 style.
    Layer 0: Wide neon colored aura glow (bord18 blur16).
    Layer 1: Crisp text with colored outline and soft shadow (bord4 blur3 shad3).
    Kinetic reveal: Spoken words white (&H00FFFFFF&), future words dimmed (&H00A0A0A0&).
    """
    W, H = 1080, 1920
    ass = [f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font_name},76,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H96000000,1,0,0,0,100,100,2,0,1,0,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""]

    DIM = '&H00A0A0A0&'
    for idx, item in enumerate(cues):
        col = item.get("color", NEON_COLORS[idx % len(NEON_COLORS)])
        if not col.startswith("#"):
            col_map = {"yellow": "#FFC533", "white": "#33D6FF", "red": "#FF3B5C", "green": "#3DFF7A", "cyan": "#33D6FF"}
            col = col_map.get(col.lower(), NEON_COLORS[idx % len(NEON_COLORS)])

        if "words" in item and item["words"]:
            words = item["words"]
        else:
            # Fallback for phrase-only cues: split into evenly spaced words
            raw_words = item["text"].split()
            st = float(item["start"])
            en = float(item["end"])
            dur_per_word = (en - st) / max(1, len(raw_words))
            words = [{"w": w, "s": round(st + i * dur_per_word, 2), "e": round(st + (i + 1) * dur_per_word, 2)} for i, w in enumerate(raw_words)]

        toks = [w["w"] for w in words]
        if not toks:
            continue

        split = len(toks) if len(toks) <= 3 or sum(map(len, toks)) <= 14 else (len(toks) + 1) // 2

        for k in range(len(toks)):
            a = words[k]["s"]
            b = words[k + 1]["s"] if k + 1 < len(words) else words[-1]["e"]
            if b <= a:
                continue

            parts = []
            for m, t in enumerate(toks):
                color_code = '{\\1c&H00FFFFFF&}' if m <= k else '{\\1c' + DIM + '}'
                parts.append(color_code + t)
                if m == split - 1 and m != len(toks) - 1:
                    parts.append('\\N')
                elif m != len(toks) - 1:
                    parts.append(' ')

            txt = ''.join(parts).replace(' \\N', '\\N')
            pop = '{\\fscx106\\fscy106\\t(0,90,\\fscx100\\fscy100)}' if k == 0 else ''
            glow = f'{{\\pos({W // 2},{y_pos})\\bord18\\blur16\\3c{ass_col(col)}\\3a&H20&\\shad0\\1a&HFF&}}'
            main = f'{{\\pos({W // 2},{y_pos})\\bord4\\blur3\\3c{ass_col(col)}\\shad3}}'
            ass.append(f'Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{glow}{pop}{txt}')
            ass.append(f'Dialogue: 1,{ts(a)},{ts(b)},Cap,,0,0,0,,{main}{pop}{txt}')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass) + "\n")
    return out_path


def burn_ass_subtitles(input_mp4, ass_file, output_mp4, fontsdir="/system/fonts"):
    """Burns ASS subtitles into vertical 9:16 clip using libass filter."""
    vf_arg = f"ass={ass_file}"
    if fontsdir and Path(fontsdir).exists():
        vf_arg += f":fontsdir={fontsdir}"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-i", input_mp4,
        "-vf", vf_arg,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        output_mp4
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Created kinetic subtitled clip: {output_mp4}")


STYLE_PRESETS = {
    "bold_yellow": (
        "Fontname=Arial,FontSize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
        "BackColour=&H80000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=120"
    ),
    "clean_white": (
        "Fontname=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=120"
    ),
    "tiktok": (
        "Fontname=Impact,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=4,Shadow=2,Alignment=2,MarginV=140"
    ),
}


def sanitize_text(text):
    """Strip characters that cause missing-glyph tofu boxes in standard FFmpeg fonts."""
    cleaned = re.sub(r'[^\x20-\x7E]', '', text)
    return cleaned.strip() or "WAIT FOR IT..."


def burn_text(input_mp4, text, output_mp4, duration=3.5):
    """Burns an opening hook text into the first few seconds of a vertical clip with a smooth fade-out."""
    clean = sanitize_text(text)
    escaped_text = clean.replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    fade_expr = f"if(lt(t,{duration - 0.5}),1,if(lt(t,{duration}),({duration}-t)/0.5,0))"
    drawtext_filter = (
        f"drawtext=text='{escaped_text}':fontcolor=white:fontsize=52:box=1:"
        f"boxcolor=black@0.75:boxborderw=24:x=(w-text_w)/2:y=h*0.16:"
        f"alpha='{fade_expr}'"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", input_mp4,
        "-vf", drawtext_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        output_mp4
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Created hook-captioned clip: {output_mp4}")


def burn_subtitles(input_mp4, srt_file, output_mp4, style="bold_yellow"):
    """Burns SRT subtitle track into vertical 9:16 clip."""
    style_str = STYLE_PRESETS.get(style, STYLE_PRESETS["bold_yellow"])
    sub_filter = f"subtitles='{srt_file}':force_style='{style_str}'"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", input_mp4,
        "-vf", sub_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        output_mp4
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Created subtitled clip: {output_mp4}")


def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    input_file = args[0]
    in_path = Path(input_file)
    if not in_path.exists():
        print(f"Error: Video file not found: {input_file}", file=sys.stderr)
        sys.exit(1)

    out_file = None
    hook_text = None
    srt_file = None
    json_sub_file = None
    ass_file = None
    style = "bold_yellow"
    pos = "bottom"
    layout = "blur"

    i = 1
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_file = args[i + 1]
            i += 2
        elif args[i] == "--text" and i + 1 < len(args):
            hook_text = args[i + 1]
            i += 2
        elif (args[i] == "--subtitles" or args[i] == "--json") and i + 1 < len(args):
            json_sub_file = args[i + 1]
            i += 2
        elif args[i] == "--ass" and i + 1 < len(args):
            ass_file = args[i + 1]
            i += 2
        elif args[i] == "--srt" and i + 1 < len(args):
            srt_file = args[i + 1]
            i += 2
        elif args[i] == "--style" and i + 1 < len(args):
            style = args[i + 1]
            i += 2
        elif args[i] == "--pos" and i + 1 < len(args):
            pos = args[i + 1]
            i += 2
        elif args[i] == "--layout" and i + 1 < len(args):
            layout = args[i + 1]
            i += 2
        else:
            i += 1

    if not out_file:
        out_file = str(in_path.with_name(f"{in_path.stem}_captioned{in_path.suffix}"))

    if json_sub_file:
        with open(json_sub_file, "r", encoding="utf-8") as f:
            cues = json.load(f)
        temp_ass = in_path.with_name(f"{in_path.stem}_temp.ass")
        # Position subtitle in middle (y=800) for stack layout or if --pos middle is passed
        y_coord = 800 if (pos == "middle" or layout == "stack") else 1574
        build_narrator_ass(cues, str(temp_ass), y_pos=y_coord)
        burn_ass_subtitles(input_file, str(temp_ass), out_file)
        if temp_ass.exists():
            temp_ass.unlink()
    elif ass_file:
        burn_ass_subtitles(input_file, ass_file, out_file)
    elif srt_file:
        burn_subtitles(input_file, srt_file, out_file, style=style)
    elif hook_text:
        burn_text(input_file, hook_text, out_file)
    else:
        print("Please provide --subtitles 'cues.json', --ass 'subs.ass', --text 'hook text', or --srt 'subtitles.srt'.")


if __name__ == "__main__":
    main()
