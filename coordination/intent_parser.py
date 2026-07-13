"""Classify artist SMS intent via Claude on OpenRouter.

Parses inbound artist messages to determine intent (approve, reject, revise,
delay, question, casual) with a confidence threshold.  Below 0.8 confidence
the parser returns ``needs_clarification``.
"""

from __future__ import annotations

import enum
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------

class IntentType(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"
    DELAY = "delay"
    QUESTION = "question"
    CASUAL = "casual"
    NEEDS_CLARIFICATION = "needs_clarification"


class Intent(BaseModel):
    """Parsed intent from an artist message."""

    intent_type: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured data extracted from the message (e.g. date for delay)",
    )
    raw_message: str = ""
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are classifying an artist's SMS message in the context of a record label \
release pipeline.  The artist is responding to feedback about their track.

Classify the message into exactly one intent:
- approve: Artist wants to proceed / ship it / it's good / green light
- reject: Artist wants to scrap / abandon / start over entirely
- revise: Artist wants to make changes / needs work / will fix it
- delay: Artist wants more time / push back timeline / give me until [date]
- question: Artist is asking a question about feedback or process
- casual: Greeting, thank you, or unrelated chatter

Return ONLY valid JSON with this exact schema:
{
  "intent_type": "approve|reject|revise|delay|question|casual",
  "confidence": 0.0-1.0,
  "extracted_data": {},
  "reasoning": "brief explanation"
}

For "delay" intent, include {"date": "YYYY-MM-DD"} in extracted_data if a date \
is mentioned.  For "revise" intent, include {"focus_areas": [...]} if the artist \
mentions specific things to fix.

Examples:
- "ship it" → approve (0.95)
- "this is fire let's go" → approve (0.92)
- "needs work on the chorus" → revise (0.90), focus_areas: ["chorus"]
- "give me till friday" → delay (0.88)
- "what did you mean about the bridge?" → question (0.93)
- "hey what's up" → casual (0.95)
- "hmm" → needs lower confidence, likely needs_clarification
"""

CONFIDENCE_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class IntentParser:
    """Async intent classifier using Claude via OpenRouter."""

    def __init__(
        self,
        openrouter_api_key: str,
        model: str = "anthropic/claude-sonnet-5",
        base_url: str = "https://openrouter.ai/api/v1",
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ) -> None:
        self._api_key = openrouter_api_key
        self._model = model
        self._base_url = base_url
        self._confidence_threshold = confidence_threshold
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-record-label.local",
                    "X-Title": "AI Record Label Intent Parser",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def classify(self, message: str, context: str = "") -> Intent:
        """Classify an artist SMS message.

        Parameters
        ----------
        message:
            The raw SMS text from the artist.
        context:
            Optional context about the current release state (e.g. "Track is
            in FEEDBACK_GIVEN state, A&R sent mix notes yesterday").

        Returns
        -------
        Intent with type, confidence, and extracted data.  If confidence is
        below threshold, intent_type will be ``needs_clarification``.
        """
        user_content = f"Artist message: {message!r}"
        if context:
            user_content = f"Context: {context}\n\n{user_content}"

        client = await self._get_client()

        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 256,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("OpenRouter API error: %s %s", exc.response.status_code, exc.response.text)
            return Intent(
                intent_type=IntentType.NEEDS_CLARIFICATION,
                confidence=0.0,
                raw_message=message,
                reasoning=f"API error: {exc.response.status_code}",
            )
        except httpx.RequestError as exc:
            logger.error("OpenRouter request failed: %s", exc)
            return Intent(
                intent_type=IntentType.NEEDS_CLARIFICATION,
                confidence=0.0,
                raw_message=message,
                reasoning=f"Request failed: {exc}",
            )

        return self._parse_response(response.json(), message)

    def _parse_response(self, api_response: dict[str, Any], original_message: str) -> Intent:
        """Parse the OpenRouter API response into an Intent."""
        try:
            content = api_response["choices"][0]["message"]["content"]
            # Strip markdown code fences if present.
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("Failed to parse LLM response: %s", exc)
            return Intent(
                intent_type=IntentType.NEEDS_CLARIFICATION,
                confidence=0.0,
                raw_message=original_message,
                reasoning=f"Parse error: {exc}",
            )

        confidence = float(parsed.get("confidence", 0.0))
        intent_type_raw = parsed.get("intent_type", "casual")

        try:
            intent_type = IntentType(intent_type_raw)
        except ValueError:
            intent_type = IntentType.NEEDS_CLARIFICATION
            confidence = 0.0

        # Apply confidence threshold.
        if confidence < self._confidence_threshold:
            return Intent(
                intent_type=IntentType.NEEDS_CLARIFICATION,
                confidence=confidence,
                extracted_data=parsed.get("extracted_data", {}),
                raw_message=original_message,
                reasoning=parsed.get("reasoning", "Below confidence threshold"),
            )

        return Intent(
            intent_type=intent_type,
            confidence=confidence,
            extracted_data=parsed.get("extracted_data", {}),
            raw_message=original_message,
            reasoning=parsed.get("reasoning", ""),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
