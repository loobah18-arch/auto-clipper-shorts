---
name: deepseek-assistant
description: DeepSeek AI assistant integration with persistent project memory, context awareness, and interactive Termux CLI interface. Use to run complex reasoning, multi-model fallback, or direct AI chat via OpenRouter.
---

# DeepSeek Project Assistant Skill

The `deepseek-assistant` skill provides interactive multi-model reasoning and project context memory powered by **DeepSeek V3 / R1** via OpenRouter.

## Features
- **Project Context Memory**: Automatically injects repository structure (`main.py`, clean avatar assets, FFmpeg parameters, active listener animations).
- **Persistent Chat History**: Saves turns to `.deepseek_chat_history.json`.
- **Termux TUI**: Rich ANSI terminal interface with slash commands.

## Usage
Run the interactive CLI from Termux:
```bash
python3 deepseek_chat.py
```

Or query directly from the shell:
```bash
python3 deepseek_chat.py "Explain the active listener head nod algorithm in main.py"
```
