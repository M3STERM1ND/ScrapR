"""The operator report: what the recorded telemetry says (`REQ-OBS-001..006`, `DEC-24`, `DEC-25`).

One read-only pass over a time window, producing the figures the `TBD` values
are checked against and the lists an operator acts on:

* runs — how many, how many failed or came back partial, completion time p50
  and p95 (`TBD-03`, `TBD-04`), cost p50, p95 and max (`TBD-10`, `TBD-11`), and
  every run that spent more than 80% of its ceiling (`REQ-OBS-005 AC-2`);
* failures by stage and cause (`REQ-OBS-001 AC-2`);
* each tool's calls, failure rate, error kinds and latency (`REQ-OBS-002`,
  `REQ-OBS-003 AC-2`);
* paywalled, blocked and unreachable outcomes per source domain
  (`REQ-OBS-006`);
* export render time per format (`TBD-08`, `TBD-09`).

**Operators only** (`REQ-OBS-007 AC-2`). No API route calls this; it runs from
`scrapr-ops`, with database credentials, on a machine an operator controls.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, final
from urllib.parse import urlsplit

from sqlalchemy import ColumnElement, Float, and_, cast, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from scrapr_core.config import get_settings
from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import Accessibility, ExportStatus, RunKind, RunStatus
from scrapr_core.db.models import Export, ResearchRun, Source, ToolInvocation

__all__ = ["OperationsReport", "build_report", "render_text"]

CEILING_ALERT: Final = 0.8
"""A run over this share of its cost ceiling is listed for attention (`DEC-25`)."""


@final
@dataclass(frozen=True, slots=True)
class ToolStat:
    tool: str
    category: str
    calls: int
    failures: int
    p50_ms: float | None
    p95_ms: float | None
    error_kinds: dict[str, int]

    @property
    def failure_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


@final
@dataclass(frozen=True, slots=True)
class OperationsReport:
    since: dt.datetime
    runs: int = 0
    failed: int = 0
    partial: int = 0
    duration_p50_s: float | None = None
    duration_p95_s: float | None = None
    cost_p50_micros: float | None = None
    cost_p95_micros: float | None = None
    cost_max_micros: int | None = None
    expensive_runs: tuple[tuple[str, str, int], ...] = ()
    """(run id, kind, cost) for runs over 80% of their ceiling."""
    failures_by_cause: tuple[tuple[str, str, int], ...] = ()
    """(stage, kind, count)."""
    tools: tuple[ToolStat, ...] = ()
    source_failures: tuple[tuple[str, str, int], ...] = ()
    """(domain, outcome, count)."""
    exports: tuple[tuple[str, int, int, float | None, float | None], ...] = field(default_factory=tuple)
    """(format, ready, failed, p50 ms, p95 ms)."""


def _percentile(
    fraction: float, column: ColumnElement[Any] | InstrumentedAttribute[Any]
) -> ColumnElement[Any]:
    """Postgres `percentile_cont`, so percentiles are computed where the rows are."""
    return func.percentile_cont(fraction).within_group(column)


def build_report(session: Session, *, days: int = 7, now: dt.datetime | None = None) -> OperationsReport:
    since = (now or utcnow()) - dt.timedelta(days=days)
    settings = get_settings()

    finished = and_(ResearchRun.finished_at.is_not(None), ResearchRun.started_at >= since)
    duration = func.extract("epoch", ResearchRun.finished_at - ResearchRun.started_at)
    run_row = session.execute(
        select(
            func.count(ResearchRun.id),
            func.count(ResearchRun.id).filter(ResearchRun.status == RunStatus.FAILED),
            func.count(ResearchRun.id).filter(ResearchRun.status == RunStatus.PARTIAL),
            _percentile(0.5, duration),
            _percentile(0.95, duration),
            _percentile(0.5, cast(func.coalesce(ResearchRun.cost_micros, 0), Float)),
            _percentile(0.95, cast(func.coalesce(ResearchRun.cost_micros, 0), Float)),
            func.max(ResearchRun.cost_micros),
        ).where(finished)
    ).one()

    expensive = tuple(
        (str(run_id), kind.value, int(cost))
        for run_id, kind, cost in session.execute(
            select(ResearchRun.id, ResearchRun.kind, ResearchRun.cost_micros)
            .where(ResearchRun.started_at >= since, ResearchRun.cost_micros.is_not(None))
            .order_by(ResearchRun.cost_micros.desc())
        ).all()
        if cost
        >= CEILING_ALERT
        * (
            settings.update_cost_ceiling_micros
            if kind is RunKind.UPDATE
            else settings.run_cost_ceiling_micros
        )
    )

    failures = tuple(
        (stage or "unknown", kind or "unknown", int(count))
        for stage, kind, count in session.execute(
            select(ResearchRun.failure_stage, ResearchRun.failure_kind, func.count(ResearchRun.id))
            .where(ResearchRun.status == RunStatus.FAILED, ResearchRun.started_at >= since)
            .group_by(ResearchRun.failure_stage, ResearchRun.failure_kind)
            .order_by(func.count(ResearchRun.id).desc())
        ).all()
    )

    tools = _tool_stats(session, since)
    sources = _source_failures(session, since)

    exports = tuple(
        (fmt.value, int(ready), int(failed_count), p50, p95)
        for fmt, ready, failed_count, p50, p95 in session.execute(
            select(
                Export.format,
                func.count(Export.id).filter(Export.status == ExportStatus.READY),
                func.count(Export.id).filter(Export.status == ExportStatus.FAILED),
                _percentile(0.5, Export.render_ms),
                _percentile(0.95, Export.render_ms),
            )
            .where(Export.created_at >= since)
            .group_by(Export.format)
        ).all()
    )

    return OperationsReport(
        since=since,
        runs=int(run_row[0]),
        failed=int(run_row[1]),
        partial=int(run_row[2]),
        duration_p50_s=_float(run_row[3]),
        duration_p95_s=_float(run_row[4]),
        cost_p50_micros=_float(run_row[5]),
        cost_p95_micros=_float(run_row[6]),
        cost_max_micros=int(run_row[7]) if run_row[7] is not None else None,
        expensive_runs=expensive,
        failures_by_cause=failures,
        tools=tools,
        source_failures=sources,
        exports=exports,
    )


def _float(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]


def _tool_stats(session: Session, since: dt.datetime) -> tuple[ToolStat, ...]:
    rows = session.execute(
        select(
            ToolInvocation.tool_name,
            ToolInvocation.tool_category,
            func.count(ToolInvocation.id),
            func.count(ToolInvocation.id).filter(ToolInvocation.status == "failure"),
            _percentile(0.5, ToolInvocation.latency_ms),
            _percentile(0.95, ToolInvocation.latency_ms),
        )
        .where(ToolInvocation.created_at >= since)
        .group_by(ToolInvocation.tool_name, ToolInvocation.tool_category)
        .order_by(func.count(ToolInvocation.id).desc())
    ).all()

    kinds: dict[tuple[str, str], dict[str, int]] = {}
    for tool, category, kind, count in session.execute(
        select(
            ToolInvocation.tool_name,
            ToolInvocation.tool_category,
            ToolInvocation.error_kind,
            func.count(ToolInvocation.id),
        )
        .where(ToolInvocation.created_at >= since, ToolInvocation.error_kind.is_not(None))
        .group_by(ToolInvocation.tool_name, ToolInvocation.tool_category, ToolInvocation.error_kind)
    ).all():
        kinds.setdefault((tool, category), {})[kind] = int(count)

    return tuple(
        ToolStat(
            tool=tool,
            category=category,
            calls=int(calls),
            failures=int(failed),
            p50_ms=_float(p50),
            p95_ms=_float(p95),
            error_kinds=kinds.get((tool, category), {}),
        )
        for tool, category, calls, failed, p50, p95 in rows
    )


def _source_failures(session: Session, since: dt.datetime) -> tuple[tuple[str, str, int], ...]:
    """Inaccessible sources and failed fetches, per domain (`REQ-OBS-006 AC-1`)."""
    counts: dict[tuple[str, str], int] = {}

    for url, accessibility in session.execute(
        select(Source.url, Source.accessibility).where(
            Source.retrieved_at >= since,
            Source.accessibility != Accessibility.ACCESSIBLE,
            Source.url.is_not(None),
        )
    ).all():
        host = urlsplit(url).hostname or "unknown"
        key = (host.lower(), accessibility.value)
        counts[key] = counts.get(key, 0) + 1

    for domain, kind, count in session.execute(
        select(ToolInvocation.source_domain, ToolInvocation.error_kind, func.count(ToolInvocation.id))
        .where(
            ToolInvocation.created_at >= since,
            ToolInvocation.status == "failure",
            ToolInvocation.source_domain.is_not(None),
        )
        .group_by(ToolInvocation.source_domain, ToolInvocation.error_kind)
    ).all():
        key = (domain, kind or "error")
        counts[key] = counts.get(key, 0) + int(count)

    return tuple(
        (domain, outcome, count)
        for (domain, outcome), count in sorted(counts.items(), key=lambda item: -item[1])
    )


def _dollars(micros: float | None) -> str:
    return "n/a" if micros is None else f"${micros / 1_000_000:.3f}"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} ms"


def render_text(report: OperationsReport) -> str:
    """The report as plain text for a terminal."""
    lines: list[str] = [f"ScrapR operations since {report.since:%Y-%m-%d %H:%M} UTC", ""]
    lines.append(
        f"Runs: {report.runs} finished, {report.failed} failed, {report.partial} partial"
    )
    lines.append(
        "Completion: p50 "
        + ("n/a" if report.duration_p50_s is None else f"{report.duration_p50_s:.0f} s")
        + ", p95 "
        + ("n/a" if report.duration_p95_s is None else f"{report.duration_p95_s:.0f} s")
    )
    lines.append(
        f"Cost per run: p50 {_dollars(report.cost_p50_micros)}, p95 {_dollars(report.cost_p95_micros)}, "
        f"max {_dollars(float(report.cost_max_micros) if report.cost_max_micros is not None else None)}"
    )
    _section(lines, "Runs over 80% of their cost ceiling", [
        f"{run_id}  {kind}  {_dollars(float(cost))}" for run_id, kind, cost in report.expensive_runs
    ])
    _section(lines, "Failures by stage and cause", [
        f"{stage:<12} {kind:<32} {count}" for stage, kind, count in report.failures_by_cause
    ])
    _section(lines, "Tools", [
        f"{tool.tool:<24} {tool.category:<12} calls {tool.calls:<5} failures {tool.failures:<4} "
        f"({tool.failure_rate:.0%})  p50 {_ms(tool.p50_ms)}  p95 {_ms(tool.p95_ms)}  "
        + ", ".join(f"{kind}={count}" for kind, count in sorted(tool.error_kinds.items()))
        for tool in report.tools
    ])
    _section(lines, "Source failures by domain", [
        f"{domain:<40} {outcome:<12} {count}" for domain, outcome, count in report.source_failures
    ])
    _section(lines, "Exports", [
        f"{fmt:<5} ready {ready:<5} failed {failed:<4} p50 {_ms(p50)}  p95 {_ms(p95)}"
        for fmt, ready, failed, p50, p95 in report.exports
    ])
    return "\n".join(lines) + "\n"


def _section(lines: list[str], title: str, rows: Sequence[str]) -> None:
    lines.extend(["", title])
    lines.extend(f"  {row}" for row in rows) if rows else lines.append("  none")
