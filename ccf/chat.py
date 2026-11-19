"""Load chat from any source and weigh each message by how much of a *reaction* it is.

Supported chat files:
  * TwitchDownloaderCLI JSON  ({"comments": [{"content_offset_seconds", "message": {"body"}}]})
  * yt-dlp YouTube live-chat replay (*.live_chat.json, one JSON object per line)
  * a plain list of numbers (seconds) or of {"t": seconds, "text": "..."}
"""
import json
import re
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- reaction lexicon
# Word reactions are matched as whole tokens; emoji are matched anywhere (\b never fires next to emoji).
LAUGH = {"lmao", "lmfao", "kekw", "omegalul", "lul", "lol", "rofl", "icant", "i cant", "i can't", "dead", "deadge",
         "hahaha", "haha", "xd", "pepelaugh", "kek", "lulw"}
HYPE = {"pog", "poggers", "pogchamp", "letsgo", "lets go", "clutch", "insane", "goated", "w", "gg",
        "yooo", "sheesh", "holy", "huge", "clean", "cracked", "massive"}
SHOCK = {"wtf", "no way", "omg", "what the", "bro", "nah", "monkas", "cooked", "fail", "l", "rip",
         "f", "pepega", "aware", "doxxed", "bruh"}
LAUGH_EMOJI = {"💀", "😭", "🤣", "😂", "☠️", "☠"}
HYPE_EMOJI = {"🔥", "🐐", "💯", "🚀", "😤"}
SHOCK_EMOJI = {"😳", "😱", "🤯", "😮", "👀", "🫢", "🫨"}

# GTA 6 specific reactions: wanted levels, deaths, police chases, Vice City moments.
GTA6 = {"wasted", "busted", "5 star", "five star", "5 stars", "wanted", "cops", "police", "chase",
        "heist", "jason", "lucia", "vice city", "leonida", "ragdoll", "physics", "gta", "gta6",
        "gta 6", "rockstar", "npc", "crash", "explosion", "boom", "bonnie and clyde", "getaway",
        "car chase", "swat", "tank", "helicopter", "jetski", "alligator", "gator", "florida man"}

GAME_LEXICONS = {"gta6": GTA6, "gta": GTA6, "none": set()}

# ---------------------------------------------------------------- donation / bot filters
# Ignore off-screen sub trains, bit cheers, donation alerts, and automated bot spam.
DONATION_NOISE_RE = re.compile(
    r"\b(gifted|subscriber|subscribed|subs?|resub(bed)?|bits|donation|donated|cheer\d*|tts|prime\s+sub|tier\s*[123]|raiding\s+with|welcome\s+raiders)\b",
    re.I,
)
BOT_AUTHORS = {
    "streamelements", "nightbot", "moobot", "streamlabs", "fossabot", "wizebot", "soundalerts", "botrix"
}
DEFAULT_CHAT_DELAY = 4.0

_word_cache = {}


def _token_re(words):
    key = tuple(sorted(words))
    if key not in _word_cache:
        alts = sorted((re.escape(w).replace(r"\ ", r"\s*") for w in words), key=len, reverse=True)
        # allow stretched letters: "lmaooo", "yoooo", "wtfff"
        alts = [a + "+" if a[-1:].isalpha() else a for a in alts]
        _word_cache[key] = re.compile(r"(?<![a-z0-9])(" + "|".join(alts) + r")(?![a-z0-9])", re.I)
    return _word_cache[key]


def classify(body, game="none"):
    """Return (weight, kind) for one chat message. weight 0 means 'not a reaction'."""
    if not body:
        return 0.0, None
    stripped_body = body.strip()
    if stripped_body.startswith("!"):
        return 0.0, None
    if DONATION_NOISE_RE.search(stripped_body):
        return 0.0, None
    text = stripped_body.lower()
    scores = Counter()
    for kind, words, emoji in (("laugh", LAUGH, LAUGH_EMOJI), ("hype", HYPE, HYPE_EMOJI), ("shock", SHOCK, SHOCK_EMOJI)):
        n = len(_token_re(words).findall(text)) + sum(text.count(e) for e in emoji)
        if n:
            scores[kind] += min(n, 4)
    gw = GAME_LEXICONS.get(game, set())
    if gw:
        g = len(_token_re(gw).findall(text))
        if g:
            scores["game"] += min(g, 3)
    if not scores:
        return 0.0, None
    kind, n = scores.most_common(1)[0]
    # single letters (W / L / F) are cheap spam: count them half
    stripped = re.sub(r"[^a-z]", "", text)
    weight = 0.5 if stripped in {"w", "l", "f"} else 1.0
    weight += 0.25 * (sum(scores.values()) - 1)          # mixed reactions ("LMAO 💀💀 WTF") weigh more
    if "game" in scores:
        weight += 0.5
    if body.isupper() and len(body) > 3:                  # ALL CAPS
        weight += 0.25
    return min(weight, 2.5), kind


# ---------------------------------------------------------------- loaders
def _yt_runs(runs):
    out = []
    for r in runs or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            if e.get("isCustomEmoji"):
                sc = (e.get("shortcuts") or [""])[0]
                out.append(" " + sc.strip(":") + " ")
            else:
                out.append(e.get("emojiId", ""))
    return "".join(out)


def _load_youtube_live_chat(path):
    msgs, length = [], 0.0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            rep = obj.get("replayChatItemAction") or {}
            try:
                t = int(rep.get("videoOffsetTimeMsec", "-1")) / 1000.0
            except (TypeError, ValueError):
                continue
            if t < 0:
                continue
            for act in rep.get("actions", []):
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not r:
                    continue
                body = _yt_runs((r.get("message") or {}).get("runs"))
                author = (r.get("authorName") or {}).get("simpleText", "")
                if author and author.lower().strip() in BOT_AUTHORS:
                    continue
                msgs.append({"t": t, "text": body, "author": author})
                length = max(length, t)
    return msgs, length


def load_chat(path):
    """Return (messages, stream_length_seconds). messages = [{'t', 'text', 'author'}] sorted by t."""
    path = Path(path)
    if path.name.endswith(".live_chat.json") or path.suffix == ".jsonl":
        msgs, length = _load_youtube_live_chat(path)
        msgs.sort(key=lambda m: m["t"])
        return msgs, length
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError:
            # maybe JSON-lines from YouTube with another name
            msgs, length = _load_youtube_live_chat(path)
            msgs.sort(key=lambda m: m["t"])
            return msgs, length
    comments = data if isinstance(data, list) else data.get("comments", [])
    msgs = []
    for c in comments:
        if isinstance(c, (int, float)):
            msgs.append({"t": float(c), "text": "", "author": ""})
        elif isinstance(c, dict):
            t = c.get("content_offset_seconds", c.get("t"))
            if isinstance(t, (int, float)) and t >= 0:
                body = (c.get("message") or {}).get("body", "") if "message" in c else c.get("text", "")
                author = (c.get("commenter") or {}).get("display_name", "") if "commenter" in c else c.get("author", "")
                if author and author.lower().strip() in BOT_AUTHORS:
                    continue
                msgs.append({"t": float(t), "text": body or "", "author": author or ""})
    msgs.sort(key=lambda m: m["t"])
    length = 0.0
    if isinstance(data, dict) and isinstance(data.get("video"), dict):
        length = float(data["video"].get("length", 0) or 0)
    if msgs:
        length = max(length, msgs[-1]["t"])
    return msgs, length


def streamer_name(path):
    """Best-effort channel name from a TwitchDownloader chat file (used for on-screen credit)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s = data.get("streamer") or {}
            return s.get("name") or s.get("login") or ""
    except Exception:
        pass
    return ""


def top_reaction(messages, start, end, game="none"):
    """Most common reaction kind and the loudest short phrase chat used between start and end."""
    kinds, phrases = Counter(), Counter()
    for m in messages:
        if start <= m["t"] <= end:
            w, k = classify(m["text"], game)
            if k:
                kinds[k] += w
                p = re.sub(r"\s+", " ", m["text"].strip())
                if 2 <= len(p) <= 22:
                    phrases[p.upper()] += 1
    kind = kinds.most_common(1)[0][0] if kinds else None
    phrase = phrases.most_common(1)[0][0] if phrases else ""
    return kind, phrase


def score_moment(words, peak_db, duration=30.0):
    """Score highlight coherence and energy (0-100)."""
    score = 50.0
    if peak_db > -18.0:
        score += 25.0
    elif peak_db > -28.0:
        score += 15.0
    elif peak_db < -42.0:
        score -= 15.0

    if words:
        w_count = len(words)
        if w_count >= 5:
            score += 15.0
        text = " ".join(str(w.get("w", "")) for w in words).lower()
        high_energy_keywords = {"no", "omg", "what", "bro", "dead", "die", "cops", "star", "holy", "haha", "gator", "shit", "fuck", "run", "look"}
        if any(k in text for k in high_energy_keywords):
            score += 10.0
    else:
        score -= 20.0

    return round(max(10.0, min(100.0, score)), 1)

