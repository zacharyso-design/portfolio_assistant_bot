import { apiClient } from "./client";
import type {
  ArchiveRescanResult,
  BoardResult,
  ChatAnswer,
  CitationDetail,
  ConfigurationStatus,
  DailySummary,
  Group,
  LlmHealth,
  Project,
  ProjectDetail,
  RebuildKnowledgeResult,
  RefreshDerivedResult,
  Review,
  SearchResult,
  SnowImportResult,
  Source,
  SourceCaptureResult,
  SummaryVersionDetail,
} from "./contracts";

export type ProjectFilters = {
  q: string;
  group: string;
  assignment: string;
  status: string;
  priority: string;
};

export type TranscriptFields = {
  meeting_name: string;
  meeting_date: string;
  is_transcript: boolean;
};

function portfolioQuery(filters: ProjectFilters): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.group) params.set("portfolio_group_id", filters.group);
  if (filters.assignment !== "") params.set("assignment_group", filters.assignment === "__none" ? "" : filters.assignment);
  if (filters.status) params.set("status", filters.status);
  if (filters.priority) params.set("priority", filters.priority);
  return `/api/projects?${params}`;
}

export const backend = {
  configuration: {
    get: () => apiClient.get<ConfigurationStatus>("/api/configuration"),
    saveCredential: (apiKey: string) => apiClient.put<void>("/api/llm/credential", { api_key: apiKey }),
    removeCredential: () => apiClient.delete<void>("/api/llm/credential"),
    testHealth: () => apiClient.post<LlmHealth>("/api/llm/health", {}),
  },
  groups: {
    list: () => apiClient.get<Group[]>("/api/groups"),
    create: (name: string, sortOrder: number) => apiClient.post<Group>("/api/groups", { name, sort_order: sortOrder }),
  },
  portfolio: {
    list: (filters: ProjectFilters) => apiClient.get<BoardResult>(portfolioQuery(filters)),
    search: (query: string) => apiClient.get<SearchResult[]>(`/api/search?q=${encodeURIComponent(query)}`),
    createProject: (name: string, groupId: number | null) => apiClient.post<Project>("/api/projects", { name, portfolio_group_id: groupId }),
    getDaily: () => apiClient.get<DailySummary>("/api/daily"),
    runDaily: () => apiClient.post<DailySummary>("/api/daily", {}),
    rescanArchive: () => apiClient.post<ArchiveRescanResult>("/api/archive/rescan"),
    uploadProjectSource: (projectId: string, file: File, fields: TranscriptFields) =>
      apiClient.upload<SourceCaptureResult>(`/api/projects/${projectId}/sources`, file, fields),
  },
  project: {
    get: (projectId: string) => apiClient.get<ProjectDetail>(`/api/projects/${projectId}`),
    patch: (projectId: string, field: string, value: string | number | null) =>
      apiClient.patch<ProjectDetail>(`/api/projects/${projectId}`, { [field]: value }),
    uploadSource: (projectId: string, file: File, fields: TranscriptFields) =>
      apiClient.upload<SourceCaptureResult>(`/api/projects/${projectId}/sources`, file, fields),
    uploadSources: (projectId: string, files: File[], fields: TranscriptFields) =>
      apiClient.uploadMany<SourceCaptureResult>(`/api/projects/${projectId}/sources`, files, fields),
    addNote: (projectId: string, body: unknown) => apiClient.post<{ id: number }>(`/api/projects/${projectId}/notes`, body),
    ask: (projectId: string, question: string) => apiClient.post<ChatAnswer>(`/api/projects/${projectId}/chat`, { question }),
    addAction: (projectId: string, body: unknown) => apiClient.post<void>(`/api/projects/${projectId}/actions`, body),
    completeAction: (projectId: string, actionId: number) =>
      apiClient.post<void>(`/api/projects/${projectId}/actions/${actionId}/complete`, { confirmed: true }),
    reviewKnowledge: (projectId: string, knowledgeId: string, status: "approved" | "flagged") =>
      apiClient.patch<void>(`/api/projects/${projectId}/knowledge/${knowledgeId}`, { status }),
    reviewSummary: (projectId: string, status: "approved" | "flagged") =>
      apiClient.patch<void>(`/api/projects/${projectId}/living-summary/review`, { status }),
    regenerateSummary: (projectId: string) => apiClient.post<void>(`/api/projects/${projectId}/living-summary/regenerate`),
    getSummaryVersion: (projectId: string, revision: number) =>
      apiClient.get<SummaryVersionDetail>(`/api/projects/${projectId}/living-summary/versions/${revision}`),
    rebuildKnowledge: (projectId: string) =>
      apiClient.post<RebuildKnowledgeResult>(`/api/projects/${projectId}/knowledge/rebuild`),
    removeSource: (projectId: string, sourceId: number, reason: string) =>
      apiClient.post<void>(`/api/projects/${projectId}/sources/${sourceId}/remove`, { reason }),
    restoreSource: (projectId: string, sourceId: number) =>
      apiClient.post<Source>(`/api/projects/${projectId}/sources/${sourceId}/restore`),
  },
  sources: {
    get: (sourceId: number) => apiClient.get<Source>(`/api/sources/${sourceId}`),
    getCitation: (chunkId: number) => apiClient.get<CitationDetail>(`/api/chunks/${chunkId}`),
    refreshDerived: (sourceId: number) => apiClient.post<RefreshDerivedResult>(`/api/sources/${sourceId}/refresh-derived`),
    retryPending: () => apiClient.post<void>("/api/sources/retry-pending"),
  },
  reviews: {
    list: () => apiClient.get<Review[]>("/api/reviews?status=open"),
    upload: (file: File, fields: TranscriptFields) => apiClient.upload<void>("/api/intake/multi-project", file, fields),
    resolve: (reviewId: number, body: unknown) => apiClient.post<void>(`/api/reviews/${reviewId}/resolve`, body),
  },
  snow: {
    import: (file: File) => apiClient.upload<SnowImportResult>("/api/import/servicenow", file),
  },
};
