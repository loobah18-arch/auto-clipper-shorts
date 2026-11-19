"""Where the stream comes from: Twitch VODs, YouTube videos / past live streams, or a local file.

YouTube needs a cookies file to get past "Sign in to confirm you're not a bot" (always on GitHub's
servers). The file is found the ClipKaro way: --cookies, then $YOUTUBE_COOKIES_FILE, then
youtube_cookies.txt / cookies.txt / any *cookies*.txt in the working folder (newest first).
An optional proxy (--proxy or $YOUTUBE_PROXY) helps when the runner's IP is blocked anyway.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

YT_RE = re.compile(r"(youtube\.com|youtu\.be)/", re.I)
TWITCH_RE = re.compile(r"(twitch\.tv/videos/|^\d{6,}$)", re.I)


def kind_of(url):
    if not url:
        return None
    if YT_RE.search(url):
        return "youtube"
    if TWITCH_RE.search(url.strip()):
        return "twitch"
    if Path(url).exists():
        return "file"
    return "ytdlp"  # anything else yt-dlp understands (Kick, X, ...)


def find_cookies(explicit=None):
    for p in (explicit, os.environ.get("YOUTUBE_COOKIES_FILE")):
        if p and Path(p).is_file():
            return str(p)
    named = [p for p in ("youtube_cookies.txt", "cookies.txt") if Path(p).is_file()]
    if named:
        return named[0]
    found = sorted((p for p in glob.glob("*cookies*.txt") if Path(p).is_file()),
                   key=lambda p: -Path(p).stat().st_mtime)
    return found[0] if found else None


def _ytdlp_base(cookies=None, proxy=None, client=None, pot=True, no_cookies=False):
    exe = shutil.which("yt-dlp")
    cmd = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
    cmd += ["--no-warnings", "--no-playlist", "--retries", "3", "--fragment-retries", "5", "--socket-timeout", "45"]
    if shutil.which("node"):
        cmd += ["--js-runtimes", "node", "--remote-components", "ejs:github"]

    if not no_cookies:
        cookies_file = find_cookies(cookies)
        if cookies_file:
            cmd += ["--cookies", cookies_file]

    proxy = proxy or os.environ.get("YOUTUBE_PROXY")
    if proxy:
        cmd += ["--proxy", proxy]

    po_token = os.environ.get("YT_PO_TOKEN")
    visitor_data = os.environ.get("YT_VISITOR_DATA")
    if pot and po_token and visitor_data:
        cmd += [
            "--extractor-args", f"youtube:po_token=web.gvs+{po_token}",
            "--extractor-args", f"youtube:visitor_data={visitor_data}",
        ]

    # Check for bgutil POT provider on localhost:4416
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:4416/ping", timeout=0.5) as r:
            if r.status == 200:
                cmd += ["--extractor-args", "youtubepot-provider:url=http://127.0.0.1:4416"]
    except Exception:
        pass

    if client:
        cmd += ["--extractor-args", f"youtube:player_client={client}"]

    return cmd


def _run(cmd, what, timeout=300):
    print(f"   $ {' '.join(c if ' ' not in c else repr(c) for c in cmd[:3])} ... ({what})", flush=True)
    try:
        res = subprocess.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} timed out after {timeout}s.")
    if res.returncode != 0:
        raise RuntimeError(f"{what} failed (exit {res.returncode}). For YouTube, refresh your cookies "
                           f"(export them again while logged in) or set a proxy.")


def fetch_chat(url, out_dir, cookies=None, proxy=None, tdl="TwitchDownloaderCLI"):
    """Download the chat replay. Returns the chat file path, or None if the video has no chat."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    k = kind_of(url)
    if k == "twitch":
        out = out_dir / "chat.json"
        _run([tdl, "chatdownload", "--id", url, "-o", str(out)], "Twitch chat download")
        return out
    if k in ("youtube", "ytdlp"):
        tmpl = str(out_dir / "chat")
        for client, use_cookies, use_pot in [
            (None, True, True),
            ("mweb", True, True),
            ("ios", False, False),
        ]:
            cmd = _ytdlp_base(cookies=cookies if use_cookies else None, proxy=proxy, client=client, pot=use_pot, no_cookies=not use_cookies) + [
                "-4", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                "-o", tmpl, url
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if res.returncode == 0:
                    hits = sorted(out_dir.glob("chat*.live_chat.json"))
                    if hits:
                        return hits[0]
            except Exception:
                pass
        hits = sorted(out_dir.glob("chat*.live_chat.json"))
        return hits[0] if hits else None
    return None


def video_info(url, cookies=None, proxy=None):
    """Title, channel and duration via yt-dlp (works for Twitch and YouTube)."""
    import json
    for client, use_cookies, use_pot in [
        (None, True, True),
        ("mweb", True, True),
        ("ios", False, False),
        ("visionos", False, False),
    ]:
        cmd = _ytdlp_base(cookies=cookies if use_cookies else None, proxy=proxy, client=client, pot=use_pot, no_cookies=not use_cookies) + [
            "-4", "-J", "--skip-download", url
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                d = json.loads(out.stdout)
                return {
                    "title": d.get("title", ""),
                    "channel": d.get("channel") or d.get("uploader") or "",
                    "duration": float(d.get("duration") or 0),
                    "was_live": bool(d.get("was_live")),
                }
        except Exception:
            continue
    return {"title": "", "channel": "", "duration": 0.0, "was_live": False}


def download_section(url, start, end, out_path, cookies=None, proxy=None, tdl="TwitchDownloaderCLI"):
    """Download only [start, end] seconds of the video, 1080p max."""
    from .timefmt import hms
    out_path = Path(out_path)
    k = kind_of(url)
    if k == "twitch" and (shutil.which(tdl) or Path(tdl).exists()):
        _run([tdl, "videodownload", "--id", url, "-b", hms(start), "-e", hms(end),
              "-o", str(out_path)], "Twitch segment download")
        return out_path
    if k == "file":
        _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-to", str(end), "-i", url,
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
              str(out_path)], "local segment copy")
        return out_path

    if k != "youtube":
        cmd = _ytdlp_base(cookies, proxy) + [
            "-f", "bv*[height>=720][height<=1080][fps<=60]+ba/b[height>=720][height<=1080]/bv*[height<=1080]+ba/b",
            "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
            "--merge-output-format", "mp4", "-o", str(out_path), url]
        _run(cmd, "segment download")
        return out_path

    # YouTube: Multi-tier cascading fallback (strictly >=720p, <=1080p)
    fmt_hd = "bv*[height>=720][height<=1080][fps<=60]+ba/b[height>=720][height<=1080]"
    tiers = [
        ("web,mweb", fmt_hd, "Tier 1: 1080p/720p web,mweb + bgutil/cookies"),
        ("android,mweb,web", f"{fmt_hd}/22", "Tier 2: 1080p/720p android,mweb,web native bypass"),
        ("ios", fmt_hd, "Tier 3: 1080p/720p ios HLS (no cookies)"),
        ("android_vr", fmt_hd, "Tier 4: 1080p/720p android_vr (no cookies)"),
        ("android", "22", "Tier 5: 720p android progressive (format 22)"),
    ]

    last_err = None
    for client, fmt, label in tiers:
        try:
            print(f"   🔄 Attempting segment download via {label}...", flush=True)
            use_cookies = "no cookies" not in label
            cmd = _ytdlp_base(
                cookies=cookies if use_cookies else None,
                proxy=proxy,
                client=client,
                pot=use_cookies,
                no_cookies=not use_cookies
            ) + [
                "-4",
                "--downloader", "native",
                "-f", fmt,
                "--download-sections", f"*{start:.2f}-{end:.2f}",
                "--force-keyframes-at-cuts",
                "--merge-output-format", "mp4",
                "-o", str(out_path),
                url
            ]
            _run(cmd, f"segment download ({client})")
            if out_path.exists() and out_path.stat().st_size > 10000:
                print(f"   ✅ Segment downloaded successfully via {client}", flush=True)
                return out_path
        except Exception as e:
            print(f"   ⚠️ {label} failed: {e}", flush=True)
            last_err = e

    raise RuntimeError(f"segment download failed all tiers. Last error: {last_err}")


def download_audio(url, out_path, cookies=None, proxy=None):
    """Whole-stream audio only (small), used to find moments when there is no chat."""
    out_path = Path(out_path)
    if kind_of(url) == "file":
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-vn", "-ac", "1", "-ar", "16000",
              str(out_path)], "audio extract")
        return out_path

    target_m4a = out_path.with_suffix(".m4a")
    tmpl = str(out_path.with_suffix(".%(ext)s"))

    # For Twitch or non-YouTube
    if kind_of(url) != "youtube":
        cmd = _ytdlp_base(cookies, proxy) + ["-f", "ba/b/bestaudio/18/b", "-x", "--audio-format", "m4a",
                                             "-o", tmpl, url]
        _run(cmd, "audio download")
        return target_m4a

    # YouTube: Multi-tier cascading fallback (copied from auto-clipper-shorts Tier 3c)
    tiers = [
        ("android,mweb,web", "ba/b/bestaudio/18/b", "Tier 3c: android,mweb,web (fmt ba/b/18)"),
        ("web,mweb", "ba/b/bestaudio/18/b", "Tier 1: web,mweb + bgutil/cookies"),
        ("ios", "best/18/b", "Tier 2: ios HLS (no cookies)"),
        ("android_vr", "best/18/b", "Tier 2: android_vr (no cookies)"),
        ("android", "18/b", "Tier 2: android (no cookies)"),
    ]

    last_err = None
    for client, fmt, label in tiers:
        try:
            print(f"   🔄 Attempting audio download via {label}...", flush=True)
            use_cookies = "no cookies" not in label
            cmd = _ytdlp_base(
                cookies=cookies if use_cookies else None,
                proxy=proxy,
                client=client,
                pot=use_cookies,
                no_cookies=not use_cookies
            ) + [
                "-4",
                "--downloader", "native",
                "-f", fmt,
                "-x", "--audio-format", "m4a",
                "-o", tmpl,
                url
            ]
            _run(cmd, f"audio download ({client})")
            if target_m4a.exists() and target_m4a.stat().st_size > 1000:
                print(f"   ✅ Audio downloaded successfully via {client}", flush=True)
                return target_m4a
            for cand in out_path.parent.glob(f"{out_path.stem}.*"):
                if cand.is_file() and cand.stat().st_size > 1000:
                    return cand
        except Exception as e:
            print(f"   ⚠️ {label} failed: {e}", flush=True)
            last_err = e

    raise RuntimeError(f"audio download failed all tiers. Last error: {last_err}")
