DROP INDEX IF EXISTS ux_sources_intake_sha;
DROP INDEX IF EXISTS ux_sources_intake_child_native;

-- Shared-intake root packages are deduplicated by content, but attachments are
-- distinct archived records even when two files contain identical bytes.
CREATE UNIQUE INDEX ux_sources_intake_sha
  ON sources(sha256) WHERE project_id IS NULL AND parent_source_id IS NULL;

-- A pre-upgrade interrupted retry could create the same logical attachment with
-- different bytes. Preserve every row, but give later duplicates a legacy-only
-- identity so the constraint can be installed without deleting archived data.
UPDATE sources
SET native_id = native_id || ':legacy-duplicate:' || id
WHERE project_id IS NULL AND parent_source_id IS NOT NULL AND native_id IS NOT NULL
  AND id NOT IN (
    SELECT min(id) FROM sources
    WHERE project_id IS NULL AND parent_source_id IS NOT NULL AND native_id IS NOT NULL
    GROUP BY parent_source_id, native_id
  );

CREATE UNIQUE INDEX ux_sources_intake_child_native
  ON sources(parent_source_id, native_id)
  WHERE project_id IS NULL AND parent_source_id IS NOT NULL AND native_id IS NOT NULL;
