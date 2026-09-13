"""Ordering an update, most likely to have changed first (`REQ-VER-003 AC-3`, `DEC-19`)."""

from __future__ import annotations

import datetime as dt

from scrapr_core.versioning import AreaHistory, CitedEvidence, Priority, prioritize_areas

NOW = dt.datetime(2026, 9, 13, tzinfo=dt.UTC)


def area(name: str, categories: tuple[str, ...], evidence: tuple[CitedEvidence, ...] = ()) -> AreaHistory:
    return AreaHistory(name=name, questions=(f"What about {name}?",), categories=categories, evidence=evidence)


def test_stale_then_volatile_then_stable() -> None:
    stable = area("History", ("web_search",))
    volatile = area("Hiring", ("jobs",))
    stale = area(
        "Share price",
        ("web_search",),
        (
            CitedEvidence(
                content="Acme's share price closed at $42.",
                published_at=NOW - dt.timedelta(days=5),
                retrieved_at=NOW - dt.timedelta(days=5),
            ),
        ),
    )

    ordered = prioritize_areas([stable, volatile, stale], now=NOW)

    assert [entry.area.name for entry in ordered] == ["Share price", "Hiring", "History"]
    assert [entry.priority for entry in ordered] == [
        Priority.STALE,
        Priority.VOLATILE,
        Priority.STABLE,
    ]
    assert "aged past" in ordered[0].reason
    assert "jobs" in ordered[1].reason


def test_recent_evidence_is_not_stale() -> None:
    fresh = area(
        "Revenue",
        ("filings",),
        (
            CitedEvidence(
                content="Acme reported revenue of $1.2bn.",
                published_at=NOW - dt.timedelta(days=30),
                retrieved_at=NOW - dt.timedelta(days=1),
            ),
        ),
    )

    assert prioritize_areas([fresh], now=NOW)[0].priority is Priority.STABLE


def test_retrieval_date_stands_in_when_publication_is_unknown() -> None:
    old_read = area(
        "Share price",
        ("web_search",),
        (
            CitedEvidence(
                content="Acme's share price closed at $42.",
                published_at=None,
                retrieved_at=NOW - dt.timedelta(days=3),
            ),
        ),
    )

    assert prioritize_areas([old_read], now=NOW)[0].priority is Priority.STALE


def test_ties_keep_the_previous_plan_order() -> None:
    first = area("Competitors", ("news",))
    second = area("Hiring", ("jobs",))

    assert [entry.area.name for entry in prioritize_areas([first, second], now=NOW)] == [
        "Competitors",
        "Hiring",
    ]
