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

    @property
    def summary(self) -> str:
        if not self.missing:
            return f"{len(self.registered)} tools registered; every category served"
        absent = ", ".join(category.value for category in self.missing)
        return (
            f"{len(self.registered)} tools registered; "
            f"no provider configured for: {absent}"
        )


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

    registry.freeze()

    served = set(registry.categories())
    missing = tuple(
        category
        for category in ToolCategory
        # Documents are Phase 4 and have no provider by design, so listing them
        # as missing would report a gap that is not one.
        if category is not ToolCategory.DOCUMENTS and category not in served
    )

    return registry, RegistryReport(registered=tuple(registered), missing=missing)
