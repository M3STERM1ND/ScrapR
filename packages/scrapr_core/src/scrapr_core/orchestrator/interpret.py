"""Stage 1 — Interpret the objective (`REQ-AGENT-001`).

In: the user's objective and optional context. Out: the research subject and the
questions that answering it depends on.

Three things this stage owes:

* **A multi-part objective yields questions covering each part** (`AC-1`). "As a
  company, an investment, and a place to work" is three enquiries wearing one
  sentence, and a plan that collapses them into one has already lost.
* **The subject is named** (`AC-2`), because the workspace header shows it and a
  user has to be able to see what ScrapR decided to research.
* **Ambiguity is stated, never guessed silently** (`AC-3`). Where a name could
  mean two companies, the reading is recorded and rendered with the report.

**The objective is passed as untrusted material, not as instruction.** It comes
from outside the application, and §9 admits no exceptions: the instruction says
what to do, and everything from outside is data to analyse. That is not paranoia
about the user attacking themselves — it is what keeps the rule single, and a
rule with one exception is a rule nobody can rely on. A `context_url` the user
supplies is fetched later by a tool, and its content is untrusted by anyone's
reckoning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from pydantic import BaseModel, Field, field_validator

from scrapr_core.llm.contract import LLMProvider, ModelTier, UntrustedDocument
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted

__all__ = [
    "MAX_QUESTIONS",
    "Interpretation",
    "ObjectiveInput",
    "interpret",
]

MAX_QUESTIONS = 12
"""Upper bound on questions from one objective.

Not a quality target: a bound. Question count drives the per-area effort ceiling
(`DEC-04 §4.1`), so an interpretation that emits forty questions would quietly
buy itself forty questions' worth of budget. The cap makes that impossible
without making the model's job harder.
"""

MIN_QUESTIONS = 1

INSTRUCTION = Trusted(
    "You are interpreting a research objective. Read the material and decide:\n"
    "1. the subject — the specific company, person, market or thing being asked "
    "about;\n"
    "2. the questions that must be answered to satisfy the objective.\n\n"
    "Cover every distinct part of the objective. An objective asking about a "
    "company, an investment and a workplace is three enquiries, and each needs "
    "its own questions.\n\n"
    "Write questions that can be answered from evidence: specific, separately "
    "checkable, and about the subject rather than about research method. Do not "
    "write a question you would not know how to source.\n\n"
    "If the subject is ambiguous — a name that could mean more than one entity, "
    "or a term with several readings — pick the most likely reading, say which "
    "you picked, and say what else it could have meant. Never resolve ambiguity "
    "silently."
)


@final
@dataclass(frozen=True, slots=True)
class ObjectiveInput:
    """What the user gave us (`REQ-INPUT-001..005`)."""

    objective: str
    instructions: str | None = None
    context_company: str | None = None
    context_ticker: str | None = None
    context_url: str | None = None

    def as_material(self) -> list[UntrustedDocument]:
        """Render the objective and its context as labelled material.

        Labels are written here, by the application. A user cannot title their
        own text "system instructions", because they do not get to choose the
        label.
        """
        origin = SourceRef(kind="user", locator="objective")
        material = [
            UntrustedDocument(
                content=Untrusted(self.objective, origin),
                label="research objective",
            )
        ]

        if self.instructions:
            material.append(
                UntrustedDocument(
                    content=Untrusted(self.instructions, origin),
                    label="user instructions",
                )
            )

        context = [
            (name, value)
            for name, value in (
                ("company", self.context_company),
                ("ticker", self.context_ticker),
                ("starting page", self.context_url),
            )
            if value
        ]
        if context:
            joined = "\n".join(f"{name}: {value}" for name, value in context)
            material.append(
                UntrustedDocument(
                    content=Untrusted(joined, origin),
                    label="supplied context",
                )
            )

        return material


class Interpretation(BaseModel):
    """What stage 1 produces, validated on arrival."""

    subject: str = Field(min_length=1, max_length=200)
    """The entity being researched. Shown in the workspace header (`AC-2`)."""

    interpretation_note: str | None = Field(default=None, max_length=1000)
    """How an ambiguous objective was read (`AC-3`). Null when nothing was
    ambiguous, because a note that says "this was unambiguous" is noise."""

    questions: list[str] = Field(min_length=MIN_QUESTIONS, max_length=MAX_QUESTIONS)
    """What must be answered. Each becomes a persisted row in stage 2, and the
    set of them is what termination is measured against (`DEC-04 §3`)."""

    @field_validator("questions")
    @classmethod
    def _questions_are_substantive(cls, value: list[str]) -> list[str]:
        """Reject blank and duplicate questions.

        Duplicates are not cosmetic: coverage is counted per question, so two
        copies of one question would need twice the sources to resolve the same
        ground, and would bill the area for the privilege.
        """
        cleaned = [question.strip() for question in value]
        if any(not question for question in cleaned):
            raise ValueError("a research question cannot be blank")

        seen: set[str] = set()
        unique: list[str] = []
        for question in cleaned:
            key = question.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(question)
        return unique


async def interpret(
    objective: ObjectiveInput,
    provider: LLMProvider,
    tier: ModelTier = ModelTier.STANDARD,
) -> Interpretation:
    """Turn an objective into a subject and a question set.

    `STANDARD` tier: this is the stage every later stage inherits from, so a
    cheap misreading here is the most expensive mistake in the run.
    """
    result = await provider.complete_structured(
        INSTRUCTION, objective.as_material(), Interpretation, tier
    )
    return result.value
