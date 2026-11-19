"""Find the streamer's webcam overlay: a face that stays in the same place, near the edge of the
frame, in most sampled frames. Game characters (GTA cutscenes!) move around, so they don't win.
Uses the YuNet detector (OpenCV Model Zoo, MIT) shipped in assets/models.

Returns a crop box (x, y, w, h) in source pixels, or None when there is no cam (pure gameplay)."""
from pathlib import Path

MODEL = Path(__file__).resolve().parent.parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"

PRESETS = {  # fractions of the frame (x, y, w, h) for a typical 1/4-width cam overlay
    "top-left": (0.0, 0.0, 0.25, 0.33),
    "top-right": (0.75, 0.0, 0.25, 0.33),
    "bottom-left": (0.0, 0.67, 0.25, 0.33),
    "bottom-right": (0.75, 0.67, 0.25, 0.33),
}


def preset_box(pos, src_w, src_h):
    fx, fy, fw, fh = PRESETS[pos]
    return int(fx * src_w), int(fy * src_h), int(fw * src_w), int(fh * src_h)


def _frames(video, n=16):
    import cv2
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    for k in range(n):
        if total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (k + 0.5) / n))
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return frames


def detect(video, panel_aspect=1080 / 700):
    """Auto-detect the cam box. None if OpenCV is missing or no stable cam face is found."""
    try:
        import cv2
    except ImportError:
        return None
    if not MODEL.exists() or not hasattr(cv2, "FaceDetectorYN"):
        return None
    frames = _frames(video)
    if len(frames) < 6:
        return None
    src_h, src_w = frames[0].shape[:2]
    scale = 640 / src_w
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (640, int(src_h * scale)), 0.75, 0.3, 50)
    faces = []
    for f in frames:
        small = cv2.resize(f, (640, int(src_h * scale)))
        _, res = det.detect(small)
        faces.append([] if res is None else [(r[0] / scale, r[1] / scale, r[2] / scale, r[3] / scale)
                                             for r in res])
    tol = 0.04 * src_w
    best, hits_best = None, 0
    for fs in faces:
        for (x, y, w, h) in fs:
            cx, cy = x + w / 2, y + h / 2
            # 1. Overlay webcam face size: not too big, not too small
            if not (0.05 * src_h <= h <= 0.28 * src_h):
                continue
            # 2. Must be in one of the 4 corners (top-left, top-right, bottom-left, bottom-right)
            in_left = cx < 0.30 * src_w
            in_right = cx > 0.70 * src_w
            in_top = cy < 0.40 * src_h
            in_bottom = cy > 0.60 * src_h
            if not ((in_left and in_top) or (in_right and in_top) or (in_left and in_bottom) or (in_right and in_bottom)):
                continue
            # 3. Must be stable in position and size across frames
            hits = sum(any(abs(a + c / 2 - cx) < tol and abs(b + d / 2 - cy) < tol and abs(d - h) < 0.08 * src_h
                           for a, b, c, d in g) for g in faces)
            if hits > hits_best:
                best, hits_best = (cx, cy, h), hits
    # Real webcam face overlay is present in >= 65% of sampled frames
    if not best or hits_best < 0.65 * len(frames):
        return None
    cx, cy, fh = best
    bh = min(src_h, 2.6 * fh)
    bw = min(src_w, bh * panel_aspect)
    x = max(0, min(src_w - bw, cx - bw / 2))
    y = max(0, min(src_h - bh, cy - bh * 0.45))
    return int(x) // 2 * 2, int(y) // 2 * 2, int(bw) // 2 * 2, int(bh) // 2 * 2
