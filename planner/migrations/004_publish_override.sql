-- T-22g: publishing past the review gate.
--
-- A human may publish a document whose gate is not satisfied, as long as they type a
-- reason. The reason, who typed it, when, and the exact blockers it bypassed are kept on
-- the document forever (the same rule as a check waiver, §8.4), and every row that went
-- live without anyone confirming it is flagged, so the UI can say so beside the season.

ALTER TABLE source_documents ADD COLUMN publish_override_reason TEXT;
ALTER TABLE source_documents ADD COLUMN publish_override_by TEXT;
ALTER TABLE source_documents ADD COLUMN publish_override_at TEXT;
ALTER TABLE source_documents ADD COLUMN publish_override_detail TEXT;  -- JSON: checks + row ids

ALTER TABLE seasons ADD COLUMN published_unreviewed INTEGER NOT NULL DEFAULT 0;

-- When a human last corrected the row's fields by hand. An edited row that nobody has
-- approved or rejected yet counts as uncertain, so its section is sent to review (T-22g).
ALTER TABLE seasons ADD COLUMN edited_at TEXT;
