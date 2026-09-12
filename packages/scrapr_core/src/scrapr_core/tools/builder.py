"""Building the registry from configuration (`DEC-07 §6.1`).

Registration used to be a hardcoded list of fixtures in the worker. With five
keyed providers it becomes conditional, and five conditionals inline is where
that function stops being readable — so it moves here, next to the registry it
fills.

**A category with no key does not register in production, and that is the
designed behaviour.** `REQ-TOOL-009 AC-2` means the orchestrator asks for a
category and never learns who answered; a category nothing serves returns
`not_found`, the run reports the area as a gap, and the report names it.
Degraded and stated, never silent — which is the same path an outage takes, so
there is one behaviour to reason about rather than two.

**Outside production, an unserved category falls back to its fixture** — the
same three-outcome shape `build_provider` uses for the model, and for the same
reason. A developer without keys must still be able to run the pipeline, and
the alternative is that every local run and every end-to-end test produces a
failed version that looks like a research defect rather than an absent
credential. Production never sees a fixture: serving canned items as retrieval
would be the tool-layer equivalent of shipping the scripted provider.

**Page fetch always registers.** It needs no key (`REQ-TOOL-003`), so the one
category that is never missing is the one that reads a specific page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from scrapr_core.config import Settings
from scrapr_core.tools.contract import ToolCategory
from scrapr_core.tools.impl.adzuna import AdzunaTool
from scrapr_core.tools.impl.edgar import SecEdgarTool
from scrapr_core.tools.impl.fixture import FixtureTool, fixture_item
from scrapr_core.tools.impl.fmp import FinancialModelingPrepTool
from scrapr_core.tools.impl.page_fetch import PageFetchTool
from scrapr_core.tools.impl.tavily import news_tool, search_tool
from scrapr_core.tools.registry import ToolRegistry

__all__ = ["RegistryReport", "build_registry"]


@final
@dataclass(frozen=True, slots=True)
class RegistryReport:
    """What registered, and what did not.

    Returned rather than logged because a worker starting with three of six
    categories is something an operator must be able to see at a glance, and
    grepping a log for absences is not that.
    """

    registered: tuple[str, ...]
    missing: tuple[ToolCategory, ...]
    """Categories nothing serves. Production only — outside it these are
    stubbed instead, and a category is never in both."""

    stubbed: tuple[ToolCategory, ...] = ()
    """Categories running on a fixture because no key was configured. Always
    empty in production."""

    @property
    def summary(self) -> str:
        parts = [f"{len(self.registered)} tools registered"]
        if self.missing:
            absent = ", ".join(category.value for category in self.missing)
            parts.append(f"no provider configured for: {absent}")
        if self.stubbed:
            faked = ", ".join(category.value for category in self.stubbed)
            # Said loudly, because a local run that looks like research is
            # exactly what a stand-in must never be mistaken for.
            parts.append(f"FIXTURES (no key, not real research): {faked}")
        if not self.missing and not self.stubbed:
            parts.append("every category served")
        return "; ".join(parts)


def build_registry(settings: Settings) -> tuple[ToolRegistry, RegistryReport]:
    """Register every tool whose configuration is present, then freeze.

    Frozen before it is returned (`REQ-SEC-015 AC-1`): tool availability is
    fixed by application config at start, so retrieved content cannot add, name
    or reach a tool later in the run.
    """
    registry = ToolRegistry()
    registered: list[str] = []

    def add(tool: object) -> None:
        registry.register(tool)  # type: ignore[arg-type]
        registered.append(getattr(tool, "name", "unnamed"))

    # No key. `REQ-TOOL-003` was buildable while every other question was open,
    # and it stays available when every key is absent.
    add(PageFetchTool())

    if settings.tavily_api_key.strip():
        # One vendor, two categories (`DEC-07 §3.1`). Separate registrations,
        # so replacing either leaves the other alone.
        add(search_tool(settings.tavily_api_key))
        add(news_tool(settings.tavily_api_key))

    if settings.fmp_api_key.strip():
        add(FinancialModelingPrepTool(api_key=settings.fmp_api_key))

    if settings.sec_edgar_user_agent.strip():
        # EDGAR has no key and instead refuses requests without a contactable
        # User-Agent, so that string is what gates this category.
        add(SecEdgarTool(user_agent=settings.sec_edgar_user_agent))

    if settings.adzuna_app_id.strip() and settings.adzuna_app_key.strip():
        add(
            AdzunaTool(
                app_id=settings.adzuna_app_id, app_key=settings.adzuna_app_key
            )
        )

    served = set(registry.categories())
    unserved = tuple(
        category
        for category in ToolCategory
        # Documents are Phase 4 and have no provider by design, so listing them
        # as missing would report a gap that is not one.
        if category is not ToolCategory.DOCUMENTS and category not in served
    )

    stubbed: tuple[ToolCategory, ...] = ()
    if settings.scrapr_env != "production":
        stubbed = unserved
        for category in unserved:
            add(_fixture_for(category))

    registry.freeze()

    return registry, RegistryReport(
        registered=tuple(registered),
        missing=() if stubbed else unserved,
        stubbed=stubbed,
    )


def _fixture_for(category: ToolCategory) -> FixtureTool:
    """A stand-in for one unserved category, local only.

    Two distinct sources, because `MIN_SOURCES_PER_QUESTION` is two: a
    one-source fixture would leave every question open and make a local run
    look like a research failure rather than a missing credential — which is
    the exact confusion this fallback exists to prevent.
    """
    host = f"{category.value.replace('_', '-')}.fixture.example"
    return FixtureTool(
        name=f"fixture_{category.value}",
        category=category,
        items=tuple(
            fixture_item(
                source_name=f"{host} result {index + 1}",
                text=body,
                source_url=f"https://{host}/{index + 1}",
            )
            for index, body in enumerate(
                (
                    "The company reported $1.2bn revenue for FY2025, up 18% "
                    "year over year.",
                    "Hiring continued through the fourth quarter, with 40 open "
                    "engineering roles listed.",
                )
            )
        ),
    )
