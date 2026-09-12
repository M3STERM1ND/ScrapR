"""Registration and dispatch by category.

**The orchestrator asks a category and gets an answer.** It never names a
provider, which is what turns `OPEN-05..09` from blockers into configuration
(`REQ-TOOL-009 AC-2`). Adding a tool is a `register()` call at startup; nothing
in the orchestrator changes.

**The registry freezes before a run starts** (`REQ-SEC-015 AC-1`). Tool
availability is fixed by application config, so retrieved content cannot add,
name or reach a tool — even if a later stage were tricked into trying. A
`register()` after `freeze()` raises rather than being quietly ignored, because
a silently dropped registration is a tool that mysteriously never runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from scrapr_core.tools.contract import (
    TARGETED_CATEGORIES,
    Tool,
    ToolCategory,
    ToolFailure,
    ToolOutcome,
    ToolRequest,
)

__all__ = ["RegistryFrozenError", "ToolRegistry", "UnknownToolError"]


class RegistryFrozenError(RuntimeError):
    """Raised when a tool is registered after the registry was frozen."""


class UnknownToolError(LookupError):
    """Raised when a request pins a tool name that was never registered.

    A missing *name* is a wiring bug and raises. A missing *category* is an
    operational condition — no provider configured yet, which is the normal
    state while `OPEN-05..09` are open — and returns a `ToolFailure` instead.
    """


class ToolRegistry:
    """Holds the tools an application run may use."""

    def __init__(self) -> None:
        self._by_name: dict[str, Tool] = {}
        self._by_category: dict[ToolCategory, list[Tool]] = {}
        self._frozen = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Add a tool. Refused once frozen, and refused on a duplicate name."""
        if self._frozen:
            raise RegistryFrozenError(
                f"cannot register {tool.name!r}: tool availability is fixed at "
                "run start (REQ-SEC-015 AC-1)"
            )
        if tool.name in self._by_name:
            raise ValueError(f"a tool named {tool.name!r} is already registered")

        self._by_name[tool.name] = tool
        self._by_category.setdefault(tool.category, []).append(tool)

    def freeze(self) -> None:
        """Close the registry for the rest of the process. Idempotent."""
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def for_category(self, category: ToolCategory) -> Sequence[Tool]:
        """Every tool that can serve a category, in registration order."""
        return tuple(self._by_category.get(category, ()))

    def categories(self) -> Sequence[ToolCategory]:
        """Which categories have at least one tool."""
        return tuple(sorted(self._by_category))

    def plannable_categories(self) -> Sequence[ToolCategory]:
        """Which categories an area may be planned against.

        This is what the planning stage consults: an area is only planned
        against retrieval that actually exists **and can answer a query**.

        `TARGETED_CATEGORIES` are excluded because they answer a specific URL
        or upload rather than a question. Planning an area against one produces
        `not_found` for every question in it, and the run then reports an area
        that "could not be researched" when the truth is that it was asked the
        wrong kind of question.
        """
        return tuple(
            category
            for category in sorted(self._by_category)
            if category not in TARGETED_CATEGORIES
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        """Run a request against its category, or against a pinned tool.

        A tool that raises anyway is converted to a `ToolFailure`. The contract
        says failures return rather than raise (`REQ-TOOL-010 AC-1`), and a
        third-party client that violates it must not be able to abort a run —
        `REQ-AGENT-009 AC-1` requires unrelated areas to survive.
        """
        tool = self._select(request)
        if tool is None:
            return ToolFailure(
                kind="not_found",
                message=f"no tool registered for category {request.category.value!r}",
                tool="",
                category=request.category,
            )

        try:
            async with asyncio.timeout(request.budget.timeout_seconds):
                return await tool.invoke(request)
        except TimeoutError:
            return ToolFailure(
                kind="timeout",
                message=(
                    f"{tool.name} exceeded its budget of "
                    f"{request.budget.timeout_seconds}s"
                ),
                tool=tool.name,
                category=request.category,
            )
        except Exception as exc:
            # A raising tool must not end the run: REQ-AGENT-009 AC-1 requires
            # unrelated areas to survive one provider misbehaving.
            return ToolFailure(
                kind="error",
                message=f"{tool.name} raised {type(exc).__name__}: {exc}",
                tool=tool.name,
                category=request.category,
            )

    def _select(self, request: ToolRequest) -> Tool | None:
        if request.tool:
            tool = self._by_name.get(request.tool)
            if tool is None:
                raise UnknownToolError(f"no tool named {request.tool!r} is registered")
            if tool.category is not request.category:
                raise UnknownToolError(
                    f"tool {request.tool!r} serves {tool.category.value!r}, "
                    f"not {request.category.value!r}"
                )
            return tool

        candidates = self._by_category.get(request.category)
        return candidates[0] if candidates else None
