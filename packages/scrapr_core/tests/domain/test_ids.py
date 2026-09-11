"""UUIDv7 identifiers.

Every ScrapR identifier is a UUIDv7: time-ordered so index locality survives,
and non-enumerable so knowing one id does not let you guess the next
(`REQ-SEC-009 AC-1`).

Generation is application-side. Native `uuidv7()` landed in PostgreSQL 18, so a
migration with `default uuidv7()` fails outright on anything older; generating in
Python removes the database version dependency entirely.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from uuid import UUID

import pytest

from scrapr_core.domain import ids


def test_version_is_7() -> None:
    assert ids.uuid7().version == 7


def test_variant_is_rfc_4122() -> None:
    # The two most significant bits of octet 8 must be 0b10.
    value = ids.uuid7()
    assert (value.bytes[8] & 0b1100_0000) == 0b1000_0000


def test_is_a_real_uuid_and_round_trips_through_text() -> None:
    value = ids.uuid7()
    assert isinstance(value, UUID)
    assert UUID(str(value)) == value


def test_timestamp_reflects_wall_clock_now() -> None:
    before = time.time_ns() // 1_000_000
    value = ids.uuid7()
    after = time.time_ns() // 1_000_000

    encoded = int.from_bytes(value.bytes[:6], "big")

    assert before <= encoded <= after


def test_timestamp_is_readable_back_out() -> None:
    value = ids.uuid7()
    recovered = ids.timestamp_ms(value)
    assert abs(recovered - time.time_ns() // 1_000_000) < 1000


def test_timestamp_ms_rejects_other_uuid_versions() -> None:
    not_v7 = UUID("f81d4fae-7dec-11d0-a765-00a0c91e6bf6")  # v1
    with pytest.raises(ValueError, match="version 7"):
        ids.timestamp_ms(not_v7)


def test_values_are_distinct() -> None:
    generated = [ids.uuid7() for _ in range(10_000)]
    assert len(set(generated)) == 10_000


def test_strictly_increasing_so_index_locality_holds() -> None:
    generated = [ids.uuid7() for _ in range(10_000)]
    assert generated == sorted(generated)
    # Strictly increasing, not merely non-decreasing.
    assert all(a < b for a, b in pairwise(generated))


def test_hex_sorts_the_same_way_as_chronological_order() -> None:
    """Lexicographic ordering of the text form must match generation order.

    This is what lets a database index on the id column stay hot, and what makes
    `order by id` a valid stand-in for `order by created_at`.
    """
    generated = [ids.uuid7() for _ in range(1_000)]
    as_text = [str(value) for value in generated]
    assert as_text == sorted(as_text)


def test_monotonic_within_a_single_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interesting case: many ids inside one clock tick.

    A naive implementation reuses the same 48-bit timestamp and relies on random
    bits alone, which orders ids arbitrarily within the millisecond and can
    collide. Ordering must hold regardless.

    The pinned instant is ahead of wall clock rather than a fixed constant,
    because the generator carries module state: pinning to a past instant would
    look like the clock stepping backwards, and exercise that branch instead of
    this one.
    """
    pinned = time.time_ns() // 1_000_000 + 60_000
    monkeypatch.setattr(ids, "_now_ms", lambda: pinned)

    generated = [ids.uuid7() for _ in range(5_000)]

    assert len(set(generated)) == 5_000
    assert all(a < b for a, b in pairwise(generated))
    assert all(ids.timestamp_ms(v) == pinned for v in generated)


def test_ordering_survives_the_clock_moving_backwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NTP correction or a leap adjustment must not produce a descending id."""
    clock = {"now": time.time_ns() // 1_000_000 + 60_000}
    monkeypatch.setattr(ids, "_now_ms", lambda: clock["now"])

    first = ids.uuid7()
    clock["now"] -= 5_000  # clock jumps backwards five seconds
    second = ids.uuid7()

    assert second > first
    # The rewound clock is ignored rather than encoded, so the timestamp holds.
    assert ids.timestamp_ms(second) == ids.timestamp_ms(first)


def test_unique_and_ordered_under_concurrent_generation() -> None:
    """Workers generate ids in parallel; the counter must be thread safe."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(lambda _: [ids.uuid7() for _ in range(500)], range(8)))

    flattened = [value for batch in batches for value in batch]
    assert len(set(flattened)) == len(flattened)

    for batch in batches:
        assert all(a < b for a, b in pairwise(batch))


def test_new_id_is_the_name_the_rest_of_the_codebase_uses() -> None:
    """Call sites should read as intent, not as an encoding detail."""
    assert ids.new_id().version == 7
