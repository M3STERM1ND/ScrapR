"""Tool implementations.

Phase 0 ships fixtures only: `OPEN-05..09` name no provider yet, so a real
implementation would be a guess about an interface nobody has chosen. Each real
provider lands here as its own question closes, next to — not instead of — the
fixture that proves the contract.
"""

from __future__ import annotations

from scrapr_core.tools.impl.fixture import FailingFixtureTool, FixtureTool, fixture_item

__all__ = ["FailingFixtureTool", "FixtureTool", "fixture_item"]
