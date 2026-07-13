"""Tests for intent parser — SMS classification via Claude/OpenRouter."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from coordination.intent_parser import (
    CONFIDENCE_THRESHOLD,
    Intent,
    IntentParser,
    IntentType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser() -> IntentParser:
    return IntentParser(
        openrouter_api_key="test-key-123",
        model="anthropic/claude-sonnet-4-20250514",
        base_url="https://openrouter.ai/api/v1",
    )


def _mock_openrouter_response(intent_type: str, confidence: float, **extra: object) -> dict:
    """Build a mock OpenRouter chat completion response."""
    content = json.dumps({
        "intent_type": intent_type,
        "confidence": confidence,
        "extracted_data": extra.get("extracted_data", {}),
        "reasoning": extra.get("reasoning", "test"),
    })
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntentClassification:
    @pytest.mark.asyncio
    @respx.mock
    async def test_approve_intent(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_mock_openrouter_response("approve", 0.95))
        )

        intent = await parser.classify("ship it")
        assert intent.intent_type == IntentType.APPROVE
        assert intent.confidence == 0.95
        assert intent.raw_message == "ship it"

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_revise_intent(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_mock_openrouter_response(
                    "revise",
                    0.90,
                    extracted_data={"focus_areas": ["chorus"]},
                ),
            )
        )

        intent = await parser.classify("needs work on the chorus")
        assert intent.intent_type == IntentType.REVISE
        assert intent.extracted_data.get("focus_areas") == ["chorus"]

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_delay_intent_with_date(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_mock_openrouter_response(
                    "delay",
                    0.88,
                    extracted_data={"date": "2026-05-22"},
                ),
            )
        )

        intent = await parser.classify("give me till friday")
        assert intent.intent_type == IntentType.DELAY
        assert intent.extracted_data.get("date") == "2026-05-22"

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_question_intent(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_mock_openrouter_response("question", 0.93))
        )

        intent = await parser.classify("what did you mean about the bridge?")
        assert intent.intent_type == IntentType.QUESTION

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_casual_intent(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_mock_openrouter_response("casual", 0.95))
        )

        intent = await parser.classify("hey what's up")
        assert intent.intent_type == IntentType.CASUAL

        await parser.close()


class TestConfidenceThreshold:
    @pytest.mark.asyncio
    @respx.mock
    async def test_low_confidence_returns_needs_clarification(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_mock_openrouter_response("approve", 0.5))
        )

        intent = await parser.classify("hmm")
        assert intent.intent_type == IntentType.NEEDS_CLARIFICATION
        assert intent.confidence == 0.5

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_exactly_at_threshold(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_mock_openrouter_response("approve", CONFIDENCE_THRESHOLD),
            )
        )

        intent = await parser.classify("yeah ok")
        assert intent.intent_type == IntentType.APPROVE

        await parser.close()

    def test_threshold_value(self) -> None:
        assert CONFIDENCE_THRESHOLD == 0.8


class TestErrorHandling:
    @pytest.mark.asyncio
    @respx.mock
    async def test_api_error_returns_needs_clarification(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "Internal error"})
        )

        intent = await parser.classify("ship it")
        assert intent.intent_type == IntentType.NEEDS_CLARIFICATION
        assert intent.confidence == 0.0
        assert "API error" in intent.reasoning

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_json_response(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not valid json"}}]},
            )
        )

        intent = await parser.classify("test")
        assert intent.intent_type == IntentType.NEEDS_CLARIFICATION

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_markdown_wrapped_json(self, parser: IntentParser) -> None:
        """LLMs sometimes wrap JSON in markdown code fences."""
        content = '```json\n{"intent_type": "approve", "confidence": 0.92, "extracted_data": {}, "reasoning": "test"}\n```'
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
            )
        )

        intent = await parser.classify("let's go")
        assert intent.intent_type == IntentType.APPROVE
        assert intent.confidence == 0.92

        await parser.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_intent_type(self, parser: IntentParser) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_mock_openrouter_response("invalid_intent", 0.95),
            )
        )

        intent = await parser.classify("something weird")
        assert intent.intent_type == IntentType.NEEDS_CLARIFICATION

        await parser.close()


class TestIntentModel:
    def test_intent_model_fields(self) -> None:
        intent = Intent(
            intent_type=IntentType.APPROVE,
            confidence=0.95,
            raw_message="ship it",
            reasoning="Clear approval",
        )
        assert intent.intent_type == IntentType.APPROVE
        assert intent.confidence == 0.95
        assert intent.extracted_data == {}

    def test_all_intent_types(self) -> None:
        expected = {"approve", "reject", "revise", "delay", "question", "casual", "needs_clarification"}
        actual = {t.value for t in IntentType}
        assert actual == expected
