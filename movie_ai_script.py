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
AUTO_CATALOG_PATH = WORKSPACE_DIR / "movie_catalog_auto.json"


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


def _read_catalog_file(path: Path) -> list:
    """Read one catalog file, returning an empty list on any problem."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[movie_ai_script] Failed to read {path.name}: {e}")
        return []
    movies = data.get("movies") if isinstance(data, dict) else None
    return [m for m in movies if isinstance(m, dict)] if isinstance(movies, list) else []


def load_catalog() -> dict:
    """Loads the merged catalog: hand-curated entries plus auto-generated ones.

    Curated entries always win. Auto-generated entries (from a Google Drive
    folder) are additive only, so a hand-tuned series such as The Avengers keeps
    its curated parts and scripts and is never replaced by a generated one.
    """
    curated = _read_catalog_file(CATALOG_PATH)
    generated = _read_catalog_file(AUTO_CATALOG_PATH)

    merged: dict[str, dict] = {}
    for movie in generated:
        key = str(movie.get("id") or "")
        if key:
            merged[key] = movie
    for movie in curated:          # curated is applied last, so it wins
        key = str(movie.get("id") or "")
        if key:
            merged[key] = movie

    return {"movies": list(merged.values())}


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


GROQ_MODELS = [
    "openai/gpt-oss-120b",   # production tier; replaced the retired llama-3.3-70b-versatile
    "qwen/qwen3.6-27b",      # documented alternative
    "llama-3.3-70b-versatile",  # retired 2026-08-16; kept last for enterprise keys
]

OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct",
    "anthropic/claude-3.5-sonnet",
]


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST JSON and return the parsed 'choices' message, or raise."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def generate_movie_script_ai(movie_name: str, language: str = "en") -> dict:
    """
    Generates a fresh MovieGyan-style movie explanation script using Groq /
    OpenRouter / DeepSeek, falling back to a generic template only if every
    provider fails.

    The returned dict always carries ``script_source``: "groq", "openrouter" or
    "deepseek" for real AI output, or "template" for the built-in fallback.
    Callers that need genuine narration must check this.
    """
    api_key_groq = os.environ.get("GROQ_API_KEY")
    api_key_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    api_key_openrouter = os.environ.get("OPENROUTER_API_KEY")

    sys_prompt = SYSTEM_PROMPT_HI if language == "hi" else SYSTEM_PROMPT_EN
    user_prompt = f"Create a viral movie explanation Short for the film: '{movie_name}'. Highlight the premise, psychological tension, and the shocking plot twist or ending."

    def finish(result: dict, source: str) -> dict:
        result["search_query"] = f"{result.get('title', movie_name)} official trailer"
        result["script_source"] = source
        return result

    groq_models = [
        m.strip() for m in os.environ.get("GROQ_MODEL", "").split(",") if m.strip()
    ] or GROQ_MODELS

    if api_key_groq:
        for model in groq_models:
            try:
                result = _post_json(
                    "https://api.groq.com/openai/v1/chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.7,
                        "max_tokens": 600,
                    },
                    {"Authorization": f"Bearer {api_key_groq}", "User-Agent": "MovieShortsBot/1.0"},
                    25,
                )
                return finish(result, "groq")
            except Exception as e:
                print(f"[movie_ai_script] Groq model {model} failed: {e}")

    if api_key_openrouter:
        for model in OPENROUTER_MODELS:
            try:
                result = _post_json(
                    "https://openrouter.ai/api/v1/chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.7,
                        "max_tokens": 600,
                    },
                    {"Authorization": f"Bearer {api_key_openrouter}"},
                    35,
                )
                return finish(result, "openrouter")
            except Exception as e:
                print(f"[movie_ai_script] OpenRouter model {model} failed: {e}")

    if api_key_deepseek:
        try:
            result = _post_json(
                "https://api.deepseek.com/chat/completions",
                {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7,
                },
                {"Authorization": f"Bearer {api_key_deepseek}"},
                30,
            )
            return finish(result, "deepseek")
        except Exception as e:
            print(f"[movie_ai_script] DeepSeek script generation failed: {e}")

    catalog_match = get_movie_from_catalog(movie_name)
    if catalog_match:
        found = dict(catalog_match)
        found["script_source"] = "catalog"
        return found

    # Generic template. Flagged so callers can refuse to publish filler.
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
        "tags": ["movieexplained", "movierecap", "moviegyan", "plottwist", "cinema", "shorts"],
        "script_source": "template",
    }


if __name__ == "__main__":
    import sys
    test_movie = sys.argv[1] if len(sys.argv) > 1 else "The Platform"
    print(f"Testing script generation for '{test_movie}'...")
    script_data = generate_movie_script_ai(test_movie)
    print(json.dumps(script_data, indent=2))
