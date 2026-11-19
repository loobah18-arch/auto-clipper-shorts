"""Turn a 16:9 stream segment into a finished 1080x1920 Short in one ffmpeg pass.

Layouts (all keep the top ~170px and bottom ~380px free of key content, where the Shorts / Reels /
TikTok buttons and texts sit):
  stack     streamer cam on top, gameplay below, captions on the seam   (the standard streamer clip)
  gameplay  no cam: zoomed gameplay in the middle over a blurred copy,
            hook on top, captions under the gameplay                    (GTA 6 gameplay / trailers)
  blur      the full 16:9 frame over a blurred fill
  crop      centre crop to 9:16
Also: loudness normalised to -14 LUFS (what the apps play at), a thin progress bar, faststart.
"""
import subprocess
from pathlib import Path

from . import captions, facecam

FONT_DIR = captions.FONT_DIR
CAM_H = 700            # stack: cam panel height; gameplay gets 1920-700 = 1220
GAME_FG_H = 1080       # gameplay: zoomed gameplay panel height (square)


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "default=nw=1", str(video)],
                         capture_output=True, text=True).stdout
    d = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return int(d.get("width", 1920)), int(d.get("height", 1080)), float(d.get("duration", 0) or 0)


def _esc(p):
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def choose_layout(layout, cam_pos, piece, src_w, src_h, require_cam=True):
    """Resolve 'auto' to stack (cam found) or gameplay (no cam). Returns (layout, cam_box)."""
    cam = None
    if layout in ("stack", "auto"):
        if cam_pos in facecam.PRESETS:
            cam = facecam.preset_box(cam_pos, src_w, src_h)
        else:
            cam = facecam.detect(piece)
        if cam:
            return "stack", cam
        if layout == "stack" or require_cam:
            print("   ! no webcam found; skipping candidate (webcam required)", flush=True)
            return None, None
        return "gameplay", None
    return layout, None


def video_graph(layout, src_w, src_h, cam=None, fps=30):
    """(filter_complex up to [base], caption_y, credit_v)."""
    if layout == "stack":
        cx, cy, cw, ch = cam
        game_h = 1920 - CAM_H
        gw = min(src_w, int(src_h * 1080 / game_h) // 2 * 2)
        g = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:{CAM_H}:force_original_aspect_ratio=increase,"
             f"crop=1080:{CAM_H},setsar=1[cam];"
             f"[0:v]crop={gw}:{src_h}:(iw-{gw})/2:0,scale=1080:{game_h},setsar=1[game];"
             f"[cam][game]vstack=inputs=2,fps={fps}[base]")
        return g, CAM_H, 190
    if layout == "gameplay":
        fw = min(src_w, src_h) // 2 * 2       # square centre of the action, zoomed
        top = 360
        g = (f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
             f"boxblur=30:3,eq=brightness=-0.22:saturation=1.3[bg];"
             f"[0:v]crop={fw}:{src_h}:(iw-{fw})/2:0,scale=1080:{GAME_FG_H},setsar=1[fg];"
             f"[bg][fg]overlay=0:{top},fps={fps}[base]")
        return g, top + GAME_FG_H + 75, top - 60
    if layout == "crop":
        g = (f"[0:v]crop=floor(ih*9/16/2)*2:ih:(iw-ow)/2:0,scale=1080:1920,setsar=1,fps={fps}[base]")
        return g, 1300, 190
    # blur
    g = (f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,"
         f"eq=brightness=-0.15[bg];[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[base]")
    return g, 1330, 190


def render(piece, out_path, layout="auto", cam_pos="auto", words=None, hook="", credit="",
           progress_bar=True, fps=30, require_cam=True, duration=None,
           ig_comment=None, caption_style="ig-comment"):
    piece, out_path = Path(piece), Path(out_path)
    src_w, src_h, dur = probe(piece)
    if duration is not None:
        dur = min(dur, float(duration))
    layout, cam = choose_layout(layout, cam_pos, piece, src_w, src_h, require_cam=require_cam)
    if not layout:
        return {"layout": None, "cam": None, "duration": dur}
    graph, cap_y, credit_v = video_graph(layout, src_w, src_h, cam, fps)

    sticker_info = None
    sticker_path = None
    if (caption_style == "ig-comment" or caption_style is None) and ig_comment:
        try:
            from . import sticker
            sticker_path = out_path.with_name(out_path.stem + "_ig_sticker.png")
            sticker_info = sticker.generate_instagram_sticker(
                username=ig_comment.get("username", "vicecity_daily"),
                time_ago=ig_comment.get("time", "2h"),
                text=ig_comment.get("text", ""),
                likes=ig_comment.get("likes", "38.4K"),
                out_path=sticker_path,
                is_verified=ig_comment.get("verified", False),
            )
        except Exception as e:
            print(f"   ! sticker generation failed ({e}); falling back to ASS", flush=True)
            sticker_info = None

    ass = out_path.with_suffix(".ass")
    captions.build_ass(ass, words or [], dur, caption_y=cap_y, hook=hook, credit=credit, credit_v=credit_v,
                       ig_comment=None if sticker_info else ig_comment, style=caption_style)
    last = "base"
    inputs = ["-i", str(piece)]
    if sticker_info and sticker_path and sticker_path.exists():
        inputs += ["-i", str(sticker_path)]
        card_h = sticker_info.get("card_height", 210)
        margin = sticker_info.get("margin", 50)
        sticker_y = int(cap_y - (card_h // 2) - margin)
        graph += (f";[1:v]format=rgba[stk];"
                  f"[{last}][stk]overlay=(W-w)/2:{sticker_y}:eof_action=repeat[base_stk]")
        last = "base_stk"

    graph += f";[{last}]subtitles='{_esc(ass)}':fontsdir='{_esc(FONT_DIR)}'[subs]"
    last = "subs"
    if progress_bar and dur > 0:
        graph += (f";color=c=0xFFE500:s=1080x10:r={fps}[bar];"
                  f"[{last}][bar]overlay=x='-W+W*t/{dur:.3f}':y=H-10:shortest=1[v]")
        last = "v"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + ["-filter_complex", graph,
           "-map", f"[{last}]", "-map", "0:a?",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{dur:.3f}", str(out_path)]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed for {piece}")
    return {"layout": layout, "cam": cam, "duration": dur}
