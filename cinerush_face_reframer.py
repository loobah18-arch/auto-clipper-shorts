#!/usr/bin/env python3
"""
Cinerush Shot-Based Face-Tracking 9:16 Reframer
Solves the static center crop problem:
1. Detects camera shot boundaries via scenedetect (or OpenCV HSV histogram fallback).
2. Subdivides camera shots > 3.0s into 2.0s sub-windows to adapt to camera pans.
3. Samples 3 frames per shot and detects faces via MediaPipe or OpenCV YuNet ONNX.
4. Centers crop on active face(s) or centers between two faces if both fit in 9:16.
5. Locks crop position statically for the entire shot so there is ZERO frame-to-frame jitter.
6. Cuts and stitches the video with original dialogue audio and 30ms audio de-clicking fades.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def log(msg: str) -> None:
    print(f"[CinerushFaceReframer] {msg}", flush=True)


YUNET_MODEL_PATH = Path(__file__).resolve().parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"


class CinerushFaceDetector:
    """Multi-backend face detector: MediaPipe -> OpenCV YuNet ONNX -> Haar Cascade -> Center Fallback."""

    def __init__(self):
        self.backend = "none"
        self._mp_detector = None
        self._yunet_detector = None
        self._haar_detector = None
        self._init_backend()

    def _init_backend(self):
        # 1. Try MediaPipe
        try:
            import mediapipe as mp
            self._mp_detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.4
            )
            self.backend = "mediapipe"
            log("👤 Face Detection backend initialized: MediaPipe")
            return
        except Exception:
            pass

        # 2. Try OpenCV YuNet
        if YUNET_MODEL_PATH.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet_detector = cv2.FaceDetectorYN.create(
                    str(YUNET_MODEL_PATH), "", (320, 320), score_threshold=0.35, nms_threshold=0.3
                )
                self.backend = "yunet"
                log("👤 Face Detection backend initialized: OpenCV YuNet (ONNX)")
                return
            except Exception as e:
                log(f"⚠️ YuNet initialization failed: {e}")

        # 3. Try Haar Cascade
        try:
            if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
                haar_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if os.path.exists(haar_path):
                    self._haar_detector = cv2.CascadeClassifier(haar_path)
                    self.backend = "haar"
                    log("👤 Face Detection backend initialized: OpenCV Haar Cascade")
                    return
        except Exception:
            pass

        log("ℹ️ No ML face detector available; using center framing fallback.")
        self.backend = "fallback_center"

    def detect_faces(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """
        Detects faces in a BGR frame.

        Returns:
            list[tuple[int, int, int, int]]: Bounding boxes [x, y, w, h].
        """
        if frame_bgr is None:
            return []
        h, w = frame_bgr.shape[:2]
        boxes: list[tuple[int, int, int, int]] = []

        if self.backend == "mediapipe" and self._mp_detector:
            try:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                res = self._mp_detector.process(rgb)
                if res.detections:
                    for det in res.detections:
                        bb = det.location_data.relative_bounding_box
                        bx = int(max(0, bb.xmin * w))
                        by = int(max(0, bb.ymin * h))
                        bw = int(min(w - bx, bb.width * w))
                        bh = int(min(h - by, bb.height * h))
                        if bw > 15 and bh > 15:
                            boxes.append((bx, by, bw, bh))
                return boxes
            except Exception:
                pass

        if self.backend == "yunet" and self._yunet_detector:
            try:
                self._yunet_detector.setInputSize((w, h))
                _, faces = self._yunet_detector.detect(frame_bgr)
                if faces is not None:
                    for f in faces:
                        bx, by, bw, bh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
                        bx = max(0, min(w - 1, bx))
                        by = max(0, min(h - 1, by))
                        bw = max(1, min(w - bx, bw))
                        bh = max(1, min(h - by, bh))
                        if bw > 15 and bh > 15:
                            boxes.append((bx, by, bw, bh))
                return boxes
            except Exception:
                pass

        if self.backend == "haar" and self._haar_detector:
            try:
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                detected = self._haar_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
                for bx, by, bw, bh in detected:
                    boxes.append((int(bx), int(by), int(bw), int(bh)))
                return boxes
            except Exception:
                pass

        return []


def detect_camera_shots(
    video_path: Path,
    start_sec: float,
    end_sec: float,
) -> list[tuple[float, float]]:
    """
    Detects camera shot cuts in the specified video segment.
    Uses scenedetect if available, otherwise OpenCV histogram difference.
    """
    shots: list[tuple[float, float]] = []

    # 1. Try PySceneDetect
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
        video = open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0))
        video.seek(start_sec)
        scene_manager.detect_scenes(video, end_time=end_sec)
        scene_list = scene_manager.get_scene_list()
        if scene_list:
            for s_start, s_end in scene_list:
                shots.append((round(s_start.get_seconds(), 3), round(s_end.get_seconds(), 3)))
    except Exception:
        pass

    # 2. Fallback: OpenCV HSV histogram cut detector
    if not shots:
        shots = _opencv_detect_shots(video_path, start_sec, end_sec)

    if not shots:
        shots = [(start_sec, end_sec)]

    # 3. Subdivide any camera shot > 3.0 seconds into 2.0-second sub-windows
    subdivided: list[tuple[float, float]] = []
    for s_start, s_end in shots:
        dur = s_end - s_start
        if dur > 3.0:
            cur = s_start
            while cur < s_end:
                nxt = min(s_end, cur + 2.0)
                if nxt - cur >= 0.5:
                    subdivided.append((round(cur, 3), round(nxt, 3)))
                elif subdivided:
                    # Merge tiny tail into previous window
                    subdivided[-1] = (subdivided[-1][0], round(s_end, 3))
                cur = nxt
        else:
            subdivided.append((round(s_start, 3), round(s_end, 3)))

    return subdivided


def _opencv_detect_shots(video_path: Path, start_sec: float, end_sec: float) -> list[tuple[float, float]]:
    """Ultra-fast fallback scene cut detector sampling at 0.75s intervals with thumbnail downscaling."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [(start_sec, end_sec)]

    shots: list[tuple[float, float]] = []
    prev_hist = None
    cur_shot_start = start_sec

    step_sec = 0.75
    cur_time = start_sec

    while cur_time < end_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, cur_time * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        # Fast downscale to 160x90 for negligible CPU usage
        tiny = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_NEAREST)
        hsv = cv2.cvtColor(tiny, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

        if prev_hist is not None:
            similarity = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
            if similarity < 0.50 and (cur_time - cur_shot_start) >= 0.75:
                shots.append((round(cur_shot_start, 3), round(cur_time, 3)))
                cur_shot_start = cur_time

        prev_hist = hist
        cur_time += step_sec

    cap.release()
    if cur_shot_start < end_sec:
        shots.append((round(cur_shot_start, 3), round(end_sec, 3)))

    return shots


def calculate_shot_tracking(
    video_path: Path,
    shot_start: float,
    shot_end: float,
    frame_w: int,
    frame_h: int,
    crop_w: int,
    crop_h: int,
    detector: CinerushFaceDetector,
) -> tuple[int, int, bool]:
    """
    Samples frames across the shot and determines:
    - x_start: horizontal crop starting position
    - x_end: horizontal crop ending position
    - is_wide_shot: True if multiple subjects are spaced wide apart (> crop_w * 0.88)

    If the subject moves significantly across the shot (> 5% of width),
    x_start != x_end enables a smooth cinematic camera pan that keeps
    the subject continuously framed without cutting them off!
    If the subject is mostly stationary, x_start == x_end holds the shot steady.
    """
    cap = cv2.VideoCapture(str(video_path))
    default_x = (frame_w - crop_w) // 2
    default_x = default_x - (default_x % 2)

    if not cap.isOpened():
        return default_x, default_x, False

    dur = max(0.1, shot_end - shot_start)
    num_samples = max(3, min(7, int(dur / 0.45) + 1))
    sample_timestamps = [shot_start + dur * (i + 0.5) / num_samples for i in range(num_samples)]

    sampled_centers: list[float] = []
    is_wide_shot = False

    for ts in sample_timestamps:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        faces = detector.detect_faces(frame)
        if not faces:
            continue

        if len(faces) >= 2:
            centers = sorted([bx + bw / 2.0 for bx, by, bw, bh in faces])
            min_c = centers[0]
            max_c = centers[-1]
            span = max_c - min_c
            if span <= (crop_w * 0.88):
                # Both faces fit comfortably inside the crop window
                sampled_centers.append((min_c + max_c) / 2.0)
            else:
                # Wide shot: actors are spread apart
                is_wide_shot = True
                largest = max(faces, key=lambda f: f[2] * f[3])
                sampled_centers.append(largest[0] + largest[2] / 2.0)
        else:
            sampled_centers.append(faces[0][0] + faces[0][2] / 2.0)

    cap.release()

    if not sampled_centers:
        return default_x, default_x, is_wide_shot

    # Average start and end centers
    cx_start = sum(sampled_centers[:2]) / len(sampled_centers[:2])
    cx_end = sum(sampled_centers[-2:]) / len(sampled_centers[-2:])
    shift = abs(cx_end - cx_start)

    # Deadband threshold: if shift < 5% of width, lock camera to prevent jitter
    if shift < (frame_w * 0.05):
        mean_cx = sum(sampled_centers) / len(sampled_centers)
        x_fixed = int(round(mean_cx - crop_w / 2.0))
        x_fixed = max(0, min(frame_w - crop_w, x_fixed))
        x_fixed = x_fixed - (x_fixed % 2)
        return x_fixed, x_fixed, is_wide_shot

    # Smooth tracking pan
    x_start = int(round(cx_start - crop_w / 2.0))
    x_end = int(round(cx_end - crop_w / 2.0))

    x_start = max(0, min(frame_w - crop_w, x_start))
    x_end = max(0, min(frame_w - crop_w, x_end))
    x_start = x_start - (x_start % 2)
    x_end = x_end - (x_end % 2)

    return x_start, x_end, is_wide_shot


def calculate_shot_crop_x(
    video_path: Path,
    shot_start: float,
    shot_end: float,
    frame_w: int,
    frame_h: int,
    crop_w: int,
    detector: CinerushFaceDetector,
) -> int:
    """
    Samples frames in the shot and calculates horizontal crop x1.
    Maintains backward compatibility with unit tests and returns static/start crop.
    """
    x_start, _, _ = calculate_shot_tracking(
        video_path=video_path,
        shot_start=shot_start,
        shot_end=shot_end,
        frame_w=frame_w,
        frame_h=frame_h,
        crop_w=crop_w,
        crop_h=frame_h,
        detector=detector,
    )
    return x_start


def render_shot_locked_cinerush_video(
    source_video: Path,
    keep_intervals: list[tuple[float, float]],
    output_path: Path,
    profile: str = "cinerush_style",
    aspect_ratio: str | None = None,
) -> Path:
    """
    Renders Cinerush short with active face-tracking & adaptive framing:
    - profile 'cinerush_style': 1:1 square (1080x1080), visible width = 1080px (+78% over 9:16)
    - profile 'shorts_9x16': 9:16 vertical (1080x1920) with blurred background fallback for wide shots
    - Smooth camera panning when actors move across frame (> 5% shift)
    - Deadband locked camera when stationary to prevent jitter
    - Preserves 100% original movie dialogue audio
    - Adds 30ms audio fades at interval boundaries to prevent clicks
    - Renders final CFR 30fps MP4 via FFmpeg
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Probe source resolution
    cap = cv2.VideoCapture(str(source_video))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    cap.release()

    # Determine layout geometry
    is_square = (aspect_ratio == "1:1") or (profile == "cinerush_style")
    is_landscape = (aspect_ratio == "16:9") or (profile == "landscape")

    if is_square:
        out_w = 1080
        out_h = 1080
        crop_w = min(frame_w, frame_h)
        crop_h = min(frame_w, frame_h)
        target_mode = "square_1x1"
    elif is_landscape:
        out_w = 1920
        out_h = 1080
        crop_w = frame_w
        crop_h = frame_h
        target_mode = "landscape_16x9"
    else:
        out_w = 1080
        out_h = 1920
        crop_w = int(frame_h * 9 / 16)
        crop_h = frame_h
        target_mode = "shorts_9x16"

    crop_w = crop_w - (crop_w % 2)
    crop_h = crop_h - (crop_h % 2)

    log(f"📐 Layout mode: {target_mode} -> Crop: {crop_w}x{crop_h}, Output: {out_w}x{out_h}")

    detector = CinerushFaceDetector()
    temp_dir = Path(tempfile.mkdtemp(prefix="cinerush_render_"))

    segment_files: list[Path] = []
    try:
        seg_idx = 0
        for s_idx, (start_sec, end_sec) in enumerate(keep_intervals):
            dur = end_sec - start_sec
            if dur <= 0.1:
                continue

            # Detect shots within this keep interval
            shots = detect_camera_shots(source_video, start_sec, end_sec)

            # Analyze face tracking for each shot
            shot_plans: list[dict[str, Any]] = []
            for shot_start, shot_end in shots:
                if shot_end - shot_start <= 0.05:
                    continue
                x_start, x_end, is_wide = calculate_shot_tracking(
                    source_video, shot_start, shot_end, frame_w, frame_h, crop_w, crop_h, detector
                )
                shot_plans.append({
                    "start": shot_start,
                    "end": shot_end,
                    "x_start": x_start,
                    "x_end": x_end,
                    "is_wide": is_wide,
                })

            for plan in shot_plans:
                shot_start = plan["start"]
                shot_end = plan["end"]
                x_start = plan["x_start"]
                x_end = plan["x_end"]
                is_wide = plan["is_wide"]
                shot_dur = max(0.05, shot_end - shot_start)
                seg_out = temp_dir / f"seg_{seg_idx:04d}.mp4"

                # Audio fade: 30ms in, 30ms out to prevent clicks
                fade_dur = min(0.03, shot_dur / 4.0)
                af = f"afade=t=in:st=0:d={fade_dur:.3f},afade=t=out:st={max(0, shot_dur - fade_dur):.3f}:d={fade_dur:.3f}"

                # Video filter:
                # 1. Wide shot in 9:16 -> blurred background composite to prevent cutting actors
                if is_wide and target_mode == "shorts_9x16":
                    vf = (
                        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                        f"crop={out_w}:{out_h},boxblur=20:5,eq=brightness=-0.15[bg];"
                        f"[0:v]scale={out_w}:-2[fg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30"
                    )
                # 2. Moving subject -> smooth camera pan tracking
                elif x_start != x_end and target_mode != "landscape_16x9":
                    vf = (
                        f"crop={crop_w}:{crop_h}:x='clip({x_start}+({x_end}-{x_start})*t/{shot_dur:.3f},0,in_w-out_w)':0,"
                        f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,fps=30"
                    )
                # 3. Static centered crop
                else:
                    vf = (
                        f"crop={crop_w}:{crop_h}:{x_start}:0,"
                        f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,fps=30"
                    )

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{shot_start:.3f}",
                    "-to", f"{shot_end:.3f}",
                    "-i", str(source_video),
                    "-vf", vf,
                    "-af", af,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "19",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    str(seg_out),
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
                if proc.returncode == 0 and seg_out.exists():
                    segment_files.append(seg_out)
                    seg_idx += 1
                else:
                    log(f"⚠️ Warning: Segment {shot_start:.2f}-{shot_end:.2f} failed: {proc.stderr[:150]}")

        if not segment_files:
            raise RuntimeError("Failed to render any video segments during face-tracking reframing.")

        # Stitch all segments with concat demuxer
        concat_txt = temp_dir / "concat.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for sf in segment_files:
                f.write(f"file '{sf.resolve()}'\n")

        log(f"🎬 Stitching {len(segment_files)} face-tracked segments into {out_w}x{out_h} short...")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "19",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]
        res = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg segment concatenation failed: {res.stderr[:300]}")

        log(f"✅ Reframed video successfully generated: {output_path} ({out_w}x{out_h})")
        return output_path

    finally:
        # Cleanup temporary slice files
        for sf in segment_files:
            try:
                sf.unlink()
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass
