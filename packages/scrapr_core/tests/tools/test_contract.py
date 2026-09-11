"""The contract's rules, which are enforced at construction rather than by review.

`REQ-TOOL-012 AC-2` requires every retrieved item to carry provenance. These
tests are what makes "requires" mean something: an item that cannot say where it
came from has no valid in-memory representation, so no later stage has to check
for one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.security.trust import SourceRef, Untrusted, UntrustedContentError
from scrapr_core.tools.contract import (
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolItem,
    ToolResult,
)

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
CONTENT = Untrusted("Acme reported $1.2bn revenue.", SourceRef("web", "https://a.example"))


def item(**overrides: object) -> ToolItem:
    fields: dict[str, object] = {
        "source_name": "Acme investor relations",
        "source_category": SourceCategory.WEB,
        "retrieved_at": NOW,
        "accessibility": Accessibility.ACCESSIBLE,
        "content": CONTENT,
        "source_url": "https://acme.example/ir",
    }
    fields.update(overrides)
    return ToolItem(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_an_item_with_a_url_is_valid() -> None:
    assert item().source_url == "https://acme.example/ir"


def test_an_item_identified_without_a_url_is_valid() -> None:
    """A filing or a financial series is identified by an accession number or a
    ticker, not by a URL."""
    built = item(source_url=None, source_identifier="0000320193-25-000106")

    assert built.source_identifier == "0000320193-25-000106"


def test_an_unlocatable_item_is_rejected() -> None:
    with pytest.raises(ValueError, match="locatable"):
        item(source_url=None, source_identifier=None)


def test_an_unnamed_item_is_rejected() -> None:
    with pytest.raises(ValueError, match="name its source"):
        item(source_name="   ")


def test_a_naive_retrieval_timestamp_is_rejected() -> None:
    """Recency feeds conflict detection and confidence, and a naive timestamp
    cannot be compared against another source's."""
    with pytest.raises(ValueError, match="retrieved_at must be timezone-aware"):
        item(retrieved_at=dt.datetime(2026, 9, 10, 12, 0))


def test_a_naive_publication_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="published_at must be timezone-aware"):
        item(published_at=dt.datetime(2026, 9, 10, 12, 0))


def test_item_content_stays_untrusted() -> None:
    """The single most important property of the contract: retrieved content
    cannot become an instruction, even by accident (`REQ-SEC-012`)."""
    built = item()

    with pytest.raises(UntrustedContentError):
        f"{built.content}"


def test_an_item_is_immutable() -> None:
    built = item()

    with pytest.raises(AttributeError):
        built.source_name = "something else"  # type: ignore[misc]


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("timeout_seconds", 0), ("timeout_seconds", -1), ("max_results", 0)],
)
def test_a_nonsensical_budget_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ToolBudget(**{field: value})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------


def test_an_empty_result_is_a_success_not_a_failure() -> None:
    """A search that found nothing is a gap to state; a search that could not
    run is a failure to report. `REQ-AGENT-009` treats them differently."""
    result = ToolResult(
        items=(), retrieved_at=NOW, tool="fixture", category=ToolCategory.WEB_SEARCH
    )

    assert result.is_empty
    assert isinstance(result, ToolResult)


def test_a_result_with_items_is_not_empty() -> None:
    result = ToolResult(
        items=(item(),),
        retrieved_at=NOW,
        tool="fixture",
        category=ToolCategory.WEB_SEARCH,
    )

    assert not result.is_empty


@pytest.mark.parametrize("kind", ["paywalled", "not_found"])
def test_permanent_failures_are_not_retryable(kind: str) -> None:
    """Retrying a paywall burns the area's budget on an outcome that cannot
    change."""
    failure = ToolFailure(
        kind=kind,  # type: ignore[arg-type]
        message="behind a subscription",
        tool="fixture",
        category=ToolCategory.NEWS,
    )

    assert not failure.retryable


@pytest.mark.parametrize("kind", ["timeout", "error", "rate_limited", "blocked"])
def test_transient_failures_stay_retryable(kind: str) -> None:
    failure = ToolFailure(
        kind=kind,  # type: ignore[arg-type]
        message="upstream hiccup",
        tool="fixture",
        category=ToolCategory.NEWS,
    )

    assert failure.retryable
