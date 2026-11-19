"""Hook text, title, caption and hashtags for each clip, from what chat reacted with.

Every clip gets its own title (identical titles look like spam to YouTube) and credits the streamer."""
import random

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

GTA6_TITLES = {
    "laugh": [
        "Bro Regretted Everything in GTA 6",
        "The Exact Second His Soul Left His Body in GTA 6",
        "He Broke Character So Fast in GTA 6",
        "He Really Thought He Was Safe in Vice City",
        "Bro Found Out What Happens in Vice City",
        "Rockstar Did Him So Dirty in GTA 6",
        "Bro Tried to Hold It In and Failed Miserably",
        "When GTA 6 Physics Decide to Humble You",
        "He Was NOT Prepared for What Happened Next",
        "Bro Went Through All 5 Stages of Grief in GTA 6",
    ],
    "shock": [
        "He Did NOT Expect Vice City Cops to Do This",
        "The Exact Moment Everything Went Wrong in GTA 6",
        "He Discovered GTA 6 AI the Hard Way",
        "He Paused the Game in Real Life",
        "Rockstar Made GTA 6 NPCs Way Too Ruthless",
        "Bro Thought Plot Armor Existed in GTA 6",
        "He Stared Into the Void After This Happened",
        "When You Accidentally Get 5 Stars in GTA 6",
        "He Realized He Made a Huge Mistake in GTA 6",
    ],
    "hype": [
        "He Pulled Off the Cleanest Escape in GTA 6",
        "The Most Insane Interaction in GTA 6 History",
        "Bro Did the Impossible in Vice City",
        "12 Years of Waiting Was Worth This One Play",
        "He Actually Outsmarted 5-Star Vice City Cops",
        "When Flow State Hits in GTA 6",
        "The Cleanest Move You Will See in GTA 6 Today",
    ],
    "game": [
        "When GTA 6 Physics Decide Your Fate",
        "GTA 6 Just Delivered the Funniest Scene Ever",
        "Rockstar Spent 12 Years Perfecting This Exact Chaos",
        "Florida in GTA 6 is Completely Out of Pocket",
        "Bro Got Folded by Vice City Physics in 4K",
        "The Most Unhinged NPC Interaction in GTA 6",
        "Vice City Air Hit Him With a Reality Check",
    ],
    None: [
        "The Funniest Moment in GTA 6 So Far",
        "He Thought He Was Ready for GTA 6",
        "When Vice City Reality Hits You Hard",
        "Bro Regretted Everything in GTA 6",
    ],
}

IG_COMMENTS = {
    "laugh": [
        "bro went through all 5 stages of grief in 3 seconds 😭💀",
        "the pause of pure realization killed me 💀",
        "he looked at the webcam like 'chat please tell me that didn't happen' 😭",
        "bro forgot Rockstar spent 12 years coding NPC vengeance 💀",
        "nah the silence after it happened was deafening 😭",
        "bro died inside before his character even hit the ground 💀",
        "the instant regret on his face in 4K 😭💀",
        "bro sat there re-evaluating his entire existence 💀",
        "he's fighting demons trying not to lose his mind 😭",
        "Rockstar spent 2 billion dollars to fold streamers like laundry 💀",
        "bro's controller was definitely sweating 😭💀",
        "nah bro got humbled by Vice City real quick 💀",
        "the way his laugh slowly turned into despair 😭😭",
        "bro really thought he had plot armor in GTA 6 💀",
    ],
    "shock": [
        "bro paused the game in real life 💀",
        "Vice City police do NOT negotiate bro 😭🚔",
        "ain't no way that just happened on a live stream 😳",
        "bro really thought plot armor was gonna save him 💀",
        "he stared into the void for a solid 10 seconds 😭",
        "the AI literally predicted his every move bro 💀",
        "even the NPCs in the background were flabbergasted 😳",
        "bro did NOT read the terms and conditions of Vice City 💀",
        "that wasn't in the mission briefing bro 😭💀",
        "my soul would've evacuated the building right there 😳",
        "Rockstar programmed the AI to have personal beef with him 💀",
        "he sat there buffering in 1080p 😭",
    ],
    "hype": [
        "bro is actually the main character 🐐🔥",
        "cleanest escape in Vice City history and it's not even close 🔥",
        "nah that timing was straight out of a Hollywood movie 🤯",
        "bro played that like he had 10,000 hours in Vice City 🔥",
        "he definitely had the soundtrack bumping in his head 🐐",
        "give that man his flowers right now bro 🔥",
        "bro entered flow state and cooked the entire map 🤯",
        "12 years of waiting was worth this exact play 🔥",
        "the movement was actually illegal bro 🤯",
    ],
    "game": [
        "when GTA 6 physics decide your free trial of life has expired 💀",
        "Florida Man simulator 2026 is actually unreal 😭💀",
        "Rockstar really spent 12 years making NPCs hold personal grudges 💀",
        "bro got folded by Vice City physics in 60 FPS 😭",
        "nah Vice City is completely lawless and I love it 💀",
        "the ragdoll physics in this game are dangerously personal 😭💀",
        "Rockstar didn't have to violate him like that bro 💀",
        "Vice City air hit him with an immediate reality check 😭",
        "the Euphoria physics engine was waiting for this exact moment 💀",
    ],
    None: [
        "bro was NOT ready for GTA 6 reality 💀",
        "nah Vice City is totally out of pocket 💀",
        "the pause of pure realization killed me 😭",
        "ain't no way that just happened on stream 😳",
        "bro sat there re-evaluating his entire life decisions 😭💀",
    ],
}

IG_USERNAMES = [
    "vicecity_daily", "gta6_clips", "floridaman_moments", "rockstar_central",
    "npc_behaviour", "streamer_outtakes", "clipdimension", "hoodclips_gaming",
    "vicecity_pd", "daily_ragdolls", "gta6memes", "pixelated_moments"
]
IG_TIMES = ["2h", "4h", "7h", "11h", "14h", "1d"]
IG_LIKES = ["24.8K", "38.2K", "47.6K", "61.3K", "78.9K", "94.2K", "118K"]


def _tc(text):
    """Title Case without breaking apostrophes (Couldn't, not Couldn'T)."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())


def make(rank, kind, phrase, streamer="", game="none", fixed_hook="", seed=None):
    rnd = random.Random(seed if seed is not None else rank)
    hook = fixed_hook or rnd.choice(HOOKS.get(kind) or HOOKS[None])
    ig_comment = {
        "text": rnd.choice(IG_COMMENTS.get(kind) or IG_COMMENTS[None]),
        "username": rnd.choice(IG_USERNAMES),
        "time": rnd.choice(IG_TIMES),
        "likes": rnd.choice(IG_LIKES),
        "verified": rnd.random() < 0.35,
    }
    who = streamer.strip()
    clean_handle = who.lstrip("@") if who else ""
    if game in ("gta6", "gta"):
        options = GTA6_TITLES.get(kind) or GTA6_TITLES[None]
        pick = rnd.choice(options)
        if clean_handle and rnd.random() < 0.40:
            if pick.startswith("He ") or pick.startswith("Bro "):
                pick = f"@{clean_handle} " + pick[pick.index(" ") + 1:]
            else:
                pick = f"When @{clean_handle} " + pick[:1].lower() + pick[1:]
        base = pick
        tags = GTA6_TAGS
    else:
        base = f"{who + ': ' if who else ''}{_tc(hook)}"
        tags = GENERIC_TAGS
    emoji = {"laugh": " 💀", "hype": " 🔥", "shock": " 😳", "game": " 🚨"}.get(kind, "")
    title = (base + emoji)[:90]
    credit_line = f"Credit: @{clean_handle} (go follow!)" if clean_handle else ""
    description = "\n".join(x for x in [title, credit_line, "", " ".join(tags)] if x is not None)
    return {"hook": hook, "title": title, "description": description,
            "tags": [t.lstrip("#") for t in tags], "credit": f"@{clean_handle}" if clean_handle else "",
            "ig_comment": ig_comment}
