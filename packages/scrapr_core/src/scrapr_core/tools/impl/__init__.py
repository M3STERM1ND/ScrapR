"""Tool implementations.

Fixtures, plus the real tools whose open questions have closed. `DEC-07` closed
`OPEN-05..09`, so all five keyed providers are here; `REQ-TOOL-003` never
needed a question closed and was built first.

Each real provider sits beside — not instead of — the fixture that proves the
contract. The fixture is what the orchestrator is written against and what the
pipeline tests run on; the cassette is what proves one vendor is handled. §15
has said this since Phase 0 and there was nothing to record until now.
"""

from __future__ import annotations

from scrapr_core.tools.impl.adzuna import AdzunaTool
from scrapr_core.tools.impl.edgar import SecEdgarTool
from scrapr_core.tools.impl.fixture import FailingFixtureTool, FixtureTool, fixture_item
from scrapr_core.tools.impl.fmp import FinancialModelingPrepTool
from scrapr_core.tools.impl.page_fetch import PageFetchTool, extract_text
from scrapr_core.tools.impl.tavily import TavilyTool, news_tool, search_tool

__all__ = [
    "AdzunaTool",
    "FailingFixtureTool",
    "FinancialModelingPrepTool",
    "FixtureTool",
    "PageFetchTool",
    "SecEdgarTool",
    "TavilyTool",
    "extract_text",
    "fixture_item",
    "news_tool",
    "search_tool",
]
