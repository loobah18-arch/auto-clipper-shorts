---
name: nvidia-nemotron
description: Integration playbook for NVIDIA NIM and Nemotron 3 Ultra (550B MoE) for coding, deep reasoning, and long-context agentic workflows.
---

# NVIDIA Nemotron 3 Ultra Playbook

This skill provides complete configuration and usage instructions for NVIDIA's hosted **Nemotron 3 Ultra** (`nvidia/nemotron-3-ultra-550b-a55b`) model.

## 1. Specifications
- **Model ID**: `nvidia/nemotron-3-ultra-550b-a55b`
- **Base URL**: `https://integrate.api.nvidia.com/v1`
- **Context Window**: 1,000,000 tokens (1M context)
- **Architecture**: 550B+ Total Parameters, 55B Active MoE
- **Specialties**: Agentic reasoning, long-context code synthesis, viral content strategy, and multi-file planning.

## 2. API Key Setup
1. Visit [NVIDIA NIM Model Catalog](https://build.nvidia.com/).
2. Select **Nemotron 3 Ultra** and generate a free API key (`nvapi-xxxxxxxxxxxxxxxx`).
3. Set your environment variable:
   ```bash
   export NVIDIA_API_KEY="nvapi-YOUR_KEY_HERE"
   ```

## 3. Direct Python Integration (OpenAI-Compatible)
```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY")
)

response = client.chat.completions.create(
    model="nvidia/nemotron-3-ultra-550b-a55b",
    messages=[
        {"role": "system", "content": "You are a master coder and reasoning agent."},
        {"role": "user", "content": "Analyze the transcript and identify viral moments."}
    ],
    temperature=0.2,
    max_tokens=4096
)

print(response.choices[0].message.content)
```

## 4. Antigravity CLI / LiteLLM Proxy Gateway
Route local Antigravity CLI queries through NVIDIA Nemotron:
```bash
pip install litellm
litellm --model openai/nvidia/nemotron-3-ultra-550b-a55b --api_base https://integrate.api.nvidia.com/v1 --port 8000
```
