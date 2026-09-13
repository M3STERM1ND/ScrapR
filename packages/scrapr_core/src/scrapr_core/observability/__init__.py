"""Recording what the system did, for the people who run it (`REQ-OBS-001..008`).

`telemetry` meters model calls and records tool calls as a step runs; `report`
turns what was recorded into the figures `DEC-24` and `DEC-25` are checked
against. Nothing here is served to a user.
"""

from __future__ import annotations

from scrapr_core.observability.telemetry import (
    TOOL_CALL_COST_MICROS,
    MeteredProvider,
    RunLedger,
    StepTelemetry,
    current_telemetry,
    record_tool_call,
    step_telemetry,
)

__all__ = [
    "TOOL_CALL_COST_MICROS",
    "MeteredProvider",
    "RunLedger",
    "StepTelemetry",
    "current_telemetry",
    "record_tool_call",
    "step_telemetry",
]
