#!/usr/bin/env python3
"""
NVIDIA Nemotron MCP Server for Antigravity CLI.
Exposes nvidia_nemotron_query tool to Antigravity CLI using the hosted NVIDIA NIM API.
"""

import os
import sys
import json
import urllib.request

def handle_request(req):
    method = req.get("method")
    
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "nvidia_nemotron_query",
                    "description": "Query NVIDIA Nemotron 3 Ultra (550B MoE, 1M context) for complex coding, deep reasoning, viral strategy, and large codebase planning.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The prompt or task for Nemotron 3 Ultra"
                            },
                            "system_prompt": {
                                "type": "string",
                                "description": "Optional system prompt to guide Nemotron's role"
                            }
                        },
                        "required": ["prompt"]
                    }
                }
            ]
        }
        
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        
        if name == "nvidia_nemotron_query":
            prompt = args.get("prompt", "")
            system_prompt = args.get("system_prompt", "You are NVIDIA Nemotron 3 Ultra, a premier coding and reasoning AI.")
            api_key = os.environ.get("NVIDIA_API_KEY", "")
            
            if not api_key:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Error: NVIDIA_API_KEY environment variable is not set. Please obtain a free key from build.nvidia.com and export NVIDIA_API_KEY=nvapi-..."
                        }
                    ]
                }
                
            payload = json.dumps({
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 4096
            }).encode("utf-8")
            
            req_obj = urllib.request.Request(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
            )
            
            try:
                with urllib.request.urlopen(req_obj, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    text_out = data["choices"][0]["message"]["content"]
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": text_out
                            }
                        ]
                    }
            except Exception as e:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error querying NVIDIA Nemotron API: {e}"
                        }
                    ]
                }
                
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
