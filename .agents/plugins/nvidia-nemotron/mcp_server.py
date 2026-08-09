#!/data/data/com.termux/files/usr/bin/env python3
"""
NVIDIA Nemotron 3 Ultra MCP Server for Antigravity.
Exposes tools for deep reasoning, coding, and architectural planning via NVIDIA NIM.
"""

import os
import sys
import json
import urllib.request

API_KEY = os.environ.get("NVIDIA_API_KEY", "")

def call_nemotron(messages, max_tokens=4096, temperature=0.2):
    if not API_KEY:
        raise ValueError("NVIDIA_API_KEY is not set.")
        
    payload = json.dumps({
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

def handle_request(req):
    method = req.get("method")
    
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "nemotron_query",
                    "description": "Send a reasoning or coding prompt directly to NVIDIA Nemotron 3 Ultra (550B MoE, 1M context).",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The task, question, or code to analyze"
                            },
                            "system_prompt": {
                                "type": "string",
                                "description": "Optional system instructions (e.g. 'You are a senior systems architect')"
                            }
                        },
                        "required": ["prompt"]
                    }
                },
                {
                    "name": "nemotron_code_review",
                    "description": "Have NVIDIA Nemotron 3 Ultra review and optimize code for performance, security, and edge cases.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "The source code to review"
                            },
                            "focus": {
                                "type": "string",
                                "description": "Specific focus area: performance, security, refactoring, or bug finding"
                            }
                        },
                        "required": ["code"]
                    }
                }
            ]
        }
        
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        
        try:
            if name == "nemotron_query":
                prompt = args.get("prompt", "")
                sys_prompt = args.get("system_prompt", "You are NVIDIA Nemotron 3 Ultra, a world-class reasoning and programming AI.")
                msgs = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt}
                ]
                ans = call_nemotron(msgs)
                return {"content": [{"type": "text", "text": ans}]}
                
            elif name == "nemotron_code_review":
                code = args.get("code", "")
                focus = args.get("focus", "general quality and performance")
                msgs = [
                    {"role": "system", "content": f"You are NVIDIA Nemotron 3 Ultra. Perform a thorough code review focusing on: {focus}."},
                    {"role": "user", "content": f"Please review the following code:\n\n```\n{code}\n```"}
                ]
                ans = call_nemotron(msgs)
                return {"content": [{"type": "text", "text": ans}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}
            
    return {}

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            res = handle_request(req)
            out = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "result": res
            }
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)}
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
