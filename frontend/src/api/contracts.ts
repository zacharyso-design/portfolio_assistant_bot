export type Group = { id: number; name: string; sort_order: number; is_system: number; project_count: number };

export type Project = {
  id: string;
  name: string;
  portfolio_group_id: number;
  portfolio_group_name: string;
  status: "Green" | "Yellow" | "Red" | "Complete";
  priority: "Critical" | "High" | "Medium" | "Low";
  owner_text?: string;
  next_action?: string;
  next_action_due?: string;
  latest_change?: string;
  snow_number?: string;
  assignment_group?: string;
  snow_state?: string;
  snow_priority?: string;
  current_summary: string;
  snow_metadata: Record<string, string>;
};

export type Citation = {
  source_id: number;
  chunk_id: number;
  locator?: string;
  original_filename?: string;
  meeting_name?: string;
  meeting_date?: string;
  excerpt?: string;
  citation_id?: string;
  display_name?: string;
  original_relative_path?: string;
  source_date?: string;
};

export type Update = { id: number; text: string; update_type: string; created_at: string; citations: Citation[] };
export type OriginalFile = { id: number; original_name: string; relative_path: string; size_bytes: number; sha256: string; is_attachment: number };
export type SourceLifecycleEvent = { id: string; event_type: string; reason: string; created_at: string };
export type Source = {
  id: number;
  original_filename: string;
  source_type: string;
  processing_state: string;
  error_message?: string;
  meeting_name?: string;
  meeting_date?: string;
  created_at: string;
  parent_source_id?: number;
  parent_original_filename?: string;
  metadata: Record<string, unknown>;
  ingestion_id?: string;
  ingestion_path?: string;
  source_title?: string;
  source_date?: string;
  source_summary?: string;
  capture_method?: string;
  canonical_source?: number;
  memory_state: "pending" | "active" | "removed";
  project_fit_confirmed?: number;
  original_files?: OriginalFile[];
  manifest?: Record<string, unknown>;
  lifecycle?: SourceLifecycleEvent[];
};

export type KnowledgeItem = {
  id: string;
  text: string;
  category: string;
  source_date?: string;
  review_status: "unreviewed" | "approved" | "flagged";
  supersedes_knowledge_item_id?: string;
  source_id: number;
  source_title?: string;
  source_type: string;
  citations: Citation[];
};

export type SummarySection = { section: string; text: string; knowledge_item_ids: string[] };
export type SummaryVersionBase = { id: number; revision: number; review_status: string; created_at: string };
export type SummaryVersion = SummaryVersionBase & { generation_state: string };
export type SummaryVersionDetail = SummaryVersionBase & { content: { sections: SummarySection[] } };
export type LivingSummary = {
  project_id: string;
  generation_state: "current" | "updating" | "stale" | "failed";
  review_status: "unreviewed" | "approved" | "flagged";
  revision: number;
  generated_at?: string;
  error?: string;
  current?: SummaryVersionDetail;
  versions: SummaryVersion[];
};

export type ActionItem = {
  id: number;
  description: string;
  assignee_type: string;
  assignee_value: string;
  due_date?: string;
  state: string;
  progress_text?: string;
  citations: Citation[];
};

export type ProjectDetail = Project & {
  updates: Update[];
  sources: Source[];
  action_items: ActionItem[];
  summary_citations: Citation[];
  knowledge_history: KnowledgeItem[];
  living_summary: LivingSummary;
};

export type SearchResult = {
  result_type: string;
  project_id?: string;
  project_name?: string;
  title: string;
  excerpt?: string;
  source_id?: number;
  source_type?: string;
  source_date?: string;
  locator?: string;
  original_file_id?: number;
};

export type BoardResult = {
  items: Project[];
  total: number;
  limit: number;
  assignment_groups: string[];
  metrics: { total: number; active: number; red: number };
};

export type ReviewEvidence = Record<string, unknown> & {
  text?: string;
  excerpt?: string;
  citations?: Citation[];
  recommended_project_id?: string;
  description?: string;
  assignee_type?: string;
  assignee_value?: string;
  due_date?: string;
  suggested_rule?: Record<string, unknown>;
  action_item_id?: number;
  field?: string;
  value?: unknown;
};

export type Review = {
  id: number;
  kind: string;
  status: string;
  question: string;
  reason: string;
  evidence: ReviewEvidence[];
  options: Array<{ project_id: string; label: string }>;
  memory_preview: string;
  project_id?: string;
  project_name?: string;
  original_filename?: string;
};

export type ConfigurationStatus = {
  llm_adapter: string;
  llm_model: string;
  llm_judgment_model: string;
  llm_endpoint: string | null;
  api_key_present: boolean;
  api_key_source: "environment" | "encrypted_local" | "none" | "fake";
  api_key_environment_override: boolean;
  api_key_local_saved: boolean;
  credential_error: boolean;
  scheduler?: { message?: string };
};

export type LlmHealth = {
  ok: boolean;
  configured: boolean;
  model_id: string;
  endpoint?: string;
  latency_ms?: number;
  error?: string;
};

export type DailySummary = { summary_text?: string; updated_at?: string };
export type SourceCaptureResult = { duplicate: boolean; source?: Source };
export type ArchiveRescanResult = { projects: number; sources: number; errors: number };
export type RefreshDerivedResult = { files_refreshed: number; unsupported_files: number };
export type RebuildKnowledgeResult = { active_sources: number; knowledge_items: number };
export type CitationDetail = Citation & { text: string };
export type ChatClaim = { citations: Citation[] };
export type ChatAnswer = { answer: string; evidence_dropped_chunks?: number; uncertainty?: string; claims?: ChatClaim[] };
export type SnowImportResult = {
  tickets_read: number;
  new_comments_applied: number;
  tickets_unchanged: number;
  review_or_error_count: number;
  pending_ai: number;
  affected_projects: string[];
  review_item_ids: number[];
};
