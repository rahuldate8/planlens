"""Run the rules over a plan and summarise where the time went."""

from __future__ import annotations

from dataclasses import dataclass, field

from planlens.model import PlanNode, QueryPlan
from planlens.rules import DEFAULT_RULES, Context, Finding, Rule, Thresholds


@dataclass
class Hotspot:
    node: PlanNode
    exclusive_ms: float
    share: float

    def to_dict(self) -> dict:
        return {
            "node_id": self.node.node_id,
            "node": self.node.label,
            "exclusive_ms": round(self.exclusive_ms, 3),
            "share": round(self.share, 4),
        }


@dataclass
class Report:
    plan: QueryPlan
    total_time_ms: float
    hotspots: list[Hotspot] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def analyzed(self) -> bool:
        return self.plan.analyzed

    def to_dict(self) -> dict:
        return {
            "analyzed": self.analyzed,
            "planning_time_ms": self.plan.planning_time_ms,
            "execution_time_ms": self.plan.execution_time_ms,
            "total_time_ms": round(self.total_time_ms, 3),
            "hotspots": [h.to_dict() for h in self.hotspots],
            "findings": [f.to_dict() for f in self.findings],
        }


def analyze(
    plan: QueryPlan,
    *,
    rules: list[Rule] | None = None,
    thresholds: Thresholds | None = None,
    top: int = 3,
) -> Report:
    thresholds = thresholds or Thresholds()
    total = plan.execution_time_ms or plan.root.total_time_ms
    ctx = Context(plan=plan, thresholds=thresholds, total_time_ms=total)

    hotspots: list[Hotspot] = []
    if plan.analyzed and total > 0:
        ranked = sorted(plan.nodes(), key=lambda n: n.exclusive_time_ms, reverse=True)
        hotspots = [
            Hotspot(node=n, exclusive_ms=n.exclusive_time_ms, share=n.exclusive_time_ms / total)
            for n in ranked[:top]
            if n.exclusive_time_ms > 0
        ]

    findings: list[Finding] = []
    for node in plan.nodes():
        for rule in rules or DEFAULT_RULES:
            finding = rule(node, ctx)
            if finding is not None:
                findings.append(finding)
    findings.sort(key=lambda f: (-f.severity.rank, -f.node.total_time_ms))

    return Report(plan=plan, total_time_ms=total, hotspots=hotspots, findings=findings)
