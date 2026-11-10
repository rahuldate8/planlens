import pytest

from planlens import analyze
from planlens.model import PlanParseError, loads_plan, parse_plan
from planlens.rules import filter_columns


def node(node_type, *, plans=(), **fields):
    raw = {"Node Type": node_type, **fields}
    if plans:
        raw["Plans"] = list(plans)
    return raw


def test_parses_the_list_form_postgres_emits():
    plan = parse_plan([{"Plan": node("Result"), "Execution Time": 1.5}])
    assert plan.root.node_type == "Result"
    assert plan.execution_time_ms == 1.5


def test_accepts_bare_node():
    assert parse_plan(node("Result")).root.node_type == "Result"


@pytest.mark.parametrize("bad", ["", "Seq Scan on t", "[]", '{"Plan": {}}', "42"])
def test_rejects_invalid_input(bad):
    with pytest.raises(PlanParseError):
        loads_plan(bad)


def test_exclusive_time_subtracts_children_across_loops():
    child = node("Index Scan", **{"Actual Total Time": 0.01, "Actual Loops": 1000})
    root = node("Nested Loop", plans=[node("Seq Scan"), child], **{"Actual Total Time": 25.0})
    plan = parse_plan(root)
    assert plan.root.children[1].total_time_ms == pytest.approx(10.0)
    assert plan.root.exclusive_time_ms == pytest.approx(15.0)


def test_exclusive_time_is_clamped_at_zero():
    root = node(
        "Gather",
        plans=[node("Seq Scan", **{"Actual Total Time": 50.0, "Actual Loops": 3})],
        **{"Actual Total Time": 60.0, "Actual Loops": 1},
    )
    assert parse_plan(root).root.exclusive_time_ms == 0.0


def test_node_ids_follow_tree_order():
    root = node("A", plans=[node("B", plans=[node("C")]), node("D")])
    ids = [(n.node_type, n.node_id, n.depth) for n in parse_plan(root).nodes()]
    assert ids == [("A", 1, 0), ("B", 2, 1), ("C", 3, 2), ("D", 4, 1)]


@pytest.mark.parametrize(
    ("expr", "cols"),
    [
        ("((status)::text = 'open'::text)", ["status"]),
        ("((policy_id = 42) AND (status = 'open'::text))", ["policy_id", "status"]),
        ("(c.created_at >= '2026-01-01'::date)", ["created_at"]),
        ("(deleted_at IS NULL)", ["deleted_at"]),
        ("(name ~~ 'abc%'::text)", ["name"]),
        (None, []),
    ],
)
def test_filter_columns(expr, cols):
    assert filter_columns(expr) == cols


def test_small_misestimates_are_ignored():
    root = node("Seq Scan", **{"Plan Rows": 10, "Actual Rows": 500, "Actual Loops": 1,
                               "Actual Total Time": 1.0})
    assert analyze(parse_plan(root)).findings == []


def test_large_cold_scan_flags_cache_misses():
    root = node(
        "Seq Scan",
        **{"Relation Name": "t", "Plan Rows": 100, "Actual Rows": 100, "Actual Loops": 1,
           "Actual Total Time": 5.0, "Shared Hit Blocks": 100, "Shared Read Blocks": 5000},
    )
    assert [f.rule_id for f in analyze(parse_plan(root)).findings] == ["PL006"]


def test_index_only_scan_heap_fetches():
    root = node(
        "Index Only Scan",
        **{"Relation Name": "t", "Index Name": "t_idx", "Plan Rows": 100, "Actual Rows": 100,
           "Actual Loops": 1, "Actual Total Time": 1.0, "Heap Fetches": 90},
    )
    finding = analyze(parse_plan(root)).findings[0]
    assert finding.rule_id == "PL007"
    assert "VACUUM t" in finding.suggestion
