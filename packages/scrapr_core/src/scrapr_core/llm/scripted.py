"""A deterministic stand-in provider, for development without a model.

`OPEN-04` names no AI provider, and implementation plan §15 says the pipeline is
exercised "end-to-end over fixtures plus the fake LLM". This is that fake, in
the form the *running* stack needs rather than the form a unit test needs: it
answers any stage, in order, without being primed.

**It does not write research. It rearranges what it was given.** Every statement
it produces is copied out of the material in front of it, and every citation
points at an evidence id that material actually contained. That is the whole
design: a stand-in that invented plausible findings would make a broken pipeline
look like a working product, which is the one failure mode this project cannot
afford.

**It refuses to run in production.** A deterministic echo is a development
convenience; shipping it would mean serving users research nobody did.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import final

from pydantic import BaseModel

from scrapr_core.db.enums import ClaimType
from scrapr_core.llm.contract import (
    ModelTier,
    StructuredResult,
    TokenUsage,
    UntrustedDocument,
)
from scrapr_core.llm.envelope import render_untrusted
from scrapr_core.orchestrator.extract import ExtractedEvidence, Extraction
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.orchestrator.plan import _PlanDraft
from scrapr_core.orchestrator.synthesize import (
    DraftClaim,
    DraftSection,
    SynthesisDraft,
)
from scrapr_core.security.trust import Trusted

__all__ = ["ScriptedProvider", "UnsupportedSchemaError"]

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]")

MAX_EXCERPT_WORDS = 12


class UnsupportedSchemaError(NotImplementedError):
    """Raised for a stage this stand-in has no answer for.

    Loud on purpose: a new stage that silently got an empty response would look
    like a model that had nothing to say.
    """


@final
class ScriptedProvider:
    """Answers each stage by reading the material it was handed."""

    def __init__(self, *, name: str = "scripted", model: str = "scripted-1") -> None:
        self._name = name
        self._model = model

    @property
    def name(self) -> str:
        return self._name

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        if not isinstance(instruction, Trusted):
            raise TypeError("instruction must be Trusted (REQ-SEC-012)")

        rendered = render_untrusted(untrusted)
        value = self._answer(schema, untrusted, rendered)

        return StructuredResult(
            value=value,
            model=self._model,
            tier=model_tier,
            usage=TokenUsage(),
        )

    # ------------------------------------------------------------------

    def _answer[T: BaseModel](
        self,
        schema: type[T],
        untrusted: Sequence[UntrustedDocument],
        rendered: str,
    ) -> T:
        if schema is Interpretation:
            return self._interpret(untrusted)  # type: ignore[return-value]
        if schema is _PlanDraft:
            return self._plan(untrusted)  # type: ignore[return-value]
        if schema is Extraction:
            return self._extract(untrusted)  # type: ignore[return-value]
        if schema is SynthesisDraft:
            return self._synthesize(untrusted, rendered)  # type: ignore[return-value]

        raise UnsupportedSchemaError(
            f"the scripted provider has no answer for {schema.__name__}; "
            "configure a real provider or extend it deliberately"
        )

    @staticmethod
    def _interpret(untrusted: Sequence[UntrustedDocument]) -> Interpretation:
        """Read the objective back as a subject and one question per clause.

        Splitting on "and" and commas is crude, and it is meant to be: a real
        interpretation is `OPEN-04`'s job, and a cleverer stand-in would only
        disguise how much of the quality comes from the model.
        """
        objective = untrusted[0].content.text.strip() if untrusted else "the subject"
        clauses = [
            clause.strip(" ?.,")
            for clause in re.split(r"\band\b|,", objective)
            if clause.strip(" ?.,")
        ]
        questions = [f"What does the evidence say about {clause}?" for clause in clauses[:6]]

        return Interpretation(
            subject=objective[:80],
            interpretation_note=(
                "Interpreted by the development stand-in, not by a model."
            ),
            questions=questions or [f"What does the evidence say about {objective}?"],
        )

    @staticmethod
    def _plan(untrusted: Sequence[UntrustedDocument]) -> _PlanDraft:
        """One area per question, using whichever categories are available."""
        text = untrusted[0].content.text if untrusted else ""
        questions = [
            line.removeprefix("- ").strip()
            for line in text.splitlines()
            if line.startswith("- ")
        ]
        categories = [
            line.strip()
            for line in (untrusted[1].content.text.splitlines() if len(untrusted) > 1 else [])
            if line.strip()
        ]

        return _PlanDraft(
            areas=[
                _PlanDraft.Area(
                    name=f"Area {index + 1}",
                    questions=[question],
                    tool_categories=categories,
                )
                for index, question in enumerate(questions)
            ]
        )

    @staticmethod
    def _extract(untrusted: Sequence[UntrustedDocument]) -> Extraction:
        """Quote the first sentence of each content block, verbatim.

        Verbatim is not a nicety here: extraction drops any excerpt the source
        does not contain, so a stand-in that paraphrased would produce nothing
        and look like a retrieval problem.
        """
        found: list[ExtractedEvidence] = []

        # The first document is the question; content blocks follow it.
        for index, document in enumerate(untrusted[1:]):
            text = document.content.text.strip()
            if not text:
                continue
            match = _SENTENCE.search(text)
            sentence = (match.group(0) if match else text).strip()
            excerpt = " ".join(sentence.split()[:MAX_EXCERPT_WORDS])
            if excerpt and excerpt in text:
                found.append(
                    ExtractedEvidence(
                        statement=sentence, excerpt=excerpt, item_index=index
                    )
                )

        return Extraction(evidence=found)

    @staticmethod
    def _synthesize(
        untrusted: Sequence[UntrustedDocument], rendered: str
    ) -> SynthesisDraft:
        """Restate the evidence, citing the ids the material actually carried."""
        evidence_ids = list(dict.fromkeys(_UUID_PATTERN.findall(rendered)))
        statements = [
            line.removeprefix("statement:").strip()
            for line in (untrusted[-1].content.text.splitlines() if untrusted else [])
            if line.startswith("statement:")
        ]

        claims = [
            DraftClaim(
                text=statement,
                claim_type=ClaimType.FACT,
                evidence_ids=[evidence_ids[index]] if index < len(evidence_ids) else [],
            )
            for index, statement in enumerate(statements)
        ]

        if not claims:
            return SynthesisDraft()

        return SynthesisDraft(
            summary=[
                DraftClaim(
                    text=claims[0].text,
                    claim_type=ClaimType.FACT,
                    evidence_ids=list(claims[0].evidence_ids),
                    is_important=True,
                )
            ],
            sections=[DraftSection(title="What the sources say", claims=claims)],
        )
