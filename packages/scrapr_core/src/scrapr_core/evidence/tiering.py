"""Source authority tiering (`REQ-EVID-002`, `DEC-08`, closing `OPEN-15`).

A source's tier is decided by a **static, ordered rule table** evaluated at
insert. First match wins, and the rule that fired is recorded. No model call, no
heuristic score, no per-run variation — `AC-3` requires assignment be
"deterministic and inspectable, not per-run improvisation", and a table you can
read top to bottom is the only mechanism that is obviously both.

**This is load-bearing, not decoration.** Three things already read the tier:

1. **Termination** (`DEC-04 §3.2`) — a question resolves on two distinct
   sources above `LOWER`, or on one `PRIMARY` alone. Tier decides how much
   research a question costs.
2. **Confidence** (`DEC-09 §4.1`) — the first input to the level a reader sees.
3. **Conflict explanation** (`REQ-EVID-013`) — how the report says which of two
   disagreeing sources to believe.

**Tier is not belief.** It answers "how close is this to the origin", not "how
much should I trust this". A company's own statement about itself is `PRIMARY`
because it is the origin — and emphatically not impartial about its own
performance. That distinction belongs to conflict explanation, and `DEC-10 §4.2`
keeps it there.

**Nothing is ever excluded** (`REQ-EVID-003`). This module labels; it does not
filter. A `LOWER` source still reaches the report, still supports claims, and
still shows its tier — `AC-3` forbids a hard filter that silently discards one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final
from urllib.parse import urlsplit

from scrapr_core.db.enums import AuthorityTier, SourceCategory

__all__ = [
    "DATA_PROVIDERS",
    "GOVERNMENT_SUFFIXES",
    "PUBLISHERS",
    "TierDecision",
    "assign_tier",
    "registrable_host",
]

GOVERNMENT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "gov",
        "mil",
        "gov.uk",
        "parliament.uk",
        "europa.eu",
        "gc.ca",
        "gov.au",
        "govt.nz",
        "go.jp",
        "gov.sg",
        "gov.in",
        "gov.za",
        "gov.br",
        "bund.de",
        "gouv.fr",
    }
)
"""Suffixes that make a host an official public body.

An open-ended set that starts with the common ones and grows on evidence
(`DEC-08 §8`). Being incomplete makes a real government source `LOWER`, which
understates it — the failure direction that costs coverage rather than
credibility.
"""

PUBLISHERS: Final[frozenset[str]] = frozenset(
    {
        "reuters.com",
        "apnews.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "nytimes.com",
        "washingtonpost.com",
        "economist.com",
        "bbc.co.uk",
        "bbc.com",
        "theguardian.com",
        "cnbc.com",
        "forbes.com",
        "businessinsider.com",
        "axios.com",
        "politico.com",
        "npr.org",
        "aljazeera.com",
        "nikkei.com",
        "scmp.com",
        "lemonde.fr",
        "spiegel.de",
        "elpais.com",
        "theinformation.com",
        "techcrunch.com",
        "arstechnica.com",
    }
)
"""Established publishers, tiered `SECONDARY` (`REQ-TOOL-007 AC-2`).

**This list encodes whoever wrote it** (`DEC-08 §9`). It under-represents
non-English and regional outlets, and it will be wrong at first — it is meant
to be edited, and being visible and diffable is the entire argument for it over
a scoring heuristic. Read it as a project artefact, not a fact about the world.
"""

DATA_PROVIDERS: Final[frozenset[str]] = frozenset(
    {
        "sec.gov",
        "investor.gov",
        "federalreserve.gov",
        "bls.gov",
        "census.gov",
        "worldbank.org",
        "imf.org",
        "oecd.org",
        "eurostat.ec.europa.eu",
        "financialmodelingprep.com",
        "adzuna.com",
        "crunchbase.com",
        "pitchbook.com",
        "statista.com",
    }
)
"""Reference data publishers, tiered `SECONDARY` unless a higher rule already
fired. Several are also government hosts and reach `PRIMARY` at rule 3 first,
which is correct and is why order matters."""


@final
@dataclass(frozen=True, slots=True)
class TierDecision:
    """A tier, and the rule that produced it.

    `rationale` is persisted to `sources.tier_rationale` because `AC-3` requires
    the assignment be inspectable after the fact — a user asking "why is this
    secondary" gets the rule name, not a score.
    """

    tier: AuthorityTier
    rule: str
    detail: str = ""

    @property
    def rationale(self) -> dict[str, str]:
        return {"rule": self.rule, "tier": self.tier.value, "detail": self.detail}


def registrable_host(url: str | None) -> str:
    """The host, lowercased and stripped of `www.`.

    Not a public-suffix implementation. Matching is done by suffix below, so
    `uk.reuters.com` matches `reuters.com` without needing its own entry —
    which is what `DEC-08 §3` means by matching on registrable domain.
    """
    if not url:
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _matches(host: str, entries: frozenset[str]) -> str:
    """The entry `host` is or sits under, or an empty string.

    Suffix matching is anchored on a dot so that `notreuters.com` cannot match
    `reuters.com` — a lookalike domain reading as an established publisher is
    precisely the failure a deny-list-free allowlist is supposed to avoid.
    """
    for entry in entries:
        if host == entry or host.endswith(f".{entry}"):
            return entry
    return ""


def assign_tier(
    *,
    url: str | None,
    category: SourceCategory,
    subject_hosts: frozenset[str] = frozenset(),
    default: AuthorityTier = AuthorityTier.LOWER,
) -> TierDecision:
    """Decide one source's tier (`DEC-08 §3`). First match wins.

    `subject_hosts` is the research subject's own domain(s), resolved once at
    plan time and version-scoped so a re-run cannot silently retier existing
    evidence.

    `default` is what an unmatched source gets. It is `LOWER` by decision, and
    a parameter rather than a constant so the threshold can be dialled back
    without editing this table — see the setting in `config`. Raising it to
    `SECONDARY` restores pre-`DEC-08` behaviour, in which two sources nobody
    vouched for resolve a question between them.
    """
    # 1, 2 — what the source *is*, decided by the tool that retrieved it.
    if category is SourceCategory.FILING:
        return TierDecision(AuthorityTier.PRIMARY, "filing", "a regulatory filing")
    if category is SourceCategory.OFFICIAL:
        return TierDecision(
            AuthorityTier.PRIMARY, "official", "an official statement"
        )

    host = registrable_host(url)
    if not host:
        return TierDecision(default, "no_host", "no host to evaluate")

    # 3 — a public body.
    government = _matches(host, GOVERNMENT_SUFFIXES)
    if government:
        return TierDecision(
            AuthorityTier.PRIMARY, "government", f"government suffix {government}"
        )

    # 4 — the subject talking about itself. Closest to the origin, and not
    # impartial about it; `DEC-10 §4.2` is where that distinction is drawn.
    subject = _matches(host, subject_hosts)
    if subject:
        return TierDecision(
            AuthorityTier.PRIMARY, "subject_domain", f"the subject's own site {subject}"
        )

    # 5, 6 — vouched for by a list somebody can read and argue with.
    publisher = _matches(host, PUBLISHERS)
    if publisher:
        return TierDecision(
            AuthorityTier.SECONDARY, "publisher", f"established publisher {publisher}"
        )

    provider = _matches(host, DATA_PROVIDERS)
    if provider:
        return TierDecision(
            AuthorityTier.SECONDARY, "data_provider", f"reference data from {provider}"
        )

    # 7 — unknown. Not excluded (`REQ-EVID-003`), just not corroborating on its
    # own: `DEC-04 §3.2` needs one source above `LOWER` to resolve a question.
    return TierDecision(default, "unlisted", f"{host} is not on any list")
