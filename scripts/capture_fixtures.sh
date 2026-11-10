#!/usr/bin/env bash
# Rebuild tests/fixtures/*.json from a real PostgreSQL instance.
# Usage: scripts/capture_fixtures.sh [dbname]   (default: planlens_fixtures)
set -euo pipefail
DB="${1:-planlens_fixtures}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures"

psql -d postgres -qc "DROP DATABASE IF EXISTS $DB" -qc "CREATE DATABASE $DB"
psql -d "$DB" -q -v ON_ERROR_STOP=1 -f "$(dirname "$0")/fixtures_schema.sql"

capture() {  # name, session settings, query, explain options
  local name="$1" settings="$2" query="$3" opts="${4:-ANALYZE, BUFFERS, FORMAT JSON}"
  psql -d "$DB" -qAt -v ON_ERROR_STOP=1 \
    -c "SET max_parallel_workers_per_gather = 0; $settings" \
    -c "EXPLAIN ($opts) $query" > "$OUT/$name.json"
  echo "captured $name"
}

capture seq_scan_filter "" \
  "SELECT * FROM claims WHERE policy_id = 42 AND status = 'open'"

capture stale_stats "" \
  "SELECT policy_id, count(*) FROM claims_stale GROUP BY policy_id"

capture sort_spill "SET work_mem = '64kB';" \
  "SELECT * FROM claims ORDER BY amount DESC"

capture hash_batches "SET work_mem = '64kB';" \
  "SELECT p.state, sum(c.amount) FROM claims c JOIN policies p USING (policy_id) GROUP BY p.state"

capture nested_loop "SET enable_hashjoin = off; SET enable_mergejoin = off;" \
  "SELECT p.holder_name, c.amount FROM policies p JOIN claims c ON c.policy_id = p.policy_id WHERE p.policy_id <= 300"

capture healthy "" \
  "SELECT * FROM claims WHERE created_at > now() - interval '2 days'"

capture estimate_only "" \
  "SELECT * FROM claims WHERE policy_id = 42" "FORMAT JSON"
