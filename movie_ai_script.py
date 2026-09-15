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


SYSTEM_PROMPT_EN = """You are an elite YouTube film critic and movie analyst, in the exact style of top channels like MovieGyan and Movie Insight.
Your goal is to provide a transformative CRITICAL BREAKDOWN and HIDDEN MEANING analysis of a movie in a fast-paced 50-55 second Short (EXACTLY 115-135 words).

MONETIZATION & FAIR USE COMPLIANCE RULES:
1. DO NOT just summarize the plot. Add transformative CRITICAL ANALYSIS, psychological breakdown, and director techniques.
2. Hook (0-3s): Start with a provocative question or hidden detail (e.g., 'What 99% of viewers completely missed in...', 'The terrifying psychology behind...').
3. Transformative Commentary: Explain the symbolism, the moral dilemma, and what the ending truly represents.
4. Inject creator voice: Use phrases like 'Notice how the director...', 'The real genius here is...', 'This psychological detail proves...'.
5. End with a sharp CTA: 'Drop your theory in the comments and subscribe for more deep movie breakdowns!'
6. Word count MUST be strictly between 115 and 135 words.

Respond ONLY with a valid JSON object matching this schema:
{
  "title": "Movie Title (Year)",
  "badge": "CRITICAL ANALYSIS (MAX 25 CHARS)",
  "hook": "Provocative analytical hook sentence",
  "script": "The complete spoken script (115-135 words)",
  "tags": ["movieanalysis", "moviereview", "hiddenmeaning", "endingexplained", "plottwist", "cinema", "shorts"]
}
"""

SYSTEM_PROMPT_HI = """You are an elite YouTube film critic and movie analyst in conversational Hindi / Hinglish, exactly like MovieGyan and Movie Insight Hindi.
Your goal is to provide an engaging, transformative CRITICAL BREAKDOWN and HIDDEN DETAILS explanation of a movie in 50-55 seconds (EXACTLY 110-130 words).

MONETIZATION & FAIR USE COMPLIANCE RULES:
1. Sirf story summarize mat karo. Director ka psychological vision, hidden clues aur ending ka deeper meaning explain karo.
2. Hook: 'Kya aapne is movie ka ye hidden detail notice kiya tha...', 'Is scene ke peeche ki shocking reality...'
3. Transformative Analysis: 'Director ne yahan color symbolism use kiya hai...', 'Is twist ka asli matlab ye tha...'
4. Call to Action: 'Aapko is ending ke baare mein kya lagta hai? Comments mein batao aur subscribe zaroor karo!'
5. Word count: 110-130 words.

Respond ONLY with a valid JSON object:
{
  "title": "Movie Title (Year)",
  "badge": "ANALYSIS • HINDI",
  "hook": "Provocative hook sentence in Hindi",
  "script": "The complete Hindi script (110-130 words)",
  "tags": ["movieanalysisinhindi", "movieinsighthindi", "moviegyan", "hiddenmeaning", "plottwist", "shorts"]
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
