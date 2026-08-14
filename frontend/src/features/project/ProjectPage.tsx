import { useCallback, useEffect, useMemo, useState } from "react";
import { backend } from "../../api/backend";
import type { ActionItem, ChatAnswer, Citation, CitationDetail, Group, KnowledgeItem, ProjectDetail, Source, SummaryVersion, SummaryVersionDetail } from "../../api/contracts";
import type { Navigate } from "../../app/router";
import { IngestionDropZone } from "../../components/DropZones";
import { Notice } from "../../components/Feedback";
import { PageHeader } from "../../components/PageHeader";
import { pollProjectSource, pollRefresh } from "../../lib/polling";
import { ActionItemsPanel } from "./ActionItemsPanel";
import { KnowledgePanel } from "./KnowledgePanel";
import { ProjectChatPanel } from "./ProjectChatPanel";
import { ProjectDialogs, type PastedTextDraft } from "./ProjectDialogs";
import { citationsFor, selectKnowledge, selectVisibleSources, type KnowledgeFilters } from "./projectSelectors";
import { SourcesPanel } from "./SourcesPanel";
import { SummaryPanel } from "./SummaryPanel";

const DEFAULT_KNOWLEDGE_FILTERS: KnowledgeFilters = { q: "", category: "", status: "all", source: "", date: "" };

export function ProjectPage({ projectId, navigate }: { projectId: string; navigate: Navigate }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [notice, setNotice] = useState("");
  const [citation, setCitation] = useState<CitationDetail | null>(null);
  const [sourceDetail, setSourceDetail] = useState<Source | null>(null);
  const [priorSummary, setPriorSummary] = useState<SummaryVersionDetail | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasted, setPasted] = useState<PastedTextDraft>({ title: "Manual note", text: "", isTranscript: false, meetingName: "", meetingDate: new Date().toISOString().slice(0, 10) });
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ChatAnswer | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [view, setView] = useState<"summary" | "knowledge" | "sources">("summary");
  const [filters, setFilters] = useState(DEFAULT_KNOWLEDGE_FILTERS);

  const load = useCallback(async () => {
    const [detail, groupData] = await Promise.all([backend.project.get(projectId), backend.groups.list()]);
    setProject(detail);
    setGroups(groupData);
    return detail;
  }, [projectId]);
  useEffect(() => { load().catch(error => setNotice(error.message)); }, [load]);

  async function patchProject(field: string, value: string | number | null) {
    setProject(await backend.project.patch(projectId, field, value));
  }

  async function uploadFiles(files: File[]) {
    const first = files[0];
    if (!first) return;
    const lowerNames = files.map(file => file.name.toLowerCase());
    const transcript = lowerNames.some(name => [".vtt", ".srt"].some(ext => name.endsWith(ext))) ||
      (lowerNames.some(name => [".txt", ".md"].some(ext => name.endsWith(ext))) && confirm("Does this selection contain a meeting transcript?"));
    let meeting_name = "";
    let meeting_date = "";
    if (transcript) {
      meeting_name = prompt("Meeting name") || "";
      meeting_date = prompt("Meeting date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "";
      if (!meeting_name || !meeting_date) throw new Error("Meeting name and date are required for transcripts.");
    }
    const fields = { meeting_name, meeting_date, is_transcript: transcript };
    const result = files.length === 1 && !first.webkitRelativePath
      ? await backend.project.uploadSource(projectId, first, fields)
      : await backend.project.uploadSources(projectId, files, fields);
    setNotice(result.duplicate
      ? (result.source?.memory_state === "removed" ? "This source is already preserved in Archive. Restore it from the Sources view to use it again." : "This content is already preserved for the project.")
      : "Source package preserved; the project-fit check runs before any memory update.");
    await load();
    if (!result.duplicate && result.source?.id) void pollProjectSource(projectId, result.source.id, load).catch(error => setNotice(error.message));
  }

  async function openCitation(item: Citation) {
    setCitation({ ...(await backend.sources.getCitation(item.chunk_id)), citation_id: item.citation_id });
  }

  async function reviewKnowledge(item: KnowledgeItem, status: "approved" | "flagged") {
    await backend.project.reviewKnowledge(projectId, item.id, status);
    setNotice(`Knowledge item ${status}; Living Summary approval remains independent.`);
    await load();
  }

  async function reviewSummary(status: "approved" | "flagged") {
    await backend.project.reviewSummary(projectId, status);
    setNotice(`Living Summary ${status}; Knowledge History states were not changed.`);
    await load();
  }

  async function flagStatement(ids: string[]) {
    await Promise.all(ids.map(id => backend.project.reviewKnowledge(projectId, id, "flagged")));
    await load();
  }

  async function selectSummaryVersion(version: SummaryVersion) {
    setPriorSummary(await backend.project.getSummaryVersion(projectId, version.revision));
  }

  async function refreshDerived(source: Source) {
    const result = await backend.sources.refreshDerived(source.id);
    setNotice(`Rebuilt ${result.files_refreshed} extracted file(s); all original hashes were verified first.`);
    setSourceDetail(await backend.sources.get(source.id));
    await load();
  }

  async function removeSource(source: Source) {
    if (!confirm(`Remove ${source.source_title || source.original_filename} from this project's active memory? The original package will be kept in the managed Archive and can be restored.`)) return;
    const answer = prompt("Why is this source being removed?", "Added to the wrong project");
    if (answer === null) return;
    await backend.project.removeSource(projectId, source.id, answer.trim() || "Removed from active project memory by the user.");
    setSourceDetail(null);
    setNotice("Source removed from summary, chat, search, knowledge, and source-created actions. Its original package remains recoverable in Archive.");
    await load();
  }

  async function restoreSource(source: Source) {
    const restored = await backend.project.restoreSource(projectId, source.id);
    setSourceDetail(null);
    setNotice(restored.memory_state === "active"
      ? "Source restored to this project. Current knowledge has been rebuilt from active sources."
      : "Source restored to this project and queued for project-fit review before memory changes.");
    await load();
  }

  async function rebuildKnowledge() {
    const result = await backend.project.rebuildKnowledge(projectId);
    setNotice(`Project knowledge rebuilt from ${result.active_sources} active source package(s) and ${result.knowledge_items} knowledge item(s).`);
    await load();
  }

  async function submitPastedText(event: React.FormEvent) {
    event.preventDefault();
    const source = await backend.project.addNote(projectId, {
      title: pasted.title,
      text: pasted.text,
      is_transcript: pasted.isTranscript,
      meeting_name: pasted.isTranscript ? pasted.meetingName : null,
      meeting_date: pasted.isTranscript ? pasted.meetingDate : null,
    });
    setPasteOpen(false);
    setPasted({ ...pasted, text: "" });
    setNotice("Pasted text was preserved exactly as submitted; the project-fit check runs before any memory update.");
    await load();
    void pollProjectSource(projectId, source.id, load).catch(error => setNotice(error.message));
  }

  async function askProject(event: React.FormEvent) {
    event.preventDefault(); setChatBusy(true);
    try { setAnswer(await backend.project.ask(projectId, question)); setQuestion(""); }
    catch (error) { setNotice((error as Error).message); }
    finally { setChatBusy(false); }
  }

  async function addAction() {
    const description = prompt("Action description")?.trim();
    if (!description) return;
    const assignee = prompt("Assignee name, team/office, or Me", "Me")?.trim() || "Me";
    const due = prompt("Due date (YYYY-MM-DD), or leave blank", "")?.trim() || null;
    await backend.project.addAction(projectId, { description, assignee_type: assignee.toLowerCase() === "me" ? "me" : "person", assignee_value: assignee, due_date: due, state: "open", progress_text: null });
    await load();
  }

  async function completeAction(item: ActionItem) {
    if (!confirm("Complete this action item? This requires your explicit approval.")) return;
    await backend.project.completeAction(projectId, item.id);
    await load();
  }

  async function retryPending() {
    await backend.sources.retryPending();
    await pollRefresh(load);
  }

  const knowledge = useMemo(() => project ? selectKnowledge(project, filters) : [], [project, filters]);
  const visibleSources = useMemo(() => project ? selectVisibleSources(project.sources) : [], [project]);
  if (!project) return <div className="page loading">Loading project...</div>;

  return <>
    <PageHeader eyebrow={project.portfolio_group_name} title={project.name}>
      <button className="button" onClick={() => navigate("/")}>Back to portfolio</button>
      <select aria-label="Project priority" value={project.priority} onChange={event => patchProject("priority", event.target.value)}>{["Critical", "High", "Medium", "Low"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Project status" value={project.status} onChange={event => patchProject("status", event.target.value)}>{["Green", "Yellow", "Red", "Complete"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Portfolio group" value={project.portfolio_group_id} onChange={event => patchProject("portfolio_group_id", Number(event.target.value))}>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select>
    </PageHeader>
    <div className="archive-workspace">
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
      <section className="snow-strip"><div><small>SNOW number</small><strong>{project.snow_number || "Not linked"}</strong></div><div><small>SNOW state</small><strong>{project.snow_state || "-"}</strong></div><div><small>SNOW priority</small><strong>{project.snow_priority || "-"}</strong></div><div><small>Assignment Group</small><strong>{project.assignment_group || "None"}</strong></div></section>
      <nav className="project-tabs" aria-label="Project archive views"><button className={view === "summary" ? "active" : ""} onClick={() => setView("summary")}>Living Summary</button><button className={view === "knowledge" ? "active" : ""} onClick={() => setView("knowledge")}>Knowledge History <span>{project.knowledge_history.length}</span></button><button className={view === "sources" ? "active" : ""} onClick={() => setView("sources")}>Sources <span>{visibleSources.length}</span></button></nav>
      <section className="panel manual-fields"><header><div><small>User-controlled</small><h2>Project fields</h2></div></header><div><label>Project name<input defaultValue={project.name} onBlur={event => event.target.value !== project.name && patchProject("name", event.target.value)} /></label><label>Owner<input defaultValue={project.owner_text || ""} onBlur={event => patchProject("owner_text", event.target.value || null)} /></label><label>Primary next action<input defaultValue={project.next_action || ""} onBlur={event => patchProject("next_action", event.target.value || null)} /></label><label>Due date<input type="date" defaultValue={project.next_action_due || ""} onBlur={event => patchProject("next_action_due", event.target.value || null)} /></label></div></section>
      <IngestionDropZone onFiles={files => uploadFiles(files).catch(error => setNotice(error.message))} />
      <button className="button" onClick={() => setPasteOpen(true)}>Paste a note or transcript</button>
      {view === "summary" && <SummaryPanel project={project} citationsFor={ids => citationsFor(project, ids)} onOpenCitation={item => openCitation(item).catch(error => setNotice(error.message))} onRegenerate={() => backend.project.regenerateSummary(projectId).then(load).catch(error => setNotice(error.message))} onReview={status => reviewSummary(status).catch(error => setNotice(error.message))} onFlagStatement={ids => flagStatement(ids).catch(error => setNotice(error.message))} onSelectVersion={version => selectSummaryVersion(version).catch(error => setNotice(error.message))} />}
      {view === "knowledge" && <KnowledgePanel project={project} knowledge={knowledge} filters={filters} onFiltersChange={setFilters} onOpenCitation={item => openCitation(item).catch(error => setNotice(error.message))} onReview={(item, status) => reviewKnowledge(item, status).catch(error => setNotice(error.message))} />}
      {view === "sources" && <SourcesPanel sources={visibleSources} onRebuildKnowledge={() => rebuildKnowledge().catch(error => setNotice(error.message))} onRetryPending={() => retryPending().catch(error => setNotice(error.message))} onInspect={source => backend.sources.get(source.id).then(setSourceDetail).catch(error => setNotice(error.message))} onRemove={source => removeSource(source).catch(error => setNotice(error.message))} onRestore={source => restoreSource(source).catch(error => setNotice(error.message))} />}
      <ActionItemsPanel items={project.action_items} onAdd={() => addAction().catch(error => setNotice(error.message))} onComplete={item => completeAction(item).catch(error => setNotice(error.message))} />
      <ProjectChatPanel answer={answer} question={question} busy={chatBusy} onQuestionChange={setQuestion} onSubmit={askProject} onOpenCitation={item => openCitation(item).catch(error => setNotice(error.message))} />
    </div>
    <ProjectDialogs project={project} citation={citation} source={sourceDetail} priorSummary={priorSummary} pasteOpen={pasteOpen} pasted={pasted} onCitationClose={() => setCitation(null)} onSourceClose={() => setSourceDetail(null)} onSummaryClose={() => setPriorSummary(null)} onPasteClose={() => setPasteOpen(false)} onPastedChange={setPasted} onSubmitPasted={event => submitPastedText(event).catch(error => setNotice(error.message))} onRefreshDerived={source => refreshDerived(source).catch(error => setNotice(error.message))} onRemoveSource={source => removeSource(source).catch(error => setNotice(error.message))} onRestoreSource={source => restoreSource(source).catch(error => setNotice(error.message))} />
  </>;
}
