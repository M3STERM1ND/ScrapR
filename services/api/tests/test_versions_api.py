"""Update Research and version navigation over HTTP (`REQ-VER-001..008`).

Real runner, real stages, fake providers. The update is started the way the
workspace starts it, and read back the way the workspace reads it: the version
list with each version's origin, the old version exactly as it was, and the new
one carrying its What's Changed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pipeline_support import (
    HIRING_TEXT,
    extraction_provider,
    fixture_registry,
    run_pipeline,
    run_update,
    scripted_provider,
    search_tool,
)
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import ClaimType
from scrapr_core.orchestrator.synthesize import DraftClaim, SynthesisDraft
from scrapr_core.tools import ToolCategory, ToolRegistry

pytestmark = pytest.mark.integration

QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)


def saying(revenue: str):  # type: ignore[no-untyped-def]
    def draft(evidence_ids):  # type: ignore[no-untyped-def]
        return SynthesisDraft(
            summary=[
                DraftClaim(
                    text=f"Acme reported {revenue} revenue for FY2025.",
                    claim_type=ClaimType.FACT,
                    evidence_ids=[str(evidence_ids[0])] if evidence_ids else [],
                    is_important=True,
                )
            ],
            sections=[],
        )

    return draft


def moved_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        search_tool(texts=("Acme Corp reported revenue of $1.5bn for fiscal 2025, up 25%.", HIRING_TEXT))
    )
    registry.register(search_tool(name="fixture_news", category=ToolCategory.NEWS, host="apnews.com"))
    registry.freeze()
    return registry


async def researched(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    response = client.post("/v1/research", json={"objective": "How is Acme Corp performing?"})
    assert response.status_code == 202
    await run_pipeline(
        session_factory,
        fixture_registry(),
        scripted_provider("Acme Corp", (QUESTION,), AREAS),
        synthesis=saying("$1.2bn"),
    )
    return str(response.json()["session_id"])


def test_there_is_nothing_to_update_before_a_report_exists(client: TestClient) -> None:
    created = client.post(
        "/v1/research", json={"objective": "How is Acme Corp performing?", "defer_start": True}
    ).json()

    response = client.post(f"/v1/research/{created['session_id']}/update")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_update"


def test_an_update_is_refused_while_research_is_running(client: TestClient) -> None:
    created = client.post("/v1/research", json={"objective": "How is Acme Corp performing?"}).json()

    response = client.post(f"/v1/research/{created['session_id']}/update")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "research_in_progress"


async def test_an_update_creates_a_new_version_and_keeps_the_old_one(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Flow G: G-1 to G-8 through the API."""
    session_id = await researched(client, session_factory)
    before = client.get(f"/v1/research/{session_id}/versions/1").json()
    assert before["origin"] == "initial"
    assert before["change_summary"] is None

    started = client.post(f"/v1/research/{session_id}/update")
    assert started.status_code == 202
    assert started.json()["version_number"] == 2

    # A second press while it runs is refused rather than racing the first.
    assert client.post(f"/v1/research/{session_id}/update").status_code == 409

    header = client.get(f"/v1/research/{session_id}").json()
    assert header["status"] == "pending"
    assert [(v["version_number"], v["origin"]) for v in header["versions"]] == [
        (1, "initial"),
        (2, "update"),
    ]

    await run_update(
        session_factory,
        moved_registry(),
        extraction_provider("Acme reported $1.5bn revenue for FY2025.", "revenue of $1.5bn"),
        synthesis=saying("$1.5bn"),
    )

    # `REQ-VER-008 AC-1`: every version, with its creation time.
    header = client.get(f"/v1/research/{session_id}").json()
    assert [v["version_number"] for v in header["versions"]] == [1, 2]
    assert all(v["created_at"] and v["closed_at"] for v in header["versions"])
    assert header["status"] == "complete"

    # `REQ-VER-002 AC-1`: the original reads back exactly as it did.
    assert client.get(f"/v1/research/{session_id}/versions/1").json() == before

    # `REQ-VER-006 AC-1`: What's Changed is on the new version, typed.
    latest = client.get(f"/v1/research/{session_id}/versions/2").json()
    summary = latest["change_summary"]
    assert latest["origin"] == "update"
    assert summary["available"] is True and summary["has_changes"] is True
    assert summary["compared_with"]["version_number"] == 1
    change = next(item for item in summary["changes"] if item["kind"] == "figure_changed")
    assert change["category"] == "financial_figures"
    assert "1.5" in change["after"]["value"]

    # `REQ-VER-007 AC-1`: the evidence behind the change is in this version.
    evidence_ids = {item["id"] for claim in latest["claims"] for item in claim["evidence"]}
    assert set(change["evidence_ids"]) <= evidence_ids
    # `AC-2`: the prior conclusion is inspectable in the prior version.
    assert change["before"]["claim_id"] in {claim["id"] for claim in before["claims"]}


async def test_the_update_route_is_the_only_new_version_path_without_a_question(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """`REQ-VER-001 AC-2`, read with `DEC-20`: reading research never makes a version."""
    session_id = await researched(client, session_factory)

    for _ in range(3):
        client.get(f"/v1/research/{session_id}")
        client.get(f"/v1/research/{session_id}/versions/1")
        client.get(f"/v1/research/{session_id}/activity")

    assert [v["version_number"] for v in client.get(f"/v1/research/{session_id}").json()["versions"]] == [1]
