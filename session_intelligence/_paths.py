"""Resolve filesystem paths (data dir, db path) from env or platform defaults."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """Return the platform-default data directory for the app.

    Honors ``AI_RECORD_LABEL_DATA`` if set.
    """
    env = os.environ.get("AI_RECORD_LABEL_DATA")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ai-record-label"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "ai-record-label"
        return home / "AppData" / "Roaming" / "ai-record-label"
    # Linux / other
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "ai-record-label"
    return home / ".local" / "share" / "ai-record-label"


def default_db_path() -> Path:
    """Return the default SQLite DB path."""
    return default_data_dir() / "hermes.db"
