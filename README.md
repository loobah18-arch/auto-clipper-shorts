# 🎙️ Auto Clipper Shorts

Automatically finds viral moments in top podcasts & talks, transcribes them with Whisper AI, selects viral hooks with Groq LLM, renders vertical 9:16 Shorts with dynamic karaoke subtitles via FFmpeg, and uploads them to your new YouTube Channel — **100% Free & Hands-Free via GitHub Actions**.

---

## ⚡ 100% Free & Open-Source Stack

Just like our Roblox Shorts pipeline, this runs entirely on free tier tools:

| Component | Technology | Cost |
|---|---|---|
| **Audio Extraction** | `yt-dlp` | Free / Open-Source |
| **Transcription & Word Timestamps** | `faster-whisper` (CPU int8) | Free / Open-Source |
| **Viral Hook Detection** | `Groq` (`llama-3.3-70b-versatile`) | Free Tier API |
| **9:16 Video & Karaoke Subtitles** | Pure `FFmpeg` + `ASS` engine | Free / Open-Source |
| **YouTube Upload** | Google YouTube Data API v3 | Free (10,000 units/day) |
| **Scheduled Automation** | GitHub Actions (`ubuntu-latest`) | Free (2,000 min/month) |

---

## 🛡️ Copyright-Safe Creators & Content Policy

To keep your channel 100% safe from copyright strikes, this pipeline uses curated channels and podcasts that operate under **Creative Commons**, **Public Domain / University Lectures**, or **explicit open clipping & attribution policies**:

| Channel | Focus Area | Clipping Policy |
|---|---|---|
| **Lex Fridman Podcast** | AI, Tech, Science, Philosophy | Explicitly allows clips with channel & guest attribution |
| **Huberman Lab** | Neuroscience, Health, Protocols | Educational commentary / fair use attribution |
| **Big Think** | Ideas, Psychology, Science | Creative Commons & educational thought leadership |
| **Modern Wisdom** (Chris Williamson) | Mindset, Psychology, Self-Improvement | Open clipping policy with source link |
| **Y Combinator** | Startups, Founders, Tech | Public startup school & founder lessons |
| **Stanford Online & MIT OCW** | Science, Innovation, Lectures | Open educational resources (CC-BY) |
| **Oxford Union** | World Debates & Addresses | Public discourse & educational commentary |

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
| `GROQ_API_KEY` | Free from [console.groq.com](https://console.groq.com) |
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
