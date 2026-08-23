"""Run Demucs with a cross-platform SoundFile WAV writer.

TorchAudio 2.11 routes saving through TorchCodec, which requires a shared-library
FFmpeg installation on Windows. This preserves lossless WAV stems without those DLLs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import soundfile
from demucs import audio as demucs_audio
from demucs.separate import main as demucs_main


def _save_wav(
    uri: str | Path,
    source: Any,
    sample_rate: int,
    *,
    encoding: str | None = None,
    bits_per_sample: int | None = None,
    **_unused: Any,
) -> None:
    samples = source.detach().cpu().numpy().T
    if encoding == "PCM_F":
        subtype = "FLOAT"
    elif bits_per_sample == 24:
        subtype = "PCM_24"
    elif bits_per_sample == 32:
        subtype = "PCM_32"
    else:
        subtype = "PCM_16"
    soundfile.write(str(uri), samples, sample_rate, subtype=subtype, format="WAV")


def main() -> None:
    demucs_audio.ta.save = _save_wav
    demucs_main()


if __name__ == "__main__":
    main()
