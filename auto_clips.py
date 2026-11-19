#!/usr/bin/env python3
"""Hands-free mode: find today's top GTA 6 streams/videos, clip the ones not done yet.

    python3 auto_clips.py                      # 2 new sources, 3 Shorts each, into clips/
    python3 auto_clips.py --count 3 --top 2
    python3 auto_clips.py --list               # just show what it would pick

Remembers what it already clipped in state/processed.json, so each run picks new videos.
Extra flags after `--` go straight to chat_clips.py (e.g. -- --emotes --layout stack).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ccf import discover, sources

STATE = Path("state/processed.json")


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # keep the newest 500 so the file stays small
    items = sorted(state.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:500]
    STATE.write_text(json.dumps(dict(items), indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    passthru = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthru = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--game", default="gta6", help='"gta6" (default) or any game name, e.g. "valorant", "minecraft"')
    p.add_argument("--count", type=int, default=1, help="how many new streams/videos to clip (default 1)")
    p.add_argument("--top", type=int, default=1, help="Shorts per stream/video (default 1)")
    p.add_argument("--min-views", type=int, default=1000)
    p.add_argument("--min-minutes", type=int, default=10)
    p.add_argument("--streamers", default="streamers.txt")
    p.add_argument("--out", default="clips")
    p.add_argument("--cookies")
    p.add_argument("--proxy")
    p.add_argument("--tdl", default="TwitchDownloaderCLI")
    p.add_argument("--list", action="store_true", help="only print what would be clipped")
    a = p.parse_args(argv)

    import datetime
    if datetime.date.today() <= datetime.date(2026, 12, 1) and a.game != "gta6":
        print(f"🎯 [GTA 6 Priority] Override active until 1 December 2026: focusing on GTA 6 (requested {a.game}).", flush=True)
        a.game = "gta6"

    cookies = sources.find_cookies(a.cookies)
    proxy = a.proxy or os.environ.get("YOUTUBE_PROXY")
    state = load_state()

    print(f"🔎 Looking for today's top {'GTA 6' if a.game == 'gta6' else a.game} streams and videos...", flush=True)
    picks, ranked = discover.discover(a.game, a.count, a.min_minutes, a.min_views, set(state),
                                      cookies, proxy, a.streamers)
    print(f"\n{len(ranked)} new candidates passed the filters. Top 10:")
    for i, c in enumerate(ranked[:10], 1):
        mins = int(c["duration"] // 60) if c["duration"] else "?"
        tag = "ended stream" if c["live_status"] == "was_live" else "video"
        print(f"  {i:>2}. {(format(c['views'], ',') if c.get('views_known', True) else '?'):>9} views  {mins:>4} min  {tag:<12} {c['channel'][:22]:<22} | {c['title'][:60]}")
    if a.list or not picks:
        if not picks:
            print("\nNothing new worth clipping right now.")
        return 0

    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)
    all_posts, done = [], 0
    candidates = ranked if ranked else picks
    for n, c in enumerate(candidates, 1):
        if done >= a.count:
            break
        sub = out_root / f"{done + 1:02d}_{c['id'].replace(':', '_')}"
        print(f"\n🎬 [Short {done + 1}/{a.count} - Candidate {n}/{len(candidates)}] {c['channel']} | {c['title']}\n   {c['url']}", flush=True)
        cmd = [sys.executable, "chat_clips.py", "--url", c["url"], "--game", "gta6" if a.game == "gta6" else "none", "--top", str(a.top),
               "--out", str(sub), "--credit", c["channel"] or "", "--layout", "stack", "--require-cam", "--chat-delay", "4.0",
               "--before", "34", "--after", "18"]
        if cookies:
            cmd += ["--cookies", str(cookies)]
        if proxy:
            cmd += ["--proxy", proxy]
        if c["platform"] == "twitch":
            cmd += ["--tdl", a.tdl]
        cmd += passthru
        rc = subprocess.run(cmd).returncode
        state[c["id"]] = {"title": c["title"], "channel": c["channel"], "url": c["url"],
                          "at": int(time.time()), "ok": rc == 0}
        save_state(state)
        posts_file = sub / "posts.json"
        if rc == 0 and posts_file.exists():
            posts = json.loads(posts_file.read_text(encoding="utf-8"))
            for post in posts:
                post["source_url"] = c["url"]
                post["source_title"] = c["title"]
            all_posts += posts
            done += 1
        else:
            print(f"   ! skipped (exit {rc}), trying next candidate...", flush=True)

    (out_root / "posts.json").write_text(json.dumps(all_posts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n🎉 {len(all_posts)} Shorts from {done} source(s). Titles in {out_root}/posts.json")
    return 0 if all_posts else 1


if __name__ == "__main__":
    sys.exit(main())
