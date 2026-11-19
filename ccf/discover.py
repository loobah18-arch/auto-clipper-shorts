"""Find fresh GTA 6 streams and videos to clip, automatically.

Sources (no API key needed for YouTube):
  * YouTube search, uploaded today, sorted by views (finished live streams + long gameplay videos)
  * Channels you list in streamers.txt (their latest ended streams / uploads)
  * Twitch top GTA VI past broadcasts of the day, when TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are set
    (free app at https://dev.twitch.tv/console/apps)

Every candidate must look like real GTA 6 gameplay: talk/news/leak/countdown videos are skipped,
GTA 5 / GTA Online videos are skipped, Shorts and anything under --min-minutes are skipped.
"""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

GTA6_RE = re.compile(r"\b(gta\s?(6|vi)|gta6|grand\s+theft\s+auto\s+(6|vi))\b", re.I)
OTHER_GTA_RE = re.compile(r"\b(gta\s?(5|v|iv|4|3|iii|online|rp|sa)|gta5|gtav|grand\s+theft\s+auto\s+(5|v|iv)|"
                          r"san\s+andreas|vice\s+city\s+stories|liberty\s+city|red\s+dead|rdr\s?2|roleplay|\w+rp)\b", re.I)
OTHER_GAME_RE = re.compile(r"\bplaying\s+(?!gta|grand|the\s+new\s+gta)\w", re.I)
NOT_GAMEPLAY_RE = re.compile(
    r"\b(news|leak(s|ed)?|trailer|countdown|count\s?down|everything\s+we\s+know|explained|theory|theories|"
    r"predictions?|review(s|ed)?|price|pre-?order|reaction\s+to\s+(the\s+)?trailer|why\s+gta|"
    r"before\s+(gta|the\s+game|you\s+buy)|stuff\s+before|at\s+\d+\s*(am|pm)|release\s+(date|time)|unlock\s+time|podcast|tier\s?list|ranking|"
    r"top\s+\d+|\d+\s+things|watch\s+this\s+first|how\s+to\s+(start|grow)|downloading|pre-?load|"
    r"info(s|rmation)?|setup|tour|should\s+\w+\s+play|talking|awards?|screenshots?|videos?\s+per\s+day|"
    r"comparison|compared|soon|waiting|hype\s+train|unboxing|collector'?s\s+edition|pc\s+port|specs|requirements|map\s+size)\b", re.I)
GAMEPLAY_HINT_RE = re.compile(
    r"\b(gameplay|playthrough|walkthrough|let'?s\s+play|part\s*\d+|ep(isode)?\.?\s*\d+|#\d+|first\s+(time|hour|day|look)|"
    r"day\s*1|live|stream|chaos|funny|moments|rampage|heist|chase|wanted|missions?|full\s+game|blind|early\s+access|drops)\b", re.I)

# sp filters: CAMSBAgCEAE = sort by views + uploaded today + type video
YT_SEARCHES = [
    "https://www.youtube.com/results?search_query={q}&sp=CAMSBAgCEAE%253D",
]
YT_QUERIES = ["gta 6 gameplay", "gta 6 live stream", "gta 6 funny moments", "gta 6 first time playing"]


def _ytdlp_flat(url, n, cookies=None, proxy=None):
    cmd = ["yt-dlp", "--flat-playlist", "-J", "--playlist-end", str(n), "--no-warnings"]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    if proxy:
        cmd += ["--proxy", proxy]
    try:
        out = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            print(f"   ! search failed: {url[:80]} ({out.stderr.strip()[-160:]})", flush=True)
            return []
        return json.loads(out.stdout).get("entries") or []
    except Exception as e:
        print(f"   ! search failed: {url[:80]} ({e})", flush=True)
        return []


def is_game_gameplay(title, game_name):
    """Any other game: title must name the game and not be news/talk."""
    t = title or ""
    words = [w for w in re.split(r"\W+", game_name.lower()) if w]
    if not words or not all(re.search(r"\b" + re.escape(w) + r"\b", t.lower()) for w in words):
        return False
    return not NOT_GAMEPLAY_RE.search(t)


def is_gta6_gameplay(title, game="gta6"):
    if game != "gta6":
        return is_game_gameplay(title, game)
    t = title or ""
    if not GTA6_RE.search(t):
        return False
    if OTHER_GTA_RE.search(t):
        return False
    if NOT_GAMEPLAY_RE.search(t) or OTHER_GAME_RE.search(t):
        return False
    return bool(GAMEPLAY_HINT_RE.search(t))


def _yt_candidate(e, origin):
    vid = e.get("id")
    if not vid or len(vid) != 11:
        return None
    return {
        "id": f"yt:{vid}",
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "",
        "views": int(e.get("view_count") or e.get("concurrent_view_count") or 0),
        "views_known": bool(e.get("view_count") or e.get("concurrent_view_count")),
        "duration": float(e.get("duration") or 0),
        "live_status": e.get("live_status") or "",
        "platform": "youtube",
        "origin": origin,
        "gameplay_hint": bool(GAMEPLAY_HINT_RE.search(e.get("title") or "")),
    }


def queries_for(game):
    if game == "gta6":
        return YT_QUERIES
    return [f"{game} gameplay", f"{game} highlights", f"{game} funny moments", f"{game} best plays"]


def youtube_search(per_query=20, cookies=None, proxy=None, game="gta6"):
    found = []
    for q in queries_for(game):
        for tmpl in YT_SEARCHES:
            url = tmpl.format(q=urllib.parse.quote_plus(q))
            entries = _ytdlp_flat(url, per_query, cookies, proxy)
            print(f"   YouTube '{q}': {len(entries)} results", flush=True)
            for e in entries:
                c = _yt_candidate(e, f"search: {q}")
                if c:
                    found.append(c)
    return found


def youtube_channels(handles, per_channel=5, cookies=None, proxy=None):
    found = []
    for h in handles:
        h = h if h.startswith("@") else "@" + h
        for tab in ("streams", "videos"):
            url = f"https://www.youtube.com/{h}/{tab}"
            for e in _ytdlp_flat(url, per_channel, cookies, proxy):
                c = _yt_candidate(e, f"channel {h}")
                if c:
                    c["channel"] = c["channel"] or h
                    c["followed"] = True
                    found.append(c)
    return found


def _twitch_get(path, token, client_id):
    req = urllib.request.Request("https://api.twitch.tv/helix/" + path,
                                 headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def twitch_top(game_name="Grand Theft Auto VI", n=20, logins=()):
    if game_name.lower() == "gta6":
        game_name = "Grand Theft Auto VI"
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        print("   (Twitch skipped: add TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET to enable)", flush=True)
        return []
    try:
        body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                       "grant_type": "client_credentials"}).encode()
        with urllib.request.urlopen("https://id.twitch.tv/oauth2/token", data=body, timeout=30) as r:
            token = json.load(r)["access_token"]
        games = _twitch_get("games?name=" + urllib.parse.quote(game_name), token, cid).get("data") or []
        if not games:
            print(f"   ! Twitch has no category named {game_name!r} yet", flush=True)
            return []
        gid = games[0]["id"]
        vids = _twitch_get(f"videos?game_id={gid}&period=day&sort=views&type=archive&first={n}", token, cid)["data"]
        for login in logins:
            u = _twitch_get("users?login=" + urllib.parse.quote(login), token, cid).get("data") or []
            if u:
                vids += _twitch_get(f"videos?user_id={u[0]['id']}&type=archive&first=3", token, cid)["data"]
    except Exception as e:
        print(f"   ! Twitch lookup failed: {e}", flush=True)
        return []
    out = []
    for v in vids:
        m = re.findall(r"(\d+)([hms])", v.get("duration", ""))
        dur = sum(int(x) * {"h": 3600, "m": 60, "s": 1}[u] for x, u in m)
        out.append({"id": f"tw:{v['id']}", "url": v["url"], "title": v.get("title", ""),
                    "channel": v.get("user_name", ""), "views": int(v.get("view_count") or 0),
                    "duration": float(dur), "live_status": "was_live", "platform": "twitch",
                    "origin": "twitch", "gameplay_hint": True, "category_gta6": True,
                    "followed": v.get("user_login", "").lower() in {l.lower() for l in logins}})
    return out


def load_followed(path="streamers.txt"):
    yt, tw = [], []
    if not os.path.exists(path):
        return yt, tw
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("twitch:"):
            tw.append(line.split(":", 1)[1].strip())
        else:
            yt.append(line.removeprefix("youtube:").strip())
    return yt, tw


def discover(game="gta6", count=2, min_minutes=10, min_views=1000, seen=(), cookies=None, proxy=None,
             streamers_file="streamers.txt"):
    """Return the best `count` new candidates, best first, plus the full ranked list."""
    yt_handles, tw_logins = load_followed(streamers_file)
    cands = youtube_search(cookies=cookies, proxy=proxy, game=game)
    if yt_handles:
        cands += youtube_channels(yt_handles, cookies=cookies, proxy=proxy)
    cands += twitch_top(game, n=20, logins=tw_logins)

    best, why = {}, {}

    def skip(reason):
        why[reason] = why.get(reason, 0) + 1

    for c in cands:
        if c["id"] in seen or c["id"] in best:
            skip("already clipped / duplicate"); continue
        if c["live_status"] in ("is_live", "is_upcoming", "post_live"):
            skip("still live"); continue  # chat replay + seeking only work once the stream has ended
        if c["duration"] and c["duration"] < min_minutes * 60:
            skip("too short"); continue  # Shorts and short edits: nothing left to clip
        if not is_gta6_gameplay(c["title"], game):
            skip("not gameplay of this game"); continue
        # some regions/runners get no view count from search: the search is already sorted by views
        if c.get("views_known", True) and c["views"] < min_views and not c.get("followed"):
            skip("too few views"); continue
        # rank: followed creators first (+2).
        # On Twitch: finished streams (+1) have chat replays.
        # On YouTube: regular uploaded videos (+1) download cleanly without live manifest bot gates.
        is_twitch_vod = (c["platform"] == "twitch" and c["live_status"] == "was_live")
        is_yt_video = (c["platform"] == "youtube" and c["live_status"] != "was_live")
        c["score"] = (2.0 if c.get("followed") else 0.0) \
            + (1.0 if (is_twitch_vod or is_yt_video) else 0.0) \
            + (0.5 if c["gameplay_hint"] else 0.0) + min(c["views"], 5_000_000) / 5_000_000
        best[c["id"]] = c
    if why:
        print("   skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])), flush=True)
    ranked = sorted(best.values(), key=lambda c: c["score"], reverse=True)
    return ranked[:count], ranked
