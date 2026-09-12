"""The uniform tool contract, the registry, and the tools themselves.

`packages/scrapr_core/tools` is one of the three packages `mypy --strict` covers
with no escape hatches (implementation plan §9), because this is where retrieved
content enters the system and a wrong call must be a type error rather than a
runtime surprise.
"""

from __future__ import annotations

from scrapr_core.tools.contract import (
    TARGETED_CATEGORIES,
    FailureKind,
    Tool,
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolItem,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.registry import (
    RegistryFrozenError,
    ToolRegistry,
    UnknownToolError,
)

__all__ = [
    "TARGETED_CATEGORIES",
    "FailureKind",
    "RegistryFrozenError",
    "Tool",
    "ToolBudget",
    "ToolCategory",
    "ToolFailure",
    "ToolItem",
    "ToolOutcome",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "UnknownToolError",
]
