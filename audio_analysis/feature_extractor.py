"""Track-level audio feature extraction using librosa + madmom.

Extracts and stores two sets of features:
  - Librosa features (migration 014 + 015 columns):
      rhythm: BPM, beat count, time signature, tempo variability, pulse clarity,
              onset density
      tonality: musical key, key confidence, mode, Tonnetz (6-dim JSON)
      spectral: centroid, rolloff, flatness, contrast, ZCR, MFCC (13-dim JSON × 2)
      dynamics: loudness RMS, dynamic range dB
      source separation: HPSS harmonic ratio
  - madmom features (migration 015):
      RNN BPM, beat confidence, downbeat count, swing ratio

Both write to `track_audio_features`. Idempotent: re-running replaces the row.
The PANNs CNN14 embedding is handled separately in embedding_extractor.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import librosa
import numpy as np

logger = logging.getLogger(__name__)


class FeatureExtractionError(Exception):
    """Raised when feature extraction fails for a recoverable reason."""


# ── Krumhansl–Schmucklisky key profiles ──────────────────────────────────────

_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _estimate_key(chroma_mean: np.ndarray) -> tuple[str, float, str]:
    """Estimate musical key from mean chroma vector.

    Returns:
        (key_name, confidence, mode) where mode is "major" | "minor"
        and confidence is the best correlation normalised to [0, 1].
    """
    best_corr = -np.inf
    best_key = "C major"
    best_mode = "major"

    for i in range(12):
        shifted = np.roll(chroma_mean, -i)
        major_corr = float(np.corrcoef(shifted, _MAJOR_PROFILE)[0, 1])
        minor_corr = float(np.corrcoef(shifted, _MINOR_PROFILE)[0, 1])
        if major_corr > best_corr:
            best_corr = major_corr
            best_key = f"{_NOTE_NAMES[i]} major"
            best_mode = "major"
        if minor_corr > best_corr:
            best_corr = minor_corr
            best_key = f"{_NOTE_NAMES[i]} minor"
            best_mode = "minor"

    confidence = (best_corr + 1.0) / 2.0
    return best_key, round(confidence, 4), best_mode


def _estimate_time_signature(beat_frames: np.ndarray, sr: int, hop_length: int) -> str:
    """Estimate time signature from inter-beat intervals.

    Returns "4/4", "3/4", or "6/8". Falls back to "4/4" when ambiguous.
    """
    if len(beat_frames) < 4:
        return "4/4"

    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    ibi = np.diff(beat_times)
    if len(ibi) == 0:
        return "4/4"

    median_ibi = float(np.median(ibi))
    long_beats = np.sum(ibi > median_ibi * 1.35) / len(ibi)

    if long_beats > 0.30:
        return "6/8"

    onset_env = np.zeros(int(beat_times[-1] * sr / hop_length) + 1)
    for bt in beat_frames:
        if bt < len(onset_env):
            onset_env[int(bt)] = 1.0

    ac = librosa.autocorrelate(onset_env, max_size=int(6 * sr / hop_length))
    period_range = slice(
        max(1, int(2 * median_ibi * sr / hop_length)),
        int(6 * median_ibi * sr / hop_length),
    )
    if len(ac[period_range]) > 0:
        rel_peak = int(np.argmax(ac[period_range]))
        period_frames = rel_peak + period_range.start
        period_beats = period_frames / (median_ibi * sr / hop_length)
        if 2.5 <= period_beats <= 3.5:
            return "3/4"

    return "4/4"


def _tempo_variability(beat_frames: np.ndarray, sr: int, hop_length: int) -> float:
    """Coefficient of variation of inter-beat intervals (std / mean).

    0 = perfectly rigid tempo. Higher = more rubato / human feel.
    """
    if len(beat_frames) < 3:
        return 0.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    ibi = np.diff(beat_times)
    mean_ibi = float(np.mean(ibi))
    if mean_ibi == 0:
        return 0.0
    return round(float(np.std(ibi) / mean_ibi), 4)


def _pulse_clarity(y: np.ndarray, sr: int, hop_length: int) -> float:
    """Normalised peak strength of the predominant tempo in the tempogram.

    Returns a value in [0, 1] where 1 = very clear, metronomic pulse.
    """
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tg = librosa.feature.tempogram(onset_envelope=oenv, sr=sr, hop_length=hop_length)
    peak = float(np.max(tg))
    if peak == 0:
        return 0.0
    return round(min(peak / 10.0, 1.0), 4)  # empirical normalisation


def _madmom_features(file_path: str) -> dict:
    """Extract RNN-based beat features using madmom.

    Returns madmom_bpm, madmom_beat_confidence, madmom_downbeat_count,
    madmom_swing_ratio.
    Raises FeatureExtractionError on failure.
    """
    try:
        from madmom.features.beats import RNNBeatProcessor, BeatTrackingProcessor
        from madmom.features.downbeats import RNNDownBeatProcessor, DBNDownBeatTrackingProcessor
    except ImportError as exc:
        raise FeatureExtractionError(f"madmom not available: {exc}") from exc

    try:
        # ── Beat tracking ──────────────────────────────────────────────────
        beat_proc = RNNBeatProcessor()(file_path)
        tracker = BeatTrackingProcessor(fps=100)
        beats = tracker(beat_proc)  # array of beat times in seconds

        if len(beats) < 2:
            return {
                "madmom_bpm": None,
                "madmom_beat_confidence": None,
                "madmom_downbeat_count": None,
                "madmom_swing_ratio": None,
            }

        ibi = np.diff(beats)
        madmom_bpm = round(float(60.0 / np.median(ibi)), 1)
        madmom_beat_confidence = round(float(np.mean(beat_proc)), 4)

        # ── Downbeat tracking ──────────────────────────────────────────────
        db_proc = RNNDownBeatProcessor()(file_path)
        db_tracker = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
        downbeats_arr = db_tracker(db_proc)  # [[time, beat_number], ...]
        # Count rows where beat_number == 1
        madmom_downbeat_count = int(np.sum(downbeats_arr[:, 1] == 1))

        # ── Swing ratio ────────────────────────────────────────────────────
        # Compute ratio of odd-indexed to even-indexed sub-beat IBIs.
        # For straight 8ths the ratio ≈ 1.0; for swung feel it's > 1.0.
        if len(ibi) >= 4:
            odd_ibi = ibi[0::2]
            even_ibi = ibi[1::2]
            min_len = min(len(odd_ibi), len(even_ibi))
            if min_len > 0 and float(np.mean(even_ibi[:min_len])) > 0:
                madmom_swing_ratio = round(
                    float(np.mean(odd_ibi[:min_len])) / float(np.mean(even_ibi[:min_len])),
                    4,
                )
            else:
                madmom_swing_ratio = 1.0
        else:
            madmom_swing_ratio = 1.0

        return {
            "madmom_bpm": madmom_bpm,
            "madmom_beat_confidence": madmom_beat_confidence,
            "madmom_downbeat_count": madmom_downbeat_count,
            "madmom_swing_ratio": madmom_swing_ratio,
        }

    except Exception as exc:
        raise FeatureExtractionError(f"madmom beat analysis failed: {exc}") from exc


# ── Public API ────────────────────────────────────────────────────────────────

def extract_audio_features(
    file_path: str,
    db_path: str,
    track_id: int,
) -> dict:
    """Extract and store track-level audio features.

    Runs librosa analysis and madmom beat analysis, then writes a single row to
    track_audio_features (INSERT OR REPLACE).

    Returns the stored feature dict.
    Raises FeatureExtractionError on failure (nothing written to DB).
    """
    path = Path(file_path)
    if not path.exists():
        raise FeatureExtractionError(f"Audio file not found: {file_path}")

    logger.info("Extracting audio features for track %d (%s)", track_id, path.name)

    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True)
    except Exception as exc:
        raise FeatureExtractionError(f"librosa.load failed: {exc}") from exc

    sr = int(sr)
    hop_length = 512

    # ── Rhythm ────────────────────────────────────────────────────────────────
    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
        bpm = float(round(float(np.atleast_1d(tempo_raw)[0]), 1))
        beat_count = int(len(beat_frames))
    except Exception as exc:
        raise FeatureExtractionError(f"Beat tracking failed: {exc}") from exc

    time_sig = _estimate_time_signature(beat_frames, sr=sr, hop_length=hop_length)
    tempo_var = _tempo_variability(beat_frames, sr=sr, hop_length=hop_length)
    pulse_clr = _pulse_clarity(y=y, sr=sr, hop_length=hop_length)

    # ── Onset density ─────────────────────────────────────────────────────────
    try:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length)
        duration_sec = len(y) / sr
        onset_density = round(float(len(onset_frames)) / max(duration_sec, 1.0), 3)
    except Exception as exc:
        raise FeatureExtractionError(f"Onset detection failed: {exc}") from exc

    # ── Tonality ──────────────────────────────────────────────────────────────
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
        chroma_mean = chroma.mean(axis=1)
        musical_key, key_confidence, mode = _estimate_key(chroma_mean)
    except Exception as exc:
        raise FeatureExtractionError(f"Key estimation failed: {exc}") from exc

    try:
        tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
        tonnetz_mean_arr = tonnetz.mean(axis=1).tolist()
        tonnetz_mean = json.dumps([round(v, 5) for v in tonnetz_mean_arr])
    except Exception as exc:
        raise FeatureExtractionError(f"Tonnetz failed: {exc}") from exc

    # ── MFCCs ─────────────────────────────────────────────────────────────────
    try:
        mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop_length)
        mfcc_mean = json.dumps([round(v, 4) for v in mfccs.mean(axis=1).tolist()])
        mfcc_var = json.dumps([round(v, 4) for v in mfccs.var(axis=1).tolist()])
    except Exception as exc:
        raise FeatureExtractionError(f"MFCC extraction failed: {exc}") from exc

    # ── Spectral texture ──────────────────────────────────────────────────────
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)
        rolloff = librosa.feature.spectral_rolloff(
            y=y, sr=sr, hop_length=hop_length, roll_percent=0.85
        )
        flatness = librosa.feature.spectral_flatness(y=y, hop_length=hop_length)
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop_length)
        zcr = librosa.feature.zero_crossing_rate(y=y, hop_length=hop_length)

        spectral_centroid_mean = round(float(np.mean(centroid)), 2)
        spectral_rolloff_mean = round(float(np.mean(rolloff)), 2)
        spectral_flatness_mean = round(float(np.mean(flatness)), 6)
        spectral_contrast_mean = round(float(np.mean(contrast)), 4)
        zero_crossing_rate_mean = round(float(np.mean(zcr)), 6)
    except Exception as exc:
        raise FeatureExtractionError(f"Spectral analysis failed: {exc}") from exc

    # ── HPSS harmonic ratio ───────────────────────────────────────────────────
    try:
        y_harm, _ = librosa.effects.hpss(y)
        harm_energy = float(np.sum(y_harm ** 2))
        total_energy = float(np.sum(y ** 2))
        hpss_harmonic_ratio = round(
            harm_energy / total_energy if total_energy > 0 else 0.0, 4
        )
    except Exception as exc:
        raise FeatureExtractionError(f"HPSS failed: {exc}") from exc

    # ── Dynamics ──────────────────────────────────────────────────────────────
    try:
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        loudness_rms = round(float(np.mean(rms)), 6)

        frames_per_sec = sr // hop_length
        window_rms = [
            float(rms[i : i + frames_per_sec].mean())
            for i in range(0, len(rms), frames_per_sec)
            if len(rms[i : i + frames_per_sec]) > 0
        ]
        if len(window_rms) >= 2:
            peak_db = float(20 * np.log10(max(window_rms) + 1e-9))
            floor_db = float(20 * np.log10(np.percentile(window_rms, 10) + 1e-9))
            dynamic_range_db = round(peak_db - floor_db, 2)
        else:
            dynamic_range_db = 0.0
    except Exception as exc:
        raise FeatureExtractionError(f"Dynamics analysis failed: {exc}") from exc

    # ── madmom ────────────────────────────────────────────────────────────────
    try:
        madmom_data = _madmom_features(str(path))
    except FeatureExtractionError as exc:
        # madmom failure is non-fatal — log and continue with nulls
        logger.warning("madmom analysis failed for track %d: %s", track_id, exc)
        madmom_data = {
            "madmom_bpm": None,
            "madmom_beat_confidence": None,
            "madmom_downbeat_count": None,
            "madmom_swing_ratio": None,
        }

    # ── Assemble & persist ────────────────────────────────────────────────────
    features: dict = {
        "track_id": track_id,
        # rhythm
        "bpm": bpm,
        "beat_count": beat_count,
        "time_signature": time_sig,
        "tempo_variability": tempo_var,
        "pulse_clarity": pulse_clr,
        "onset_density": onset_density,
        # tonality
        "musical_key": musical_key,
        "key_confidence": key_confidence,
        "mode": mode,
        "tonnetz_mean": tonnetz_mean,
        # mfcc
        "mfcc_mean": mfcc_mean,
        "mfcc_var": mfcc_var,
        # spectral
        "spectral_centroid_mean": spectral_centroid_mean,
        "spectral_rolloff_mean": spectral_rolloff_mean,
        "spectral_flatness_mean": spectral_flatness_mean,
        "spectral_contrast_mean": spectral_contrast_mean,
        "zero_crossing_rate_mean": zero_crossing_rate_mean,
        # source separation
        "hpss_harmonic_ratio": hpss_harmonic_ratio,
        # dynamics
        "loudness_rms": loudness_rms,
        "dynamic_range_db": dynamic_range_db,
        # madmom
        **madmom_data,
    }

    logger.info(
        "Track %d: %.1f BPM (madmom %.1f), %s %s, flatness=%.4f, "
        "onset_density=%.2f/s, harmonic=%.2f, DR=%.1fdB",
        track_id,
        bpm,
        madmom_data["madmom_bpm"] or 0.0,
        musical_key,
        time_sig,
        spectral_flatness_mean,
        onset_density,
        hpss_harmonic_ratio,
        dynamic_range_db,
    )

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_audio_features (
                track_id,
                bpm, beat_count, time_signature, tempo_variability,
                pulse_clarity, onset_density,
                musical_key, key_confidence, mode,
                tonnetz_mean, mfcc_mean, mfcc_var,
                spectral_centroid_mean, spectral_rolloff_mean,
                spectral_flatness_mean, spectral_contrast_mean,
                zero_crossing_rate_mean, hpss_harmonic_ratio,
                loudness_rms, dynamic_range_db,
                madmom_bpm, madmom_beat_confidence,
                madmom_downbeat_count, madmom_swing_ratio
            ) VALUES (
                ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?
            )
            """,
            (
                track_id,
                features["bpm"], features["beat_count"], features["time_signature"],
                features["tempo_variability"],
                features["pulse_clarity"], features["onset_density"],
                features["musical_key"], features["key_confidence"], features["mode"],
                features["tonnetz_mean"], features["mfcc_mean"], features["mfcc_var"],
                features["spectral_centroid_mean"], features["spectral_rolloff_mean"],
                features["spectral_flatness_mean"], features["spectral_contrast_mean"],
                features["zero_crossing_rate_mean"], features["hpss_harmonic_ratio"],
                features["loudness_rms"], features["dynamic_range_db"],
                features["madmom_bpm"], features["madmom_beat_confidence"],
                features["madmom_downbeat_count"], features["madmom_swing_ratio"],
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM track_audio_features WHERE track_id = ?", (track_id,)
        ).fetchone()
        return dict(row)
    except sqlite3.OperationalError as exc:
        # Surface as the documented error type so callers (dispatcher.py)
        # convert this to a visible pipeline_error feedback row and the
        # timeout scanner's automatic retry can pick it up.
        raise FeatureExtractionError(f"Failed to store audio features: {exc}") from exc
    finally:
        conn.close()


def get_audio_features(db_path: str, track_id: int) -> dict | None:
    """Return stored features for a track, or None if not yet extracted."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM track_audio_features WHERE track_id = ?", (track_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_audio_features(db_path: str) -> list[dict]:
    """Return features for all tracks that have been analyzed."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.*, t.title
              FROM track_audio_features f
              JOIN tracks t ON t.id = f.track_id
            ORDER BY f.track_id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
