"""Rule-based checks that turn plan nodes into findings.

Each rule is a function `(node, ctx) -> Finding | None`. Rules only look at a
single node (plus its parent/children), which keeps them easy to test and to
reason about. Thresholds live in `Thresholds` so callers can tune them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from planlens.model import PlanNode, QueryPlan


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


@dataclass(frozen=True)
class Thresholds:
    misestimate_ratio: float = 10.0
    misestimate_min_rows: float = 1_000
    seq_scan_removed_rows: float = 10_000
    seq_scan_removed_fraction: float = 0.9
    nested_loop_inner_loops: int = 1_000
    cache_hit_ratio: float = 0.9
    cache_min_blocks: int = 1_000
    heap_fetch_ratio: float = 0.2
    # Nodes taking less than this share of runtime are not worth flagging.
    min_time_share: float = 0.05


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: Severity
    node: PlanNode
    detail: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "node_id": self.node.node_id,
            "node": self.node.label,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


@dataclass
class Context:
    plan: QueryPlan
    thresholds: Thresholds
    total_time_ms: float

    def time_share(self, node: PlanNode) -> float:
        if self.total_time_ms <= 0:
            return 0.0
        return node.total_time_ms / self.total_time_ms


Rule = Callable[[PlanNode, Context], "Finding | None"]

_COLUMN_RE = re.compile(
    r"\(*\s*(?:\w+\.)?\"?([a-z_][a-z0-9_]*)\"?\)?(?:::[\w ]+)?\s*"
    r"(?:=|<>|!=|<=|>=|<|>|~~\*?|!~~|IS\s+(?:NOT\s+)?NULL|=\s*ANY)",
    re.IGNORECASE,
)


def filter_columns(expr: str | None) -> list[str]:
    """Best-effort extraction of column names compared in a Filter/Cond string.

    Postgres prints filters as SQL fragments such as
    `((status)::text = 'open'::text) AND (customer_id = 42)`. This is a
    heuristic, not a SQL parser: it is used only to phrase index suggestions.
    """
    if not expr:
        return []
    seen: list[str] = []
    for match in _COLUMN_RE.finditer(expr):
        col = match.group(1).lower()
        if col not in seen and col not in {"and", "or", "not", "true", "false"}:
            seen.append(col)
    return seen


def _fmt_rows(n: float) -> str:
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


# --- rules ------------------------------------------------------------------


def row_misestimate(node: PlanNode, ctx: Context) -> Finding | None:
    t = ctx.thresholds
    if not node.analyzed or node.loops == 0:
        return None
    est = max(node.plan_rows, 1.0)
    actual = max(node.actual_rows, 1.0)
    if max(est, actual) < t.misestimate_min_rows:
        return None
    ratio = max(est, actual) / min(est, actual)
    if ratio < t.misestimate_ratio:
        return None
    direction = "under" if actual > est else "over"
    tables = sorted({n.relation for n in node.walk() if n.relation})
    target = ", ".join(tables) if tables else "the tables feeding this node"
    severity = Severity.CRITICAL if ratio >= 100 else Severity.WARNING
    return Finding(
        rule_id="PL001",
        title=f"Row estimate off by {ratio:.0f}x",
        severity=severity,
        node=node,
        detail=(
            f"Planner {direction}estimated: expected {_fmt_rows(est)} rows, got "
            f"{_fmt_rows(actual)} per loop. Bad estimates lead to bad join and scan choices."
        ),
        suggestion=(
            f"Run ANALYZE {target}; then re-check. If it persists, consider extended statistics "
            "(CREATE STATISTICS) for correlated columns or a higher statistics target."
        ),
    )


def seq_scan_filter(node: PlanNode, ctx: Context) -> Finding | None:
    t = ctx.thresholds
    if node.node_type not in {"Seq Scan", "Parallel Seq Scan"} or not node.analyzed:
        return None
    removed = node.rows_removed_by_filter
    kept = node.total_actual_rows
    if removed < t.seq_scan_removed_rows:
        return None
    fraction = removed / (removed + kept) if removed + kept else 0
    if fraction < t.seq_scan_removed_fraction:
        return None
    cols = filter_columns(node.raw.get("Filter"))
    index_hint = (
        f"CREATE INDEX CONCURRENTLY ON {node.relation} ({', '.join(cols)});"
        if cols and node.relation
        else "an index matching the filter columns"
    )
    return Finding(
        rule_id="PL002",
        title="Sequential scan discards most rows",
        severity=Severity.CRITICAL if ctx.time_share(node) >= 0.3 else Severity.WARNING,
        node=node,
        detail=(
            f"Read {_fmt_rows(removed + kept)} rows and threw away {fraction:.0%} of them "
            f"(filter: {node.raw.get('Filter', '?')})."
        ),
        suggestion=f"Consider {index_hint}",
    )


def sort_spill(node: PlanNode, ctx: Context) -> Finding | None:
    if "Sort" not in node.node_type or node.raw.get("Sort Space Type") != "Disk":
        return None
    used_kb = node.raw.get("Sort Space Used", 0)
    return Finding(
        rule_id="PL003",
        title="Sort spilled to disk",
        severity=Severity.WARNING,
        node=node,
        detail=f"Sort method '{node.raw.get('Sort Method', '?')}' used {used_kb} kB on disk.",
        suggestion=(
            f"Raise work_mem for this session above ~{int(used_kb * 1.5 / 1024) + 1}MB, "
            "or add an index that returns rows already in the needed order."
        ),
    )


def hash_batches(node: PlanNode, ctx: Context) -> Finding | None:
    if node.node_type != "Hash":
        return None
    batches = int(node.raw.get("Hash Batches", 1))
    if batches <= 1:
        return None
    return Finding(
        rule_id="PL004",
        title="Hash table split into batches",
        severity=Severity.WARNING,
        node=node,
        detail=(
            f"Hash used {batches} batches (originally planned "
            f"{node.raw.get('Original Hash Batches', 1)}); "
            f"peak memory {node.raw.get('Peak Memory Usage', '?')} kB. Batches go to temp files."
        ),
        suggestion="Increase work_mem (or hash_mem_multiplier) so the hash fits in one batch.",
    )


def nested_loop_inner(node: PlanNode, ctx: Context) -> Finding | None:
    t = ctx.thresholds
    if node.node_type != "Nested Loop" or len(node.children) < 2:
        return None
    inner = node.children[1]
    if inner.loops < t.nested_loop_inner_loops or ctx.time_share(node) < t.min_time_share:
        return None
    is_scan = "Seq Scan" in inner.node_type
    return Finding(
        rule_id="PL005",
        title="Nested loop runs its inner side many times",
        severity=Severity.CRITICAL if is_scan else Severity.WARNING,
        node=node,
        detail=(
            f"Inner node '{inner.label}' executed {inner.loops:,} times; the join loop "
            f"itself accounts for {node.exclusive_time_ms:,.1f} ms "
            f"({node.exclusive_time_ms / ctx.total_time_ms:.0%} of runtime)."
        ),
        suggestion=(
            "Index the inner join key, or fix the outer row estimate so the planner "
            "picks a hash or merge join."
            if is_scan
            else "Check the outer side's row estimate; a hash join may be cheaper at this volume."
        ),
    )


def low_cache_hit(node: PlanNode, ctx: Context) -> Finding | None:
    t = ctx.thresholds
    if not node.relation:
        return None
    hit, read = node.shared_hit, node.shared_read
    total = hit + read
    if total < t.cache_min_blocks:
        return None
    ratio = hit / total
    if ratio >= t.cache_hit_ratio:
        return None
    return Finding(
        rule_id="PL006",
        title="Most pages came from disk, not cache",
        severity=Severity.INFO,
        node=node,
        detail=(
            f"{read:,} blocks read vs {hit:,} hit in shared buffers "
            f"({ratio:.0%} hit ratio). Timings may improve on a warm cache."
        ),
        suggestion=(
            "Re-run to compare cold vs warm timings; if it stays cold, reduce rows read "
            "or review shared_buffers sizing."
        ),
    )


def index_only_heap_fetches(node: PlanNode, ctx: Context) -> Finding | None:
    t = ctx.thresholds
    if node.node_type != "Index Only Scan" or not node.analyzed:
        return None
    fetches = float(node.raw.get("Heap Fetches", 0))
    rows = node.total_actual_rows
    if rows == 0 or fetches / rows < t.heap_fetch_ratio:
        return None
    return Finding(
        rule_id="PL007",
        title="Index-only scan still visits the heap",
        severity=Severity.INFO,
        node=node,
        detail=f"{fetches:,.0f} heap fetches for {rows:,.0f} rows; visibility map is stale.",
        suggestion=f"VACUUM {node.relation or 'the table'} so pages are marked all-visible.",
    )


DEFAULT_RULES: list[Rule] = [
    row_misestimate,
    seq_scan_filter,
    sort_spill,
    hash_batches,
    nested_loop_inner,
    low_cache_hit,
    index_only_heap_fetches,
]
