"""Word-by-word captions (the 2025-26 Shorts / Reels / TikTok look) plus the hook title and credit,
all in one ASS file so libass draws everything in a single ffmpeg pass.

  * faster-whisper gives word timestamps (free, CPU, ~1 min for 3 clips on GitHub Actions)
  * 1-3 words on screen at a time, ALL CAPS, heavy font, thick black outline
  * the word being said pops (scale) and turns yellow; the rest stay white
  * Whisper's invented words during game music / explosions are dropped (low probability)
  * hook title: black text on a white box at the top for the first seconds (Instagram style)
"""
import re
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+")

_model = None


def transcribe(media, model_size="small", language=None):
    """[{'w','s','e'}] words with times in seconds, or [] when faster-whisper isn't installed."""
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("   ! faster-whisper not installed: pip install faster-whisper (captions skipped)", flush=True)
        return []
    if _model is None:
        print(f"   Loading Whisper '{model_size}' (first run downloads it)...", flush=True)
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # decode with ffmpeg ourselves (avoids PyAV version issues on Termux / CI)
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    if not raw:
        return []
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segs, _info = _model.transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                                    vad_parameters={"min_silence_duration_ms": 300},
                                    condition_on_previous_text=False)
    words = []
    for seg in segs:
        if seg.no_speech_prob > 0.7:
            continue
        for w in seg.words or []:
            text = w.word.strip()
            if not text or w.probability < 0.35:
                continue
            words.append({"w": text, "s": float(w.start), "e": float(w.end)})
    return words


def _clean(t):
    t = EMOJI_RE.sub("", t)
    t = re.sub(r"[{}\\]", "", t)
    return t.strip()


def group_words(words, max_words=3, max_chars=16, max_gap=0.45):
    groups, cur = [], []
    for w in words:
        txt = _clean(w["w"]).upper()
        if not txt:
            continue
        w = dict(w, w=txt)
        if cur and (len(cur) >= max_words or w["s"] - cur[-1]["e"] > max_gap
                    or len(" ".join(x["w"] for x in cur + [w])) > max_chars
                    or cur[-1]["w"][-1:] in ".?!,"):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def snap_speech_boundaries(words, raw_duration, min_duration=40.0, tail_window=8.0):
    """Find a natural sentence ending or speech pause within the tail of the clip.
    Avoids cutting the streamer off mid-sentence or mid-word.
    Returns snapped_duration (float).
    """
    effective_min = min(float(min_duration), float(raw_duration))
    if not words or raw_duration <= effective_min:
        return float(raw_duration)

    last_word = words[-1]
    last_speech_end = float(last_word.get("e", raw_duration))
    tail_start = max(effective_min, raw_duration - tail_window)
    tail_words = [w for w in words if float(w.get("s", 0)) >= tail_start]

    best_snap = None
    # 1. Punctuation mark indicating end of sentence/reaction (. ! ?)
    for w in tail_words:
        txt = str(w.get("w", "")).strip()
        end_t = float(w.get("e", 0))
        if txt.endswith((".", "!", "?")):
            best_snap = end_t + 0.35

    # 2. Significant pause between words (pause >= 0.4s)
    if best_snap is None and len(tail_words) >= 2:
        for i in range(len(tail_words) - 1):
            curr_e = float(tail_words[i].get("e", 0))
            next_s = float(tail_words[i + 1].get("s", 0))
            if next_s - curr_e >= 0.4:
                best_snap = curr_e + 0.25

    # 3. Speech completely ceased within the tail window (avoid cutting when speech stopped early during laughing/gameplay)
    if best_snap is None and last_speech_end >= tail_start and last_speech_end < raw_duration - 0.6:
        best_snap = last_speech_end + 0.4

    if best_snap is not None:
        snapped = max(effective_min, min(raw_duration, best_snap))
        return round(float(snapped), 2)

    return float(raw_duration)



def _ts(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _wrap(text, width=20):
    """Balanced 1-2 line wrap (no lonely last word)."""
    words = text.split()
    if len(text) <= width or len(words) < 2:
        return text
    best = min(range(1, len(words)),
               key=lambda k: max(len(" ".join(words[:k])), len(" ".join(words[k:]))))
    return " ".join(words[:best]) + r"\N" + " ".join(words[best:])


HIGHLIGHT = "&H0000E5FF&"  # yellow (ASS is BGR)
WHITE = "&H00FFFFFF&"


def build_ass(out_path, words, duration, caption_y=1000, hook="", hook_secs=3.5, credit="",
              font="Montserrat Black", hook_font="Montserrat Black", credit_v=180,
              ig_comment=None, style=None):
    if style is None:
        style = "ig-comment" if ig_comment else "word"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,1,0,1,7,4,5,40,40,0,1
Style: Hook,{hook_font},64,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,22,0,8,90,90,170,1
Style: Credit,{font},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,1,0,1,3,1,8,40,40,{credit_v},1
Style: Card,Montserrat Black,10,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: IGText,Montserrat Black,38,&H00000000,&H00000000,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Badge,{font},32,&H0000E6FF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,1,0,3,10,0,7,50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if style == "word":
        pos = rf"\pos(540,{caption_y})"
        groups = group_words(words)
        for gi, g in enumerate(groups):
            nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else duration
            g_end = min(duration, nxt, g[-1]["e"] + 0.25)
            for i, w in enumerate(g):
                s = w["s"]
                e = g[i + 1]["s"] if i + 1 < len(g) else g_end
                if e - s < 0.05:
                    continue
                parts = []
                for j, x in enumerate(g):
                    if j == i:
                        parts.append(r"{\c" + HIGHLIGHT + r"\fscx112\fscy112}" + x["w"] + r"{\c" + WHITE + r"\fscx100\fscy100}")
                    else:
                        parts.append(x["w"])
                pop = r"{\fscx85\fscy85\t(0,90,\fscx100\fscy100)}" if i == 0 else ""
                ev.append(f"Dialogue: 1,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{{{pos}}}{pop}{' '.join(parts)}")
        hook = _clean(hook or "")
        if hook:
            ev.append(f"Dialogue: 2,{_ts(0)},{_ts(min(hook_secs, duration))},Hook,,0,0,0,,"
                      r"{\fad(0,250)\fscx90\fscy90\t(0,120,\fscx100\fscy100)}" + _wrap(hook.upper()))
    elif style == "ig-comment" and ig_comment:
        card_w, card_h = 900, 220
        card_x = (1080 - card_w) // 2  # 90
        card_y = caption_y - (card_h // 2)  # 590
        text = ig_comment.get("text", "bro was NOT ready for GTA 6 💀💀")
        uname = ig_comment.get("username", "vicecity_daily")
        time_ago = ig_comment.get("time", "2h")
        likes = ig_comment.get("likes", "38.4K")

        # Layer 0: Shadow & White Card (vector path with rounded corners)
        ev.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Card,,0,0,0,,{{\\pos({card_x},{card_y})\\p1\\c&H00FFFFFF&\\bord6\\3c&H20000000&\\shad8\\4c&H30000000&}}"
                  f"m 30 0 l 870 0 b 885 0 900 15 900 30 l 900 190 b 900 205 885 220 870 220 l 30 220 b 15 220 0 205 0 190 l 0 30 b 0 15 15 0 30 0\\p0")
        # Layer 1: Instagram Avatar Ring (Sunset gradient)
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Card,,0,0,0,,{{\\pos({card_x + 40},{card_y + 45})\\p1\\c&H007B2ADD&\\bord3\\3c&H002985F5&}}"
                  f"m 0 25 b 0 11 11 0 25 0 b 39 0 50 11 50 25 b 50 39 39 50 25 50 b 11 50 0 39 0 25\\p0")
        # Layer 1: Inner Avatar Fill
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Card,,0,0,0,,{{\\pos({card_x + 45},{card_y + 50})\\p1\\c&H0040342D&}}"
                  f"m 0 20 b 0 9 9 0 20 0 b 31 0 40 9 40 20 b 40 31 31 40 20 40 b 9 40 0 31 0 20\\p0")
        # Layer 2: Username and Timestamp
        clean_uname = uname.lstrip('@')
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(duration)},IGText,,0,0,0,,{{\\pos({card_x + 110},{card_y + 42})}}"
                  f"{{\\fs32\\b1\\c&H00333333&}}{clean_uname} {{\\fs26\\b0\\c&H00888888&}}· {time_ago}")
        # Layer 2: Comment Text
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(duration)},IGText,,0,0,0,,{{\\pos({card_x + 40},{card_y + 102})}}"
                  f"{{\\fs40\\b1\\c&H00111111&}}{text}")
        # Layer 2: Reply and Likes (separate events for clean layout)
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(duration)},IGText,,0,0,0,,{{\\pos({card_x + 40},{card_y + 158})}}"
                  f"{{\\fs24\\b0\\c&H00777777&}}Reply")
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(duration)},IGText,,0,0,0,,{{\\pos({card_x + 720},{card_y + 158})}}"
                  f"{{\\fs26\\c&H002424ED&}}♥ {{\\fs24\\b0\\c&H00777777&}}{likes}")

    # Story-arc stage HUD badges (woo's clips benchmark format: HOOK -> SETUP -> CLIMAX -> PAYOFF)
    if duration >= 25.0:
        t_hook = min(4.5, duration * 0.10)
        t_setup = duration * 0.52
        t_climax = duration * 0.78
        stages = [
            (0.0, t_hook, "[ 01 · THE HOOK ]"),
            (t_hook, t_setup, "[ 02 · THE SETUP ]"),
            (t_setup, t_climax, "[ 03 · THE CLIMAX ]"),
            (t_climax, duration, "[ 04 · THE PAYOFF ]"),
        ]
        for s_t, e_t, label in stages:
            ev.append(f"Dialogue: 3,{_ts(s_t)},{_ts(e_t)},Badge,,0,0,0,,  {label}  ")

    credit = _clean(credit or "")
    if credit:
        ev.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Credit,,0,0,0,,{{\\fad(200,0)\\alpha&H30&}}{credit}")
    Path(out_path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return out_path
