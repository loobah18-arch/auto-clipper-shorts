# 🎬 Auto Movie Explanation Shorts (MovieGyan & Movie Insight Style)

An automated YouTube Shorts channel engine that produces high-retention 50-55 second **Movie Explanations, Plot Breakdowns, and Twist Reveals** in the signature style of viral channels like **MovieGyan** and **Movie Insight Hindi**.

Generates neural narration (`edge-tts`), dynamic word-level neon karaoke subtitles (`.ass`), uses explicitly configured authorized trailer or local media, slices fast 2.5–3.2s scene cuts, renders a 9:16 vertical split/blurred layout via FFmpeg, and uploads to YouTube with SEO metadata. Rendering and review artifacts are automated through GitHub Actions.

---

## 🌟 Visual Layout & Aesthetic (MovieGyan Style)

- **9:16 Vertical Framing (1080x1920)**: Engineered specifically for YouTube Shorts, TikTok, and Reels.
- **Blurred Mirror Background**: Sourced trailer footage dynamically scaled, cropped to 1080x1920, and blurred (`boxblur=25:5`) to eliminate black bars.
- **Centered 16:9 Foreground Movie Clip**: Crisp movie scenes positioned at `(W-w)/2:(H-h)/2 - 40`.
- **Rapid Scene Switches (Fair Use & High Retention)**: Automatically extracts 15–20 dynamic scene segments (every 2.5–3.2s) across the trailer to keep pacing relentless.
- **Top Badge Pill**: Sleek dark header badge (`🎬 MOVIE EXPLAINED • {MOVIE TITLE}`).
- **Dynamic Neon Karaoke Subtitles**:
  - Active spoken word: Highlighted in **Bright Yellow** (`&H0000FFFF&`) with pop scale.
  - Inactive words: Crisp white with heavy black outline for 100% legibility.
  - Placed comfortably in the lower third above YouTube Shorts UI buttons.
- **Audio Mix**: Deep cinematic narrator voiceover (0dB) with optional, explicitly configured BGM. No bundled copyrighted soundtrack is selected by default.

---

## ⚡ 100% Free Tech Stack

| Component | Technology | Cost |
|---|---|---|
| **Movie Script Engine** | Curated Catalog + Groq (Llama 3.3 70B) / DeepSeek | Free Tier |
| **Neural Voiceover** | `edge-tts` (ChristopherNeural / MadhurNeural) | 100% Free |
| **Footage Extraction** | Explicitly configured authorized trailer/local media via `yt-dlp` or FFmpeg | Free / Open-Source |
| **Subtitle Engine** | Word-Level Timing + Advanced ASS Karaoke | Free / Open-Source |
| **Video Compositor** | `FFmpeg` (Multi-layer filtergraph) | Free / Open-Source |
| **YouTube Upload** | Google YouTube Data API v3 | Free (10,000 units/day) |
| **Cloud Automation** | GitHub Actions (`ubuntu-latest`) | Free (2,000 min/month) |

---

## 📚 Curated Movie Catalog & AI Generation

Includes 15+ pre-crafted viral thriller & mind-bending movie recaps in `movie_catalog.json`:
- **The Platform (2019)** — The 333-floor vertical prison food mystery
- **Fall (2022)** — Trapped on a 2,000-ft rusted tower with a chilling twist
- **Coherence (2013)** — Passing comet fractures reality into infinite dimensions
- **Shutter Island (2010)** — Asylum investigation and the iconic psychiatric twist
- **Interstellar (2014)** — Gargantua black hole gravitational time dilation
- **Source Code (2011)** — 8-minute recurring train explosion time loop
- **The Mist (2007)** — Lovecraftian horrors and cinema's most heartbreaking ending
- **Parasite (2019)** — Basement bunker secret under the luxury mansion
- **A Quiet Place (2018)** — Blind sound-hunting alien predators
- **Ex Machina (2014)** — AI Turing test and ruthless deception
- **Don't Breathe (2016)** — Blind military veteran's dark basement secret
- **Vivarium (2019)** — Infinite suburban labyrinth extraterrestrial zoo
- **Predestination (2014)** — Ultimate bootstrap paradox time loop
- **The Truman Show (1998)** — Life as a 24/7 unscripted global TV show
- **Triangle (2009)** — Abandoned ocean liner temporal purgatory loop

### On-Demand AI Generation
Pass any movie name:
```bash
python generate_movie_short.py --movie "Inception"
```
Uses Groq / DeepSeek to generate a complete viral 50-55s movie breakdown formatted for MovieGyan storytelling.

---

## 🚀 Usage

### 1. Cloud Dry Run
Run the render from GitHub Actions with `dry_run=true`; do not run FFmpeg or video rendering on a phone. The workflow uploads the MP4, thumbnail, subtitles, render manifest, and `ffprobe` report to the `movie-explanation-short` artifact.

### 2. Authorized Media Sources
The default workflow uses only an explicitly configured `trailer_url` from the catalog, an authorized local file passed by the operator, or a neutral generated fallback. Trailer search, private Google Drive media, external poster artwork, and BGM are disabled by default and must be explicitly enabled for a rights-cleared source.

```bash
python generate_movie_short.py --movie "Inception" --video-file /path/to/authorized_inception.mp4 --dry-run
```

### 3. Publishing Safety
- Keep the default `PRIVACY_STATUS` as `unlisted` until the content and source rights have been reviewed.
- Use original or licensed narration, footage, and music.
- Do not treat a Fair Use notice, watermark, or color grade as a guarantee of platform approval.
- Add a channel watermark only when it represents your actual channel identity:
  ```bash
  python generate_movie_short.py --movie "Fall" --watermark "@CinemaInsights"
  ```
- Custom voiceover remains supported for cloud runs that provide an authorized audio file.

### 4. View Catalog and Upload History
```bash
python generate_movie_short.py --list
```

### 3. Run Pipeline Unit Tests
```bash
python test_movie_pipeline.py
python test_pipeline.py
python test_workflow_contract.py
```

### 4. Upload to YouTube
Provide your YouTube OAuth tokens in `.env`:
```env
CLIENT_ID=your_client_id
CLIENT_SECRET=your_client_secret
REFRESH_TOKEN=your_refresh_token
PRIVACY_STATUS=unlisted
```
Run:
```bash
python generate_movie_short.py --movie "The Platform" --upload
```

---

## 🤖 GitHub Actions Automation

Trigger manually or on schedule via `.github/workflows/daily_clip.yml`:
1. Go to **GitHub Repo → Actions → 🎬 Auto Movie Explanation Shorts (MovieGyan Style)**.
2. Click **Run workflow**.
3. Optionally enter:
   - `movie_name`: e.g. `Interstellar` (or leave blank to auto-rotate from catalog).
   - `lang`: `en` or `hi`.
   - `dry_run`: `true` to test render and inspect video artifact without uploading.
4. Download the rendered MP4, thumbnail, subtitles, manifest, diagnostics, and logs from the workflow **Artifacts** (`movie-explanation-short`).
