"""Write ID3/equivalent tags to exported audio files.

Uses mutagen. Any failure is logged and swallowed — we never want a tag
write to crash the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_bpm(bpm: float | None) -> str | None:
    if bpm is None:
        return None
    return f"{bpm:.2f}".rstrip("0").rstrip(".") or "0"


def write_export_metadata(
    file_path: Path,
    project_name: str,
    bpm: float | None,
    session_date: str,
) -> bool:
    """Write project_name / bpm / session_date tags to an audio file.

    Returns True on success, False if anything went wrong (silent).
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    bpm_str = _format_bpm(bpm)

    try:
        if suffix == ".mp3":
            return _write_mp3(file_path, project_name, bpm_str, session_date)
        if suffix in (".wav", ".wave"):
            return _write_wav(file_path, project_name, bpm_str, session_date)
        if suffix in (".aif", ".aiff"):
            return _write_aiff(file_path, project_name, bpm_str, session_date)
        # FLAC and other VorbisComment-style containers.
        if suffix in (".flac", ".ogg", ".oga", ".opus"):
            return _write_vorbis(file_path, project_name, bpm_str, session_date)
    except Exception:
        logger.exception("Tag write failed for %s", file_path)
        return False

    logger.debug("No tag writer for %s", suffix)
    return False


def _write_mp3(path: Path, project: str, bpm: str | None, date: str) -> bool:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TBPM, TDRC  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TALB(encoding=3, text=project))
        if bpm:
            tags.add(TBPM(encoding=3, text=bpm))
        tags.add(TDRC(encoding=3, text=date))
        tags.save(str(path))
        return True
    except Exception:
        logger.exception("MP3 tag write failed for %s", path)
        return False


def _write_wav(path: Path, project: str, bpm: str | None, date: str) -> bool:
    try:
        from mutagen.wave import WAVE  # type: ignore[import-untyped]
        from mutagen.id3 import TALB, TBPM, TDRC  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        audio = WAVE(str(path))
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        if tags is not None:
            tags.add(TALB(encoding=3, text=project))
            if bpm:
                tags.add(TBPM(encoding=3, text=bpm))
            tags.add(TDRC(encoding=3, text=date))
        audio.save()
        return True
    except Exception:
        logger.exception("WAV tag write failed for %s", path)
        return False


def _write_aiff(path: Path, project: str, bpm: str | None, date: str) -> bool:
    try:
        from mutagen.aiff import AIFF  # type: ignore[import-untyped]
        from mutagen.id3 import TALB, TBPM, TDRC  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        audio = AIFF(str(path))
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        if tags is not None:
            tags.add(TALB(encoding=3, text=project))
            if bpm:
                tags.add(TBPM(encoding=3, text=bpm))
            tags.add(TDRC(encoding=3, text=date))
        audio.save()
        return True
    except Exception:
        logger.exception("AIFF tag write failed for %s", path)
        return False


def _write_vorbis(path: Path, project: str, bpm: str | None, date: str) -> bool:
    try:
        from mutagen import File as MutagenFile  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        audio = MutagenFile(str(path))
        if audio is None:
            return False
        audio["ALBUM"] = project
        if bpm:
            audio["BPM"] = bpm
        audio["DATE"] = date
        audio.save()
        return True
    except Exception:
        logger.exception("VorbisComment tag write failed for %s", path)
        return False
