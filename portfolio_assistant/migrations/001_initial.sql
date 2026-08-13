PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  name_manually_overridden INTEGER NOT NULL DEFAULT 0 CHECK (name_manually_overridden IN (0, 1)),
  portfolio_group_id INTEGER NOT NULL REFERENCES portfolio_groups(id),
  status TEXT NOT NULL DEFAULT 'Green' CHECK (status IN ('Green', 'Yellow', 'Red', 'Complete')),
  priority TEXT NOT NULL DEFAULT 'Medium' CHECK (priority IN ('Critical', 'High', 'Medium', 'Low')),
  owner_text TEXT,
  next_action TEXT,
  next_action_due TEXT,
  latest_change TEXT,
  snow_number TEXT UNIQUE,
  assignment_group TEXT,
  snow_state TEXT,
  snow_priority TEXT,
  snow_updated_at TEXT,
  snow_metadata_json TEXT NOT NULL DEFAULT '{}',
  snow_comments_cell_hash TEXT,
  folder_path TEXT NOT NULL UNIQUE,
  current_summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_projects_group ON projects(portfolio_group_id);
CREATE INDEX IF NOT EXISTS ix_projects_assignment_group ON projects(assignment_group COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS ix_projects_priority ON projects(priority);
CREATE INDEX IF NOT EXISTS ix_projects_next_action_due ON projects(next_action_due);
CREATE INDEX IF NOT EXISTS ix_projects_updated_at ON projects(updated_at DESC);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT REFERENCES projects(id),
  parent_source_id INTEGER REFERENCES sources(id),
  source_type TEXT NOT NULL,
  native_id TEXT,
  sha256 TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  original_path TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  meeting_name TEXT,
  meeting_date TEXT,
  processing_state TEXT NOT NULL CHECK (processing_state IN ('captured', 'processing', 'pending_ai', 'complete', 'needs_review', 'unsupported', 'error')),
  error_code TEXT,
  error_message TEXT,
  model_id TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  processed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_project_native
  ON sources(project_id, native_id) WHERE project_id IS NOT NULL AND native_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_project_sha
  ON sources(project_id, sha256) WHERE project_id IS NOT NULL AND native_id IS NULL AND parent_source_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_intake_sha
  ON sources(sha256) WHERE project_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_sources_project ON sources(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_sources_state ON sources(processing_state, created_at);

CREATE TABLE IF NOT EXISTS source_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  project_id TEXT REFERENCES projects(id),
  sequence INTEGER NOT NULL,
  text TEXT NOT NULL,
  locator TEXT NOT NULL,
  entry_hash TEXT,
  comment_at TEXT,
  author TEXT,
  entry_type TEXT,
  processing_state TEXT,
  processed_at TEXT,
  UNIQUE(source_id, sequence)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_chunks_source_entry
  ON source_chunks(source_id, entry_hash) WHERE entry_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_chunks_source ON source_chunks(source_id);
CREATE INDEX IF NOT EXISTS ix_chunks_project ON source_chunks(project_id);
CREATE INDEX IF NOT EXISTS ix_chunks_comment_at ON source_chunks(comment_at);

CREATE TABLE IF NOT EXISTS project_updates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id INTEGER REFERENCES sources(id),
  update_type TEXT NOT NULL,
  text TEXT NOT NULL,
  citations_json TEXT NOT NULL,
  model_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_updates_project_created ON project_updates(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS action_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES projects(id),
  description TEXT NOT NULL,
  assignee_type TEXT NOT NULL CHECK (assignee_type IN ('me', 'person', 'team_office')),
  assignee_value TEXT NOT NULL,
  due_date TEXT,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'blocked', 'complete')),
  progress_text TEXT,
  source_id INTEGER REFERENCES sources(id),
  citations_json TEXT,
  created_by TEXT NOT NULL CHECK (created_by IN ('user', 'ai')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_actions_project ON action_items(project_id);
CREATE INDEX IF NOT EXISTS ix_actions_state ON action_items(state);
CREATE INDEX IF NOT EXISTS ix_actions_due ON action_items(due_date);

CREATE TABLE IF NOT EXISTS review_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  source_id INTEGER REFERENCES sources(id),
  project_id TEXT REFERENCES projects(id),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
  question TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  options_json TEXT NOT NULL DEFAULT '[]',
  memory_preview TEXT NOT NULL DEFAULT '',
  resolution_json TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_reviews_status_created ON review_items(status, created_at DESC);

CREATE TABLE IF NOT EXISTS routing_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_type TEXT NOT NULL CHECK (rule_type IN ('ticket_number', 'project_name', 'filename_phrase', 'sender_subject', 'meeting_workstream')),
  pattern TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  target_project_id TEXT NOT NULL REFERENCES projects(id),
  created_from_review_id INTEGER NOT NULL REFERENCES review_items(id),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_routing_rule_active
  ON routing_rules(rule_type, pattern, context_json, target_project_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS daily_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL UNIQUE,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  project_changes_json TEXT NOT NULL,
  counts_json TEXT NOT NULL,
  model_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS project_updates_append_only_update
BEFORE UPDATE ON project_updates BEGIN SELECT RAISE(ABORT, 'project_updates are append-only'); END;
CREATE TRIGGER IF NOT EXISTS project_updates_append_only_delete
BEFORE DELETE ON project_updates BEGIN SELECT RAISE(ABORT, 'project_updates are append-only'); END;
CREATE TRIGGER IF NOT EXISTS routing_rules_no_delete
BEFORE DELETE ON routing_rules BEGIN SELECT RAISE(ABORT, 'routing_rules are append-only'); END;

INSERT OR IGNORE INTO portfolio_groups(name, sort_order, is_system, created_at)
VALUES ('Unassigned', 0, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
