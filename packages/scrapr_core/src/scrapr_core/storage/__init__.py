"""Object storage and document extraction — Phase 4.

`DEC-12` makes the S3 API the contract, so `objects` is one client against two
environments. `extract` turns a stored file into chunks with locators, because
implementation plan §10 requires a citation resolve to a place in a document
rather than to the document.

The application never handles file bytes on the request path: uploads go by
presigned PUT direct to storage, and extraction reads them in the worker.
"""

from __future__ import annotations

from scrapr_core.storage.extract import (
    ACCEPTED_TYPES,
    CHUNK_CHARS,
    ExtractedChunk,
    ExtractionError,
    accepted_types_message,
    describe_locator,
    detect_content_type,
    extract_chunks,
)
from scrapr_core.storage.objects import (
    GET_EXPIRY_SECONDS,
    PUT_EXPIRY_SECONDS,
    ObjectStore,
    PresignedUpload,
    StorageError,
    storage_key_for,
)

__all__ = [
    "ACCEPTED_TYPES",
    "CHUNK_CHARS",
    "GET_EXPIRY_SECONDS",
    "PUT_EXPIRY_SECONDS",
    "ExtractedChunk",
    "ExtractionError",
    "ObjectStore",
    "PresignedUpload",
    "StorageError",
    "accepted_types_message",
    "describe_locator",
    "detect_content_type",
    "extract_chunks",
    "storage_key_for",
]
