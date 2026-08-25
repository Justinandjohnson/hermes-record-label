import json

import numpy as np
import pytest

from audio_analysis import segment_analyzer


def _description(start: float, end: float) -> dict:
    return {
        "start_sec": start,
        "end_sec": end,
        "section_label": "loop",
        "energy": 4,
        "elements_present": ["synth"],
        "mood": "still",
        "production_notes": "A repeating synth figure continues.",
        "standout": False,
        "standout_reason": None,
        "visual_anchor": "a blue light moving across an empty room",
    }


@pytest.mark.asyncio
async def test_description_batch_retries_each_boundary_when_provider_omits_segments(
    tmp_path, monkeypatch
):
    audio_path = tmp_path / "track.wav"
    audio_path.write_bytes(b"fixture")
    boundaries = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    calls: list[list[tuple[float, float]]] = []

    monkeypatch.setattr(
        segment_analyzer.librosa,
        "load",
        lambda *_args, **_kwargs: (np.zeros(66_150, dtype=np.float32), 22_050),
    )

    async def fake_call(_path, batch, *, model, api_key):
        calls.append(batch)
        described = [_description(*boundary) for boundary in batch]
        if len(batch) > 1:
            described = described[:1]
        return json.dumps({"segments": described})

    monkeypatch.setattr(segment_analyzer, "_call_gemini_batch", fake_call)

    result = await segment_analyzer._call_gemini_for_descriptions(
        audio_path,
        boundaries,
        model="fixture-model",
        api_key="fixture-key",
    )

    assert calls == [boundaries, [boundaries[0]], [boundaries[1]], [boundaries[2]]]
    assert [(row["start_sec"], row["end_sec"]) for row in result] == boundaries
