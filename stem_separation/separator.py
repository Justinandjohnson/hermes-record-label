"""Demucs-based stem separation.

Wraps the demucs CLI (python -m demucs) to separate a track into
vocals / drums / bass / other stems. Stores outputs under:
    {stems_dir}/htdemucs/{track_filename_stem}/
        vocals.wav
        drums.wav
        bass.wav
        other.wav
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DEMUCS_MODEL = "htdemucs"
STEM_NAMES = ("vocals", "drums", "bass", "other")


class StemSeparatorError(Exception):
    """Raised when stem separation fails."""


def stem_output_dir(file_path: str | Path, stems_base: Path) -> Path:
    """Return the directory Demucs will write stems into for a given source file."""
    return stems_base / DEMUCS_MODEL / Path(file_path).stem


def stems_already_exist(file_path: str | Path, stems_base: Path) -> bool:
    """Return True if all 4 stems are already on disk (skip re-separation)."""
    out_dir = stem_output_dir(file_path, stems_base)
    return all((out_dir / f"{s}.wav").exists() for s in STEM_NAMES)


async def separate_stems(
    file_path: str | Path,
    stems_base: Path,
    *,
    model: str = DEMUCS_MODEL,
    force: bool = False,
) -> dict[str, str]:
    """Separate a track into stems using Demucs.

    Args:
        file_path:  Absolute path to the source audio file.
        stems_base: Root directory for all stems (DATA_DIR/stems).
        model:      Demucs model name (default: htdemucs).
        force:      Re-run even if stems already exist on disk.

    Returns:
        Dict mapping stem name → absolute path string, e.g.:
            {"vocals": "/…/htdemucs/song/vocals.wav", "drums": "…", …}

    Raises:
        StemSeparatorError: If Demucs is not installed or separation fails.
        FileNotFoundError: If the source file does not exist.
    """
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"Audio file not found: {src}")

    out_dir = stem_output_dir(src, stems_base)

    if not force and stems_already_exist(src, stems_base):
        logger.info("Stems already exist for %s — skipping Demucs", src.name)
        return _collect_stem_paths(out_dir)

    stems_base.mkdir(parents=True, exist_ok=True)

    logger.info("Running Demucs (%s) on %s → %s", model, src.name, stems_base)

    cmd = [
        sys.executable, "-m", "demucs",
        "--name", model,
        "--out", str(stems_base),
        str(src),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        raise StemSeparatorError(
            "Demucs is not installed. Run: uv add demucs  (or: pip install demucs)"
        )

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        raise StemSeparatorError(
            f"Demucs returned exit code {proc.returncode}. stderr:\n{err_text}"
        )

    if not out_dir.exists():
        raise StemSeparatorError(
            f"Demucs completed but output dir missing: {out_dir}\n"
            f"stdout: {stdout.decode(errors='replace')}"
        )

    paths = _collect_stem_paths(out_dir)
    logger.info("Stems ready: %s", list(paths.keys()))
    return paths


def _collect_stem_paths(out_dir: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for stem in STEM_NAMES:
        p = out_dir / f"{stem}.wav"
        if p.exists():
            paths[stem] = str(p)
    return paths
