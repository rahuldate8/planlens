"""Human-readable and JSON renderings of a Report."""

from __future__ import annotations

import json

from planlens.analyze import Report
from planlens.model import PlanNode
from planlens.rules import Severity

_ICONS = {Severity.CRITICAL: "✗", Severity.WARNING: "⚠", Severity.INFO: "ℹ"}
_COLORS = {Severity.CRITICAL: "31", Severity.WARNING: "33", Severity.INFO: "36"}


def _c(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def render_tree(root: PlanNode, flagged: set[int], color: bool = False) -> list[str]:
    lines = []
    for node in root.walk():
        indent = "  " * node.depth + ("-> " if node.depth else "")
        timing = ""
        if node.analyzed:
            timing = (
                f"  ({node.total_time_ms:,.1f} ms, rows {node.actual_rows:,.0f}"
                f" est {node.plan_rows:,.0f}, loops {node.loops:,})"
            )
        mark = _c(" ◀", "31", color) if node.node_id in flagged else ""
        lines.append(f"[{node.node_id:>2}] {indent}{node.label}{timing}{mark}")
    return lines


def render_text(report: Report, *, color: bool = False, show_tree: bool = True) -> str:
    out: list[str] = []
    plan = report.plan
    if report.analyzed:
        out.append(
            _c(f"Execution time: {report.total_time_ms:,.1f} ms", "1", color)
            + (f"  (planning {plan.planning_time_ms:,.1f} ms)" if plan.planning_time_ms else "")
        )
    else:
        out.append(
            _c("Plan has no runtime data", "1", color)
            + " - run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) for timings and most checks."
        )

    if report.hotspots:
        out.append("")
        out.append(_c("Where the time went", "1", color))
        for h in report.hotspots:
            where = f"[{h.node.node_id}] {h.node.label}"
            out.append(f"  {h.share:6.1%}  {h.exclusive_ms:>10,.1f} ms  {where}")

    if show_tree:
        out.append("")
        out.append(_c("Plan", "1", color))
        flagged = {f.node.node_id for f in report.findings}
        out.extend("  " + line for line in render_tree(plan.root, flagged, color))

    out.append("")
    if not report.findings:
        out.append(_c("No issues found.", "32", color))
        return "\n".join(out)

    out.append(_c(f"Findings ({len(report.findings)})", "1", color))
    for f in report.findings:
        head = f"{_ICONS[f.severity]} {f.rule_id} {f.title}"
        out.append(f"  {_c(head, _COLORS[f.severity], color)}  [{f.node.node_id}] {f.node.label}")
        out.append(f"      {f.detail}")
        out.append(f"      fix: {f.suggestion}")
    return "\n".join(out)


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)
