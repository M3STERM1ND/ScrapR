"""Turning evidence into claims, sections and a citation map.

Phase 0 ships only the validation gate, because the gate is a seam — a version
is judged before it is shown — while synthesis proper is Phase 1.
"""

from __future__ import annotations

from scrapr_core.synthesis.validation import (
    GateViolation,
    ValidationReport,
    validate_version,
)

__all__ = ["GateViolation", "ValidationReport", "validate_version"]
