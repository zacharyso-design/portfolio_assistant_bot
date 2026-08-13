ALTER TABLE projects ADD COLUMN archive_id TEXT;
ALTER TABLE projects ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE projects ADD COLUMN summary_generation_state TEXT NOT NULL DEFAULT 'stale'
  CHECK (summary_generation_state IN ('current', 'updating', 'stale', 'failed'));
ALTER TABLE projects ADD COLUMN summary_review_status TEXT NOT NULL DEFAULT 'unreviewed'
  CHECK (summary_review_status IN ('unreviewed', 'approved', 'flagged'));
ALTER TABLE projects ADD COLUMN summary_generated_at TEXT;
ALTER TABLE projects ADD COLUMN summary_error TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_archive_id ON projects(archive_id) WHERE archive_id IS NOT NULL;

ALTER TABLE sources ADD COLUMN ingestion_id TEXT;
ALTER TABLE sources ADD COLUMN ingestion_path TEXT;
ALTER TABLE sources ADD COLUMN source_title TEXT;
ALTER TABLE sources ADD COLUMN source_date TEXT;
ALTER TABLE sources ADD COLUMN capture_method TEXT NOT NULL DEFAULT 'file_upload';
ALTER TABLE sources ADD COLUMN canonical_source INTEGER NOT NULL DEFAULT 1 CHECK (canonical_source IN (0, 1));
ALTER TABLE sources ADD COLUMN linked_ingestion_id TEXT;
ALTER TABLE sources ADD COLUMN source_summary TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN processing_version INTEGER NOT NULL DEFAULT 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_ingestion_id ON sources(ingestion_id) WHERE ingestion_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_sources_ingestion_path ON sources(ingestion_path);

CREATE TABLE original_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  relative_path TEXT NOT NULL,
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  is_attachment INTEGER NOT NULL DEFAULT 0 CHECK (is_attachment IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE(source_id, relative_path)
);

CREATE INDEX ix_original_files_source ON original_files(source_id);
CREATE INDEX ix_original_files_sha ON original_files(sha256);

CREATE TABLE citation_records (
  id TEXT PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  chunk_id INTEGER NOT NULL REFERENCES source_chunks(id),
  original_relative_path TEXT NOT NULL,
  display_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  locator TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  source_date TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(source_id, chunk_id)
);

CREATE INDEX ix_citation_source ON citation_records(source_id);

CREATE TABLE knowledge_items (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  text TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'development',
  source_date TEXT,
  citation_ids_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (review_status IN ('unreviewed', 'approved', 'flagged')),
  supersedes_knowledge_item_id TEXT REFERENCES knowledge_items(id),
  created_at TEXT NOT NULL
);

CREATE INDEX ix_knowledge_project_created ON knowledge_items(project_id, created_at DESC);
CREATE INDEX ix_knowledge_source ON knowledge_items(source_id);
CREATE INDEX ix_knowledge_review ON knowledge_items(project_id, review_status);

CREATE TABLE summary_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES projects(id),
  revision INTEGER NOT NULL,
  content_json TEXT NOT NULL,
  markdown TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (review_status IN ('unreviewed', 'approved', 'flagged')),
  generation_state TEXT NOT NULL DEFAULT 'current'
    CHECK (generation_state IN ('current', 'updating', 'stale', 'failed')),
  error_message TEXT,
  model_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, revision)
);

CREATE INDEX ix_summary_project_revision ON summary_versions(project_id, revision DESC);
