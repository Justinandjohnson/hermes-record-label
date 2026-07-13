"""Hermes public demo API — bring-your-own-keys, fully stateless.

Drop one track in, get the Hermes A&R experience: deep audio analysis,
a four-judge Roundtable, a synthesized label verdict, and (optionally)
each judge's one-liner spoken aloud via ElevenLabs.

Privacy contract:
- API keys arrive per-request, live only in that request's scope, and are
  never logged, stored, or echoed back.
- Audio is analyzed in memory and discarded; nothing is persisted.

ponytail: judges read the Stage-1 analysis rather than re-hearing the audio
(4x audio tokens on the visitor's key); send audio to each judge if fidelity
ever matters more than cost.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

ANALYSIS_MODEL = "google/gemini-3.1-pro-preview"
JUDGE_MODEL = "anthropic/claude-sonnet-4.5"
SYNTHESIS_MODEL = "anthropic/claude-sonnet-4.5"

MAX_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_FORMATS = {".wav": "wav", ".mp3": "mp3", ".flac": "flac",
                   ".m4a": "m4a", ".ogg": "ogg", ".aiff": "aiff", ".aif": "aiff"}
RATE_LIMIT_RUNS = 6
RATE_LIMIT_WINDOW = 3600  # seconds

app = FastAPI(title="Hermes Demo", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lyokoandco.com", "https://www.lyokoandco.com",
                   "http://localhost:5173", "http://localhost:8788"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_runs: dict[str, list[float]] = defaultdict(list)  # ip -> timestamps


ANALYSIS_PROMPT = """You are the audio-analysis engine of Hermes, an AI record label.
Listen to this track completely. Return ONLY valid JSON:
{
  "bpm": number, "key": "e.g. F# minor", "genre": "primary genre",
  "subgenres": ["..."], "mood": ["3-5 descriptors"],
  "energy_arc": "one sentence describing the energy over time",
  "sections": [{"label": "intro/verse/chorus/...", "time": "m:ss", "note": "what happens"}],
  "production_notes": ["3-5 specific observations about the mix and arrangement"],
  "strengths": ["2-4 things genuinely working"],
  "concerns": ["2-4 honest issues"],
  "one_line_essence": "what this song is, in one sentence"
}"""

JUDGES = {
    "rubin": {
        "name": "Rick Rubin", "role": "Creative Catalyst",
        "lens": (
            "You are Rick Rubin, the Creative Catalyst. You don't think about genres — "
            "you think about truth. The only question: what is the song trying to say, "
            "and is anything getting in the way of it saying that? Technique serves the "
            "work, it doesn't lead it. You listen completely, without agenda. You hear "
            "what the song wants to be, not what the artist thinks it should be."
        ),
    },
    "rhone": {
        "name": "Sylvia Rhone", "role": "Cultural Authenticator",
        "lens": (
            "You are Sylvia Rhone, the Cultural Authenticator. Four decades watching music "
            "move through culture — you know the difference between music that comes from "
            "somewhere real and music constructed to go somewhere profitable. Warm but "
            "direct; a hard truth from you feels like someone fighting FOR the artist. "
            "You respected the music before the business, and never reversed that order."
        ),
    },
    "kallman": {
        "name": "Craig Kallman", "role": "Early Conviction Scout",
        "lens": (
            "You are Craig Kallman, the Early Conviction Scout. Thirty years signing "
            "artists before anyone knew their names — you hear potential as though the "
            "finished thing already exists. You knew in 30 seconds with Bruno Mars and "
            "Cardi B. Busy and direct: a CEO clearing a 10-second window to say something "
            "that matters. It either has it or it doesn't."
        ),
    },
    "janick": {
        "name": "John Janick", "role": "Vision Gatekeeper",
        "lens": (
            "You are John Janick, the Vision Gatekeeper. You built Fueled by Ramen as an "
            "indie, then ran Interscope with an indie mind. You look for complete worlds, "
            "not great songs — you sign artists, not tracks. The music is a door; you want "
            "to know what's behind it. Quiet, precise, no over-praise. When you say "
            "'I see it,' you mean it."
        ),
    },
}

JUDGE_TASK = """Here is the label's deep analysis of a track submitted to Hermes:

{analysis}

Give your verdict as this persona. Return ONLY valid JSON:
{{
  "verdict": "SIGN" | "DEVELOP" | "PASS",
  "score": number 1-10,
  "one_liner": "your verdict in one spoken sentence, in your voice",
  "notes": "2-3 sentences of specific, honest reasoning through your lens"
}}"""

SYNTHESIS_PROMPT = """You are Hermes, the label's coordination engine. Four judges have
ruled on a track. Synthesize a final label verdict.

Analysis: {analysis}

Verdicts: {verdicts}

Return ONLY valid JSON:
{{
  "final_verdict": "SIGN" | "DEVELOP" | "PASS",
  "headline": "one bold sentence — the label's position",
  "consensus": "2-3 sentences on where the room agreed and split",
  "next_moves": ["three concrete, specific next steps for this artist"]
}}"""


def _rate_limit(ip: str) -> None:
    now = time.time()
    _runs[ip] = [t for t in _runs[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_runs[ip]) >= RATE_LIMIT_RUNS:
        raise HTTPException(429, "Rate limit: 6 runs per hour per IP. Come back soon.")
    _runs[ip].append(now)


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


async def _openrouter(client: httpx.AsyncClient, key: str, model: str,
                      content: list | str, max_tokens: int = 4096) -> dict:
    resp = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}",
                 "HTTP-Referer": "https://lyokoandco.com",
                 "X-Title": "Hermes Demo"},
        json={"model": model,
              "messages": [{"role": "user", "content": content}],
              "temperature": 0.4, "max_tokens": max_tokens},
        timeout=240,
    )
    if resp.status_code == 401:
        raise HTTPException(401, "OpenRouter rejected that API key.")
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    if not raw:
        raise HTTPException(502, f"{model} returned an empty response.")
    return json.loads(_strip_fences(raw))


async def _voice_clips(client: httpx.AsyncClient, key: str,
                       verdicts: dict[str, dict]) -> dict[str, str]:
    """One spoken clip per judge, using the account's premade voices (fetched,
    never guessed). Returns {judge: base64 mp3}."""
    resp = await client.get(ELEVENLABS_VOICES_URL, headers={"xi-api-key": key}, timeout=30)
    if resp.status_code == 401:
        raise HTTPException(401, "ElevenLabs rejected that API key.")
    resp.raise_for_status()
    voices = [v["voice_id"] for v in resp.json().get("voices", [])]
    if len(voices) < len(verdicts):
        raise HTTPException(502, "ElevenLabs account has too few voices for the panel.")

    async def tts(voice_id: str, text: str) -> str:
        r = await client.post(
            ELEVENLABS_TTS_URL.format(voice_id=voice_id),
            headers={"xi-api-key": key},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
            timeout=120,
        )
        if r.status_code == 401:
            raise HTTPException(
                401, "ElevenLabs key lacks text-to-speech permission — "
                     "use a key with full TTS access.")
        r.raise_for_status()
        return base64.b64encode(r.content).decode("ascii")

    keys = list(verdicts)
    clips = await asyncio.gather(
        *(tts(voices[i], verdicts[k]["one_liner"]) for i, k in enumerate(keys))
    )
    return dict(zip(keys, clips))


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "hermes-demo"}


@app.post("/run")
async def run(request: Request,
              file: UploadFile = File(...),
              openrouter_key: str = Form(...),
              elevenlabs_key: str = Form("")) -> StreamingResponse:
    _rate_limit(request.client.host if request.client else "unknown")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_FORMATS:
        raise HTTPException(415, f"Unsupported format {suffix or '(none)'} — "
                                 f"use {', '.join(sorted(ALLOWED_FORMATS))}.")
    audio = await file.read()
    if len(audio) > MAX_FILE_BYTES:
        raise HTTPException(413, "File too large — 20 MB max for the demo.")
    if not audio:
        raise HTTPException(400, "Empty file.")

    or_key = openrouter_key.strip()
    el_key = elevenlabs_key.strip()
    if not or_key:
        raise HTTPException(400, "OpenRouter API key is required.")

    audio_b64 = base64.b64encode(audio).decode("ascii")
    audio_format = ALLOWED_FORMATS[suffix]

    async def pipeline():
        def event(**kw) -> str:
            return json.dumps(kw) + "\n"

        try:
            async with httpx.AsyncClient() as client:
                yield event(stage="analysis", status="running",
                            label="The label is listening…")
                analysis = await _openrouter(client, or_key, ANALYSIS_MODEL, [
                    {"type": "text", "text": ANALYSIS_PROMPT},
                    {"type": "input_audio",
                     "input_audio": {"data": audio_b64, "format": audio_format}},
                ], max_tokens=8192)
                yield event(stage="analysis", status="done", data=analysis)

                yield event(stage="roundtable", status="running",
                            label="The Roundtable convenes…")
                analysis_json = json.dumps(analysis, indent=2)
                results = await asyncio.gather(*(
                    _openrouter(client, or_key, JUDGE_MODEL,
                                f"{j['lens']}\n\n{JUDGE_TASK.format(analysis=analysis_json)}")
                    for j in JUDGES.values()
                ))
                verdicts = {k: {**JUDGES[k], "lens": None, **r}
                            for k, r in zip(JUDGES, results)}
                for k, v in verdicts.items():
                    yield event(stage="judge", judge=k, status="done", data=v)

                yield event(stage="synthesis", status="running",
                            label="Hermes weighs the room…")
                synthesis = await _openrouter(
                    client, or_key, SYNTHESIS_MODEL,
                    SYNTHESIS_PROMPT.format(analysis=analysis_json,
                                            verdicts=json.dumps(
                                                {k: {x: v[x] for x in
                                                     ("name", "verdict", "score", "notes")}
                                                 for k, v in verdicts.items()})))
                yield event(stage="synthesis", status="done", data=synthesis)

                if el_key:
                    yield event(stage="voice", status="running",
                                label="The judges speak…")
                    clips = await _voice_clips(client, el_key, verdicts)
                    yield event(stage="voice", status="done", data=clips)

                yield event(stage="complete", status="done")
        except HTTPException as exc:
            yield event(stage="error", status="error", detail=exc.detail)
        except json.JSONDecodeError:
            yield event(stage="error", status="error",
                        detail="A model returned malformed JSON — try again.")
        except httpx.HTTPStatusError as exc:
            yield event(stage="error", status="error",
                        detail=f"Upstream API error ({exc.response.status_code}).")

    return StreamingResponse(pipeline(), media_type="application/x-ndjson")
