"""Find the streamer's webcam: a face that stays in the same place across the clip.

How it works (YuNet face detector, OpenCV Model Zoo, MIT, shipped in assets/models):
  1. Sample ~10 frames spread evenly across the clip window itself.
  2. Detect faces over the WHOLE frame (cams sit in corners, mid-left under alerts, next to chat...).
  3. Cluster the detections by position + size and keep the face that shows up in >= 30% of the
     samples. Game characters (GTA cutscenes!) move around, so they rarely form a stable cluster.
  4. Small/medium stable face (2-12% of frame width)  -> "overlay": a webcam box on top of gameplay.
     The crop is snapped to the overlay's border lines when they are visible (they stay put while the
     gameplay moves), else built around the median face box with padding.
     Big stable face (>= 12% of frame width)          -> "fullscreen": IRL / full-cam layout, rendered
     as a single 9:16 crop centred on the face.
  5. The crop is computed once from the median box and LOCKED for the whole clip (no drifting).

detect() keeps the old contract (overlay crop box or None); analyze() returns the full result."""
from pathlib import Path

MODEL = Path(__file__).resolve().parent.parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"

PRESETS = {  # fractions of the frame (x, y, w, h) for a typical 1/4-width cam overlay
    "top-left": (0.0, 0.0, 0.25, 0.33),
    "top-right": (0.75, 0.0, 0.25, 0.33),
    "bottom-left": (0.0, 0.67, 0.25, 0.33),
    "bottom-right": (0.75, 0.67, 0.25, 0.33),
}

N_SAMPLES = 10
MIN_HIT_FRAC = 0.30          # face must be in >= 30% of the samples
MIN_FACE_W = 0.02            # face width as a fraction of frame width (1080p: ~38 px)
MAX_FACE_W = 0.35
FULLSCREEN_FACE_W = 0.12     # at/above this the cam IS the frame (IRL / fullscreen cam)


def preset_box(pos, src_w, src_h):
    fx, fy, fw, fh = PRESETS[pos]
    return int(fx * src_w), int(fy * src_h), int(fw * src_w), int(fh * src_h)


def _even(v):
    return int(v) // 2 * 2


# ---------------------------------------------------------------- sampling + detection (needs cv2)

def sample_frames(video, n=N_SAMPLES):
    """n frames spread evenly across the whole video (seek by time, robust to bad frame counts)."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur_ms = (total / fps * 1000.0) if total and fps else 0.0
    frames = []
    for k in range(n):
        if dur_ms:
            cap.set(cv2.CAP_PROP_POS_MSEC, dur_ms * (k + 0.5) / n)
        ok, f = cap.read()
        if ok and f is not None:
            frames.append(f)
    cap.release()
    return frames


def detect_faces(frames, det_w=1280, score=0.6):
    """Per frame: list of (x, y, w, h, score) in source pixels, from the whole frame."""
    import cv2
    if not frames:
        return []
    src_h, src_w = frames[0].shape[:2]
    det_w = min(det_w, src_w)
    scale = det_w / src_w
    size = (det_w, int(round(src_h * scale)))
    det = cv2.FaceDetectorYN.create(str(MODEL), "", size, score, 0.3, 50)
    out = []
    for f in frames:
        small = cv2.resize(f, size, interpolation=cv2.INTER_AREA) if scale != 1 else f
        _, res = det.detect(small)
        out.append([] if res is None else
                    [(r[0] / scale, r[1] / scale, r[2] / scale, r[3] / scale, float(r[14])) for r in res])
    return out


# ---------------------------------------------------------------- pure selection logic (unit tested)

def _median(vals):
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def select_face(faces, src_w, src_h, min_hit_frac=MIN_HIT_FRAC, min_w=MIN_FACE_W, max_w=MAX_FACE_W):
    """Pick the stable face across samples. faces = per-sample lists of (x, y, w, h[, score]).

    Returns {"box": median (x, y, w, h), "hits": n, "samples": N, "kind": "overlay"|"fullscreen"} or
    None. A face counts once per sample; it must sit at ~the same place with ~the same size in at least
    min_hit_frac of the samples. Overlay-sized faces win over fullscreen-sized ones (a webcam overlay
    beats a cutscene close-up), then more hits, then bigger faces."""
    n = len(faces)
    if n == 0:
        return None
    import math
    need = max(2 if n >= 4 else 1, math.ceil(min_hit_frac * n - 1e-9))
    tol = 0.04 * src_w
    cands = []
    for fs in faces:
        for f in fs:
            x, y, w, h = f[:4]
            if not (min_w * src_w <= w <= max_w * src_w):
                continue
            cands.append((x, y, w, h))
    best = None
    for (x, y, w, h) in cands:
        cx, cy = x + w / 2, y + h / 2
        members = []
        for fs in faces:
            match = None
            for g in fs:
                a, b, c, d = g[:4]
                if (abs(a + c / 2 - cx) < tol and abs(b + d / 2 - cy) < tol
                        and 0.6 <= c / max(w, 1e-6) <= 1.6):
                    match = (a, b, c, d)
                    break
            if match:
                members.append(match)
        hits = len(members)
        if hits < need:
            continue
        box = tuple(_median([m[i] for m in members]) for i in range(4))
        kind = "fullscreen" if box[2] >= FULLSCREEN_FACE_W * src_w else "overlay"
        key = (kind == "overlay", hits, box[2])
        if best is None or key > best[0]:
            best = (key, {"box": box, "hits": hits, "samples": n, "kind": kind})
    return best[1] if best else None


def padded_cam_box(face, src_w, src_h, panel_aspect=1080 / 700):
    """Cam crop around the (median) face: head-and-shoulders, face slightly above centre."""
    x, y, w, h = face
    cx, cy = x + w / 2, y + h / 2
    bh = min(src_h, 2.4 * h)
    bw = min(src_w, bh * panel_aspect)
    bx = max(0, min(src_w - bw, cx - bw / 2))
    by = max(0, min(src_h - bh, cy - bh * 0.45))
    return _even(bx), _even(by), _even(bw), _even(bh)


def fullscreen_box(face, src_w, src_h):
    """Single-panel 9:16 crop (full height) centred on the face."""
    x, y, w, h = face
    cw = min(src_w, src_h * 9 / 16)
    cx = x + w / 2
    bx = max(0, min(src_w - cw, cx - cw / 2))
    return _even(bx), 0, _even(cw), _even(src_h)


def _plausible_overlay(rect, face, src_w, src_h):
    rx, ry, rw, rh = rect
    fx, fy, fw, fh = face
    if rw <= 0 or rh <= 0:
        return False
    if not (rx <= fx + fw * 0.2 and fx + fw * 0.8 <= rx + rw and ry <= fy + fh * 0.2 and fy + fh * 0.8 <= ry + rh):
        return False          # must contain the face
    if not (0.8 <= rw / rh <= 2.6):
        return False
    if rw * rh > 0.30 * src_w * src_h:
        return False          # bigger than any overlay: not a border we found
    if rw < 1.6 * fw or rh < 1.4 * fh:
        return False          # tighter than the face: snapped to the face itself
    return True


def snap_overlay(edge_x, edge_y, face, src_w, src_h, ratio=2.5, floor=8.0):
    """Snap to the webcam overlay's border lines.

    edge_x / edge_y: |d/dx| and |d/dy| averaged over the sampled frames (numpy, any scale; coords
    rescaled). Borders of an overlay are long straight lines that stay put while gameplay moves, so they
    stand out in the time-average. For each side we search outward from the face for the strongest line
    along the face's span; a side with no clear line falls back to the frame edge when the search
    reaches it. Returns (x, y, w, h) in source pixels or None."""
    import numpy as np
    H, W = edge_x.shape
    sx, sy = W / src_w, H / src_h
    fx, fy, fw, fh = face[0] * sx, face[1] * sy, face[2] * sx, face[3] * sy
    cx, cy = fx + fw / 2, fy + fh / 2

    def line_scores(arr, axis_len, lo, hi, span_lo, span_hi, vertical):
        lo, hi = int(max(0, lo)), int(min(axis_len - 1, hi))
        s0, s1 = int(max(0, span_lo)), int(min((H if vertical else W), span_hi))
        if hi <= lo or s1 - s0 < 3:
            return None, None
        if vertical:
            prof = arr[s0:s1, lo:hi + 1].mean(axis=0)
        else:
            prof = arr[lo:hi + 1, s0:s1].mean(axis=1)
        return lo, prof

    def side(arr, axis_len, lo, hi, span_lo, span_hi, vertical, edge_val, outward_up):
        """Scan outward from the face; the first line clearly stronger than everything between it and
        the face is the border (chat text / minimaps further out can be stronger, but come later)."""
        start, prof = line_scores(arr, axis_len, lo, hi, span_lo, span_hi, vertical)
        reaches = lo <= 1 or hi >= axis_len - 2
        if prof is None or len(prof) < 4:
            return edge_val if reaches else None
        order = list(range(len(prof)))[::-1] if outward_up else list(range(len(prof)))
        seq = [float(prof[i]) for i in order]
        for j in range(3, len(seq)):
            inside = float(np.median(seq[:j]))
            if seq[j] >= max(floor, ratio * inside):
                # take the local maximum of this line (borders can be 2-3 px thick)
                k = max(range(j, min(len(seq), j + 3)), key=lambda t: seq[t])
                return start + order[k]
        return edge_val if reaches else None

    left = side(edge_x, W, fx - 5 * fw, fx - 0.25 * fw, cy - 1.0 * fh, cy + 1.2 * fh, True, 0, True)
    right = side(edge_x, W, fx + 1.25 * fw, fx + 6 * fw, cy - 1.0 * fh, cy + 1.2 * fh, True, W - 1, False)
    top = side(edge_y, H, fy - 3.5 * fh, fy - 0.2 * fh, cx - 1.2 * fw, cx + 1.2 * fw, False, 0, True)
    bottom = side(edge_y, H, fy + 1.5 * fh, fy + 5 * fh, cx - 1.2 * fw, cx + 1.2 * fw, False, H - 1, False)
    def src(v, edge_hi, scale, pad):
        if v is None:
            return None
        if v <= 0:
            return 0.0
        if v >= edge_hi:
            return float(src_w if scale is sx else src_h)
        return (v + pad) / scale

    return {"left": src(left, W - 1, sx, 2), "right": src(right, W - 1, sx, -2),
            "top": src(top, H - 1, sy, 2), "bottom": src(bottom, H - 1, sy, -2)}


def rect_from_sides(sides, face, src_w, src_h):
    if not sides or any(sides.get(k) is None for k in ("left", "right", "top", "bottom")):
        return None
    x0, x1, y0, y1 = sides["left"], sides["right"], sides["top"], sides["bottom"]
    rect = (_even(x0), _even(y0), _even(x1 - x0), _even(y1 - y0))
    return rect if _plausible_overlay(rect, face, src_w, src_h) else None


def snap_by_motion(motion, face, src_w, src_h, win=4, min_outer=14.0, max_inner=18.0, ratio=2.5):
    """Snap to the overlay using motion: the cam's background (the streamer's room) barely changes
    between samples while the gameplay around it changes a lot. motion = per-pixel std over the
    sampled frames (numpy, any scale). Scanning outward from the face, the border is the first place
    where a quiet strip (inside) meets a busy strip (outside). Reaching the frame edge while still
    quiet means the overlay touches that edge. Returns {"left", "right", "top", "bottom"} in source
    pixels (None = not found on that side)."""
    import numpy as np
    H, W = motion.shape
    sx, sy = W / src_w, H / src_h
    fx, fy, fw, fh = face[0] * sx, face[1] * sy, face[2] * sx, face[3] * sy
    cx, cy = fx + fw / 2, fy + fh / 2

    def prof(vertical, span_lo, span_hi):
        n = H if vertical else W
        a, b = int(max(0, span_lo)), int(min(n, span_hi))
        if b - a < 3:
            return None
        return motion[a:b, :].mean(axis=0) if vertical else motion[:, a:b].mean(axis=1)

    def scan(p, start, stop, step, edge_val):
        n = len(p)
        i = int(start)
        while 0 <= i < n and (i - stop) * step < 0:
            inner = p[i + 1:i + 1 + win] if step < 0 else p[max(0, i - win):i]
            outer = p[max(0, i - win):i] if step < 0 else p[i + 1:i + 1 + win]
            if len(inner) and len(outer):
                im, om = float(inner.mean()), float(outer.mean())
                if om >= max(min_outer, ratio * im) and im < max_inner:
                    # the border is where the profile crosses halfway from quiet to busy
                    mid = (im + om) / 2
                    j = i
                    while 0 <= j + step < n and abs(j - i) <= 2 * win and p[j + step] < mid:
                        j += step
                    return j
            i += step
        tail = p[max(0, int(stop) - win):int(stop) + 1] if step < 0 else p[int(stop):int(stop) + win]
        if (stop <= 1 or stop >= n - 2) and len(tail) and float(np.mean(tail)) < max_inner:
            return edge_val
        return None

    pv = prof(True, cy - 0.8 * fh, cy + 1.0 * fh)
    ph = prof(False, cx - 1.0 * fw, cx + 1.0 * fw)
    if pv is None or ph is None:
        return {}
    left = scan(pv, fx - 0.3 * fw, max(0, fx - 7 * fw), -1, 0)
    right = scan(pv, fx + 1.3 * fw, min(W - 1, fx + 8 * fw), 1, W - 1)
    top = scan(ph, fy - 0.2 * fh, max(0, fy - 4 * fh), -1, 0)
    bottom = scan(ph, fy + 1.5 * fh, min(H - 1, fy + 6 * fh), 1, H - 1)
    def src(v, edge_hi, scale, pad):
        if v is None:
            return None
        if v <= 0:
            return 0.0
        if v >= edge_hi:
            return float(src_w if scale is sx else src_h)
        return (v + pad) / scale

    return {"left": src(left, W - 1, sx, 1), "right": src(right, W - 1, sx, 1),
            "top": src(top, H - 1, sy, 1), "bottom": src(bottom, H - 1, sy, 1)}


def motion_map(frames, width=640):
    import cv2
    import numpy as np
    src_h, src_w = frames[0].shape[:2]
    size = (width, int(round(src_h * width / src_w)))
    g = np.stack([cv2.cvtColor(cv2.resize(f, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                  .astype(np.float32) for f in frames])
    return g.std(axis=0)


def edge_maps(frames, width=640):
    import cv2
    import numpy as np
    src_h, src_w = frames[0].shape[:2]
    size = (width, int(round(src_h * width / src_w)))
    ax = np.zeros((size[1], size[0]), np.float32)
    ay = np.zeros_like(ax)
    for f in frames:
        g = cv2.cvtColor(cv2.resize(f, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
        ax += np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        ay += np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    return ax / len(frames), ay / len(frames)


# ---------------------------------------------------------------- public API

def analyze(video, panel_aspect=1080 / 700, n=N_SAMPLES):
    """{"kind": "overlay"|"fullscreen", "box": crop (x, y, w, h), "face": median face, "hits", "samples",
    "snapped": bool} or None when OpenCV/model is missing or there is no stable face."""
    try:
        import cv2
    except ImportError:
        return None
    if not MODEL.exists() or not hasattr(cv2, "FaceDetectorYN"):
        return None
    frames = sample_frames(video, n)
    if len(frames) < 3:
        return None
    src_h, src_w = frames[0].shape[:2]
    sel = select_face(detect_faces(frames), src_w, src_h)
    if not sel:
        return None
    face = sel["box"]
    if sel["kind"] == "fullscreen":
        return {"kind": "fullscreen", "box": fullscreen_box(face, src_w, src_h), "face": face,
                "hits": sel["hits"], "samples": sel["samples"], "snapped": False}
    rect = None
    try:
        # per side: motion border first (robust inside a busy room), edge line second
        by_motion = snap_by_motion(motion_map(frames), face, src_w, src_h) or {}
        ex, ey = edge_maps(frames)
        by_edge = snap_overlay(ex, ey, face, src_w, src_h) or {}
        sides = {k: by_motion.get(k) if by_motion.get(k) is not None else by_edge.get(k)
                 for k in ("left", "right", "top", "bottom")}
        rect = rect_from_sides(sides, face, src_w, src_h)
    except Exception as e:  # never let snapping kill the clip: fall back to the padded face box
        print(f"   (cam border snap failed: {e})", flush=True)
        rect = None
    box = rect if rect else padded_cam_box(face, src_w, src_h, panel_aspect)
    return {"kind": "overlay", "box": box, "face": face, "hits": sel["hits"], "samples": sel["samples"],
            "snapped": bool(rect)}


def detect(video, panel_aspect=1080 / 700):
    """Overlay cam crop box (x, y, w, h) or None (old contract: no fullscreen handling)."""
    r = analyze(video, panel_aspect)
    return r["box"] if r and r["kind"] == "overlay" else None
