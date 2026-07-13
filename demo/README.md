# Hermes Demo API

The public try-it service behind [lyokoandco.com/hermes-demo.html](https://lyokoandco.com/hermes-demo.html).

Drop in one track with your own OpenRouter key (ElevenLabs key optional for
spoken verdicts) and get the Hermes A&R experience: deep audio analysis, the
four-judge Roundtable, and a synthesized label verdict.

**Stateless by design** — your keys and audio exist only for the duration of
your request. Nothing is stored, logged, or reused.

## Run locally

```bash
cd demo
pip install -r requirements.txt
uvicorn app:app --port 8788
```

`POST /run` (multipart): `file` (audio ≤ 20 MB), `openrouter_key`,
`elevenlabs_key` (optional). Streams NDJSON progress events.
