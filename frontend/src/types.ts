export type Group = { id: number; name: string; sort_order: number; is_system: number; project_count: number };

export type Project = {
  id: string; name: string; portfolio_group_id: number; portfolio_group_name: string;
  status: "Green" | "Yellow" | "Red" | "Complete";
  priority: "Critical" | "High" | "Medium" | "Low";
  owner_text?: string; next_action?: string; next_action_due?: string; latest_change?: string;
  snow_number?: string; assignment_group?: string; snow_state?: string; snow_priority?: string;
  current_summary: string; snow_metadata: Record<string, string>;
};

export type Citation = {
  source_id: number; chunk_id: number; locator?: string; original_filename?: string;
  meeting_name?: string; meeting_date?: string; excerpt?: string; citation_id?: string;
  display_name?: string; original_relative_path?: string; source_date?: string;
};

export type Update = { id: number; text: string; update_type: string; created_at: string; citations: Citation[] };
export type Source = {
  id: number; original_filename: string; source_type: string; processing_state: string;
  error_message?: string; meeting_name?: string; meeting_date?: string; created_at: string;
  parent_source_id?: number; parent_original_filename?: string;
  metadata: Record<string, unknown>;
  ingestion_id?: string; ingestion_path?: string; source_title?: string; source_date?: string;
  source_summary?: string; capture_method?: string; canonical_source?: number;
  original_files?: OriginalFile[]; manifest?: Record<string, unknown>;
};
export type OriginalFile = { id: number; original_name: string; relative_path: string; size_bytes: number; sha256: string; is_attachment: number };
export type KnowledgeItem = {
  id: string; text: string; category: string; source_date?: string; review_status: "unreviewed" | "approved" | "flagged";
  supersedes_knowledge_item_id?: string; source_id: number; source_title?: string; source_type: string; citations: Citation[];
};
export type SummarySection = { section: string; text: string; knowledge_item_ids: string[] };
export type LivingSummary = {
  project_id: string; generation_state: "current" | "updating" | "stale" | "failed";
  review_status: "unreviewed" | "approved" | "flagged"; revision: number; generated_at?: string; error?: string;
  current?: { id: number; revision: number; review_status: string; created_at: string; content: { sections: SummarySection[] } };
  versions: Array<{ id: number; revision: number; review_status: string; generation_state: string; created_at: string }>;
};
export type ActionItem = {
  id: number; description: string; assignee_type: string; assignee_value: string;
  due_date?: string; state: string; progress_text?: string; citations: Citation[];
};
export type ProjectDetail = Project & {
  updates: Update[]; sources: Source[]; action_items: ActionItem[]; summary_citations: Citation[];
  knowledge_history: KnowledgeItem[]; living_summary: LivingSummary;
};

export type SearchResult = {
  result_type: string; project_id?: string; project_name?: string; title: string; excerpt?: string;
  source_id?: number; source_type?: string; source_date?: string; locator?: string; original_file_id?: number;
};

export type BoardResult = {
  items: Project[]; total: number; limit: number; assignment_groups: string[];
  metrics: { total: number; active: number; red: number };
};

export type Review = {
  id: number; kind: string; status: string; question: string; reason: string;
  evidence: Array<Record<string, unknown>>; options: Array<{ project_id: string; label: string }>;
  memory_preview: string; project_id?: string; project_name?: string; original_filename?: string;
};
