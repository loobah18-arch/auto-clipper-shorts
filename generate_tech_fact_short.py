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
from datetime import datetime, timezone
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

FALLBACK_SERIES_DATABASE = [
    {
        "series_id": "underwater_internet",
        "topic": "HOW THE INTERNET ACTUALLY WORKS",
        "badge_base": "INTERNET SECRETS 🌐",
        "next_topic_teaser": "the secret Linux kernel process architecture and memory management subsystems",
        "total_parts": 3,
        "parts": [
            {
                "part_number": 1,
                "title": "How 99% of the Global Internet Flows Underwater • Part 1 🌊 #Shorts #TechShorts",
                "badge": "INTERNET SECRETS • PART 1",
                "voice": "en-US-ChristopherNeural",
                "tags": ["techshorts", "technology", "internet", "cables", "networking", "engineering", "shorts"],
                "script": (
                    "Did you know that over ninety nine percent of all international internet traffic does not travel through satellites or wireless cell towers, "
                    "but through massive fiber optic cables resting on the pitch black ocean floor? "
                    "Right now, there are over five hundred active subsea cables stretching eight hundred thousand miles across the globe, connecting every continent on Earth. "
                    "These glass lines are barely as thick as a garden hose, yet they transmit petabits of financial transactions, streaming videos, and cloud database queries every single millisecond. "
                    "Inside each cable, data travels as pulses of infrared laser light reflecting through ultra-pure silica glass cores thinner than a single human hair. "
                    "Because laser photons gradually lose energy and scatter as they travel thousands of miles through the ocean abyss, "
                    "engineers must run ten thousand volts of high-voltage direct current electricity directly alongside the glass fibers. "
                    "Every fifty miles along the seabed, this power feeds massive titanium optical repeaters containing erbium-doped laser amplifiers that excite the incoming photons and boost the light signals back to full strength! "
                    "Here is the terrifying reality: if an underwater earthquake triggers a submarine landslide, or a rogue ship anchor accidentally drags across the seabed, "
                    "entire nations and banking networks can instantly lose their internet connection in an instant! "
                    "In Part 2, we uncover why high-voltage seabed power lines attract deep-sea sharks and the extreme multi-layer armor engineered to stop them. "
                    "Like and subscribe so you don't miss Part 2!"
                )
            },
            {
                "part_number": 2,
                "title": "Why Sharks Attack Undersea Cables & Extreme Armor • Part 2 🦈 #Shorts",
                "badge": "INTERNET SECRETS • PART 2",
                "voice": "en-US-ChristopherNeural",
                "tags": ["techshorts", "technology", "internet", "cables", "engineering", "sharks", "shorts"],
                "script": (
                    "Welcome back to Part 2 of How the Global Internet Actually Works! "
                    "Why do deep-sea sharks aggressively attack and bite undersea fiber optic cables? "
                    "The answer lies in biological electroreception. Sharks possess specialized gel-filled sensory organs in their snouts known as the Ampullae of Lorenzini, "
                    "which detect microscopic electrical fields emitted by the muscle twitches of prey. "
                    "When thousands of volts of electricity pulse through the subsea cable's copper conductor to power optical repeaters, "
                    "it creates a faint electromagnetic field in the surrounding salt water. "
                    "Deep-sea predators mistake these electromagnetic pulses for wounded marine animals and bite down with thousands of pounds of pressure! "
                    "To prevent catastrophic blackouts, telecommunications engineers designed an extreme multi-layered armored fortress. "
                    "At the core lies water-resistant petroleum jelly surrounding the delicate glass strands. "
                    "This is encased inside a solid welded copper tube for power transmission, followed by a polycarbonate insulator, "
                    "a thick aluminum water barrier, high-tensile braided steel wire strands, and an outer double-layer of ballistic Kevlar and waterproof polyethylene tar. "
                    "In shallow coastal waters, specialized robotic plows even bury the armored cables several meters deep into the ocean bedrock. "
                    "In Part 3, we reveal how hundred-million-dollar autonomous robotic ships locate severed fibers miles underwater and laser weld them with micron precision. "
                    "Drop a like and subscribe for Part 3!"
                )
            },
            {
                "part_number": 3,
                "title": "How Robotic Ships Laser Weld Severed Cables in the Abyss • Part 3 🚢 #Shorts",
                "badge": "INTERNET SECRETS • PART 3",
                "voice": "en-US-ChristopherNeural",
                "tags": ["techshorts", "technology", "internet", "cables", "engineering", "deepsea", "shorts"],
                "script": (
                    "Welcome back to the finale of How the Global Internet Actually Works! "
                    "When a deep sea fiber optic cable snaps twenty thousand feet below the surface, how do engineers fix it without draining the entire ocean? "
                    "They deploy specialized hundred-million-dollar cable repair ships equipped with five-ton robotic submarines. "
                    "First, engineers on land shoot optical time-domain reflectometer light pulses down the broken line. "
                    "By measuring the exact microsecond the laser bounce returns, they pinpoint the break location within a few meters! "
                    "The ship navigates to the coordinate using satellite dynamic positioning, and launches a remote-operated submarine equipped with diamond-tipped saws and heavy hydraulic grapples. "
                    "The robot cuts the severed cable, seals the open end, and hoists it to the surface ship's dust-free cleanroom deck. "
                    "There, specialized optical technicians use microscopic fusion splicers to laser weld each individual glass strand together with sub-micron alignment. "
                    "While repairs take weeks at sea, global Border Gateway Protocol routers automatically reroute internet traffic across alternate trans-oceanic routes in milliseconds, "
                    "ensuring you never notice a single dropped connection! "
                    "And that is the invisible engineering keeping our planet connected. "
                    "In our next series, we'll be breaking down the secret Linux kernel process architecture and memory management subsystems, so stay tuned and make sure to like and subscribe!"
                )
            }
        ]
    },
    {
        "series_id": "linux_superpowers",
        "topic": "LINUX KERNEL & ARCHITECTURE SECRETS",
        "badge_base": "LINUX HACKS 🐧",
        "next_topic_teaser": "how modern cryptography, elliptic curves, and zero-knowledge proofs protect global privacy",
        "total_parts": 3,
        "parts": [
            {
                "part_number": 1,
                "title": "The Unix Philosophy & Stream Pipelines Explained • Part 1 🐧 #Shorts",
                "badge": "LINUX HACKS • PART 1",
                "voice": "en-US-GuyNeural",
                "tags": ["linux", "terminal", "bash", "developer", "codingtips", "sysadmin", "shorts"],
                "script": (
                    "Stop frantically pressing the up arrow key fifty times just to find a complex command you ran three weeks ago! "
                    "Every software developer and system administrator needs to understand the fundamental architecture of the Unix shell. "
                    "Inside any bash or zsh terminal, pressing Control plus R activates the recursive incremental history search engine. "
                    "The moment you type a single keyword like docker, git, or ssh, the kernel scans your history buffer and auto-completes the exact syntax with zero typos. "
                    "If you press Control plus R again, it cycles backwards through every past execution in memory. "
                    "Even more powerful is the Unix pipeline concept developed by Doug McIlroy in nineteen seventy-three. "
                    "Under the hood, the pipe operator creates an inter-process communication ring buffer in kernel memory. "
                    "When you chain commands like grep, awk, and sort together, Linux runs all processes concurrently in parallel, "
                    "streaming gigabytes of data between programs via standard input and standard output without ever writing temporary files to your physical disk! "
                    "In Part 2, we uncover how the Linux slash proc virtual filesystem lets you inspect live computer RAM and running CPU registers as plain text. "
                    "Drop a like and subscribe for Part 2!"
                )
            },
            {
                "part_number": 2,
                "title": "The Magic of Linux /proc & Live RAM Filesystem • Part 2 ⚡ #Shorts",
                "badge": "LINUX HACKS • PART 2",
                "voice": "en-US-GuyNeural",
                "tags": ["linux", "terminal", "sysadmin", "kernel", "coding", "developer", "shorts"],
                "script": (
                    "Welcome back to Part 2 of Linux Kernel Secrets! "
                    "Did you know that in Linux, literally everything in your operating system is represented as a plain text file? "
                    "If you open your terminal and navigate to slash proc on your machine, you are looking directly into your live computer RAM. "
                    "The proc directory does not consume a single byte of hard drive space. It is a dynamic pseudo-filesystem generated on the fly by the Linux kernel. "
                    "When you read slash proc slash cpuinfo or slash proc slash meminfo, the kernel intercepts the read system call and formats its internal hardware state into text in real time. "
                    "Every running program on your system gets its own dedicated folder matching its Process ID. "
                    "Inside slash proc slash PID, you can view the application's open file descriptors, active environment variables, CPU execution time, "
                    "and even inspect raw virtual memory pages via the mem file without opening a debugger! "
                    "This transparent abstraction is why Linux dominates ninety percent of global cloud infrastructure and supercomputers. "
                    "In Part 3, we reveal the terrifying Linux Out-Of-Memory Killer algorithm that secretly hunts down and terminates background apps when RAM runs out. "
                    "Hit like and subscribe for Part 3!"
                )
            },
            {
                "part_number": 3,
                "title": "The Terrifying Linux OOM Killer Algorithm • Part 3 💀 #Shorts",
                "badge": "LINUX HACKS • PART 3",
                "voice": "en-US-GuyNeural",
                "tags": ["linux", "kernel", "sysadmin", "coding", "developer", "containers", "shorts"],
                "script": (
                    "Welcome back to the finale of Linux Kernel Secrets! "
                    "What happens when your server runs completely out of physical RAM? "
                    "A typical operating system would freeze, lock up, or crash with a blue screen. "
                    "Linux handles this situation with a brutal kernel algorithm known as the Out Of Memory Killer! "
                    "Because Linux implements memory overcommitting to maximize server efficiency, processes can allocate more virtual memory than physically exists. "
                    "When real physical RAM and swap space become exhausted, the kernel triggers an emergency page allocation failure. "
                    "The OOM Killer instantly pauses system execution and scans the task list of every active process. "
                    "It calculates a mathematical badness score based on the process's physical memory footprint, runtime duration, and its oom score adjustment setting. "
                    "Once the worst offender is calculated, the kernel sends an uncatchable, unignorable SIGKILL signal nine, "
                    "instantly annihilating the offending application and freeing up its memory pages to protect the core operating system! "
                    "This ruthless architectural efficiency laid the groundwork for modern container runtimes like Docker and Kubernetes. "
                    "In our next series, we'll be exploring how modern cryptography, elliptic curves, and zero-knowledge proofs protect global privacy, so stay tuned and make sure to like and subscribe!"
                )
            }
        ]
    },
    {
        "series_id": "modern_cryptography",
        "topic": "THE CRYPTOGRAPHY REVOLUTION",
        "badge_base": "CYBERSECURITY 🛡️",
        "next_topic_teaser": "why zero point one plus zero point two is not zero point three and the space rockets it exploded",
        "total_parts": 3,
        "parts": [
            {
                "part_number": 1,
                "title": "How SSH Logs You In Without Sending Your Password • Part 1 🔐 #Shorts",
                "badge": "CYBERSECURITY • PART 1",
                "voice": "en-US-ChristopherNeural",
                "tags": ["cybersecurity", "ssh", "encryption", "infosec", "linux", "networking", "shorts"],
                "script": (
                    "How do SSH keys log you into top secret cloud servers across the globe without ever transmitting your password across the internet? "
                    "It all comes down to an ingenious mathematical concept known as asymmetric public key cryptography. "
                    "When you generate an SSH key pair on your computer, your machine uses a cryptographically secure random number generator to create two mathematically linked keys: a public key and a private key. "
                    "You freely share the public key and store it on any remote server in the world, while your private key remains encrypted and heavily guarded on your local machine. "
                    "When you initiate a connection, the server never asks for your secret passphrase. "
                    "Instead, the server generates a random string of numbers called a cryptographic nonce, encrypts it using your public key, and sends the scrambled puzzle back to your computer. "
                    "Only the corresponding private key on your machine possesses the mathematical trapdoor required to decrypt that challenge! "
                    "Your computer decrypts the number, signs it with your private key, and sends back the proof. "
                    "The server verifies the signature and opens access instantly, without a single sensitive credential ever traversing the network! "
                    "In Part 2, we uncover why even a billion supercomputers running for trillions of years cannot crack elliptic curve cryptography. "
                    "Drop a like and subscribe for Part 2!"
                )
            },
            {
                "part_number": 2,
                "title": "Why Supercomputers Cannot Crack Elliptic Curves • Part 2 🛡️ #Shorts",
                "badge": "CYBERSECURITY • PART 2",
                "voice": "en-US-ChristopherNeural",
                "tags": ["cybersecurity", "encryption", "math", "infosec", "techfacts", "shorts"],
                "script": (
                    "Welcome back to Part 2 of The Cryptography Revolution! "
                    "Once a cryptographic challenge is locked with your public key, why can't a hacker intercept the transmission and solve it? "
                    "Because modern security relies on Elliptic Curve Cryptography and the mathematical hardness of the discrete logarithm problem. "
                    "An elliptic curve is defined by the algebraic formula y squared equals x cubed plus ax plus b, plotted over a finite prime field. "
                    "When you multiply a point on this geometric curve by a secret scalar number, the resulting point bounces unpredictably across the grid. "
                    "Calculating the final point in the forward direction takes less than a millisecond on a smartphone processor. "
                    "However, if an attacker is given the starting point and the ending point, finding the original secret multiplier requires checking every single possibility one by one! "
                    "For a standard 256-bit elliptic curve key, there are more possible combinations than the estimated number of atoms in the entire observable universe! "
                    "Even if you networked every supercomputer on Earth running continuously until the death of our sun, they could not brute force your private key. "
                    "In Part 3, we reveal Zero-Knowledge Proofs and how computers mathematically prove secret knowledge without revealing a single letter. "
                    "Like and subscribe for Part 3!"
                )
            },
            {
                "part_number": 3,
                "title": "Zero-Knowledge Proofs: Proving Secrets Without Sharing Them • Part 3 🤯 #Shorts",
                "badge": "CYBERSECURITY • PART 3",
                "voice": "en-US-ChristopherNeural",
                "tags": ["cybersecurity", "zkp", "encryption", "privacy", "math", "blockchain", "shorts"],
                "script": (
                    "Welcome back to the finale of The Cryptography Revolution! "
                    "Imagine proving you know the secret combination to a bank vault without ever typing the code, speaking the numbers, or giving away a single clue. "
                    "This mathematical breakthrough is known as a Zero-Knowledge Proof! "
                    "First conceptualized by Shafi Goldwasser, Silvio Micali, and Charles Rackoff in nineteen eighty-five, zero-knowledge protocols allow a prover to mathematically demonstrate the truth of a statement to a verifier without conveying any additional information. "
                    "Modern implementations like zk-SNARKs convert computational statements into quadratic arithmetic programs and polynomial commitments. "
                    "By sampling random evaluation points on these polynomials, a computer can prove that a complex calculation was executed correctly with ninety-nine point nine nine nine percent mathematical certainty, "
                    "while keeping the underlying data completely confidential! "
                    "Zero-knowledge technology is currently transforming digital identity, private electronic voting, and scaling decentralized computing networks worldwide. "
                    "And that is how advanced mathematics protects human freedom in the digital age. "
                    "In our next series, we'll be discussing why zero point one plus zero point two is not zero point three and the space rockets it exploded, so stay tuned and make sure to like and subscribe!"
                )
            }
        ]
    },
    {
        "series_id": "floating_point_math",
        "topic": "FLOATING POINT SECRETS & DISASTERS",
        "badge_base": "CS DEEP DIVE 💻",
        "next_topic_teaser": "the crazy true history of the first computer bug and the moth that changed computing",
        "total_parts": 3,
        "parts": [
            {
                "part_number": 1,
                "title": "Why 0.1 + 0.2 Is NOT 0.3 In Computer Science • Part 1 🤯 #Shorts",
                "badge": "CS DEEP DIVE • PART 1",
                "voice": "en-US-EricNeural",
                "tags": ["programming", "coding", "computerscience", "math", "python", "javascript", "shorts"],
                "script": (
                    "If you open your browser console, Python interpreter, or C compiler right now and evaluate zero point one plus zero point two, "
                    "you will not receive zero point three! "
                    "Instead, your machine will print zero point three zero zero zero zero zero zero zero zero zero zero zero zero zero zero four! "
                    "This is not a software bug or a compiler flaw. It is a fundamental law of binary microprocessors governed by the IEEE seven fifty-four floating-point standard. "
                    "Humans count in base ten decimals because we have ten fingers. In base ten, fractions like one half and one fifth terminate cleanly. "
                    "However, computers count strictly in base two binary bits: ones and zeroes. "
                    "In binary arithmetic, numbers are represented as sums of powers of two, such as one half, one fourth, one eighth, and one sixteenth. "
                    "When you attempt to write the decimal number zero point one in binary, it produces an infinite repeating fraction: zero point zero zero zero one one zero zero one one recurring infinitely! "
                    "Because physical silicon registers have a finite number of bits, the processor must truncate and round the infinite binary string, "
                    "introducing a microscopic rounding discrepancy into every calculation! "
                    "In Part 2, we explore how this exact microscopic rounding error caused a five hundred million dollar space rocket to explode thirty-seven seconds after liftoff. "
                    "Hit like and subscribe for Part 2!"
                )
            },
            {
                "part_number": 2,
                "title": "The $500M Ariane 5 Floating Point Rocket Explosion • Part 2 🚀 #Shorts",
                "badge": "CS DEEP DIVE • PART 2",
                "voice": "en-US-EricNeural",
                "tags": ["programming", "engineering", "space", "rockets", "computerscience", "disasters", "shorts"],
                "script": (
                    "Welcome back to Part 2 of Floating Point Disasters! "
                    "On June fourth, nineteen ninety-six, the European Space Agency launched the maiden flight of the Ariane Five rocket, carrying four cutting-edge scientific satellites valued at half a billion dollars. "
                    "Just thirty-seven seconds after liftoff, the rocket suddenly veered sharply off course, flipped ninety degrees at supersonic speed, and disintegrated in a colossal fireball! "
                    "The post-flight inquiry uncovered one of the most infamous software bugs in human history. "
                    "The rocket's inertial reference guidance system reused legacy software from the older, slower Ariane Four rocket. "
                    "The algorithm attempted to convert a sixty-four bit floating-point variable measuring horizontal velocity into a sixteen-bit signed integer. "
                    "Because the Ariane Five accelerated much faster than its predecessor, the horizontal velocity value exceeded thirty-two thousand seven hundred sixty-seven, the maximum limit of a sixteen-bit integer! "
                    "The hardware suffered an integer overflow exception, causing the flight computer to crash and send full diagnostic error data directly to the rocket's aerodynamic thrusters, "
                    "which interpreted the error codes as flight commands and ripped the vehicle apart! "
                    "In Part 3, we reveal how a zero point thirty-four second clock drift caused a tragic military missile defense failure. "
                    "Like and subscribe for Part 3!"
                )
            },
            {
                "part_number": 3,
                "title": "The 0.34 Second Clock Drift Disaster • Part 3 ⚠️ #Shorts",
                "badge": "CS DEEP DIVE • PART 3",
                "voice": "en-US-EricNeural",
                "tags": ["programming", "computerscience", "engineering", "history", "military", "shorts"],
                "script": (
                    "Welcome back to the finale of Floating Point Disasters! "
                    "In nineteen ninety-one, during the Gulf War, an incoming Scud missile struck a military barracks in Dhahran, Saudi Arabia, after a Patriot missile defense battery failed to intercept it. "
                    "The government investigation revealed that the catastrophic failure was caused by a minute floating-point rounding error in the system's internal tracking clock. "
                    "The radar computer measured time in tenths of a second by multiplying the integer clock ticks by the value zero point one using a twenty-four bit fixed-point register. "
                    "Because zero point one cannot be represented with exact precision in binary, the calculation lost approximately zero point zero zero zero zero zero zero zero nine five seconds per tick. "
                    "Under normal conditions with frequent reboots, this tiny error went unnoticed. "
                    "However, the battery had been running continuously for over one hundred hours without a restart! "
                    "Over one hundred hours, that microscopic rounding error accumulated into a total clock drift of zero point thirty-four seconds! "
                    "At hypersonic speeds, a zero point thirty-four second timing discrepancy meant the radar looked more than six hundred meters away from the actual incoming missile! "
                    "And that is why mathematical precision and numeric type safety are matters of life and death in computer engineering. "
                    "In our next series, we'll be discussing the crazy true history of the very first computer bug, so stay tuned and make sure to like and subscribe!"
                )
            }
        ]
    },
    {
        "series_id": "first_computer_bug",
        "topic": "THE HISTORY & ANATOMY OF COMPUTER BUGS",
        "badge_base": "TECH HISTORY 📜",
        "next_topic_teaser": "how ninety nine percent of the global internet travels under the ocean",
        "total_parts": 3,
        "parts": [
            {
                "part_number": 1,
                "title": "The Crazy True Story of the First Computer Bug • Part 1 🪲 #TechShorts",
                "badge": "TECH HISTORY • PART 1",
                "voice": "en-US-GuyNeural",
                "tags": ["techhistory", "programming", "debugging", "computerscience", "gracehopper", "shorts"],
                "script": (
                    "Have you ever wondered why software engineers and developers across the world call fixing broken code debugging? "
                    "The origin story is completely literal and involves a real two-inch moth trapped inside an electromechanical supercomputer! "
                    "In nineteen forty-seven, computing pioneer Grace Hopper and her engineering team were operating the Harvard Mark Two computer for the United States Navy. "
                    "The Mark Two was a massive sixteen-ton machine composed of thousands of mechanical relays, vacuum tubes, and rotating shafts that filled an entire room. "
                    "Suddenly, the computer began producing severe calculation errors and halted operations. "
                    "The engineering team spent hours manually testing electrical circuits across hundreds of relay panels until they reached panel F. "
                    "Wedged tightly between the electrical contact points of relay number seventy was an actual dead moth that had flown in through an open window and short-circuited the electrical signal! "
                    "The team carefully extracted the insect with tweezers, taped it into their official Navy logbook, and penned the famous line: First actual case of bug being found. "
                    "In Part 2, we uncover how a satellite software glitch nearly triggered a full-scale nuclear world war during the Cold War. "
                    "Like and subscribe so you don't miss Part 2!"
                )
            },
            {
                "part_number": 2,
                "title": "The Satellite Bug That Nearly Started WWIII • Part 2 🛰️ #Shorts",
                "badge": "TECH HISTORY • PART 2",
                "voice": "en-US-GuyNeural",
                "tags": ["techhistory", "history", "computerscience", "bugs", "coldwar", "shorts"],
                "script": (
                    "Welcome back to Part 2 of Computer Bug History! "
                    "On September twenty-sixth, nineteen eighty-three, Soviet early-warning computers inside the secret Serpukhov-15 bunker suddenly flashed flashing red emergency sirens. "
                    "The automated Oko satellite detection software reported that the United States had launched five nuclear intercontinental ballistic missiles directly at Soviet territory! "
                    "Military protocol and chain of command dictated an immediate retaliatory nuclear strike that would have triggered World War Three within minutes. "
                    "However, duty officer Stanislav Petrov noticed that the satellite system was relatively new and reasoned that a genuine first-strike attack would involve hundreds of simultaneous missiles, not just five. "
                    "Petrov bravely defied military protocol, declared the alerts a false alarm, and held back the nuclear retaliation. "
                    "Subsequent software audits proved he was right: the satellite's orbital tracking software contained a catastrophic false-positive edge case! "
                    "When the high-altitude Molniya satellite reached a specific orbital alignment, sunlight glinting off high-altitude storm clouds was misinterpreted by the optical sensor algorithms as thermal rocket plumes! "
                    "In Part 3, we reveal the Year 2038 Unix Epoch Time Bomb that threatens millions of legacy servers worldwide. "
                    "Drop a like and subscribe for Part 3!"
                )
            },
            {
                "part_number": 3,
                "title": "The Year 2038 Unix Epoch Time Bomb • Part 3 ⏳ #Shorts",
                "badge": "TECH HISTORY • PART 3",
                "voice": "en-US-GuyNeural",
                "tags": ["techhistory", "y2k38", "linux", "programming", "clocks", "shorts"],
                "script": (
                    "Welcome back to the finale of Computer Bug History! "
                    "On January nineteenth, twenty thirty-eight, millions of 32-bit operating systems, banking mainframes, and embedded industrial controllers will face a digital apocalypse known as the Year 2038 Problem! "
                    "Unix-based operating systems store time as the number of elapsed seconds since the Unix Epoch: midnight UTC on January first, nineteen seventy. "
                    "In 32-bit systems, this timestamp is stored as a signed thirty-two bit integer. "
                    "At exactly three fourteen AM and seven seconds UTC on January nineteenth, twenty thirty-eight, the counter will reach its maximum positive limit of two billion one hundred forty-seven million four hundred eighty-three thousand six hundred forty-seven seconds! "
                    "On the very next second, the integer will overflow into the negative sign bit, instantly resetting the system clock to December thirteenth, nineteen zero-one! "
                    "Unpatched servers, industrial power grids, medical devices, and aviation controllers will experience catastrophic scheduling logic failures. "
                    "Global software engineers are currently undertaking massive infrastructure migrations to sixty-four bit time integers, "
                    "which will extend the clock's overflow safety threshold by another two hundred ninety-two billion years! "
                    "In our next series, we'll be exploring how ninety nine percent of the global internet travels under the ocean, so stay tuned and make sure to like and subscribe!"
                )
            }
        ]
    }
]

# Backward compatibility alias
TECH_FACTS_DATABASE = [p for s in FALLBACK_SERIES_DATABASE for p in s["parts"]]


def validate_series_quality(series_data: dict) -> bool:
    """
    Strictly validates that generated series is in-depth, thorough, and high quality.
    Each part must be a comprehensive 2.5-3 minute mini-documentary (~240+ words per part, no upper cap),
    with natural episodic framing and clear CTAs.
    """
    if not series_data or not isinstance(series_data, dict):
        return False
    parts = series_data.get("parts", [])
    if len(parts) < 3:
        log("⚠️ Quality check failed: Series has fewer than 3 parts.")
        return False

    for p in parts:
        script = p.get("script", "").strip()
        word_count = len(script.split())
        
        # 1. Word count check: Must be at least 240 words for genuine 2.5-3 min depth
        if word_count < 240:
            log(f"⚠️ Quality check failed: Part {p.get('part_number')} script too short ({word_count} words < 240 words). Needs full technical depth.")
            return False
            
        # 2. Call-to-action check
        script_lower = script.lower()
        if not ("like" in script_lower or "subscribe" in script_lower):
            log(f"⚠️ Quality check failed: Part {p.get('part_number')} missing CTA.")
            return False

    return True


def query_opencode_deepseek(system_prompt: str, user_prompt: str, max_tokens: int = 6500, temperature: float = 0.75) -> dict:
    """
    Priority 1: Queries OpenCode DeepSeek v4 Flash (CLI or HTTP API) with strict quality validation.
    """
    import subprocess
    import shutil
    from main import parse_llm_json

    # 1. Try local OpenCode CLI if available
    opencode_bin = shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    if opencode_bin and Path(opencode_bin).exists():
        try:
            log("🧠 Querying OpenCode DeepSeek v4 Flash via OpenCode CLI (Priority 1)...")
            full_prompt = (
                f"{system_prompt}\n\n"
                f"{user_prompt}\n\n"
                "Return ONLY a single valid raw JSON object. No conversational preamble, no markdown wrappers."
            )
            res = subprocess.run(
                [opencode_bin, "run", "-m", "opencode/deepseek-v4-flash-free", full_prompt],
                capture_output=True,
                text=True,
                timeout=120
            )
            if res.returncode == 0 and res.stdout:
                parsed = parse_llm_json(res.stdout)
                if validate_series_quality(parsed):
                    log(f"✅ OpenCode DeepSeek v4 Flash successfully generated in-depth 3-min series: '{parsed.get('topic')}' ({len(parsed['parts'])} Parts)")
                    return parsed
        except Exception as oe:
            log(f"⚠️ OpenCode CLI execution notice: {oe}")

    # 2. Try HTTP endpoints with OPENCODE_API_KEY / DEEPSEEK_API_KEY
    opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not opencode_key:
        return None

    import urllib.request

    base_url = os.environ.get("OPENCODE_BASE_URL")
    endpoints = []
    
    if base_url:
        endpoints.append((base_url, ["opencode/deepseek-v4-flash-free", "deepseek-v4-flash", "deepseek-chat"]))
    
    endpoints.extend([
        ("https://api.opencode.ai/v1/chat/completions", ["opencode/deepseek-v4-flash-free", "deepseek-v4-flash", "deepseek-ai/deepseek-v4-flash"]),
        ("https://api.deepseek.com/chat/completions", ["deepseek-chat", "deepseek-reasoner"]),
        ("https://openrouter.ai/api/v1/chat/completions", ["deepseek/deepseek-v4-flash", "openai/gpt-oss-20b:free", "openai/gpt-oss-20b", "deepseek/deepseek-chat", "deepseek/deepseek-r1"])
    ])

    for url, model_list in endpoints:
        for model in model_list:
            try:
                host_label = url.split("/")[2]
                log(f"🧠 Querying OpenCode DeepSeek v4 Flash (Priority 1: {model} @ {host_label})...")
                payload = json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }).encode("utf-8")

                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {opencode_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/auto-clipper-shorts",
                        "X-Title": "Auto Clipper Shorts"
                    }
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_text = data["choices"][0]["message"]["content"]
                    parsed = parse_llm_json(raw_text)
                    if validate_series_quality(parsed):
                        log(f"✅ OpenCode DeepSeek v4 Flash successfully generated in-depth 3-min series: '{parsed.get('topic')}' ({len(parsed['parts'])} Parts)")
                        return parsed
            except Exception as e:
                log(f"⚠️ OpenCode DeepSeek notice ({model}): {e}")
                continue
    return None


def query_groq_fallback(system_prompt: str, user_prompt: str) -> dict:
    """
    Priority 2 & 3 Fallback: Groq Llama-3.3-70B and GPT OSS 20B with strict quality validation.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        return None

    try:
        from groq import Groq
        from main import parse_llm_json
        client = Groq(api_key=groq_api_key)
        
        # Priority 2: Llama-3.3-70B | Priority 3: GPT OSS 20B
        for model_name in ["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "openai/gpt-oss-20b:free", "gpt-oss-20b"]:
            try:
                log(f"🦙 Querying Groq Fallback ({model_name})...")
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.75,
                    max_tokens=6500
                )
                raw_text = resp.choices[0].message.content
                series_data = parse_llm_json(raw_text)
                if validate_series_quality(series_data):
                    log(f"✅ Groq ({model_name}) successfully generated in-depth 3-min series: '{series_data.get('topic')}' ({len(series_data['parts'])} Parts)")
                    return series_data
            except Exception as me:
                log(f"⚠️ Groq model {model_name} notice: {me}")
                continue
    except Exception as ge:
        log(f"⚠️ Groq dynamic series generation error: {ge}")

    return None


def fetch_live_tech_milestones_and_research() -> list:
    """
    Fetches real-world research breakthroughs and historical milestones from public APIs (arXiv & Wikipedia On-This-Day)
    to provide dynamic, authentic real-world inspiration for AI technical series generation.
    """
    inspirations = []
    # 1. Wikipedia 'On This Day' in Tech & Science (zero-key public REST API)
    try:
        now = datetime.now(timezone.utc)
        url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/selected/{now.month:02d}/{now.day:02d}"
        req = urllib.request.Request(url, headers={"User-Agent": "AutoClipperResearch/1.0 (https://github.com/loobah18-arch)"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
            for ev in data.get("selected", [])[:8]:
                txt = ev.get("text", "")
                yr = ev.get("year", "")
                if any(w in txt.lower() for w in ["computer", "space", "launch", "satellite", "physics", "internet", "robot", "radio", "telescope", "flight", "discovery", "system", "engine", "network", "electric", "code"]):
                    inspirations.append(f"Historical Tech Milestone ({yr}): {txt}")
    except Exception:
        pass

    # 2. arXiv Computer Science, Quantum & Cryptography Research Papers (zero-key open API)
    try:
        import xml.etree.ElementTree as ET
        url = "http://export.arxiv.org/api/query?search_query=cat:cs.CR+OR+cat:quant-ph+OR+cat:cs.AR&start=0&max_results=3"
        req = urllib.request.Request(url, headers={"User-Agent": "AutoClipperResearch/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            xml_data = r.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns)[:3]:
                title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
                summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")[:120]
                inspirations.append(f"arXiv Research Paper: '{title}' - {summary}")
    except Exception:
        pass

    return inspirations


def generate_dynamic_series_with_groq(history: dict = None) -> dict:
    """
    Generates an in-depth, thorough 3-to-4 part mini-documentary series (2.5-3 minutes per video, no word cap) on ONE technical topic:
    1. OpenCode DeepSeek v4 Flash (Priority 1)
    2. Groq Llama-3.3-70B (Priority 2 Fallback)
    3. GPT OSS 20B (Priority 3 Fallback)
    4. Database Catalog (Safety Net)
    """
    past_topics = []
    if history:
        past_topics = history.get("ai_past_topics", [])[-15:]

    # Live public research & milestone context
    live_context = fetch_live_tech_milestones_and_research()
    research_context_str = ""
    if live_context:
        research_context_str = f"\nOptional Real-World Research Context & Historical Milestones:\n" + "\n".join(f"- {c}" for c in live_context) + "\n"

    system_prompt = (
        "You are an elite, world-class technical documentary storyteller (like Veritasium, Fireship, and Kurzgesagt).\n"
        "Your mission is to create a deeply technical, high-retention 3-part or 4-part masterclass mini-documentary series on ONE fascinating engineering, programming, or computer science subject.\n"
        "STRICT CONTENT & ANTI-SLOP RULES (CRITICAL):\n"
        "1. FULL 2.5 TO 3-MINUTE DURATION PER VIDEO: Every part MUST be an exhaustive, long-form explanation between 320 and 420 words (~2.5 to 3 minutes of spoken audio). NO low word caps, no rushing, and no summarizing!\n"
        "2. NO META-TALK OR INTRO FILLER: NEVER say 'in this video we will explore', 'in this series we delve', 'from X to Y we cover it all', or 'let us talk about'. NEVER tease a topic without explaining it immediately. Dive STRAIGHT into the real technical mechanisms, numbers, hardware, or code from word one!\n"
        "3. EXPLAIN THE REAL MECHANISMS IN FULL DETAIL: Every part must explain the actual technical science: exact math, algorithms, protocols, memory registers, voltages, frequencies, physical constraints, historical disasters, or dollar losses.\n"
        "4. NARRATIVE PROGRESSION ACROSS 3-4 PARTS:\n"
        "   - Part 1: Concrete Paradox & Physical/Algorithmic Foundation (Hook + Deep explanation of why this is counter-intuitive + Full technical mechanics + Cliffhanger for Part 2 + 'Like and subscribe for Part 2!').\n"
        "   - Part 2: Under-The-Hood Architecture & Real-World Engineering (Exact step-by-step mechanism, algorithms, physical hardware, memory subsystems + Cliffhanger for Part 3 + 'Like and subscribe for Part 3!').\n"
        "   - Part 3 (or Finale): Real-World Catastrophe, Legendary Glitch, or Modern Breakthrough + Master Takeaway + Next Series Teaser + 'Like and subscribe to stay tuned!'.\n"
        "5. Spoken English: Pure natural spoken text for TTS. No markdown symbols, no raw URLs."
    )

    user_prompt = f"""
Generate an in-depth 3-Part Tech Masterclass Series with full 2.5-3 minute depth per video (~350-420 words per part).{research_context_str}
Avoid repeating any of these recent topics: {json.dumps(past_topics)}

JSON Output Schema:
{{
  "topic": "<Core topic name in ALL CAPS, e.g. 'HOW WI-FI SEES THROUGH WALLS'>",
  "badge_base": "<Short 2-3 word badge with emoji, e.g. 'WI-FI RADAR 📡' or 'QUANTUM SECRETS 💻'>",
  "next_topic_teaser": "<Short teaser of what the next upcoming series will explore, e.g. 'the secret Linux kernel memory management algorithms'>",
  "total_parts": 3,
  "parts": [
    {{
      "part_number": 1,
      "title": "<Catchy title under 65 chars including '• Part 1' and #Shorts #TechShorts>",
      "badge": "<badge_base> • PART 1",
      "voice": "en-US-ChristopherNeural",
      "tags": ["techshorts", "technology", "part1", "shorts", "developer"],
      "script": "<EXACTLY 320-420 words directly explaining the core technical concept in full depth and ending with cliffhanger for Part 2 + 'Like and subscribe for Part 2!'>"
    }},
    {{
      "part_number": 2,
      "title": "<Catchy title under 65 chars including '• Part 2' and #Shorts #TechShorts>",
      "badge": "<badge_base> • PART 2",
      "voice": "en-US-ChristopherNeural",
      "tags": ["techshorts", "technology", "part2", "shorts", "developer"],
      "script": "<EXACTLY 320-420 words explaining the deeper inner architecture in full depth and ending with cliffhanger for Part 3 + 'Like and subscribe for Part 3!'>"
    }},
    {{
      "part_number": 3,
      "title": "<Catchy title under 65 chars including '• Part 3' and #Shorts #TechShorts>",
      "badge": "<badge_base> • PART 3",
      "voice": "en-US-ChristopherNeural",
      "tags": ["techshorts", "technology", "part3", "shorts", "developer"],
      "script": "<EXACTLY 320-420 words delivering the master resolution in full depth, giving the next-topic teaser, and ending with 'stay tuned and make sure to like and subscribe!'>"
    }}
  ]
}}
"""
    # Priority 1: OpenCode DeepSeek v4 Flash
    series = query_opencode_deepseek(system_prompt, user_prompt)
    if series:
        return series

    # Priority 2 & 3: Groq Llama-3.3-70B & 8B Fallback
    log("⚠️ OpenCode DeepSeek limit reached or unavailable. Falling back to Groq Llama-3.3-70B (Priority 2)...")
    series = query_groq_fallback(system_prompt, user_prompt)
    if series:
        return series

    return None


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
        for model_name in ["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "openai/gpt-oss-20b:free", "gpt-oss-20b"]:
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
    log(" 🎬 Generating Multi-Part Tech Story Short (TTS Narration)")
    log("=======================================================")

    history = load_json(HISTORY_PATH, {
        "last_series_index": -1,
        "active_series": None,
        "completed_series": [],
        "ai_past_topics": []
    })

    active_series = history.get("active_series")
    
    # Check if we need to start a fresh series:
    needs_new_series = False
    if not active_series:
        needs_new_series = True
    elif not active_series.get("parts"):
        needs_new_series = True
    elif active_series.get("current_part_index", 0) >= len(active_series.get("parts", [])):
        needs_new_series = True

    if fact_index is not None and 0 <= fact_index < len(TECH_FACTS_DATABASE):
        # Override mode: render specific isolated fact
        fact = TECH_FACTS_DATABASE[fact_index]
        part_info = fact
        series_topic = fact.get("topic", "TECH FACT")
        log(f"Selected Explicit Topic [{fact_index + 1}/{len(TECH_FACTS_DATABASE)}]: {fact['topic']} ({fact['badge']})")
    else:
        if needs_new_series:
            log("🔄 No active series in progress. Starting a brand new deep-dive tech series...")
            # Try Groq first for dynamic series
            new_series = generate_dynamic_series_with_groq(history)
            if new_series and new_series.get("parts"):
                active_series = new_series
                active_series["current_part_index"] = 0
                past_list = history.get("ai_past_topics", [])
                past_list.append(active_series["topic"])
                history["ai_past_topics"] = past_list[-30:]
            else:
                last_s_idx = history.get("last_series_index", -1)
                next_s_idx = (last_s_idx + 1) % len(FALLBACK_SERIES_DATABASE)
                active_series = json.loads(json.dumps(FALLBACK_SERIES_DATABASE[next_s_idx]))
                active_series["current_part_index"] = 0
                history["last_series_index"] = next_s_idx
                log(f"Selected Fallback Series [{next_s_idx + 1}/{len(FALLBACK_SERIES_DATABASE)}]: {active_series['topic']}")
            
            history["active_series"] = active_series

        # Retrieve current part to render
        part_idx = active_series.get("current_part_index", 0)
        parts_list = active_series.get("parts", [])
        if part_idx >= len(parts_list):
            part_idx = 0
            active_series["current_part_index"] = 0
            
        part_info = parts_list[part_idx]
        total_p = len(parts_list)
        series_topic = active_series.get("topic", "TECH DEEP DIVE")
        log(f"🎬 Series: '{series_topic}' ➔ Rendering Part {part_info.get('part_number', part_idx + 1)} of {total_p}...")

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
    segments = asyncio.run(synthesize_tech_audio(part_info["script"], voice_mp3, voice=part_info.get("voice", "en-US-ChristopherNeural")))
    start_sec = 0.0
    # Add comfortable 1.8s post-speech buffer so YouTube loop playback never clips the final words
    last_word_end = segments[-1]["end"] if segments else 10.0
    end_sec = last_word_end + 1.8
    duration = end_sec - start_sec

    log(f"✅ Tech voice synthesis complete ({duration:.1f}s narration across {len(segments)} segments).")

    # 2. Generate Neon Karaoke ASS Subtitles (Portrait 9:16 and Landscape 16:9)
    ass_path = OUTPUT_DIR / "tech_fact_subtitles.ass"
    generate_karaoke_ass_subtitles(segments, start_sec, end_sec, ass_path, is_landscape=False)

    ass_landscape_path = OUTPUT_DIR / "tech_fact_landscape_subtitles.ass"
    generate_karaoke_ass_subtitles(segments, start_sec, end_sec, ass_landscape_path, is_landscape=True)

    # 3. Render 1080x1920 Short and 1920x1080 Normal Video via Studio Visualizer
    out_video = OUTPUT_DIR / "tech_fact_short.mp4"
    out_landscape_video = OUTPUT_DIR / "tech_fact_video_169.mp4"

    log("🎨 Rendering 1080x1920 portrait Short with Studio Layout...")
    render_studio_visualizer_short(
        audio_full_path=voice_mp3,
        start_sec=start_sec,
        end_sec=end_sec,
        ass_subtitle_path=ass_path,
        output_final_path=out_video,
        speaker_badge=part_info.get("badge", "TECH FACT 💡"),
        transcript_segments=segments,
        speaker_gender="male",
        host_gender="male",
        topic_title=series_topic,
        video_reference_path=None,
        is_landscape=False
    )

    log("🎨 Rendering 1920x1080 widescreen Normal Video with Studio Layout...")
    render_studio_visualizer_short(
        audio_full_path=voice_mp3,
        start_sec=start_sec,
        end_sec=end_sec,
        ass_subtitle_path=ass_landscape_path,
        output_final_path=out_landscape_video,
        speaker_badge=part_info.get("badge", "TECH FACT 💡"),
        transcript_segments=segments,
        speaker_gender="male",
        host_gender="male",
        topic_title=f"{series_topic} - {part_info.get('title', '')}",
        video_reference_path=None,
        is_landscape=True
    )

    if out_video.exists() and out_video.stat().st_size > 50000:
        size_mb = out_video.stat().st_size / (1024 * 1024)
        log(f"✅ Tech Short generated successfully! ({size_mb:.2f} MB)")

        dest_1 = home_downloads / "tech_fact_short.mp4"
        shutil.copy2(out_video, dest_1)
        log(f"📁 Copied Short to: {dest_1}")

        if out_landscape_video.exists() and out_landscape_video.stat().st_size > 50000:
            dest_1_ls = home_downloads / "tech_fact_video_169.mp4"
            shutil.copy2(out_landscape_video, dest_1_ls)
            log(f"📁 Copied Normal Video to: {dest_1_ls}")

        if phone_downloads.exists():
            try:
                dest_2 = phone_downloads / "tech_fact_short.mp4"
                shutil.copy2(out_video, dest_2)
                log(f"📱 Copied Short to Phone Downloads: {dest_2}")
                if out_landscape_video.exists():
                    dest_2_ls = phone_downloads / "tech_fact_video_169.mp4"
                    shutil.copy2(out_landscape_video, dest_2_ls)
                    log(f"📱 Copied Normal Video to Phone Downloads: {dest_2_ls}")
            except Exception as e:
                log(f"Phone copy notice: {e}")

        # 4. Upload to YouTube if not dry-run (Dual Upload: Short + Normal Video)
        if not dry_run:
            clip_info = {
                "viral_title": part_info["title"],
                "speaker_badge": part_info.get("badge", "TECH FACT 💡"),
                "tags": part_info.get("tags", ["techshorts", "coding", "techfacts", "shorts", "technology"])
            }
            podcast_dummy = {
                "name": "Tech Facts & Developer Tips",
                "default_tags": ["techshorts", "technology", "programming", "shorts", "developer", "coding"],
                "attribution_template": "Curated by @woosclips ⚡ Subscribe for daily tech revelations!"
            }
            log(f"🚀 Uploading Short (9:16) to YouTube channel @woosclips: {part_info['title']}...")
            upload_to_youtube(out_video, clip_info, podcast_dummy, original_video_url="https://youtube.com/@woosclips", is_short=True)
            
            if out_landscape_video.exists() and out_landscape_video.stat().st_size > 50000:
                log(f"🚀 Uploading Normal Video (16:9) to YouTube channel @woosclips: {part_info['title']}...")
                upload_to_youtube(out_landscape_video, clip_info, podcast_dummy, original_video_url="https://youtube.com/@woosclips", is_short=False)

        # Clean up temporary subtitle files
        for p in [ass_path, ass_landscape_path]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        # 5. Advance Series State Rotation
        if fact_index is None and active_series:
            next_part = active_series.get("current_part_index", 0) + 1
            if next_part >= len(active_series.get("parts", [])):
                log(f"🎉 Series '{active_series.get('topic')}' concluded ({len(active_series['parts'])}/{len(active_series['parts'])} Parts complete)!")
                completed = history.get("completed_series", [])
                completed.append({
                    "topic": active_series.get("topic"),
                    "total_parts": len(active_series.get("parts", [])),
                    "completed_at": datetime.now(timezone.utc).isoformat()
                })
                history["completed_series"] = completed[-20:]
                history["active_series"] = None  # Ready to start next topic on next run!
            else:
                active_series["current_part_index"] = next_part
                history["active_series"] = active_series
                log(f"📌 Series state saved: next run will render Part {next_part + 1} of {len(active_series['parts'])}.")

        save_json(HISTORY_PATH, history)

        log("=======================================================")
        log(f" 🎉 Videos Ready (Short + Normal Video): {part_info['title']}")
        log("=======================================================")
    else:
        log("❌ Video generation failed or output file is empty.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate and upload Multi-Part Tech Story Shorts.")
    parser.add_argument("index", nargs="?", type=int, default=None, help="Explicit fact index (optional override)")
    parser.add_argument("--dry-run", action="store_true", help="Generate video without uploading")
    parser.add_argument("--index", dest="fact_idx", type=int, default=None, help="Explicit fact index")
    args = parser.parse_args()

    chosen_index = args.fact_idx if args.fact_idx is not None else args.index
    render_tech_short(chosen_index, dry_run=args.dry_run)
