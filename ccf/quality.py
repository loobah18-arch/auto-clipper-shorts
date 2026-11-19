"""Cheap visual sanity check for a candidate moment: skip dark / frozen screens (menus, loading,
pause screens, 'be right back') before spending Whisper and render time on them.

One ffmpeg pass decodes the segment at 2 fps as tiny grayscale frames; the facecam box is masked out
so a talking face over a black menu still counts as static gameplay."""
import subprocess

W, H = 160, 90


def frame_stats(video, cam=None, src_w=1920, src_h=1080, fps=2):
    """(mean_luma 0-255, mean_abs_frame_diff 0-255, frames) for the gameplay part of the segment."""
    try:
        import numpy as np
    except ImportError:
        return None
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video), "-vf",
                          f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    n = len(raw) // (W * H)
    if n < 2:
        return None
    frames = np.frombuffer(raw[:n * W * H], np.uint8).reshape(n, H, W).astype(np.float32)
    mask = np.ones((H, W), bool)
    if cam:
        cx, cy, cw, ch = cam
        x0, y0 = int(cx / src_w * W), int(cy / src_h * H)
        x1, y1 = int((cx + cw) / src_w * W) + 1, int((cy + ch) / src_h * H) + 1
        mask[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = False
    if mask.sum() < 0.2 * W * H:
        mask[:] = True
    luma = float(frames[:, mask].mean())
    diff = float(np.abs(np.diff(frames, axis=0))[:, mask].mean())
    return luma, diff, n


def is_dull(video, cam=None, src_w=1920, src_h=1080, min_luma=32.0, min_motion=1.6):
    """(True, reason) when the gameplay is mostly dark or barely moving; (False, info) otherwise."""
    st = frame_stats(video, cam, src_w, src_h)
    if st is None:
        return False, "no stats (check skipped)"
    luma, diff, n = st
    info = f"luma {luma:.0f}/255, motion {diff:.2f}, {n} frames"
    if luma < min_luma:
        return True, "too dark (" + info + ")"
    if diff < min_motion:
        return True, "static screen (" + info + ")"
    return False, info
