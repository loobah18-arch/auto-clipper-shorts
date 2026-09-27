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
import time
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


def _extract_json(content: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("model reply contained no JSON object")


_JSON_ONLY_SUFFIX = (
    "\n\nCRITICAL: Your entire reply must be one raw JSON object and nothing else. "
    "No preamble, no explanation, no markdown code fences. Start the reply with { and end it with }."
)


class ModelReplyError(Exception):
    """Raised when a provider replies but not in the expected JSON shape."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0

# Once a provider/model answers successfully, reuse it for the remaining calls
# instead of re-probing every fallback for each part.
_PREFERRED: dict[str, str] = {}


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST JSON and return the parsed reply object.

    Handles three provider quirks:
    - some hosted models reject ``response_format`` with a 400, retried without it
    - 429/5xx are retried with exponential backoff so bulk generation survives
      free-tier rate limits
    - reasoning models can return empty content, which is reported explicitly
    """
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in (400, 422) and "response_format" in payload:
                print(f"[movie_ai_script] {payload.get('model')} rejected response_format; retrying without it")
                payload = {k: v for k, v in payload.items() if k != "response_format"}
                payload["messages"] = [
                    {**m, "content": m["content"] + _JSON_ONLY_SUFFIX} for m in payload["messages"]
                ]
                last_error = None
                continue
            if error.code == 429 or error.code >= 500:
                delay = _BACKOFF_BASE ** (attempt + 1)
                print(f"[movie_ai_script] {payload.get('model')} HTTP {error.code}; backing off {delay:.0f}s")
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            delay = _BACKOFF_BASE ** (attempt + 1)
            print(f"[movie_ai_script] {payload.get('model')} network error ({error}); retrying in {delay:.0f}s")
            time.sleep(delay)
            continue
    else:
        raise last_error if last_error else RuntimeError("request failed with no response")

    message = data["choices"][0]["message"]
    content = message.get("content") or ""
    if not content.strip():
        reasoning = message.get("reasoning") or ""
        print(
            f"[movie_ai_script] {payload.get('model')} returned empty content "
            f"(finish_reason={data['choices'][0].get('finish_reason')}, "
            f"reasoning_chars={len(reasoning)})."
        )
        raise ModelReplyError("empty completion", raw=reasoning)
    try:
        return _extract_json(content)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"[movie_ai_script] {payload.get('model')} reply was not JSON: {str(content)[:180]!r}")
        raise ModelReplyError(str(error), raw=content) from error


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

    def from_prose(raw: str, source: str) -> dict:
        """Use a non-JSON AI reply as narration rather than throwing it away."""
        text = re.sub(r"```[a-zA-Z]*", " ", raw or "").strip()
        text = re.sub(r"\s+", " ", text)
        if len(text.split()) < 25:
            raise ModelReplyError("AI reply too short to use as narration", raw=raw)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        return finish({
            "title": movie_name,
            "badge": movie_name.upper()[:22],
            "hook": sentences[0] if sentences else f"What really happened in {movie_name}?",
            "script": text,
            "tags": ["movieexplained", "movierecap", "moviegyan", "plottwist", "cinema", "shorts"],
        }, source)

    groq_models = [
        m.strip() for m in os.environ.get("GROQ_MODEL", "").split(",") if m.strip()
    ] or GROQ_MODELS
    preferred = _PREFERRED.get("groq")
    if preferred in groq_models:
        groq_models = [preferred] + [m for m in groq_models if m != preferred]

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
                        "max_tokens": 4000,
                    },
                    {"Authorization": f"Bearer {api_key_groq}", "User-Agent": "MovieShortsBot/1.0"},
                    25,
                )
                _PREFERRED["groq"] = model
                return finish(result, "groq")
            except ModelReplyError as e:
                try:
                    return from_prose(e.raw, "groq")
                except ModelReplyError:
                    print(f"[movie_ai_script] Groq model {model} gave unusable prose")
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
                        "max_tokens": 4000,
                    },
                    {"Authorization": f"Bearer {api_key_openrouter}"},
                    35,
                )
                _PREFERRED["openrouter"] = model
                return finish(result, "openrouter")
            except ModelReplyError as e:
                try:
                    return from_prose(e.raw, "openrouter")
                except ModelReplyError:
                    print(f"[movie_ai_script] OpenRouter model {model} gave unusable prose")
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
        except ModelReplyError as e:
            try:
                return from_prose(e.raw, "deepseek")
            except ModelReplyError:
                print("[movie_ai_script] DeepSeek gave unusable prose")
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
