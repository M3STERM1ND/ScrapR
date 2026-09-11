"""Who is asking. The value every repository call is scoped by.

`REQ-SEC-002 AC-2` requires ownership be enforced server-side on every access,
and implementation plan §4.3 puts that enforcement in one place: repositories
take an `OwnerContext` and every query filters on it. Route handlers never
filter, because a rule applied in twenty handlers is a rule that is missing from
the twenty-first.

**Exactly one owner, mirroring the `one_owner` check constraint.** A context
carrying both a user and an anonymous session is ambiguous, and ambiguous
ownership is how research leaks between accounts. Constructing one raises.

In Phase 1 the owner is always an anonymous session; in Phase 5 it becomes
either. Building the type now is what stops authorization being retrofitted into
query paths later, which is the retrofit that never quite reaches every path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final
from uuid import UUID

__all__ = ["OwnerContext"]


@final
@dataclass(frozen=True, slots=True, repr=False)
class OwnerContext:
    """The owner on whose behalf a repository call is made."""

    user_id: UUID | None = None
    anonymous_session_id: UUID | None = None

    def __post_init__(self) -> None:
        owners = (self.user_id, self.anonymous_session_id)
        if sum(owner is not None for owner in owners) != 1:
            raise ValueError(
                "an OwnerContext has exactly one owner: a user id or an "
                f"anonymous session id, got {owners!r}"
            )

    @classmethod
    def for_user(cls, user_id: UUID) -> OwnerContext:
        """Ownership by an account (`REQ-AUTH-005`)."""
        return cls(user_id=user_id)

    @classmethod
    def for_anonymous(cls, anonymous_session_id: UUID) -> OwnerContext:
        """Ownership by an unauthenticated session (`REQ-AUTH-001`)."""
        return cls(anonymous_session_id=anonymous_session_id)

    @property
    def is_anonymous(self) -> bool:
        return self.anonymous_session_id is not None

    def __repr__(self) -> str:
        """Identify the kind of owner without disclosing the id.

        For an anonymous session the id *is* the access-control key, so it does
        not belong in a log line anyone tailing stdout can read. The generated
        dataclass repr would print it, which is why it is replaced rather than
        supplemented — `str()` falls back to this too.
        """
        kind = "anonymous" if self.is_anonymous else "user"
        return f"OwnerContext({kind})"
