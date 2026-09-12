"""The development stand-in provider.

It exists so the local stack runs without a model, which means when it breaks
the symptom is an empty report rather than an error — the worst kind of failure
to go untested. Two behaviours carry that risk:

* **Excerpts must be verbatim.** Extraction drops any excerpt its source does
  not contain, so a stand-in that paraphrases produces nothing and looks like a
  retrieval problem.
* **Citations must be real ids.** Synthesis drops citations it cannot resolve
  and re-types the claim, so an invented id turns every fact into an
  uncertainty and looks like a research problem.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from scrapr_core.db.enums import ClaimType
from scrapr_core.llm.contract import ModelTier, UntrustedDocument
from scrapr_core.llm.scripted import ScriptedProvider, UnsupportedSchemaError
from scrapr_core.orchestrator.extract import Extraction, _ground
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.orchestrator.plan import _PlanDraft
from scrapr_core.orchestrator.synthesize import SynthesisDraft
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted

INSTRUCTION = Trusted("do the thing")
PAGE = "Acme reported revenue of $1.2bn for fiscal 2025. Margin held at 75%."


def material(*pairs: tuple[str, str]) -> list[UntrustedDocument]:
    return [
        UntrustedDocument(
            content=Untrusted(text, SourceRef("fixture", label)), label=label
        )
        for label, text in pairs
    ]


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


# --------------------------------------------------------------------------
# Interpretation and planning
# --------------------------------------------------------------------------


async def test_an_objective_becomes_a_subject_and_questions(
    provider: ScriptedProvider,
) -> None:
    result = await provider.complete_structured(
        INSTRUCTION,
        material(("research objective", "Analyse Acme as a company and as an employer")),
        Interpretation,
    )

    assert result.value.subject
    assert len(result.value.questions) >= 2


async def test_the_interpretation_says_it_came_from_a_stand_in(
    provider: ScriptedProvider,
) -> None:
    """The note reaches the workspace header, so a developer looking at a local
    report can tell at a glance that no model was involved."""
    result = await provider.complete_structured(
        INSTRUCTION, material(("research objective", "Analyse Acme")), Interpretation
    )

    assert "stand-in" in (result.value.interpretation_note or "")


async def test_planning_assigns_every_question_to_an_area(
    provider: ScriptedProvider,
) -> None:
    interpretation_text = "Subject: Acme\n\nQuestions:\n- What is revenue?\n- What is margin?"
    result = await provider.complete_structured(
        INSTRUCTION,
        material(
            ("interpretation", interpretation_text),
            ("available tool categories", "web_search\nnews"),
        ),
        _PlanDraft,
    )

    planned = [question for area in result.value.areas for question in area.questions]
    assert planned == ["What is revenue?", "What is margin?"]
    assert all(area.tool_categories == ["web_search", "news"] for area in result.value.areas)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


async def test_extracted_excerpts_are_verbatim(provider: ScriptedProvider) -> None:
    """The behaviour the whole stand-in turns on: a paraphrase would be dropped
    by the grounding check and the local stack would produce empty reports."""
    documents = material(("question to answer", "What was revenue?"), ("block 0", PAGE))

    result = await provider.complete_structured(INSTRUCTION, documents, Extraction)

    assert result.value.evidence
    for found in result.value.evidence:
        assert found.excerpt in PAGE


async def test_extractions_survive_the_grounding_check(
    provider: ScriptedProvider,
) -> None:
    """Checked against the real function rather than by eye, because the whole
    point is that extraction will accept what this produces."""
    import datetime as dt

    from scrapr_core.db.enums import Accessibility, SourceCategory
    from scrapr_core.tools.contract import ToolItem

    item = ToolItem(
        source_name="Acme IR",
        source_category=SourceCategory.WEB,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=Accessibility.ACCESSIBLE,
        content=Untrusted(PAGE, SourceRef("web", "https://acme.example")),
        source_url="https://acme.example",
    )
    documents = material(("question to answer", "What was revenue?"), ("block 0", PAGE))
    extraction = (
        await provider.complete_structured(INSTRUCTION, documents, Extraction)
    ).value

    grounded = _ground(extraction, [item])

    assert len(grounded) == 1


async def test_an_empty_block_yields_no_evidence(provider: ScriptedProvider) -> None:
    documents = material(("question to answer", "What was revenue?"), ("block 0", "   "))

    result = await provider.complete_structured(INSTRUCTION, documents, Extraction)

    assert result.value.evidence == []


# --------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------


async def test_synthesis_cites_ids_the_material_actually_carried(
    provider: ScriptedProvider,
) -> None:
    """An invented id is dropped downstream and the claim is re-typed, so a
    stand-in that guesses would turn every fact into an uncertainty."""
    evidence_id = "01a08e93-514b-7495-82df-6ddcdf511846"
    evidence_block = (
        f"evidence id: {evidence_id}\n"
        "question: What is revenue?\n"
        "source: Acme IR\n"
        "statement: Acme reported $1.2bn revenue.\n"
        'excerpt: "revenue of $1.2bn"'
    )

    result = await provider.complete_structured(
        INSTRUCTION,
        material(("research objective", "Analyse Acme"), ("evidence", evidence_block)),
        SynthesisDraft,
    )

    claims = [claim for section in result.value.sections for claim in section.claims]
    assert claims
    assert claims[0].evidence_ids == [evidence_id]
    assert claims[0].claim_type is ClaimType.FACT


async def test_synthesis_opens_with_a_summary(provider: ScriptedProvider) -> None:
    evidence_block = (
        "evidence id: 01a08e93-514b-7495-82df-6ddcdf511846\n"
        "statement: Acme reported $1.2bn revenue."
    )

    result = await provider.complete_structured(
        INSTRUCTION,
        material(("research objective", "Analyse Acme"), ("evidence", evidence_block)),
        SynthesisDraft,
    )

    assert result.value.summary


async def test_no_evidence_produces_no_report(provider: ScriptedProvider) -> None:
    """Nothing to rearrange means nothing to say, which is the honest answer."""
    result = await provider.complete_structured(
        INSTRUCTION,
        material(("research objective", "Analyse Acme"), ("evidence", "")),
        SynthesisDraft,
    )

    assert result.value.sections == []
    assert result.value.summary == []


# --------------------------------------------------------------------------
# The contract it still has to keep
# --------------------------------------------------------------------------


async def test_untrusted_content_cannot_be_the_instruction(
    provider: ScriptedProvider,
) -> None:
    """`REQ-SEC-012` holds for the stand-in too. A development provider that
    relaxed the boundary would let a test pass that production would fail."""
    with pytest.raises(TypeError, match="must be Trusted"):
        await provider.complete_structured(
            "a bare string",  # type: ignore[arg-type]
            material(("research objective", "Analyse Acme")),
            Interpretation,
        )


async def test_an_unknown_schema_is_refused_loudly(
    provider: ScriptedProvider,
) -> None:
    """A new stage that silently got an empty response would look like a model
    with nothing to say."""

    class SomethingNew(BaseModel):
        value: str = "x"

    with pytest.raises(UnsupportedSchemaError, match="SomethingNew"):
        await provider.complete_structured(INSTRUCTION, [], SomethingNew)


async def test_the_tier_is_reported_back(provider: ScriptedProvider) -> None:
    result = await provider.complete_structured(
        INSTRUCTION,
        material(("research objective", "Analyse Acme")),
        Interpretation,
        ModelTier.DEEP,
    )

    assert result.tier is ModelTier.DEEP
    assert provider.name == "scripted"
