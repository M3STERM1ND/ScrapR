"""`scrapr-ops`: the operator's view of a running deployment (`REQ-OBS-001..008`).

    scrapr-ops report [--days N]

Reads the database the process is configured for and prints the operations
report: run outcomes and timings, cost against the ceilings, failures by cause,
tool failure rates and latency, source failures by domain, export timings.

A command, not an endpoint, on purpose (`REQ-OBS-007 AC-2`): raw queries,
failure detail and cost data are for the people running ScrapR, and the way to
keep them from users is to have no route that serves them. Access control is
the database credential the operator already holds.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from scrapr_core.db.engine import build_engine, build_session_factory
from scrapr_core.observability.report import build_report, render_text

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrapr-ops", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="print the operations report")
    report.add_argument("--days", type=int, default=7, help="window in days (default 7)")
    arguments = parser.parse_args(argv)

    factory = build_session_factory(build_engine())
    with factory() as session:
        sys.stdout.write(render_text(build_report(session, days=arguments.days)))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
