# 💻 Auto Clipper Shorts — Tech Facts & Developer Tips Edition

Automatically finds viral moments in top tech, coding, and computer science channels (or generates standalone viral tech facts & developer tricks), transcribes them with Whisper AI, selects high-impact hooks with NVIDIA Nemotron / Groq LLM, renders vertical 9:16 Shorts with dynamic neon karaoke subtitles via FFmpeg, and uploads them to your YouTube Channel — **100% Free & Hands-Free via GitHub Actions**.

---

## ⚡ 100% Free & Open-Source Stack

Just like our automated pipelines, this runs entirely on free tier tools:

| Component | Technology | Cost |
|---|---|---|
| **Audio Extraction** | `yt-dlp` | Free / Open-Source |
| **Transcription & Word Timestamps** | `faster-whisper` (CPU int8) | Free / Open-Source |
| **Viral Tech Hook Detection** | `NVIDIA Nemotron` / `Groq` | Free Tier API |
| **9:16 Video & Karaoke Subtitles** | Pure `FFmpeg` + `ASS` engine | Free / Open-Source |
| **YouTube Upload** | Google YouTube Data API v3 | Free (10,000 units/day) |
| **Scheduled Automation** | GitHub Actions (`ubuntu-latest`) | Free (2,000 min/month) |

---

## 🛡️ Curated Tech Sources & Creators

To keep your channel educational, high-retention, and copyright-safe, this pipeline uses curated tech, developer, and computer science creators:

| Channel / Topic | Focus Area | Tags & Badges |
|---|---|---|
| **Fireship** | Fast Tech Insights, Code in 100s | `DEV HACK ⚡`, `#Coding` |
| **NetworkChuck** | Linux Superpowers, Cloud, Hacking | `LINUX TRICK 🐧`, `#Linux` |
| **Computerphile** | Deep Computer Science & Cryptography | `TECH FACT 💡`, `#ComputerScience` |
| **ByteByteGo (Alex Xu)** | System Design & Backend Architecture | `SYSTEM DESIGN 🏗️`, `#Developer` |
| **ThePrimeTime (ThePrimeagen)** | Software Engineering & Dev Tools | `DEV PRO TIP 💻`, `#Programming` |
| **Waveform Clips (MKBHD Team)** | Future Tech, AI Hardware & Gadgets | `AI INSIGHT 🤖`, `#FutureTech` |

*All generated descriptions automatically credit the creator and link back to the full original episode.*

---

## 🎬 Video Layout & Aesthetic (9:16 Vertical)

1. **Background**: Original 16:9 footage scaled to 1080x1920 with heavy Gaussian blur and subtle dark vignette for depth.
2. **Foreground**: 1080p crisp center crop with high contrast.
3. **Top Header**: Stylized Speaker / Topic badge.
4. **Bottom Subtitles**: High-energy ASS karaoke subtitles:
   - Active word glowing yellow highlight (`&H002BF5FF`)
   - Thick black stroke for 100% readability on any background
   - Vertical margin positioned above YouTube Shorts interface buttons

---

## ⚙️ Quick Setup (5 Minutes)

### 1. Create a New GitHub Repository
Create a new GitHub repository for your clipping channel (e.g. `auto-clipper-shorts`).

```bash
cd auto-clipper-shorts
git init
git add .
git commit -m "Initial commit for auto-clipper-shorts"
git branch -M main
git remote add origin git@github.com:<YOUR_USERNAME>/auto-clipper-shorts.git
git push -u origin main
```

### 2. Set Up YouTube OAuth for the New Channel
Run the helper locally once:
```bash
python auth_setup.py
```
Log in with your **new YouTube channel account**. It will output:
- `CLIENT_ID`
- `CLIENT_SECRET`
- `REFRESH_TOKEN`

### 3. Add GitHub Secrets
In your new GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Where to get it |
|---|---|
| `NVIDIA_API_KEY` | Free / Developer API key from [build.nvidia.com](https://build.nvidia.com) (Nemotron 3 Ultra 550B) |
| `GROQ_API_KEY` | Free from [console.groq.com](https://console.groq.com) (Fallback LLM) |
| `MINIMAX_API_KEY` | Optional: [platform.minimax.io](https://platform.minimax.io) for H3 / Hailuo avatar gesture video generation |
| `MINIMAX_VIDEO_MODEL` | Optional: `hailuo-h3` (default) or `video-01` |
| `CLIENT_ID` | From Google Cloud Console |
| `CLIENT_SECRET` | From Google Cloud Console |
| `REFRESH_TOKEN` | From `auth_setup.py` |

---

## 🚀 How to Run

### Automatic (Scheduled)
Runs twice daily automatically at **08:00 and 20:00 UTC** via GitHub Actions.

### Manual Trigger
1. Go to **Actions → 🎬 Auto Podcast Clipper Shorts → Run workflow**.
2. Optionally enter a specific **YouTube video URL** to clip on demand!

### Local Dry Run
```bash
python main.py --dry-run --channel lex_fridman
```
