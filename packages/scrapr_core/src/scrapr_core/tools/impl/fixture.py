"""Hand-authored fixture tools — what stands in for the unresolved providers.

`OPEN-05..09` name no provider yet, so there is nothing to record a cassette
from. Fixtures are what Phase 0 actually needs (implementation plan §15), and
they keep earning their place afterwards: a cassette proves a provider is
handled correctly, while a fixture proves the *contract* is, which is what the
orchestrator is written against.

A fixture tool is also the cheapest possible proof of `REQ-TOOL-009 AC-3`
(extensibility): registering a second one in a category that already has a
provider changes no orchestrator code, and the test suite says so.

Fixture content is `Untrusted` like any other retrieved text — including the
adversarial fixtures, which is the point of `tests/adversarial/`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.security.trust import SourceRef, Untrusted
from scrapr_core.tools.contract import (
    FailureKind,
    ToolCategory,
    ToolFailure,
    ToolItem,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)

__all__ = ["FailingFixtureTool", "FixtureTool", "fixture_item"]


def fixture_item(
    *,
    source_name: str,
    text: str,
    source_url: str | None = None,
    source_identifier: str | None = None,
    source_category: SourceCategory = SourceCategory.WEB,
    retrieved_at: dt.datetime | None = None,
    published_at: dt.datetime | None = None,
    accessibility: Accessibility = Accessibility.ACCESSIBLE,
) -> ToolItem:
    """Build one fixture item, wrapping its text as `Untrusted`.

    A helper rather than a literal in every fixture, so that no fixture can
    accidentally be authored with trusted-looking content: the wrapping is not
    optional here either.
    """
    locator = source_url or source_identifier or source_name
    return ToolItem(
        source_name=source_name,
        source_category=source_category,
        retrieved_at=retrieved_at or dt.datetime.now(dt.UTC),
        accessibility=accessibility,
        content=Untrusted(text, SourceRef(kind="fixture", locator=locator)),
        source_url=source_url,
        source_identifier=source_identifier,
        published_at=published_at,
    )


@final
@dataclass(frozen=True, slots=True)
class FixtureTool:
    """Returns a fixed set of items, respecting the request's result budget."""

    name: str
    category: ToolCategory
    items: Sequence[ToolItem]

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        """Return the fixture, truncated to `budget.max_results`.

        Honouring the budget matters even for a fake: a pipeline that only ever
        sees fixtures small enough to fit will not have its truncation path
        exercised until a real provider returns forty results.
        """
        return ToolResult(
            items=tuple(self.items[: request.budget.max_results]),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )


@final
@dataclass(frozen=True, slots=True)
class FailingFixtureTool:
    """Always fails, with a chosen failure kind.

    Partial-result handling (`REQ-AGENT-009`) and failure classification
    (`REQ-TOOL-010`) both need a provider that reliably fails, and "unplug the
    network" is not a test.
    """

    name: str
    category: ToolCategory
    kind: FailureKind = "error"
    message: str = "fixture failure"

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        return ToolFailure(
            kind=self.kind,
            message=self.message,
            tool=self.name,
            category=self.category,
        )
