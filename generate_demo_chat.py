#!/usr/bin/env python3
"""
Generate realistic TwitchDownloader-compatible chat demo JSON
Matches the guide example: 2 hours of stream, background chat, and chaotic spikes.
"""

import json
import random
from pathlib import Path


def generate_demo_chat(output_file="demo_chat.json"):
    random.seed(42)
    stream_length = 7200  # 2 hours
    comments = []

    # Common Twitch emotes and messages
    messages_pool = [
        "LMAO", "KEKW", "OMEGALUL", "WHAT JUST HAPPENED", "NO WAAAAY",
        "clip it!!", "GG", "POGGERS", "W", "bro missed the jump",
        "chat did you see that??", "monkaW", "AYOOO", "Hahaha", "skull emoji",
        "GTA 6 hype", "LETS GOOO", "WTF", "pepeLaugh", "RIP"
    ]

    commenters = [
        "x_gamer99", "twitch_viewer", "speedy_stream", "sub_legend", "clipperrr",
        "night_owl", "stream_lurker", "pixel_king", "pro_sniper", "funny_bot"
    ]

    # Baseline chatter: ~1-3 messages every few seconds
    current_time = 0.0
    while current_time < stream_length:
        current_time += random.uniform(0.3, 0.8)
        if current_time >= stream_length:
            break
        comments.append({
            "_id": f"msg_{len(comments)}",
            "content_offset_seconds": round(current_time, 2),
            "commenter": {"display_name": random.choice(commenters)},
            "message": {"body": random.choice(messages_pool)}
        })

    # Add 5 major spikes
    # 1. 0:20:00 (1200s) -> ~100 messages in 10s
    # 2. 0:55:30 (3330s) -> ~110 messages in 10s
    # 3. 1:40:10 (6010s) -> ~120 messages in 10s
    # 4. 0:38:15 (2295s) -> ~70 messages in 10s
    # 5. 1:15:00 (4500s) -> ~60 messages in 10s
    spikes = [
        (1200, 100, ["LMAOOOO", "AINT NO WAY", "CLIP THAT", "BROOO", "KEKW"]),
        (3330, 110, ["WHAT", "NO SHOT", "POGGG", "OMEGALUL", "HES HIM"]),
        (6010, 120, ["W PLAY", "INSANE", "GOD TIER", "WTFFFF", "HOLY"]),
        (2295, 75, ["hahahaha", "rip bozo", "skull", "F in chat"]),
        (4500, 65, ["wait what", "glitch???", "NOOO", "so close"]),
    ]

    for spike_t, count, spike_phrases in spikes:
        for _ in range(count):
            offset = spike_t + random.uniform(0.0, 9.8)
            comments.append({
                "_id": f"msg_spike_{len(comments)}",
                "content_offset_seconds": round(offset, 2),
                "commenter": {"display_name": random.choice(commenters)},
                "message": {"body": random.choice(spike_phrases)}
            })

    # Sort all comments by content_offset_seconds
    comments.sort(key=lambda c: c["content_offset_seconds"])

    data = {
        "video": {
            "id": "demo_1234567890",
            "title": "GTA Stream - Best Moments Demo",
            "length": stream_length
        },
        "comments": comments
    }

    out_path = Path(output_file)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(',', ':'))

    print(f"Generated {len(comments)} chat comments in {out_path} ({stream_length}s stream)")
    return out_path


if __name__ == "__main__":
    generate_demo_chat()
