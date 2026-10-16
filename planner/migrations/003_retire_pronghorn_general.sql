-- Pronghorn is controlled-only: the 2026 Big Game brochure prints no general pronghorn
-- season (DESIGN.md §5.3; "All pronghorn hunting, including archery seasons, is by
-- controlled hunt"). The `pronghorn_general` tag type was never valid, so existing rows
-- move to `controlled` and the tag type is retired.
--
-- Local DBs at the time of writing hold no `pronghorn_general` seasons or saved tags; the
-- UPDATEs are for any DB that does.

UPDATE seasons SET tag_type_id = 'controlled' WHERE tag_type_id = 'pronghorn_general';
UPDATE my_tags SET tag_type_id = 'controlled' WHERE tag_type_id = 'pronghorn_general';
DELETE FROM tag_types WHERE id = 'pronghorn_general';
