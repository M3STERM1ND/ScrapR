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

import pytest

import scrapr_worker.main as worker
from scrapr_core.config import Settings
from scrapr_core.llm.anthropic_provider import AnthropicProvider
from scrapr_core.llm.scripted import ScriptedProvider
from scrapr_core.orchestrator.pipeline import STAGES
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
    blank = {name: "" for name in ALL_KEYS}
    return Settings().model_copy(
        update={**blank, "scrapr_env": "production", **overrides}
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


def test_a_keyless_local_run_still_has_retrieval() -> None:
    """A developer without keys must be able to run the pipeline.

    Before this fallback existed, a keyless local run registered page fetch
    alone, found nothing, and closed the version as failed — which reads as a
    research defect rather than an absent credential.
    """
    registry, report = build_registry(settings(scrapr_env="local"))

    assert report.stubbed, "nothing stood in for the unserved categories"
    assert registry.for_category(ToolCategory.WEB_SEARCH)
    # And it says plainly what it is, because a local run that looks like
    # research is exactly what a stand-in must not be mistaken for.
    assert "not real research" in report.summary


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

    assert set(runner._handlers) == set(STAGES)
