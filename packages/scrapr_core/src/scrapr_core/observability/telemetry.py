"""What a run spent and what it called, recorded as it happens (`REQ-OBS-002..007`, `DEC-25`).

Two instruments, one ledger:

* **`MeteredProvider`** wraps the model provider and adds each call's tokens and
  cost to the ledger of the step that made it.
* **`record_tool_call`** is called by the tool registry after every invocation
  and writes a `tool_invocations` row: which tool, which category, how it went,
  how long it took, what it was asked, and which domain it concerned.

**The ledger is found through a context variable, not passed down.** Stages
call the provider and the registry through signatures that predate metering,
and threading a ledger through every one of them would change a dozen call
sites to add a side channel. The job runner opens a `step_telemetry` scope
around each step; outside one — a unit test calling a stage directly —
instruments record nothing and change nothing.

**Cost is enforced here too.** A step that runs against a budget attaches it to
its telemetry, and every metered model call and charged tool call spends from
it, so the run's cost counter is live rather than reconstructed afterwards
(`DEC-25`).

**None of this reaches a user** (`REQ-OBS-007 AC-2`). Tool names, queries and
failure detail go to `tool_invocations`, which no API route reads.
"""

from __future__ import annotations

import contextvars
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, Protocol, final
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from scrapr_core.db.models import ToolInvocation
from scrapr_core.domain.json import JsonMapping
from scrapr_core.llm.anthropic_provider import ProviderOutputTruncated
from scrapr_core.llm.contract import (
    LLMProvider,
    ModelTier,
    StructuredResult,
    TokenUsage,
    UntrustedDocument,
)
from scrapr_core.security.trust import Trusted
from scrapr_core.tools.contract import ToolCategory, ToolFailure, ToolOutcome, ToolRequest

__all__ = [
    "TOOL_CALL_COST_MICROS",
    "MeteredProvider",
    "RunLedger",
    "SpendsCost",
    "StepTelemetry",
    "current_telemetry",
    "record_retrieval",
    "record_tool_call",
    "redact_detail",
    "step_telemetry",
]

TOOL_CALL_COST_MICROS: Final[dict[ToolCategory, int]] = {
    # Tavily, per search (`DEC-07`): web search and news are both searches.
    ToolCategory.WEB_SEARCH: 8_000,
    ToolCategory.NEWS: 8_000,
    # Flat-rate or free: FMP's plan, EDGAR, Adzuna's free tier, our own fetch
    # and our own full-text index.
    ToolCategory.FINANCIAL: 0,
    ToolCategory.FILINGS: 0,
    ToolCategory.JOBS: 0,
    ToolCategory.PAGE_FETCH: 0,
    ToolCategory.DOCUMENTS: 0,
}
"""What one call to each category costs, in millionths of a dollar (`DEC-25`)."""


class SpendsCost(Protocol):
    """Anything a step's cost can be charged against: the run budget."""

    def spend(self, *, cost_micros: int = 0, tool_calls: int = 0) -> None: ...


@final
@dataclass(slots=True)
class RunLedger:
    """Tokens, cost and calls for one step, by model tier."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    by_tier: dict[str, dict[str, int]] = field(default_factory=dict)
    retrieval: dict[str, int] = field(default_factory=dict)
    """Counts between retrieval and evidence: items returned, candidates the
    model proposed, evidence grounded, drops by reason, fallbacks taken."""

    def add_retrieval(self, counts: Mapping[str, int]) -> None:
        for name, value in counts.items():
            self.retrieval[name] = self.retrieval.get(name, 0) + int(value)

    def add_usage(self, tier: ModelTier, input_tokens: int, output_tokens: int, cost_micros: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_micros += cost_micros
        self.model_calls += 1
        bucket = self.by_tier.setdefault(tier.value, {"calls": 0, "input": 0, "output": 0, "cost_micros": 0})
        bucket["calls"] += 1
        bucket["input"] += input_tokens
        bucket["output"] += output_tokens
        bucket["cost_micros"] += cost_micros

    def as_json(self) -> JsonMapping:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_micros": self.cost_micros,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "by_tier": {tier: dict(values) for tier, values in self.by_tier.items()},
            "retrieval": dict(self.retrieval),
        }


@final
@dataclass(slots=True)
class StepTelemetry:
    """The scope a running step records into."""

    session: Session
    run_id: UUID
    stage: str
    ledger: RunLedger = field(default_factory=RunLedger)
    budget: SpendsCost | None = None

    def charge(self, cost_micros: int) -> None:
        if cost_micros and self.budget is not None:
            self.budget.spend(cost_micros=cost_micros)


_CURRENT: Final[contextvars.ContextVar[StepTelemetry | None]] = contextvars.ContextVar(
    "scrapr_step_telemetry", default=None
)


def current_telemetry() -> StepTelemetry | None:
    return _CURRENT.get()


@contextmanager
def step_telemetry(session: Session, run_id: UUID, stage: str) -> Iterator[StepTelemetry]:
    """Open a recording scope for one step."""
    telemetry = StepTelemetry(session=session, run_id=run_id, stage=stage)
    token = _CURRENT.set(telemetry)
    try:
        yield telemetry
    finally:
        _CURRENT.reset(token)


@final
class MeteredProvider:
    """A model provider that reports what each call cost (`REQ-OBS-004`)."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        try:
            result = await self._inner.complete_structured(instruction, untrusted, schema, model_tier)
        except ProviderOutputTruncated as exc:
            # The tokens were generated and billed even though no answer came
            # back, so they count against the step like any other call.
            _record_usage(exc.tier, exc.usage)
            raise
        _record_usage(result.tier, result.usage)
        return result


def _record_usage(tier: ModelTier, usage: TokenUsage) -> None:
    telemetry = current_telemetry()
    if telemetry is not None:
        telemetry.ledger.add_usage(tier, usage.input_tokens, usage.output_tokens, usage.cost_micros)
        telemetry.charge(usage.cost_micros)


def record_retrieval(counts: Mapping[str, int]) -> None:
    """Add retrieval-to-evidence counts to the step in scope, if there is one.

    Flattened names (`dropped_excerpt_not_in_source`) so the run's
    `effort_used.retrieval` stays a flat map of integers a query can sum.
    """
    telemetry = current_telemetry()
    if telemetry is not None:
        telemetry.ledger.add_retrieval(counts)


def _domain(params: Mapping[str, object]) -> str | None:
    """The host a request concerned, when it names one (`REQ-OBS-006`)."""
    for key in ("url", "source_url"):
        value = params.get(key)
        if isinstance(value, str) and value:
            host = urlsplit(value).hostname
            return host.lower() if host else None
    return None


def _request_record(request: ToolRequest) -> JsonMapping:
    """What was asked, for diagnosis (`REQ-OBS-007 AC-1`).

    The parameters the application built — a query, a URL, a session id —
    never credentials, which live in provider clients and are not parameters.
    Long values are clipped: this is a diagnostic record, not an archive.
    """
    return {
        key: (value[:500] if isinstance(value, str) else value)
        for key, value in request.params.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def record_tool_call(
    request: ToolRequest,
    tool_name: str,
    outcome: ToolOutcome,
    started: float,
) -> None:
    """Write one `tool_invocations` row for the step in scope, if there is one.

    Called by the registry for every invocation, successful or not, so the
    failure rate of each tool is its failures over its calls (`REQ-OBS-002`).
    """
    telemetry = current_telemetry()
    if telemetry is None:
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    failed = isinstance(outcome, ToolFailure)
    cost = 0 if failed else TOOL_CALL_COST_MICROS.get(request.category, 0)

    telemetry.ledger.tool_calls += 1
    telemetry.ledger.cost_micros += cost
    telemetry.charge(cost)

    telemetry.session.add(
        ToolInvocation(
            run_id=telemetry.run_id,
            tool_name=tool_name or "unregistered",
            tool_category=request.category.value,
            status="failure" if failed else "success",
            error_kind=outcome.kind if isinstance(outcome, ToolFailure) else None,
            latency_ms=latency_ms,
            request_digest={"stage": telemetry.stage, **_request_record(request)},
            cost_micros=cost,
            source_domain=_domain(request.params),
            result_items=None if isinstance(outcome, ToolFailure) else len(outcome.items),
            error_detail=(
                redact_detail(outcome.message) if isinstance(outcome, ToolFailure) else None
            ),
        )
    )


ERROR_DETAIL_LIMIT: Final = 500

_QUERY_STRING = re.compile(r"(https?://[^\s?#\"']+)\?[^\s\"']*")
_SECRET_PAIR = re.compile(
    r"(?i)\b(api[_-]?key|apikey|app[_-]?key|app[_-]?id|token|secret|password|key)"
    r"(\s*[=:]\s*)[\"']?[^\s&\"',;]+"
)


def redact_detail(message: str) -> str:
    """A failure message fit for an operator table (`REQ-OBS-007`).

    Providers authenticate by query parameter, and an HTTP client's exception
    text includes the URL it called — so a raw message is one bad day away from
    storing an API key. Query strings are cut and anything shaped like a
    credential assignment is masked before the text is clipped.
    """
    cleaned = _QUERY_STRING.sub(r"\1?<redacted>", message)
    cleaned = _SECRET_PAIR.sub(r"\1\2<redacted>", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[:ERROR_DETAIL_LIMIT]
