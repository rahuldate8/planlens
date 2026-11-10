"""Parse `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` output into a typed plan tree."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PlanParseError(ValueError):
    """Raised when input is not a recognisable EXPLAIN JSON document."""


@dataclass
class PlanNode:
    node_type: str
    raw: dict[str, Any]
    children: list[PlanNode] = field(default_factory=list)
    parent: PlanNode | None = field(default=None, repr=False)
    node_id: int = 0
    depth: int = 0

    # --- identity ---------------------------------------------------------
    @property
    def relation(self) -> str | None:
        return self.raw.get("Relation Name")

    @property
    def alias(self) -> str | None:
        return self.raw.get("Alias")

    @property
    def index_name(self) -> str | None:
        return self.raw.get("Index Name")

    @property
    def label(self) -> str:
        parts = [self.node_type]
        if self.index_name:
            parts.append(f"using {self.index_name}")
        if self.relation:
            target = self.relation
            if self.alias and self.alias != self.relation:
                target += f" {self.alias}"
            parts.append(f"on {target}")
        return " ".join(parts)

    # --- estimates vs actuals ----------------------------------------------
    @property
    def analyzed(self) -> bool:
        return "Actual Total Time" in self.raw or "Actual Loops" in self.raw

    @property
    def loops(self) -> int:
        return int(self.raw.get("Actual Loops", 1) or 0)

    @property
    def plan_rows(self) -> float:
        return float(self.raw.get("Plan Rows", 0))

    @property
    def actual_rows(self) -> float:
        """Rows produced per loop (as Postgres reports it)."""
        return float(self.raw.get("Actual Rows", 0))

    @property
    def total_actual_rows(self) -> float:
        return self.actual_rows * self.loops

    @property
    def total_time_ms(self) -> float:
        """Inclusive wall time across all loops, in milliseconds."""
        return float(self.raw.get("Actual Total Time", 0.0)) * self.loops

    @property
    def exclusive_time_ms(self) -> float:
        """Time spent in this node alone, excluding its children.

        Parallel workers and InitPlans can make the subtraction go negative,
        so the result is clamped at zero.
        """
        children = sum(c.total_time_ms for c in self.children)
        return max(0.0, self.total_time_ms - children)

    @property
    def rows_removed_by_filter(self) -> float:
        return float(self.raw.get("Rows Removed by Filter", 0)) * self.loops

    # --- buffers -----------------------------------------------------------
    @property
    def shared_hit(self) -> int:
        return int(self.raw.get("Shared Hit Blocks", 0))

    @property
    def shared_read(self) -> int:
        return int(self.raw.get("Shared Read Blocks", 0))

    def walk(self) -> Iterator[PlanNode]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class QueryPlan:
    root: PlanNode
    planning_time_ms: float | None
    execution_time_ms: float | None
    raw: dict[str, Any]

    @property
    def analyzed(self) -> bool:
        return self.root.analyzed

    def nodes(self) -> list[PlanNode]:
        return list(self.root.walk())


def _build(
    raw: dict[str, Any], parent: PlanNode | None, counter: list[int], depth: int
) -> PlanNode:
    if "Node Type" not in raw:
        raise PlanParseError("plan node is missing 'Node Type'")
    counter[0] += 1
    node = PlanNode(
        node_type=raw["Node Type"],
        raw={k: v for k, v in raw.items() if k != "Plans"},
        parent=parent,
        node_id=counter[0],
        depth=depth,
    )
    node.children = [_build(child, node, counter, depth + 1) for child in raw.get("Plans", [])]
    return node


def parse_plan(data: Any) -> QueryPlan:
    """Build a QueryPlan from decoded EXPLAIN JSON.

    Accepts the list Postgres emits (`[{"Plan": ...}]`), a single top-level
    object (`{"Plan": ...}`), or a bare plan node.
    """
    if isinstance(data, list):
        if not data:
            raise PlanParseError("EXPLAIN output is an empty list")
        data = data[0]
    if not isinstance(data, dict):
        raise PlanParseError("expected a JSON object or list from EXPLAIN (FORMAT JSON)")

    top = data if "Plan" in data else {"Plan": data}
    root = _build(top["Plan"], None, [0], 0)
    return QueryPlan(
        root=root,
        planning_time_ms=top.get("Planning Time"),
        execution_time_ms=top.get("Execution Time"),
        raw=top,
    )


def loads_plan(text: str) -> QueryPlan:
    """Parse EXPLAIN (FORMAT JSON) output from a string."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanParseError(
            f"input is not JSON ({exc.msg}); run EXPLAIN with FORMAT JSON"
        ) from exc
    return parse_plan(data)


def load_plan(path: str | Path) -> QueryPlan:
    """Parse EXPLAIN (FORMAT JSON) output from a file."""
    return loads_plan(Path(path).read_text())
