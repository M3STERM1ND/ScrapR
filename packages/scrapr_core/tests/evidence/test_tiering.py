"""Source authority tiering (`REQ-EVID-002`, `DEC-08`).

The rule table is a pure function, so it is tested as one. The cases that
matter are the boundaries, because that is where a tiering mistake is both
likely and invisible: a lookalike domain reading as an established publisher, a
subdomain that should inherit, a subject with no domain at all.

**The tier is load-bearing**, which is why these are worth being fussy about.
Termination reads it (`DEC-04 §3.2`), confidence reads it (`DEC-09 §4.1`), and
conflict explanation reads it (`REQ-EVID-013`). A source tiered wrongly is a
question resolved too cheaply or a claim rated too highly.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import AuthorityTier, SourceCategory
from scrapr_core.evidence.tiering import assign_tier, registrable_host


def tier_of(url: str, **kwargs: object) -> AuthorityTier:
    return assign_tier(
        url=url,
        category=kwargs.pop("category", SourceCategory.WEB),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    ).tier


# --------------------------------------------------------------------------
# Primary — closest to the origin
# --------------------------------------------------------------------------


def test_a_filing_is_primary_whatever_its_url() -> None:
    """`REQ-TOOL-005 AC-1` holds through this rule rather than through the
    filings tool asserting a tier of its own."""
    decision = assign_tier(
        url="https://sec.gov/Archives/edgar/data/1/2/acme-10k.htm",
        category=SourceCategory.FILING,
    )

    assert decision.tier is AuthorityTier.PRIMARY
    assert decision.rule == "filing"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://www.sec.gov/x", id="sec"),
        pytest.param("https://bls.gov/data", id="bls"),
        pytest.param("https://www.gov.uk/thing", id="gov-uk"),
        pytest.param("https://ec.europa.eu/stat", id="europa"),
    ],
)
def test_a_government_host_is_primary(url: str) -> None:
    assert tier_of(url) is AuthorityTier.PRIMARY


def test_the_subjects_own_site_is_primary() -> None:
    """`DEC-08 §3.1`: closest to the origin, and emphatically not impartial
    about its own performance — a distinction `DEC-10 §4.2` keeps elsewhere."""
    decision = assign_tier(
        url="https://investors.acme.com/q4",
        category=SourceCategory.WEB,
        subject_hosts=frozenset({"acme.com"}),
    )

    assert decision.tier is AuthorityTier.PRIMARY
    assert decision.rule == "subject_domain"


# --------------------------------------------------------------------------
# Secondary, and the lookalike that must not reach it
# --------------------------------------------------------------------------


def test_an_established_publisher_is_secondary() -> None:
    """`REQ-TOOL-007 AC-2`."""
    assert tier_of("https://www.reuters.com/business/acme") is AuthorityTier.SECONDARY


def test_a_subdomain_inherits_from_its_registrable_domain() -> None:
    """So the allowlist does not need an entry per locale."""
    assert tier_of("https://uk.reuters.com/x") is AuthorityTier.SECONDARY


def test_a_lookalike_domain_does_not_inherit() -> None:
    """The failure an allowlist exists to prevent.

    `notreuters.com` ends with the string `reuters.com`, and a naive suffix
    check would tier a domain anyone can register as an established publisher.
    """
    assert tier_of("https://notreuters.com/x") is AuthorityTier.LOWER
    assert tier_of("https://reuters.com.evil.test/x") is AuthorityTier.LOWER


def test_a_subject_lookalike_does_not_inherit_either() -> None:
    """Same trap on rule 4, where the consequence is `PRIMARY`."""
    assert (
        tier_of("https://notacme.com/x", subject_hosts=frozenset({"acme.com"}))
        is AuthorityTier.LOWER
    )


# --------------------------------------------------------------------------
# The default, which is the decision
# --------------------------------------------------------------------------


def test_an_unlisted_host_is_lower_by_default() -> None:
    """`DEC-08 §4`, the largest behavioural change in that record.

    Two sources nobody vouched for no longer resolve a question between them.
    The alternative default says "unknown" and "established publisher" are the
    same thing to termination, confidence and conflict explanation alike.
    """
    decision = assign_tier(url="https://someblog.test/post", category=SourceCategory.WEB)

    assert decision.tier is AuthorityTier.LOWER
    assert decision.rule == "unlisted"


def test_the_default_is_configurable() -> None:
    """The dial-back. At `SECONDARY` this is pre-`DEC-08` behaviour, which is
    the escape hatch if `LOWER` proves too strict against real sources."""
    decision = assign_tier(
        url="https://someblog.test/post",
        category=SourceCategory.WEB,
        default=AuthorityTier.SECONDARY,
    )

    assert decision.tier is AuthorityTier.SECONDARY


def test_a_source_with_no_url_takes_the_default() -> None:
    """An upload or a provider record identified by accession number. Not an
    error, and not a reason to invent a tier."""
    assert (
        assign_tier(url=None, category=SourceCategory.WEB).tier is AuthorityTier.LOWER
    )


# --------------------------------------------------------------------------
# Inspectability, which is the acceptance criterion
# --------------------------------------------------------------------------


def test_every_decision_records_the_rule_that_fired() -> None:
    """`AC-3`. A user asking "why is this secondary" gets the rule, not a
    score — which is the whole argument for a table over a heuristic."""
    decision = assign_tier(
        url="https://www.ft.com/content/x", category=SourceCategory.WEB
    )

    assert decision.rationale["rule"] == "publisher"
    assert decision.rationale["tier"] == "secondary"
    assert "ft.com" in decision.rationale["detail"]


def test_tiering_is_deterministic() -> None:
    """`REQ-EVID-016 AC-2` needs the relationship consistent across runs, and
    a pure function of the URL is how that is guaranteed rather than hoped."""
    calls = [
        assign_tier(url="https://reuters.com/a", category=SourceCategory.WEB)
        for _ in range(5)
    ]

    assert len({(d.tier, d.rule) for d in calls}) == 1


def test_nothing_is_ever_excluded() -> None:
    """`REQ-EVID-003 AC-3`: no hard filter silently discards a lower-tier
    source. This module labels; it has no return value that means "drop"."""
    for url in ("https://someblog.test/x", "https://reuters.com/x", "https://sec.gov/x"):
        assert assign_tier(url=url, category=SourceCategory.WEB).tier in AuthorityTier


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://www.Reuters.com/x", "reuters.com", id="case-and-www"),
        pytest.param("https://reuters.com:443/x", "reuters.com", id="port"),
        pytest.param("not a url", "", id="garbage"),
        pytest.param(None, "", id="none"),
    ],
)
def test_host_extraction(url: str | None, expected: str) -> None:
    assert registrable_host(url) == expected
