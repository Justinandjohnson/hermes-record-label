# Hermes action audit — 2026-08-22

## Outcome

The production dispatcher does real work, but the repository's existing suite did not
prove that a real recording produced durable outcomes. The new action harness does.
Its acceptance run on `[TwoShot] j.flac` passed 7/7 local action checks: validation,
inbox artifact, database registration, matching hash, event emission, vault artifact,
and matching vault hash.

## Findings

### High — the legacy engine overclaimed action execution

`coordination.engine.CoordinationEngine` is unused by production and returns
`ActionResult` descriptors from pure rules. It does not send messages, analyze audio,
or upload anything. Its documentation said it executed/dispatched actions, which made
descriptor generation look like completed work. The wording is now truthful. The real
runtime is `TrackPipelineDispatcher` in `coordination/dispatcher.py`.

### High — a dispatcher failure could prevent vaulting

The watcher emitted to the synchronous production dispatcher before mirroring the
accepted source. A model/network exception therefore skipped vault copies. Mirroring
now happens immediately after registration and before downstream work, with a regression
test proving the original remains vaulted when dispatch raises.

### High — full embeddings were undeployable from a clean install on Windows

The dispatcher always calls PANNs, but `panns-inference` was not declared. The package's
own first-run downloader invokes Unix `wget`, so even installing it manually failed on
Windows. The dependency is now locked and Hermes downloads/verifies the labels and
checkpoint itself using HTTP before inference.

### Medium — Windows intake was broken

The periodic scanner hardcoded `/usr/bin/find` even though the project advertises
Windows launch support. It now uses cross-platform pathlib traversal.

### Medium — the headline test claim is misleading

README says “288 tests passing.” The current cross-platform run collected more tests,
but the default suite mixes unit tests with machine-specific Mac deployment assertions.
On this Windows host: 311 portable tests pass; the full invocation has 12 failures for
missing Homebrew/macOS binaries, LaunchAgent paths, Mac credentials, and a running Mac
deployment. Those are environment checks, not portable product regressions.

### Medium — existing end-to-end coverage is mostly simulated

The test tree contains extensive mock/patch/skip usage, including patched analysis,
stems, embeddings, calendars, and agents. Those tests are useful for logic but cannot
answer “did the label act on a real song?” The real-file harness prohibits replacement
services and grades observable files, hashes, rows, stems, embeddings, and transitions.

### Low — optional madmom analysis is unavailable on this Windows environment

The live run logged `No module named 'madmom'`. Core librosa features, timed model
segments, stems, and embeddings still completed, so this does not fail the action tier.
Treat madmom-specific downbeat/beat refinements as an optional deployment capability
until a Python 3.12/Windows-compatible package or replacement is selected.

### High — browser intake and reads omitted the local API token

The browser UI loaded successfully but sent unauthenticated `POST /api/intake` requests,
which the API correctly rejected. Its read bridge also required a remote configuration in
browser mode, so normal track reads repeatedly failed with `No remote config`. Browser
mode now bootstraps the local `/token` once and uses it consistently for reads, uploads,
audio, settings, state transitions, and artist messages. The rebuilt browser client and
an authenticated intake-route check now pass against the running local API.

### High — the Windows launcher did not launch a usable application

The PowerShell launcher wrote to the reserved `$PID` variable, required a separate
`sqlite3` executable, applied only migration 001, treated optional Hermes as mandatory,
and never started the HTTP API. It now applies and records all 15 migrations through
Python, builds the UI, starts and health-checks both the watcher and API, records exact
process trees for reliable shutdown, and falls back to the browser when no Tauri binary
exists. A stop/restart cycle was verified against the live installation.

### High — token bootstrap was exposed more broadly than necessary

The API listens on all interfaces so configured Tailscale clients can use it, but the
browser bootstrap endpoint must not disclose its bearer token to LAN/Tailscale callers.
`/token` now requires a loopback peer, a local Host header, and an approved local app
Origin. Remote clients continue to work with a token they have been explicitly given.

### Medium — successful intake blocked on the slow AI pipeline

The upload response waited while analysis, model calls, and agent review ran. That made
a healthy intake appear frozen and increased the chance of browser/proxy timeouts. File
validation and database registration remain synchronous, but the action pipeline now
runs in a named background worker and the response reports whether it started. Errors
are persisted/logged by the pipeline, while the UI gets an immediate useful result.

### Medium — frontend failures hid the actionable server error

The Hub replaced every upload failure with “check the server.” Multipart errors are now
decoded from the API's JSON response and displayed to the artist. The API also rejects
empty, invalid-length, and over-limit uploads before reading their bodies; the limit is
configurable and defaults to 512 MiB.

### Medium — dependency and route drift escaped review

The UI used `/verdict`, but that path was absent from the server's protected GET registry.
It is now registered and covered by a contract regression. `npm audit` also found six
advisories (four high); the lockfile was updated and now reports zero known advisories.

### Low — the repository-wide lint baseline is not yet clean

The runtime-significant static pass (`F`/`E9`) is clean across production code. The
broader Ruff configuration still reports pre-existing formatting, modernization, and
ambiguous-Unicode warnings (including intentional musical/IPA characters), mostly style
debt rather than execution defects. These should be handled as a dedicated formatting
change instead of mixing hundreds of mechanical edits into the readiness fixes.

### High — Live Mode used a loudness gate instead of real speech detection

The RMS-based detector could not reliably distinguish a person from room noise and its
manual Record/Finish fallbacks contradicted hands-free Live Mode. It has been replaced
with the Silero v5 neural browser VAD running through ONNX Runtime Web and AudioWorklet.
It preserves 800 ms before speech, rejects sub-300 ms noises, and closes a real speech
turn after 4.5 seconds below the non-speech threshold. The captured 16 kHz float audio is
encoded as a mono PCM16 WAV before ElevenLabs transcription.

The VAD model and runtime assets are copied locally during dev/build, the heavy runtime
is lazy-loaded, and the initialized model is reused across conversation turns. Enabling
Live Mode voices the latest table message, then opens the mic; agent playback pauses the
mic, transcription is dispatched to the table, new replies play sequentially, and the
mic reopens. Manual recording controls have been removed.

A real-browser regression now feeds a 6.27-second spoken roundtable fixture through the
production Silero/AudioWorklet path and independently forces the adaptive-energy fallback
with the same frames while continuous interface noise remains present after the speech.
Both paths must detect speech, emit a valid 16 kHz WAV, and close within 3.5–6.0 seconds
after playback. The release gate adapts to the lower-quartile noise floor and the detected
speech peak, so steady interface noise cannot hold a turn open indefinitely. The main
action harness exposes this as the required `hands_free_voice_turn` check via
`--check-live-mode`. Latest evidence: both paths passed, the turn closed after 4.324
seconds despite the noisy tail, and Chrome reported no page errors.

Physical-input diagnostics then proved the browser defaulted to `DroidCam Audio`, which
delivered frames containing digital silence; automatic fallback selected the Steinberg
UR22, where ElevenLabs identified the captured content as `[static]`. Live Mode now lists
all browser microphone inputs explicitly with live dB/neural telemetry, remembers the
selected device for the active session, and blocks static/noise-only transcripts instead
of dispatching them to agents. Voice playback now has generation-based cancellation,
stops on Live Mode cleanup, and pauses track playback before an agent voice starts.
The selected physical input and gain are now persisted per browser. With no saved choice,
Live Mode rejects virtual-camera inputs in favor of the available hardware interface;
reconnecting an interface safely falls back if its browser device ID changed. A live
0.25×–4.0× gain slider scales both detection energy and the WAV sent to transcription.

### High — artist chat did not create a real roundtable conversation

Questions were routed to a pending-message draft with one fixed context key. The first
question eventually received only a canned acknowledgement and later questions could be
deduplicated into no response. Artist messages now return immediately, dispatch in the
background, and generate fresh context-grounded roundtable replies for each question.
Typed replies participate in the Live Mode state machine: listening pauses, the artist's
message is shown in the room, new replies are voiced in order, and the microphone re-arms.
The Creative Director had been omitted from the first-listen roster, while artist questions
only invited A&R and the manager. First listens and room-wide questions now require all
seven label voices. Every agent returns a separately validated visual conception grounded
in the track; Maren leads with the most specific scene, palette, light, texture, composition,
camera behavior, and a generative question for the artist.

### Full live tier — passing

The local and full real-song tiers are green. With the supplied ignored local OpenRouter
credential, the live tier persisted a real model analysis, four non-empty Demucs stems,
timed segments, local audio features, a PANNs embedding, twelve agent/system messages,
vault bytes, and two release-state transitions. Run `20260823T020901Z-a622b8fdda`
passed all 16/16 observable action checks, including `complete_roundtable_spoke` and
`every_agent_shared_visual_conception`, and ended in `FEEDBACK_GIVEN`. The seven review
messages averaged 50.1 words and topped out at 67 words.

The live run exposed two provider-compatibility failures. Gemini sometimes returned one
description for a six-segment batch or omitted required fields; failed batches now retry
one boundary at a time. Qwen 3.8 defaulted to optional `xhigh` reasoning, exhausted the
voice completion budget, and returned null content. Voice calls now disable reasoning,
validate both structured fields, and retry malformed responses. PANNs downloaded and
cached its 327,428,481-byte checkpoint successfully on this Windows host.

### Performance and concision benchmark

The full harness now has independent action and quality verdicts, configurable runtime
and word budgets, per-stage timing, and per-message word counts. On the same real audio:

- baseline: 60.55 seconds, 30.0 average review words;
- optimized: 45.09 seconds, 30.5 average review words, maximum 36;
- result: 25.5% faster while retaining 14/14 action checks and all quality gates.

Compared with the earlier uncapped live output (41.0 words on average), current review
messages are 25.6% shorter.

The improvement overlaps paid network segment analysis with independent local stems,
features, and embedding work. Six agent voices were already concurrent; their structured
response cap is now 192 tokens with mandatory-minimal reasoning and a 40-word hard limit
(32 words for the manager's next-choice message). Primary audio analysis is now the
largest remaining latency target at 28.42 seconds.

## Added release operations

- Verified H.264/AAC 16:9 YouTube and 9:16 TikTok deliverables from real audio/artwork.
- Real YouTube resumable upload returning provider ID and processing status.
- Real TikTok creator preflight, chunk upload, publish ID, and status fetch.
- Mandatory artist approval gates on all external uploads.
- A collaborative Higgsfield workflow: seven attributed label voices plus artist input
  produce three treatments; only an artist-approved or edited prompt expands into a
  model-profile-validated 20-shot plan. Planned jobs remain planned until 20 real
  provider IDs and fetched results exist.
