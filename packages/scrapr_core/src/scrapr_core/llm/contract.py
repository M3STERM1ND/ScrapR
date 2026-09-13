"""The AI provider abstraction (`REQ-TECH-006`).

One interface; the provider is selected by config once `OPEN-04` closes. Two
properties are worth more than the indirection itself:

1. **Structured output everywhere.** Every stage that uses the model returns a
   validated Pydantic model, never free prose that a later stage has to parse.
   Extraction returns evidence records, synthesis returns claims, conflict
   explanation returns a category plus text. Free prose survives only in section
   body text and conversational answers, and even those carry claim references.

2. **Instructions and research material are separate parameters.** There is no
   signature that accepts untrusted text as instruction (§9). That is what makes
   `REQ-SEC-012 AC-1` — the boundary is structural, not a matter of prompt
   wording — true of the call itself rather than of a convention.

`ModelTier` exists because `OPEN-04` asks which tier serves which stage. Naming
the tiers now turns that into a config table instead of a decision scattered
across seven call sites.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Protocol, final

from pydantic import BaseModel

from scrapr_core.security.trust import Trusted, Untrusted

__all__ = [
    "LLMProvider",
    "ModelTier",
    "StructuredResult",
    "TokenUsage",
    "UntrustedDocument",
]


@unique
class ModelTier(StrEnum):
    """How much model a stage needs.

    A tier, not a model name: the orchestrator states what the work is worth and
    configuration decides what answers it, so changing model is not a change to
    the pipeline.
    """

    CHEAP = "cheap"
    """High volume, low judgement: classification, extraction from clean text."""

    STANDARD = "standard"
    """The default. Synthesis, conflict explanation, sufficiency judgement."""

    DEEP = "deep"
    """Reserved for work where a wrong answer is expensive and rare enough to
    pay for: cross-area reasoning, final validation of a contested claim."""


@final
@dataclass(frozen=True, slots=True)
class UntrustedDocument:
    """One piece of research material handed to the model as *data*.

    `label` is written by the application, never taken from the content, so a
    document cannot title itself "System instructions".
    """

    content: Untrusted
    label: str = "document"
    ref: str | None = None
    """The block number the envelope shows (`#<ref>`), when a stage needs the
    model to point back at a block. Unset, blocks are numbered by position.

    Extraction needs it: positional numbering counted the question as `#1`, so
    the first retrieved item was `#2` while the schema asked for index `0`. A
    model answering with the number it could see pointed every excerpt two
    blocks away from its source, and grounding dropped it."""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("an untrusted document needs a label to be referred by")
        if self.ref is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.ref):
            # Application-authored and rendered into the envelope header, so it
            # must not be able to carry anything but a short token.
            raise ValueError("a document ref must be a short alphanumeric token")


@final
@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What one call consumed. Feeds the run's effort budget (`DEC-04`)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    """Millionths of a currency unit; integer, because floating-point money is a
    reconciliation bug waiting to happen."""


@final
@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    """A validated response, plus what it cost to get.

    Generic over the schema, so a caller that asks for `EvidenceRecord` gets
    `EvidenceRecord` and not `BaseModel` — the type checker keeps the promise
    that structured output is actually structured.
    """

    value: T
    model: str
    tier: ModelTier
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMProvider(Protocol):
    """What every AI provider implements.

    `instruction` is `Trusted` and `untrusted` is a separate sequence. Passing
    retrieved content as an instruction is not a mistake to be caught in review;
    it is a call that does not type-check.
    """

    @property
    def name(self) -> str: ...

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        """Answer `instruction` about `untrusted`, shaped as `schema`."""
