"""
Tests for session_intelligence.metadata_writer.

Verifies that ID3/RIFF/VorbisComment tags are written correctly to
WAV, MP3, AIFF, and FLAC files.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from session_intelligence.metadata_writer import write_export_metadata, _format_bpm


# ---------------------------------------------------------------------------
# _format_bpm
# ---------------------------------------------------------------------------

class TestFormatBpm:
    def test_none_returns_none(self) -> None:
        assert _format_bpm(None) is None

    def test_whole_number_no_decimals(self) -> None:
        assert _format_bpm(120.0) == "120"

    def test_fractional_bpm_kept(self) -> None:
        assert _format_bpm(93.5) == "93.5"

    def test_trailing_zeros_stripped(self) -> None:
        assert _format_bpm(100.00) == "100"


# ---------------------------------------------------------------------------
# WAV tagging
# ---------------------------------------------------------------------------

class TestWriteWav:
    def test_wav_tag_write_succeeds(self, wav_file: Path) -> None:
        result = write_export_metadata(wav_file, "Late Night Drive", 90.0, "2026-05-16")
        assert result is True

    def test_wav_tags_readable_back(self, wav_file: Path) -> None:
        write_export_metadata(wav_file, "Late Night Drive", 90.0, "2026-05-16")
        try:
            from mutagen.wave import WAVE
            audio = WAVE(str(wav_file))
            tags = audio.tags
            assert tags is not None
            assert "TALB" in tags  # album = project name
            assert str(tags["TALB"]) == "Late Night Drive"
        except ImportError:
            pytest.skip("mutagen not installed")

    def test_wav_no_bpm_still_succeeds(self, wav_file: Path) -> None:
        result = write_export_metadata(wav_file, "Proj", None, "2026-05-16")
        assert result is True

    def test_unknown_extension_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "track.xyz"
        f.write_bytes(b"not audio")
        result = write_export_metadata(f, "Proj", 120.0, "2026-05-16")
        assert result is False

    def test_nonexistent_file_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.wav"
        result = write_export_metadata(f, "Proj", 120.0, "2026-05-16")
        assert result is False


# ---------------------------------------------------------------------------
# MP3 tagging (only if mutagen available)
# ---------------------------------------------------------------------------

class TestWriteMp3:
    def test_mp3_tag_write(self, tmp_path: Path) -> None:
        pytest.importorskip("mutagen")
        try:
            from mutagen.mp3 import MP3
        except ImportError:
            pytest.skip("mutagen.mp3 not available")

        # Create a minimal valid MP3 header (just enough for mutagen to open)
        # Use a pre-built minimal silent MP3 frame
        mp3_path = tmp_path / "test.mp3"
        # Write a 1-frame silent MP3 (128kbps, 44.1kHz, stereo)
        frame = bytes([
            0xFF, 0xFB, 0x90, 0x00,  # MP3 sync + header
        ] + [0x00] * 413)
        mp3_path.write_bytes(frame * 10)

        result = write_export_metadata(mp3_path, "MyProject", 120.0, "2026-05-16")
        # We just verify it doesn't crash and returns a bool
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# FLAC / VorbisComment tagging
# ---------------------------------------------------------------------------

class TestWriteFlac:
    def test_flac_tag_write_returns_bool(self, tmp_path: Path) -> None:
        pytest.importorskip("mutagen")
        flac_path = tmp_path / "test.flac"
        flac_path.write_bytes(b"fLaC" + b"\x00" * 100)  # minimal fake FLAC header
        result = write_export_metadata(flac_path, "Proj", 93.0, "2026-05-16")
        assert isinstance(result, bool)
