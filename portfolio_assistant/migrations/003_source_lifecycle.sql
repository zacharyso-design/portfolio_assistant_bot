ALTER TABLE sources ADD COLUMN memory_state TEXT NOT NULL DEFAULT 'pending'
  CHECK (memory_state IN ('pending', 'active', 'removed'));
ALTER TABLE sources ADD COLUMN project_fit_confirmed INTEGER NOT NULL DEFAULT 0
  CHECK (project_fit_confirmed IN (0, 1));
ALTER TABLE sources ADD COLUMN archived_previous_memory_state TEXT
  CHECK (archived_previous_memory_state IN ('pending', 'active'));
ALTER TABLE sources ADD COLUMN memory_state_changed_at TEXT;

WITH RECURSIVE source_roots(id, root_processing_state, root_project_id) AS (
  SELECT id, processing_state, project_id FROM sources WHERE parent_source_id IS NULL
  UNION ALL
  SELECT s.id, r.root_processing_state, r.root_project_id
  FROM sources s JOIN source_roots r ON s.parent_source_id = r.id
)
UPDATE sources
SET memory_state = CASE
      WHEN (SELECT root_processing_state FROM source_roots WHERE source_roots.id = sources.id) = 'complete'
      THEN 'active' ELSE 'pending' END,
    project_fit_confirmed = CASE
      WHEN (SELECT root_processing_state FROM source_roots WHERE source_roots.id = sources.id) = 'complete'
        OR (SELECT root_project_id FROM source_roots WHERE source_roots.id = sources.id) IS NULL
      THEN 1 ELSE 0 END,
    memory_state_changed_at = coalesce(processed_at, created_at)
WHERE memory_state_changed_at IS NULL;

-- Routed segments are logical roots of linked project packages. Their parent points to the
-- Shared Intake evidence for provenance, not for processing-state inheritance.
UPDATE sources
SET memory_state = 'active', project_fit_confirmed = 1,
    memory_state_changed_at = coalesce(memory_state_changed_at, processed_at, created_at)
WHERE source_type = 'routed_segment' AND processing_state = 'complete'
  AND project_id IS NOT NULL AND memory_state <> 'removed';

CREATE INDEX ix_sources_memory_state ON sources(memory_state, project_id, created_at);

CREATE TABLE source_lifecycle_events (
  id TEXT PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  ingestion_id TEXT,
  project_id TEXT REFERENCES projects(id),
  event_type TEXT NOT NULL CHECK (event_type IN (
    'project_fit_confirmed', 'moved_before_processing', 'removed_from_memory', 'restored_to_memory'
  )),
  from_project_id TEXT REFERENCES projects(id),
  to_project_id TEXT REFERENCES projects(id),
  reason TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX ix_source_lifecycle_source ON source_lifecycle_events(source_id, created_at);

CREATE TRIGGER source_lifecycle_events_no_update
BEFORE UPDATE ON source_lifecycle_events BEGIN
  SELECT RAISE(ABORT, 'source lifecycle events are append-only');
END;

CREATE TRIGGER source_lifecycle_events_no_delete
BEFORE DELETE ON source_lifecycle_events BEGIN
  SELECT RAISE(ABORT, 'source lifecycle events are append-only');
END;
