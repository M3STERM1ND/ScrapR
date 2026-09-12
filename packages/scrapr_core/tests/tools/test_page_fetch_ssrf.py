"""The SSRF suite for page fetch (implementation plan §15, `REQ-SEC-015 AC-2`).

Page fetch is the only tool that opens a connection to an address chosen
outside the application, which makes it the only tool that can be pointed at
the inside of the network it runs in. The plan states the rule in §9: it
"refuses private, link-local and loopback addresses, and re-validates after
every redirect".

Two halves, and both are needed. Refusing the address closes the direct attempt
— `http://169.254.169.254/` asking a cloud metadata service for credentials.
Re-validating every hop closes the interesting one: a public host that answers
`302 Location: http://127.0.0.1/`, which a client following redirects on its
own would walk straight through, having validated only the URL it was handed.

These tests do not touch the network. Literal addresses need no resolver, and
the hostname cases replace `getaddrinfo` so that what is under test is the
verdict, not the DNS of whoever is running the suite.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

from scrapr_core.tools.impl.net import (
    UrlRejected,
    resolve_and_validate,
)

# Each address with the threat it stands for, so a failure names the hole.
PRIVATE_ADDRESSES = [
    pytest.param("http://127.0.0.1/admin", id="loopback-v4"),
    pytest.param("http://[::1]/admin", id="loopback-v6"),
    pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
    pytest.param("http://10.0.0.5/internal", id="rfc1918-10"),
    pytest.param("http://172.16.0.1/internal", id="rfc1918-172"),
    pytest.param("http://192.168.1.1/router", id="rfc1918-192"),
    pytest.param("http://0.0.0.0/", id="unspecified"),
    pytest.param("http://[::ffff:127.0.0.1]/", id="v4-mapped-loopback"),
    pytest.param("http://[fe80::1]/", id="link-local-v6"),
    pytest.param("http://[fc00::1]/", id="unique-local-v6"),
]


@pytest.fixture
def resolves_to(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Point every hostname at an address of the test's choosing.

    The guard resolves before it judges, so testing a *name* means controlling
    what it resolves to. Doing that here rather than relying on a real lookup
    is what keeps the suite honest offline and identical in CI.
    """

    def install(address: str, family: int = socket.AF_INET) -> None:
        async def fake_getaddrinfo(
            host: str, port: object, **kwargs: object
        ) -> list[tuple[object, ...]]:
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 80))]

        class _Loop:
            @staticmethod
            def getaddrinfo(*args: object, **kwargs: object) -> object:
                return fake_getaddrinfo(str(args[0]), args[1] if len(args) > 1 else None)

        monkeypatch.setattr(
            "scrapr_core.tools.impl.net.asyncio.get_running_loop", lambda: _Loop()
        )

    yield install


# --------------------------------------------------------------------------
# The addresses themselves
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", PRIVATE_ADDRESSES)
async def test_a_private_address_is_refused(url: str) -> None:
    """`REQ-SEC-015 AC-2`. Each of these reaches something the agent must not."""
    verdict = await resolve_and_validate(url)

    assert isinstance(verdict, UrlRejected), f"{url} was allowed"
    assert verdict.reason == "private_address"


async def test_a_public_address_is_allowed() -> None:
    """The guard has to let the web through, or the tool is a no-op.

    A literal public address, so the assertion does not depend on a resolver
    agreeing with the test.
    """
    verdict = await resolve_and_validate("https://93.184.215.14/")

    assert not isinstance(verdict, UrlRejected)


async def test_a_hostname_pointing_at_loopback_is_refused(resolves_to: Any) -> None:
    """The reason the check is on the resolved address and not on the name.

    Nothing about `totally-normal.example` looks dangerous. A name-based
    deny-list passes it, and the connection lands on the loopback interface.
    """
    resolves_to("127.0.0.1")

    verdict = await resolve_and_validate("http://totally-normal.example/")

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == "private_address"


async def test_one_private_answer_rejects_the_whole_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name answering with both a public and a private address is refused.

    Which answer a client picks is not decided here, so "one of them is safe"
    guarantees nothing about the connection that actually gets made.
    """

    async def mixed(host: str, port: object, **kwargs: object) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.215.14", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80)),
        ]

    class _Loop:
        @staticmethod
        def getaddrinfo(*args: object, **kwargs: object) -> object:
            return mixed(str(args[0]), None)

    monkeypatch.setattr(
        "scrapr_core.tools.impl.net.asyncio.get_running_loop", lambda: _Loop()
    )

    verdict = await resolve_and_validate("http://split-horizon.example/")

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == "private_address"


# --------------------------------------------------------------------------
# Everything that is not a web page
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        pytest.param("file:///etc/passwd", "scheme", id="file"),
        pytest.param("gopher://example.com/", "scheme", id="gopher"),
        pytest.param("ftp://example.com/x", "scheme", id="ftp"),
        pytest.param("data:text/html,<b>x</b>", "scheme", id="data"),
    ],
)
async def test_a_non_web_scheme_is_refused(url: str, reason: str) -> None:
    """`file://` reads the worker's own disk; the rest are smuggling vectors."""
    verdict = await resolve_and_validate(url)

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == reason


async def test_embedded_credentials_are_refused(resolves_to: Any) -> None:
    """A standard way to confuse a parser about which host is really addressed,
    and never present on a page worth researching."""
    resolves_to("93.184.215.14")

    verdict = await resolve_and_validate("http://user:pw@example.test/")

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == "credentials"


async def test_a_url_with_no_host_is_refused() -> None:
    verdict = await resolve_and_validate("http:///nowhere")

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == "no_host"


async def test_a_name_that_does_not_resolve_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reported as `not_found` by the caller rather than as an error: a name
    that does not exist is a dead citation, not a broken fetcher."""

    class _Loop:
        @staticmethod
        def getaddrinfo(*args: object, **kwargs: object) -> object:
            raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(
        "scrapr_core.tools.impl.net.asyncio.get_running_loop", lambda: _Loop()
    )

    verdict = await resolve_and_validate("http://nope.invalid/")

    assert isinstance(verdict, UrlRejected)
    assert verdict.reason == "unresolvable"
