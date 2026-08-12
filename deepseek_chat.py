#!/usr/bin/env python3
"""
Antigravity, Claude & DeepSeek Super Assistant - Termux Autonomous Edition
Provides 100% natural language interaction and REAL live command execution.
Automatically detects intent for:
- GitHub Workflows ("run workflow", "trigger workflow", "run daily clip")
- Web Search ("search for...", "look up...", "what is the latest...")
- GitHub Repository ("check my github", "git status", "repo info...")
- Git Push ("push code to github", "commit and push...")
- Web Page Reading (any URL pasted or mentioned)
- YouTube Processing (any YouTube link)
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path

# Paths
REPO_DIR = Path("/data/data/com.termux/files/home/antigravity/auto-clipper-shorts")
HISTORY_FILE = REPO_DIR / ".deepseek_chat_history.json"
ENV_FILE = REPO_DIR / ".env"

# Load API Keys
ENV_VARS = {}
if ENV_FILE.exists():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    ENV_VARS[k.strip()] = v.strip("\"'").replace(" ", "")
    except Exception:
        pass

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or ENV_VARS.get("OPENROUTER_API_KEY", "")
NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY") or ENV_VARS.get("NVIDIA_API_KEY", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY") or ENV_VARS.get("GROQ_API_KEY", "")

MODELS = {
    "claude": {
        "name": "Claude 3.5 Sonnet",
        "model_id": "anthropic/claude-3.5-sonnet",
        "provider": "openrouter"
    },
    "deepseek": {
        "name": "DeepSeek R1 (Reasoning)",
        "model_id": "deepseek/deepseek-r1",
        "provider": "openrouter"
    },
    "deepseek-v3": {
        "name": "DeepSeek V3 (Chat)",
        "model_id": "deepseek/deepseek-chat",
        "provider": "openrouter"
    },
    "gemini": {
        "name": "Gemini 3.6 Flash / Pro",
        "model_id": "google/gemma-4-31b-it:free",
        "provider": "openrouter"
    },
    "nemotron": {
        "name": "NVIDIA Nemotron 49B",
        "model_id": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "provider": "nvidia"
    },
    "llama": {
        "name": "Groq Llama 3.3 70B",
        "model_id": "llama-3.3-70b-versatile",
        "provider": "groq"
    }
}

CURRENT_MODEL_KEY = "nemotron"


# ANSI Styling
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_PURPLE = "\033[35m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_RED = "\033[31m"

SYSTEM_CONTEXT = """You are an autonomous AI Pair Programmer combining Claude 3.5 Sonnet, Antigravity, and DeepSeek.
You have live execution tools for GitHub CLI (gh), Git, Web Search, and YouTube.

CRITICAL GROUNDING RULE:
- Never fabricate or hallucinate command execution logs or outputs.
- ONLY report real, empirical tool execution outputs provided in the context blocks.
- If a tool action was executed, explain the REAL empirical result accurately.

PROJECT MEMORY:
- Location: /data/data/com.termux/files/home/antigravity/auto-clipper-shorts
- Active Branch: main
- Active GitHub Workflow: daily_clip.yml (ID 329597731)
"""


def run_command_output(cmd: list) -> str:
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=REPO_DIR, timeout=35)
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error executing {' '.join(cmd)}: {e}"


def search_web(query: str) -> str:
    """Perform live web search via DuckDuckGo HTML API."""
    try:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            titles = re.findall(r"<a[^>]*class=\"result__a\"[^>]*>(.*?)</a>", html, re.DOTALL)
            snippets = re.findall(r"<a[^>]*class=\"result__snippet\"[^>]*>(.*?)</a>", html, re.DOTALL)
            urls = re.findall(r"<a[^>]*class=\"result__url\"[^>]*>(.*?)</a>", html, re.DOTALL)

            results = []
            for t, s, u in zip(titles[:5], snippets[:5], urls[:5]):
                clean_t = re.sub(r"<.*?>", "", t).strip()
                clean_s = re.sub(r"<.*?>", "", s).strip()
                clean_u = re.sub(r"<.*?>", "", u).strip()
                results.append(f"🌐 **{clean_t}**\n   {clean_s}\n   🔗 {clean_u}")

            if results:
                return "\n\n".join(results)
            return "No web search results found."
    except Exception as e:
        return f"Web search error: {e}"


def read_url(url: str) -> str:
    """Fetch content from any URL and clean HTML to text."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<.*?>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:4000]
    except Exception as e:
        return f"Error reading URL {url}: {e}"


def extract_youtube_info(url: str) -> str:
    """Extract YouTube video metadata using yt-dlp."""
    try:
        cmd = ["yt-dlp", "--dump-json", "--no-warnings", url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            title = data.get("fulltitle") or data.get("title", "Unknown Title")
            channel = data.get("channel") or data.get("uploader", "Unknown Channel")
            duration = data.get("duration_string") or f"{data.get('duration', 0)}s"
            description = (data.get("description") or "")[:400]
            
            return f"📺 **YouTube Video Info**:\n- **Title**: {title}\n- **Channel**: {channel}\n- **Duration**: {duration}\n- **URL**: {url}\n- **Description**: {description}..."
        else:
            return f"Error extracting YouTube URL: {res.stderr.strip()}"
    except Exception as e:
        return f"Error with yt-dlp: {e}"


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-30:], f, indent=2)
    except Exception:
        pass


def clear_history():
    save_history([])


def format_markdown_terminal(text: str) -> str:
    lines = text.split("\n")
    formatted = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            formatted.append(f"{C_PURPLE}──────────────────────────────────────────{C_RESET}")
            continue

        if in_code_block:
            formatted.append(f"{C_YELLOW}  │ {line}{C_RESET}")
            continue

        if line.startswith("# "):
            formatted.append(f"\n{C_BOLD}{C_CYAN}  █ {line[2:].upper()}{C_RESET}")
        elif line.startswith("## "):
            formatted.append(f"\n{C_BOLD}{C_PURPLE}  ✦ {line[3:]}{C_RESET}")
        elif line.startswith("### "):
            formatted.append(f"\n{C_BOLD}{C_GREEN}  ● {line[4:]}{C_RESET}")
        elif line.strip().startswith("- ") or line.strip().startswith("* "):
            formatted.append(f"  {C_CYAN}•{C_RESET} {line.strip()[2:]}")
        elif len(line.strip()) > 2 and line.strip()[0].isdigit() and line.strip()[1] in (".", ")"):
            formatted.append(f"  {C_YELLOW}{line.strip()[:2]}{C_RESET} {line.strip()[2:].strip()}")
        else:
            l = line.replace("**", C_BOLD).replace("`", C_CYAN)
            formatted.append(f"  {l}")

    return "\n".join(formatted)


def execute_llm_request(provider: str, model_id: str, messages: list) -> str:
    payload = json.dumps({
        "model": model_id,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096
    }).encode("utf-8")

    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        key = OPENROUTER_KEY
    elif provider == "nvidia":
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        key = NVIDIA_KEY
    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        key = GROQ_KEY
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        key = OPENROUTER_KEY

    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/antigravity",
        "X-Title": "Antigravity Multi-Model Assistant"
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        msg = data["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning") or ""


def query_assistant(user_prompt: str) -> str:
    p_lower = user_prompt.lower()
    auto_data = []

    # 1. Automatic GitHub Workflow Intent Recognition
    if any(k in p_lower for k in ("run workflow", "trigger workflow", "dispatch workflow", "run daily clip", "run any of the")):
        print(f"{C_CYAN}🤖 Auto-detected Workflow request: Running GitHub Actions workflow daily_clip.yml...{C_RESET}")
        run_res = run_command_output(["gh", "workflow", "run", "daily_clip.yml", "--ref", "main"])
        wf_runs = run_command_output(["gh", "run", "list", "--workflow", "daily_clip.yml", "-L", "3"])
        auto_data.append(f"[Live Action Executed - GitHub Workflow Triggered]:\nTrigger Result: {run_res or 'Workflow dispatched successfully to main branch'}\nRecent Runs Status:\n{wf_runs}")

    # 2. Automatic GitHub / Repository Intent Recognition
    elif any(k in p_lower for k in ("github", "repo", "repository", "git status", "my git", "my remote", "branch")):
        print(f"{C_CYAN}🤖 Auto-detected GitHub request: Fetching live GitHub metadata...{C_RESET}")
        gh_info = run_command_output(["gh", "repo", "view", "--json", "name,owner,url,description"])
        git_st = run_command_output(["git", "status", "-s"])
        git_br = run_command_output(["git", "branch", "--show-current"])
        git_rem = run_command_output(["git", "remote", "-v"])
        auto_data.append(f"[Live GitHub & Git Data]:\nRepository Info: {gh_info}\nBranch: {git_br}\nGit Remote: {git_rem}\nGit Status: {git_st or 'Clean'}")

    # 3. Automatic Git Push Intent Recognition
    if any(k in p_lower for k in ("push code", "push to github", "commit and push", "push my changes")):
        print(f"{C_CYAN}🤖 Auto-detected Push request: Staging, committing & pushing code to GitHub...{C_RESET}")
        run_command_output(["git", "add", "."])
        commit_res = run_command_output(["git", "commit", "-m", "Auto-commit via Assistant"])
        push_res = run_command_output(["git", "push", "origin", "HEAD"])
        auto_data.append(f"[Live Git Push Action Executed]:\nCommit Result: {commit_res}\nPush Result: {push_res}")

    # 4. Automatic Web Search Intent Recognition
    search_match = re.search(r"(?:search for|search web for|search|look up|google|find online)\s+([^\.\?\n]+)", user_prompt, re.IGNORECASE)
    if search_match:
        sq = search_match.group(1).strip()
        if sq and not sq.startswith("http"):
            print(f"{C_CYAN}🤖 Auto-detected Search request: Querying live web for '{sq}'...{C_RESET}")
            s_res = search_web(sq)
            auto_data.append(f"[Live Web Search Results for '{sq}']:\n{s_res}")

    # 5. Automatic HTTP/HTTPS URL Extraction (YouTube or Web Pages)
    words = user_prompt.split()
    for w in words:
        if w.startswith("http://") or w.startswith("https://"):
            if "youtube.com" in w or "youtu.be" in w:
                print(f"{C_CYAN}🤖 Auto-detected YouTube link: Extracting video info via yt-dlp...{C_RESET}")
                yt_info = extract_youtube_info(w)
                auto_data.append(f"[Live YouTube Data]:\n{yt_info}")
            else:
                print(f"{C_CYAN}🤖 Auto-detected Web URL: Fetching web page content...{C_RESET}")
                web_text = read_url(w)
                auto_data.append(f"[Live Web Page Content for {w}]:\n{web_text[:2500]}")

    full_user_prompt = user_prompt
    if auto_data:
        full_user_prompt += "\n\n" + "\n\n".join(auto_data)

    history = load_history()
    messages = [{"role": "system", "content": SYSTEM_CONTEXT}]
    messages.extend(history)
    messages.append({"role": "user", "content": full_user_prompt})

    m_info = MODELS.get(CURRENT_MODEL_KEY, MODELS["claude"])
    
    try:
        reply = execute_llm_request(m_info["provider"], m_info["model_id"], messages)
    except Exception as primary_err:
        # Fallback 1: DeepSeek via OpenRouter
        try:
            fb_info = MODELS["deepseek"]
            reply = f"{C_YELLOW}[Auto-fallback to DeepSeek R1]:{C_RESET}\n" + execute_llm_request(fb_info["provider"], fb_info["model_id"], messages)
        except Exception as fb_err1:
            # Fallback 2: NVIDIA Nemotron
            try:
                fb_info2 = MODELS["nemotron"]
                reply = f"{C_YELLOW}[Auto-fallback to NVIDIA Nemotron]:{C_RESET}\n" + execute_llm_request(fb_info2["provider"], fb_info2["model_id"], messages)
            except Exception as fb_err2:
                # Fallback 3: Groq Llama
                try:
                    fb_info3 = MODELS["llama"]
                    reply = f"{C_YELLOW}[Auto-fallback to Groq Llama 3.3]:{C_RESET}\n" + execute_llm_request(fb_info3["provider"], fb_info3["model_id"], messages)
                except Exception as fb_err3:
                    return f"Error: All model providers failed ({primary_err} / {fb_err1} / {fb_err2} / {fb_err3})."

    history.append({"role": "user", "content": user_prompt})
    history.append({"role": "assistant", "content": reply})
    save_history(history)
    return reply


def print_banner():
    history = load_history()
    turn_count = len(history) // 2
    m_info = MODELS.get(CURRENT_MODEL_KEY, MODELS["claude"])

    print(f"{C_CYAN}┌─────────────────────────────────────────────────────────────┐{C_RESET}")
    print(f"{C_CYAN}│{C_RESET}  {C_BOLD}{C_PURPLE}🌐 CLAUDE, ANTIGRAVITY & DEEPSEEK (AUTONOMOUS){C_RESET}             {C_CYAN}│{C_RESET}")
    print(f"{C_CYAN}│{C_RESET}  {C_CYAN}🟢 Model:{C_RESET} {m_info['name']:<20} {C_CYAN}🤖 Mode:{C_RESET} 100% Natural AI   {C_CYAN}│{C_RESET}")
    print(f"{C_CYAN}│{C_RESET}  {C_CYAN}📁 Repo:{C_RESET} auto-clipper-shorts        {C_CYAN}🧠 Turns:{C_RESET} {turn_count:<5}          {C_CYAN}│{C_RESET}")
    print(f"{C_CYAN}└─────────────────────────────────────────────────────────────┘{C_RESET}")
    print(f"{C_DIM} Ask anything in plain English! (No slash commands required).{C_RESET}\n")


def main():
    os.chdir(REPO_DIR)
    
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        print(f"\n{C_PURPLE}⚡ Thinking...{C_RESET}\n")
        reply = query_assistant(prompt)
        print(format_markdown_terminal(reply))
        print()
        return

    os.system("clear")
    print_banner()

    while True:
        try:
            m_name = MODELS[CURRENT_MODEL_KEY]["name"].split()[0]
            print(f"{C_PURPLE}╭───[{C_RESET}{C_BOLD}⚡ {m_name}{C_RESET}{C_PURPLE}]{C_RESET}")
            user_input = input(f"{C_PURPLE}╰─>{C_RESET} {C_BOLD}").strip()
            print(C_RESET, end="")

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print(f"\n{C_PURPLE}👋 Session ended. Goodbye!{C_RESET}\n")
                break

            print(f"\n{C_CYAN}⚡ Processing request...{C_RESET}\n")
            reply = query_assistant(user_input)

            print(f"{C_BOLD}{C_GREEN}⚡ Assistant Response:{C_RESET}")
            print(format_markdown_terminal(reply))
            print()

        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{C_PURPLE}👋 Session ended.{C_RESET}\n")
            break


if __name__ == "__main__":
    main()
