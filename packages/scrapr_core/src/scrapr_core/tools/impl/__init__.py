"""Tool implementations.

Fixtures, plus the real tools whose open questions have closed. `OPEN-05..09`
each name a vendor nobody has chosen, so those five tools are still fixtures;
page fetch names none — a URL is a URL — and is real (`REQ-TOOL-003`).

Each real provider lands here as its own question closes, next to, not instead
of, the fixture that proves the contract: the fixture is what the orchestrator
is written against, and the cassette is what proves one vendor is handled.
"""

from __future__ import annotations

from scrapr_core.tools.impl.fixture import FailingFixtureTool, FixtureTool, fixture_item
from scrapr_core.tools.impl.page_fetch import PageFetchTool, extract_text

__all__ = [
    "FailingFixtureTool",
    "FixtureTool",
    "PageFetchTool",
    "extract_text",
    "fixture_item",
]
