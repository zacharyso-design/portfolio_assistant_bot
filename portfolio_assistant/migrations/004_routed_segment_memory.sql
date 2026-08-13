-- Correct databases that applied migration 003 before routed segments were recognized as
-- logical roots of their linked project packages.
UPDATE sources
SET memory_state = 'active', project_fit_confirmed = 1,
    memory_state_changed_at = coalesce(processed_at, created_at)
WHERE source_type = 'routed_segment' AND processing_state = 'complete'
  AND project_id IS NOT NULL AND memory_state <> 'removed';
