"""Tests for audio file format validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_watcher.validator import (
    MAX_FILE_SIZE,
    MIN_FILE_SIZE,
    AudioFormat,
    ValidationResult,
    _detect_format,
    validate_audio_file,
)


# ---------------------------------------------------------------------------
# Magic byte detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    """Unit tests for _detect_format."""

    def test_wav_magic(self) -> None:
        # RIFF....WAVE
        header = b"RIFF\x00\x00\x00\x00WAVE"
        assert _detect_format(header) is AudioFormat.WAV

    def test_flac_magic(self) -> None:
        header = b"fLaC\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is AudioFormat.FLAC

    def test_ogg_magic(self) -> None:
        header = b"OggS\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is AudioFormat.OGG

    def test_aiff_magic(self) -> None:
        header = b"FORM\x00\x00\x00\x00AIFF"
        assert _detect_format(header) is AudioFormat.AIFF

    def test_aifc_magic(self) -> None:
        header = b"FORM\x00\x00\x00\x00AIFC"
        assert _detect_format(header) is AudioFormat.AIFF

    def test_mp3_id3_magic(self) -> None:
        header = b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is AudioFormat.MP3

    def test_mp3_sync_bytes(self) -> None:
        # 0xFF 0xFB is a common MP3 sync word.
        header = b"\xff\xfb\x90\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is AudioFormat.MP3

    def test_mp3_sync_edge(self) -> None:
        # 0xFF 0xE0 is the minimum valid sync (all 3 bits set).
        header = b"\xff\xe0\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is AudioFormat.MP3

    def test_riff_not_wave(self) -> None:
        # RIFF but not WAVE -- e.g. AVI
        header = b"RIFF\x00\x00\x00\x00AVI "
        assert _detect_format(header) is None

    def test_form_not_aiff(self) -> None:
        # FORM but not AIFF/AIFC
        header = b"FORM\x00\x00\x00\x008SVX"
        assert _detect_format(header) is None

    def test_unknown_format(self) -> None:
        header = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        assert _detect_format(header) is None

    def test_too_short(self) -> None:
        assert _detect_format(b"\x00\x00") is None
        assert _detect_format(b"") is None

    def test_pdf_rejected(self) -> None:
        header = b"%PDF-1.4\x00\x00\x00\x00"
        assert _detect_format(header) is None

    def test_png_rejected(self) -> None:
        header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00"
        assert _detect_format(header) is None


# ---------------------------------------------------------------------------
# Full validation with real files
# ---------------------------------------------------------------------------


def _write_file(path: Path, header: bytes, total_size: int) -> Path:
    """Write a fake audio file with the given header, padded to total_size."""
    padding = total_size - len(header)
    if padding < 0:
        padding = 0
    path.write_bytes(header + b"\x00" * padding)
    return path


class TestValidateAudioFile:
    """Integration tests for validate_audio_file."""

    def test_valid_wav(self, tmp_path: Path) -> None:
        # Minimal WAV-like file: RIFF + WAVE + fmt sub-chunk in header.
        wav_header = b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        wav_header += b"\x01\x00\x02\x00\x44\xac\x00\x00\x10\xb1\x02\x00"
        wav_header += b"\x04\x00\x10\x00data\x00\x00\x00\x00"
        path = _write_file(tmp_path / "test.wav", wav_header, 20_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.WAV
        assert result.file_size == 20_000

    def test_valid_flac(self, tmp_path: Path) -> None:
        # fLaC + STREAMINFO metadata block (type 0).
        header = b"fLaC\x00\x00\x00\x22" + b"\x00" * 34
        path = _write_file(tmp_path / "test.flac", header, 15_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.FLAC

    def test_valid_ogg(self, tmp_path: Path) -> None:
        header = b"OggS\x00\x00\x00\x00\x00\x00\x00\x00"
        path = _write_file(tmp_path / "test.ogg", header, 12_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.OGG

    def test_valid_aiff(self, tmp_path: Path) -> None:
        header = b"FORM\x00\x00\x00\x00AIFF"
        path = _write_file(tmp_path / "test.aiff", header, 11_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.AIFF

    def test_valid_mp3_id3(self, tmp_path: Path) -> None:
        header = b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00"
        path = _write_file(tmp_path / "test.mp3", header, 50_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.MP3

    def test_valid_mp3_sync(self, tmp_path: Path) -> None:
        header = b"\xff\xfb\x90\x00" + b"\x00" * 8
        path = _write_file(tmp_path / "test.mp3", header, 30_000)
        result = validate_audio_file(path)
        assert result.is_valid
        assert result.format is AudioFormat.MP3

    def test_file_too_small(self, tmp_path: Path) -> None:
        header = b"RIFF\x00\x00\x00\x00WAVEfmt "
        path = _write_file(tmp_path / "tiny.wav", header, 500)
        result = validate_audio_file(path)
        assert not result.is_valid
        assert "too small" in (result.rejection_reason or "").lower()

    def test_file_too_large(self, tmp_path: Path) -> None:
        # Don't actually write 200MB -- just test the stat path.
        path = tmp_path / "huge.wav"
        # Create a sparse file if the OS supports it; otherwise skip.
        try:
            with path.open("wb") as f:
                f.seek(MAX_FILE_SIZE + 1)
                f.write(b"\x00")
        except OSError:
            pytest.skip("Cannot create large sparse file on this platform")
        result = validate_audio_file(path)
        assert not result.is_valid
        assert "too large" in (result.rejection_reason or "").lower()

    def test_non_audio_format(self, tmp_path: Path) -> None:
        path = _write_file(tmp_path / "readme.txt", b"Hello world! " * 1000, 13_000)
        result = validate_audio_file(path)
        assert not result.is_valid
        assert "format" in (result.rejection_reason or "").lower()

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        result = validate_audio_file(tmp_path / "does_not_exist.wav")
        assert not result.is_valid
        assert "exist" in (result.rejection_reason or "").lower()

    def test_corrupt_wav_missing_fmt(self, tmp_path: Path) -> None:
        # RIFF+WAVE but no fmt sub-chunk.
        header = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32
        path = _write_file(tmp_path / "corrupt.wav", header, 20_000)
        result = validate_audio_file(path)
        assert not result.is_valid
        assert "corrupt" in (result.rejection_reason or "").lower()

    def test_corrupt_flac_bad_block_type(self, tmp_path: Path) -> None:
        # fLaC followed by invalid metadata block type (127 = reserved).
        header = b"fLaC\x7f\x00\x00\x22" + b"\x00" * 34
        path = _write_file(tmp_path / "corrupt.flac", header, 15_000)
        result = validate_audio_file(path)
        assert not result.is_valid
        assert "corrupt" in (result.rejection_reason or "").lower()
