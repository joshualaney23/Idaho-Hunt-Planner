-- T-22b: the §8.4 review gate needs three things schema version 1 doesn't store.
--
-- 1. `seasons.section` — which §8.7 section (`deer_general`, `controlled_deer`, …) a row
--    came from. The gate groups rows by section (bulk approve only touches sections whose
--    checks all pass), and §8.7 display safety flags every season of a waived section.
-- 2. `seasons.reviewed_at` — when the row was approved/rejected by hand.
-- 3. `import_checks.waived_by` / `waived_at` — a waiver keeps the reason, who typed it and
--    when, on the check forever (§8.4).

ALTER TABLE seasons ADD COLUMN section TEXT;
ALTER TABLE seasons ADD COLUMN reviewed_at TEXT;

ALTER TABLE import_checks ADD COLUMN waived_by TEXT;
ALTER TABLE import_checks ADD COLUMN waived_at TEXT;
