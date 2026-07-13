# Build Plan — Roundtable Verdict, Wave Vault, Maren Artwork, Granular Segments

Single source of truth for the four features we agreed to build. Phased so each
phase ends in a working state.

## Phase 1 — Foundation (data + skill files)

Pure additions. Nothing existing breaks.

### 1a. Migration `013_verdict_segments_wavevault_artwork.sql`

Four new tables:

**`roundtable_verdicts`** — Dez's structured close-of-meeting decision per track.
- `id`, `track_id` (FK), `recommendation` (`SHIP` | `REVISE` | `VAULT` | `MINE_FOR_LOOPS`)
- `headline` TEXT, `reasoning` TEXT
- `next_action_kind` TEXT, `next_action_payload` TEXT (JSON)
- `created_at`, `superseded_at` (when a later verdict replaces this one)
- UNIQUE active row per track via partial index `WHERE superseded_at IS NULL`

**`track_segments`** — structural segments from a second-pass analysis.
- `id`, `track_id` (FK), `start_sec` REAL, `end_sec` REAL
- `section_label` TEXT (`intro` | `verse` | `chorus` | `drop` | `bridge` | `outro` | freeform)
- `energy` INTEGER (1–10)
- `elements_present` TEXT (JSON array)
- `mood` TEXT, `production_notes` TEXT
- `standout` INTEGER (0/1), `standout_reason` TEXT (nullable)
- `visual_anchor` TEXT — one-line image-language description, the Maren bridge
- `model_used` TEXT, `analyzed_at`
- Index on `(track_id, start_sec)`

**`wave_vault`** — curated loop/stem index. Separate from the song vault.
- `id`, `track_id` (FK), `stem` (`vocals` | `drums` | `bass` | `other` | `full`)
- `start_sec` REAL nullable, `end_sec` REAL nullable (NULL = whole stem)
- `bpm` REAL, `musical_key` TEXT
- `tags` TEXT (JSON array), `notes` TEXT
- `added_by` TEXT (agent name), `added_at`
- Index on `(bpm, musical_key)` for cross-track matching

**`artwork_generations`** — Maren's NanoBanana attempts and what was picked.
- `id`, `track_id` (FK), `brief` TEXT — Maren's visual brief for this round
- `prompt` TEXT, `variant_axis` TEXT (`medium` | `vantage` | `era` | `abstraction`)
- `model` TEXT (e.g. `nano-banana-pro`), `image_url` TEXT
- `rationale` TEXT — Maren's one-sentence "why this variant"
- `picked` INTEGER (0/1), `created_at`
- Index on `(track_id, created_at)`

### 1b. Maren's NanoBanana skill — `agents/creative_director/skills/nano_banana.md`

Base content forked from kousen's gist (CC-friendly content, no scripts). Adapt
slot names and add our custom layer:

- **Base** — 5-component formula (Subject → Action → Location → Composition →
  Style), text rendering rules, anti-patterns, Nano Banana 2 vs Pro differences,
  worked examples
- **Custom translation layer** — how to turn a song into a prompt:
  - Thematic anchoring rule: pick ONE concrete image from `essence_elements`,
    a standout segment's `visual_anchor`, or a specific lyric line. No mood
    words as starting points
  - Evidence requirement: every variant must cite its source (timestamp,
    lyric, or agent observation)
  - Variant axis rule: 3–4 prompts share the anchor, diverge on ONE axis
    (medium / vantage / era / abstraction)
  - One worked end-to-end chain: song data → anchor → 4 variants
- **Domain lens** — Maren picks one per variant: documentary photo, editorial
  illustration, film still, abstract collage, product photo, painting

### 1c. Maren SOUL.md — Artwork generation section

Add a new section instructing Maren to:
1. Read `track_segments`, `track_lyrics`, `stem_instrumental_analyses`, and the
   roundtable observations when state hits `ART_NEEDED`
2. Load `skills/nano_banana.md` and follow the translation rules
3. Compose a visual brief, then 3–4 variant prompts following the axis rule
4. Call the nano-banana endpoint, present results back to roundtable

### 1d. Wave Vault page scaffold + nav entry

Frontend route + page shell so the data model has a visible home. Real
listing/filter/playback is Phase 4 once the schema has rows in it.

---

## Phase 2 — Verdict synthesis (Dez closes the meeting)

### 2a. Verdict shape — `desktop-app/src/lib/verdict.ts`

TypeScript type matching the DB row + helper to map `recommendation` to:
- a primary CTA label
- a primary action (state transition or vault op)

### 2b. Dez verdict prompt — extend `agents/manager/SOUL.md`

New section: "Closing the meeting." Dez reads all agent observations after the
last contributor speaks, produces a JSON object matching the verdict schema. The
roundtable backend stores it as a `roundtable_verdicts` row and emits a
`verdict_ready` feedback intent.

### 2c. Backend — `coordination/verdict_synthesizer.py`

Pure function: given a track_id, collect agent observations, hit Claude via
OpenRouter with the verdict prompt, validate the JSON, write the row.

### 2d. HTTP endpoint — `GET /tracks/:id/verdict` and `POST /tracks/:id/verdict/act`

Read the current verdict + perform the next_action (state transition, vault,
wave-vault stems, etc.) when the user clicks the CTA.

### 2e. Frontend — table center shows verdict + CTA

Replace the current `summary` lookup with `useVerdict(trackId)`. If a verdict
exists, render the headline + reasoning + a primary button driven by
`next_action_kind`.

---

## Phase 3 — Granular segment analysis

### 3a. `audio_analysis/segment_analyzer.py`

Second-pass module that runs after stem separation completes. Gemini call with
prompt instructing structural segmentation (not fixed windows). Returns segments
with all fields from the `track_segments` schema, including `visual_anchor`.

### 3b. Pipeline hook — wire into existing analysis flow

`analyzer.py` or stem post-processing triggers the segmenter. Failures are
logged but don't block intake; segments are an enrichment, not a gate.

### 3c. Frontend — timeline + clickable timestamps

Audio playback page renders a timeline strip showing segments with their
section labels and a marker for `standout=true`. Agent messages that reference
a timestamp become clickable and seek the player.

---

## Phase 4 — Maren NanoBanana pipeline + Wave Vault listing

### 4a. `artwork/nano_banana_client.py`

OpenRouter HTTP client for Nano Banana Pro / 2. Functions:
- `generate(prompt, variant_axis, aspect_ratio="1:1") -> ImageResult`
- Writes a row to `artwork_generations` per variant
- Returns the image URL + the row id

### 4b. Maren orchestration — `agents/creative_director/orchestrator.py` (or
inline in dispatcher)

When state → `ART_NEEDED`:
1. Build the visual brief from track data + roundtable
2. Generate 4 variant prompts following Maren's skill
3. Fire all 4 in parallel
4. Post each to the roundtable as a feedback message from Maren with the
   image and rationale

### 4c. HTTP endpoints
- `POST /artwork/:track_id/generate` — manual trigger
- `POST /artwork/generations/:id/pick` — user picks a variant
- `GET /artwork/:track_id/generations` — list all attempts

### 4d. Wave Vault listing — real implementation

Page from 1d gets data: grid of cards with stem playback, BPM/key filters,
"drag into new project" action, search by tag. Empty state explains the
"vault from roundtable" flow.

### 4e. Roundtable wave-vault hook

When a verdict is `MINE_FOR_LOOPS` or when Rubin/Kallman explicitly call out a
moment, write a `wave_vault` row. Make this a dedicated action in the verdict
schema so it shows up as a CTA on the table center.

---

## What we deliberately are NOT doing

- No mock data or stub responses anywhere. If a feature isn't wired up
  end-to-end, the UI omits it rather than fakes it.
- No backup files. No dead branches.
- Granular analysis runs only on tracks past intake — no retroactive batch unless
  the user asks for it.
- NanoBanana generations are not auto-approved. Maren reviews, user picks.

---

## Execution status

**Phase 1 — Foundation:** complete
- `013_verdict_segments_wavevault_artwork.sql` ✓ applied
- `agents/creative_director/skills/nano_banana.md` ✓
- `agents/creative_director/SOUL.md` extended ✓
- Wave Vault page + route ✓
- `desktop-app/src/lib/verdict.ts` ✓

**Phase 2 — Verdict synthesis:** complete
- `coordination/verdict_synthesizer.py` ✓ (Claude via OpenRouter)
- `agents/manager/SOUL.md` "Closing the Meeting" ✓
- HTTP `GET /verdict`, `POST /verdict/synthesize`, `POST /verdict/act` ✓
- `desktop-app/src/hooks/useVerdict.ts` ✓
- Roundtable canvas renders verdict + CTA when table opens ✓

**Phase 3 — Granular segment analysis:** complete
- `audio_analysis/segment_analyzer.py` ✓ (Gemini second pass)
- Dispatcher hook after stem separation ✓
- HTTP `GET /segments` ✓
- `desktop-app/src/components/SegmentTimeline.tsx` ✓
- Wired into RoundtableReview header (shows when segments exist) ✓

**Phase 4 — Maren NanoBanana + Wave Vault listing:** complete
- `artwork/nano_banana_client.py` ✓ (OpenRouter image-gen client)
- `artwork/maren_orchestrator.py` ✓ (brief → 4 variants in parallel)
- Auto-fire on `ART_NEEDED` (background thread, non-blocking) ✓
- HTTP `GET /artwork/generations`, `GET /artwork/image`,
  `POST /artwork/generate`, `POST /artwork/pick` ✓
- HTTP `GET /wave_vault` ✓
- `desktop-app/src/components/ArtworkVariants.tsx` ✓
- Wave Vault page upgraded with real data + BPM/key/stem filters ✓

## Operational notes for next session

- All four DB tables exist and are indexed.
- `OPENROUTER_API_KEY` is required for verdict synthesis, segment analysis,
  and NanoBanana — all routed through OpenRouter.
- Maren's NanoBanana orchestration runs in a background thread when a track
  transitions into `ART_NEEDED`. First fire ~60s for 4 variants in parallel.
- Verdict synthesis is currently triggered explicitly via the
  `POST /verdict/synthesize` endpoint. Auto-firing when the roundtable
  finishes is a small follow-up (one call inside the dispatcher when the
  last expected agent posts).
- The verdict CTA in the table center handles approve / request_revision /
  vault / wave_vault. For `approve`, it walks `FEEDBACK_GIVEN → APPROVED →
  ART_NEEDED` in one click, which triggers Maren's background generation.
- Pre-existing Pyright diagnostics in `dispatcher.py` and `http_api.py`
  (the `_db_conn` contextmanager pattern, `int(data.get(...))` calls) are
  not introduced by this work — they were in the file before. Runtime
  imports cleanly.
