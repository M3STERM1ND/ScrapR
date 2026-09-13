"""`scrapr-ops report` end to end, against the test database (`REQ-OBS-001..006`)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

import scrapr_worker.ops as ops

pytestmark = pytest.mark.integration


def test_the_report_command_prints_every_section(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ops, "build_engine", lambda: migrated_engine)

    assert ops.main(["report", "--days", "3"]) == 0

    printed = capsys.readouterr().out
    for heading in (
        "ScrapR operations since",
        "Runs:",
        "Cost per run:",
        "Runs over 80% of their cost ceiling",
        "Failures by stage and cause",
        "Tools",
        "Source failures by domain",
        "Exports",
    ):
        assert heading in printed


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        ops.main([])
