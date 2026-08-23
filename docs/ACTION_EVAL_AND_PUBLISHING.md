# Action eval and social publishing

## Prove the label acted

```bash
uv run python -m evals.action_harness --audio "/path/to/song.flac"
```

The persistent `report.json` verifies the accepted bytes, track row, emitted event,
vault copy, and matching SHA-256 hashes. A passing result is `action_backed`; an
agent response by itself can never pass.

Use `--full-pipeline` only with production credentials and dependencies. It also
requires real analysis and timed-segment rows, four non-empty stems, audio features,
an embedding, a release-state transition, and a clean dispatcher exit. It does not
mock any service or convert provider errors into passes.

Use the quality budgets to make speed and concision regressions fail visibly:

```bash
uv run python -m evals.action_harness --audio "/path/to/song.flac" \
  --full-pipeline --max-runtime-seconds 55 \
  --max-agent-words 40 --max-average-agent-words 35
```

The report keeps `verdict` for durable action and adds an independent
`quality_verdict`, per-stage `timings_ms`, and review-message word counts. This keeps a
fast but broken run from passing, while also exposing a correct but slow or verbose run.

## Prepare platform deliverables

```bash
uv run python -m scripts.social_release prepare --audio "/path/to/song.wav" \
  --artwork "/path/to/cover.png" --output-dir release-output --slug song-title
```

This creates probed H.264/AAC MP4 files in 16:9 and 9:16. The image is padded rather
than cropped and the audio determines the exact duration.

## YouTube

Use a Google OAuth authorized-user token with the `youtube.upload` scope. Start
private, inspect the result in Studio, then intentionally change visibility.

```bash
uv run python -m scripts.social_release youtube-auth \
  --client-secrets "/path/to/google-client-secret.json" \
  --token ~/.hermes/google/youtube-token.json --channel "@yourchannel"
```

```bash
uv run python -m scripts.social_release youtube --video release-output/song-youtube.mp4 \
  --token ~/.hermes/google/youtube-token.json --title "Song — Artist" \
  --channel "@yourchannel" --description "Official audio" --privacy private \
  --artist-approved
```

The approval flag is mandatory. A successful call returns the real video ID and
processing status. See [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
and [resumable uploads](https://developers.google.com/youtube/v3/guides/uploading_a_video).

## TikTok Direct Post

The TikTok app needs approved `video.publish` access and artist authorization. The
client queries creator capabilities before every post, enforces returned constraints,
uploads valid byte ranges, and returns the real `publish_id` and status.

```powershell
$env:TIKTOK_ACCESS_TOKEN="..."
uv run python -m scripts.social_release tiktok --video release-output/song-tiktok.mp4 `
  --title "Song — Artist" --privacy SELF_ONLY --artist-approved
```

See the [Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started)
and [media transfer guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide).

## Optional Higgsfield 20-shot loop

First use Higgsfield `models_recommend`/`models_get` and save the selected model's
returned JSON as `model-profile.json`. Drafting combines the artist direction with
attributed label feedback from the track database:

```bash
uv run python -m scripts.social_release higgsfield-draft \
  --user-direction "A solitary performer moving through an empty night train" \
  --duration 99.25 --aspect-ratio 9:16 --model-profile model-profile.json \
  --db hermes.db --track-id 12 --output release-output/video-draft.json
```

The artist reviews three treatments. They may approve one unchanged or save an edited
master prompt in `edited-prompt.txt`:

```bash
uv run python -m scripts.social_release higgsfield-approve \
  --draft release-output/video-draft.json --candidate 2 \
  --edited-prompt-file edited-prompt.txt --artist-approved \
  --output release-output/approved-20-shot-plan.json
```

The result is an approved prompt plus a 20-shot play-by-play validated against the live
profile's aspect ratios, durations, option values, and parameter names. Native model
audio is disabled so the mastered track remains authoritative. It still does
not claim videos exist. Estimate the exact cost and obtain separate spending approval,
then submit batches of 6, 6, 6, and 2. Completion requires 20 real provider job IDs and
20 fetched, viewable results; failed jobs remain failed. See the Creative Director's
`skills/higgsfield_music_video_loop.md` for model-aware prompting and execution rules.
