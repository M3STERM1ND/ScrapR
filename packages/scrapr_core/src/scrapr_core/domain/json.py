"""A precise type for JSON-shaped data.

`mypy --strict` runs with `disallow_any_explicit` over `security`, `llm` and
`tools` (implementation plan §9), so `Mapping[str, Any]` — the obvious spelling
for a provider payload or a tool parameter — is not available there. That is the
point: `Any` silences the checker exactly where the checker is the enforcement
mechanism.

This alias is the replacement. It describes what JSON actually is, so a value
that came from a provider cannot be passed somewhere structured data is expected
without the checker noticing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["JsonMapping", "JsonValue"]

type JsonValue = (
    str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None
)
"""Any value that survives a JSON round trip."""

type JsonMapping = Mapping[str, JsonValue]
"""A JSON object. Read-only: parameters and payloads are passed, not edited."""
