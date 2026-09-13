"""Building the registry from configuration (`DEC-07 §6.1`).

Registration used to be a hardcoded list of fixtures in the worker. With five
keyed providers it becomes conditional, and five conditionals inline is where
that function stops being readable — so it moves here, next to the registry it
fills.

**A category with no key does not register, and that is the designed
behaviour.** `REQ-TOOL-009 AC-2` means the orchestrator asks for a category and
never learns who answered; a category nothing serves returns `not_found`, the
run reports the area as a gap, and the report names it. Degraded and stated,
never silent — which is the same path an outage takes, so there is one
behaviour to reason about rather than two.

**Fixtures are an explicit opt-in, and never reach real research.** They used
to fill every unserved category whenever the environment was not production.
The first real run showed the cost: a real model read a canned "$1.2bn revenue"
item on `reuters.com` and the report cited it as a Reuters filing about NVIDIA.
Now a fixture registers only when `Settings.fixtures_permitted` — the
`SCRAPR_ALLOW_FIXTURES` flag, outside production, with no real model configured.
And a fixture never borrows a real publisher's name: it lives on the reserved
`.invalid` host, tiers `LOWER`, and says what it is in its source name.

**Page fetch always registers.** It needs no key (`REQ-TOOL-003`), so the one
category that is never missing is the one that reads a specific page.

**Documents register whenever there is a database to read**, for the same
reason: the store is our own Postgres, so there is no credential to be missing
and no fixture that would mean anything. A session with no uploads simply has
nothing to find, which is a legitimately empty result rather than an unserved
category — and the orchestrator asks the category only for sessions that have
ready documents, so an empty index is never queried at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.config import Settings
from scrapr_core.tools.contract import ToolCategory
from scrapr_core.tools.impl.adzuna import AdzunaTool
from scrapr_core.tools.impl.documents import DocumentTool
from scrapr_core.tools.impl.edgar import SecEdgarTool
from scrapr_core.tools.impl.fixture import FixtureTool, fixture_item
from scrapr_core.tools.impl.fmp import FinancialModelingPrepTool
from scrapr_core.tools.impl.page_fetch import PageFetchTool
from scrapr_core.tools.impl.tavily import news_tool, search_tool
from scrapr_core.tools.registry import ToolRegistry

__all__ = ["FIXTURE_HOST", "RegistryReport", "build_registry"]


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
    """Categories nothing serves. When fixtures are permitted these are
    stubbed instead, and a category is never in both."""

    stubbed: tuple[ToolCategory, ...] = ()
    """Categories running on a fixture because no key was configured. Empty
    unless `Settings.fixtures_permitted`."""

    fixtures_refused: bool = False
    """`SCRAPR_ALLOW_FIXTURES` was set but ignored — production, or a real model
    is configured. Reported so the flag cannot look like it did something."""

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
        if self.fixtures_refused:
            parts.append(
                "SCRAPR_ALLOW_FIXTURES ignored: fixtures never run beside a real "
                "model or in production"
            )
        if not self.missing and not self.stubbed:
            parts.append("every category served")
        return "; ".join(parts)


def build_registry(
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
) -> tuple[ToolRegistry, RegistryReport]:
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

    # `REQ-TOOL-008`. Optional only because tests build a registry without a
    # database; the worker always passes one, and a worker that did not would
    # read a user's uploads into a run that never mentions them.
    if session_factory is not None:
        add(DocumentTool(session_factory=session_factory))

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
        # Documents are served by our own database or not at all. A fixture
        # standing in for them would invent passages from files the user never
        # uploaded, which is worse than the gap it papers over.
        if category is not ToolCategory.DOCUMENTS and category not in served
    )

    stubbed: tuple[ToolCategory, ...] = ()
    if settings.fixtures_permitted:
        stubbed = unserved
        for category in unserved:
            add(_fixture_for(category))

    registry.freeze()

    return registry, RegistryReport(
        registered=tuple(registered),
        missing=() if stubbed else unserved,
        stubbed=stubbed,
        fixtures_refused=settings.allow_fixtures and not settings.fixtures_permitted,
    )


FIXTURE_HOST = "fixture.invalid"
"""Where fixture items claim to come from.

`.invalid` is reserved (RFC 2606) and can never resolve, so no real publisher's
name is borrowed and no reader can follow a citation to a page that looks
real. Unlisted, so `DEC-08` tiers it `LOWER` — a fixture is never authority.
"""


def _fixture_for(category: ToolCategory) -> FixtureTool:
    """A stand-in for one unserved category, for keyless local plumbing only.

    Every piece of it says what it is: the host, the source name and the text.
    A fixture that reads like research is how canned figures became a report's
    headline numbers.
    """
    return FixtureTool(
        name=f"fixture_{category.value}",
        category=category,
        items=tuple(
            fixture_item(
                source_name=f"FIXTURE {category.value} placeholder {index + 1} (not real research)",
                text=body,
                source_url=f"https://{FIXTURE_HOST}/{category.value}/{index + 1}",
            )
            for index, body in enumerate(
                (
                    "FIXTURE PLACEHOLDER: a fictional example company reported "
                    "$1.2bn revenue for FY2025. This is not real data.",
                    "FIXTURE PLACEHOLDER: a fictional example company listed 40 "
                    "open engineering roles. This is not real data.",
                )
            )
        ),
    )
