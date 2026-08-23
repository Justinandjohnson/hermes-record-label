"""Create platform-ready music videos from a real track and approved artwork."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class MediaPreparationError(RuntimeError):
    """Raised when ffmpeg/ffprobe cannot create or verify a deliverable."""


def _binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaPreparationError(f"{name} is required and was not found on PATH")
    return path


def probe_video(path: str | Path) -> dict[str, Any]:
    video = Path(path).expanduser().resolve()
    if not video.is_file():
        raise MediaPreparationError(f"Video not found: {video}")
    result = subprocess.run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaPreparationError(result.stderr.strip() or "ffprobe failed")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise MediaPreparationError("Deliverable has no video stream")
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise MediaPreparationError("Deliverable has no audio stream")
    return data


def render_music_video(
    audio_path: str | Path,
    artwork_path: str | Path,
    output_path: str | Path,
    *,
    aspect_ratio: str,
) -> dict[str, Any]:
    """Render a valid H.264/AAC MP4; never reports success without probing it."""
    dimensions = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
    if aspect_ratio not in dimensions:
        raise ValueError("aspect_ratio must be '16:9' or '9:16'")
    audio = Path(audio_path).expanduser().resolve()
    artwork = Path(artwork_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not audio.is_file() or not artwork.is_file():
        raise MediaPreparationError("Both audio and artwork must be real files")
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = dimensions[aspect_ratio]
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    )
    result = subprocess.run(
        [
            _binary("ffmpeg"),
            "-y",
            "-loop",
            "1",
            "-i",
            str(artwork),
            "-i",
            str(audio),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaPreparationError(result.stderr[-2000:] or "ffmpeg failed")
    probe = probe_video(output)
    return {
        "path": str(output),
        "aspect_ratio": aspect_ratio,
        "bytes": output.stat().st_size,
        "probe": probe,
    }
