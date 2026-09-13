"""What happens to research at the end of its life (`DEC-17`, `DEC-18`).

Two operations and one rule. **Deleting** hides research at once, stops its
runs and removes its files. **Purging** removes every row once nothing can still
be writing to them. The rule is that expired anonymous research reaches the same
purge through the same code, so there is one deletion implementation rather
than two that drift.
"""

from __future__ import annotations

from scrapr_core.lifecycle.deletion import (
    DeletionOutcome,
    ObjectDeleter,
    delete_research,
    storage_keys_for_sessions,
)
from scrapr_core.lifecycle.purge import PURGE_GRACE, PurgeReport, purge

__all__ = [
    "PURGE_GRACE",
    "DeletionOutcome",
    "ObjectDeleter",
    "PurgeReport",
    "delete_research",
    "purge",
    "storage_keys_for_sessions",
]
