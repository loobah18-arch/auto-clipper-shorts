"""Generate authentic, native Instagram comment sticker graphics for Shorts / Reels overlay.

Produces a pixel-perfect floating comment card matching real Instagram UI:
- Pure white rounded rectangle card with realistic soft diffused drop shadow.
- Instagram Story sunset gradient ring around the circular profile avatar.
- Semi-bold clean sans-serif username (no '@' prefix, matching real Instagram comments).
- Optional Instagram blue verified badge and time-ago indicator (e.g. '· 2h').
- Word-wrapped comment body with high-resolution color emoji support.
- 'Reply' button and parametric vector red heart with formatted like count.
"""
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = REPO_ROOT / "assets" / "fonts" / "Inter.ttf"
EMOJI_DIR = REPO_ROOT / "assets" / "emojis"


def draw_heart(size=30, color=(237, 73, 86, 255)):
    """Draw a mathematical smooth parametric heart curve with 4x supersampling."""
    scale = 4
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = []
    for step in range(360):
        t = math.radians(step)
        x = 16 * (math.sin(t) ** 3)
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        px = s / 2 + (x / 17.0) * (s * 0.44)
        py = s / 2 + (y / 17.0) * (s * 0.44) + (s * 0.04)
        pts.append((px, py))
    d.polygon(pts, fill=color)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def draw_verified_badge(size=20):
    """Draw an authentic Instagram verified badge (blue circle + white checkmark)."""
    scale = 4
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, s, s), fill=(0, 149, 246, 255))
    pts = [(s * 0.28, s * 0.52), (s * 0.44, s * 0.68), (s * 0.74, s * 0.34)]
    d.line(pts, fill=(255, 255, 255, 255), width=int(2.5 * scale), joint="curve")
    return im.resize((size, size), Image.Resampling.LANCZOS)


def draw_avatar(size=72):
    """Draw a circular creator avatar surrounded by the signature Instagram gradient ring."""
    scale = 4
    s = size * scale
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ring_thick = int(3.5 * scale)
    gap = int(2.5 * scale)

    for y in range(s):
        for x in range(s):
            dx, dy = x - s / 2, y - s / 2
            dist = math.sqrt(dx * dx + dy * dy)
            if s / 2 - ring_thick <= dist <= s / 2:
                angle = (math.atan2(dy, dx) + math.pi) / (2 * math.pi)
                if angle < 0.25:
                    t = angle / 0.25
                    r = int(254 * (1 - t) + 250 * t)
                    g = int(218 * (1 - t) + 126 * t)
                    b = int(117 * (1 - t) + 30 * t)
                elif angle < 0.5:
                    t = (angle - 0.25) / 0.25
                    r = int(250 * (1 - t) + 214 * t)
                    g = int(126 * (1 - t) + 41 * t)
                    b = int(30 * (1 - t) + 118 * t)
                elif angle < 0.75:
                    t = (angle - 0.5) / 0.25
                    r = int(214 * (1 - t) + 150 * t)
                    g = int(41 * (1 - t) + 47 * t)
                    b = int(118 * (1 - t) + 191 * t)
                else:
                    t = (angle - 0.75) / 0.25
                    r = int(150 * (1 - t) + 254 * t)
                    g = int(47 * (1 - t) + 218 * t)
                    b = int(191 * (1 - t) + 117 * t)
                grad.putpixel((x, y), (r, g, b, 255))

    avatar_bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ad = ImageDraw.Draw(avatar_bg)
    ad.ellipse((ring_thick, ring_thick, s - ring_thick, s - ring_thick), fill=(255, 255, 255, 255))

    offset = ring_thick + gap
    inner_r = s - 2 * offset
    ad.ellipse((offset, offset, offset + inner_r, offset + inner_r), fill=(32, 38, 50, 255))

    head_r = int(inner_r * 0.22)
    cx, cy = s // 2, offset + int(inner_r * 0.38)
    ad.ellipse((cx - head_r, cy - head_r, cx + head_r, cy + head_r), fill=(215, 222, 235, 255))
    sh_w = int(inner_r * 0.44)
    sh_top = cy + head_r + int(inner_r * 0.08)
    ad.chord((cx - sh_w, sh_top - int(inner_r * 0.1), cx + sh_w, offset + inner_r + int(inner_r * 0.25)),
             0, 180, fill=(215, 222, 235, 255))

    final = Image.alpha_composite(grad, avatar_bg)
    return final.resize((size, size), Image.Resampling.LANCZOS)


def generate_instagram_sticker(username, time_ago, text, likes, out_path,
                               is_verified=False, width=940):
    """Generate an authentic Instagram comment sticker PNG with soft drop shadow.

    Returns dict:
        {"path": out_path, "width": total_w, "height": total_h,
         "card_width": width, "card_height": card_h, "margin": margin}
    """
    bg_color = (255, 255, 255, 255)
    uname_color = (18, 18, 18, 255)
    time_color = (115, 115, 115, 255)
    body_color = (24, 24, 24, 255)
    reply_color = (115, 115, 115, 255)
    likes_color = (115, 115, 115, 255)

    font_file = FONT_PATH if FONT_PATH.exists() else None
    if font_file:
        f_uname = ImageFont.truetype(str(font_file), 30)
        try:
            f_uname.set_variation_by_name(b"SemiBold")
        except Exception:
            pass
        f_meta = ImageFont.truetype(str(font_file), 26)
        try:
            f_meta.set_variation_by_name(b"Regular")
        except Exception:
            pass
        f_body = ImageFont.truetype(str(font_file), 34)
        try:
            f_body.set_variation_by_name(b"Regular")
        except Exception:
            pass
        f_reply = ImageFont.truetype(str(font_file), 24)
        try:
            f_reply.set_variation_by_name(b"SemiBold")
        except Exception:
            pass
        f_likes = ImageFont.truetype(str(font_file), 22)
        try:
            f_likes.set_variation_by_name(b"Regular")
        except Exception:
            pass
    else:
        f_uname = ImageFont.load_default()
        f_meta = f_uname
        f_body = f_uname
        f_reply = f_uname
        f_likes = f_uname

    avatar_size = 72
    pad_x = 36
    pad_y = 28
    content_x = pad_x + avatar_size + 20
    heart_w = 30
    heart_col_w = 70
    max_text_w = width - content_x - heart_col_w - pad_x

    def measure_text_with_emojis(txt):
        tot = 0
        for c in txt:
            hex_c = f"{ord(c):x}"
            em_p = EMOJI_DIR / f"{hex_c}.png"
            if em_p.exists():
                tot += 36
            else:
                tot += f_body.getlength(c) if hasattr(f_body, "getlength") else 18
        return tot

    words = text.split(" ")
    lines = []
    curr = []
    for w in words:
        test_line = " ".join(curr + [w])
        if measure_text_with_emojis(test_line) <= max_text_w:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))
    if not lines:
        lines = [text]

    line_h = 44
    header_h = 32
    reply_h = 30
    spacing = 10

    card_h = pad_y * 2 + header_h + spacing + (len(lines) * line_h) + spacing + reply_h
    card_h = max(card_h, 210)

    card = Image.new("RGBA", (width, card_h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((0, 0, width, card_h), radius=30, fill=bg_color)

    # Circular avatar with story ring
    av = draw_avatar(avatar_size)
    card.alpha_composite(av, (pad_x, pad_y + 2))

    # Username + Verified + Time
    clean_uname = username.lstrip("@")
    cd.text((content_x, pad_y + 4), clean_uname, font=f_uname, fill=uname_color)
    u_w = f_uname.getlength(clean_uname) if hasattr(f_uname, "getlength") else len(clean_uname) * 16
    cur_meta_x = content_x + u_w + 8

    if is_verified:
        vb = draw_verified_badge(20)
        card.alpha_composite(vb, (int(cur_meta_x), pad_y + 9))
        cur_meta_x += 26

    cd.text((cur_meta_x, pad_y + 6), f"· {time_ago}", font=f_meta, fill=time_color)

    # Body lines with color Twemoji support
    text_y = pad_y + header_h + spacing
    for line in lines:
        cur_x = content_x
        for c in line:
            hex_c = f"{ord(c):x}"
            em_p = EMOJI_DIR / f"{hex_c}.png"
            if em_p.exists():
                em_img = Image.open(em_p).convert("RGBA").resize((32, 32), Image.Resampling.LANCZOS)
                card.alpha_composite(em_img, (int(cur_x), text_y + 2))
                cur_x += 36
            else:
                cd.text((cur_x, text_y), c, font=f_body, fill=body_color)
                cur_x += f_body.getlength(c) if hasattr(f_body, "getlength") else 18
        text_y += line_h

    # Footer: Reply
    foot_y = text_y + 4
    cd.text((content_x, foot_y), "Reply", font=f_reply, fill=reply_color)

    # Heart + Likes on right side
    heart = draw_heart(heart_w, color=(237, 73, 86, 255))
    heart_x = width - pad_x - heart_w - 12
    heart_y = pad_y + 14
    card.alpha_composite(heart, (heart_x, heart_y))

    l_w = f_likes.getlength(likes) if hasattr(f_likes, "getlength") else len(likes) * 12
    likes_x = int(heart_x + (heart_w / 2) - (l_w / 2))
    likes_y = heart_y + heart_w + 6
    cd.text((likes_x, likes_y), likes, font=f_likes, fill=likes_color)

    # Ambient diffused drop shadow
    margin = 50
    total_w = width + margin * 2
    total_h = card_h + margin * 2
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((margin, margin + 12, margin + width, margin + 12 + card_h),
                         radius=30, fill=(0, 0, 0, 42))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))

    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(card, (margin, margin))

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_file)

    return {
        "path": out_file,
        "width": total_w,
        "height": total_h,
        "card_width": width,
        "card_height": card_h,
        "margin": margin,
    }
