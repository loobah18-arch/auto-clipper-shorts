#!/usr/bin/env python3
"""
Generate Tech Fact & Developer Tip Shorts for Auto Clipper Shorts.
Uses Neural Text-to-Speech (edge-tts) for high-energy tech narration,
generates dynamic neon karaoke subtitles, tech badges, and audio visualizers.
"""

import os
import sys
import json
import random
import shutil
import asyncio
from pathlib import Path

# Add project root to sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

try:
    import edge_tts
except ImportError:
    edge_tts = None

from main import (
    create_word_timestamps_from_segment,
    generate_karaoke_ass_subtitles,
    render_studio_visualizer_short,
    upload_to_youtube,
    load_json,
    save_json,
    HISTORY_PATH,
    log,
    OUTPUT_DIR
)

TECH_FACTS_DATABASE = [
    {
        "topic": "UNDERWATER INTERNET CABLES",
        "badge": "TECH FACT 💡",
        "title": "How 99% of the Internet Travels Under the Ocean 🌊 #TechShorts",
        "voice": "en-US-ChristopherNeural",
        "tags": ["techfacts", "technology", "internet", "cables", "networking", "shorts"],
        "script": (
            "Did you know that over ninety nine percent of all international internet traffic does not travel through satellites in space, "
            "but through massive fiber optic cables resting on the pitch black ocean floor? "
            "There are over five hundred active subsea cables stretching eight hundred thousand miles across the Earth, connecting every continent. "
            "These glass lines are barely as thick as a garden hose, yet they transmit petabits of financial transactions, streaming video, and cloud data every single millisecond. "
            "Here is the crazy part. If a deep sea anchor accidentally snags one of these cables, or an underwater earthquake shifts the seabed, "
            "entire nations can instantly lose their internet connection overnight! "
            "Specialized repair ships have to spend weeks in rough seas grappling the ocean floor to pull the severed glass fibers back to the surface for microscopic laser welding. "
            "Subscribe for more mind blowing tech secrets!"
        )
    },
    {
        "topic": "LINUX TERMINAL SUPERPOWER",
        "badge": "DEV TRICK ⚡",
        "title": "The Secret Linux Shortcut Every Developer Needs 🐧 #Shorts",
        "voice": "en-US-GuyNeural",
        "tags": ["linux", "terminal", "bash", "developer", "codingtips", "shorts"],
        "script": (
            "Stop frantically hitting the up arrow fifty times just to find a terminal command you ran three weeks ago! "
            "Every programmer needs to know this secret Linux superpower. "
            "Inside any bash or zsh terminal, just press Control plus R on your keyboard. "
            "This instantly activates the recursive history search engine. "
            "The moment you start typing a single keyword like docker, git, or ssh, Linux instantly searches your entire shell history and auto-completes your longest, most complicated command with zero typos! "
            "If you press Control plus R again, it cycles backwards through every past variation you ever executed. "
            "Once you find it, just press Enter to run it immediately, or hit the Right Arrow key to edit your parameters. "
            "This one shortcut will save you hours of command line frustration every single month. "
            "Drop a like if you did not know this trick!"
        )
    },
    {
        "topic": "FLOATING POINT SECRETS",
        "badge": "CS FACT 💻",
        "title": "Why 0.1 + 0.2 Is NOT 0.3 In Computer Science 🤯 #Shorts",
        "voice": "en-US-EricNeural",
        "tags": ["programming", "coding", "computerscience", "math", "python", "shorts"],
        "script": (
            "If you open your browser console, Python terminal, or C plus plus compiler right now and calculate zero point one plus zero point two, "
            "you will not get zero point three! "
            "Instead, you get zero point three zero zero zero zero zero zero zero zero zero zero zero zero zero zero four! "
            "This is not a bug in your computer, it is a fundamental mathematical law of how microprocessors work. "
            "Modern computers do not count in base ten decimals like humans. They use base two binary floating point numbers under the IEEE seven fifty four standard. "
            "In binary math, fractions like one tenth create an infinite repeating sequence, exactly like trying to write one third in decimal! "
            "Because computer memory is finite, the CPU has to round off the last binary bit. "
            "This tiny precision error has famously crashed space exploration rockets and caused multi-million dollar banking glitches in financial trading algorithms. "
            "Follow for more computer science revelations!"
        )
    },
    {
        "topic": "HOW SSH KEYS WORK",
        "badge": "CYBERSECURITY 🛡️",
        "title": "How SSH Logs You In Without Sending Your Password 🔐 #Shorts",
        "voice": "en-US-ChristopherNeural",
        "tags": ["cybersecurity", "ssh", "encryption", "infosec", "linux", "shorts"],
        "script": (
            "How do SSH keys log you into top secret cloud servers across the globe without ever transmitting your password across the internet? "
            "It all comes down to an ingenious mathematical trapdoor known as asymmetric cryptography. "
            "When you generate an SSH key pair, your computer creates a public key and a private key. "
            "You place the public key on the remote server, while keeping your private key heavily guarded on your laptop. "
            "When you log in, the server never asks for your secret password. "
            "Instead, the server generates a random cryptographic challenge, locks it with your public key, and transmits it to your computer. "
            "The mathematical magic is that only your private key has the unique mathematical power to decrypt and solve that challenge! "
            "Your machine sends back the proof, and the server unlocks instantly, keeping your access completely immune to hacker eavesdropping. "
            "Subscribe for more cybersecurity breakdowns!"
        )
    },
    {
        "topic": "THE FIRST COMPUTER BUG",
        "badge": "TECH HISTORY 📜",
        "title": "The Crazy True Story of the First Computer Bug 🪲 #TechShorts",
        "voice": "en-US-GuyNeural",
        "tags": ["techhistory", "programming", "debugging", "computerscience", "shorts"],
        "script": (
            "Have you ever wondered why software engineers call fixing a broken program debugging? "
            "The origin story is completely literal and involves a real two-inch moth! "
            "In nineteen forty seven, computer science pioneer Grace Hopper was working on the Harvard Mark Two electromechanical computer for the United States Navy. "
            "Suddenly, the massive sixteen-ton machine stopped processing calculations correctly. "
            "The engineering team spent hours meticulously inspecting thousands of electrical relays across the entire computer until they finally inspected relay number seventy in panel F. "
            "Trapped between the metal contact points was an actual dead moth that was short-circuiting the electrical signal! "
            "They removed the insect with tweezers, taped it into their official engineering logbook, and wrote: First actual case of bug being found. "
            "From that exact moment in history, resolving software errors was officially crowned debugging. "
            "Drop a follow for more tech history!"
        )
    }
]


async def synthesize_tech_audio(script_text: str, output_mp3: Path, voice: str = "en-US-ChristopherNeural", rate: str = "+10%"):
    """Synthesizes high quality voice narration and returns word-timed segments with automatic retry."""
    if not edge_tts:
        raise RuntimeError("edge-tts is not installed. Run `pip install edge-tts`")

    log(f"🎙️ Synthesizing tech narration voice using {voice} (rate: {rate})...")
    voices_to_try = [voice, "en-US-GuyNeural", "en-US-EricNeural", "en-US-AndrewNeural", "en-US-BrianNeural"]
    sentences = []
    
    for v in voices_to_try:
        try:
            comm = edge_tts.Communicate(script_text, voice=v, rate=rate)
            sentences = []
            with open(output_mp3, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                        s = chunk["offset"] / 10_000_000
                        d = chunk["duration"] / 10_000_000
                        sentences.append({"text": chunk["text"], "start": s, "end": s + d})
            if output_mp3.exists() and output_mp3.stat().st_size > 10000:
                break
        except Exception as e:
            log(f"TTS retry with alternate voice {v}: {e}")
            await asyncio.sleep(1.0)
            continue

    # If sentence boundaries weren't returned, fallback to total audio duration estimation
    if not sentences and output_mp3.exists():
        import subprocess
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_mp3)]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_dur = float(res.stdout.strip() or 50.0)
        sentences = [{"text": script_text, "start": 0.0, "end": total_dur}]

    segments = []
    for idx, s in enumerate(sentences):
        words = create_word_timestamps_from_segment(s["text"], s["start"], s["end"])
        segments.append({
            "id": idx,
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
            "words": words
        })

    return segments


def generate_dynamic_tech_fact_with_groq(history: dict = None) -> dict:
    """
    Uses Groq Llama-3.3-70B (or fallback models) to dynamically invent a brand new,
    viral, mind-blowing tech story / developer tip short (~50-55s / ~140 words).
    Falls back to TECH_FACTS_DATABASE if Groq is unavailable.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        return None

    past_topics = []
    if history:
        past_topics = history.get("ai_past_topics", [])[-15:]

    system_prompt = (
        "You are an elite viral tech short-form storyteller (like Veritasium, Fireship, Kurzgesagt, and Cleo Abram).\n"
        "Your mission is to generate a deeply fascinating, mind-blowing tech story, programming secret, "
        "hardware mystery, cybersecurity breakdown, or computer science paradox for YouTube Shorts.\n"
        "CRITICAL RULES:\n"
        "1. Duration & Word Count: The script MUST be between 130 and 155 spoken words (~50 to 55 seconds spoken duration).\n"
        "2. Structure: 3-Act structure: (Act 1: Attention-grabbing hook/paradox) -> (Act 2: Deep technical explanation with real history/numbers/mechanisms) -> (Act 3: Mind-blowing takeaway & CTA).\n"
        "3. Spoken English: Write natural spoken English suitable for TTS narration. Do not use markdown, raw URLs, or code blocks in the script. Spell out numbers or acronyms where helpful.\n"
        "4. Tone: High-energy, authoritative, thrilling, and educational."
    )

    user_prompt = f"""
Generate a completely fresh, exciting Tech Short concept.
Avoid repeating any of these recent topics: {json.dumps(past_topics)}

JSON Output Schema:
{{
  "topic": "<Short 3-5 word header in ALL CAPS, e.g. 'HOW WI-FI SEES THROUGH WALLS'>",
  "badge": "<Short 2-3 word badge with emoji, e.g. 'TECH SECRET ⚡' or 'CYBERSECURITY 🛡️' or 'CS FACT 💻'>",
  "title": "<Viral YouTube Shorts title under 65 chars ending with #Shorts #TechShorts>",
  "voice": "<One of: 'en-US-ChristopherNeural', 'en-US-GuyNeural', 'en-US-EricNeural'>",
  "tags": ["techshorts", "technology", "programming", "coding", "developer", "shorts"],
  "script": "<The complete 130-155 word narration script (~50-55 seconds)>"
}}
"""
    log("🧠 Generating fresh viral tech topic with Groq Llama-3.3-70B...")
    try:
        from groq import Groq
        from main import parse_llm_json
        client = Groq(api_key=groq_api_key)
        for model_name in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            try:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.75,
                    max_tokens=800
                )
                raw_text = resp.choices[0].message.content
                fact_data = parse_llm_json(raw_text)
                
                if fact_data.get("script") and len(fact_data["script"].split()) >= 80:
                    log(f"✅ Groq generated dynamic topic: {fact_data.get('topic')} ({fact_data.get('badge')})")
                    return fact_data
            except Exception as me:
                log(f"⚠️ Groq model {model_name} notice: {me}")
                continue
    except Exception as ge:
        log(f"⚠️ Groq dynamic generation error: {ge}")
        
    return None


def render_tech_short(fact_index: int = None, dry_run: bool = False):
    log("=======================================================")
    log(" 🎬 Generating Tech Fact & Developer Tip Short (TTS Narration)")
    log("=======================================================")

    history = load_json(HISTORY_PATH, {"last_fact_index": -1, "processed_clips": [], "ai_past_topics": []})
    
    fact = None
    if fact_index is not None and 0 <= fact_index < len(TECH_FACTS_DATABASE):
        chosen_idx = fact_index
        fact = TECH_FACTS_DATABASE[chosen_idx]
        log(f"Selected Catalog Topic [{chosen_idx + 1}/{len(TECH_FACTS_DATABASE)}]: {fact['topic']} ({fact['badge']})")
    else:
        # Try dynamic AI generation with Groq first
        fact = generate_dynamic_tech_fact_with_groq(history)
        if fact:
            chosen_idx = -1
            past_list = history.get("ai_past_topics", [])
            past_list.append(fact["topic"])
            history["ai_past_topics"] = past_list[-30:]
        else:
            last_idx = history.get("last_fact_index", -1)
            chosen_idx = (last_idx + 1) % len(TECH_FACTS_DATABASE)
            fact = TECH_FACTS_DATABASE[chosen_idx]
            log(f"Selected Fallback Topic [{chosen_idx + 1}/{len(TECH_FACTS_DATABASE)}]: {fact['topic']} ({fact['badge']})")

    # Output directories
    home_downloads = Path.home() / "downloads" / "auto_clipper_output"
    phone_downloads = Path.home() / "storage" / "downloads" / "auto_clipper_output"
    home_downloads.mkdir(parents=True, exist_ok=True)
    try:
        phone_downloads.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    voice_mp3 = OUTPUT_DIR / "tech_narration.mp3"
    
    # 1. Synthesize Audio Narration with exact word timestamps
    segments = asyncio.run(synthesize_tech_audio(fact["script"], voice_mp3, voice=fact.get("voice", "en-US-ChristopherNeural")))
    start_sec = 0.0
    end_sec = segments[-1]["end"] + 0.6  # slight buffer at end
    duration = end_sec - start_sec

    log(f"✅ Tech voice synthesis complete ({duration:.1f}s narration across {len(segments)} segments).")

    # 2. Generate Neon Karaoke ASS Subtitles
    ass_path = OUTPUT_DIR / "tech_fact_subtitles.ass"
    generate_karaoke_ass_subtitles(segments, start_sec, end_sec, ass_path)

    # 3. Render 1080x1920 Short via Studio Visualizer
    out_video = OUTPUT_DIR / "tech_fact_short.mp4"
    log("🎨 Rendering 1080x1920 portrait Short with Studio Layout...")
    render_studio_visualizer_short(
        audio_full_path=voice_mp3,
        start_sec=start_sec,
        end_sec=end_sec,
        ass_subtitle_path=ass_path,
        output_final_path=out_video,
        speaker_badge=fact["badge"],
        transcript_segments=segments,
        speaker_gender="male",
        host_gender="male",
        topic_title=fact["topic"],
        video_reference_path=None
    )

    if out_video.exists() and out_video.stat().st_size > 50000:
        size_mb = out_video.stat().st_size / (1024 * 1024)
        log(f"✅ Tech Fact Short generated successfully! ({size_mb:.2f} MB)")

        dest_1 = home_downloads / "tech_fact_short.mp4"
        shutil.copy2(out_video, dest_1)
        log(f"📁 Copied to: {dest_1}")

        if phone_downloads.exists():
            try:
                dest_2 = phone_downloads / "tech_fact_short.mp4"
                shutil.copy2(out_video, dest_2)
                log(f"📱 Copied to Phone Downloads: {dest_2}")
            except Exception as e:
                log(f"Phone copy notice: {e}")

        # 4. Upload to YouTube if not dry-run
        if not dry_run:
            clip_info = {
                "viral_title": fact["title"],
                "speaker_badge": fact["badge"],
                "tags": fact.get("tags", ["techshorts", "coding", "techfacts", "shorts", "technology"])
            }
            podcast_dummy = {
                "name": "Tech Facts & Developer Tips",
                "default_tags": ["techshorts", "technology", "programming", "shorts", "developer", "coding"],
                "attribution_template": "Curated by @woosclips ⚡ Subscribe for daily tech revelations!"
            }
            log(f"🚀 Uploading Short to YouTube channel @woosclips: {fact['title']}...")
            upload_to_youtube(out_video, clip_info, podcast_dummy, original_video_url="https://youtube.com/@woosclips")

        # Update History Rotation
        history["last_fact_index"] = chosen_idx
        save_json(HISTORY_PATH, history)

        log("=======================================================")
        log(f" 🎉 Video Ready: {fact['title']}")
        log("=======================================================")
    else:
        log("❌ Video generation failed or output file is empty.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate and upload Tech Fact Shorts.")
    parser.add_argument("index", nargs="?", type=int, default=None, help="Index of fact to render")
    parser.add_argument("--dry-run", action="store_true", help="Generate video without uploading")
    parser.add_argument("--index", dest="fact_idx", type=int, default=None, help="Explicit fact index")
    args = parser.parse_args()

    chosen_index = args.fact_idx if args.fact_idx is not None else args.index
    render_tech_short(chosen_index, dry_run=args.dry_run)
