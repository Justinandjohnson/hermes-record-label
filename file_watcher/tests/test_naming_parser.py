"""Tests for filename metadata parsing."""

from __future__ import annotations

import pytest

from file_watcher.naming_parser import ParsedFilename, parse_filename


class TestVersionParsing:
    """Detect version markers in filenames."""

    @pytest.mark.parametrize(
        ("filename", "expected_version"),
        [
            ("Track 3 - rough mix v2.wav", 2),
            ("song_v10.flac", 10),
            ("demo version 3.mp3", 3),
            ("beat rev4.wav", 4),
            ("idea revision 6.aiff", 6),
            ("My Song V1.wav", 1),
        ],
    )
    def test_version_detected(self, filename: str, expected_version: int) -> None:
        result = parse_filename(filename)
        assert result.version_hint == expected_version

    def test_no_version(self) -> None:
        result = parse_filename("untitled.wav")
        assert result.version_hint is None


class TestBpmParsing:
    """Detect BPM hints in filenames."""

    @pytest.mark.parametrize(
        ("filename", "expected_bpm"),
        [
            ("demo_beat_120bpm.mp3", 120),
            ("groove 140 bpm.wav", 140),
            ("bpm90_sketch.flac", 90),
            ("bpm 75 idea.wav", 75),
        ],
    )
    def test_bpm_detected(self, filename: str, expected_bpm: int) -> None:
        result = parse_filename(filename)
        assert result.bpm_hint == expected_bpm

    def test_bpm_out_of_range(self) -> None:
        # 10 bpm is unrealistically slow -- should be rejected.
        result = parse_filename("test_10bpm.wav")
        assert result.bpm_hint is None

    def test_no_bpm(self) -> None:
        result = parse_filename("My Song.wav")
        assert result.bpm_hint is None


class TestTagDetection:
    """Detect production-stage tags."""

    def test_rough_tag(self) -> None:
        result = parse_filename("Track 3 - rough mix v2.wav")
        assert result.is_rough
        assert "rough" in result.tags

    def test_demo_tag(self) -> None:
        result = parse_filename("demo_beat_120bpm.mp3")
        assert result.is_rough
        assert "demo" in result.tags

    def test_final_tag(self) -> None:
        result = parse_filename("My Song (final master).flac")
        assert result.is_final
        assert "final" in result.tags
        assert "master" in result.tags

    def test_mix_tag(self) -> None:
        result = parse_filename("groove_mixdown.wav")
        assert "mixdown" in result.tags

    def test_draft_tag(self) -> None:
        result = parse_filename("idea draft.wav")
        assert result.is_rough
        assert "draft" in result.tags

    def test_no_tags(self) -> None:
        result = parse_filename("untitled.wav")
        assert result.tags == []
        assert not result.is_rough
        assert not result.is_final


class TestTitleExtraction:
    """Extract clean titles from filenames."""

    def test_basic_title(self) -> None:
        result = parse_filename("My Song.wav")
        assert result.title == "My Song"

    def test_title_with_version_stripped(self) -> None:
        result = parse_filename("Track 3 - rough mix v2.wav")
        assert "v2" not in result.title.lower()
        assert "Track 3" in result.title

    def test_title_with_parenthetical_stripped(self) -> None:
        result = parse_filename("My Song (final master).flac")
        assert result.title == "My Song"

    def test_title_with_bpm_stripped(self) -> None:
        result = parse_filename("demo_beat_120bpm.mp3")
        assert "120" not in result.title
        assert "bpm" not in result.title.lower()

    def test_underscores_to_spaces(self) -> None:
        result = parse_filename("my_new_song.wav")
        assert result.title == "my new song"

    def test_dashes_to_spaces(self) -> None:
        result = parse_filename("my-new-song.wav")
        assert result.title == "my new song"

    def test_extension_stripped(self) -> None:
        result = parse_filename("My Song.flac")
        assert ".flac" not in result.title

    def test_fallback_to_stem_if_all_stripped(self) -> None:
        # If cleaning removes everything, use the original stem.
        result = parse_filename("rough.wav")
        # "rough" is a tag, so the cleaner might strip it -- but we
        # fallback to the full stem.
        assert result.title  # not empty

    def test_brackets_stripped(self) -> None:
        result = parse_filename("Song [demo v3].wav")
        assert "demo" not in result.title.lower()
        assert result.title.strip() == "Song"


class TestRawFilename:
    """The raw_filename field preserves the original."""

    def test_raw_preserved(self) -> None:
        result = parse_filename("/some/path/Track 3 - rough mix v2.wav")
        assert result.raw_filename == "Track 3 - rough mix v2.wav"


class TestEdgeCases:
    """Pathological filenames."""

    def test_empty_stem(self) -> None:
        result = parse_filename(".wav")
        assert result.title  # should not be empty

    def test_deeply_nested_path(self) -> None:
        result = parse_filename("/a/b/c/d/e/f/Song.wav")
        assert result.title == "Song"

    def test_multiple_versions(self) -> None:
        # "v2" appears first -- parser picks it up.
        result = parse_filename("song v2 revision 5.wav")
        # Should find at least one version.
        assert result.version_hint is not None

    def test_unicode_filename(self) -> None:
        result = parse_filename("Cancion_bonita_v3.wav")
        assert result.version_hint == 3
        assert "bonita" in result.title.lower()
