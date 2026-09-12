"""Where the fetcher is allowed to go (`REQ-SEC-015 AC-2`, SSRF).

Page fetch is the one tool that takes a URL from outside the application and
opens a connection to it. Implementation plan §9 states the rule: it "refuses
private, link-local and loopback addresses, and re-validates after every
redirect".

**The check is on the resolved address, not on the hostname.** A deny-list of
names is not a control: `localhost` has a thousand spellings, and a name the
attacker owns can simply resolve to `169.254.169.254`. So the host is resolved
first and every address it answers with has to be publicly routable — one
private answer rejects the whole URL, because which address a client picks from
a multi-answer resolution is not something this code decides.

**Redirects are not followed by the HTTP client.** A validated URL that replies
`302 Location: http://127.0.0.1/` would otherwise walk straight past the guard,
so the fetcher follows hops itself and calls back here for each one.

**A residual gap, stated rather than papered over.** Validating a name and then
handing that same name to the client leaves a window in which the second
resolution can answer differently — DNS rebinding. Closing it means connecting
to the address that was validated rather than re-resolving, which is a custom
transport and belongs with the Phase 8 hardening pass (`OPEN-18`). What is here
closes the whole of the ordinary case: a hostname that points at private space,
and a redirect chain that turns toward it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Literal, final
from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_REDIRECTS",
    "UrlRejected",
    "UrlVerdict",
    "resolve_and_validate",
]

ALLOWED_SCHEMES = frozenset({"http", "https"})
"""Everything else is refused. `file://` reads the worker's disk and `gopher://`
and friends are classic request-smuggling vectors; none of them is a web page."""

MAX_REDIRECTS = 5
"""Enough for the ordinary canonicalisation chain — http to https, apex to www,
a trailing slash — and short enough that a redirect loop ends as a failure
rather than as a budget being drained."""

type RejectionReason = Literal[
    "scheme", "credentials", "no_host", "unresolvable", "private_address"
]
"""Why a URL was refused. A closed set, because the fetcher maps it onto a
`FailureKind` and a message pattern-matched from prose is not a contract."""


@final
@dataclass(frozen=True, slots=True)
class UrlRejected:
    """A URL the fetcher must not open."""

    reason: RejectionReason
    detail: str
    """Internal only. `REQ-SEC-010` keeps upstream detail away from users, and
    this names hosts and addresses."""


@final
@dataclass(frozen=True, slots=True)
class UrlAccepted:
    """A URL that resolved entirely to publicly routable addresses."""

    url: str
    host: str


type UrlVerdict = UrlAccepted | UrlRejected


def _is_publicly_routable(address: str) -> bool:
    """Whether one resolved address may be connected to.

    Written as a deny-list with `is_global` as a backstop rather than as
    `is_global` alone: the explicit clauses say which threat each one answers,
    and a future Python that reclassifies a range cannot silently open one.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False

    # `::ffff:127.0.0.1` is loopback wearing an IPv6 spelling, and the flags
    # below read `False` on the wrapper.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if (
        ip.is_private  # 10/8, 172.16/12, 192.168/16, and the RFC1918 v6 equivalents
        or ip.is_loopback  # 127/8, ::1
        or ip.is_link_local  # 169.254/16 — the cloud metadata endpoint lives here
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0, which some stacks route to localhost
    ):
        return False

    return ip.is_global


async def resolve_and_validate(raw_url: str) -> UrlVerdict:
    """Decide whether one URL may be fetched.

    Called for the requested URL and again for every redirect target, which is
    the whole point: a chain is only as safe as its last hop.
    """
    parts = urlsplit(raw_url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return UrlRejected(
            reason="scheme",
            detail=f"scheme {parts.scheme!r} is not http or https",
        )

    # Credentials in a URL are never present on a page a research tool should
    # be reading, and they are a standard way to confuse a parser about which
    # host is really being addressed.
    if parts.username is not None or parts.password is not None:
        return UrlRejected(
            reason="credentials", detail="the URL carries embedded credentials"
        )

    try:
        host = parts.hostname
    except ValueError as exc:  # malformed IPv6 literal, chiefly
        return UrlRejected(reason="no_host", detail=f"unparsable host: {exc}")

    if not host:
        return UrlRejected(reason="no_host", detail="the URL names no host")

    try:
        # Blocking, and this module is called from async code: getaddrinfo can
        # sit for seconds on a slow resolver and would stall the event loop for
        # every other area being researched at the same time.
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port, proto=socket.IPPROTO_TCP
        )
    except (OSError, socket.gaierror) as exc:
        return UrlRejected(reason="unresolvable", detail=f"{host} did not resolve: {exc}")

    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        return UrlRejected(reason="unresolvable", detail=f"{host} resolved to nothing")

    private = sorted(
        address for address in addresses if not _is_publicly_routable(address)
    )
    if private:
        # One bad answer rejects the URL. Which address a client picks out of a
        # multi-answer resolution is not decided here, so "some of them are
        # safe" is not a guarantee of anything.
        return UrlRejected(
            reason="private_address",
            detail=f"{host} resolves to non-public address(es): {', '.join(private)}",
        )

    return UrlAccepted(url=raw_url, host=host)
