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
    _has_real_scripts,
    _title_matches_curated,
    build_entry,
    detect_episode_number,
    humanize,
    parse_season_episode,
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
    generate_speech_audio,
    humanize_speech_text,
    find_system_font,
    load_gdrive_map,
    parse_timestamp_to_seconds,
    resolve_gdrive_source,
    resolve_part_window,
    OUTPUT_DIR,
    BGM_DIR,
    DEFAULT_BGM_OFFSETS,
    get_default_bgm_offset,
    resolve_non_copyright_bgm,
    detect_character_gender,
    resolve_character_voice,
    is_indian_movie,
)
from movie_ai_script import is_stale_critic_analysis_script



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
        # Explicitly clear provider keys so this exercises the template fallback
        # deterministically instead of depending on the ambient environment (and
        # never making a real network call). The title must not exist in the
        # curated catalog, otherwise the catalog branch answers instead.
        with unittest.mock.patch.dict(os.environ, {
            "GROQ_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "",
        }):
            res = generate_movie_script_ai("Zzqx Nonexistent Film 2999", language="en")
        self.assertIsNotNone(res)
        self.assertIn("script", res)
        self.assertGreater(len(res["script"].split()), 50)
        self.assertEqual(res.get("script_source"), "template")

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

    def test_provider_word_timestamp_generation(self):
        sentences = [
            {
                "text": "Wait look at that",
                "start": 0.1,
                "end": 1.2,
                "words": [
                    {"word": "Wait", "start": 0.1, "end": 0.5},
                    {"word": "look", "start": 0.6, "end": 0.8},
                    {"word": "at", "start": 0.82, "end": 0.95},
                    {"word": "that", "start": 0.98, "end": 1.2},
                ]
            }
        ]
        words = create_word_timestamps_from_sentences(sentences)
        self.assertEqual(len(words), 4)
        self.assertEqual(words[0]["word"], "WAIT")
        self.assertEqual(words[0]["start"], 0.1)
        self.assertEqual(words[0]["end"], 0.5)
        self.assertEqual(words[1]["word"], "LOOK")
        self.assertEqual(words[1]["start"], 0.6)

    def test_humanize_speech_text_contractions_and_pauses(self):
        raw = "Bro, imagine you are running a base. They cannot stop him; he does not care. Wait did you see that? It is insane."
        humanized = humanize_speech_text(raw)
        self.assertIn("you're", humanized)
        self.assertIn("can't", humanized)
        self.assertIn("doesn't", humanized)
        self.assertIn("It's", humanized)
        self.assertIn("Wait —", humanized)
        self.assertIn(" — ", humanized)

    def test_generate_speech_audio_signature_and_defaults(self):
        import inspect
        sig = inspect.signature(generate_speech_audio)
        self.assertEqual(sig.parameters["voice"].default, "en-US-EmmaNeural")
        self.assertEqual(sig.parameters["rate"].default, "+2%")
        self.assertEqual(sig.parameters["pitch"].default, "+2Hz")

    def test_is_stale_critic_analysis_script(self):
        critic_script = "What 99% of viewers missed in Avengers Endgame is the quiet grief. Notice how Russo's camera lingers, turning the quantum realm into a visual metaphor."
        self.assertTrue(is_stale_critic_analysis_script(critic_script))

        story_recap_script = "Bro — imagine waking up to find a rogue AI just hacked Earth's deadliest weapons. Tony Stark tries to stop it, but Ultron escapes into the internet!"
        self.assertFalse(is_stale_critic_analysis_script(story_recap_script))

    def test_detect_character_gender_and_resolve_voice(self):
        female_script = "Hope Annabelle is a former gymnast who lost everything. She is broke and sleeping in her dad's basement until a letter changes her life."
        self.assertEqual(detect_character_gender(female_script), "female")
        self.assertEqual(resolve_character_voice("female", language="en"), "en-US-EmmaNeural")
        self.assertEqual(resolve_character_voice("female", language="hi"), "hi-IN-SwaraNeural")

        male_script = "Tony Stark and Steve Rogers assemble the team to hunt down Thanos. He wields the infinity gauntlet with unstoppable force."
        self.assertEqual(detect_character_gender(male_script), "male")
        self.assertEqual(resolve_character_voice("male", language="en"), "en-US-EmmaNeural")
        self.assertEqual(resolve_character_voice("male", language="hi"), "hi-IN-MadhurNeural")

    def test_is_indian_movie_detection(self):
        # Known Indian titles
        self.assertTrue(is_indian_movie(title="RRR (2022)"))
        self.assertTrue(is_indian_movie(title="KGF Chapter 2"))
        self.assertTrue(is_indian_movie(title="Pushpa: The Rise"))
        self.assertTrue(is_indian_movie(title="Stree 2"))
        self.assertTrue(is_indian_movie(title="Tumbbad (2018)"))
        self.assertTrue(is_indian_movie(title="Mirzapur Season 3"))
        self.assertTrue(is_indian_movie(title="Dangal"))
        self.assertTrue(is_indian_movie(title="Baahubali: The Beginning"))
        self.assertTrue(is_indian_movie(title="Jawan"))
        self.assertTrue(is_indian_movie(title="Panchayat Season 2"))

        # Explicit metadata
        self.assertTrue(is_indian_movie(movie_data={"language": "hindi", "title": "Random Movie"}))
        self.assertTrue(is_indian_movie(movie_data={"country": "India", "title": "Random Movie"}))
        self.assertTrue(is_indian_movie(movie_data={"genre": "Bollywood Thriller", "title": "Random"}))
        self.assertTrue(is_indian_movie(movie_data={"tags": ["bollywood", "action"], "title": "Random"}))

        # Devanagari script detection
        self.assertTrue(is_indian_movie(title="दंगल"))
        self.assertTrue(is_indian_movie(script_text="यह एक रहस्यमयी कहानी है"))

        # Non-Indian titles
        self.assertFalse(is_indian_movie(title="The Avengers (2012)"))
        self.assertFalse(is_indian_movie(title="Dune Part Two"))
        self.assertFalse(is_indian_movie(title="The Bronze (2015)"))
        self.assertFalse(is_indian_movie(title="Interstellar (2014)"))

    def test_indian_movie_auto_hindi_voice_routing(self):
        from generate_movie_short import select_next_movie
        # Indian movie automatically resolves to Hindi voice
        male_indian = {"title": "KGF Chapter 2", "script": "Rocky bhai arrives in KGF with immense power and he rules the empire."}
        gender = detect_character_gender(male_indian["script"], male_indian)
        self.assertEqual(gender, "male")
        self.assertTrue(is_indian_movie(movie_data=male_indian))
        voice = resolve_character_voice(gender, language="hi")
        self.assertEqual(voice, "hi-IN-MadhurNeural")

        female_indian = {"title": "Stree 2", "script": "She roams the village at night in a red saree and her whispers echo in the dark."}
        gender = detect_character_gender(female_indian["script"], female_indian)
        self.assertEqual(gender, "female")
        self.assertTrue(is_indian_movie(movie_data=female_indian))
        voice = resolve_character_voice(gender, language="hi")
        self.assertEqual(voice, "hi-IN-SwaraNeural")

    def test_resolve_non_copyright_bgm_mitski(self):
        resolved = resolve_non_copyright_bgm(None)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.exists())
        self.assertIn(resolved.name, ["cinematic_suspense_thriller.mp3", "cinematic_suspense_drone.mp3"])

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

    def test_bgm_track_is_configurable_and_present(self):
        """Non-copyright BGM is enabled by default and verified royalty-free."""
        from movie_video_engine import resolve_non_copyright_bgm, DEFAULT_NON_COPYRIGHT_BGM
        self.assertTrue(
            (BGM_DIR / "cinematic_suspense_thriller.mp3").exists(),
            "the configured non-copyright BGM track must exist in assets/bgm/",
        )
        orchestrator = (WORKSPACE_DIR / "generate_movie_short.py").read_text(encoding="utf-8")
        self.assertIn("MOVIE_BGM_PATH", orchestrator)
        self.assertIn("get_bgm_offset_for_part", orchestrator)
        # Test that resolve_non_copyright_bgm blocks copyrighted music
        safe_resolved = resolve_non_copyright_bgm("assets/bgm/malevolent_shrine_sukuna.mp3")
        self.assertEqual(safe_resolved, DEFAULT_NON_COPYRIGHT_BGM)

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
        validate_media_info(MediaInfo(120.0, 1080, 1920, True))  # Detailed explanation length supported
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(180.1, 1080, 1920, True))
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(50.0, 720, 1280, True))
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(50.0, 1080, 1920, False))

    def test_long_video_validation_supports_extended_duration_and_resolutions(self):
        # Stitched full movie long videos can run for minutes/hours and be vertical or landscape
        validate_media_info(MediaInfo(1200.0, 1080, 1920, True), is_long_video=True)
        validate_media_info(MediaInfo(1200.0, 1920, 1080, True), is_long_video=True)
        validate_media_info(MediaInfo(600.0, 1280, 720, True), is_long_video=True)
        with self.assertRaises(MediaValidationError):
            validate_media_info(MediaInfo(1200.0, 400, 300, True), is_long_video=True)

    def test_full_video_duplicate_key_isolation(self):
        from movie_pipeline_state import episode_key
        part_key = episode_key("avengers", 1)
        full_key = episode_key("avengers", None, is_full_video=True)
        self.assertEqual(part_key, "avengers:p1")
        self.assertEqual(full_key, "avengers:full_video")
        self.assertNotEqual(part_key, full_key)

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
        # Mocked: an unmocked build_entry would call a real AI provider whenever
        # an API key happens to be present in the environment.
        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "real", "script_source": "groq"},
        ):
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
        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "real", "script_source": "groq"},
        ):
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

    def test_copy_of_prefix_is_stripped(self):
        """Google Drive marks duplicates with 'Copy of '; it must not leak."""
        self.assertEqual(
            humanize("Copy of Jujutsu Kaisen - S01E01 1080p.mkv"),
            humanize("Jujutsu Kaisen - S01E01 1080p.mkv"),
        )
        self.assertEqual(
            humanize("Copy of Copy of Jujutsu Kaisen - S01E01 1080p.mkv"),
            humanize("Jujutsu Kaisen - S01E01 1080p.mkv"),
        )

    def test_season_episode_with_delimiters(self):
        """S01_E01, S04_E11_1, and S02.E03 must parse season and episode correctly."""
        season, episode, prefix = parse_season_episode("Demon_Slayer_Kimetsu_no_Yaiba_480P_S01_E01.mp4")
        self.assertEqual(season, 1)
        self.assertEqual(episode, 1)
        self.assertEqual(slugify(series_stem("Demon_Slayer_Kimetsu_no_Yaiba_480P_S01_E01.mp4")), "demon_slayer_kimetsu_no_yaiba")

        season4, episode11, _ = parse_season_episode("Demon_Slayer_Kimetsu_no_Yaiba_480P_S04_E11_1.mp4")
        self.assertEqual(season4, 4)
        self.assertEqual(episode11, 11)

    def test_seasons_do_not_collide(self):
        """S01E01, S02E01 and S03E01 are three distinct episodes."""
        seasons = {
            name: parse_season_episode(name)[0]
            for name in (
                "Copy of Jujutsu Kaisen S01E01 1080p.mkv",
                "Copy of Jujutsu Kaisen-S02E01-720p.mkv",
                "Copy of Jujutsu_Kaisen_S03E01_1080p_HEVC.mkv",
            )
        }
        self.assertEqual(seasons, {
            "Copy of Jujutsu Kaisen S01E01 1080p.mkv": 1,
            "Copy of Jujutsu Kaisen-S02E01-720p.mkv": 2,
            "Copy of Jujutsu_Kaisen_S03E01_1080p_HEVC.mkv": 3,
        })

    def test_watermark_after_episode_tag_does_not_split_the_group(self):
        """'PIKAHD.COM' quality tags must not create separate series."""
        names = [
            "Copy of Jujutsu Kaisen - S01E01 1080p 10bit [HIN-ENG] x265.mkv",
            "Copy of  Jujutsu Kaisen S01E02-1080p-[HINDI-ENGLISH]-PIKAHD.COM.mkv",
            "Copy of Jujutsu Kaisen - S02E01-720p-[HIN-ENG-JAP]-PIKAHD.EU.mkv",
        ]
        keys = {slugify(series_stem(name)) for name in names}
        self.assertEqual(len(keys), 1, f"expected one group, got {keys}")
        self.assertEqual(keys.pop(), "jujutsu_kaisen")

    def test_multi_season_series_orders_by_season_then_episode(self):
        files = [
            ("Jujutsu Kaisen S01E02.mkv", "F1", 2),
            ("Jujutsu Kaisen S02E01.mkv", "F2", 1),
            ("Jujutsu Kaisen S01E01.mkv", "F3", 1),
            ("Jujutsu Kaisen S02E02.mkv", "F4", 2),
        ]
        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "real", "script_source": "groq"},
        ):
            entry = build_entry(files, "jujutsu_kaisen")
        self.assertEqual(
            [(p["season"], p["episode_number"]) for p in entry["parts"]],
            [(1, 1), (1, 2), (2, 1), (2, 2)],
        )

    def test_placeholder_scripts_are_flagged_and_refused(self):
        """Auto-ingest must not register 55 episodes of identical filler."""
        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "generic filler", "script_source": "template"},
        ):
            entry = build_entry([("Some.Movie.2020.mkv", "FID", None)], "some_movie")
        self.assertFalse(_has_real_scripts(entry), "template scripts must not count as real")

        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "real analysis", "script_source": "groq"},
        ):
            good = build_entry([("Some.Movie.2020.mkv", "FID", None)], "some_movie")
        self.assertTrue(_has_real_scripts(good))

    def test_ai_providers_cover_groq_openrouter_and_deepseek(self):
        """Groq's retired model must not be the only option any more."""
        source = (WORKSPACE_DIR / "movie_ai_script.py").read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_MODELS", source)
        self.assertIn("openrouter", source)
        self.assertIn("openai/gpt-oss-120b", source)
        self.assertIn("script_source", source)

    def test_long_series_is_generated_incrementally(self):
        """A 10-episode series must not need 10 AI calls in one run."""
        files = [(f"Anime S01E{i:02d}.mkv", f"F{i}", i) for i in range(1, 11)]
        ready: list[dict] = []
        with unittest.mock.patch(
            "movie_auto_catalog._generate_script",
            return_value={"script": "real", "script_source": "groq"},
        ):
            for _ in range(3):
                entry = build_entry(files, "anime", max_parts=4, skip=len(ready))
                self.assertIsNotNone(entry)
                ready.extend(entry["parts"])
                self.assertLessEqual(len(entry["parts"]), 4)

        self.assertEqual([p["part_number"] for p in ready], list(range(1, 11)))
        self.assertEqual(sorted(p["episode_number"] for p in ready), list(range(1, 11)))
        self.assertEqual(ready[-1]["gdrive_file_id"], "F10")

    def test_per_run_cap_is_configurable(self):
        self.assertIn("AUTO_INGEST_MAX_PARTS_PER_RUN", (WORKSPACE_DIR / "movie_auto_catalog.py").read_text(encoding="utf-8"))

    def test_auto_series_total_parts_reflects_full_length(self):
        """A batched series must not be marked complete after the first batch."""
        from generate_movie_short import EpisodeNotReadyError, select_next_movie
        import generate_movie_short as orchestrator

        entry = {
            "id": "jujutsu_kaisen",
            "title": "Jujutsu Kaisen",
            "parts_available": 55,
            "parts": [
                {"part_number": i, "title": f"Episode {i}", "script": "x"}
                for i in range(1, 7)
            ],
        }
        history = {
            "uploaded_movies": [],
            "current_series": {
                "movie_id": "jujutsu_kaisen", "movie_title": "Jujutsu Kaisen",
                "current_part": 5, "total_parts": 55, "completed": False,
            },
        }
        with unittest.mock.patch.object(orchestrator, "load_movie_history", return_value=history):
            with unittest.mock.patch.object(orchestrator, "load_catalog", return_value={"movies": [entry]}):
                # Part 6 exists, and the series length is 55 - not len(parts)=6.
                selected = select_next_movie()
        self.assertEqual(selected["part_number"], 6)
        self.assertEqual(selected["total_parts"], 55)

        # Asking for an ungenerated episode must fail loudly, not fall back to part 1.
        history["current_series"]["current_part"] = 6
        with unittest.mock.patch.object(orchestrator, "load_movie_history", return_value=history):
            with unittest.mock.patch.object(orchestrator, "load_catalog", return_value={"movies": [entry]}):
                with self.assertRaises(EpisodeNotReadyError):
                    select_next_movie()

    def test_auto_series_part_inherits_gdrive_and_window_metadata(self):
        """Auto-cataloged and episodic parts must carry their Drive and fractional window metadata."""
        from generate_movie_short import select_next_movie
        import generate_movie_short as orchestrator

        entry = {
            "id": "test_series",
            "title": "Test Series",
            "parts_available": 10,
            "parts": [
                {
                    "part_number": 1,
                    "title": "Episode 1",
                    "script": "Narrative script",
                    "gdrive_file_id": "DRIVE_ID_101",
                    "gdrive_file_name": "Test.Series.S01E01.mp4",
                    "window_start_frac": 0.04,
                    "window_end_frac": 0.96,
                }
            ],
        }
        history = {"uploaded_movies": [], "current_series": None}
        with unittest.mock.patch.object(orchestrator, "load_movie_history", return_value=history):
            with unittest.mock.patch.object(orchestrator, "load_catalog", return_value={"movies": [entry]}):
                selected = select_next_movie()

        self.assertEqual(selected["gdrive_file_id"], "DRIVE_ID_101")
        self.assertEqual(selected["gdrive_file_name"], "Test.Series.S01E01.mp4")
        self.assertEqual(selected["window_start_frac"], 0.04)
        self.assertEqual(selected["window_end_frac"], 0.96)

        resolved = resolve_gdrive_source("test_series", selected)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["file_id"], "DRIVE_ID_101")
        self.assertEqual(resolved["file_name"], "Test.Series.S01E01.mp4")

    def test_openrouter_fallback_includes_free_tier_models(self):
        """A zero-credit account still needs a working fallback."""
        from movie_ai_script import OPENROUTER_MODELS
        free = [m for m in OPENROUTER_MODELS if m.endswith(":free")]
        self.assertGreaterEqual(
            len(free), 2,
            "OpenRouter fallback must include verified free-tier models",
        )
        # Models that removed their free tier upstream must not be listed.
        for gone in ("anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001"):
            self.assertNotIn(gone, OPENROUTER_MODELS)

    def test_malformed_provider_envelope_fails_softly(self):
        """A 200 with no choices must not raise KeyError and waste the attempt."""
        import urllib.request
        from movie_ai_script import ModelReplyError, _post_json

        class _FakeResponse:
            def read(self):
                return b'{"error": {"message": "free tier throttled"}}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResponse()
        try:
            with self.assertRaises(ModelReplyError):
                _post_json("https://example.invalid", {"model": "t", "messages": []}, {}, 5)
        finally:
            urllib.request.urlopen = original

    def test_bgm_continues_across_parts(self):
        """Part N must resume where part N-1's music ended."""
        from movie_video_engine import get_bgm_offset_for_part

        # 8 parts of 51s against a 182.6s track.
        track = 182.636
        prior: list[float] = []
        offsets = []
        for _ in range(8):
            offsets.append(get_bgm_offset_for_part(
                len(prior) + 1, prior_part_durations=prior, track_duration=track
            ))
            prior.append(51.0)

        self.assertEqual(offsets[0], 0.0, "first part starts at the beginning")
        # Continuity: each offset equals the running total, wrapped to the track.
        for index in range(1, 8):
            expected = (51.0 * index) % track
            self.assertAlmostEqual(offsets[index], round(expected, 3), places=2)
        # Wrapping keeps every offset playable.
        self.assertTrue(all(0.0 <= o < track for o in offsets))

    def test_bgm_offset_without_history_uses_nominal_length(self):
        from movie_video_engine import get_bgm_offset_for_part
        self.assertEqual(get_bgm_offset_for_part(1, [], track_duration=182.6), 0.0)
        self.assertAlmostEqual(
            get_bgm_offset_for_part(3, [], track_duration=182.6), 104.0, places=2
        )
        # A single standalone video has no part number, so it starts at zero.
        self.assertEqual(get_bgm_offset_for_part(None, [], track_duration=182.6), 0.0)

    def test_bgm_timeline_ignores_unconfirmed_renders(self):
        """Failed/dry runs must not shift the soundtrack timeline."""
        from movie_pipeline_state import confirmed_part_durations
        from movie_video_engine import get_bgm_offset_for_part

        history = {
            "uploaded_movies": [
                # The part that actually shipped.
                {"movie_id": "m", "part_number": 1, "status": "uploaded",
                 "youtube_id": "REAL1", "duration_sec": 50.66},
                # Later re-renders of the same part that never published. These
                # are recorded AFTER the real upload, so an implementation that
                # ignores the confirmed check keeps the wrong duration.
                {"movie_id": "m", "part_number": 1, "status": "rendered",
                 "youtube_id": None, "duration_sec": 96.62},
                {"movie_id": "m", "part_number": 1, "status": "failed",
                 "youtube_id": None, "duration_sec": 12.0},
            ]
        }
        prior = confirmed_part_durations(history, "m", 2)

        # Only the confirmed upload counts, so the offset is 50.66 - not 96.62
        # or 12.0, which is what an unfiltered scan would produce.
        self.assertEqual(prior, [50.66])
        self.assertAlmostEqual(
            get_bgm_offset_for_part(2, prior, track_duration=182.636), 50.66, places=2
        )
        # A different series must not leak into this one's timeline.
        self.assertEqual(confirmed_part_durations(history, "other", 2), [])

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
