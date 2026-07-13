# Skill — Writing NanoBanana prompts that come from the song

Maren loads this skill when she needs to generate cover art for a track that has
reached `ART_NEEDED`. The skill teaches two things:

1. **How NanoBanana actually wants to be prompted** — the official prompt shape
2. **How to translate a song into one of those prompts** — our custom layer

The second part is the one that matters. Without it, NanoBanana produces generic
"muted earth tones" album covers indistinguishable from stock art. With it, every
cover Maren ships is anchored in something specific the song or the agents said.

---

## Part 1 — Prompt shape (the canonical formula)

NanoBanana (Gemini 3 Pro Image, codename Nano Banana Pro) and NanoBanana 2
(Gemini 3.1 Flash Image) both respond best to *natural-language narrative*, not
keyword lists. Every prompt fills five slots:

```
[Subject + adjectives] doing [Action] in [Location/Context].
[Composition / Camera Angle]. [Lighting / Atmosphere]. [Style / Medium].
[Specific constraint, including any text in quotes].
```

### Slot rules

**Subject** — concrete, specific. Not "a person" — "a sophisticated woman in her
late forties wearing vintage Chanel-style tweed." Materiality matters: not "a
jacket" but "navy blue tweed with frayed cuffs."

**Action** — describe what the subject is doing, even for still shots. "Standing
slightly turned, gazing past the camera" reads differently than "posing."

**Location / Context** — give the model an environment, not just a backdrop.
"A seamless deep-cherry studio backdrop" is fine. "A rain-soaked alley in Osaka
at 3am" is better when the song calls for it.

**Composition** — use real photographic terms. `medium-full shot`, `low angle`,
`overhead 90°`, `Dutch angle`, `shallow depth of field (f/1.8)`, `wide
establishing shot`, `macro`. Pick one. Don't stack contradictions.

**Lighting / Atmosphere** — also real terms. `Rembrandt lighting`,
`chiaroscuro`, `golden hour backlight with long shadows`, `single hard practical
light from screen left`, `flat overcast diffusion`.

**Style / Medium** — the most powerful slot for differentiating variants. Pick
one and commit:
- `35mm film, Portra 400, pronounced grain, slight halation`
- `editorial illustration, gouache on heavy paper, visible brushstrokes`
- `studio product photo, three-point softbox, brushed-aluminum reflectors`
- `1980s color film, slightly grainy, faded magenta cast`
- `abstract paper collage, torn edges, layered geometry`
- `film still from a 1970s European art-house feature`

**Text** — any text on the cover goes in quotes, with the font described
explicitly: `the word "LONDON" in bold condensed sans-serif, set lower-left,
muted gold against the background`. Bandcamp covers usually don't need text;
when they do, keep it to title + artist.

### Anti-patterns — refuse these

- Keyword lists (`"cinematic, moody, atmospheric, ethereal"`)
- Vague subjects (`"a person"`, `"someone"`, `"a figure"`)
- Mood words used as composition (`"dreamy composition"` is not a composition;
  `"shallow depth of field, soft focus on midground"` is)
- Negative instructions (`"no people"` → use `"empty"`)
- Contradictory specs (`"wide-angle close-up"` does not exist)
- Stacking adjectives without a noun to anchor them
- More than ~5 details in any one slot

### Resolution & format

- Bandcamp covers: square `1:1`, minimum 3000×3000. Request `4K` resolution.
- For NanoBanana 2 (faster, cheaper) include `at 2K resolution` in the prompt.
- For NanoBanana Pro, omit the resolution — Pro defaults to its highest.

---

## Part 2 — Translating a song into a prompt

This is where Maren earns her keep. Generic "muted earth tones" prompts are
banned. Every cover must trace back to something the song or the agents
actually said.

### The thematic anchor rule

Before writing a single word of prompt, Maren picks **one concrete image**.
The anchor comes from exactly one of these sources, in priority order:

1. A standout segment's `visual_anchor` field (preferred — already image-language)
2. A specific phrase from `track_lyrics.lyrics_clean` — a line, a metaphor, a
   recurring image
3. An item from `stem_instrumental_analyses.essence_elements` (Rubin's "what
   this track is really about")
4. A reference one of the agents made by name (Rhone's cultural specificity,
   Janick's vision call-out, Kallman's first-instinct comparison)

If Maren can't name the anchor in one sentence — *"the anchor is the lyric
'still drives the long way home'"* — she doesn't have one yet. Re-read the
materials. The anchor is a noun phrase, not a mood word.

**Banned as anchors:** "muted earth tones", "warm and melancholy", "introspective
vibe", "nighttime feeling", "moody atmosphere", any palette description on its
own, any genre label. These can *appear* in the prompt as consequences of the
anchor, never as starting points.

### The evidence requirement

Every variant prompt Maren produces carries an evidence note Maren can hand to
the user:

> *Variant 2 — anchored to the standout at 1:48 ("the synth opens up like a
> window"). Treated as a still wide shot, single source light, no figure.*

If she can't write the evidence sentence, the prompt is not grounded and gets
rewritten.

### The variant axis rule

When Maren generates 3–4 variants, every variant must **share the same anchor**
and **differ on exactly one axis**. Not four random ideas — four real takes on
the same idea.

The axes she can choose from:

- **Medium** — same scene rendered four ways. 35mm photo / editorial illustration
  / film still / paper collage. Strongest axis for an opinionated cover.
- **Vantage** — same subject from four positions. Close macro / waist-up portrait
  / wide establishing / overhead. Use when the anchor is a *thing* whose
  meaning shifts with framing.
- **Era** — same image styled across four periods. 1970s film stock / 1990s
  digital camcorder still / contemporary editorial photo / future-fiction.
  Use when the song itself plays with time.
- **Abstraction** — same anchor at four removes from literal. Literal
  photograph / staged metaphor / symbolic still life / pure abstraction.
  Use when the song's central image is too on-the-nose if shown directly.

Pick one axis per round. Mixing axes produces incoherence.

### The domain lens

Each variant also commits to **one** domain lens. This is the same idea as the
medium axis but operates per-variant rather than across variants. Pick from:

- `documentary photograph` — present-tense, real-world, no staging
- `editorial illustration` — drawn, opinionated, magazine-feature look
- `film still` — frame from a movie that doesn't exist; implies cinematography
- `studio product photo` — controlled lighting, clean background, object-as-subject
- `painting` — specify medium (oil / gouache / watercolor / acrylic)
- `mixed-media collage` — physical materials, torn paper, found imagery

The lens determines which composition and lighting language you use. A
documentary photograph wants `35mm, f/2.8, available light`. A studio product
photo wants `three-point softbox, seamless backdrop`. Don't cross the streams.

### Maren's voice in the prompt

She is the director, not the camera. Maren writes prompts the way she'd brief a
photographer — confident, specific, opinionated. No hedging, no "maybe", no
"perhaps."

---

## Part 3 — Worked end-to-end example

A real chain so the rules are concrete.

### Input — what Maren receives

```
Track: "Long Way Home" by Sera Voss
Tempo: 84 BPM, key: G minor
Lyrics excerpt: "still drives the long way home / radio dead, just the hum
                / counting streetlights instead of years"
essence_elements: ["the hum under everything", "refusing to arrive",
                   "small motions that mean grief"]
Standout segment (1:42–1:58):
  visual_anchor: "a single tail light receding on a wet two-lane road,
                  the dash glow on her hands"
  standout_reason: "the synth pad opens and the vocal drops to half-volume —
                    the song stops trying"
Rhone said: "this is a Pacific Northwest sound, not a 'driving song' generally —
              the specificity matters, don't strand it in Americana"
```

### Anchor

> *The lyric "counting streetlights instead of years" + the standout at 1:42's
> visual_anchor "a single tail light receding on a wet two-lane road." She is
> in the car, the road is the song, the streetlights are the years.*

One noun phrase. Concrete. From the materials.

### Axis chosen

**Medium.** This song's central image is strong enough to survive being rendered
four different ways; the question for the user is which treatment serves it.

### The four variants

**Variant 1 — 35mm documentary photograph**
```
Documentary photograph of a woman in her late thirties driving alone at night
on a wet two-lane road through Pacific Northwest fir country, both hands on
the wheel, dashboard glow lighting her knuckles in warm amber. Shot through
the windshield from the passenger seat with a single street-lamp halation
streaking the glass. Medium-close shot, shallow depth of field. 35mm Portra
400 stock, pronounced grain, slight halation, no flash. Cold blue rain on the
windshield against the amber dash light.
```
*Rationale:* the literal reading. Sera's first instinct will be that this is
"too obvious," but the photographic specificity (Portra, halation, Pacific
Northwest cues) keeps it from being stock.

**Variant 2 — Editorial illustration**
```
Editorial illustration in gouache on heavy textured paper of a single red
tail light receding into rain-blurred darkness on a two-lane road lined with
silhouetted firs. No driver visible. Vantage from low, looking up the road.
Visible brush texture, limited palette of deep wet-asphalt black, amber tail
light, faded streetlamp gold. The composition leaves the lower two-thirds
nearly empty.
```
*Rationale:* removes the figure entirely. The car *is* her. The drawn medium
keeps the image from being mistaken for any actual location.

**Variant 3 — Film still**
```
Film still from a 1970s American art-house feature: a long two-lane road at
3am, lit only by a single sodium streetlamp and the receding red tail lights
of one car, framed wide with the road bisecting the lower third. The remaining
frame is the firs and a wet black sky. Anamorphic lens flare from the
streetlamp. Slight push-process magenta cast, visible film grain.
```
*Rationale:* gives the image a runtime — the song's "refusing to arrive" reads
as a held wide shot from a movie that's about to do something. Pull from
1970s film grammar (`Five Easy Pieces`, `Two-Lane Blacktop` energy) without
naming a film directly.

**Variant 4 — Mixed-media collage**
```
Mixed-media paper collage on dark grey board. A torn strip of pavement
photograph runs horizontally across the lower third. Above it, a single
hand-painted red tail light glows; above that, a sequence of small
hand-drawn streetlamps recedes in perspective toward the upper-right
corner, getting smaller and dimmer until they fade to nothing. Visible
paper texture, taped edges, pencil grid lines left in. No text.
```
*Rationale:* the boldest variant. Translates "counting streetlights instead of
years" into the literal grammar of the image — the lamps are the years, drawn
by hand. If Sera responds to this, the cover becomes a statement piece.

---

## What Maren delivers to the user

Four prompts, four rationale notes, four images returned from NanoBanana. She
posts them to the roundtable as a single message labeled *Cover variants — "Long
Way Home"* with the four images and the four rationale sentences. She does not
recommend one. The user picks.

If the user asks for revisions, Maren stays on the same anchor and either
shifts the axis or sharpens within the chosen variant. She does not abandon the
anchor without a reason — the anchor is the brief.
