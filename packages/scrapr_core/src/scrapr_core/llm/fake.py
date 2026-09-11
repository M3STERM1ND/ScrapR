"""A fake provider returning canned structured responses.

`OPEN-04` has not named an AI provider, and Phase 0 does not need one: what it
needs is the *seam* exercised, so that choosing a provider later is a config
change (implementation plan §13). Every stage can be built and tested against
this.

It is also a test oracle for the trust boundary. The type checker already
refuses to pass `Untrusted` as an instruction; this fake refuses at runtime too,
so a test that reaches for `str(content)` to "just make it work" fails loudly
instead of quietly proving the wrong thing.

Deterministic by construction: responses are enqueued in the order they will be
consumed, and a call with nothing left to return raises rather than inventing a
value. A fake that improvises is a fake that hides a missing expectation.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from pydantic import BaseModel

from scrapr_core.llm.contract import (
    ModelTier,
    StructuredResult,
    TokenUsage,
    UntrustedDocument,
)
from scrapr_core.llm.envelope import render_untrusted
from scrapr_core.security.trust import Trusted

__all__ = ["FakeLLMProvider", "NoCannedResponseError", "RecordedCall"]


class NoCannedResponseError(AssertionError):
    """Raised when the fake is called with no response left to give."""


@final
@dataclass(frozen=True, slots=True)
class RecordedCall:
    """What one call to the fake looked like, for a test to assert against."""

    instruction: Trusted
    documents: Sequence[UntrustedDocument]
    schema: type[BaseModel]
    tier: ModelTier
    rendered: str
    """The untrusted half as it would have been sent — fenced and labelled.

    Recorded so an adversarial test can assert an injection payload appears only
    inside an envelope, and never in the instruction.
    """


class FakeLLMProvider:
    """Returns pre-loaded responses in order, recording every call."""

    def __init__(self, *, name: str = "fake", model: str = "fake-1") -> None:
        self._name = name
        self._model = model
        self._responses: deque[BaseModel] = deque()
        self._calls: list[RecordedCall] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def calls(self) -> Sequence[RecordedCall]:
        return tuple(self._calls)

    def enqueue(self, *responses: BaseModel) -> None:
        """Load responses, consumed in the order given."""
        self._responses.extend(responses)

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        """Return the next canned response, checked against `schema`."""
        if not isinstance(instruction, Trusted):
            raise TypeError(
                "instruction must be Trusted; retrieved content belongs in "
                "untrusted=[...] (REQ-SEC-012)"
            )

        self._calls.append(
            RecordedCall(
                instruction=instruction,
                documents=tuple(untrusted),
                schema=schema,
                tier=model_tier,
                rendered=render_untrusted(untrusted),
            )
        )

        if not self._responses:
            raise NoCannedResponseError(
                f"the fake provider was asked for a {schema.__name__} but has no "
                "responses left; enqueue one in the test"
            )

        response = self._responses.popleft()
        if not isinstance(response, schema):
            raise NoCannedResponseError(
                f"the next canned response is {type(response).__name__}, but the "
                f"caller asked for {schema.__name__}"
            )

        return StructuredResult(
            value=response,
            model=self._model,
            tier=model_tier,
            usage=TokenUsage(input_tokens=0, output_tokens=0, cost_micros=0),
        )
