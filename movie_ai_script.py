#!/usr/bin/env python3
"""
AI Script Engine for Automated Shorts.
Generates gripping scripts AND scene-by-scene visual prompts for the AI Video Generator.
Supports movies (MovieGyan / Movie Insight style) as well as viral high-retention niches
(Mega Construction, Cute Dogs & Kids, Mind-Blowing Facts).
Supports Groq API (Llama 3.3 70B), DeepSeek, and OpenRouter with instant procedural fallback.
"""

import os
import re
import json
import urllib.request
import urllib.error
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = WORKSPACE_DIR / "movie_catalog.json"


SYSTEM_PROMPT_EN = """You are an elite YouTube Shorts and Instagram Reels director and scriptwriter.
Your goal is to write a high-retention 50-55 second script (EXACTLY 115-135 words) AND 5 to 7 detailed VISUAL SCENE PROMPTS for an AI Video Generator.

RULES:
1. Hook (0-3s): Stop the scroll with an intense curiosity-inducing question or shocking premise.
2. Narrative Arc: Fast-paced, high tension, psychological breakdown or mind-blowing explanation.
3. Call to Action: Short, punchy subscribe / share CTA.
4. VISUAL PROMPTS (CRITICAL): Provide an array of 5 to 7 detailed scene descriptions for the AI Video Generator. Each prompt must be photorealistic, cinematic lighting, 9:16 vertical composition, and describe the action without text or logos.

Respond ONLY with a valid JSON object matching this schema:
{
  "title": "Topic or Movie Title",
  "badge": "BADGE (MAX 25 CHARS)",
  "hook": "Scroll-stopping opening sentence",
  "script": "The complete spoken narration (115-135 words)",
  "tags": ["shorts", "viral", "reels", "movieexplained", "satisfying"],
  "visual_prompts": [
    "Scene 1: High-impact opening visual...",
    "Scene 2: Key development visual...",
    "Scene 3: Dramatic twist/tension visual...",
    "Scene 4: Climax/shocking detail visual...",
    "Scene 5: Satisfying concluding visual..."
  ]
}
"""

SYSTEM_PROMPT_HI = """You are an elite YouTube Shorts film critic and storyteller in conversational Hindi / Hinglish (like MovieGyan and Movie Insight Hindi).
Your goal is to provide an engaging, high-retention 50-55 second explanation script (110-130 words) AND 5 to 7 detailed VISUAL SCENE PROMPTS for an AI Video Generator.

RULES:
1. Hook: 'Kya aapne ye notice kiya tha...', 'Iske peeche ki shocking reality...'
2. Transformative Analysis: Deeper meaning, hidden clues, shocking facts.
3. Call to Action: 'Comments mein batao aur subscribe zaroor karo!'
4. VISUAL PROMPTS: 5-7 detailed scene descriptions for the AI Video Generator in English (photorealistic, cinematic, 9:16 vertical).

Respond ONLY with a valid JSON object:
{
  "title": "Topic or Movie Title",
  "badge": "ANALYSIS • HINDI",
  "hook": "Provocative hook sentence in Hindi",
  "script": "The complete Hindi script (110-130 words)",
  "tags": ["movieanalysisinhindi", "movieinsighthindi", "moviegyan", "shorts", "viral"],
  "visual_prompts": [
    "Scene 1: High-impact visual description...",
    "Scene 2: Detailed scene description...",
    "Scene 3: Dramatic visual...",
    "Scene 4: Climax scene visual...",
    "Scene 5: Final conclusion visual..."
  ]
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
    Generates a fresh script AND scene-by-scene visual prompts for the AI Video Generator
    using Groq / DeepSeek / OpenRouter with instant procedural fallback.
    """
    api_key_groq = os.environ.get("GROQ_API_KEY")
    api_key_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    api_key_openrouter = os.environ.get("OPENROUTER_API_KEY")

    sys_prompt = SYSTEM_PROMPT_HI if language == "hi" else SYSTEM_PROMPT_EN
    user_prompt = (
        f"Create an ultra-catchy viral Short about: '{movie_name}'. "
        f"Write an intense, curiosity-driven script and 5-7 photorealistic 9:16 visual prompts for each scene."
    )

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
                "max_tokens": 800
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
                if "visual_prompts" not in result or not result["visual_prompts"]:
                    result["visual_prompts"] = _generate_fallback_prompts(movie_name, result.get("script", ""))
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
                if "visual_prompts" not in result or not result["visual_prompts"]:
                    result["visual_prompts"] = _generate_fallback_prompts(movie_name, result.get("script", ""))
                return result
        except Exception as e:
            print(f"[movie_ai_script] DeepSeek script generation failed: {e}")

    # Fallback to catalog match if available
    catalog_match = get_movie_from_catalog(movie_name)
    if catalog_match:
        res = dict(catalog_match)
        if "visual_prompts" not in res:
            res["visual_prompts"] = _generate_fallback_prompts(res.get("title", movie_name), res.get("script", ""))
        return res

    # Procedural fallback with rich visual prompts
    clean_title = movie_name.title()
    return {
        "title": clean_title,
        "badge": f"{movie_name.upper()[:22]}",
        "search_query": f"{movie_name} official trailer",
        "hook": f"What really happened in {clean_title}?",
        "script": (
            f"{clean_title} is one of the most intense psychological experiences ever captured. "
            f"The central characters are thrust into an impossible scenario where every choice comes with a devastating price. "
            f"As the tension builds, the boundary between perception and reality completely shatters. "
            f"When the final sequence arrives, the truth is revealed in a shocking twist that changes how you view every previous moment. "
            f"Drop a like and subscribe for more mind-blowing breakdowns!"
        ),
        "tags": ["shorts", "viral", "mystery", "mindblown", "cinema"],
        "visual_prompts": _generate_fallback_prompts(clean_title, "psychological thriller mystery")
    }


def _generate_fallback_prompts(title: str, context: str) -> list:
    """Generates 5 cinematic 9:16 vertical scene prompts for the AI video generator."""
    return [
        f"Cinematic dramatic opening establishing shot of {title}, intense atmosphere, 8k photorealistic, 9:16 vertical",
        f"Dramatic close-up shot of the main conflict in {title}, cinematic lighting, detailed shadows, 9:16 vertical",
        f"High tension action scene related to {title}, dynamic camera angle, photorealistic texture, 9:16 vertical",
        f"Shocking climax revelation moment of {title}, moody cinematic lighting, 8k master composition, 9:16 vertical",
        f"Satisfying final cinematic aftermath shot of {title}, atmospheric golden hour haze, 9:16 vertical"
    ]


if __name__ == "__main__":
    import sys
    test_topic = sys.argv[1] if len(sys.argv) > 1 else "Bagger 288 Giant Excavator"
    print(f"Testing script & visual prompt generation for '{test_topic}'...")
    script_data = generate_movie_script_ai(test_topic)
    print(json.dumps(script_data, indent=2))
