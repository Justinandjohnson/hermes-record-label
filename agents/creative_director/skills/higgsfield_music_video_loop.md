# Skill — Collaborative Higgsfield music-video development

Load this skill when the artist wants AI-generated music-video concepts, shot prompts,
or a Higgsfield generation loop. The outcome is an artist-approved treatment and a
model-valid play-by-play, followed by real provider jobs only after cost approval.

## Evidence before ideation

Collect all available inputs and keep their authorship visible:

1. The artist's own direction, non-negotiables, references, likeness permission, and
   anything they explicitly do not want. Artist direction wins every conflict.
2. Maren's visual reading and catalog-continuity notes.
3. Ravi's emotional/structural observations, Rubin's essence elements, Rhone's cultural
   specificity, Janick's visual references, Kallman's instinct, and Dez's scope/budget.
4. Lyrics, standout `track_segments.visual_anchor` moments, arrangement transitions,
   track duration, and approved artwork/reference media.

Do not flatten these into anonymous “label feedback.” Candidate treatments name which
source contributed each anchor. If label thoughts are missing, ask the relevant agents
for visual ideas before drafting; do not invent consensus.

## Select the actual Higgsfield model

Model capabilities change. Never prompt from memory alone.

1. If the artist selected a model, call Higgsfield `models_get` for that model ID.
2. Otherwise call `models_recommend` with the desired video mode and available inputs,
   show the best choices/tradeoffs, let the artist choose, then call `models_get`.
3. Retain the returned model ID, durations/range, aspect ratios, media roles, parameters,
   audio behavior, and unlimited-generation support in the draft package.
4. Unsupported parameter names or media roles are errors, not suggestions to silently
   swap models. Re-query if the catalog changed.

Current model tendencies are only routing hints; `models_get` remains authoritative:

- Seedance 2.5: use `omni_reference` when identity, image, video, or track-audio
  references matter; use `t2v` only with no references. Describe continuous action.
- Kling 3.0: useful for intentional multi-shot motion and start/end frames. Set sound
  off when the released track will replace generated audio.
- FLUX 3 Video: useful for start/end-frame storyboards and continuation. Make the
  transformation between frames explicit.
- Grok Video 1.5: useful for concise cinematic motion from a start image or audio/image
  references; stay inside its shorter duration range.

## Draft three treatments

Run `scripts.social_release higgsfield-draft` with the artist direction, actual label
thoughts (or the track database), track duration, and the JSON returned by `models_get`.

Each of the three candidates must contain:

- one concrete narrative/visual thesis, not a palette or mood-word pile;
- a master prompt with subject identity, action, environment, composition, camera
  language, lighting/material texture, temporal behavior, and continuity rules;
- five key story beats tied to song timestamps;
- attributed evidence showing what came from the artist/song/each label voice;
- a real difference in narrative device, production approach, or visual grammar.

Do not produce twenty cosmetic adjective swaps. Do not claim any candidate is approved.

## Prompt construction for each shot

Write one observable clip, in this order:

`[subject/identity] [action through time] in [specific environment]. [shot size and
lens/composition]. [one achievable camera movement]. [lighting/material/atmosphere].
[physical change from first to last frame]. [continuity cue].`

Rules:

- Image-to-video prompts describe motion and change; the reference image already
  supplies appearance. Text-to-video prompts must specify appearance concretely.
- Prefer one camera movement. Avoid contradictions such as locked-off plus orbiting.
- Use positive descriptions (`an empty platform`) instead of negative prompt lists.
- Treat identity, wardrobe, hero props, palette, and screen direction as a continuity
  ledger. Repeat only what the model needs to preserve.
- Use start/end images only when the selected model supports those roles.
- Do not request captions, logos, release text, or watermarks inside generated footage.
- The mastered song remains the soundtrack. Disable native generated audio unless the
  artist explicitly wants separate ambience/effects for later mixing.

## Artist approval and editing

Present the three master prompts and five-beat outlines. The artist can:

- choose a candidate unchanged;
- edit its master prompt directly;
- combine named elements from candidates; or
- reject the round and give a correction.

Only an explicit choice/edit becomes `approved_prompt`. Run
`scripts.social_release higgsfield-approve --artist-approved` to expand it into exactly
20 planned shots. The resulting play-by-play must include song timestamp, generation
duration, story purpose, full prompt, continuity cue, and only supported model params.

Approval of the treatment is not approval to spend credits or generate.

## Cost gate and 20-job execution

1. Estimate the full 20-shot cost with the exact approved parameters. Check balance.
2. If Higgsfield returns `unlim_choice`, ask whether to use unlimited generations or
   credits; never choose for the artist.
3. After explicit cost approval, upload/confirm references and persist their media IDs.
4. Submit jobs in indexed batches of 6, 6, 6, and 2. Persist every returned job ID.
5. Poll with `jobs_wait` in groups of at most eight. Once all are terminal, call
   `show_generation_by_ids` exactly once for the collected set (up to 24).
6. A shot is complete only with a completed provider job and viewable result URL.
   Preserve failures as failures; never relabel plans, previews, or submission errors.

The artist reviews the 20 outputs before editing or publishing. Higgsfield generation
does not authorize YouTube/TikTok upload.
