"""The trust boundary between agent instructions and retrieved content.

`REQ-SEC-012 AC-1` requires this boundary be enforced structurally rather than by
instruction wording, and implementation plan section 9 sets out the mechanism:
two distinct types with no conversion function between them.

    Trusted    system policy, agent instructions, application logic
    Untrusted  every web page, search result and uploaded document

`Untrusted` is deliberately not a `str` subclass, and is actively hostile to
becoming one. Every implicit path to a string raises `UntrustedContentError`, so
retrieved content reaching an instruction channel fails loudly at the first test
that executes it instead of quietly becoming a prompt injection in production.

Reading `.text` is the single deliberate escape hatch. It is greppable, which is
what code review relies on, and it is the path evidence extraction uses.

Enforcement is the type plus `mypy --strict` over this package, `llm` and
`tools`. It is not a lint rule: ruff and flake8 are not type-aware and cannot
know what a variable holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, final

__all__ = [
    "SourceRef",
    "Trusted",
    "Untrusted",
    "UntrustedContentError",
]


class UntrustedContentError(RuntimeError):
    """Raised when untrusted content is pushed toward an instruction channel."""


@final
@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a piece of untrusted content came from.

    Carries no retrieved content itself, only provenance, so unlike `Untrusted`
    it stays an ordinary value that is safe to log, format and stringify.
    """

    kind: str
    """Broad origin class: ``web``, ``document``, ``search``, ``filing``, ..."""

    locator: str
    """URL, or upload id plus locator, or provider record id."""

    def __str__(self) -> str:
        return f"{self.kind}:{self.locator}"


@final
class Trusted(str):
    """Text the application authored: system policy, task instructions, config.

    An ordinary `str`. The point of the type is not to restrict it but to make
    the *other* side of the boundary impossible to pass here by accident, since
    `LLMProvider.complete_structured` accepts `Trusted` for instructions and
    `Untrusted` only through a separate parameter.
    """

    __slots__ = ()


@final
class Untrusted:
    """Retrieved content. Data to be analyzed, never a directive to follow.

    Not a `str`, and resistant to becoming one::

        str(content)        -> UntrustedContentError
        f"{content}"        -> UntrustedContentError
        f"{content:>10}"    -> UntrustedContentError
        "%s" % content      -> UntrustedContentError
        print(content)      -> UntrustedContentError
        bytes(content)      -> UntrustedContentError
        "a" + content       -> TypeError (not a str, so no concatenation)

        content.text        -> the text, deliberately and greppably
        repr(content)       -> safe: origin and length, never the content
    """

    __slots__ = ("_origin", "_text")

    # Declared for the type checker. `__slots__` plus the raising `__setattr__`
    # below mean these are only ever written through `object.__setattr__`, which
    # mypy cannot see, so without these annotations every read is an unknown
    # attribute returning Any.
    _text: str
    _origin: SourceRef

    def __init__(self, text: str, origin: SourceRef) -> None:
        # Bypass the frozen-style __setattr__ below during construction.
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_origin", origin)

    @property
    def text(self) -> str:
        """The retrieved text. The single deliberate escape hatch.

        Every use of this property is a place where untrusted content enters
        ordinary code, so it is intended to be easy to grep for and to review.
        """
        return self._text

    @property
    def origin(self) -> SourceRef:
        """Provenance of the content. Safe to log."""
        return self._origin

    def __str__(self) -> NoReturn:
        raise UntrustedContentError(
            "Untrusted content cannot be stringified. Pass it to "
            "LLMProvider.complete_structured(untrusted=...) or read .text explicitly."
        )

    def __format__(self, format_spec: str, /) -> NoReturn:
        # Implemented with its real signature rather than aliased to __str__.
        # An alias has the wrong arity, so format(x, ">10") would raise TypeError
        # instead of this error: still loud, but the wrong failure to debug.
        raise UntrustedContentError(
            "Untrusted content cannot be formatted. Pass it to "
            "LLMProvider.complete_structured(untrusted=...) or read .text explicitly."
        )

    def __bytes__(self) -> NoReturn:
        raise UntrustedContentError(
            "Untrusted content cannot be encoded. Read .text explicitly if you "
            "genuinely need the bytes."
        )

    def __repr__(self) -> str:
        # The sanctioned channel for logging and debugging: provenance and size,
        # never the payload. Length is safe to disclose; content is not.
        return f"Untrusted(origin={self._origin!r}, chars={len(self._text)})"

    def __setattr__(self, name: str, value: object, /) -> NoReturn:
        raise AttributeError(
            f"Untrusted is immutable; cannot set {name!r}. Construct a new "
            "instance instead."
        )

    def __delattr__(self, name: str, /) -> NoReturn:
        raise AttributeError(f"Untrusted is immutable; cannot delete {name!r}.")
