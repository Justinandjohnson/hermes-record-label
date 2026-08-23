"""Deep-audio embedding extraction using PANNs (CNN14).

Produces a 2048-dimensional float32 embedding vector per track, stored in the
`track_audio_embeddings` table (migration 015). Idempotent: re-running replaces
the existing row.

PANNs (Pretrained Audio Neural Networks):
  Kong et al., 2020. "PANNs: Large-Scale Pretrained Audio Neural Networks for
  Audio Pattern Recognition." IEEE/ACM TASLP 28, 2880-2894.

The CNN14 model weights (~335 MB) are downloaded on first use to
~/panns_data/. Subsequent runs load them from disk.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from pathlib import Path

import httpx
import numpy as np

logger = logging.getLogger(__name__)

_MODEL_NAME = "CNN14"
_LABELS_URL = (
    "https://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv"
)
_CHECKPOINT_URL = "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"


class EmbeddingExtractionError(Exception):
    """Raised when embedding extraction fails for a recoverable reason."""


def _download_asset(url: str, destination: Path, minimum_bytes: int) -> None:
    """Download a required PANNs asset without the package's Unix-only wget call."""
    if destination.is_file() and destination.stat().st_size >= minimum_bytes:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
        if partial.stat().st_size < minimum_bytes:
            raise EmbeddingExtractionError(
                f"Downloaded PANNs asset is incomplete: {partial.stat().st_size} bytes"
            )
        partial.replace(destination)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, EmbeddingExtractionError):
            raise
        raise EmbeddingExtractionError(
            f"Could not download required PANNs asset {destination.name}: {exc}"
        ) from exc


def _ensure_panns_assets() -> Path:
    data_dir = Path.home() / "panns_data"
    _download_asset(_LABELS_URL, data_dir / "class_labels_indices.csv", 10_000)
    checkpoint = data_dir / "Cnn14_mAP=0.431.pth"
    _download_asset(_CHECKPOINT_URL, checkpoint, 300_000_000)
    return checkpoint


def extract_embedding(
    file_path: str,
    db_path: str,
    track_id: int,
) -> np.ndarray:
    """Extract and store a CNN14 embedding for a track.

    Downloads model weights on first use (~335 MB).

    Returns the 2048-dim float32 embedding array.
    Raises EmbeddingExtractionError on failure (nothing written to DB).
    """
    path = Path(file_path)
    if not path.exists():
        raise EmbeddingExtractionError(f"Audio file not found: {file_path}")

    logger.info("Extracting CNN14 embedding for track %d (%s)", track_id, path.name)

    checkpoint = _ensure_panns_assets()
    try:
        from panns_inference import AudioTagging
    except (ImportError, OSError) as exc:
        raise EmbeddingExtractionError(f"panns_inference not available: {exc}") from exc

    try:
        import torch
    except ImportError as exc:
        raise EmbeddingExtractionError(f"torch not available: {exc}") from exc

    try:
        import librosa
    except ImportError as exc:
        raise EmbeddingExtractionError(f"librosa not available: {exc}") from exc

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        at = AudioTagging(checkpoint_path=str(checkpoint), device=device)

        # panns_inference expects (batch, samples) at 32 kHz
        y, _ = librosa.load(str(path), sr=32000, mono=True)
        query = y[np.newaxis, :]  # (1, T)

        _, embedding_np = at.inference(query)
        embedding = embedding_np[0].astype(np.float32)  # (2048,)

    except Exception as exc:
        raise EmbeddingExtractionError(f"CNN14 inference failed: {exc}") from exc

    # Pack as raw bytes (2048 x 4 bytes = 8192 bytes)
    blob = struct.pack(f"{len(embedding)}f", *embedding)

    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_audio_embeddings
                (track_id, model, embedding)
            VALUES (?, ?, ?)
            """,
            (track_id, _MODEL_NAME, blob),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        # Surface as the documented error type so callers (dispatcher.py)
        # convert this to a visible pipeline_error feedback row and the
        # timeout scanner's automatic retry can pick it up.
        raise EmbeddingExtractionError(f"Failed to store embedding: {exc}") from exc
    finally:
        conn.close()

    logger.info(
        "Track %d: CNN14 embedding stored (%d dims, norm=%.4f)",
        track_id,
        len(embedding),
        float(np.linalg.norm(embedding)),
    )
    return embedding


def get_embedding(db_path: str, track_id: int) -> np.ndarray | None:
    """Return the stored CNN14 embedding for a track, or None if absent."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        row = conn.execute(
            """
            SELECT embedding FROM track_audio_embeddings
             WHERE track_id = ? AND model = ?
            """,
            (track_id, _MODEL_NAME),
        ).fetchone()
        if row is None:
            return None
        blob: bytes = row[0]
        n = len(blob) // 4
        return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)
    finally:
        conn.close()


def get_all_embeddings(db_path: str) -> list[dict]:
    """Return all stored embeddings as {track_id, embedding} dicts."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT e.track_id, e.model, e.embedding, e.extracted_at, t.title
              FROM track_audio_embeddings e
              JOIN tracks t ON t.id = e.track_id
             WHERE e.model = ?
             ORDER BY e.track_id ASC
            """,
            (_MODEL_NAME,),
        ).fetchall()
        result = []
        for r in rows:
            blob: bytes = r["embedding"]
            n = len(blob) // 4
            embedding = np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)
            result.append(
                {
                    "track_id": r["track_id"],
                    "model": r["model"],
                    "embedding": embedding,
                    "extracted_at": r["extracted_at"],
                    "title": r["title"],
                }
            )
        return result
    finally:
        conn.close()
