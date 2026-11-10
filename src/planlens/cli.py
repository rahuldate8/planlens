"""Command-line interface: `planlens analyze plan.json`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from planlens import __version__
from planlens.analyze import analyze
from planlens.model import PlanParseError, loads_plan
from planlens.render import render_json, render_text
from planlens.rules import Severity

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planlens",
        description="Find the bottleneck in a PostgreSQL EXPLAIN (FORMAT JSON) plan.",
    )
    parser.add_argument("--version", action="version", version=f"planlens {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="analyze a plan file ('-' for stdin)")
    a.add_argument("plan", help="path to EXPLAIN JSON output, or '-' for stdin")
    a.add_argument("--format", choices=["text", "json"], default="text")
    a.add_argument("--top", type=int, default=3, help="number of hotspots to show")
    a.add_argument("--no-tree", action="store_true", help="hide the plan tree")
    a.add_argument("--sql", help="file containing the SQL, gives the AI more context")
    a.add_argument("--ai", action="store_true", help="add an explanation from Claude")
    a.add_argument("--model", default=None, help="Claude model for --ai")
    a.add_argument(
        "--fail-on",
        choices=[s.value for s in Severity],
        default=None,
        help="exit 1 if any finding is at or above this severity (for CI)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        plan = loads_plan(_read_source(args.plan))
    except (OSError, PlanParseError) as exc:
        print(f"planlens: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = analyze(plan, top=args.top)

    if args.format == "json":
        print(render_json(report))
    else:
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        print(render_text(report, color=color, show_tree=not args.no_tree))

    if args.ai:
        from planlens.ai import DEFAULT_MODEL, AIUnavailableError, explain

        sql = Path(args.sql).read_text() if args.sql else None
        try:
            text = explain(report, sql=sql, model=args.model or DEFAULT_MODEL)
        except AIUnavailableError as exc:
            print(f"planlens: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print("\nAI review\n" + text)

    if args.fail_on:
        floor = Severity(args.fail_on).rank
        if any(f.severity.rank >= floor for f in report.findings):
            return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
