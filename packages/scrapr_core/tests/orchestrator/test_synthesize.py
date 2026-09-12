"""Stage 10: the rules synthesis imposes on whatever the model wrote.

A model asked to write a report will write one. The question is what happens to
the claims it cannot support, and the answer has to be the same every time:
a fact without evidence is not a fact, a forecast without assumptions is not
published, an unanswered question is named as a gap, and nothing is quietly
upgraded to fill space.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import ClaimType
from scrapr_core.domain.ids import new_id
from scrapr_core.llm import FakeLLMProvider, ModelTier
from scrapr_core.orchestrator.synthesize import (
    INSTRUCTION,
    SUMMARY_TITLE,
    Claim,
    DraftClaim,
    DraftSection,
    Section,
    SynthesisDraft,
    SynthesisInput,
    SynthesisResult,
    synthesize,
    with_area_gaps,
)

OBJECTIVE = "How is Acme positioned against its competitors?"
EVIDENCE_ID = new_id()

EVIDENCE = [
    SynthesisInput(
        evidence_id=EVIDENCE_ID,
        statement="Acme reported $1.2bn revenue in FY2025.",
        excerpt="revenue of $1.2bn",
        source_name="Form 10-K",
        question="What is revenue?",
    )
]


def claim(
    text: str = "Acme reported $1.2bn revenue in FY2025.",
    claim_type: ClaimType = ClaimType.FACT,
    evidence: list[str] | None = None,
    assumptions: list[str] | None = None,
    important: bool = False,
) -> DraftClaim:
    return DraftClaim(
        text=text,
        claim_type=claim_type,
        evidence_ids=[str(EVIDENCE_ID)] if evidence is None else evidence,
        assumptions=assumptions or [],
        is_important=important,
    )


async def run(
    draft: SynthesisDraft,
    evidence: list[SynthesisInput] | None = None,
    unresolved: list[str] | None = None,
) -> SynthesisResult:
    provider = FakeLLMProvider()
    provider.enqueue(draft)
    return await synthesize(
        EVIDENCE if evidence is None else evidence,
        unresolved or [],
        OBJECTIVE,
        provider,
    )


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


async def test_a_report_opens_with_the_executive_summary() -> None:
    """`REQ-SYNTH-003`, and `REQ-SYNTH-005 AC-1`: ordering is explicit, so the
    summary stays first across a reload and an export."""
    result = await run(
        SynthesisDraft(
            summary=[claim("Acme grew 18% in FY2025.")],
            sections=[DraftSection(title="Revenue", claims=[claim()])],
        )
    )

    assert result.sections[0].title == SUMMARY_TITLE
    assert result.sections[0].is_executive_summary
    assert [section.ordering for section in result.sections] == [0, 1]


async def test_a_section_with_no_surviving_claims_is_not_emitted() -> None:
    """`REQ-SYNTH-004 AC-3`. A heading with nothing under it implies coverage
    that does not exist."""
    result = await run(
        SynthesisDraft(
            summary=[claim()],
            sections=[
                DraftSection(
                    title="Speculation",
                    claims=[claim(claim_type=ClaimType.FORECAST, assumptions=[])],
                )
            ],
        )
    )

    assert [section.title for section in result.sections] == [SUMMARY_TITLE]
    assert any("empty section" in reason for reason in result.dropped)


async def test_sections_keep_the_order_the_model_chose() -> None:
    result = await run(
        SynthesisDraft(
            summary=[claim()],
            sections=[
                DraftSection(title="Revenue", claims=[claim()]),
                DraftSection(title="Competition", claims=[claim()]),
            ],
        )
    )

    assert [s.title for s in result.sections] == [SUMMARY_TITLE, "Revenue", "Competition"]
    assert [s.ordering for s in result.sections] == [0, 1, 2]


# --------------------------------------------------------------------------
# Claim typing
# --------------------------------------------------------------------------


async def test_an_evidenced_fact_stays_a_fact() -> None:
    result = await run(SynthesisDraft(summary=[claim()]))

    kept = result.sections[0].claims[0]
    assert kept.claim_type is ClaimType.FACT
    assert kept.evidence_ids == (EVIDENCE_ID,)


async def test_a_fact_citing_nothing_is_not_a_fact() -> None:
    """`REQ-EVID-017`. It may still be a reasonable reading, so it is re-typed
    rather than thrown away — but it can never be presented as evidenced."""
    result = await run(SynthesisDraft(summary=[claim(evidence=[])]))

    assert result.sections[0].claims[0].claim_type is not ClaimType.FACT


async def test_a_fact_citing_an_unknown_id_is_not_a_fact() -> None:
    """A citation the reader cannot follow is not a citation."""
    result = await run(SynthesisDraft(summary=[claim(evidence=[str(new_id())])]))

    kept = result.sections[0].claims[0]
    assert kept.evidence_ids == ()
    assert kept.claim_type is not ClaimType.FACT


async def test_analysis_with_nothing_read_becomes_an_uncertainty() -> None:
    """`REQ-SYNTH-010 AC-2`. Analysis is a reading of evidence; with nothing
    read it is speculation, and speculation must not wear that label."""
    result = await run(
        SynthesisDraft(summary=[claim(claim_type=ClaimType.ANALYSIS, evidence=[])])
    )

    assert result.sections[0].claims[0].claim_type is ClaimType.UNCERTAINTY


async def test_analysis_over_evidence_stays_analysis() -> None:
    result = await run(
        SynthesisDraft(summary=[claim(claim_type=ClaimType.ANALYSIS)])
    )

    assert result.sections[0].claims[0].claim_type is ClaimType.ANALYSIS


async def test_a_forecast_without_assumptions_is_dropped() -> None:
    """`REQ-SYNTH-009 AC-1`: not emitted. Not emitted with an empty list, and
    not quietly re-typed into something that looks evidenced."""
    result = await run(
        SynthesisDraft(
            summary=[claim(), claim(claim_type=ClaimType.FORECAST, assumptions=[])]
        )
    )

    types = [c.claim_type for c in result.sections[0].claims]
    assert ClaimType.FORECAST not in types
    assert any("forecast without assumptions" in reason for reason in result.dropped)


async def test_a_forecast_with_assumptions_keeps_them() -> None:
    """`REQ-SYNTH-009 AC-2`: adjacent to the forecast, not in a footnote."""
    result = await run(
        SynthesisDraft(
            summary=[
                claim(
                    claim_type=ClaimType.FORECAST,
                    assumptions=["supply constraints ease", "  ", "no export change"],
                )
            ]
        )
    )

    kept = result.sections[0].claims[0]
    assert kept.claim_type is ClaimType.FORECAST
    assert kept.assumptions == ("supply constraints ease", "no export change")


async def test_duplicate_citations_are_kept_once() -> None:
    result = await run(
        SynthesisDraft(summary=[claim(evidence=[str(EVIDENCE_ID), str(EVIDENCE_ID)])])
    )

    assert result.sections[0].claims[0].evidence_ids == (EVIDENCE_ID,)


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------


async def test_an_unresolved_question_becomes_a_named_gap() -> None:
    """`REQ-SYNTH-010 AC-1`, `DEC-04 §6.3`. Written from the question, so the
    sentence stating what could not be established is not itself an opportunity
    to speculate about the answer."""
    result = await run(
        SynthesisDraft(summary=[claim()]),
        unresolved=["What is FY2025 segment revenue?"],
    )

    gaps = [
        c for c in result.sections[0].claims if c.claim_type is ClaimType.UNCERTAINTY
    ]
    assert len(gaps) == 1
    assert "FY2025 segment revenue" in gaps[0].text
    assert gaps[0].is_important


async def test_no_evidence_at_all_produces_gaps_not_a_guess() -> None:
    """The honest output of a run that found nothing is a report that is
    entirely gaps, not a model's best guess at the answer."""
    result = await run(
        SynthesisDraft(summary=[claim()]),
        evidence=[],
        unresolved=["What is revenue?", "What is margin?"],
    )

    assert len(result.sections) == 1
    assert all(
        c.claim_type is ClaimType.UNCERTAINTY for c in result.sections[0].claims
    )
    assert len(result.sections[0].claims) == 2


async def test_nothing_at_all_produces_nothing() -> None:
    """No evidence and no questions is not a report; it is a failed run, and
    stage 1.8 is what reports it as one."""
    result = await run(SynthesisDraft(), evidence=[], unresolved=[])

    assert result.sections == ()


async def test_no_model_call_happens_when_there_is_no_evidence() -> None:
    """Paying for a synthesis over nothing would buy exactly the speculation
    `REQ-SYNTH-010 AC-2` forbids."""
    provider = FakeLLMProvider()

    await synthesize([], ["What is revenue?"], OBJECTIVE, provider)

    assert provider.calls == ()


# --------------------------------------------------------------------------
# The trust boundary
# --------------------------------------------------------------------------


async def test_evidence_and_objective_travel_as_material() -> None:
    provider = FakeLLMProvider()
    provider.enqueue(SynthesisDraft(summary=[claim()]))

    await synthesize(EVIDENCE, [], OBJECTIVE, provider)

    call = provider.calls[0]
    assert call.instruction == INSTRUCTION
    assert OBJECTIVE not in call.instruction
    assert [d.label for d in call.documents] == ["research objective", "evidence"]
    assert str(EVIDENCE_ID) in call.rendered


@pytest.mark.parametrize("tier", [ModelTier.STANDARD])
async def test_synthesis_runs_at_standard_tier(tier: ModelTier) -> None:
    provider = FakeLLMProvider()
    provider.enqueue(SynthesisDraft(summary=[claim()]))

    await synthesize(EVIDENCE, [], OBJECTIVE, provider)

    assert provider.calls[0].tier is tier


# --------------------------------------------------------------------------
# Area-level gaps in the report (`REQ-AGENT-009 AC-2`)
# --------------------------------------------------------------------------


def test_area_gaps_join_the_executive_summary() -> None:
    """A reader finds out what they are about to read in the summary. Learning
    halfway down that a third of the subject was never covered is too late."""
    sections = (
        Section(
            title=SUMMARY_TITLE,
            ordering=0,
            claims=(Claim(text="Revenue grew.", claim_type=ClaimType.FACT),),
            is_executive_summary=True,
        ),
        Section(
            title="Revenue",
            ordering=1,
            claims=(Claim(text="Revenue grew.", claim_type=ClaimType.FACT),),
        ),
    )

    result = with_area_gaps(sections, ["Hiring could not be researched."])

    assert len(result) == 2
    summary_claims = result[0].claims
    assert summary_claims[-1].text == "Hiring could not be researched."
    assert summary_claims[-1].claim_type is ClaimType.UNCERTAINTY
    assert summary_claims[-1].is_important


def test_no_gaps_leaves_the_report_untouched() -> None:
    sections = (
        Section(
            title=SUMMARY_TITLE,
            ordering=0,
            claims=(Claim(text="Revenue grew.", claim_type=ClaimType.FACT),),
            is_executive_summary=True,
        ),
    )

    assert with_area_gaps(sections, []) == sections


def test_gaps_become_the_report_when_there_is_nothing_else() -> None:
    """A run that produced nothing still owes the reader the reason."""
    result = with_area_gaps((), ["Hiring could not be researched."])

    assert len(result) == 1
    assert result[0].is_executive_summary
    assert result[0].title == "What could not be researched"
    assert result[0].claims[0].text == "Hiring could not be researched."


def test_gaps_are_prepended_when_a_report_has_no_summary() -> None:
    """Ordering stays contiguous, because `REQ-SYNTH-005` makes it the report's
    structure rather than a display hint."""
    sections = (
        Section(
            title="Revenue",
            ordering=0,
            claims=(Claim(text="Revenue grew.", claim_type=ClaimType.FACT),),
        ),
    )

    result = with_area_gaps(sections, ["Hiring could not be researched."])

    assert [section.ordering for section in result] == [0, 1]
    assert result[0].is_executive_summary
    assert result[1].title == "Revenue"


# --------------------------------------------------------------------------
# Sections come from the objective and the evidence (`REQ-AGENT-006`)
# --------------------------------------------------------------------------


async def test_the_report_has_no_static_section_template() -> None:
    """`AC-3`: section selection is derived, not stamped out.

    Two different drafts over the same evidence produce two different section
    sets, because nothing in this stage carries a list of sections a report is
    supposed to have. A hiring report with an empty stock section (`AC-1`) is
    impossible for the same reason: there is no section it did not ask for.
    """
    hiring = await run(
        SynthesisDraft(
            summary=[claim()],
            sections=[DraftSection(title="Hiring and headcount", claims=[claim()])],
        )
    )
    investment = await run(
        SynthesisDraft(
            summary=[claim()],
            sections=[DraftSection(title="Valuation", claims=[claim()])],
        )
    )

    assert [s.title for s in hiring.sections] == [SUMMARY_TITLE, "Hiring and headcount"]
    assert [s.title for s in investment.sections] == [SUMMARY_TITLE, "Valuation"]


async def test_a_section_the_evidence_does_not_support_never_appears() -> None:
    """`AC-1`, `AC-2`: no padded section, because a section whose claims all
    fail the rules is dropped rather than shipped empty."""
    result = await run(
        SynthesisDraft(
            summary=[claim()],
            sections=[
                DraftSection(title="Stock information", claims=[]),
                DraftSection(title="Revenue", claims=[claim()]),
            ],
        )
    )

    assert "Stock information" not in [s.title for s in result.sections]
    assert "Revenue" in [s.title for s in result.sections]
