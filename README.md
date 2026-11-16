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


## Original Audio Delivery Format (@woosclips)

Woo's clips keeps the **authentic original audio from the source video** across all moments and scenes instead of narrating the video with an AI voiceover.
- Transcribe (Groq Whisper) -> LLM selects the highest-retention continuous story moments.
- Footage is cut directly to those moments with **original dialogue and audio preserved**.
- Mastered with broadcast loudness normalization (`loudnorm=I=-14:TP=-1.5:LRA=11`), kinetic captions, and subtle ambient bed.
- Hypit deliverable runs with `--original-audio` (or `--no-narration` for classic SVML arc cutter).
- AI TTS voiceover narration can optionally be re-enabled with `--narrate`.

Setup: add a free Groq API key (https://console.groq.com/keys) as the repo secret `GROQ_API_KEY`.
`OPENROUTER_API_KEY` works as a backup script writer. With neither key, the workflow falls back to the classic render.
