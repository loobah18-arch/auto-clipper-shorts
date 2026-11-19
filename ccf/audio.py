"""Loudness spikes: find moments in videos that have no chat (GTA 6 gameplay uploads, trailers,
YouTube videos with chat replay off). Explosions, screaming and crashes are loud *relative to the
minutes around them*, the same local-baseline trick the chat detector uses."""
import re
import subprocess


def loudness_per_second(audio_path):
    """List of RMS dB per second of audio."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path), "-vn",
           "-af", "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,"
                  "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
           "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    vals = []
    for m in re.finditer(r"RMS_level=(-?[\d.]+|-inf)", out):
        v = m.group(1)
        vals.append(-90.0 if v == "-inf" else max(-90.0, float(v)))
    return vals


def loudness_buckets(audio_path, bucket=10):
    """Per bucket: a pseudo 'message count' (0..~100) from the loudest 3 seconds in the bucket,
    so the chat scorer can be reused unchanged."""
    per_sec = loudness_per_second(audio_path)
    out = []
    for i in range(0, len(per_sec), bucket):
        win = sorted(per_sec[i:i + bucket], reverse=True)[:3]
        if not win:
            break
        db = sum(win) / len(win)
        out.append(max(0.0, (db + 60.0) * 2.0))   # -60 dB -> 0, -10 dB -> 100
    return out


def find_audio_peak(audio_path, target_offset=20.0, window_radius=4.0):
    """Find the exact loudest second (RMS dB) within [target_offset - window_radius, target_offset + window_radius].
    Returns (peak_offset_seconds, peak_rms_db).
    """
    levels = loudness_per_second(audio_path)
    if not levels:
        return float(target_offset), -60.0
    start_sec = max(0, int(target_offset - window_radius))
    end_sec = min(len(levels), int(target_offset + window_radius + 1))
    if start_sec >= end_sec:
        return float(target_offset), -60.0
    window = levels[start_sec:end_sec]
    best_idx = max(range(len(window)), key=lambda i: window[i])
    peak_sec = start_sec + best_idx
    return float(peak_sec), window[best_idx]

