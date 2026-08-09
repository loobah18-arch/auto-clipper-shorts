---
name: video-ffmpeg-engineer
description: Advanced FFmpeg video processing, 9:16 vertical conversion, dynamic karaoke ASS subtitle generation, audio normalization, and filters.
---

# FFmpeg Video & Subtitle Engineering Playbook

This skill provides advanced filtergraphs, karaoke styling formulas, and encoding configurations for vertical short-form video (YouTube Shorts, TikTok, Instagram Reels).

## 1. Professional 9:16 Vertical Filtergraph
Transform standard 16:9 widescreen footage into an engaging 9:16 vertical layout using blurred background padding and centered foreground:
```bash
ffmpeg -y -i input.mp4 -filter_complex "\
[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.08:contrast=1.05[bg];\
[0:v]scale=1040:-2[fg];\
[bg][fg]overlay=(W-w)/2:(H-h)/2 - 50[comp1];\
[comp1]drawbox=y=160:color=black@0.65:width=iw:height=90:t=fill,\
drawtext=text='SPEAKER NAME':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=182[comp2];\
[comp2]ass='subtitles.ass'[v]" \
-map "[v]" -map "0:a?" -c:v libx264 -preset veryfast -crf 19 -c:a aac -b:a 192k -pix_fmt yuv420p output.mp4
```

## 2. Dynamic Karaoke Subtitle (ASS Format) Specs
For maximum viewer retention (MrBeast / Hormozi style):
- **Resolution**: `PlayResX: 1080`, `PlayResY: 1920`
- **Font**: Bold sans-serif (Montserrat, Roboto, DejaVu Sans), `FontSize: 58`
- **Active Word Color**: Bright Yellow (`&H002BF5FF&` in BGR hex)
- **Inactive Word Color**: Crisp White (`&H00FFFFFF&`)
- **Outline & Shadow**: Heavy dark outline (`Outline: 4.0`, `OutlineColour: &H00000000&`)
- **Animation Tags**:
  - `\fscx115\fscy115`: 15% pop on active spoken words.
  - `\t(0, 150, \fscx100\fscy100)`: Smooth bounce transition.
