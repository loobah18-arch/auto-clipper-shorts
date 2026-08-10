#!/data/data/com.termux/files/usr/bin/env python3
"""
Multi-Model AI Hub MCP Server for Antigravity.
Connects multiple AI providers (NVIDIA NIM, Groq, OpenRouter, DeepSeek, Ollama)
into Antigravity CLI via Model Context Protocol (MCP).
"""

import os
import sys
import json
import urllib.request
from pathlib import Path

# Load keys from environment or .env file
ENV_VARS = {}
env_path = Path(__file__).resolve().parents[3] / ".env"
if env_path.exists():
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    ENV_VARS[k.strip()] = v.strip("\"'").replace(" ", "")
    except Exception:
        pass


def get_key(key_name: str) -> str:
    return os.environ.get(key_name) or ENV_VARS.get(key_name, "")


PROVIDERS = {
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_name": "NVIDIA_API_KEY",
        "default_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "models": [
            "nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "meta/llama-3.1-70b-instruct",
            "mistralai/mistral-large-2-instruct",
            "deepseek-ai/deepseek-v4-flash-0731",
            "google/gemma-3-12b-it"
        ]
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_name": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            "llama-3.3-70b-versatile",
            "deepseek-r1-distill-llama-70b",
            "llama-3.1-8b-instant",
            "gemma2-9b-it"
        ]
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_name": "OPENROUTER_API_KEY",
        "default_model": "openrouter/free",
        "models": [
            "openrouter/free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "openai/gpt-oss-20b:free",
            "anthropic/claude-3.5-sonnet",
            "deepseek/deepseek-r1",
            "openai/gpt-4o"
        ]
    },
    "deepseek": {
        "url": "https://api.deepseek.com/chat/completions",
        "key_name": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "models": [
            "deepseek-chat",
            "deepseek-reasoner"
        ]
    },
    "ollama": {
        "url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1/chat/completions"),
        "key_name": None,
        "default_model": "llama3.2",
        "models": [
            "llama3.2",
            "qwen2.5-coder",
            "deepseek-r1:1.5b",
            "mistral"
        ]
    }
}


def send_chat_completion(provider_name: str, model_name: str, messages: list, max_tokens: int = 4096, temperature: float = 0.3) -> str:
    provider = PROVIDERS.get(provider_name.lower())
    if not provider:
        raise ValueError(f"Unknown provider '{provider_name}'. Supported: {list(PROVIDERS.keys())}")
        
    api_key = get_key(provider["key_name"]) if provider["key_name"] else "no-key"
    if provider["key_name"] and not api_key:
        raise ValueError(f"{provider['key_name']} is not configured. Please add it to your .env or environment.")
        
    target_model = model_name or provider["default_model"]
    
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/antigravity",
        "X-Title": "Antigravity CLI"
    }
    if api_key and api_key != "no-key":
        headers["Authorization"] = f"Bearer {api_key}"
        
    payload = json.dumps({
        "model": target_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }).encode("utf-8")
    
    req = urllib.request.Request(provider["url"], data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def handle_request(req):
    method = req.get("method")
    
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "ai_query",
                    "description": "Send a prompt to any supported AI model across NVIDIA NIM, Groq, OpenRouter, DeepSeek, or local Ollama.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The question, task, or text to send to the model"
                            },
                            "provider": {
                                "type": "string",
                                "description": "Provider name: 'nvidia', 'groq', 'openrouter', 'deepseek', or 'ollama'",
                                "default": "nvidia"
                            },
                            "model": {
                                "type": "string",
                                "description": "Specific model name (or leave blank to use provider default)"
                            },
                            "system_prompt": {
                                "type": "string",
                                "description": "Optional system prompt instruction"
                            }
                        },
                        "required": ["prompt"]
                    }
                },
                {
                    "name": "list_supported_providers_and_models",
                    "description": "List all configured AI providers, status of API keys, and supported model IDs.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }
        
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        
        try:
            if name == "ai_query":
                prompt = args.get("prompt", "")
                provider = args.get("provider", "nvidia")
                model = args.get("model", "")
                sys_prompt = args.get("system_prompt", "You are an expert AI assistant delivering clear, accurate, high-impact responses.")
                
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt}
                ]
                ans = send_chat_completion(provider, model, messages)
                return {"content": [{"type": "text", "text": ans}]}
                
            elif name == "list_supported_providers_and_models":
                lines = ["### 🌐 Multi-Model AI Hub Status:\n"]
                for p_name, p_info in PROVIDERS.items():
                    key_val = get_key(p_info["key_name"]) if p_info["key_name"] else "Local / No Key Required"
                    status_icon = "🟢 Active" if (key_val and key_val != "") else "⚪ Key Missing"
                    lines.append(f"#### **{p_name.upper()}** ({status_icon})")
                    lines.append(f"- **Key Env Variable**: `{p_info['key_name'] or 'None (Local)'}`")
                    lines.append(f"- **Default Model**: `{p_info['default_model']}`")
                    lines.append(f"- **Sample Models**: `{', '.join(p_info['models'])}`\n")
                    
                return {"content": [{"type": "text", "text": "\n".join(lines)}]}
                
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            return {"error": str(e)}
            
    return {"error": f"Unknown method: {method}"}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            res = handle_request(req)
            if "id" in req:
                res["id"] = req["id"]
            res["jsonrpc"] = "2.0"
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_res = {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}}
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
