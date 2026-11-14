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


## Narrated recap format (Tested&Proved style)

`narrated_recap.py` turns the full source video into a ~60s, 1080x1080 square Short (set RECAP_ASPECT=9:16 for vertical) with an AI voiceover:
transcribe (Groq Whisper) -> LLM writes a hook-to-payoff script that points every line at its moment
in the source -> edge-tts voices each line -> footage is cut to those moments with the original audio muted
-> captions follow the voice, background music ducks under it. The LLM also writes the title
("He Turned X Into Y 😳 (@NileRed/YT)") and a plain story description.

Setup: add a free Groq API key (https://console.groq.com/keys) as the repo secret `GROQ_API_KEY`.
`OPENROUTER_API_KEY` works as a backup script writer. With neither key, the workflow falls back to the classic render.
