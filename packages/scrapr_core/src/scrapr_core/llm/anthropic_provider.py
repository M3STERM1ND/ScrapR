"""The Anthropic provider (`DEC-06`, closing `OPEN-04`).

What `REQ-TECH-006` asked for was an abstraction, and it already existed: the
stages ask for a `ModelTier` and never learn which model answered. This module
is the first real body behind it.

**The tier table is the decision; this file implements it.** `DEC-06 §1` maps
`CHEAP`/`STANDARD`/`DEEP` onto three models, and every row is settings-backed
so changing one is an environment change rather than a code change.

**The request parameters are not uniform across the three models**, which is
the one genuinely fiddly thing here and the reason a per-tier profile exists
rather than one request builder with flags:

* Sonnet 5 and Opus 5 take `thinking={"type": "adaptive"}` and
  `output_config.effort`.
* Haiku 4.5 takes neither — `effort` is rejected outright, and thinking there
  is the older `budget_tokens` form.

Sending the wrong one is a 400 at runtime, so the profile carries what each
model actually accepts instead of the call site remembering.

**The trust boundary is enforced by where text is placed, not by wording.** The
instruction is `Trusted` and goes in `system`; the material is `Untrusted` and
goes in a user message, inside the generated delimiters `envelope.py` draws.
There is no path in this file that puts retrieved text into `system`, and there
must never be one (§9, `REQ-SEC-012 AC-1`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, final

import anthropic
from anthropic.types import OutputConfigParam, ThinkingConfigParam, Usage
from pydantic import BaseModel

from scrapr_core.llm.contract import (
    ModelTier,
    StructuredResult,
    TokenUsage,
    UntrustedDocument,
)
from scrapr_core.llm.envelope import render_untrusted
from scrapr_core.security.trust import Trusted

type Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""The effort levels the API accepts. Spelled out rather than typed as `str`
so a typo in a profile is a type error here, not a 400 in production."""

__all__ = [
    "MODEL_CHEAP",
    "MODEL_DEEP",
    "MODEL_STANDARD",
    "AnthropicProvider",
    "ProviderRefusal",
    "TierProfile",
]

# `DEC-06 §1`. Defaults, not constants: every one is overridable by settings.
MODEL_CHEAP = "claude-haiku-4-5"
MODEL_STANDARD = "claude-sonnet-5"
MODEL_DEEP = "claude-opus-5"


class ProviderRefusal(RuntimeError):
    """The model declined the request (`stop_reason: "refusal"`).

    Raised rather than returned because it is not a research outcome: no
    evidence was gathered and no judgement was made, so there is nothing for a
    stage to degrade gracefully into. It is distinguishable from a transport
    failure so that a caller can tell "the model would not answer" from "the
    model could not be reached" — a distinction `REQ-SEC-010` needs when
    deciding what a user may be told.
    """


@final
@dataclass(frozen=True, slots=True)
class TierProfile:
    """Which model serves a tier, and what that model will accept.

    `adaptive_thinking` and `effort` are model facts, not preferences. Haiku
    4.5 rejects `output_config.effort` and does not take adaptive thinking;
    Sonnet 5 and Opus 5 take both. Encoding that here is what stops a tier swap
    in config from producing a 400 in production.
    """

    model: str
    max_tokens: int
    adaptive_thinking: bool
    effort: Effort | None
    input_micros_per_token: int
    output_micros_per_token: int


# Prices are $/1M tokens from the published table, expressed as whole micros
# per token — which divides exactly, so there is no rounding to argue about:
# $1.00/1M is 1 micro/token, $25.00/1M is 25.
DEFAULT_PROFILES: dict[ModelTier, TierProfile] = {
    ModelTier.CHEAP: TierProfile(
        model=MODEL_CHEAP,
        max_tokens=8_000,
        adaptive_thinking=False,
        effort=None,
        input_micros_per_token=1,
        output_micros_per_token=5,
    ),
    ModelTier.STANDARD: TierProfile(
        model=MODEL_STANDARD,
        max_tokens=16_000,
        adaptive_thinking=True,
        effort="high",
        input_micros_per_token=2,
        output_micros_per_token=10,
    ),
    ModelTier.DEEP: TierProfile(
        model=MODEL_DEEP,
        max_tokens=16_000,
        adaptive_thinking=True,
        # `DEC-06 §3`: `DEEP` is for work where a wrong answer is expensive and
        # rare enough to pay for, which is exactly when the effort ceiling earns
        # its cost. Nothing in Phase 1 asks for this tier.
        effort="xhigh",
        input_micros_per_token=5,
        output_micros_per_token=25,
    ),
}


def _estimate_micros(usage: Usage, profile: TierProfile) -> int:
    """What one call cost, in millionths of a dollar.

    An estimate, and labelled one: cached reads and cache writes are priced at
    roughly 0.1x and 1.25x of the input rate, and those multipliers are applied
    here rather than ignored. `TBD-10` is unset, so this feeds a placeholder
    ceiling — but it feeds it with real numbers, which is what Phase 8 needs in
    order to have something to measure.
    """
    plain = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    output = int(getattr(usage, "output_tokens", 0) or 0)

    return (
        plain * profile.input_micros_per_token
        + cached * profile.input_micros_per_token // 10
        + written * profile.input_micros_per_token * 125 // 100
        + output * profile.output_micros_per_token
    )


@final
class AnthropicProvider:
    """`LLMProvider` over the Anthropic Messages API."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        profiles: dict[ModelTier, TierProfile] | None = None,
    ) -> None:
        """Build a provider.

        `client` is injectable so the contract tests can run against a recorded
        transport without reaching the network, and so a caller that already
        manages an HTTP pool can hand one in.
        """
        self._client = client or anthropic.AsyncAnthropic()
        self._profiles = dict(profiles or DEFAULT_PROFILES)

    @property
    def name(self) -> str:
        return "anthropic"

    def profile_for(self, tier: ModelTier) -> TierProfile:
        """Which model answers a tier.

        A missing tier raises rather than falling back. A silent downgrade to a
        cheaper model would change what the research is worth without anyone
        deciding to, and `DEC-06 §7.3` mapped every tier precisely so that this
        cannot happen by omission.
        """
        profile = self._profiles.get(tier)
        if profile is None:
            raise KeyError(f"no model configured for tier {tier.value!r}")
        return profile

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        """Answer `instruction` about `untrusted`, shaped as `schema`."""
        profile = self.profile_for(model_tier)

        # Absent rather than null: the SDK's `omit` sentinel leaves a parameter
        # out of the wire request entirely, which is what Haiku needs — sending
        # `effort: null` is still sending `effort`.
        thinking: ThinkingConfigParam | anthropic.Omit = (
            {"type": "adaptive"} if profile.adaptive_thinking else anthropic.omit
        )
        output_config: OutputConfigParam | anthropic.Omit = anthropic.omit
        if profile.effort is not None:
            output_config = OutputConfigParam(effort=profile.effort)

        message = await self._client.messages.parse(
            model=profile.model,
            max_tokens=profile.max_tokens,
            # The instruction is the stable prefix across every call this stage
            # makes, which is exactly what caches well (`DEC-06 §5`). Anything
            # per-run placed ahead of it would forfeit the discount silently.
            system=[
                {
                    "type": "text",
                    "text": str(instruction),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": self._material(untrusted)}],
            output_format=schema,
            thinking=thinking,
            output_config=output_config,
        )

        # Always checked before reading content: on a refusal the content is
        # not the answer, and reading it would hand a stage an empty structure
        # it would treat as a finding.
        if message.stop_reason == "refusal":
            raise ProviderRefusal(
                f"{profile.model} declined the request for {schema.__name__}"
            )

        parsed = message.parsed_output
        if parsed is None:
            raise ProviderRefusal(
                f"{profile.model} returned no parsable {schema.__name__}; "
                f"stop_reason={message.stop_reason!r}"
            )

        return StructuredResult(
            value=parsed,
            model=profile.model,
            tier=model_tier,
            usage=TokenUsage(
                input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
                cost_micros=_estimate_micros(message.usage, profile),
            ),
        )

    @staticmethod
    def _material(untrusted: Sequence[UntrustedDocument]) -> str:
        """Render the material, or say plainly that there is none.

        An empty user message is rejected by the API, and a stage with nothing
        to analyse is a real case — interpretation runs before anything has
        been retrieved. Saying so is better than sending a space.
        """
        rendered = render_untrusted(untrusted)
        return rendered or "No material was supplied."
