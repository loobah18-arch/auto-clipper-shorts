# 🎬 Hypit Video Engine

Hypit video workflow and domain-specific authoring framework for AI coding agents.

This project uses **SVML** (Semantic Video Markup Language) and **SVS** stylesheets to author high-impact viral shorts where cuts, captions, transitions, and audio ducking are anchored to spoken words.

---

## 🚀 Features

- **Semantic Video Markup Language (SVML)**: Declarative canvas (`<space:Canvas>`), timelines (`<time:Timeline>`), and audio/visual tracks (`<visual:Track>`, `<audio:Track>`).
- **Word-Anchored Typography**: Frame-locked, kinetic subtitles anchored to speech cues (`look.svs` styles).
- **Run Manifests (`.svrun`)**: Reusable candidate graphs linking sources, styles, and rendering targets.
- **Hypit Engine**: High-performance local and cloud compiler utilizing FFmpeg for 9:16 vertical delivery.
- **Agent Skill Integration**: Native AI agent skill installed at `.agents/skills/hypit/` and global `~/.agents/skills/hypit/`.

---

## 📁 Project Structure

```
├── .agents/skills/hypit/     <- Complete Hypit Agent skill, DSL reference & playbooks
├── .github/workflows/       <- Cloud rendering CI/CD (hypit_render.yml)
├── assets/
│   ├── fonts/               <- Design tokens & typography (Montserrat-ExtraBold)
│   ├── bgm/                 <- Curated background music tracks
│   └── sfx/                 <- Sound effects (pops, whooshes, shutters)
├── sources/
│   ├── mrbeast_yacht.svml   <- SVML authoring specification for MrBeast $1 vs $1B Yacht
│   ├── look.svs             <- Hypit stylesheet for typography and film settings
│   └── production.svrun     <- Production execution manifest
├── hypit_engine.py          <- Hypit compiler and video render pipeline
├── test_hypit_engine.py     <- Unit tests for parsing and subtitle generation
├── package.json             <- Hypit project configuration
└── requirements.txt         <- Python dependencies
```

---

## ⚡ Usage

### Validate Project
```bash
python3 hypit_engine.py --check
```

### Render Deliverable
```bash
python3 hypit_engine.py sources/production.svrun -o /storage/emulated/0/Download/hypit_mrbeast_short.mp4
```

### Run Tests
```bash
python3 test_hypit_engine.py
```


## Tested&Proved Benchmark Format (@woosclips)

The automated Shorts pipeline mirrors the top-performing benchmark format from [@woosclips](https://youtube.com/shorts/GKz-NtMJFg8):
- **Aspect Ratio**: 1080x1440 (3:4 vertical canvas) to maximize phone screen real estate without black bars.
- **Audio Architecture**:
  - **0.0s – 3.75s (Hook Window)**: Deep human baritone voiceover (`BrianMultilingualNeural`) reading the declarative hook title, with source audio dimmed in the background.
  - **3.75s – 60.0s (Main Story Arc)**: 100% authentic original video audio (`volume=1.10`) preserving the creator's real dialogue, reactions, and sound effects.
- **Story Arc**: 6-stage "Completion Compulsion" curve (`HOOK -> RISING ACTION -> CONFLICT -> COMEBACK -> RISING ACTION -> PAYOFF`) cut across the entire raw source video.
- **HUD & Packaging**: Top-left stage badges (`[ 01 · HOOK ]`, `[ 02 · RISING ACTION ]`, etc.), giant yellow title card, and cinematic transition whoosh SFX (`assets/sfx/whoosh.mp3`).

Setup: add a free Groq API key (https://console.groq.com/keys) as the repo secret `GROQ_API_KEY`.
`OPENROUTER_API_KEY` works as a backup script writer. With neither key, the workflow falls back to the classic render.
