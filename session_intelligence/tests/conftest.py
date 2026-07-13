"""Shared pytest fixtures for session_intelligence tests."""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "schema" / "migrations" / "002_sessions.sql"
)
INITIAL_MIGRATION = (
    Path(__file__).resolve().parents[2] / "schema" / "migrations" / "001_initial.sql"
)


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """Return a path to a freshly migrated SQLite DB."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if INITIAL_MIGRATION.exists():
        conn.executescript(INITIAL_MIGRATION.read_text())
    conn.executescript(MIGRATION_PATH.read_text())
    conn.commit()
    conn.close()
    return str(db_path)


def _minimal_als_xml(
    bpm: float = 120.0,
    time_sig_num: int = 4,
    time_sig_den: int = 4,
    track_names: list[str] | None = None,
    plugins: list[str] | None = None,
    root_note: int = 0,
    scale_name: str = "Major",
) -> bytes:
    """Build a tiny .als-style XML doc that exercises the parser paths."""
    track_names = track_names or ["Drums", "Bass"]
    plugins = plugins or ["Serum"]

    tracks_xml = "".join(
        f"""
        <AudioTrack>
          <Name>
            <UserName Value="{name}" />
            <EffectiveName Value="{name}" />
          </Name>
        </AudioTrack>
        """
        for name in track_names
    )
    plugins_xml = "".join(
        f"""
        <VstPluginInfo>
          <PlugName Value="{p}" />
        </VstPluginInfo>
        """
        for p in plugins
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton Creator="Ableton Live 11.3.0" MinorVersion="11.0_433">
  <LiveSet>
    <MasterTrack>
      <DeviceChain>
        <Mixer>
          <Tempo>
            <Manual Value="{bpm}" />
            <AutomationTarget Id="1" />
          </Tempo>
          <TimeSignature>
            <TimeSignatureNumerator Value="{time_sig_num}" />
            <TimeSignatureDenominator Value="{time_sig_den}" />
          </TimeSignature>
        </Mixer>
      </DeviceChain>
    </MasterTrack>
    <ScaleInformation>
      <RootNote Value="{root_note}" />
      <Name Value="{scale_name}" />
    </ScaleInformation>
    <Tracks>
      {tracks_xml}
    </Tracks>
    <Plugins>
      {plugins_xml}
    </Plugins>
  </LiveSet>
</Ableton>
""".encode("utf-8")


@pytest.fixture
def make_als(tmp_path: Path):
    """Factory: writes a gzipped fake .als to disk and returns its path."""

    def _make(
        name: str = "MyProject.als",
        bpm: float = 120.0,
        time_sig_num: int = 4,
        time_sig_den: int = 4,
        track_names: list[str] | None = None,
        plugins: list[str] | None = None,
        root_note: int = 0,
        scale_name: str = "Major",
    ) -> Path:
        xml = _minimal_als_xml(
            bpm=bpm,
            time_sig_num=time_sig_num,
            time_sig_den=time_sig_den,
            track_names=track_names,
            plugins=plugins,
            root_note=root_note,
            scale_name=scale_name,
        )
        path = tmp_path / name
        with gzip.open(path, "wb") as fh:
            fh.write(xml)
        return path

    return _make
