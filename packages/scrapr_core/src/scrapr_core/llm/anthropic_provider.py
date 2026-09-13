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

**`max_tokens` is shared between thinking and the answer.** Adaptive thinking
spends from the same allowance the structured output needs, so a tier that
thinks hard over a lot of material needs room for both. The NVIDIA run used all
16,000 tokens thinking over 229 pieces of evidence and never began the report.
`STANDARD` therefore allows 64,000 — an allowance, not a spend: a call is billed
for what it generates, and the run's cost ceiling meters that.

**An allowance that large has to stream.** The SDK refuses a non-streaming
request whose `max_tokens` implies more than ten minutes of generation, so a
profile above `NON_STREAMING_MAX_TOKENS` is sent as a stream and read to the
final message. Smaller tiers keep the plain request.

**Exhaustion is not a refusal.** A response stopped by `max_tokens` without a
complete answer raises `ProviderOutputTruncated`, which says what happened and
carries what the call cost. Retrying the same request cannot fit a larger answer
into the same allowance, so a caller can treat it as permanent.

**The trust boundary is enforced by where text is placed, not by wording.** The
instruction is `Trusted` and goes in `system`; the material is `Untrusted` and
goes in a user message, inside the generated delimiters `envelope.py` draws.
There is no path in this file that puts retrieved text into `system`, and there
must never be one (§9, `REQ-SEC-012 AC-1`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, final

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    Message,
    MessageParam,
    OutputConfigParam,
    TextBlock,
    TextBlockParam,
    ThinkingConfigParam,
    Usage,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

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
    "NON_STREAMING_MAX_TOKENS",
    "AnthropicProvider",
    "ProviderOutputTruncated",
    "ProviderRefusal",
    "TierProfile",
]

# `DEC-06 §1`. Defaults, not constants: every one is overridable by settings.
MODEL_CHEAP = "claude-haiku-4-5"
MODEL_STANDARD = "claude-sonnet-5"
MODEL_DEEP = "claude-opus-5"

NON_STREAMING_MAX_TOKENS: Final = 21_333
"""The largest `max_tokens` the SDK sends without streaming.

The SDK estimates an hour per 128,000 tokens and refuses a plain request it
expects to outlast ten minutes: `3600 * max_tokens / 128_000 > 600`, which is
anything above 21,333. A test pins this against the SDK itself, so an upgrade
that moves the line fails there rather than in production."""


class ProviderRefusal(RuntimeError):
    """The model declined the request (`stop_reason: "refusal"`).

    Raised rather than returned because it is not a research outcome: no
    evidence was gathered and no judgement was made, so there is nothing for a
    stage to degrade gracefully into. It is distinguishable from a transport
    failure so that a caller can tell "the model would not answer" from "the
    model could not be reached" — a distinction `REQ-SEC-010` needs when
    deciding what a user may be told.
    """


class ProviderOutputTruncated(RuntimeError):
    """The model spent its whole `max_tokens` before completing the answer.

    Not a refusal and not a transport failure: the model was answering and ran
    out of room, whether in thinking or partway through the JSON. The same
    request would exhaust the same allowance again, so this is the one provider
    failure a caller should not retry as sent.

    Carries the usage, because the tokens were generated and billed even though
    nothing usable came back — a cost report that dropped them would under-count
    exactly the calls that went wrong.
    """

    def __init__(
        self, message: str, *, model: str, tier: ModelTier, usage: TokenUsage
    ) -> None:
        super().__init__(message)
        self.model = model
        self.tier = tier
        self.usage = usage


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
        # Synthesis thinks over every piece of evidence a run gathered and then
        # writes the whole report; both come out of this allowance. Above
        # `NON_STREAMING_MAX_TOKENS`, so this tier streams.
        max_tokens=64_000,
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


def _output_config(schema: type[BaseModel], profile: TierProfile) -> OutputConfigParam:
    """The response schema, plus effort where the model takes it.

    Built exactly as the SDK's `messages.parse` builds it, so the wire request
    is unchanged. The response is parsed here rather than by that helper
    because the helper validates first: a stream cut off mid-JSON raises at the
    end of the text block, before the event that says *why* it ended arrives,
    and a truncation would surface as a schema error.
    """
    output_format = JSONOutputFormatParam(
        type="json_schema",
        schema=anthropic.transform_schema(TypeAdapter(schema).json_schema()),
    )
    # `effort` is left out entirely when unset — Haiku rejects the key itself.
    if profile.effort is None:
        return OutputConfigParam(format=output_format)
    return OutputConfigParam(format=output_format, effort=profile.effort)


def _parse[T: BaseModel](
    message: Message,
    schema: type[T],
    profile: TierProfile,
    tier: ModelTier,
    usage: TokenUsage,
) -> T:
    """The structured answer, or the reason there is none.

    A response that stopped at `max_tokens` without a complete answer is
    truncation, whether the model never reached the answer or stopped partway
    through it. Any other stop keeps its existing meaning: no text is
    `ProviderRefusal`, and invalid JSON is the schema's own `ValidationError`.
    """
    exhausted = message.stop_reason == "max_tokens"
    text = next(
        (block.text for block in message.content if isinstance(block, TextBlock)),
        None,
    )

    if text is None:
        if exhausted:
            raise _truncated(schema, profile, tier, usage, "before starting it")
        raise ProviderRefusal(
            f"{profile.model} returned no parsable {schema.__name__}; "
            f"stop_reason={message.stop_reason!r}"
        )

    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        if exhausted:
            raise _truncated(schema, profile, tier, usage, "partway through it") from exc
        raise


def _truncated(
    schema: type[BaseModel],
    profile: TierProfile,
    tier: ModelTier,
    usage: TokenUsage,
    where: str,
) -> ProviderOutputTruncated:
    return ProviderOutputTruncated(
        f"{profile.model} exhausted max_tokens={profile.max_tokens} writing "
        f"{schema.__name__}, {where} ({usage.output_tokens} output tokens)",
        model=profile.model,
        tier=tier,
        usage=usage,
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

        message = await self._send(
            profile,
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
            output_config=_output_config(schema, profile),
        )
        usage = TokenUsage(
            input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
            cost_micros=_estimate_micros(message.usage, profile),
        )

        # Always checked before reading content: on a refusal the content is
        # not the answer, and reading it would hand a stage an empty structure
        # it would treat as a finding.
        if message.stop_reason == "refusal":
            raise ProviderRefusal(
                f"{profile.model} declined the request for {schema.__name__}"
            )

        return StructuredResult(
            value=_parse(message, schema, profile, model_tier, usage),
            model=profile.model,
            tier=model_tier,
            usage=usage,
        )

    async def _send(
        self,
        profile: TierProfile,
        *,
        system: list[TextBlockParam],
        messages: list[MessageParam],
        output_config: OutputConfigParam,
    ) -> Message:
        """Send one request, streamed when the allowance is too large not to be.

        The stream is read to its final message rather than consumed
        incrementally: nothing here can use half a structured answer, and the
        accumulated message has the same shape a plain request returns.
        """
        # Absent rather than null: the SDK's `omit` sentinel leaves a parameter
        # out of the wire request entirely, which is what Haiku needs.
        thinking: ThinkingConfigParam | anthropic.Omit = (
            {"type": "adaptive"} if profile.adaptive_thinking else anthropic.omit
        )

        if profile.max_tokens > NON_STREAMING_MAX_TOKENS:
            async with self._client.messages.stream(
                model=profile.model,
                max_tokens=profile.max_tokens,
                system=system,
                messages=messages,
                thinking=thinking,
                output_config=output_config,
            ) as stream:
                return await stream.get_final_message()

        return await self._client.messages.create(
            model=profile.model,
            max_tokens=profile.max_tokens,
            system=system,
            messages=messages,
            thinking=thinking,
            output_config=output_config,
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
