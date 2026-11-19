"""Hook text, title, caption and hashtags for each clip, built from the actual moment.

Every title is assembled from what really happened in the clip: the streamer's name, the phrase chat
spammed during the spike and keywords from the transcript. A title history (title_history.json, committed
with the upload history) makes sure no title is ever repeated and no template is reused within the last
~30 titles, so two streamers never get the same headline.

The Instagram-style comment sticker uses a real chat message from the spike (filtered for spam, emote
walls, links and slurs) and the real chatter's name, falling back to a transcript quote, then to a pool."""
import json
import random
import re
import time
from pathlib import Path

HOOKS = {
    "laugh": ["CHAT COULDN'T STOP LAUGHING", "I CAN'T BREATHE", "THIS IS SO DUMB", "HE DIDN'T SEE IT COMING"],
    "hype": ["THIS IS INSANE", "WAIT FOR IT...", "CLEANEST PLAY EVER?", "CHAT WENT CRAZY"],
    "shock": ["WAIT... WHAT JUST HAPPENED?", "NO WAY THIS HAPPENED", "HE WAS NOT READY", "WATCH TILL THE END"],
    "game": ["13 YEARS WAS WORTH IT", "GTA 6 PHYSICS ARE UNREAL", "VICE CITY IS NOT SAFE",
             "5 STARS IN 10 SECONDS"],
    None: ["WAIT FOR IT...", "WATCH TILL THE END", "CHAT LOST IT HERE"],
}

GTA6_TAGS = ["#GTA6", "#GTAVI", "#GrandTheftAuto6", "#ViceCity", "#Shorts", "#Gaming", "#Rockstar"]
GENERIC_TAGS = ["#Shorts", "#Gaming", "#Twitch", "#Streamer", "#Clips"]

DEFAULT_HISTORY = Path(__file__).resolve().parent.parent / "title_history.json"
HISTORY_WINDOW = 30

# ---------------------------------------------------------------- title templates
# Placeholders: {name} streamer, {phrase} what chat spammed, {kw} a transcript/game keyword,
# {quote} 2-6 words the streamer actually said, {game} "GTA 6".
# Each template lists the placeholders it needs; it is only used when they are available.
TITLE_TEMPLATES = [
    ("chat_spam", ("name", "phrase"), "Chat Spammed \"{phrase}\" After {name} Did This in {game}"),
    ("chat_lost", ("name", "phrase"), "{name}'s Chat Lost It: \"{phrase}\""),
    ("phrase_lead", ("name", "phrase"), "\"{phrase}\" - {name}'s Chat After This {game} Moment"),
    ("said_then", ("name", "quote"), "{name} Said \"{quote}\" and {game} Answered"),
    ("quote_lead", ("name", "quote"), "\"{quote}\" - {name} Seconds Before Disaster in {game}"),
    ("famous_last", ("name", "quote"), "{name}'s Famous Last Words: \"{quote}\""),
    ("vs_kw", ("name", "kw"), "{name} vs {kw} in {game} Went Exactly How You Think"),
    ("kw_humbled", ("name", "kw"), "{kw} Humbled {name} Live on Stream"),
    ("kw_wasnt_ready", ("name", "kw"), "{name} Was Not Ready for the {kw} in {game}"),
    ("kw_never", ("name", "kw"), "{name} Has Trust Issues With {kw} After This"),
    ("kw_found_out", ("name", "kw"), "{name} Found Out What the {kw} Does in {game}"),
    ("first_time", ("name",), "{name} Playing {game} for the First Time Was a Mistake"),
    ("exact_second", ("name",), "The Exact Second {name} Regretted Playing {game}"),
    ("vice_city", ("name",), "Vice City Did {name} So Dirty"),
    ("nobody_warned", ("name",), "Nobody Warned {name} About This Part of {game}"),
    ("stages_grief", ("name",), "{name} Went Through All 5 Stages of Grief in {game}"),
    ("plot_armor", ("name",), "{name} Thought Plot Armor Existed in {game}"),
    ("stream_froze", ("name",), "{name}'s Stream Went Silent After This {game} Moment"),
    ("rockstar_cooked", ("name",), "Rockstar Cooked {name} With This One"),
    ("chat_saw", ("name",), "Chat Saw It Coming. {name} Did Not."),
    ("speechless", ("name",), "{game} Left {name} Speechless"),
    ("reaction_kind", ("name", "kind_word"), "{name}'s {kind_word} Reaction in {game} Is Unreal"),
]

KIND_WORDS = {"laugh": "Funniest", "shock": "Most Shocked", "hype": "Hardest", "game": "Wildest"}
EMOJI_BY_KIND = {"laugh": ["💀", "😭", "😂"], "hype": ["🔥", "🐐", "🤯"], "shock": ["😳", "😱", "💀"],
                 "game": ["🚨", "💀", "😭"], None: ["💀", "😭", "😳"]}

# transcript keywords worth putting in a title (GTA 6 nouns and big moments)
TITLE_KEYWORDS = {
    "cops": "Cops", "cop": "Cops", "police": "Police", "swat": "SWAT", "helicopter": "Helicopter",
    "heli": "Helicopter", "tank": "Tank", "alligator": "Alligator", "gator": "Gator", "boat": "Boat",
    "jetski": "Jet Ski", "plane": "Plane", "car": "Car", "truck": "Truck", "bike": "Bike",
    "motorcycle": "Motorcycle", "train": "Train", "shark": "Shark", "npc": "NPC", "npcs": "NPCs",
    "lucia": "Lucia", "jason": "Jason", "bank": "Bank", "heist": "Heist", "store": "Store",
    "stars": "5 Stars", "wanted": "Wanted Level", "explosion": "Explosion", "bomb": "Bomb",
    "grenade": "Grenade", "rpg": "RPG", "beach": "Beach", "club": "Club", "strip": "Strip Club",
    "gas": "Gas Station", "hospital": "Hospital", "jail": "Jail", "parachute": "Parachute",
    "ramp": "Ramp", "bridge": "Bridge", "ocean": "Ocean", "water": "Water", "dog": "Dog",
    "crocodile": "Crocodile", "florida": "Florida", "miami": "Vice City", "chase": "Chase",
    "physics": "Physics", "ragdoll": "Ragdoll", "mission": "Mission", "robbery": "Robbery",
}

STOPWORDS = set("""a an the and or but so to of in on at for with is are was were be been it its it's this that
i you he she we they me my your our his her them what why how when where who just like yeah yo bro oh
okay ok um uh no not do does did dont don't im i'm gonna wanna got get go going come there here""".split())

# ---------------------------------------------------------------- comment sticker
FALLBACK_COMMENTS = {
    "laugh": [
        "bro went through all 5 stages of grief in 3 seconds 😭💀",
        "the pause after it happened killed me 💀",
        "he looked at chat like 'tell me that didn't happen' 😭",
        "nah the silence after that was deafening 😭",
        "the instant regret on his face 😭💀",
        "bro's controller was definitely sweating 😭💀",
    ],
    "shock": [
        "bro paused the game in real life 💀",
        "Vice City police do NOT negotiate 😭🚔",
        "ain't no way that just happened live 😳",
        "he sat there buffering in 1080p 😭",
        "that wasn't in the mission briefing 😭💀",
    ],
    "hype": [
        "bro is actually the main character 🐐🔥",
        "nah that timing was straight out of a movie 🤯",
        "give that man his flowers right now 🔥",
        "the movement was actually illegal 🤯",
    ],
    "game": [
        "Florida Man simulator is actually unreal 😭💀",
        "the ragdoll physics in this game are personal 😭💀",
        "Vice City is completely lawless and I love it 💀",
        "the NPCs in this game hold grudges fr 💀",
    ],
    None: [
        "bro was NOT ready for Vice City 💀",
        "ain't no way that just happened on stream 😳",
        "the pause of pure realization killed me 😭",
    ],
}

HANDLE_PARTS_A = ["vice", "leonida", "chat", "late", "night", "clip", "npc", "jason", "lucia", "pixel", "stream", "ragdoll"]
HANDLE_PARTS_B = ["watcher", "enjoyer", "goblin", "fan", "andy", "lurker", "clips", "moments", "fiend", "kid"]
IG_TIMES = ["1h", "2h", "3h", "4h", "5h", "6h", "8h", "9h", "11h", "13h", "17h", "1d", "2d"]

EMOTE_WORDS = {
    "kekw", "omegalul", "lul", "lulw", "pog", "poggers", "pogchamp", "pogu", "kappa", "sadge", "monkas", "monkaw",
    "pepelaugh", "pepega", "aware", "copium", "hopium", "clueless", "deadge", "ez", "clap", "catjam", "residentsleeper",
    "biblethump", "pepehands", "widepeepohappy", "kekl", "icant", "xd", "lmao", "lmfao", "lol", "w", "l", "f", "gg",
    "o7", "oop", "ome", "jebaited", "trihard", "kreygasm", "notlikethis", "wutface", "4head", "wesmart", "based",
    "listening", "classic", "gigachad", "chatting", "erm", "stare", "susge", "hmm", "o", "omg", "wtf", "bruh",
}
LINK_RE = re.compile(r"(https?://|www\.|\.com\b|\.tv\b|\.gg\b|discord|\.ly\b)", re.I)
MENTION_RE = re.compile(r"@\w+")
# keep YouTube-safe: drop messages with slurs / self-harm bait / sexual spam
BLOCK_RE = re.compile(r"(n[i1!]gg|f[a@]gg?[o0]?t|retard|tranny|kys\b|kill\s+yourself|rape|porn|onlyfans|nazi|hitler|"
                      r"cancer|\bslut|\bwhore)", re.I)
SPAM_RE = re.compile(r"(\b\w+\b)(\s+\1\b){3,}", re.I)
CAMEL_EMOTE_RE = re.compile(r"^[a-z]+[A-Z][A-Za-z0-9]*$")
CHAT_PHRASE_RE = re.compile(r"[^\w\s'!?.,]")


def _tc(text):
    """Title Case without breaking apostrophes (Couldn't, not Couldn'T)."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())


# ---------------------------------------------------------------- history
def load_history(path=None):
    p = Path(path) if path else DEFAULT_HISTORY
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record_titles(entries, path=None, keep=500):
    """Append [{'title','template','streamer'}] to the title history file."""
    p = Path(path) if path else DEFAULT_HISTORY
    hist = load_history(p)
    now = int(time.time())
    for e in entries:
        if e.get("title"):
            hist.append({"title": e["title"], "template": e.get("template", ""),
                         "streamer": e.get("streamer", ""), "at": e.get("at", now)})
    hist = hist[-keep:]
    p.write_text(json.dumps(hist, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return hist


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


# ---------------------------------------------------------------- moment features
def _is_emote_token(tok):
    t = tok.strip(".,!?'\"").lower()
    if not t:
        return True
    if t in EMOTE_WORDS or re.fullmatch(r"(lo+l|lma+o+|ha(ha)+h?|xd+|w+|l+|o+)", t):
        return True
    if CAMEL_EMOTE_RE.match(tok.strip(".,!?")):   # pepeLaugh, monkaW, catJAM
        return True
    return not re.search(r"[a-z]", t)            # emoji / numbers only


def clean_chat_text(text):
    """Normalise one chat message for on-screen use, or '' if it is not usable."""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("!") or LINK_RE.search(t) or BLOCK_RE.search(t):
        return ""
    t = MENTION_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(.)\1{4,}", r"\1\1\1", t)      # "NOOOOOOOO" -> "NOOO"
    if SPAM_RE.search(t):
        return ""
    return t


def chat_phrase_candidates(messages, start, end):
    """Cleaned messages in [start, end] that read like a real human comment: (text, author, t)."""
    out = []
    for m in messages:
        if not (start <= m.get("t", -1) <= end):
            continue
        txt = clean_chat_text(m.get("text", ""))
        if not txt or not (8 <= len(txt) <= 90):
            continue
        toks = txt.split()
        real = [w for w in toks if not _is_emote_token(w) and len(w.strip(".,!?'\"")) >= 2]
        if len(real) < 3 or len(real) < 0.5 * len(toks):
            continue
        letters = re.sub(r"[^A-Za-z]", "", txt)
        if letters.isupper() and len(letters) > 12:   # full-caps shouting reads badly: soften
            txt = txt[:1].upper() + txt[1:].lower()
        out.append((txt, (m.get("author") or "").strip(), float(m.get("t", 0))))
    return out


def pick_sticker_comment(messages, start, end, peak_t=None, words=None, kind=None, rnd=None):
    """Return {'text', 'username', 'source'} for the Instagram comment sticker.

    1. a real top chat message from the spike (most liked-looking: reaction words, near the peak, 20-60 chars)
    2. a transcript line the streamer said
    3. a varied fallback pool"""
    rnd = rnd or random.Random()
    cands = chat_phrase_candidates(messages or [], start, end)
    if cands:
        from .chat import classify
        peak = peak_t if peak_t is not None else (start + end) / 2

        def score(c):
            txt, _a, t = c
            w, _k = classify(txt, "gta6")
            ln = len(txt)
            shape = 1.0 if 18 <= ln <= 60 else 0.6
            near = 1.0 / (1.0 + abs(t - peak) / 6.0)
            return w + shape + near + rnd.random() * 0.15

        txt, author, _t = max(cands, key=score)
        return {"text": txt, "username": handle_from_author(author, rnd), "source": "chat"}
    quote = transcript_quote(words, min_words=4, max_words=12)
    if quote:
        lead = rnd.choice(["he really said", "bro said", "the way he said", "not him saying"])
        return {"text": f"{lead} \"{quote.lower()}\" {rnd.choice(['😭', '💀', '😭💀'])}",
                "username": handle_from_author("", rnd), "source": "transcript"}
    pool = FALLBACK_COMMENTS.get(kind) or FALLBACK_COMMENTS[None]
    return {"text": rnd.choice(pool), "username": handle_from_author("", rnd), "source": "pool"}


def handle_from_author(author, rnd=None):
    rnd = rnd or random.Random()
    a = re.sub(r"[^a-z0-9_.]", "", (author or "").lower())
    if 3 <= len(a) <= 24:
        return a
    h = rnd.choice(HANDLE_PARTS_A) + rnd.choice(["_", "", "."]) + rnd.choice(HANDLE_PARTS_B)
    if rnd.random() < 0.6:
        h += str(rnd.randint(2, 99))
    return h


def random_likes(rnd=None):
    """Plausible like count between 1.2K and 48K, formatted like Instagram."""
    rnd = rnd or random.Random()
    n = int(rnd.uniform(1200, 48000))
    if n < 10000 or rnd.random() < 0.5:
        return f"{n / 1000:.1f}K"
    return f"{n // 1000}K"


def transcript_quote(words, min_words=2, max_words=6):
    """A short, punchy run of words the streamer said (prefers one ending with ! or ?)."""
    if not words:
        return ""
    toks = [str(w.get("w", "")).strip() for w in words if str(w.get("w", "")).strip()]
    sents, cur = [], []
    for t in toks:
        cur.append(t)
        if t[-1:] in ".!?":
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    good = []
    for s in sents:
        clean = [re.sub(r"[^\w'?!-]", "", x) for x in s]
        clean = [x for x in clean if x]
        if not (min_words <= len(clean) <= max_words):
            continue
        if BLOCK_RE.search(" ".join(clean)):
            continue
        txt = " ".join(clean).strip(".,")
        if len(re.sub(r"[^a-zA-Z]", "", txt)) < 6:
            continue
        good.append((2 if txt.endswith(("!", "?")) else 1, len(clean), txt))
    if not good:
        return ""
    good.sort(key=lambda g: (-g[0], abs(g[1] - (min_words + max_words) / 2)))
    t = good[0][2]
    return t[:1].upper() + t[1:]


def transcript_keyword(words, messages_text=""):
    """The most title-worthy noun from the transcript (or chat), e.g. 'Cops', 'Helicopter'."""
    blob = " ".join(str(w.get("w", "")) for w in (words or [])) + " " + (messages_text or "")
    toks = re.findall(r"[a-z]+", blob.lower())
    counts = {}
    for t in toks:
        if t in TITLE_KEYWORDS:
            k = TITLE_KEYWORDS[t]
            counts[k] = counts.get(k, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _phrase_for_title(phrase):
    p = clean_chat_text(phrase or "")
    if not p:
        return ""
    p = CHAT_PHRASE_RE.sub("", p).strip()
    toks = p.split()
    if not (1 <= len(toks) <= 5) or not (2 <= len(p) <= 24):
        return ""
    if all(_is_emote_token(t) for t in toks) and len(toks) == 1 and len(p) < 4:
        return ""
    return p.upper()


# ---------------------------------------------------------------- main entry
def make(rank, kind, phrase, streamer="", game="none", fixed_hook="", seed=None, words=None,
         chat_text="", history=None, used=None, window=HISTORY_WINDOW):
    """Title, description, tags and hook for one clip.

    history: list from load_history() (titles/templates already used); used: titles made earlier in this run."""
    rnd = random.Random(seed) if seed is not None else random.Random()
    hook = fixed_hook or rnd.choice(HOOKS.get(kind) or HOOKS[None])
    who = streamer.strip()
    clean_handle = who.lstrip("@") if who else ""
    name = clean_handle or "Streamer"
    is_gta = game in ("gta6", "gta")
    game_label = "GTA 6" if is_gta else "This Game"

    hist = list(history or []) + [{"title": t, "template": tid} for t, tid in (used or [])]
    recent_templates = {h.get("template") for h in hist[-window:] if h.get("template")}
    used_titles = {_norm_title(h.get("title")) for h in hist}

    fields = {
        "name": name if clean_handle else "",
        "phrase": _phrase_for_title(phrase),
        "quote": transcript_quote(words),
        "kw": transcript_keyword(words, chat_text),
        "kind_word": KIND_WORDS.get(kind, ""),
        "game": game_label,
    }
    emoji = rnd.choice(EMOJI_BY_KIND.get(kind) or EMOJI_BY_KIND[None])

    def render_t(tmpl):
        return tmpl.format(**fields)

    usable = [t for t in TITLE_TEMPLATES if all(fields.get(f) for f in t[1])]
    # moment-specific templates (chat phrase / quote / keyword) first, then the rest
    specific = [t for t in usable if any(f in ("phrase", "quote", "kw") for f in t[1])]
    generic = [t for t in usable if t not in specific]
    rnd.shuffle(specific)
    rnd.shuffle(generic)
    order = [t for t in specific + generic if t[0] not in recent_templates] + \
            [t for t in specific + generic if t[0] in recent_templates]

    title, template_id = "", ""
    for tid, _need, tmpl in order:
        cand = (render_t(tmpl) + " " + emoji)[:95]
        if _norm_title(cand) in used_titles:
            continue
        title, template_id = cand, tid
        break
    if not title:  # everything used: make it unique with the moment's clock
        base = f"{name} in {game_label}: Moment #{len(hist) + 1}"
        title, template_id = base + " " + emoji, "fallback"

    tags = GTA6_TAGS if is_gta else GENERIC_TAGS
    if clean_handle:
        tags = tags + ["#" + re.sub(r"\W", "", clean_handle)]
    credit_line = f"Credit: @{clean_handle} (go follow!)" if clean_handle else ""
    description = "\n".join(x for x in [title, credit_line, "", " ".join(tags)] if x is not None)
    return {"hook": hook, "title": title, "template": template_id, "description": description,
            "tags": [t.lstrip("#") for t in tags], "credit": f"@{clean_handle}" if clean_handle else ""}


def make_ig_comment(messages, start, end, peak_t=None, words=None, kind=None, seed=None):
    rnd = random.Random(seed) if seed is not None else random.Random()
    c = pick_sticker_comment(messages, start, end, peak_t, words, kind, rnd)
    return {"text": c["text"], "username": c["username"], "source": c["source"],
            "time": rnd.choice(IG_TIMES), "likes": random_likes(rnd), "verified": rnd.random() < 0.08}
