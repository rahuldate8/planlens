-- Schema + data used to capture the plan fixtures in tests/fixtures.
-- An insurance-claims flavoured dataset, big enough to produce real bottlenecks.
DROP TABLE IF EXISTS claims, policies, claims_stale;

CREATE TABLE policies (
    policy_id   int PRIMARY KEY,
    holder_name text NOT NULL,
    state       text NOT NULL,
    city        text NOT NULL
);

CREATE TABLE claims (
    claim_id    bigserial PRIMARY KEY,
    policy_id   int NOT NULL,
    status      text NOT NULL,
    amount      numeric(12,2) NOT NULL,
    created_at  timestamptz NOT NULL,
    notes       text
);

INSERT INTO policies
SELECT g,
       'holder ' || g,
       (ARRAY['MH','KA','DL','TN'])[1 + g % 4],
       -- city is fully determined by state: a classic correlated-columns trap
       (ARRAY['Mumbai','Bengaluru','Delhi','Chennai'])[1 + g % 4]
FROM generate_series(1, 20000) g;

INSERT INTO claims (policy_id, status, amount, created_at, notes)
SELECT (1 + (g::bigint * 7919) % 20000)::int,
       CASE WHEN g % 50 = 0 THEN 'open' ELSE 'closed' END,
       (g % 100000) / 7.0,
       now() - (g % 730) * interval '1 day',
       repeat('x', 40)
FROM generate_series(1, 500000) g;

-- A table whose statistics are deliberately stale.
CREATE TABLE claims_stale (LIKE claims) WITH (autovacuum_enabled = off);
INSERT INTO claims_stale SELECT * FROM claims WHERE claim_id <= 1000;
ANALYZE policies, claims, claims_stale;
INSERT INTO claims_stale SELECT * FROM claims WHERE claim_id > 1000;

CREATE INDEX ON claims (created_at);
