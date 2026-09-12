"""The AI provider abstraction (`REQ-TECH-006`) and the Phase 0 fake.

One of the three packages `mypy --strict` covers with no escape hatches
(implementation plan §9): this is where trusted instructions and untrusted
material meet, and the signature is what keeps them apart.
"""

from __future__ import annotations

from scrapr_core.llm.anthropic_provider import (
    AnthropicProvider,
    ProviderRefusal,
    TierProfile,
)
from scrapr_core.llm.contract import (
    LLMProvider,
    ModelTier,
    StructuredResult,
    TokenUsage,
    UntrustedDocument,
)
from scrapr_core.llm.envelope import MATERIAL_PREAMBLE, render_untrusted
from scrapr_core.llm.fake import FakeLLMProvider, NoCannedResponseError, RecordedCall

__all__ = [
    "MATERIAL_PREAMBLE",
    "AnthropicProvider",
    "FakeLLMProvider",
    "LLMProvider",
    "ModelTier",
    "NoCannedResponseError",
    "ProviderRefusal",
    "RecordedCall",
    "StructuredResult",
    "TierProfile",
    "TokenUsage",
    "UntrustedDocument",
    "render_untrusted",
]
