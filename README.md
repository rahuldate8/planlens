# planlens

Find the bottleneck in a PostgreSQL query plan.

`planlens` reads `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` output, works out where
the time actually went, and runs a set of rule-based checks that point at the
usual causes: stale statistics, sequential scans that throw away most rows, sorts
and hashes spilling to disk, and nested loops gone wrong. It can optionally ask
Claude to explain the plan in plain language, grounded in those findings.

```text
$ planlens analyze tests/fixtures/seq_scan_filter.json

Execution time: 18.2 ms  (planning 0.3 ms)

Where the time went
   99.8%        18.1 ms  [1] Seq Scan on claims

Plan
  [ 1] Seq Scan on claims  (18.1 ms, rows 0 est 1, loops 1) ◀

Findings (1)
  ✗ PL002 Sequential scan discards most rows  [1] Seq Scan on claims
      Read 500.0k rows and threw away 100% of them (filter: ((policy_id = 42) AND (status = 'open'::text))).
      fix: Consider CREATE INDEX CONCURRENTLY ON claims (policy_id, status);
```

## Install

```bash
pip install -e .            # core: no dependencies
pip install -e '.[ai]'      # adds the Anthropic SDK for --ai
pip install -e '.[dev]'     # pytest + ruff
```

Requires Python 3.10+.

## Usage

Capture a plan:

```bash
psql -qAt -c "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT ..." > plan.json
```

Analyze it:

```bash
planlens analyze plan.json                  # text report with plan tree
planlens analyze plan.json --format json    # machine-readable
psql ... | planlens analyze -               # read from stdin
planlens analyze plan.json --fail-on warning   # exit 1 on findings, for CI
planlens analyze plan.json --ai --sql query.sql  # add an explanation from Claude
```

`--ai` uses the Anthropic API and reads credentials from the environment
(`ANTHROPIC_API_KEY`, or a profile from `ant auth login`). The plan, the SQL you
pass with `--sql`, and the automated findings are sent to the API.

Plans captured without `ANALYZE` still parse, but most checks need actual row
counts and timings.

## Checks

| ID    | Check | Typical fix |
|-------|-------|-------------|
| PL001 | Row estimate off by 10x or more | `ANALYZE`, extended statistics |
| PL002 | Sequential scan discards ≥ 90% of rows read | Index on the filter columns |
| PL003 | Sort spilled to disk | Raise `work_mem`, or an index in sort order |
| PL004 | Hash split into multiple batches | Raise `work_mem` / `hash_mem_multiplier` |
| PL005 | Nested loop runs its inner side ≥ 1,000 times | Index the join key, fix outer estimate |
| PL006 | Low shared-buffer hit ratio on a large scan | Check cold vs warm cache |
| PL007 | Index-only scan with many heap fetches | `VACUUM` to refresh the visibility map |

Thresholds live in `planlens.rules.Thresholds` and can be tuned when calling
`planlens.analyze()` from Python:

```python
from planlens import analyze, load_plan
from planlens.rules import Thresholds

report = analyze(load_plan("plan.json"), thresholds=Thresholds(misestimate_ratio=5))
for finding in report.findings:
    print(finding.rule_id, finding.title, finding.suggestion)
```

## How time is attributed

Postgres reports `Actual Total Time` per loop and *including* children. planlens
multiplies by `Actual Loops`, then subtracts the children's totals to get each
node's exclusive time. That figure drives the "Where the time went" ranking.
Parallel workers and InitPlans can make the subtraction go negative, so it is
clamped at zero.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,ai]'
.venv/bin/pytest
.venv/bin/ruff check .
```

The plans in `tests/fixtures/` are real, captured from PostgreSQL 18 against the
dataset in `scripts/fixtures_schema.sql`. To regenerate them against a local
server (this creates and drops a `planlens_fixtures` database):

```bash
scripts/capture_fixtures.sh
```

## Roadmap

- [ ] `planlens capture` – run EXPLAIN against a DSN directly (psycopg)
- [ ] Parse plain-text EXPLAIN output, not just JSON
- [ ] Compare two plans (before / after an index or a config change)
- [ ] Checks for parallel plans, JIT overhead, and CTE scans
- [ ] Estimate-only checks for plans captured without ANALYZE
- [ ] HTML report with an interactive plan tree
- [ ] Eval set for the `--ai` explanations

## License

MIT
