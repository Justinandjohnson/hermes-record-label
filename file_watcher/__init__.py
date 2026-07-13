"""File watcher service for AI Record Label.

Monitors a directory for new audio files, validates them, creates track
records in SQLite, and emits Hermes events to trigger the A&R pipeline.
"""

from file_watcher.naming_parser import ParsedFilename, parse_filename
from file_watcher.validator import ValidationResult, validate_audio_file

__all__ = [
    "ParsedFilename",
    "ValidationResult",
    "parse_filename",
    "validate_audio_file",
]
