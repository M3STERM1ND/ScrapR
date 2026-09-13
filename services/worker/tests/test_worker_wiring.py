"""The worker's wiring, and the two guards that keep a stand-in out of production.

This file is thin because the process is thin. What it checks is the part that
would be expensive to get wrong:

* **Tool availability is closed before any run starts** (`REQ-SEC-015 AC-1`).
* **A category with no key does not register**, and the absence is reported
  rather than discovered later as a run that mysteriously found nothing.
* **A worker with no model refuses to serve users** rather than serving them
  deterministic echo dressed as research.

The third is the one worth being strict about. `DEC-06 §8.1` is explicit that
the absence of a key must refuse to start, because a provider that silently
degrades reintroduces exactly the failure the guard exists to prevent.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import scrapr_worker.main as worker
from scrapr_core.config import Settings
from scrapr_core.lifecycle import PurgeReport
from scrapr_core.llm.anthropic_provider import AnthropicProvider
from scrapr_core.llm.scripted import ScriptedProvider
from scrapr_core.orchestrator.pipeline import CONVERSATION_STAGES, STAGES, UPDATE_STAGES
from scrapr_core.storage.processing import ProcessedUpload
from scrapr_core.tools import RegistryFrozenError, ToolCategory
from scrapr_core.tools.builder import build_registry
from scrapr_core.tools.impl import FixtureTool
from scrapr_worker.main import build_provider, build_runner

ALL_KEYS = {
    "anthropic_api_key": "sk-test",
    "tavily_api_key": "tv-test",
    "fmp_api_key": "fmp-test",
    "adzuna_app_id": "id-test",
    "adzuna_app_key": "key-test",
    "sec_edgar_user_agent": "ScrapR test (dev@example.com)",
}


def settings(**overrides: object) -> Settings:
    """Settings with nothing inherited from the developer's own environment.

    Every provider field is cleared first, and the environment defaults to
    production. A test that passed only because the machine running it happened
    to have a key exported would be worse than no test at all — and one that
    passed only because a local fixture filled the gap would be worse still.
    """
    blank: dict[str, object] = {name: "" for name in ALL_KEYS}
    return Settings().model_copy(
        update={**blank, "allow_fixtures": False, "scrapr_env": "production", **overrides}
    )


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def test_the_registry_is_frozen_before_any_run() -> None:
    """`REQ-SEC-015 AC-1`: retrieved content cannot add, name or reach a tool,
    because by the time anything has been retrieved the registry is closed."""
    registry, _ = build_registry(settings())

    assert registry.is_frozen
    with pytest.raises(RegistryFrozenError):
        registry.register(
            FixtureTool(name="late", category=ToolCategory.WEB_SEARCH, items=())
        )


def test_page_fetch_registers_without_any_key() -> None:
    """`REQ-TOOL-003` needed no question closed and needs no credential. It is
    the one category that is never missing."""
    registry, report = build_registry(settings())

    assert registry.for_category(ToolCategory.PAGE_FETCH)
    assert ToolCategory.PAGE_FETCH not in report.missing


def test_every_keyed_category_registers_when_configured() -> None:
    """`DEC-07`, end to end: five providers, five categories, plus page fetch."""
    registry, report = build_registry(settings(**ALL_KEYS))

    for category in (
        ToolCategory.WEB_SEARCH,
        ToolCategory.NEWS,
        ToolCategory.FINANCIAL,
        ToolCategory.FILINGS,
        ToolCategory.JOBS,
        ToolCategory.PAGE_FETCH,
    ):
        assert registry.for_category(category), f"{category.value} has no provider"

    assert not report.missing, report.summary


@pytest.mark.parametrize(
    ("cleared", "category"),
    [
        pytest.param("tavily_api_key", ToolCategory.WEB_SEARCH, id="search"),
        pytest.param("fmp_api_key", ToolCategory.FINANCIAL, id="financial"),
        pytest.param("sec_edgar_user_agent", ToolCategory.FILINGS, id="filings"),
        pytest.param("adzuna_app_key", ToolCategory.JOBS, id="jobs"),
    ],
)
def test_a_missing_key_removes_its_category_and_says_so(
    cleared: str, category: ToolCategory
) -> None:
    """Degraded and named, never silent.

    The run reports the area as a gap, which is the same path an outage takes —
    so there is one behaviour to reason about rather than two.
    """
    registry, report = build_registry(settings(**{**ALL_KEYS, cleared: ""}))

    assert not registry.for_category(category)
    assert category in report.missing
    assert category.value in report.summary


def test_production_never_falls_back_to_a_fixture() -> None:
    """The tool-layer counterpart of the scripted-provider guard.

    Serving canned items as retrieval would be research nobody did, which is
    the one thing the whole stand-in design exists to keep out of production.
    """
    registry, report = build_registry(settings(scrapr_env="production"))

    assert report.stubbed == ()
    assert not any(
        isinstance(tool, FixtureTool)
        for category in registry.categories()
        for tool in registry.for_category(category)
    )


def test_a_keyless_local_run_with_fixtures_opted_in_still_has_retrieval() -> None:
    """A developer without keys can still exercise the pipeline — by asking to.

    Before this fallback existed, a keyless local run registered page fetch
    alone, found nothing, and closed the version as failed — which reads as a
    research defect rather than an absent credential.
    """
    registry, report = build_registry(
        settings(scrapr_env="local", allow_fixtures=True)
    )

    assert report.stubbed, "nothing stood in for the unserved categories"
    assert registry.for_category(ToolCategory.WEB_SEARCH)
    # And it says plainly what it is, because a local run that looks like
    # research is exactly what a stand-in must not be mistaken for.
    assert "not real research" in report.summary


# --------------------------------------------------------------------------
# The NVIDIA run: fixture data must never reach real research
# --------------------------------------------------------------------------


def _fixtures(registry: object) -> list[FixtureTool]:
    return [
        tool
        for category in registry.categories()  # type: ignore[attr-defined]
        for tool in registry.for_category(category)  # type: ignore[attr-defined]
        if isinstance(tool, FixtureTool)
    ]


def test_fixtures_are_off_unless_explicitly_requested() -> None:
    """Being local is not consent. The NVIDIA run was local, keyed for search,
    jobs and financial data, and silently got a fixture for filings."""
    registry, report = build_registry(settings(scrapr_env="local"))

    assert _fixtures(registry) == []
    assert report.stubbed == ()
    assert ToolCategory.FILINGS in report.missing


def test_the_nvidia_configuration_gets_no_filings_fixture() -> None:
    """The exact shape of the first real run: a model key, Tavily, FMP and
    Adzuna configured, no EDGAR user agent, local environment. Filings must be a
    named gap, not a canned "$1.2bn revenue" item."""
    registry, report = build_registry(
        settings(
            scrapr_env="local",
            anthropic_api_key="sk-test",
            tavily_api_key="tv-test",
            fmp_api_key="fmp-test",
            adzuna_app_id="id-test",
            adzuna_app_key="key-test",
        )
    )

    assert _fixtures(registry) == []
    assert not registry.for_category(ToolCategory.FILINGS)
    assert report.missing == (ToolCategory.FILINGS,)


def test_a_real_model_overrides_the_fixture_opt_in() -> None:
    """Even with the flag set, a run a real model reads is real research."""
    registry, report = build_registry(
        settings(scrapr_env="local", anthropic_api_key="sk-test", allow_fixtures=True)
    )

    assert _fixtures(registry) == []
    assert report.fixtures_refused
    assert "SCRAPR_ALLOW_FIXTURES ignored" in report.summary


def test_production_refuses_to_start_with_fixtures_allowed() -> None:
    problems = settings(scrapr_env="production", allow_fixtures=True).production_problems()

    assert any("SCRAPR_ALLOW_FIXTURES" in problem for problem in problems)


async def test_a_fixture_never_presents_itself_as_a_real_publisher() -> None:
    """The fixture that became "a Reuters filings report" lived on
    `reuters.com`. A fixture now names itself, lives on a reserved host that
    cannot resolve, and tiers `LOWER`."""
    from scrapr_core.db.enums import AuthorityTier
    from scrapr_core.evidence.tiering import PUBLISHERS, assign_tier
    from scrapr_core.tools.builder import FIXTURE_HOST
    from scrapr_core.tools.contract import ToolRequest, ToolResult

    registry, _ = build_registry(settings(scrapr_env="local", allow_fixtures=True))

    for tool in _fixtures(registry):
        outcome = await tool.invoke(ToolRequest(category=tool.category, params={"query": "x"}))
        assert isinstance(outcome, ToolResult)
        for item in outcome.items:
            url = item.source_url or ""
            assert url.startswith(f"https://{FIXTURE_HOST}/")
            assert not any(publisher in url for publisher in PUBLISHERS)
            assert "FIXTURE" in item.source_name
            assert "FIXTURE" in item.content.text
            tier = assign_tier(url=url, category=item.source_category, subject_hosts=frozenset())
            assert tier.tier is AuthorityTier.LOWER


def test_a_configured_category_is_not_stubbed_over_locally() -> None:
    """A real key wins. Otherwise a developer with one provider configured
    would be testing against a fixture without knowing it."""
    registry, report = build_registry(
        settings(scrapr_env="local", tavily_api_key="tv-test")
    )

    assert ToolCategory.WEB_SEARCH not in report.stubbed
    names = [tool.name for tool in registry.for_category(ToolCategory.WEB_SEARCH)]
    assert names == ["tavily_search"]


def test_one_absent_tavily_key_removes_both_of_its_categories() -> None:
    """`DEC-07 §8`, stated as a test because it is the cost of one vendor
    serving two categories: a single outage degrades search and news together,
    and the report will name both in the same run."""
    _, report = build_registry(settings(**{**ALL_KEYS, "tavily_api_key": ""}))

    assert ToolCategory.WEB_SEARCH in report.missing
    assert ToolCategory.NEWS in report.missing


def test_documents_are_not_reported_as_a_missing_provider() -> None:
    """Phase 4 work with no provider by design. Listing it would report a gap
    that is not one, and an operator would chase it."""
    _, report = build_registry(settings(**ALL_KEYS))

    assert ToolCategory.DOCUMENTS not in report.missing


# --------------------------------------------------------------------------
# The provider, and the guard
# --------------------------------------------------------------------------


def test_a_configured_key_gets_the_real_provider() -> None:
    """`DEC-06`. This is what stops the worker refusing to start."""
    provider = build_provider(settings(anthropic_api_key="sk-test"))

    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"


def test_the_configured_models_reach_the_provider() -> None:
    """The tier table is configuration (`DEC-06 §1`), so overriding a row must
    actually change which model serves that tier."""
    provider = build_provider(
        settings(anthropic_api_key="sk-test", model_standard="claude-opus-5")
    )

    assert isinstance(provider, AnthropicProvider)
    from scrapr_core.llm.contract import ModelTier

    assert provider.profile_for(ModelTier.STANDARD).model == "claude-opus-5"


def test_a_model_override_keeps_the_tiers_output_allowance() -> None:
    """Only the model name is configurable. Swapping it must not quietly put
    synthesis back on an allowance it can spend entirely on thinking."""
    from scrapr_core.llm.contract import ModelTier

    provider = build_provider(
        settings(anthropic_api_key="sk-test", model_standard="claude-opus-5")
    )

    assert isinstance(provider, AnthropicProvider)
    assert provider.profile_for(ModelTier.STANDARD).max_tokens == 64_000


def test_a_worker_with_no_model_refuses_to_run_in_production() -> None:
    """`DEC-06 §8.1`. A deterministic echo is a development convenience;
    shipping it would mean serving users research nobody did."""
    with pytest.raises(RuntimeError, match="never run in production"):
        build_provider(settings(scrapr_env="production"))


def test_a_worker_with_no_model_falls_back_locally() -> None:
    """Locally the stand-in is the point: the pipeline stays runnable without
    spending money on every developer's test run."""
    provider = build_provider(settings(scrapr_env="local"))

    assert isinstance(provider, ScriptedProvider)


def test_a_key_satisfies_production_too() -> None:
    """The guard is about the absence of a model, not about the environment."""
    provider = build_provider(
        settings(scrapr_env="production", anthropic_api_key="sk-test")
    )

    assert isinstance(provider, AnthropicProvider)


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


def test_every_stage_has_a_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage with no handler is poisoned on its first attempt, so a wiring
    gap here would fail every run at exactly the step it reached."""
    monkeypatch.setattr(worker, "get_settings", lambda: settings(scrapr_env="local"))

    runner = build_runner("test-worker")

    # Every stage of every kind of run: first research, Update Research, and
    # follow-up research (`DEC-19`, `DEC-20`).
    assert set(runner._handlers) == {*STAGES, *UPDATE_STAGES, *CONVERSATION_STAGES}


# --------------------------------------------------------------------------
# The loop — `REQ-DOC-003`
# --------------------------------------------------------------------------


class _StubRunner:
    """A runner that finds no steps, so the loop is measured on documents."""

    def __init__(self) -> None:
        self.calls = 0

    async def run_one(self) -> bool:
        self.calls += 1
        return False


class _StubProcessor:
    """One upload to extract, then nothing."""

    def __init__(self, remaining: int = 1) -> None:
        self.remaining = remaining
        self.calls = 0

    def run_one(self) -> ProcessedUpload | None:
        self.calls += 1
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        return ProcessedUpload(uuid4(), ready=True, chunks=3)


async def test_the_loop_extracts_documents_as_well_as_running_steps() -> None:
    """The wiring most likely to be dead.

    Everything else in Phase 4 can be correct while `run_forever` never calls
    the processor: uploads would sit in `processing` forever, `has_ready_
    documents` would stay false, and every run would quietly research without
    the reader's files. Nothing else in the suite would notice.
    """
    runner = _StubRunner()
    processor = _StubProcessor(remaining=2)
    stopping = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stopping.set()

    await asyncio.gather(
        worker.run_forever(runner, stopping, processor),  # type: ignore[arg-type]
        stop_soon(),
    )

    assert processor.calls >= 2, "the loop never asked the processor for work"
    assert runner.calls >= 1, "the loop stopped claiming research steps"


async def test_the_loop_still_runs_without_a_processor() -> None:
    """The processor is optional so tests and one-shot runners can omit it.

    If that argument ever became required, an existing caller would break at
    import rather than at the line that needed it.
    """
    runner = _StubRunner()
    stopping = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stopping.set()

    await asyncio.gather(worker.run_forever(runner, stopping), stop_soon())  # type: ignore[arg-type]

    assert runner.calls >= 1


async def test_the_loop_sweeps_deleted_research_once_per_interval() -> None:
    """`DEC-18`: the purge runs from the worker, and not on every tick."""
    runner = _StubRunner()
    sweeps: list[int] = []
    stopping = asyncio.Event()

    def sweep() -> PurgeReport:
        sweeps.append(1)
        return PurgeReport()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stopping.set()

    await asyncio.gather(
        worker.run_forever(runner, stopping, None, sweep),  # type: ignore[arg-type]
        stop_soon(),
    )

    assert sweeps == [1]


class _ExplodingRunner:
    """A runner whose step commit fails, as one racing a purge would."""

    def __init__(self) -> None:
        self.calls = 0

    async def run_one(self) -> bool:
        self.calls += 1
        raise RuntimeError("foreign key violation on commit")


async def test_a_failure_around_a_step_does_not_stop_the_worker() -> None:
    """One step that cannot be recorded must not take every other run down with it."""
    runner = _ExplodingRunner()
    sweeper_failures: list[int] = []
    stopping = asyncio.Event()

    def failing_sweep() -> None:
        sweeper_failures.append(1)
        raise RuntimeError("database unavailable")

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stopping.set()

    # Completing at all is the assertion: an exception escaping `run_forever`
    # would fail the gather, and a loop spinning on the failure without
    # yielding would never let `stop_soon` run.
    await asyncio.wait_for(
        asyncio.gather(
            worker.run_forever(runner, stopping, None, failing_sweep),  # type: ignore[arg-type]
            stop_soon(),
        ),
        timeout=5,
    )

    assert runner.calls >= 1
    assert sweeper_failures == [1]
