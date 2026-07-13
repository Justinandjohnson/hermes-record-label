# Stem Separation & Lyrics Extraction

The AI Record Label uses [Demucs](https://github.com/facebookresearch/demucs) (Meta's open-source stem separator) to split every track into four isolated audio stems. These stems feed deeper agent analysis and act as a training data multiplier for any music models.

---

## What It Does

Every track can be separated into:

| Stem | File | Contents |
|------|------|----------|
| `vocals` | `vocals.wav` | Isolated singing / rapping (no instruments) |
| `drums` | `drums.wav` | Isolated drum kit / percussion |
| `bass` | `bass.wav` | Isolated bass line (bass guitar, 808, etc.) |
| `other` | `other.wav` | Everything else — synths, guitars, pads, leads |

From the isolated `vocals` stem, Gemini also extracts:
- **Full lyrics** — word-accurate transcription with section labels (`[Verse 1]`, `[Chorus]`, etc.)
- **Timestamped lines** — each lyric line with `start_time` / `end_time`
- **Vocal style** — e.g. `"melodic trap, autotune-heavy"` or `"breathy indie folk"`
- **Vocal observations** — performance notes (timing, breath control, emotional delivery)

From the isolated `other` stem, Gemini can run an **instrumental analysis**:
- Detailed instrument breakdown with roles and processing notes
- Production techniques identified (sidechain, 808 slides, etc.)
- **Essence elements** — the 1–3 things that define this track's identity (what Rubin protects)

---

## How Agents Use It

### Rick Rubin (Creative Catalyst)
Rubin's entire method is subtractive — he asks "what's the most essential thing, and is anything getting in the way?" Stem separation makes this literal. He calls `get_stem_paths` to access `vocals.wav` and hears the track stripped of all production. His production truth observations reference what the vocal alone is saying vs. what the full arrangement communicates.

### Ravi Kendrick (A&R)
Ravi uses `get_track_lyrics` to reference specific lyric lines in feedback — no more generic "the chorus words aren't landing." Now he can quote the actual line. He also uses the vocal observations to comment on delivery separate from melody.

### Sylvia Rhone (Cultural Authenticator)
Rhone uses lyrics to assess cultural specificity and authenticity. Reading the actual words helps her evaluate whether the language and references are genuine to the artist's background or borrowed aesthetically.

### Training Data Multiplier
If you're training a music understanding model, each track → 4 stems → 4 independent training examples. The model learns to recognize each element in isolation before combining them. Stem combinations (vocals+bass = "the foundation") add even more signal.

---

## MCP Tools

### `separate_stems`
```
separate_stems(
    track_id: int,
    extract_lyrics: bool = true,       # run Gemini on vocal stem for lyrics
    analyze_instrumental: bool = false, # run Gemini on other stem for arrangement
    force: bool = false                 # re-run even if stems exist
)
```
Returns stem paths, lyrics (if requested), and instrumental analysis (if requested). **Takes 1–5 minutes** depending on track length — called once per track, then cached.

### `get_stem_paths`
```
get_stem_paths(track_id: int)
```
Returns paths to all 4 stem WAV files. Returns `null` if `separate_stems` hasn't been called yet.

### `get_track_lyrics`
```
get_track_lyrics(track_id: int)
```
Returns the full lyrics transcription and vocal analysis. Returns `null` if lyrics haven't been extracted yet.

---

## Storage

Stems are stored under `DATA_DIR/stems/`:

```
~/Library/Application Support/ai-record-label/stems/
  htdemucs/
    song-title/
      vocals.wav
      drums.wav
      bass.wav
      other.wav
```

Paths are also persisted in the SQLite DB (`track_stems` table) so agents can look them up by `track_id` without knowing the filesystem layout.

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `track_stems` | File paths for each separated stem |
| `track_lyrics` | Lyrics transcription + vocal analysis from Gemini |
| `stem_instrumental_analyses` | Arrangement + essence analysis from Gemini |

---

## Installation

Demucs is included in `pyproject.toml` and installed automatically with the project:

```bash
uv sync
# or
pip install demucs
```

First run downloads the `htdemucs` model weights (~80 MB) from PyTorch Hub — requires internet. Subsequent runs are offline.

**Apple Silicon acceleration**: Demucs automatically uses MPS (Metal Performance Shaders) on M-series Macs. A 4-minute track takes roughly 2–3 minutes on M2/M3.

---

## Example Agent Workflow

```
1. File dropped in inbox
2. Intake Agent: registers track, starts Gemini full-mix analysis
3. Kallman: fires gut-check SMS ("the hook is strong enough that I found
            myself anticipating it before it arrived")
4. Ravi: listens to full mix, gives A&R feedback
5. Artist revises, Ravi approves
6. [separate_stems fires automatically — 2-3 min]
7. Janick: fires vision assessment SMS
8. Rhone: fires cultural authenticity SMS (reads lyrics for specificity)
9. Rubin: fires next morning ("what's the most important moment in this song,
           and is everything else serving it?")
10. Artist proceeds to artwork stage
```

---

## Mumble Analysis (Early-Stage Vocal Decoding)

When an artist hums or mumbles a melody before the words exist, the mumble analyzer extracts structured phonemic and melodic data, then uses Gemini to suggest words that fit.

### Pipeline

The pipeline is fully programmatic — Gemini never hears raw mumble audio (it hallucinates transcription). Instead:

```
mumble vocal WAV
  → Allosaurus (CMU, universal neural phoneme recognizer)
    → IPA phoneme sequence with timestamps (can only output phoneme symbols, never words)
  → librosa
    → F0 (pyin), BPM, key, phrase segmentation, syllable count estimates
  → pyloudnorm + scipy
    → loudness normalization (-23 LUFS), 80 Hz high-pass filter
  → structured JSON (IPA phonemes + melodic data per phrase)
    → Gemini 3.5 Flash (creative synthesis only — suggests words matching the IPA patterns)
```

### What It Returns

| Field | Description |
|-------|-------------|
| `is_mumble` | Auto-detected: true if sparse phonemes, high vowel ratio |
| `mumble_confidence` | 0.0–1.0 |
| `segments[]` | Per-phrase: timestamp, IPA phonemes, syllable count, word suggestions |
| `hook_candidates` | Complete hook phrases matching the phoneme patterns and key |
| `potential_themes` | Themes from the melody's emotional shape + phoneme character |
| `vowel_palette` | Dominant vowels the artist gravitates toward (IPA → English) |

### MCP Tools

```
analyze_mumble(track_id, use_full_mix=false)
```
Runs the full pipeline. Uses vocal stem if available, otherwise full mix. Stores results in `track_mumble_analyses` table and flags `track_lyrics.is_mumble`.

```
get_mumble_analysis(track_id)
```
Returns stored analysis results.

### Agent Behavior

When a mumble track is detected, **Ravi switches to collaborative/generative mode** — suggesting words based on the phoneme analysis instead of critiquing lyrics. Rubin hears the raw melodic intention before any words exist, which aligns with his process of finding the essential truth first.

---

## Rubin's Essence Test (Automated)

When `analyze_instrumental=true` is passed, the instrumental analysis returns an `essence_elements` field — the 1–3 elements that define the track's core identity. Rubin's agent uses this to ground his question:

> "I keep thinking about [essence element]. That's what this track IS. The question is whether everything around it is serving it or competing with it."

This makes his philosophical framing data-driven without losing the meditative quality.
