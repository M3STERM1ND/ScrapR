"""Every research domain the product promises is reachable (`REQ-AGENT-007`).

`AC-1` requires each listed domain to be reachable by at least one tool in the
tool layer, and `AC-3` that no listed domain is *structurally* impossible to
research. Those are claims about the architecture, not about providers — which
is why they can be checked now, with `OPEN-05..09` still open.

The mapping below is the claim made explicit. It fails if a category is removed
or renamed, which is exactly when a domain would quietly become unreachable: the
orchestrator asks for a category, so a domain with no category behind it is a
domain nothing can be planned against.

What this does **not** assert is that a provider exists. That is `1.9`, one task
per tool, each waiting on its own open question. A domain with a category and no
provider yields a `not_found` failure the run reports as a gap — unresearched,
but never silently missing.
"""

from __future__ import annotations

import pytest

from scrapr_core.tools import ToolCategory

# The domains `REQ-AGENT-007` names, each against the category that would serve
# it. Several share a category, which is correct: the category is the retrieval
# mechanism, not the subject.
DOMAIN_COVERAGE: dict[str, tuple[ToolCategory, ...]] = {
    "company and business overview": (ToolCategory.WEB_SEARCH, ToolCategory.PAGE_FETCH),
    "products and services": (ToolCategory.WEB_SEARCH, ToolCategory.PAGE_FETCH),
    "financials": (ToolCategory.FINANCIAL, ToolCategory.FILINGS),
    "stock information": (ToolCategory.FINANCIAL,),
    "marketing strategy": (ToolCategory.WEB_SEARCH, ToolCategory.NEWS),
    "competitors": (ToolCategory.WEB_SEARCH, ToolCategory.FINANCIAL),
    "market position and share": (ToolCategory.WEB_SEARCH, ToolCategory.FILINGS),
    "industry trends": (ToolCategory.NEWS, ToolCategory.WEB_SEARCH),
    "recent news": (ToolCategory.NEWS,),
    "customer and consumer sentiment": (ToolCategory.WEB_SEARCH, ToolCategory.NEWS),
    "growth opportunities": (ToolCategory.FILINGS, ToolCategory.WEB_SEARCH),
    "risks": (ToolCategory.FILINGS, ToolCategory.NEWS),
    "SWOT": (ToolCategory.WEB_SEARCH, ToolCategory.FILINGS),
    "career opportunities": (ToolCategory.JOBS,),
    "job roles": (ToolCategory.JOBS,),
    "salaries": (ToolCategory.JOBS, ToolCategory.WEB_SEARCH),
    "required skills and qualifications": (ToolCategory.JOBS,),
    "user-provided documents": (ToolCategory.DOCUMENTS,),
}


@pytest.mark.parametrize("domain", sorted(DOMAIN_COVERAGE))
def test_every_named_domain_has_a_category(domain: str) -> None:
    """`AC-1`, `AC-3`: a domain with no category is one nothing can plan for."""
    categories = DOMAIN_COVERAGE[domain]

    assert categories, f"{domain} is reachable by nothing"
    assert all(isinstance(category, ToolCategory) for category in categories)


def test_every_category_serves_something() -> None:
    """The other direction. A category nothing maps to is either dead weight or
    a domain somebody forgot to write down."""
    used = {category for categories in DOMAIN_COVERAGE.values() for category in categories}

    assert used == set(ToolCategory), (
        f"categories serving no listed domain: {set(ToolCategory) - used}"
    )


def test_salary_information_depends_on_a_source_bearing_category() -> None:
    """`AC-2`: salary figures are included only where the source meets the
    reliability bar, which means they must come from a real source like any
    other fact rather than from the model's memory."""
    assert ToolCategory.JOBS in DOMAIN_COVERAGE["salaries"]
