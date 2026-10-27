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


SYSTEM_PROMPT_EN = """You are an elite, captivating storyteller for Woo's Clips channel narrating high-retention movie and series explanations in the exact signature style of Bhaloo Ji and RecapKun (@recapkun).

CRITICAL RULES FOR BHALOO JI & RECAPKUN EXPLANATION STYLE:
1. IMMERSIVE NARRATIVE STORYTELLING (ZERO ROBOTIC TONE):
   - Pure high-energy, dramatic storytelling with intense narrative momentum.
   - Use natural spoken contractions everywhere ('they're', 'can't', 'he's', 'didn't', 'it's', 'won't').
   - NEVER sound like a movie critic, an essay, or a detached plot synopsis.
   - BANNED PHRASES: Never say "The film begins with...", "The opening scenes...", "The narrative introduces...", "Throughout the movie...", "The story follows...", "Notice how...", "In this movie review...".

2. 0-3 SECOND EXPLOSIVE HOOK:
   - Grab the viewer instantly in the very first sentence with an impossible dilemma, lethal shock, or extreme situation:
     e.g., "Imagine waking up trapped in an airtight titanium vault with only ten minutes of oxygen left..."
     e.g., "He had zero superpowers, but he just outsmarted an ancient god in front of thousands of people..."

3. DEEP LORE & TACTICAL CONFLICT EXPLANATION:
   - Deeply explain character motivations, powers/abilities, tactical decisions, hidden plot twists, and high-stakes psychological drama. Make the audience truly understand the brilliance of what's happening.

4. NATURAL BREATH PAUSES & PROSODY:
   - Use em-dashes (' — ') and ellipses ('...') strategically so the voiceover has breathing room, punchy rhythm, and dramatic build-up:
     e.g., "He reaches for the trigger — but right behind him... someone whispers his real name."

5. ESCALATING ACTION & CRISP BEATS:
   - Keep sentences punchy, vivid, and kinetic. Every line must reveal a new escalation or high-stakes consequence.
   - Build maximum suspense toward the climax.

6. RAZOR-SHARP CLIFFHANGER:
   - End with an irresistible, nail-biting cliffhanger that forces the viewer to demand the next part:
     e.g., "Wait until you see the impossible choice he makes in Part 2! Like and follow so you don't miss it!"

7. WORD COUNT & TIMING (STRICT DURATION CEILING):
   - 110 to 135 words maximum. Pacing calibrated strictly for a 50-55 second Short under the YouTube Shorts 60s copyright safe limit. Never exceed 140 words.

Respond ONLY with a valid JSON object matching this schema:
{
  "title": "Movie Title (Year)",
  "badge": "MOVIE RECAP",
  "hook": "Intense opening hook",
  "script": "The complete spoken human narrative recap (110-135 words)",
  "tags": ["movierecap", "movieexplained", "endingexplained", "storyexplained", "plottwist", "cinema"]
}
"""

SYSTEM_PROMPT_HI = """You are an elite YouTube movie storyteller and recap creator in conversational, energetic Hindi / Hinglish, in the exact viral style of Movrecap, MovieGyan and Movies Insight Hindi.
Your goal is to narrate a thrilling, detailed MOVIE EXPLANATION & STORY RECAP (120-150 words).

RULES:
1. PURE STORYTELLING: Bilkul natural aur bolne wale andaaz mein kahani sunaao. Kirdaaron ke saath kya hota hai, unka sangharsh aur plot ke twists ko vistaar se samjhao. Koi boring film analysis, camera techniques ya director ka naam mat lo.
2. NATURAL PAUSES & CADENCE: Em-dashes (' — ') aur ellipses ('...') ka use karo taaki aawaaz mein natural breath pauses aur suspense aaye ('Wait — dekho yahan kya hota hai...', 'Aur fir? Sannata.').
3. Hook: Shuruat aisi ho ki viewer scroll na kar sake (e.g., 'Is ladki ne sab kuch kho diya tha — lekin ek ajeeb vasiyat ne sab badal diya...').
4. Detailed Story Beats: Har scene ke mod aur suspense ko theek se explain karo taaki viewer kahani mein poori tarah doob jaye.
5. Cliffhanger / Ending: Ending par zabardast suspense chhodo taaki viewer agla part dekhe: 'Ab kya ye bach payenge? Dekhiye agle part mein! Like aur subscribe zaroor karna!'
6. Word count: 120-150 words (strictly under 55 seconds).

Respond ONLY with a valid JSON object:
{
  "title": "Movie Title (Year)",
  "badge": "MOVIE RECAP • HINDI",
  "hook": "Suspenseful Hindi opening hook",
  "script": "The complete spoken Hindi script (120-150 words)",
  "tags": ["movierecap", "movieexplainedinhindi", "movieinsighthindi", "storyexplained", "plottwist"]
}
"""

CRITIC_ANALYSIS_MARKERS = [
    "what 99% of viewers",
    "notice how",
    "camera lingers",
    "visual metaphor",
    "fractured psyche",
    "the real genius",
    "critical analysis",
    "symbolizing fractured",
    "psychological tension",
    "echoing frankenstein",
    "cautionary tale about playing god",
    "deep movie breakdowns",
    "drop your theory in the comments",
    "subtle color shift",
    "framing mirrors",
    "study of inevitability",
    "the opening scenes",
    "the film begins",
    "the movie begins",
    "the movie opens",
    "the film opens",
    "the narrative follows",
    "the plot follows",
    "the story begins",
]


def is_stale_critic_analysis_script(script: str) -> bool:
    """Detect if a script is an academic film critique / analysis essay rather than a pure story recap."""
    if not script:
        return False
    s = script.lower()
    return any(marker in s for marker in CRITIC_ANALYSIS_MARKERS)



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
    # Free tier first: these work on a zero-credit account and all return valid
    # JSON in object mode. Verified live. Free-tier budget without credits is
    # ~20 requests/minute and ~50/day, which the 6-parts-per-run ingest cap fits.
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    # Paid fallbacks, used once the account has credits.
    "meta-llama/llama-3.3-70b-instruct",
    "anthropic/claude-sonnet-4",
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

    # Some provider errors return 200 with no choices (free-tier throttling, or a
    # moderation/empty completion envelope). Treat that as a soft failure so the
    # next model is tried instead of raising KeyError.
    if not isinstance(data.get("choices"), list) or not data["choices"]:
        detail = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        raise ModelReplyError(
            f"provider returned no choices{f': {detail}' if detail else ''}"
        )

    message = data["choices"][0].get("message") or {}
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


class ScriptDurationExceededError(Exception):
    """Raised when script narration cannot fit within the maximum duration ceiling."""


def calculate_script_word_budget(
    target_duration: float = 58.0,
    format: str = "shorts",
    expected_speech_rate_wps: float = 2.4,
    intro_allowance: float = 2.5,
    outro_allowance: float = 3.5,
) -> tuple[int, int]:
    """Calculate the target word count range (min_words, max_words) for a script.

    For Shorts, bounds the script strictly so narration comfortably finishes under 58s.
    """
    if format == "long_form":
        nominal_dur = max(65.0, target_duration)
        mid = int(nominal_dur * 2.3)
        return (int(mid * 0.85), int(mid * 1.15))

    usable_dur = max(10.0, target_duration - intro_allowance - outro_allowance)
    nominal_words = int(usable_dur * expected_speech_rate_wps)
    min_words = max(25, int(nominal_words * 0.85))
    max_words = max(min_words + 15, int(nominal_words * 1.05))
    return (min_words, max_words)


def shorten_movie_script(
    script_text: str,
    target_words: int = 120,
    movie_name: str = "",
) -> str:
    """Concise rewriting of a script to meet word budget while preserving causal story progression.

    Preserves:
    1. Opening hook sentence (crucial for retention)
    2. Essential plot progression and causal relationships
    3. Cliffhanger / outro closing sentence
    Removes redundant explanations and filler adjectives.
    """
    words = script_text.split()
    if len(words) <= target_words:
        return script_text

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_text) if s.strip()]
    if len(sentences) <= 2:
        truncated = " ".join(words[:target_words])
        if not truncated.endswith((".", "!", "?")):
            truncated += "..."
        return truncated

    hook_sentence = sentences[0]
    outro_sentence = sentences[-1]
    middle_sentences = sentences[1:-1]

    hook_len = len(hook_sentence.split())
    outro_len = len(outro_sentence.split())
    available_middle_words = max(15, target_words - hook_len - outro_len)

    causal_markers = {
        "but", "suddenly", "because", "when", "however", "then", "until",
        "discovers", "realizes", "reveals", "trapped", "escapes", "kills",
        "secrets", "truth", "lies", "confronts", "decision"
    }
    scored_middle = []
    for s in middle_sentences:
        s_words = set(s.lower().split())
        score = len(s_words.intersection(causal_markers))
        scored_middle.append((score, s))

    selected_middle: list[str] = []
    accum_words = 0
    for _, s in scored_middle:
        s_len = len(s.split())
        if accum_words + s_len <= available_middle_words:
            selected_middle.append(s)
            accum_words += s_len

    if not selected_middle and middle_sentences:
        mid = middle_sentences[0]
        selected_middle.append(" ".join(mid.split()[:available_middle_words]))

    condensed = " ".join([hook_sentence] + selected_middle + [outro_sentence])
    return condensed


def generate_movie_script_ai(
    movie_name: str,
    part: int | None = None,
    total_parts: int | None = None,
    language: str = "en",
    target_max_duration: float = 58.0,
    format: str = "shorts",
) -> dict:
    """
    Generates a fresh Movrecap-style movie recap script using Groq /
    OpenRouter / DeepSeek, falling back to a generic template only if every
    provider fails.

    Calibrated with target duration to avoid exceeding Shorts duration boundaries.
    """
    api_key_groq = os.environ.get("GROQ_API_KEY")
    api_key_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    api_key_openrouter = os.environ.get("OPENROUTER_API_KEY")

    min_w, max_w = calculate_script_word_budget(target_duration=target_max_duration, format=format)
    budget_instruction = f"Strictly {min_w} to {max_w} words (calibrated for {target_max_duration:.0f}s {format})."

    sys_prompt = SYSTEM_PROMPT_HI if language == "hi" else SYSTEM_PROMPT_EN
    if part is not None:
        part_str = f"Part {part}{f' of {total_parts}' if total_parts else ''}"
        user_prompt = (
            f"Write a gripping in-universe story recap for '{movie_name}' ({part_str}) in the exact viral style of Movrecap. "
            f"LENGTH REQUIREMENT: {budget_instruction} "
            "Start IMMEDIATELY with the character's direct crisis, impossible dilemma, or intense high-stakes situation. "
            "NEVER say 'The opening scenes...', 'The movie begins...', or talk about the film/director/cinematography. "
            "Tell the plot chronologically through character actions, struggles, dialogue, and suspense that specifically match this act of the story. "
            "End with an urgent cliffhanger driving viewers to like and follow for the next part."
        )
    else:
        user_prompt = (
            f"Write a gripping in-universe story recap for '{movie_name}' in the exact viral style of Movrecap. "
            f"LENGTH REQUIREMENT: {budget_instruction} "
            "Start IMMEDIATELY with the character's direct crisis or dilemma. "
            "NEVER say 'The opening scenes...', 'The movie begins...', or talk about the film/director/cinematography. "
            "Tell the plot chronologically through character actions, struggles, and suspense. "
            "End with a thrilling climax or cliffhanger."
        )

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
    if catalog_match and catalog_match.get("script") and not is_stale_critic_analysis_script(catalog_match.get("script", "")):
        found = dict(catalog_match)
        found["script_source"] = "catalog"
        return found

    # Generic story recap template. Flagged so callers can refuse to publish filler.
    return {
        "title": f"{movie_name.title()}",
        "badge": "MOVIE RECAP",
        "search_query": f"{movie_name} official trailer",
        "hook": f"What happens in {movie_name}?",
        "script": (
            f"In {movie_name}, everything changes in an instant. "
            f"The main character faces an impossible choice when their entire world is turned upside down. "
            f"As enemies close in and secrets unravel, they must fight against overwhelming odds just to survive. "
            f"Every single step pushes them closer to an edge where one wrong move could cost everything. "
            f"Drop a like and follow for the shocking conclusion!"
        ),
        "tags": ["movierecap", "movieexplained", "endingexplained", "plottwist", "cinema", "shorts"],
        "script_source": "template",
    }


if __name__ == "__main__":
    import sys
    test_movie = sys.argv[1] if len(sys.argv) > 1 else "The Platform"
    print(f"Testing script generation for '{test_movie}'...")
    script_data = generate_movie_script_ai(test_movie)
    print(json.dumps(script_data, indent=2))
