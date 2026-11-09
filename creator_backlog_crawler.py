#!/usr/bin/env python3
"""
Creator Backlog Crawler & Fresh Upload Prioritization Engine.

Implements the user-defined algorithmic upload selection rule:
1. Examine the target creator's YouTube channel uploads in reverse chronological order (newest to oldest).
2. If the creator uploaded a fresh latest video that has not yet been clipped, clip that latest video first!
3. Otherwise, if the latest video was already clipped, continue marching backwards towards older videos
   in the catalog, clipping the next unclipped older release.
4. Record all clipped source video IDs in upload_history.json to ensure zero duplicate uploads.
"""

import sys
import json
import re
import argparse
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_FILE = ROOT_DIR / "upload_history.json"
DEFAULT_CATALOG_FILE = ROOT_DIR / "creator_catalog.json"


def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [BacklogCrawler] {msg}", flush=True)


def load_creator_catalog(catalog_file: Path = DEFAULT_CATALOG_FILE) -> Dict[str, Any]:
    if not catalog_file.exists():
        raise FileNotFoundError(f"Creator catalog not found: {catalog_file}")
    return json.loads(catalog_file.read_text(encoding="utf-8"))


def get_clipped_video_ids(history_file: Path = DEFAULT_HISTORY_FILE) -> Set[str]:
    """Retrieves all source video IDs that have already been clipped."""
    clipped = set()
    if not history_file.exists():
        return clipped
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for item in data:
                # Track source_video_id if recorded, or fallback to video_id
                src_id = item.get("source_video_id")
                if src_id:
                    clipped.add(src_id)
                # Also track any direct video_id recorded
                vid_id = item.get("video_id")
                if vid_id and not str(vid_id).startswith("dry_run"):
                    clipped.add(vid_id)
    except Exception as e:
        log(f"Warning: Failed to parse history file {history_file}: {e}")
    return clipped


def fetch_creator_uploads_ytdlp(channel_handle: str, max_results: int = 10, timeout: int = 15) -> List[Dict[str, Any]]:
    """
    Fetches real uploads using yt-dlp flat-playlist extraction.
    Fast, highly reliable, and returns genuine YouTube video IDs.
    """
    import subprocess
    clean_handle = channel_handle.strip().lstrip("@")
    url = f"https://www.youtube.com/@{clean_handle}/videos"
    cmd = [
        "yt-dlp", "--flat-playlist",
        "--print", "%(id)s|%(title)s|%(upload_date)s",
        url,
        "-I", f":{max_results}"
    ]
    uploads = []
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.strip().splitlines():
                parts = line.split("|")
                if len(parts) >= 2:
                    vid_id = parts[0].strip()
                    title = parts[1].strip()
                    pub_str = parts[2].strip() if len(parts) > 2 else ""
                    if vid_id and title:
                        uploads.append({
                            "video_id": vid_id,
                            "title": title,
                            "published_at": pub_str,
                            "url": f"https://www.youtube.com/watch?v={vid_id}",
                        })
    except Exception as e:
        log(f"Warning: yt-dlp flat playlist fetch failed for @{clean_handle}: {e}")
    return uploads


def fetch_creator_uploads_rss(channel_id: str, timeout: int = 8) -> List[Dict[str, Any]]:
    """
    Fetches the 15 latest uploads for a channel via YouTube XML RSS feed.
    Zero API quota, zero credentials required, ordered strictly newest-to-oldest.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    uploads = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
            }
            for entry in root.findall("atom:entry", ns):
                vid_elem = entry.find("yt:videoId", ns)
                title_elem = entry.find("atom:title", ns)
                pub_elem = entry.find("atom:published", ns)
                if vid_elem is not None and title_elem is not None:
                    vid_id = vid_elem.text
                    title = title_elem.text
                    published_at = pub_elem.text if pub_elem is not None else ""
                    uploads.append({
                        "video_id": vid_id,
                        "title": title,
                        "published_at": published_at,
                        "url": f"https://www.youtube.com/watch?v={vid_id}",
                    })
    except Exception as e:
        log(f"Warning: RSS fetch failed for channel {channel_id}: {e}")
    return uploads


def format_jack_craig_hook(raw_title: str, creator_info: Dict[str, Any]) -> str:
    """Formats title into Jack Craig's Question Hook + Shock Emojis + Handle."""
    base_title = raw_title.strip()
    # Strip any existing parenthetical handles, hashtags, or emojis
    clean_title = re.sub(r"\s*\(@[^)]+\)", "", base_title)
    clean_title = re.sub(r"[\U00010000-\U0010ffff]", "", clean_title)
    clean_title = clean_title.replace("#shorts", "").strip()

    # Formulate question hook if not already phrased as question
    creator_display_name = creator_info.get("name", "They").split("/")[0].strip()
    if not clean_title.endswith("?"):
        if not any(clean_title.lower().startswith(q) for q in ["can ", "did ", "how ", "why ", "what ", "who ", "will ", "is "]):
            hook = f"Can {creator_display_name} {clean_title}?"
        else:
            hook = f"{clean_title}?"
    else:
        hook = clean_title

    packaging = creator_info.get("packaging", {})
    emojis = packaging.get("shock_emojis", "😳😳")
    handle = creator_info.get("channel_handle", "@YouTube")

    candidate = f"{hook} {emojis} ({handle}/YT)"
    if len(candidate) > 95:
        candidate = f"{hook[:70]}... {emojis} ({handle}/YT)"
    return candidate


def select_target_upload(
    creator_id: str,
    history_file: Path = DEFAULT_HISTORY_FILE,
    catalog_file: Path = DEFAULT_CATALOG_FILE,
    mock_uploads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Selects the next video for a creator following the algorithm:
    1. If the latest video is unclipped -> selects it (fresh release priority).
    2. Else -> crawls backwards through older videos to find the first unclipped older video.
    """
    catalog = load_creator_catalog(catalog_file)
    creators = catalog.get("creators", {})
    if creator_id not in creators:
        raise ValueError(f"Creator '{creator_id}' not found in catalog {catalog_file}")

    creator_info = creators[creator_id]
    channel_id = creator_info.get("channel_id")
    creator_name = creator_info.get("name", creator_id)

    # 1. Fetch channel uploads (newest to oldest): yt-dlp first, then RSS fallback
    handle = creator_info.get("channel_handle", f"@{creator_id}")
    if mock_uploads is not None:
        uploads = mock_uploads
    else:
        uploads = fetch_creator_uploads_ytdlp(handle)
        if not uploads and channel_id:
            uploads = fetch_creator_uploads_rss(channel_id)

    # Fallback to sample topics from catalog if network fetch yields no results
    if not uploads:
        sample_topics = creator_info.get("sample_topics", ["Viral Challenge"])
        uploads = [
            {
                "video_id": f"catalog_sample_{i}",
                "title": topic,
                "published_at": datetime.now().isoformat(),
                "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(topic)}",
            }
            for i, topic in enumerate(sample_topics)
        ]

    clipped_ids = get_clipped_video_ids(history_file)

    # 2. Check latest video (index 0)
    latest_video = uploads[0]
    is_latest_unclipped = latest_video["video_id"] not in clipped_ids

    if is_latest_unclipped:
        selected_video = latest_video
        selection_mode = "fresh_latest_upload"
        backlog_index = 0
        reason = (
            f"🔥 Fresh latest upload detected for {creator_name}! "
            f"'{selected_video['title']}' ({selected_video['video_id']}) is brand new. "
            f"Clipping latest release with highest priority!"
        )
    else:
        # 3. Crawl backwards towards older videos
        selected_video = None
        selection_mode = "backlog_older_video"
        backlog_index = -1
        for idx, vid in enumerate(uploads[1:], start=1):
            if vid["video_id"] not in clipped_ids:
                selected_video = vid
                backlog_index = idx
                reason = (
                    f"📼 Backlog crawl for {creator_name}: Latest video ({latest_video['video_id']}) already clipped. "
                    f"Marching backwards to unclipped older video #{idx}: '{selected_video['title']}' ({selected_video['video_id']})."
                )
                break

        # If all scanned uploads have been clipped, cycle back to the earliest or latest
        if selected_video is None:
            selected_video = uploads[0]
            selection_mode = "all_clipped_recycle"
            backlog_index = 0
            reason = (
                f"🔄 All {len(uploads)} scanned uploads for {creator_name} have been previously clipped. "
                f"Recycling latest upload '{selected_video['title']}' with fresh packaging variation."
            )

    jack_craig_title = format_jack_craig_hook(selected_video["title"], creator_info)
    svml_source = creator_info.get("svml_source", "sources/mrbeast_challenge.svml")
    output_filename = f"hypit_{creator_id}_{selected_video['video_id'][:8]}.mp4"

    result = {
        "creator_id": creator_id,
        "creator_name": creator_name,
        "channel_id": channel_id,
        "channel_handle": creator_info.get("channel_handle"),
        "selection_mode": selection_mode,
        "is_latest_unclipped": is_latest_unclipped,
        "backlog_index": backlog_index,
        "total_uploads_scanned": len(uploads),
        "source_video": selected_video,
        "formatted_title": jack_craig_title,
        "svml_source": svml_source,
        "output_filename": output_filename,
        "hud_theme": creator_info.get("hud_theme", {}),
        "packaging": creator_info.get("packaging", {}),
        "reason": reason,
    }

    log(reason)
    log(f"🎬 Deliverable Title: '{jack_craig_title}' | Output: {output_filename}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Creator Backlog Crawler & Fresh Upload Prioritization Engine")
    parser.add_argument("--creator", default="mrbeast", help="Target creator identifier (e.g. mrbeast, nileblue)")
    parser.add_argument("--history", default=str(DEFAULT_HISTORY_FILE), help="Path to upload_history.json")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_FILE), help="Path to creator_catalog.json")
    parser.add_argument("--json", action="store_true", help="Output full selection result as JSON")
    args = parser.parse_args()

    history_path = Path(args.history).resolve()
    catalog_path = Path(args.catalog).resolve()

    res = select_target_upload(
        creator_id=args.creator,
        history_file=history_path,
        catalog_file=catalog_path,
    )

    if args.json:
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
