"""The uniform tool contract — the single most important interface in the backend.

`REQ-TOOL-001` requires every retrieval tool to expose the same shape, and
`REQ-TOOL-009` requires a new tool to be addable without touching the
orchestrator. Together they are what make the unresolved provider questions
(`OPEN-05..09`) *late-binding*: the orchestrator asks a category for results and
never learns which provider answered, so choosing one later is a registration
change rather than a redesign.

Three rules are enforced by the types rather than by review:

1. **Provenance is not optional.** A `ToolItem` that cannot say where it came
   from raises at construction (`REQ-TOOL-012 AC-2`). Evidence without a source
   can therefore never enter the system in the first place.
2. **Content is `Untrusted`.** There is no path that turns retrieved text into
   an instruction (§9); the type refuses to become a string.
3. **Failures return, they do not raise** (`REQ-TOOL-010 AC-1`). One dead
   provider degrades one area instead of aborting the run
   (`REQ-AGENT-009 AC-1`), and a caller that forgets to handle failure gets a
   type error rather than an exception in production.

Tools are `async` while the database layer is synchronous, and the split is
deliberate: retrieval is IO-bound and fans out across research areas, where
concurrency is the whole game, whereas the worker scales by process.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Literal, Protocol, final, runtime_checkable

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.domain.json import JsonMapping
from scrapr_core.security.trust import Untrusted

__all__ = [
    "FailureKind",
    "Tool",
    "ToolBudget",
    "ToolCategory",
    "ToolFailure",
    "ToolItem",
    "ToolOutcome",
    "ToolRequest",
    "ToolResult",
]


@unique
class ToolCategory(StrEnum):
    """What a tool retrieves. **The unit of registration and selection.**

    The orchestrator selects a category, never a provider
    (`REQ-TOOL-009 AC-2`), which is exactly why an unresolved provider question
    cannot block the pipeline.
    """

    WEB_SEARCH = "web_search"
    PAGE_FETCH = "page_fetch"
    FINANCIAL = "financial"
    FILINGS = "filings"
    JOBS = "jobs"
    NEWS = "news"
    DOCUMENTS = "documents"


type FailureKind = Literal[
    "timeout", "error", "paywalled", "blocked", "not_found", "rate_limited"
]
"""Classified failure (`REQ-TOOL-010`). The classification drives retry policy,
which is why it is a closed set and not a message to be pattern-matched."""


@final
@dataclass(frozen=True, slots=True)
class ToolBudget:
    """What a single invocation is allowed to spend (`REQ-TOOL-013`).

    Both values are `TBD-02` and therefore passed in per call rather than baked
    into a tool: setting the number is a product decision this layer must not
    make on the product's behalf.
    """

    timeout_seconds: float = 30.0
    max_results: int = 10

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_results <= 0:
            raise ValueError("max_results must be positive")


@final
@dataclass(frozen=True, slots=True)
class ToolRequest:
    """One retrieval request, addressed to a category.

    `tool` pins a specific implementation and is normally empty. It exists for
    the two cases that genuinely need it: a test asserting one provider's
    behaviour, and the second tool in a category that `REQ-TOOL-009 AC-3` asks
    be demonstrable. Orchestrator code leaves it unset.
    """

    category: ToolCategory
    params: JsonMapping = field(default_factory=dict)
    budget: ToolBudget = field(default_factory=ToolBudget)
    tool: str = ""


@final
@dataclass(frozen=True, slots=True)
class ToolItem:
    """One retrieved item, carrying its own provenance.

    Constructing one without provenance raises. That is what makes
    `REQ-EVID-001` ("every piece of evidence has a source") structural: there is
    no valid in-memory representation of sourceless content, so no later stage
    has to check for one.
    """

    source_name: str
    source_category: SourceCategory
    retrieved_at: dt.datetime
    accessibility: Accessibility
    content: Untrusted
    source_url: str | None = None
    source_identifier: str | None = None
    published_at: dt.datetime | None = None
    structured: JsonMapping | None = None
    """Provider-native structured data — a financial series, a filing header —
    kept alongside the text so numeric extraction does not have to re-parse
    prose it was handed in a usable form."""

    def __post_init__(self) -> None:
        if not self.source_name.strip():
            raise ValueError("a ToolItem must name its source (REQ-TOOL-012 AC-2)")

        if self.source_url is None and self.source_identifier is None:
            raise ValueError(
                "a ToolItem must be locatable: give source_url or "
                "source_identifier (REQ-TOOL-012 AC-2)"
            )

        # A naive timestamp cannot be compared against another source's, and
        # recency is an input to both conflict detection and confidence.
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")


@final
@dataclass(frozen=True, slots=True)
class ToolResult:
    """A successful invocation."""

    items: Sequence[ToolItem]
    retrieved_at: dt.datetime
    tool: str
    category: ToolCategory

    @property
    def is_empty(self) -> bool:
        """No results is a success, not a failure.

        A search that legitimately found nothing is different from a search that
        could not run, and `REQ-AGENT-009` treats the two differently: one is a
        gap to state, the other is a failure to report.
        """
        return not self.items


@final
@dataclass(frozen=True, slots=True)
class ToolFailure:
    """A failed invocation, returned rather than raised.

    `message` is internal. `REQ-SEC-010` requires errors be sanitized before
    they reach a user, and this text — provider names, URLs, upstream detail —
    is precisely what must not be shown.
    """

    kind: FailureKind
    message: str
    tool: str
    category: ToolCategory
    retryable: bool = True

    def __post_init__(self) -> None:
        # A paywall or a missing page will not become available on retry, and a
        # retry loop against it burns the budget for the whole area.
        if self.kind in ("paywalled", "not_found") and self.retryable:
            object.__setattr__(self, "retryable", False)


type ToolOutcome = ToolResult | ToolFailure
"""What every tool returns. A caller that ignores the failure branch fails to
type-check, which is the point."""


@runtime_checkable
class Tool(Protocol):
    """What every retrieval tool implements.

    A `Protocol`, so a tool needs no base class and no import from this module
    beyond the types it already uses — which is what keeps "add a tool without
    touching the orchestrator" (`REQ-TOOL-009`) true in both directions.
    """

    @property
    def name(self) -> str:
        """Unique across the registry. Internal only: never rendered
        (`REQ-ACT-003`)."""

    @property
    def category(self) -> ToolCategory: ...

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        """Retrieve, within the request's budget. Never raises for a failure a
        provider can produce."""

