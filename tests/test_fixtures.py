"""End-to-end checks against real plans captured from PostgreSQL 18.

Fixtures are regenerated with scripts/capture_fixtures.sh. Timings differ from
machine to machine, so these tests assert on structure and rule outcomes only.
"""

from pathlib import Path

import pytest

from planlens import analyze, load_plan

FIXTURES = Path(__file__).parent / "fixtures"


def rule_ids(name: str) -> list[str]:
    return [f.rule_id for f in analyze(load_plan(FIXTURES / f"{name}.json")).findings]


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("seq_scan_filter", "PL002"),
        ("stale_stats", "PL001"),
        ("sort_spill", "PL003"),
        ("hash_batches", "PL004"),
        ("nested_loop", "PL005"),
    ],
)
def test_each_problem_plan_triggers_its_rule(fixture, expected):
    assert expected in rule_ids(fixture)


def test_healthy_plan_has_no_findings():
    assert rule_ids("healthy") == []


def test_estimate_only_plan_is_reported_as_not_analyzed():
    report = analyze(load_plan(FIXTURES / "estimate_only.json"))
    assert not report.analyzed
    assert report.hotspots == []


def test_seq_scan_suggests_index_on_filter_columns():
    finding = analyze(load_plan(FIXTURES / "seq_scan_filter.json")).findings[0]
    assert "CREATE INDEX CONCURRENTLY ON claims (policy_id, status)" in finding.suggestion


def test_stale_stats_suggestion_names_the_table():
    report = analyze(load_plan(FIXTURES / "stale_stats.json"))
    pl001 = next(f for f in report.findings if f.rule_id == "PL001")
    assert "ANALYZE claims_stale" in pl001.suggestion


def test_hotspot_shares_never_exceed_total():
    for path in FIXTURES.glob("*.json"):
        report = analyze(load_plan(path))
        assert sum(h.share for h in report.hotspots) <= 1.0001, path.name
