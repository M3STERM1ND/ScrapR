"""PDF and PowerPoint exports (`REQ-EXP-001..010`, `DEC-21`, `DEC-22`).

`document` reads a version into a renderer-agnostic value, `themes` holds the
six fixed designs, `pdf` and `slides` render, and `jobs` is the worker's
claim-render-store loop. No part of it can call a model.
"""

from __future__ import annotations

from scrapr_core.export.document import ExportDocument, ExportSourceError, build_document
from scrapr_core.export.jobs import (
    CONTENT_TYPES,
    ArtifactStore,
    ExportProcessor,
    ProcessedExport,
    export_key,
)
from scrapr_core.export.pdf import render_pdf
from scrapr_core.export.slides import render_pptx
from scrapr_core.export.themes import THEMES, Theme, theme_for

__all__ = [
    "CONTENT_TYPES",
    "THEMES",
    "ArtifactStore",
    "ExportDocument",
    "ExportProcessor",
    "ExportSourceError",
    "ProcessedExport",
    "Theme",
    "build_document",
    "export_key",
    "render_pdf",
    "render_pptx",
    "theme_for",
]
