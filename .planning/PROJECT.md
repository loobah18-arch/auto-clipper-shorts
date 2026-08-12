# Auto-Clipper Shorts (Tech Facts & Tips Edition)

## Project Purpose
High-retention, fully automated 9:16 vertical short-form video generator for YouTube Shorts focusing on Developer Tips, Computer Science Secrets, Linux Superpowers, and Mind-Blowing Tech Revelations.

## Target Audience & Vibe
- **Audience:** Developers, tech enthusiasts, CS students, tech workers.
- **Visual Style:** Split-studio layout with Wolf Host (100% close-up speaking & natural hand gestures) and Lion Reactor (attentive & shocked hands-on-head reaction on big revelations) over HD gameplay/satisfying backgrounds with neon dynamic karaoke ASS subtitles.
- **Audio Engine:** Neural Text-to-Speech (`edge-tts`) for rapid, crisp, high-energy narration with exact word-level subtitle alignment.

## Core Directives & Guardrails
- **Wolf Avatar:** Locked in 100% close-up framing with active lip-sync visemes and natural speaking hand gestures appearing 1–2 times per video.
- **Lion Avatar:** Viewer proxy who stays calmly attentive and gets shocked (hands-on-head) at most once (or 0 times on standard facts).
- **Safety Boundaries:** Never touch `main` branch, personal files, or other repositories without explicit permission.
- **CI/CD Automation:** Runs via GitHub Actions (`.github/workflows/daily_clip.yml`) on branch `test-upgrade`.
