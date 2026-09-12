"""Source deduplication (`REQ-EVID-006`).

Three acceptance criteria, and they pull against each other, which is why this
is its own module rather than three lines in a repository.

* `AC-1` — **the same URL retrieved twice yields one source.** Until now
  `url_normalized` was set to the raw URL, so `?utm_source=news` made a second
  source out of the same page, and the unique constraint on
  `(version_id, url_normalized)` never fired. That is corroboration invented
  out of a tracking parameter.
* `AC-2` — **syndicated copies must not inflate apparent corroboration.** Three
  outlets running one wire story is one story. `DEC-07 §3.1` names this as the
  live risk in the news provider, and this is the half of the answer that can
  be implemented without outlet metadata.
* `AC-3` — **a distinct reporting period of the same publisher survives.** The
  pull in the other direction: two filings from one registrant are two facts,
  and collapsing them would delete a year of history.

The resolution is that `AC-1` and `AC-2` work on different keys. A URL
duplicate is one source. A syndication duplicate is several sources whose
*content* matches — they stay as separate rows, because `AC-2` says they must
not inflate corroboration, not that they must vanish. Coverage counts a
syndication group once; the report still shows every outlet that carried it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "TRACKING_PARAMS",
    "content_fingerprint",
    "normalize_url",
]

TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "source",
        "cmpid",
        "ncid",
        "sref",
        "__twitter_impression",
        "spm",
    }
)
"""Parameters that identify the *referral*, not the document.

Stripped because two links to one article differing only by campaign tag are
one article, and counting them as two is corroboration manufactured from
marketing.

Deliberately a closed list rather than "strip everything": a query parameter is
often the whole address. `?id=12345` on a filings endpoint or `?q=` on a search
result names a different document, and dropping it would merge pages that have
nothing to do with each other — a far worse error than keeping a duplicate.
"""

_DEFAULT_PORTS: Final[dict[str, str]] = {"http": "80", "https": "443"}

_WHITESPACE = re.compile(r"\s+")


def normalize_url(url: str | None) -> str | None:
    """Canonical form for deduplication (`AC-1`).

    What is normalised, and nothing beyond it: scheme and host case, the `www.`
    prefix, the default port, a trailing slash, tracking parameters, and
    parameter order. Everything else is left exactly as it arrived.

    The fragment is dropped — `#section-3` addresses a place *within* one
    document, and `REQ-EVID-007` stores evidence with its own excerpt, so the
    anchor adds nothing and would split one page into several sources.

    Returns `None` for a source with no URL, which is normal: an upload or a
    provider record is identified by `identifier` instead.
    """
    if not url or not url.strip():
        return None

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return None

    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme, ""):
        host = f"{host}:{port}"

    kept = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name.lower() not in TRACKING_PARAMS
    )

    path = parts.path.rstrip("/") or "/"

    return urlunsplit((scheme, host, path, urlencode(kept), ""))


def content_fingerprint(text: str) -> str:
    """A key that matches syndicated copies of one story (`AC-2`).

    Whitespace-collapsed, case-folded, hashed. Deliberately exact rather than
    fuzzy: a near-duplicate detector needs a similarity threshold, and an
    unvalidated threshold here would either merge two genuinely different
    reports or leave a wire story counted three times — and there is no data
    yet on which failure is more common.

    What this catches is the common case: outlets republishing a wire story
    verbatim. What it misses is a rewritten one, which stays `OPEN-16`-adjacent
    territory and is named in `DEC-07 §7` as the symptom that should prompt a
    real news provider.

    **Fingerprints group; they never delete.** `AC-2` requires syndication not
    inflate corroboration, and `AC-3` requires distinct reporting periods
    survive — so every source stays a row, and it is coverage counting that
    treats a group as one.
    """
    collapsed = _WHITESPACE.sub(" ", text).strip().casefold()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()
