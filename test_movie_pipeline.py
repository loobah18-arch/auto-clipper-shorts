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
    save_history,
    uploaded_part_numbers,
)
import movie_quality
import movie_video_engine
from movie_ai_script import load_catalog
from movie_auto_catalog import (
    _curated_titles,
    _title_matches_curated,
    build_entry,
    detect_episode_number,
    humanize,
    plan_windows,
    series_stem,
    slugify,
)
from movie_quality import (
    MediaInfo,
    MediaValidationError,
    PLACEHOLDER_SOURCE_TYPES,
    probe_media,
    validate_media_info,
    validate_upload_source,
)
from movie_video_engine import (
    GDRIVE_MAP_FILENAME,
    TRAILER_CLIENT_CONFIGS,
    create_word_timestamps_from_sentences,
    download_movie_trailer,
    generate_moviegyan_subtitles,
    find_system_font,
    load_gdrive_map,
    parse_timestamp_to_seconds,
    resolve_gdrive_source,
    resolve_part_window,
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

    def test_gdrive_resolver_precedence(self):
        """env override > map file > catalog field; nothing configured -> None."""
        def with_env(**kwargs):
            saved = {k: os.environ.get(k) for k in kwargs}
            for k, v in kwargs.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return saved

        try:
            # 1. Nothing configured anywhere.
            saved = with_env(GDRIVE_FILE_ID=None, GDRIVE_FILE_NAME=None)
            with tempfile.TemporaryDirectory() as tmp:
                with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", Path(tmp)):
                    self.assertIsNone(resolve_gdrive_source("the_avengers_2012", {}))

            # 2. Catalog field is the last resort.
            with_env(GDRIVE_FILE_ID=None, GDRIVE_FILE_NAME=None)
            with tempfile.TemporaryDirectory() as tmp:
                with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", Path(tmp)):
                    resolved = resolve_gdrive_source("m", {"gdrive_file_id": "CAT1"})
            self.assertEqual(resolved["file_id"], "CAT1")

            # 3. Map file beats the catalog field and supplies timeline + filename.
            with_env(GDRIVE_FILE_ID=None, GDRIVE_FILE_NAME=None)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / GDRIVE_MAP_FILENAME).write_text(json.dumps({
                    "movies": {"the_avengers_2012": {
                        "file_id": "MAP1",
                        "file_name": "avengers.mkv",
                        "timeline_start": "00:05:00",
                        "timeline_end": "00:31:00",
                    }}
                }), encoding="utf-8")
                with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", root):
                    resolved = resolve_gdrive_source(
                        "the_avengers_2012", {"gdrive_file_id": "CAT1"}
                    )
            self.assertEqual(resolved["file_id"], "MAP1")
            self.assertEqual(resolved["file_name"], "avengers.mkv")
            self.assertEqual(resolved["timeline_start"], "00:05:00")

            # 4. Env override wins over everything.
            with_env(GDRIVE_FILE_ID="ENV1", GDRIVE_FILE_NAME="env.mkv")
            with tempfile.TemporaryDirectory() as tmp:
                with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", Path(tmp)):
                    resolved = resolve_gdrive_source(
                        "the_avengers_2012", {"gdrive_file_id": "CAT1"}
                    )
            self.assertEqual(resolved["file_id"], "ENV1")
        finally:
            for key in ("GDRIVE_FILE_ID", "GDRIVE_FILE_NAME"):
                os.environ.pop(key, None)

    def test_gdrive_map_tolerates_missing_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", root):
                self.assertEqual(load_gdrive_map(), {})
                (root / GDRIVE_MAP_FILENAME).write_text("{not json", encoding="utf-8")
                self.assertEqual(load_gdrive_map(), {})
                (root / GDRIVE_MAP_FILENAME).write_text('["a list"]', encoding="utf-8")
                self.assertEqual(load_gdrive_map(), {})

    def test_gdrive_map_accepts_flat_shorthand(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / GDRIVE_MAP_FILENAME).write_text(
                json.dumps({"the_platform_2019": "FLAT1"}), encoding="utf-8"
            )
            with unittest.mock.patch.object(movie_video_engine, "WORKSPACE_DIR", root):
                resolved = resolve_gdrive_source("the_platform_2019", {})
        self.assertEqual(resolved["file_id"], "FLAT1")
        self.assertEqual(resolved["file_name"], "the_platform_2019.mkv")

    def test_drive_is_attempted_before_trailer(self):
        """Google Drive must be the highest automatic footage source."""
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        drive_at = orchestrator.index('source_type = "private_media"')
        trailer_at = orchestrator.index('source_type = "authorized_trailer"')
        self.assertLess(
            drive_at, trailer_at,
            "Google Drive must be attempted before the trailer fallback",
        )
        # The Drive branch must not be chained behind --video-file with elif.
        drive_block = orchestrator[drive_at - 200:drive_at]
        self.assertNotIn("elif", drive_block.split("if upload_requested")[-1])

    def test_episode_detection_handles_common_release_names(self):
        cases = {
            "[SubsPlease] One Piece - 001 (1080p) [AB12].mkv": 1,
            "One.Piece.EP105.1080p.mkv": 105,
            "Naruto Shippuden S01E12.mkv": 12,
            "Breaking.Bad.S02E05.720p.mkv": 5,
            "The.Office.103.avi": 103,
            # A 4-digit year is not an episode number.
            "The.Avengers.2012.720p.BluRay.mkv": None,
            "Interstellar.2014.1080p.mkv": None,
            # A resolution is not an episode number.
            "Some.Movie.720p.x264.mkv": None,
        }
        for filename, expected in cases.items():
            self.assertEqual(
                detect_episode_number(filename), expected,
                f"{filename} -> expected {expected}",
            )

    def test_episode_files_group_despite_release_tags(self):
        """Differently-tagged files of one series must share a group id."""
        tagged = "[SubsPlease] One Piece - 001 (1080p) [AB12].mkv"
        plain = "One.Piece.EP105.1080p.mkv"
        self.assertEqual(slugify(series_stem(tagged)), slugify(series_stem(plain)))

    def test_curated_titles_are_never_duplicated_by_auto_ingest(self):
        """A curated movie must not get a second, auto-generated entry."""
        curated = _curated_titles()
        self.assertTrue(curated, "curated catalog should not be empty")
        for filename in (
            "The.Avengers.2012.720p.BluRay.HIN-ENG.x264.ESub-KatmovieHD.mkv",
            "Thor.Ragnarok.2017.720p.BluRay.HIN-ENG.x264.mkv",
            "Interstellar.2014.1080p.BluRay.x264.mkv",
        ):
            self.assertTrue(
                _title_matches_curated(filename, curated),
                f"{filename} should be recognised as already curated",
            )
        # Genuinely new titles must still be registrable.
        for filename in (
            "Dune.2021.2160p.WEB-DL.mkv",
            "Dune.Part.Two.2024.1080p.mkv",
            "Jujutsu.Kaisen.S01E01.1080p.mkv",
        ):
            self.assertFalse(
                _title_matches_curated(filename, curated),
                f"{filename} should NOT be treated as curated",
            )

    def test_plan_windows_cover_usable_span(self):
        windows = plan_windows(6)
        self.assertEqual(len(windows), 6)
        self.assertEqual(windows[0][0], 0.04)
        self.assertEqual(windows[-1][1], 0.96)
        for (_, end), (next_start, _) in zip(windows, windows[1:]):
            self.assertAlmostEqual(end, next_start, places=4)
        self.assertEqual(plan_windows(1), [(0.04, 0.96)])

    def test_generated_entry_partitions_a_movie_into_parts(self):
        entry = build_entry(
            [("Some.Movie.2020.1080p.mkv", "FILEID1", None)],
            "some_movie_2020",
        )
        self.assertIsNotNone(entry)
        self.assertEqual(len(entry["parts"]), 6)
        for part in entry["parts"]:
            self.assertIn("window_start_frac", part)
            self.assertIn("window_end_frac", part)
            self.assertEqual(part["gdrive_file_id"], "FILEID1")

    def test_generated_entry_makes_one_part_per_episode(self):
        files = [
            ("Naruto Shippuden S01E01.mkv", "F1", 1),
            ("Naruto Shippuden S01E02.mkv", "F2", 2),
            ("Naruto Shippuden S01E03.mkv", "F3", 3),
        ]
        entry = build_entry(files, "naruto_shippuden")
        self.assertEqual(len(entry["parts"]), 3)
        self.assertEqual([p["episode_number"] for p in entry["parts"]], [1, 2, 3])
        self.assertEqual([p["gdrive_file_id"] for p in entry["parts"]], ["F1", "F2", "F3"])
        for part in entry["parts"]:
            self.assertEqual((part["window_start_frac"], part["window_end_frac"]), (0.04, 0.96))

    def test_fractional_windows_resolve_against_real_duration(self):
        part = {"window_start_frac": 0.04, "window_end_frac": 0.5}
        with unittest.mock.patch("movie_video_engine.get_audio_duration", return_value=7200.0):
            start, end = resolve_part_window(part, Path("movie.mkv"))
        self.assertEqual((start, end), ("00:04:48", "01:00:00"))

        # Without fractions the catalog timeline is used unchanged.
        curated = {"timeline_start": "00:05:00", "timeline_end": "00:20:00"}
        with unittest.mock.patch("movie_video_engine.get_audio_duration", return_value=7200.0):
            self.assertEqual(
                resolve_part_window(curated, Path("movie.mkv")),
                ("00:05:00", "00:20:00"),
            )

    def test_auto_catalog_merges_without_clobbering_curated(self):
        curated_movies = load_catalog()["movies"]
        curated_avengers = next(m for m in curated_movies if m["id"] == "the_avengers_2012")
        self.assertFalse(curated_avengers.get("auto_generated", False))
        self.assertEqual(len(curated_avengers.get("parts", [])), 8)

    def test_history_round_trip_preserves_audit_keys(self):
        """Unknown top-level keys must survive load/save, or audit trails are lost."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            original = {
                "uploaded_movies": [],
                "superseded_uploads": [{"movie_id": "x", "youtube_id": "abc123"}],
                "reset_note": "series reset",
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            save_history(path, load_history(path))
            reloaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["superseded_uploads"], original["superseded_uploads"])
        self.assertEqual(reloaded["reset_note"], "series reset")

    def test_superseded_uploads_do_not_block_rerender(self):
        """Records moved aside must not be counted as uploaded parts."""
        history = {
            "uploaded_movies": [],
            "superseded_uploads": [
                {
                    "movie_id": "the_avengers_2012",
                    "part_number": 1,
                    "status": "uploaded",
                    "youtube_id": "kj4AsXOcxBY",
                }
            ],
        }
        self.assertEqual(uploaded_part_numbers(history, "the_avengers_2012"), set())

    def test_upload_refuses_placeholder_render_by_default(self):
        """A Short with no licensed footage must not reach the channel."""
        # Hardcoded on purpose: deriving the expectation from the constant would
        # make this test pass vacuously if the constant were emptied.
        self.assertIn("neutral_fallback", PLACEHOLDER_SOURCE_TYPES)
        with self.assertRaises(MediaValidationError):
            validate_upload_source("neutral_fallback")
        # Explicit operator opt-in is the only way through.
        validate_upload_source("neutral_fallback", allow_fallback=True)

    def test_upload_allows_licensed_footage_sources(self):
        for source_type in ("authorized_trailer", "local_media", "private_media"):
            validate_upload_source(source_type)
            validate_upload_source(source_type, allow_fallback=False)

    def test_orchestrator_wires_the_placeholder_upload_guard(self):
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        self.assertIn("validate_upload_source(", orchestrator)
        self.assertIn("ALLOW_FALLBACK_UPLOAD", orchestrator)

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
            extractor_values = [
                cmd[i + 1] for i, a in enumerate(cmd) if a == "--extractor-args"
            ]
            # A command must select exactly one player client...
            clients = [v for v in extractor_values if v.startswith("youtube:player_client=")]
            self.assertEqual(
                len(clients), 1,
                f"expected exactly one player_client arg, got {extractor_values}",
            )
            # ...and any PO Token provider arg must carry a base_url.
            for value in extractor_values:
                if value.startswith("youtubepot-"):
                    self.assertIn("base_url=", value)
            self.assertIn("-f", cmd)
            fmt_value = cmd[cmd.index("-f") + 1]
            self.assertNotIn("player_client", fmt_value, f"-f got {fmt_value!r}")
            self.assertTrue(fmt_value.startswith(("bv*", "b")), f"-f got {fmt_value!r}")

    def test_pot_provider_extractor_arg_is_emitted_per_client(self):
        """Every client must be pointed at the PO Token provider, else it is unused."""
        recorded: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            recorded.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: bot check")

        provider = "http://127.0.0.1:4416"
        with unittest.mock.patch.dict(os.environ, {"YT_DLP_POT_PROVIDER_URL": provider}):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "trailer.mp4"
                with unittest.mock.patch.object(movie_video_engine.subprocess, "run", side_effect=fake_run):
                    download_movie_trailer("m", out, trailer_url="https://example.invalid/v")

        self.assertTrue(recorded)
        for cmd in recorded:
            self.assertIn(
                f"youtubepot-bgutilhttp:base_url={provider}", cmd,
                "PO Token provider address missing from yt-dlp invocation",
            )


def _fake_run(payload: dict) -> dict:
    """Build a unittest.mock.patch kwargs dict returning a canned ffprobe payload."""

    class _Result:
        stdout = json.dumps(payload)
        stderr = ""

    return {"return_value": _Result()}


if __name__ == "__main__":
    unittest.main()
