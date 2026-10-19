-- T-28: "Get latest regs" — checking IDFG for newly posted regulation PDFs.
--
-- `fetch_log` keeps the result of each check so the UI can say "Last checked 3 days
-- ago" and show what the last run found without repeating it. One row per run; the
-- whole §6 response body is stored as JSON, because what a run reported is a record,
-- not a queryable model.
--
-- `source_documents.replaces_id` links a silently corrected re-posting to the version
-- it supersedes: same filename on IDFG, different sha256. The new document is staged
-- at a higher `priority` so §8.5 supersession applies when a human publishes it, and
-- the review screen says "replaces <title>".

CREATE TABLE fetch_log (
  id         INTEGER PRIMARY KEY,
  checked_at TEXT NOT NULL,
  result     TEXT NOT NULL   -- JSON: the POST /api/documents/check-updates body
);

ALTER TABLE source_documents ADD COLUMN replaces_id INTEGER REFERENCES source_documents(id);
