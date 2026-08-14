import type { Citation, ProjectDetail, Source } from "../../api/contracts";

export type KnowledgeFilters = { q: string; category: string; status: string; source: string; date: string };

export function selectKnowledge(project: ProjectDetail, filters: KnowledgeFilters) {
  return project.knowledge_history.filter(item =>
    (!filters.q || `${item.text} ${item.source_title || ""}`.toLowerCase().includes(filters.q.toLowerCase())) &&
    (!filters.category || item.category === filters.category) &&
    (filters.status === "all" || item.review_status === filters.status) &&
    (!filters.source || String(item.source_id) === filters.source) &&
    (!filters.date || (item.source_date || "").slice(0, 10) === filters.date)
  );
}

export function citationsFor(project: ProjectDetail, knowledgeIds: string[]): Citation[] {
  return Array.from(new Map(
    project.knowledge_history
      .filter(item => knowledgeIds.includes(item.id))
      .flatMap(item => item.citations)
      .map(item => [`${item.source_id}-${item.chunk_id}`, item]),
  ).values());
}

export function selectVisibleSources(sources: Source[]): Source[] {
  return sources.filter((source, index, all) => {
    if (source.parent_source_id && source.source_type !== "routed_segment") return false;
    if (source.source_type !== "routed_segment") return true;
    return all.findIndex(item =>
      item.source_type === "routed_segment" && item.ingestion_path === source.ingestion_path
    ) === index;
  });
}
