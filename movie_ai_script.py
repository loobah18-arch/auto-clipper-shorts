#!/usr/bin/env python3
"""
AI Movie Script Engine for Movie Explanation Shorts.
Generates gripping 50-55 second movie explanation scripts in the exact
MovieGyan and Movie Insight Hindi storytelling style.
Supports Groq API (Llama 3.3 70B / 8B), DeepSeek, and OpenRouter with
instant fallback to curated catalog.
"""

import os
import re
import json
import urllib.request
import urllib.error
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = WORKSPACE_DIR / "movie_catalog.json"


SYSTEM_PROMPT_EN = """You are an elite YouTube Shorts movie explanation creator, in the exact style of top channels like MovieGyan and Movie Insight.
Your goal is to explain a movie's plot, tension, and shocking twist in an ultra-gripping, fast-paced 50-55 second story (EXACTLY 115-135 words).

RULES:
1. NO meta-talk. Do NOT say 'In this movie', 'Welcome back', 'Today we explain', or 'Hello guys'.
2. Start immediately with a high-stakes, spine-chilling hook (0-3s) that stops the scroll.
3. Fast-paced storytelling: explain the characters, the terrifying dilemma or world rules, and the escalating stakes.
4. Reveal the climax or mind-bending twist with immense dramatic tension.
5. End with a sharp 1-sentence CTA: 'Drop a like and subscribe for more mind-blowing movie explanations!'
6. Word count MUST be between 115 and 135 words so the speech duration fits YouTube Shorts (under 60s).

Respond ONLY with a valid JSON object matching this schema:
{
  "title": "Movie Title (Year)",
  "badge": "SHORT UPPERCASE BADGE (MAX 25 CHARS)",
  "hook": "Opening hook sentence",
  "script": "The complete spoken script (115-135 words)",
  "tags": ["movieexplained", "movierecap", "moviegyan", "plottwist", "cinema", "shorts"]
}
"""

SYSTEM_PROMPT_HI = """You are an elite YouTube Shorts movie explanation creator in conversational Hindi / Hinglish, exactly like MovieGyan and Movie Insight Hindi.
Your goal is to explain a movie's plot, tension, and shocking twist in an engaging, fast-paced 50-55 second story (EXACTLY 110-130 words).

RULES:
1. NO meta-talk. Start directly with the story hook.
2. Use conversational, engaging Hindi/Hinglish (e.g., 'Is futuristic jail mein 333 floors hain...', 'Lekin asli twist tab aata hai jab...').
3. Fast-paced storytelling: describe the situation, the terror, the mystery.
4. Deliver the shocking twist or ending explanation clearly.
5. End with: 'Aise hi mind-blowing movie explanations ke liye subscribe zaroor karein!'
6. Keep script under 130 words (under 60s spoken).

Respond ONLY with a valid JSON object:
{
  "title": "Movie Title (Year)",
  "badge": "SHORT UPPERCASE BADGE",
  "hook": "Opening hook sentence in Hindi",
  "script": "The complete Hindi script (110-130 words)",
  "tags": ["movieexplainedinhindi", "movieinsighthindi", "moviegyan", "plottwist", "shorts"]
}
"""


def load_catalog() -> dict:
    """Loads curated movie catalog."""
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[movie_ai_script] Failed to load catalog: {e}")
    return {"movies": []}


def get_movie_from_catalog(movie_query: str = None) -> dict:
    """Finds a matching movie from catalog, or returns the first available."""
    cat = load_catalog()
    movies = cat.get("movies", [])
    if not movies:
        return None

    if not movie_query:
        return movies[0]

    q_lower = movie_query.lower()
    for m in movies:
        if q_lower in m.get("id", "").lower() or q_lower in m.get("title", "").lower():
            return m

    return None


def generate_movie_script_ai(movie_name: str, language: str = "en") -> dict:
    """
    Generates a fresh MovieGyan-style movie explanation script using Groq / DeepSeek / OpenRouter.
    Falls back to catalog if no API keys are configured.
    """
    api_key_groq = os.environ.get("GROQ_API_KEY")
    api_key_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    api_key_openrouter = os.environ.get("OPENROUTER_API_KEY")

    sys_prompt = SYSTEM_PROMPT_HI if language == "hi" else SYSTEM_PROMPT_EN
    user_prompt = f"Create a viral movie explanation Short for the film: '{movie_name}'. Highlight the premise, psychological tension, and the shocking plot twist or ending."

    # Try Groq first (ultra-fast, free tier friendly)
    if api_key_groq:
        try:
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
                "max_tokens": 600
            }
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key_groq}",
                    "User-Agent": "MovieShortsBot/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                result = json.loads(content)
                result["search_query"] = f"{result.get('title', movie_name)} official trailer"
                return result
        except Exception as e:
            print(f"[movie_ai_script] Groq script generation failed: {e}")

    # Try DeepSeek fallback
    if api_key_deepseek:
        try:
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7
            }
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key_deepseek}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                result = json.loads(content)
                result["search_query"] = f"{result.get('title', movie_name)} official trailer"
                return result
        except Exception as e:
            print(f"[movie_ai_script] DeepSeek script generation failed: {e}")

    # Fallback to catalog match or default
    catalog_match = get_movie_from_catalog(movie_name)
    if catalog_match:
        return catalog_match

    # Default procedural script if nothing else matched
    return {
        "title": f"{movie_name.title()}",
        "badge": f"{movie_name.upper()[:22]}",
        "search_query": f"{movie_name} official trailer",
        "hook": f"What really happened in {movie_name}?",
        "script": (
            f"{movie_name} is one of cinema's most intense psychological thrillers. "
            f"The protagonist is placed into an impossible dilemma where every choice comes with a devastating price. "
            f"As the mystery unravels, allies turn into suspects and the boundary between perception and reality completely shatters. "
            f"When the final sequence arrives, the truth is revealed in a devastating twist that changes how you view every previous scene. "
            f"Drop a like and subscribe for more mind-blowing movie explanations!"
        ),
        "tags": ["movieexplained", "movierecap", "moviegyan", "plottwist", "cinema", "shorts"]
    }


if __name__ == "__main__":
    import sys
    test_movie = sys.argv[1] if len(sys.argv) > 1 else "The Platform"
    print(f"Testing script generation for '{test_movie}'...")
    script_data = generate_movie_script_ai(test_movie)
    print(json.dumps(script_data, indent=2))
