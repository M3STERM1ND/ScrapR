"""UUIDv7 identifier generation (RFC 9562).

Every ScrapR identifier is a UUIDv7. Two properties earn it that place:

* **Time-ordered**, so a B-tree index on a primary key stays hot instead of
  scattering writes across the whole index the way UUIDv4 does.
* **Non-enumerable**, so holding one id tells you nothing about the next
  (`REQ-SEC-009 AC-1`). Knowing an identifier is also insufficient for access
  without ownership (`REQ-SEC-002`); this is defence in depth, not the control.

**Generated application-side, not by the database.** Native ``uuidv7()`` exists
only in PostgreSQL 18 and later, so a migration carrying ``default uuidv7()``
fails outright on anything older. Generating here removes that version
dependency entirely.

**Implemented here rather than taken from the standard library.** CPython gained
``uuid.uuid7()`` in 3.14, but the project supports 3.12 and up. One
implementation across every supported version is worth more than twenty lines
saved on the newest one, because monotonicity behaviour then cannot vary with
the interpreter a developer happens to run.

Layout, most significant bit first::

    48 bits   unix timestamp, milliseconds
     4 bits   version (7)
    12 bits   counter high, or random on a fresh millisecond
     2 bits   variant (0b10)
    62 bits   counter low plus random tail
"""

from __future__ import annotations

import secrets
import threading
import time
from uuid import UUID

__all__ = ["new_id", "timestamp_ms", "uuid7"]

_VERSION = 7

# The counter occupies rand_a (12 bits) plus the top of rand_b (a further 30),
# giving 42 bits of headroom inside a single millisecond. That is far more than
# any process will consume in one tick, so the guard below effectively never
# fires; it exists so that "effectively never" is not load-bearing.
_COUNTER_BITS = 42
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1

# The counter is seeded randomly on each new millisecond rather than at zero, so
# ids stay unguessable. The seed is kept in the low half of the range to leave
# room to increment.
_SEED_BITS = _COUNTER_BITS - 1

_RANDOM_TAIL_BITS = 32

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def _now_ms() -> int:
    """Wall clock in milliseconds. Patched in tests to pin or rewind time."""
    return time.time_ns() // 1_000_000


def uuid7() -> UUID:
    """Return a fresh UUIDv7.

    Strictly increasing within a process, including across many calls inside one
    millisecond and across a clock that steps backwards.
    """
    global _last_ms, _counter

    with _lock:
        now = _now_ms()

        if now > _last_ms:
            # Fresh millisecond: reseed the counter randomly.
            _last_ms = now
            _counter = secrets.randbits(_SEED_BITS)
        else:
            # Same millisecond, or the clock moved backwards. Either way, keep
            # the previous timestamp and advance the counter, so ordering never
            # depends on the clock behaving monotonically.
            _counter += 1
            if _counter > _COUNTER_MAX:
                # Counter exhausted inside one tick. Borrow from the next
                # millisecond rather than emit a descending or duplicate id.
                _last_ms += 1
                _counter = secrets.randbits(_SEED_BITS)

        timestamp = _last_ms
        counter = _counter

    tail = secrets.randbits(_RANDOM_TAIL_BITS)

    value = timestamp << 80
    value |= _VERSION << 76
    value |= (counter >> 30) << 64          # counter high -> rand_a (12 bits)
    value |= 0b10 << 62                     # RFC 4122 variant
    value |= (counter & 0x3FFF_FFFF) << 32  # counter low -> top of rand_b
    value |= tail                           # random tail

    return UUID(int=value)


def new_id() -> UUID:
    """Alias for :func:`uuid7`, for call sites that read better as intent."""
    return uuid7()


def timestamp_ms(value: UUID) -> int:
    """Extract the creation timestamp, in milliseconds since the unix epoch.

    Raises:
        ValueError: if ``value`` is not a UUIDv7 and therefore carries no
            recoverable timestamp.
    """
    if value.version != _VERSION:
        raise ValueError(
            f"expected a UUID version 7, got version {value.version}; "
            "only v7 encodes a timestamp in this layout"
        )
    return int.from_bytes(value.bytes[:6], "big")
