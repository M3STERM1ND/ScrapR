"""Write the OpenAPI document the frontend's client is generated from.

Implementation plan §6.2: **OpenAPI is generated from the API and the TS client
is generated from that.** A route change that breaks the frontend then fails the
build rather than production, which is the only reason the two halves can be
worked on in parallel by two people.

The document is committed. That is deliberate — reviewing a pull request's
effect on the contract means reading a diff, and a generated file nobody can see
change is a contract nobody reviews. CI regenerates it and fails if the checked-in
copy is stale.

    uv run python packages/contracts/export_openapi.py          # write
    uv run python packages/contracts/export_openapi.py --check  # verify
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scrapr_api.main import create_app

OUTPUT = Path(__file__).resolve().parent / "openapi.json"


def render() -> str:
    """The document, formatted so a diff is readable line by line."""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed document is out of date instead of rewriting it",
    )
    args = parser.parse_args(argv)

    current = render()

    if args.check:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if committed != current:
            print(
                "openapi.json is out of date. Run:\n"
                "  uv run python packages/contracts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT.write_text(current, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
