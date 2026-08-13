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
  meeting_name?: string; meeting_date?: string; excerpt?: string;
};

export type Update = { id: number; text: string; update_type: string; created_at: string; citations: Citation[] };
export type Source = {
  id: number; original_filename: string; source_type: string; processing_state: string;
  error_message?: string; meeting_name?: string; meeting_date?: string; created_at: string;
  parent_source_id?: number; parent_original_filename?: string;
  metadata: Record<string, unknown>;
};
export type ActionItem = {
  id: number; description: string; assignee_type: string; assignee_value: string;
  due_date?: string; state: string; progress_text?: string; citations: Citation[];
};
export type ProjectDetail = Project & { updates: Update[]; sources: Source[]; action_items: ActionItem[]; summary_citations: Citation[] };

export type BoardResult = {
  items: Project[]; total: number; limit: number; assignment_groups: string[];
  metrics: { total: number; active: number; red: number };
};

export type Review = {
  id: number; kind: string; status: string; question: string; reason: string;
  evidence: Array<Record<string, unknown>>; options: Array<{ project_id: string; label: string }>;
  memory_preview: string; project_id?: string; project_name?: string; original_filename?: string;
};
