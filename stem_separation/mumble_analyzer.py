"""Phonetic mumble decoding for early-stage songwriting.

Pipeline (Gemini never does transcription — only creative synthesis):
  1. Load + resample to 16 kHz mono via librosa
  2. Loudness normalization (pyloudnorm → -23 LUFS)
  3. High-pass filter at 80 Hz (Butterworth)
  4. Allosaurus — universal IPA phoneme recognition with timestamps
  5. librosa — F0 (pyin), BPM, key, phrase segmentation
  6. Align IPA phonemes to melody phrases
  7. Build structured JSON: IPA phonemes + melodic data per phrase
  8. Gemini 3.5 Flash — creative synthesis over structured data

Allosaurus outputs IPA phoneme symbols, never words — hallucination
is structurally impossible. Gemini receives the IPA sequences and uses
its phonological knowledge to suggest real words that match the sounds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from pydantic import BaseModel, Field
from scipy.signal import butter, sosfilt

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "mumble_analysis.txt"

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Allosaurus emit parameter: higher = more phonemes detected (lower confidence)
_ALLO_EMIT = 1.4


# ---------------------------------------------------------------------------
# Pydantic models (match DB schema in 012_mumble.sql)
# ---------------------------------------------------------------------------

class PhoneticSegment(BaseModel):
    """A timed phrase from the mumble vocal."""

    timestamp_start: str
    timestamp_end: str
    sounds_like: str = Field(description="Phonetic approximation — what the melody sounds like it wants to say")
    syllable_count: int
    stress_pattern: str = Field(description="e.g. 'DUM-da-da DUM' — capitals = stressed")
    word_suggestions: list[str] = Field(description="Real words/phrases that fit this slot")


class MumbleAnalysis(BaseModel):
    """Full output of the mumble decoder."""

    is_mumble: bool = Field(
        description="True if this is clearly a hum/mumble rather than real lyrics"
    )
    mumble_confidence: float = Field(
        description="0.0–1.0 confidence that this is a mumble track", ge=0.0, le=1.0
    )
    rhythm_description: str = Field(
        description="Time signature and rhythmic feel"
    )
    global_stress_pattern: str = Field(
        description="The dominant syllable stress pattern across the song"
    )
    segments: list[PhoneticSegment] = Field(
        description="Timestamped phonetic phrases with word suggestions"
    )
    potential_themes: list[str] = Field(
        description="Themes inferred from the melody's emotional shape"
    )
    hook_candidates: list[str] = Field(
        description="Complete hook phrases that fit the melodic pattern"
    )
    melodic_notes: str = Field(
        description="Observations about the melody's shape, emotional arc, and structure"
    )
    vowel_palette: list[str] = Field(
        description="Dominant vowel sounds — useful for choosing words with matching mouth feel"
    )


# ---------------------------------------------------------------------------
# Audio preprocessing
# ---------------------------------------------------------------------------

def _load_preprocessed(path: Path, sr_out: int = 16000) -> tuple[np.ndarray, int]:
    """Load audio, resample to mono 16 kHz, normalize loudness, high-pass filter."""
    raw = librosa.load(str(path), sr=sr_out, mono=True)
    y: np.ndarray = raw[0]
    sr: int = int(raw[1])

    try:
        import pyloudnorm as pyln  # type: ignore[import-untyped]
        meter = pyln.Meter(sr)
        y64 = y.astype(np.float64)
        loudness = meter.integrated_loudness(y64)
        if math.isfinite(loudness):
            y = pyln.normalize.loudness(y64, loudness, -23.0).astype(np.float32)
    except Exception:
        pass

    sos = butter(4, 80 / (sr / 2), btype="high", output="sos")
    y = sosfilt(sos, y).astype(np.float32)
    return y, sr


# ---------------------------------------------------------------------------
# Allosaurus phoneme extraction
# ---------------------------------------------------------------------------

def _extract_phonemes(audio_path: Path, emit: float = _ALLO_EMIT) -> list[dict[str, Any]]:
    """Run Allosaurus with timestamps → list of {time, duration, phoneme}."""
    from allosaurus.app import read_recognizer  # type: ignore[import-untyped]

    model = read_recognizer()
    raw = model.recognize(str(audio_path), timestamp=True, emit=emit)
    if not raw or not raw.strip():
        return []

    phonemes: list[dict[str, Any]] = []
    for line in raw.strip().split("\n"):
        parts = line.strip().split()
        if len(parts) >= 3:
            phonemes.append({
                "time": float(parts[0]),
                "duration": float(parts[1]),
                "phoneme": parts[2],
            })
    return phonemes


def _classify_phoneme(p: str) -> str:
    """Rough IPA classification: vowel, consonant, or glide."""
    vowels = set("aeɛiɪoɔuʊəɐæɑʌɒyøœɤɯʏɨʉɘɵɜɞ")
    vowel_combos = {"aɪ", "aʊ", "eɪ", "oʊ", "ɔɪ", "uə", "ɪə", "eə"}
    if p in vowel_combos or (len(p) == 1 and p in vowels):
        return "vowel"
    if any(c in vowels for c in p) and len(p) <= 2:
        return "vowel"
    return "consonant"


# ---------------------------------------------------------------------------
# Melodic feature extraction
# ---------------------------------------------------------------------------

def _hz_to_note(hz: float) -> str:
    if hz <= 0 or not math.isfinite(hz):
        return ""
    midi = float(librosa.hz_to_midi(hz))
    octave = int(midi // 12) - 1
    note = _NOTE_NAMES[int(midi) % 12]
    return f"{note}{octave}"


def _estimate_key(y: np.ndarray, sr: int) -> str:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mean_chroma = np.mean(chroma, axis=1)
    key_idx = int(np.argmax(mean_chroma))
    major_profile = np.roll(
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        key_idx,
    )
    minor_profile = np.roll(
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
        key_idx,
    )
    major_corr = float(np.corrcoef(mean_chroma, major_profile)[0, 1])
    minor_corr = float(np.corrcoef(mean_chroma, minor_profile)[0, 1])
    mode = "major" if major_corr >= minor_corr else "minor"
    return f"{_NOTE_NAMES[key_idx]} {mode}"


def _extract_melody_features(y: np.ndarray, sr: int) -> dict[str, Any]:
    """BPM, key, voiced F0 stats from the full track."""
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    key = _estimate_key(y, sr)

    f0, voiced_flag, _ = librosa.pyin(
        y,
        fmin=float(librosa.note_to_hz("C2")),
        fmax=float(librosa.note_to_hz("C6")),
        sr=sr,
    )
    voiced_f0 = f0[voiced_flag] if f0 is not None else np.array([])
    voiced_fraction = float(np.mean(voiced_flag)) if voiced_flag is not None else 0.0

    dominant_notes: list[str] = []
    melodic_range = 0
    if voiced_f0.size > 4:
        midi_vals = librosa.hz_to_midi(voiced_f0[voiced_f0 > 0])
        midi_rounded = np.round(midi_vals).astype(int)
        unique, counts = np.unique(midi_rounded, return_counts=True)
        top_idx = np.argsort(counts)[-5:][::-1]
        dominant_notes = [_hz_to_note(float(librosa.midi_to_hz(float(unique[i])))) for i in top_idx]
        melodic_range = int(midi_rounded.max() - midi_rounded.min())

    return {
        "tempo_bpm": round(bpm, 1),
        "key": key,
        "voiced_fraction": round(voiced_fraction, 2),
        "dominant_notes": [n for n in dominant_notes if n],
        "melodic_range_semitones": melodic_range,
    }


# ---------------------------------------------------------------------------
# Phrase segmentation + phoneme alignment
# ---------------------------------------------------------------------------

def _segment_phrases(y: np.ndarray, sr: int) -> list[dict[str, Any]]:
    """Split into non-silent intervals."""
    intervals = librosa.effects.split(y, top_db=22, frame_length=2048, hop_length=512)
    phrases = []
    for start_sample, end_sample in intervals:
        start_sec = float(start_sample / sr)
        end_sec = float(end_sample / sr)
        if end_sec - start_sec < 0.4:
            continue
        phrases.append({
            "start": round(start_sec, 2),
            "end": round(end_sec, 2),
            "audio": y[start_sample:end_sample],
        })
    return phrases


def _analyze_phrase(
    phrase: dict[str, Any],
    sr: int,
    all_phonemes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Per-phrase: F0, syllable count, aligned IPA phonemes."""
    y_seg = phrase["audio"]
    start, end = phrase["start"], phrase["end"]

    # F0 for this phrase
    try:
        f0_seg, voiced_seg, _ = librosa.pyin(
            y_seg,
            fmin=float(librosa.note_to_hz("C2")),
            fmax=float(librosa.note_to_hz("C6")),
            sr=sr,
        )
        voiced_f0 = f0_seg[voiced_seg] if f0_seg is not None else np.array([])
        dominant_note = _hz_to_note(float(np.median(voiced_f0))) if voiced_f0.size > 0 else ""
        if voiced_f0.size > 1:
            note_range = f"{_hz_to_note(float(np.min(voiced_f0)))}-{_hz_to_note(float(np.max(voiced_f0)))}"
        else:
            note_range = dominant_note
    except Exception:
        dominant_note, note_range = "", ""

    # Syllable count from onset peaks
    onset_env = librosa.onset.onset_strength(y=y_seg, sr=sr)
    peaks = librosa.util.peak_pick(
        onset_env, pre_max=3, post_max=3, pre_avg=3, post_avg=5, delta=0.5, wait=10
    )
    syllable_count = max(1, len(peaks))

    # Align Allosaurus phonemes to this phrase's time range
    phrase_phonemes = [
        p for p in all_phonemes
        if p["time"] >= start - 0.05 and p["time"] <= end + 0.05
    ]
    ipa_sequence = " ".join(p["phoneme"] for p in phrase_phonemes)

    # Extract dominant vowel sounds from this phrase's phonemes
    vowels_in_phrase = [p["phoneme"] for p in phrase_phonemes if _classify_phoneme(p["phoneme"]) == "vowel"]

    return {
        "start_sec": start,
        "end_sec": end,
        "duration_sec": round(end - start, 2),
        "syllable_count_estimate": syllable_count,
        "dominant_note": dominant_note,
        "note_range": note_range,
        "ipa_phonemes": ipa_sequence,
        "vowels": vowels_in_phrase,
    }


# ---------------------------------------------------------------------------
# Mumble auto-detection
# ---------------------------------------------------------------------------

def _detect_mumble(
    phonemes: list[dict[str, Any]],
    total_duration: float,
    voiced_fraction: float,
) -> tuple[bool, float]:
    """Heuristic: is this a mumble track or real lyrics?

    Signals:
    - Low phoneme density (few distinct phonemes per second of voiced audio)
    - High vowel ratio (mumbles are mostly vowels/hums)
    - Repetitive phoneme inventory (few unique phonemes = mumbling)
    """
    if not phonemes or total_duration < 1:
        return True, 0.9

    voiced_seconds = max(total_duration * voiced_fraction, 1.0)
    phonemes_per_sec = len(phonemes) / voiced_seconds
    unique_phonemes = len(set(p["phoneme"] for p in phonemes))
    vowel_ratio = sum(1 for p in phonemes if _classify_phoneme(p["phoneme"]) == "vowel") / max(len(phonemes), 1)

    score = 0.0
    # Sparse phonemes → likely mumbling (real speech: 10-15 phonemes/sec)
    if phonemes_per_sec < 4:
        score += 0.35
    elif phonemes_per_sec < 8:
        score += 0.15

    # High vowel ratio → mumbling (real speech: ~40-50% vowels)
    if vowel_ratio > 0.7:
        score += 0.3
    elif vowel_ratio > 0.55:
        score += 0.15

    # Low variety → mumbling
    if unique_phonemes < 8:
        score += 0.25
    elif unique_phonemes < 15:
        score += 0.1

    confidence = min(score, 1.0)
    is_mumble = confidence > 0.4
    return is_mumble, round(confidence, 2)


# ---------------------------------------------------------------------------
# Structured JSON builder for Gemini
# ---------------------------------------------------------------------------

def _build_melodic_json(
    features: dict[str, Any],
    phrase_analyses: list[dict[str, Any]],
    all_phonemes: list[dict[str, Any]],
    total_duration: float,
    is_mumble: bool,
    mumble_confidence: float,
) -> dict[str, Any]:
    """Assemble the structured data Gemini receives."""
    # Global vowel palette from all phonemes
    all_vowels = [p["phoneme"] for p in all_phonemes if _classify_phoneme(p["phoneme"]) == "vowel"]
    vowel_counts: dict[str, int] = {}
    for v in all_vowels:
        vowel_counts[v] = vowel_counts.get(v, 0) + 1
    top_vowels = sorted(vowel_counts, key=vowel_counts.get, reverse=True)[:5]  # type: ignore[arg-type]

    # Full IPA sequence
    full_ipa = " ".join(p["phoneme"] for p in all_phonemes)

    return {
        "is_mumble": is_mumble,
        "mumble_confidence": mumble_confidence,
        "total_duration_seconds": round(total_duration, 1),
        "tempo_bpm": features["tempo_bpm"],
        "key": features["key"],
        "voiced_fraction": features["voiced_fraction"],
        "dominant_notes": features["dominant_notes"],
        "melodic_range_semitones": features["melodic_range_semitones"],
        "full_ipa_sequence": full_ipa,
        "dominant_vowel_phonemes": top_vowels,
        "phrases": [
            {k: v for k, v in pa.items() if k != "vowels"}
            for pa in phrase_analyses
        ],
    }


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

async def analyze_mumble(
    vocal_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = "google/gemini-3.5-flash",
) -> MumbleAnalysis:
    """Decode a mumble/hum vocal into lyric suggestions and themes.

    Pipeline:
      Allosaurus (IPA phonemes) + librosa (melody) → structured JSON → Gemini

    Gemini receives IPA phoneme sequences and melodic data, never raw audio.
    It uses IPA knowledge to suggest words matching the phoneme patterns.

    Args:
        vocal_path: Path to the vocals.wav stem (or full mix).
        api_key:    OpenRouter API key (falls back to OPENROUTER_API_KEY env var).
        model:      OpenRouter model slug for creative synthesis.

    Returns:
        MumbleAnalysis with phonetic segments, word suggestions, hooks, themes.
    """
    from audio_analysis.gemini_client import openrouter_multimodal

    path = Path(vocal_path)
    if not path.exists():
        raise FileNotFoundError(f"Vocal file not found: {path}")

    prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    loop = asyncio.get_event_loop()
    melodic_json = await loop.run_in_executor(None, _run_full_analysis, path)

    prompt = prompt_template.replace("{{MELODIC_JSON}}", json.dumps(melodic_json, indent=2))

    raw = await openrouter_multimodal(
        prompt,
        model=model,
        api_key=api_key,
        temperature=0.75,
        max_tokens=4096,
    )

    try:
        data = json.loads(raw)
        return MumbleAnalysis(**data)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to parse Gemini mumble response: {exc}\nRaw:\n{raw[:500]}"
        ) from exc


def _run_full_analysis(path: Path) -> dict[str, Any]:
    """Synchronous pipeline runner (called via run_in_executor)."""
    logger.info("Loading + preprocessing: %s", path.name)
    y, sr = _load_preprocessed(path)
    total_duration = float(len(y) / sr)

    logger.info("Extracting IPA phonemes via Allosaurus…")
    phonemes = _extract_phonemes(path)

    logger.info("Extracting melody features…")
    features = _extract_melody_features(y, sr)

    logger.info("Detecting mumble vs real lyrics…")
    is_mumble, mumble_confidence = _detect_mumble(
        phonemes, total_duration, features["voiced_fraction"]
    )
    logger.info(
        "Mumble detection: is_mumble=%s, confidence=%.2f (%d phonemes in %.1fs)",
        is_mumble, mumble_confidence, len(phonemes), total_duration,
    )

    logger.info("Segmenting phrases…")
    phrases = _segment_phrases(y, sr)
    if len(phrases) > 12:
        step = len(phrases) / 12
        phrases = [phrases[round(i * step)] for i in range(12)]

    phrase_analyses = [_analyze_phrase(p, sr, phonemes) for p in phrases]

    return _build_melodic_json(
        features, phrase_analyses, phonemes,
        total_duration, is_mumble, mumble_confidence,
    )
