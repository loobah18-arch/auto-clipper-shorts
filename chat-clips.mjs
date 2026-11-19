#!/usr/bin/env node
// chat-clips: find the best moments of a Twitch stream from its chat, and cut them as vertical clips.
//
// 1. Get the chat:   TwitchDownloaderCLI chatdownload --id <VOD> -o chat.json
// 2. Find + cut:     node chat-clips.mjs chat.json --vod <VOD>        (downloads only the clip pieces)
//              or:   node chat-clips.mjs chat.json --video vod.mp4    (cuts from a video you already have)
//              or:   node chat-clips.mjs chat.json                    (just lists the moments)
//
// Needs: Node 18+, ffmpeg on PATH (for cutting), TwitchDownloaderCLI on PATH or --tdl <path> (for --vod).
// Free, MIT. Only clip creators who allow it.

import { readFileSync, existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';

const args = process.argv.slice(2);
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : def;
};
const hasFlag = (name) => args.includes(`--${name}`);

const chatFile = args.find((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
if (!chatFile || hasFlag('help') || hasFlag('h')) {
  console.log(`
The Chat Clip Finder 🎬
Let Twitch chat find the best moments of a stream, then cut them into vertical 9:16 clips.

Usage:
  node chat-clips.mjs <chat.json> [options]

Options:
  --vod <URL|ID>      Twitch VOD URL or ID (downloads only the 30s clips needed)
  --video <file.mp4>  Cut from a local video file you already downloaded
  --emotes            Filter by reaction emotes/keywords (LMAO, KEKW, 💀, WTF, NO WAY, etc.)
  --min-score <num>   Minimum spike multiplier threshold (default: 1.0)
  --layout <type>     Vertical format: 'stack' (cam top, game bottom), 'blur' (ambient fill), 'crop' (center zoom)
  --cam <pos>         Webcam location for 'stack': 'bottom-left' (default), 'top-left', 'bottom-right', 'top-right'
  --top <number>      Number of clips to make (default: 5)
  --before <seconds>  Seconds before spike to start clip (default: 20)
  --after <seconds>   Seconds after spike to end clip (default: 10)
  --gap <seconds>     Minimum seconds between two clips (default: 90)
  --bucket <seconds>  Window size for counting chat messages (default: 10)
  --out <dir>         Output directory for clips and metadata (default: clips)
  --tdl <path>        Path to TwitchDownloaderCLI executable
  --dry-run           Only find moments and save moments.json, do not cut video
  --help, -h          Show this help message

Examples:
  node chat-clips.mjs chat.json --emotes --layout stack
  node chat-clips.mjs chat.json --vod https://www.twitch.tv/videos/1234567890 --top 3
`);
  process.exit(chatFile ? 0 : 1);
}

const BUCKET = Number(opt('bucket', 10)); // seconds per slice
const TOP = Number(opt('top', 5));        // how many clips
const BEFORE = Number(opt('before', 20)); // chat reacts AFTER the moment, so start earlier
const AFTER = Number(opt('after', 10));
const GAP = Number(opt('gap', 90));       // min seconds between two clips
const OUT = opt('out', 'clips');
const VOD = opt('vod');
const VIDEO = opt('video');
const LAYOUT = opt('layout', 'stack');    // 'stack' (cam top, game bottom), 'blur' (ambient 16:9), or 'crop'
const CAM = opt('cam', 'bottom-left');
const MIN_SCORE = Number(opt('min-score', 1.0));
const FILTER_EMOTES = hasFlag('emotes') || hasFlag('filter-emotes');
const DRY_RUN = hasFlag('dry-run');

const REACTION_RE = /(?<![a-z0-9])(lmao+|lmfao|kekw|omegalul|lol+|rofl|dead|icant|i cant|wtf|no\s*way+|holy|omg|yooo+|clutch|fail|cooked|doxxed|wasted|busted|pog(gers)?|w|l)(?![a-z0-9])|💀|😭|🤣|😂|🔥|😳|🤯/i;

// Smart binary detection for TwitchDownloaderCLI
const resolveTDL = () => {
  const custom = opt('tdl');
  if (custom) return custom;
  if (existsSync('./TwitchDownloaderCLI')) return './TwitchDownloaderCLI';
  if (existsSync('./TwitchDownloaderCLI.exe')) return './TwitchDownloaderCLI.exe';
  if (process.platform === 'win32') return 'TwitchDownloaderCLI.exe';
  return 'TwitchDownloaderCLI';
};
const TDL = resolveTDL();

// --- 1. Read chat ---
if (!existsSync(chatFile)) {
  console.error(`Error: Chat file not found: ${chatFile}`);
  process.exit(1);
}

let chat;
try {
  chat = JSON.parse(readFileSync(chatFile, 'utf8'));
} catch (err) {
  console.error(`Error parsing JSON from ${chatFile}: ${err.message}`);
  process.exit(1);
}

const comments = Array.isArray(chat) ? chat : (chat.comments ?? []);
const times = [];
for (const c of comments) {
  const t = typeof c === 'number' ? c : c.content_offset_seconds;
  const body = c?.message?.body ?? '';
  if (typeof t === 'number' && !isNaN(t) && t >= 0) {
    if (FILTER_EMOTES) {
      if (REACTION_RE.test(body)) times.push(t);
    } else {
      times.push(t);
    }
  }
}

if (times.length === 0) {
  console.error(FILTER_EMOTES ? 'Error: No messages matched reaction emotes/keywords.' : 'Error: No chat messages found.');
  process.exit(1);
}

// --- 2. Count messages per bucket ---
const length = Math.max(...times, chat.video?.length ?? 0);
const totalBuckets = Math.ceil(length / BUCKET) + 1;
const counts = new Array(totalBuckets).fill(0);
for (const t of times) {
  const idx = Math.floor(t / BUCKET);
  if (idx < totalBuckets) counts[idx]++;
}

// --- 3. Score calculation (Local moving median baseline) ---
const median = (arr) => {
  if (arr.length === 0) return 0;
  const s = [...arr].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)] ?? 0;
};

const around = Math.max(1, Math.round(300 / BUCKET));
const scores = [];
for (let i = 0; i < counts.length; i++) {
  const left = Math.max(0, i - around);
  const right = Math.min(counts.length, i + around + 1);
  const base = median(counts.slice(left, right));
  scores.push(counts[i] / (base + 1));
}

// --- 4. Find top peaks ---
const order = Array.from({ length: counts.length }, (_, k) => k).sort((a, b) => scores[b] - scores[a]);
const peaks = [];
for (const idx of order) {
  if (peaks.length >= TOP) break;
  if (counts[idx] < 5 || scores[idx] < MIN_SCORE) continue;
  const t = idx * BUCKET;
  if (peaks.some((p) => Math.abs(p.t - t) < GAP)) continue;
  peaks.push({ t, msgs: counts[idx], score: Number(scores[idx].toFixed(1)) });
}
peaks.sort((a, b) => a.t - b.t);

// --- 5. Print ASCII graph and top moments ---
const hms = (sec) => {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};
const bars = ' ▁▂▃▄▅▆▇█';
const W = 60, per = Math.ceil(counts.length / W);
const cols = Array.from({ length: Math.ceil(counts.length / per) }, (_, k) => Math.max(...counts.slice(k * per, (k + 1) * per)));
const top = Math.max(...cols) || 1;

const modeText = FILTER_EMOTES ? ' (Reaction Emote Filter Active)' : '';
console.log(`\n💬 ${times.length} chat messages${modeText}, ⏱ ${hms(length)} of stream\n`);
console.log(cols.map((c) => bars[Math.round((c / top) * (bars.length - 1))]).join(''));
console.log(`0:00:00${' '.repeat(Math.max(1, cols.length - 14))}${hms(length)}\n`);

if (peaks.length === 0) {
  console.log(`No moments reached the minimum multiplier threshold of ${MIN_SCORE}x.`);
  process.exit(0);
}

console.log('Top Moments:');
peaks.forEach((p, k) => {
  const clipStart = Math.max(0, p.t - BEFORE);
  const clipEnd = p.t + AFTER;
  console.log(`#${k + 1}  ${hms(p.t)}  ${p.msgs} msgs in ${BUCKET}s  (${p.score}x normal)  -> clip ${hms(clipStart)} to ${hms(clipEnd)}`);
});

mkdirSync(OUT, { recursive: true });
const momentsData = peaks.map((p, k) => {
  const clipStart = Math.max(0, p.t - BEFORE);
  const clipEnd = p.t + AFTER;
  return {
    rank: k + 1,
    t: p.t,
    timestamp: hms(p.t),
    msgs: p.msgs,
    score: p.score,
    start: clipStart,
    end: clipEnd,
    clipStart: hms(clipStart),
    clipEnd: hms(clipEnd),
    duration: clipEnd - clipStart,
    clipFile: `clip_${String(k + 1).padStart(2, '0')}.mp4`,
  };
});
writeFileSync(join(OUT, 'moments.json'), JSON.stringify(momentsData, null, 2));
console.log(`\n✅ Saved moment data to ${join(OUT, 'moments.json')}.`);

if (DRY_RUN) {
  console.log('Dry run requested; skipping video cutting.');
  process.exit(0);
}

if (!VOD && !VIDEO) {
  console.log('To render clips, add --vod <ID/URL> or --video <file.mp4>');
  process.exit(0);
}

// --- 6. Cut vertical clips ---
const run = (cmd, cmdArgs) => {
  const r = spawnSync(cmd, cmdArgs, { stdio: 'inherit' });
  if (r.status !== 0) {
    throw new Error(`Command failed: ${cmd} ${cmdArgs.join(' ')}`);
  }
};

const getFilterArgs = () => {
  if (LAYOUT === 'crop') {
    return [
      '-vf',
      'crop=w=floor(ih*9/16/2)*2:h=ih:x=(iw-ow)/2:y=0,scale=1080:1920,setsar=1',
    ];
  }
  if (LAYOUT === 'stack') {
    // Cam presets
    const CAM_PRESETS = {
      'bottom-left': { cx: 0, cy: 240, cw: 460, ch: 360 },
      'top-left': { cx: 0, cy: 0, cw: 460, ch: 360 },
      'bottom-right': { cx: 1460, cy: 240, cw: 460, ch: 360 },
      'top-right': { cx: 1460, cy: 0, cw: 460, ch: 360 },
    };
    const c = CAM_PRESETS[CAM] || CAM_PRESETS['bottom-left'];
    return [
      '-filter_complex',
      `[0:v]crop=w=${c.cw}:h=${c.ch}:x=${c.cx}:y=${c.cy},scale=1080:700,fps=30[cam];` +
      `[0:v]crop=w=floor(ih*1080/1220/2)*2:h=ih:x=(iw-ow)/2:y=0,scale=1080:1220,fps=30[game];` +
      `[cam][game]vstack=inputs=2[v_stacked];` +
      `[v_stacked]drawbox=x=0:y=698:w=1080:h=4:color=black@0.8:t=fill[v_out]`,
      '-map', '[v_out]',
      '-map', '0:a?',
    ];
  }
  return [
    '-filter_complex',
    '[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15,fps=30[bg];[0:v]scale=1080:-1,fps=30[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_out]',
    '-map', '[v_out]',
    '-map', '0:a?',
  ];
};

const CODEC_OPTS = [
  '-c:v', 'libx264',
  '-preset', 'veryfast',
  '-crf', '20',
  '-c:a', 'aac',
  '-b:a', '192k',
];

momentsData.forEach((p) => {
  const out = join(OUT, p.clipFile);
  console.log(`\n✂️ Cutting #${p.rank} -> ${out} (${p.clipStart} to ${p.clipEnd}, ${p.duration}s, layout: ${LAYOUT})`);

  if (VIDEO) {
    run('ffmpeg', [
      '-y',
      '-loglevel', 'error',
      '-ss', String(p.start),
      '-t', String(p.duration),
      '-i', VIDEO,
      ...getFilterArgs(),
      ...CODEC_OPTS,
      out,
    ]);
  } else {
    const piece = join(OUT, `piece_${p.rank}.mp4`);
    console.log(`  Downloading stream segment with ${TDL}...`);
    run(TDL, [
      'videodownload',
      '--id', VOD,
      '-b', p.clipStart,
      '-e', p.clipEnd,
      '-o', piece,
    ]);
    console.log(`  Rendering 9:16 vertical 1080x1920 (${LAYOUT}) with ffmpeg...`);
    run('ffmpeg', [
      '-y',
      '-loglevel', 'error',
      '-i', piece,
      ...getFilterArgs(),
      ...CODEC_OPTS,
      out,
    ]);
    rmSync(piece, { force: true });
  }
  console.log(`  ✅ Saved: ${out}`);
});

console.log(`\n🎉 Finished! Processed ${momentsData.length} clips in ${OUT}/`);
