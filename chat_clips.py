#!/usr/bin/env python3
"""
chat-clips: let chat (or the audio) find the best moments of a stream, then turn them into
ready-to-post vertical Shorts / Reels / TikToks: word-by-word captions, hook title, facecam stack.

Usage:
    python3 chat_clips.py <chat.json | Twitch URL | YouTube URL> [options]

Sources:
    chat.json                 TwitchDownloader chat file, or a yt-dlp *.live_chat.json (YouTube)
    --url <link>              Twitch VOD or YouTube video / past live stream: chat and clips are
                              downloaded automatically (YouTube uses your cookies file)
    --vod <ID/URL>            (old name for --url, Twitch)
    --video <file.mp4>        Cut from a local video you already have
    --no-chat                 Find moments from loudness spikes instead (gameplay videos, trailers)
    --cookies <file>          YouTube cookies (default: youtube_cookies.txt / cookies.txt here)
    --proxy <url>             Proxy for YouTube (or $YOUTUBE_PROXY)

Finding moments:
    --game <gta6|none>        Game-tuned reactions & titles (default: gta6)
    --emotes                  Count only reaction messages (LMAO, KEKW, 💀, WTF, WASTED...), weighted
    --min-score <n>           Minimum spike multiplier (default 1.0)
    --top <n>                 Number of clips (default 5)
    --before / --after <s>    Seconds before / after the spike (default 20 / 10)
    --gap <s>                 Minimum seconds between clips (default 90)
    --bucket <s>              Counting window (default 10)
    --skip-start / --skip-end <s>  Ignore "Starting soon" / ending screen (default 120 / 60)

Making the Short:
    --layout <auto|stack|gameplay|blur|crop>   (default auto: stack if a webcam is found, else gameplay)
    --cam <auto|top-left|top-right|bottom-left|bottom-right>   Webcam position (default auto-detect)
    --captions <on|off>       Word-by-word captions with Whisper (default on)
    --whisper-model <name>    tiny / base / small / medium (default small)
    --lang <code>             Spoken language, e.g. en, hi (default: auto-detect)
    --hook <text>             Fixed hook title for every clip (default: picked per clip from chat)
    --credit <name>           Streamer name shown on screen and in the description
    --out <dir>               Output folder (default clips)
    --tdl <path>              TwitchDownloaderCLI path
    --dry-run                 Only find moments and write moments.json
"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from ccf import chat as chatmod
from ccf import sources, titles
from ccf.timefmt import hms


def format_hms(seconds):
    return hms(seconds)


def calculate_median(arr):
    if not arr:
        return 0
    s = sorted(arr)
    return s[len(s) // 2]


def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("source", nargs="?")
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--url"); p.add_argument("--vod"); p.add_argument("--video")
    p.add_argument("--no-chat", action="store_true")
    p.add_argument("--cookies"); p.add_argument("--proxy")
    p.add_argument("--game", default="gta6")
    p.add_argument("--emotes", "--filter-emotes", action="store_true")
    p.add_argument("--min-score", type=float, default=1.0)
    p.add_argument("--min-msgs", type=int, default=0, help="Minimum raw chat messages in the spike window (default 0)")
    p.add_argument("--target-min", type=float, default=20.0, help="Shortest finished clip in seconds (default 20)")
    p.add_argument("--target-max", type=float, default=35.0, help="Longest finished clip in seconds (default 35)")
    p.add_argument("--payoff-lead", type=float, default=3.0, help="Seconds of lead-in before the payoff (default 3)")
    p.add_argument("--tries", type=int, default=3, help="Backup moments per wanted clip when one fails the quality gate")
    p.add_argument("--title-history", default="title_history.json", help="Title history file ('' to disable)")
    p.add_argument("--no-quality-gate", action="store_true", help="Do not skip dark / static moments")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--before", type=int, default=20)
    p.add_argument("--after", type=int, default=10)
    p.add_argument("--chat-delay", type=float, default=0.0, help="Stream latency compensation in seconds (default 0.0)")
    p.add_argument("--gap", type=int, default=90)
    p.add_argument("--bucket", type=int, default=10)
    p.add_argument("--skip-start", type=int, default=120)
    p.add_argument("--skip-end", type=int, default=60)
    p.add_argument("--layout", default="stack", choices=["auto", "stack", "gameplay", "blur", "crop"])
    p.add_argument("--require-cam", action="store_true", default=True, help="skip candidate if no webcam detected")
    p.add_argument("--no-require-cam", dest="require_cam", action="store_false")
    p.add_argument("--cam", default="auto", choices=["auto", "top-left", "top-right", "bottom-left", "bottom-right"])
    p.add_argument("--captions", default="both", choices=["both", "ig-comment", "word", "on", "off"])
    p.add_argument("--whisper-model", default="small")
    p.add_argument("--lang")
    p.add_argument("--hook", default="")
    p.add_argument("--credit", default="")
    p.add_argument("--out", default="clips")
    p.add_argument("--min-height", type=int, default=720, help="Minimum source video height (default 720)")
    p.add_argument("--tdl")
    p.add_argument("--dry-run", action="store_true")
    a, unknown = p.parse_known_args(argv)
    if a.help or (not a.source and not a.url and not a.vod and not (a.no_chat and a.video)):
        print(__doc__)
        sys.exit(0 if a.help else 1)
    if unknown:
        print(f"Error: unknown option(s): {' '.join(unknown)}  (see --help)", file=sys.stderr)
        sys.exit(2)
    a.url = a.url or a.vod
    if a.source and sources.kind_of(a.source) in ("youtube", "twitch") and not Path(a.source).exists():
        a.url, a.source = a.url or a.source, None
    return a


def resolve_tdl(custom_path=None):
    import os
    if custom_path:
        return custom_path
    for p in ("./TwitchDownloaderCLI", "./TwitchDownloaderCLI.exe"):
        if os.path.exists(p):
            return p
    return "TwitchDownloaderCLI.exe" if sys.platform == "win32" else "TwitchDownloaderCLI"


def bucket_counts(a, messages, length):
    nb = math.ceil(length / a.bucket) + 1
    counts = [0] * nb
    use_weights = a.emotes
    for m in messages:
        i = int(m["t"] // a.bucket)
        if i >= nb:
            continue
        if use_weights:
            w, _ = chatmod.classify(m["text"], a.game)
            counts[i] += w
        else:
            counts[i] += 1
    if use_weights:
        counts = [round(c, 2) for c in counts]
    return counts


def raw_bucket_counts(a, messages, length):
    """Plain message count per bucket (every message, no reaction weighting)."""
    nb = math.ceil(length / a.bucket) + 1
    raw = [0] * nb
    for m in messages:
        i = int(m["t"] // a.bucket)
        if 0 <= i < nb:
            raw[i] += 1
    return raw


def find_peaks(a, counts, raw=None, limit=None):
    nb = len(counts)
    around = max(1, round(300 / a.bucket))
    scores = []
    for i in range(nb):
        base = calculate_median(counts[max(0, i - around): min(nb, i + around + 1)])
        scores.append(counts[i] / (base + 1.0))
    first_ok = math.ceil(a.skip_start / a.bucket) if nb * a.bucket > (a.skip_start + a.skip_end) * 3 else 0
    last_ok = nb - 1 - (math.ceil(a.skip_end / a.bucket) if first_ok else 0)
    peaks = []
    limit = limit or a.top
    min_msgs = getattr(a, "min_msgs", 0) or 0
    for idx in sorted(range(nb), key=lambda i: scores[i], reverse=True):
        if len(peaks) >= limit:
            break
        if counts[idx] < 5 or scores[idx] < a.min_score or not (first_ok <= idx <= last_ok):
            continue
        if raw is not None and min_msgs and raw[idx] < min_msgs:
            continue
        t = idx * a.bucket
        if any(abs(p["t"] - t) < a.gap for p in peaks):
            continue
        msgs = counts[idx]
        peaks.append({"t": t, "msgs": int(msgs) if float(msgs).is_integer() else msgs, "score": round(scores[idx], 1),
                      "raw_msgs": raw[idx] if raw is not None and idx < len(raw) else None})
    peaks.sort(key=lambda p: p["t"])
    return peaks


def ascii_graph(counts, total_length):
    bars = " ▁▂▃▄▅▆▇█"
    per = max(1, math.ceil(len(counts) / 60))
    cols = [max(counts[k * per:(k + 1) * per] or [0]) for k in range(math.ceil(len(counts) / per))]
    top = max(cols) if cols and max(cols) > 0 else 1
    g = "".join(bars[min(8, round(c / top * 8))] for c in cols)
    end = hms(total_length)
    return f"{g}\n0:00:00{' ' * max(1, len(cols) - len(end) - 7)}{end}"


def main(argv=None):
    a = parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tdl = resolve_tdl(a.tdl)
    info = {"title": "", "channel": "", "duration": 0.0}

    # 1. Signal: chat (file or downloaded) or loudness
    messages, length, chat_file = [], 0.0, None
    raw_counts = None
    if a.source:
        chat_file = Path(a.source)
        if not chat_file.exists():
            print(f"Error: Chat file not found: {chat_file}", file=sys.stderr)
            sys.exit(1)
    elif a.url and not a.no_chat:
        print(f"💬 Downloading chat replay for {a.url} ...", flush=True)
        chat_file = sources.fetch_chat(a.url, out_dir, a.cookies, a.proxy, tdl)
        if not chat_file:
            print("   ! This video has no chat replay. Falling back to loudness spikes (--no-chat).", flush=True)
            a.no_chat = True

    if chat_file and not a.no_chat:
        try:
            messages, length = chatmod.load_chat(chat_file)
        except Exception as e:
            print(f"Error reading chat from {chat_file}: {e}", file=sys.stderr)
            sys.exit(1)
        if a.emotes:
            messages_used = [m for m in messages if chatmod.classify(m["text"], a.game)[0] > 0]
        else:
            messages_used = messages
        if not messages_used:
            print("Error: No messages matched reaction emotes/keywords. Try without --emotes." if a.emotes
                  else "Error: No chat messages found. Is this a TwitchDownloader / YouTube live-chat file?",
                  file=sys.stderr)
            sys.exit(1)
        counts = bucket_counts(a, messages_used, length)
        raw_counts = raw_bucket_counts(a, messages, length)
        label = f"{len(messages_used)} chat messages" + (" (reaction filter active)" if a.emotes else "")
    else:
        from ccf import audio
        src = a.video or a.url
        if not src:
            print("Error: --no-chat needs --video or --url", file=sys.stderr)
            sys.exit(1)
        print("🔊 Finding moments from loudness spikes ...", flush=True)
        aud = sources.download_audio(src, out_dir / "audio", a.cookies, a.proxy)
        counts = audio.loudness_buckets(aud, a.bucket)
        length = len(counts) * a.bucket
        label = "audio loudness (no chat)"

    all_peaks = find_peaks(a, counts, raw_counts, limit=a.top * max(1, a.tries))
    # the published moments.json keeps the classic top-N; the rest are backups for the quality gate
    by_score = sorted(all_peaks, key=lambda p: p["score"], reverse=True)
    peaks = sorted(by_score[:a.top], key=lambda p: p["t"])
    backups = by_score[a.top:]
    print(f"\n💬 {label}, ⏱ {hms(length)} of stream\n")
    print(ascii_graph(counts, length) + "\n")
    if not peaks:
        need = f" and {a.min_msgs} chat messages per {a.bucket}s" if a.min_msgs and raw_counts is not None else ""
        print(f"No moments reached the minimum multiplier threshold of {a.min_score}x{need}. Try lowering --min-score.")
        sys.exit(0 if a.dry_run or not (a.url or a.video) else 3)

    # 2. Name things
    streamer = a.credit or (chatmod.streamer_name(chat_file) if chat_file else "")
    if not streamer and a.url and not a.dry_run:
        info = sources.video_info(a.url, a.cookies, a.proxy)
        streamer = info.get("channel", "")

    print("Top Moments:")
    moments = []
    history = titles.load_history(a.title_history) if a.title_history else []
    for i, p in enumerate(peaks + backups):
        nominal_t = max(0.0, p["t"] - a.chat_delay)
        start = max(0, int(nominal_t - a.before) if float(nominal_t).is_integer() else round(nominal_t - a.before, 2))
        end = int(nominal_t + a.after) if float(nominal_t).is_integer() else round(nominal_t + a.after, 2)
        kind, phrase = chatmod.top_reaction(messages, p["t"] - 5, p["t"] + a.bucket + 10, a.game) if messages else (None, "")
        meta = titles.make(i + 1, kind, phrase, streamer, a.game, a.hook, history=history)
        clip_file = f"clip_{i + 1:02d}.mp4"
        spike_info = f"#{i + 1}  {hms(nominal_t)}" + (f" (chat spike {hms(p['t'])})" if a.chat_delay > 0 else "")
        raw_info = f" / {p['raw_msgs']} raw" if p.get("raw_msgs") is not None else ""
        print(f"{spike_info}  {p['msgs']} msgs in {a.bucket}s{raw_info}  ({p['score']}x normal)  "
              f"-> clip {hms(start)} to {hms(end)}" + (f"  [{kind}]" if kind else "") + ("  (backup)" if i >= len(peaks) else ""))
        moments.append({"rank": i + 1, "t": p["t"], "nominalEvent": nominal_t, "chatDelay": a.chat_delay,
                        "timestamp": hms(nominal_t), "chatTimestamp": hms(p["t"]), "msgs": p["msgs"],
                        "rawMsgs": p.get("raw_msgs"), "backup": i >= len(peaks),
                        "score": p["score"], "start": start, "end": end, "clipStart": hms(start),
                        "clipEnd": hms(end), "duration": end - start, "clipFile": clip_file,
                        "reaction": kind, "chatPhrase": phrase, **meta})

    with open(out_dir / "moments.json", "w", encoding="utf-8") as f:
        json.dump([m for m in moments if not m["backup"]], f, indent=2, ensure_ascii=False)
    print(f"\n✅ Saved moment data to {out_dir / 'moments.json'}.")

    if a.dry_run:
        print("Dry run requested; skipping video cutting.")
        sys.exit(0)
    if not a.url and not a.video:
        print("To cut video clips, provide --url <Twitch/YouTube link> or --video <local.mp4>")
        sys.exit(0)

    # 3. Cut + render (best moments first; backups replace moments that fail the quality gate)
    from ccf import audio, captions, quality, render
    posts, used_titles = [], []
    queue = sorted(moments, key=lambda m: (m["backup"], -float(m["score"])))
    for m in queue:
        if len(posts) >= a.top:
            break
        out_path = out_dir / f"clip_{len(posts) + 1:02d}.mp4"
        piece = out_dir / f"piece_{m['rank']}.mp4"
        trimmed = out_dir / f"trim_{m['rank']}.mp4"
        print(f"\n✂️  #{m['rank']} {m['clipStart']} -> {m['clipEnd']}  ({m['msgs']} msgs, {m['score']}x)", flush=True)
        try:
            if a.video:
                sources.download_section(a.video, m["start"], m["end"], piece)
            else:
                sources.download_section(a.url, m["start"], m["end"], piece, a.cookies, a.proxy, tdl)
        except RuntimeError as e:
            print(f"   ! download failed for this moment: {e}", flush=True)
            if sources.kind_of(a.url or "") == "youtube":
                sys.exit(3)   # YouTube blocks are per-video: no point trying other moments
            continue

        # Probe piece resolution and enforce minimum quality (>= 720p)
        src_w, src_h, piece_dur = render.probe(piece)
        if a.min_height and src_h < a.min_height:
            print(f"   ! skipping candidate: resolution {src_w}x{src_h} is below minimum {a.min_height}p", flush=True)
            piece.unlink(missing_ok=True)
            sys.exit(3)

        # Early check for webcam if required to avoid wasted whisper and render time
        cam, cam_info = None, None
        if a.require_cam:
            from ccf import facecam
            cam_info = facecam.analyze(piece) if a.cam not in facecam.PRESETS else None
            layout, cam = render.choose_layout(a.layout, a.cam, piece, src_w, src_h, require_cam=True,
                                               cam_info=cam_info)
            if cam_info:
                print(f"   📷 cam: {cam_info['kind']} face in {cam_info['hits']}/{cam_info['samples']} samples, "
                      f"crop {tuple(int(v) for v in cam_info['box'])}"
                      f"{' (snapped to overlay border)' if cam_info.get('snapped') else ''}", flush=True)
            if not layout:
                print(f"   ! skipping candidate: no webcam detected (require-cam active)", flush=True)
                piece.unlink(missing_ok=True)
                sys.exit(3)

        # 1. Audio peak = the payoff. Cut so it lands ~payoff_lead seconds in (setup trimmed away)
        climax_offset = max(0.0, float(m["nominalEvent"]) - float(m["start"]))
        peak_sec, peak_db = audio.find_audio_peak(piece, target_offset=climax_offset, window_radius=4.5)
        t0 = max(0.0, peak_sec - a.payoff_lead)
        if piece_dur - t0 < a.target_min:
            t0 = max(0.0, piece_dur - a.target_min)
        length = max(1.0, min(a.target_max, piece_dur - t0))
        print(f"   🔊 Payoff (audio peak) at {peak_sec:.1f}s ({peak_db:.1f} dB RMS) -> cut {t0:.1f}s..{t0 + length:.1f}s", flush=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.2f}", "-i", str(piece), "-t", f"{length:.2f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
                        str(trimmed)], check=False)
        piece.unlink(missing_ok=True)
        if not trimmed.exists() or trimmed.stat().st_size < 10000:
            print("   ! trim failed, trying next moment", flush=True)
            continue

        # Re-check the cam on the exact clip window (the streamer may have moved the overlay); keep the
        # wider-window result when the clip itself is inconclusive. The crop is locked for the render.
        if cam_info is not None:
            clip_info = facecam.analyze(trimmed)
            if clip_info and clip_info.get("kind") == cam_info.get("kind"):
                cam_info = clip_info
                cam = tuple(cam_info["box"])

        # 2. Quality gate: dark / frozen gameplay (menus, loading, BRB) is not a highlight
        if not a.no_quality_gate:
            dull, why = quality.is_dull(trimmed, cam, src_w, src_h)
            print(f"   🖼  Gameplay check: {why}", flush=True)
            if dull:
                print("   ! skipping moment: gameplay is dark or static", flush=True)
                trimmed.unlink(missing_ok=True)
                continue

        # 3. Whisper transcription
        words = captions.transcribe(trimmed, a.whisper_model, a.lang) if a.captions != "off" else []

        # 4. Sentence & speech boundary snapping (never below target_min)
        snapped_dur = captions.snap_speech_boundaries(words, length, min_duration=a.target_min, tail_window=10.0) \
            if words else length
        if snapped_dur < length:
            print(f"   ✂️ Snapped to natural speech boundary: {snapped_dur:.1f}s (trimmed {length - snapped_dur:.1f}s)", flush=True)

        # 5. Highlight accuracy & coherence score
        coherence = chatmod.score_moment(words, peak_db, snapped_dur)
        print(f"   🎯 Highlight accuracy & coherence score: {coherence}/100", flush=True)

        # 6. Title + comment sticker from the actual moment (chat + transcript)
        win_s, win_e = m["t"] - 8, m["t"] + a.bucket + 8
        chat_text = " ".join(x["text"] for x in messages if win_s <= x["t"] <= win_e)
        meta = titles.make(m["rank"], m["reaction"], m["chatPhrase"], streamer, a.game, a.hook, words=words,
                           chat_text=chat_text, history=history, used=used_titles)
        caption_style = {"on": "word", "word": "word", "off": "off", "ig-comment": "ig-comment"}.get(a.captions, "both")
        ig_comment = None
        if caption_style in ("both", "ig-comment"):
            ig_comment = titles.make_ig_comment(messages, win_s, win_e, peak_t=m["t"], words=words, kind=m["reaction"])
            print(f"   💬 Comment sticker ({ig_comment['source']}): \"{ig_comment['text']}\" "
                  f"(@{ig_comment['username']} · {ig_comment['time']} · {ig_comment['likes']})", flush=True)
        print(f"   🏷  Title: {meta['title']}  [{meta['template']}]", flush=True)

        r = render.render(trimmed, out_path, a.layout, a.cam, words, meta["hook"], meta["credit"],
                          require_cam=a.require_cam, duration=snapped_dur,
                          ig_comment=ig_comment, caption_style=caption_style, cam_info=cam_info)
        trimmed.unlink(missing_ok=True)
        if not r or not r.get("layout"):
            print(f"   ! skipping candidate: render failed or no webcam detected", flush=True)
            sys.exit(3)
        print(f"   ✅ {out_path}  ({r['layout']} layout, {len(words)} caption words)")
        used_titles.append((meta["title"], meta["template"]))
        posts.append({"file": str(out_path), "title": meta["title"], "template": meta["template"],
                      "description": meta["description"], "tags": meta["tags"], "streamer": streamer,
                      "layout": r["layout"], "coherence_score": coherence,
                      "audio_peak_db": round(peak_db, 1), "snapped_duration": snapped_dur,
                      "moment": {"chat_time": m["chatTimestamp"], "msgs": m["msgs"], "raw_msgs": m.get("rawMsgs"),
                                 "score": m["score"], "reaction": m["reaction"],
                                 "clip_start": round(float(m["start"]) + t0, 2)},
                      "ig_comment": ig_comment,
                      "stages": [s[2] for s in captions.story_stages(snapped_dur)]})

    with open(out_dir / "posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    if posts and a.title_history:
        titles.record_titles([{"title": p["title"], "template": p["template"], "streamer": streamer} for p in posts],
                             a.title_history)
    print(f"\n🎉 Finished! {len(posts)} Shorts in {out_dir}/ (titles + captions in posts.json)")
    if not posts:
        print("   No moment passed the quality gate for this source.")
        sys.exit(3)


if __name__ == "__main__":
    main()
