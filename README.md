# 🎬 Auto Movie Explanation Shorts (MovieGyan & Movie Insight Style)

An automated YouTube Shorts channel engine that produces high-retention 50-55 second **Movie Explanations, Plot Breakdowns, and Twist Reveals** in the signature style of viral channels like **MovieGyan** and **Movie Insight Hindi**.

Generates neural narration (`edge-tts`), dynamic word-level neon karaoke subtitles (`.ass`), auto-sources official movie trailer clips (`yt-dlp`), slices fast 2.5–3.2s scene cuts, renders a 9:16 vertical split/blurred layout via FFmpeg, and uploads to YouTube with SEO metadata and Fair Use attribution — **100% Free & Automated via GitHub Actions**.

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
- **Audio Mix**: Deep cinematic narrator voiceover (0dB) + subtle suspense/mystery thriller BGM (-18dB).

---

## ⚡ 100% Free Tech Stack

| Component | Technology | Cost |
|---|---|---|
| **Movie Script Engine** | Curated Catalog + Groq (Llama 3.3 70B) / DeepSeek | Free Tier |
| **Neural Voiceover** | `edge-tts` (ChristopherNeural / MadhurNeural) | 100% Free |
| **Footage Extraction** | `yt-dlp` (Official trailer & clip search) | Free / Open-Source |
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

### 1. Local Testing (Dry Run)
Generate a movie explanation Short without uploading:
```bash
# Rotate to next movie from catalog:
python generate_movie_short.py --dry-run

# Specific movie:
python generate_movie_short.py --movie "Shutter Island" --dry-run

# Hindi language voiceover (hi-IN-MadhurNeural):
python generate_movie_short.py --movie "The Platform" --lang hi --dry-run
```

### 2. Sourcing Raw Footage & Scene Packs
The pipeline provides 3 flexible ways to source movie clips:
1. **Automated Studio Trailers (Default):** Pipeline queries YouTube via `yt-dlp` for `"{Movie Title} official trailer 1080p"`, downloads, and auto-slices dynamic 2.5–3.2s cuts across the film.
2. **Local High-Res Movie File:** Pass any `.mp4` or `.mkv` file directly:
   ```bash
   python generate_movie_short.py --movie "Inception" --video-file /path/to/inception_1080p.mp4
   ```
3. **Editor Scene Packs:** Search YouTube for `"[Movie] scene pack 4k no watermark"`, download with `yt-dlp`, and pass it via `--video-file`.

### 3. Maximizing Monetization (YPP) Approval
To ensure approval under YouTube's Partner Program (Reused Content policies):
1. **Critical Analysis Scripts:** The AI script engine focuses on *psychological theories, directorial symbolism, and hidden details* rather than dry summaries.
2. **Channel Watermark & Branding:** Add your channel handle to prove authentic channel identity:
   ```bash
   python generate_movie_short.py --movie "Fall" --watermark "@CinemaInsights"
   ```
3. **Custom Human Voiceover Support:** Record your voiceover on your phone or mic and pass it in:
   ```bash
   python generate_movie_short.py --movie "Fall" --audio-file voiceover.mp3
   ```
   The pipeline will automatically synchronize dynamic karaoke subtitles to your real voice!
4. **Cinematic Color Grading:** Applied automatically to the foreground footage to provide a unique digital hash and enhanced contrast.

### 4. View Catalog and Upload History
```bash
python generate_movie_short.py --list
```

### 3. Run Pipeline Unit Tests
```bash
python test_movie_pipeline.py
python test_pipeline.py
```

### 4. Upload to YouTube
Provide your YouTube OAuth tokens in `.env`:
```env
CLIENT_ID=your_client_id
CLIENT_SECRET=your_client_secret
REFRESH_TOKEN=your_refresh_token
PRIVACY_STATUS=public
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
4. Download the rendered MP4 & Thumbnail directly from the workflow **Artifacts** (`movie-explanation-short`).
