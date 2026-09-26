#!/usr/bin/env python3
"""
Unit tests for the Movie Explanation Shorts pipeline (MovieGyan style).
Tests catalog loading, AI script fallback, subtitle generation, and duration checks.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE_DIR))

from movie_ai_script import load_catalog, get_movie_from_catalog, generate_movie_script_ai
from movie_pipeline_state import (
    DuplicateEpisodeError,
    RunStatus,
    append_history_entry,
    load_history,
    uploaded_part_numbers,
)
import movie_quality
import movie_video_engine
from movie_quality import MediaInfo, MediaValidationError, probe_media, validate_media_info
from movie_video_engine import (
    TRAILER_CLIENT_CONFIGS,
    create_word_timestamps_from_sentences,
    download_movie_trailer,
    generate_moviegyan_subtitles,
    find_system_font,
    parse_timestamp_to_seconds,
    OUTPUT_DIR,
    BGM_DIR,
    DEFAULT_BGM_OFFSETS,
    get_default_bgm_offset
)


class TestMoviePipeline(unittest.TestCase):

    def test_catalog_structure(self):
        catalog = load_catalog()
        self.assertIn("movies", catalog)
        self.assertGreaterEqual(len(catalog["movies"]), 10)
        first_movie = catalog["movies"][0]
        self.assertIn("title", first_movie)
        self.assertIn("script", first_movie)
        self.assertIn("hook", first_movie)
        self.assertIn("badge", first_movie)
        self.assertIn("tags", first_movie)

    def test_get_movie_from_catalog(self):
        m = get_movie_from_catalog("The Platform")
        self.assertIsNotNone(m)
        self.assertIn("Platform", m["title"])

        m2 = get_movie_from_catalog("NonExistentMovie12345")
        self.assertIsNone(m2)

    def test_ai_script_generator_fallback(self):
        res = generate_movie_script_ai("Shutter Island", language="en")
        self.assertIsNotNone(res)
        self.assertIn("script", res)
        self.assertGreater(len(res["script"].split()), 50)

    def test_word_timestamp_generation(self):
        sentences = [
            {"text": "In this terrifying vertical prison", "start": 0.0, "end": 2.5},
            {"text": "hundreds of inmates starve", "start": 2.6, "end": 4.5}
        ]
        words = create_word_timestamps_from_sentences(sentences)
        self.assertEqual(len(words), 9)
        self.assertEqual(words[0]["word"], "IN")
        self.assertAlmostEqual(words[0]["start"], 0.0, places=1)
        self.assertTrue(words[-1]["end"] <= 4.6)

    def test_subtitle_ass_generation(self):
        words = [
            {"word": "IN", "start": 0.0, "end": 0.3},
            {"word": "THIS", "start": 0.3, "end": 0.6},
            {"word": "PRISON", "start": 0.6, "end": 1.0},
            {"word": "SURVIVAL", "start": 1.0, "end": 1.5},
            {"word": "IS", "start": 1.5, "end": 1.8},
            {"word": "IMPOSSIBLE", "start": 1.8, "end": 2.4}
        ]
        test_ass = OUTPUT_DIR / "test_subtitles.ass"
        generate_moviegyan_subtitles(words, test_ass, group_size=3)
        self.assertTrue(test_ass.exists())
        with open(test_ass, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Script Info]", content)
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertIn("&H0000FFFF&", content)  # Neon Yellow active color
        self.assertIn("PRISON", content)
        if test_ass.exists():
            test_ass.unlink()

    def test_font_finder(self):
        font = find_system_font()
        self.assertTrue(len(font) > 0)

    def test_parse_timestamp_to_seconds(self):
        self.assertEqual(parse_timestamp_to_seconds("00:01:30"), 90.0)
        self.assertEqual(parse_timestamp_to_seconds("01:25:00"), 5100.0)
        self.assertEqual(parse_timestamp_to_seconds("05:15"), 315.0)
        self.assertEqual(parse_timestamp_to_seconds("45"), 45.0)

    def test_multipart_movie_catalog(self):
        m = get_movie_from_catalog("The Avengers")
        self.assertIsNotNone(m)
        self.assertIn("parts", m)
        self.assertEqual(len(m["parts"]), 8)
        self.assertNotIn("gdrive_folder_id", m)
        self.assertNotIn("gdrive_file_id", m)
        self.assertNotIn("gdrive_file_name", m)

        # Verify all 8 parts are strictly under 145 words (~55s, strictly < 1 min)
        for p in m["parts"]:
            wc = len(p["script"].split())
            self.assertTrue(110 <= wc <= 145, f"Part {p['part_number']} script has {wc} words, must be <= 145 words")

        part1 = m["parts"][0]
        self.assertEqual(part1["part_number"], 1)
        self.assertEqual(part1["timeline_start"], "00:01:00")
        self.assertEqual(part1["timeline_end"], "00:16:00")

    def test_select_next_movie_multipart_resolution(self):
        from generate_movie_short import select_next_movie
        res = select_next_movie("The Avengers (2012)", requested_part=2)
        self.assertEqual(res["part_number"], 2)
        self.assertIn("Part 2", res["title"])
        self.assertEqual(res["timeline_start"], "00:16:00")
        self.assertEqual(res["timeline_end"], "00:32:00")

    def test_default_bgm_is_opt_in(self):
        self.assertFalse((BGM_DIR / "malevolent_shrine_sukuna.mp3").exists())
        self.assertEqual(get_default_bgm_offset(movie_title="Interstellar (2014)"), get_default_bgm_offset(movie_title="Interstellar (2014)"))

    def test_default_bgm_offset_distribution(self):
        offset1 = get_default_bgm_offset(part_number=1)
        offset2 = get_default_bgm_offset(part_number=2)
        offset3 = get_default_bgm_offset(part_number=3)
        offset4 = get_default_bgm_offset(part_number=4)
        offset5 = get_default_bgm_offset(part_number=5)

        self.assertEqual(offset1, 0.0)
        self.assertEqual(offset2, 35.0)
        self.assertEqual(offset3, 60.0)
        self.assertEqual(offset4, 95.0)
        self.assertEqual(offset5, 118.0)

        # Offsets across consecutive parts must be distinct
        self.assertNotEqual(offset1, offset2)
        self.assertNotEqual(offset2, offset3)
        self.assertNotEqual(offset3, offset4)

    def test_standalone_movie_bgm_offsets(self):
        offset_a = get_default_bgm_offset(part_number=None, movie_title="Interstellar (2014)")
        offset_b = get_default_bgm_offset(part_number=None, movie_title="Inception (2010)")
        self.assertIn(offset_a, DEFAULT_BGM_OFFSETS)
        self.assertIn(offset_b, DEFAULT_BGM_OFFSETS)
    def test_provider_word_boundaries_are_preserved(self):
        words = create_word_timestamps_from_sentences([
            {
                "text": "Hello world",
                "start": 0.0,
                "end": 1.0,
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.45},
                    {"word": "world", "start": 0.55, "end": 1.0},
                ],
            }
        ])
        self.assertEqual([word["start"] for word in words], [0.0, 0.55])

    def test_hindi_subtitle_font_is_selected(self):
        test_ass = OUTPUT_DIR / "test_hindi_subtitles.ass"
        generate_moviegyan_subtitles(
            [{"word": "नमस्ते", "start": 0.0, "end": 0.5}],
            test_ass,
            language="hi",
        )
        content = test_ass.read_text(encoding="utf-8")
        self.assertIn("Noto Sans Devanagari", content)
        test_ass.unlink(missing_ok=True)

    def test_only_confirmed_uploads_advance_parts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = load_history(Path(temp_dir) / "movie_history.json")
            append_history_entry(history, {
                "movie_id": "example_movie",
                "part_number": 1,
                "status": RunStatus.RENDERED.value,
                "youtube_id": None,
            })
            self.assertEqual(uploaded_part_numbers(history, "example_movie"), set())
            append_history_entry(history, {
                "movie_id": "example_movie",
                "part_number": 1,
                "status": RunStatus.UPLOADED.value,
                "youtube_id": "abc123",
            })
            self.assertEqual(uploaded_part_numbers(history, "example_movie"), {1})

    def test_uploaded_episode_cannot_be_published_twice(self):
        history = {"uploaded_movies": []}
        entry = {
            "movie_id": "example_movie",
            "part_number": 1,
            "status": RunStatus.UPLOADED.value,
            "youtube_id": "abc123",
        }
        append_history_entry(history, entry)
        with self.assertRaises(DuplicateEpisodeError):
            append_history_entry(history, entry)

    def test_media_contract_rejects_invalid_render(self):
        validate_media_info(MediaInfo(50.0, 1080, 1920, True))
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(58.1, 1080, 1920, True))
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(50.0, 720, 1280, True))
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(50.0, 1080, 1920, False))

    def test_probe_media_parses_ffprobe_string_scalars(self):
        """ffprobe emits duration/width/height as JSON strings; they must parse."""
        payload = {
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920, "duration": "53.541000"},
                {"codec_type": "audio", "duration": "53.541000"},
            ],
            "format": {"duration": "53.541000"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "short.mp4"
            media.write_bytes(b"not-real-mp4")
            with unittest.mock.patch.object(movie_quality.subprocess, "run", **_fake_run(payload)):
                info = probe_media(media)
        self.assertAlmostEqual(info.duration_sec, 53.541, places=3)
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertTrue(info.has_audio)

    def test_probe_media_falls_back_to_stream_duration(self):
        """Containers without format-level duration still validate via the stream."""
        payload = {
            "streams": [
                {"codec_type": "video", "width": "1080", "height": "1920", "duration": "42.0"},
                {"codec_type": "audio"},
            ],
            "format": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "short.mp4"
            media.write_bytes(b"not-real-mp4")
            with unittest.mock.patch.object(movie_quality.subprocess, "run", **_fake_run(payload)):
                info = probe_media(media)
        self.assertAlmostEqual(info.duration_sec, 42.0, places=3)
        self.assertEqual((info.width, info.height), (1080, 1920))

    def test_probe_media_rejects_non_numeric_duration(self):
        payload = {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
            "format": {"duration": "N/A"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "short.mp4"
            media.write_bytes(b"not-real-mp4")
            with unittest.mock.patch.object(movie_quality.subprocess, "run", **_fake_run(payload)):
                with self.assertRaises(MediaValidationError):
                    probe_media(media)

    def test_trailer_client_table_shape_is_valid(self):
        """(name, format_selector, extractor_args) - the extractor arg must be third."""
        for name, fmt, extractor_args in TRAILER_CLIENT_CONFIGS:
            self.assertTrue(
                extractor_args.startswith("youtube:player_client="),
                f"{name}: extractor_args must be an extractor-args string, got {extractor_args!r}",
            )
            self.assertIn(
                name, extractor_args,
                f"{name}: extractor_args must select the matching client",
            )
            self.assertNotIn("player_client", fmt, f"{name}: fmt must be a format selector")
            self.assertTrue(fmt.startswith(("bv*", "b")), f"{name}: unexpected fmt {fmt!r}")

    def test_trailer_download_passes_extractor_args_and_format_correctly(self):
        """Guards the argument wiring: -f gets a format, --extractor-args gets IE_KEY:ARGS."""
        recorded: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            recorded.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: bot check")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "trailer.mp4"
            with unittest.mock.patch.object(movie_video_engine.subprocess, "run", side_effect=fake_run):
                result = download_movie_trailer("some movie", out, trailer_url="https://example.invalid/v")

        self.assertFalse(result)
        self.assertTrue(recorded, "yt-dlp should have been invoked")
        for cmd in recorded:
            for flag, prefix in (("--extractor-args", "youtube:player_client="), ("-f", None)):
                if flag in cmd:
                    value = cmd[cmd.index(flag) + 1]
                    if prefix:
                        self.assertTrue(
                            value.startswith(prefix),
                            f"--extractor-args got {value!r}, expected {prefix}*",
                        )
                    else:
                        self.assertNotIn("player_client", value, f"-f got {value!r}")
                        self.assertTrue(value.startswith(("bv*", "b")), f"-f got {value!r}")


def _fake_run(payload: dict) -> dict:
    """Build a unittest.mock.patch kwargs dict returning a canned ffprobe payload."""

    class _Result:
        stdout = json.dumps(payload)
        stderr = ""

    return {"return_value": _Result()}


if __name__ == "__main__":
    unittest.main()
