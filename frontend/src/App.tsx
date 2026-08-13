import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { BoardResult, Citation, Group, KnowledgeItem, Project, ProjectDetail, Review, SearchResult, Source } from "./types";

type Route = { page: "portfolio" | "project" | "review" | "import"; id?: string };

function currentRoute(): Route {
  const project = location.pathname.match(/^\/projects\/([^/]+)$/);
  if (project) return { page: "project", id: project[1] };
  if (location.pathname === "/review") return { page: "review" };
  if (location.pathname === "/import") return { page: "import" };
  return { page: "portfolio" };
}

function useRoute() {
  const [route, setRoute] = useState<Route>(currentRoute);
  useEffect(() => {
    const handler = () => setRoute(currentRoute());
    addEventListener("popstate", handler);
    return () => removeEventListener("popstate", handler);
  }, []);
  const navigate = (path: string) => { history.pushState({}, "", path); setRoute(currentRoute()); };
  return { route, navigate };
}

const pause = (milliseconds: number) => new Promise(resolve => setTimeout(resolve, milliseconds));

async function pollProjectSource(
  projectId: string, sourceId: number, refresh: () => Promise<ProjectDetail | void>,
) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    const refreshed = await refresh();
    const detail = refreshed || await api.get<ProjectDetail>(`/api/projects/${projectId}`);
    const state = detail.sources.find(source => source.id === sourceId)?.processing_state;
    if (!state || !["captured", "processing"].includes(state)) return;
    await pause(1000);
  }
}

async function pollRefresh(refresh: () => Promise<ProjectDetail | void>, attempts = 15) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const detail = await refresh();
    // Project refreshes can stop early; Review Queue intentionally polls the full window because it has no source state yet.
    if (detail && !detail.sources.some(source => ["captured", "processing"].includes(source.processing_state))) return;
    if (attempt + 1 < attempts) await pause(1000);
  }
}

function App() {
  const { route, navigate } = useRoute();
  const [reviewCount, setReviewCount] = useState(0);
  const refreshCount = useCallback(() => api.get<Review[]>("/api/reviews?status=open").then(items => setReviewCount(items.length)).catch(() => {}), []);
  useEffect(() => { refreshCount(); }, [refreshCount, route]);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => navigate("/")}><span className="logo">PA</span><span><strong>Portfolio</strong><small>Assistant</small></span></button>
        <span className="nav-label">Workspace</span>
        <nav aria-label="Primary navigation">
          <button className={route.page === "portfolio" ? "active" : ""} onClick={() => navigate("/")}><span>▦</span> Portfolio</button>
          <button className={route.page === "review" ? "active" : ""} onClick={() => navigate("/review")}><span>◇</span> Review queue <b>{reviewCount}</b></button>
          <button className={route.page === "import" ? "active" : ""} onClick={() => navigate("/import")}><span>⇩</span> SNOW import</button>
        </nav>
        <div className="local-badge"><span>Local</span><small>Government workspace</small></div>
      </aside>
      <main>
        {route.page === "portfolio" && <Portfolio navigate={navigate} />}
        {route.page === "project" && route.id && <ProjectArchiveWorkspace projectId={route.id} navigate={navigate} />}
        {route.page === "review" && <ReviewQueue navigate={navigate} onChange={refreshCount} />}
        {route.page === "import" && <SnowImport navigate={navigate} />}
      </main>
    </div>
  );
}

function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children?: React.ReactNode }) {
  return <header className="topbar"><div><small>{eyebrow}</small><h1>{title}</h1></div><div className="top-actions">{children}</div></header>;
}

function Portfolio({ navigate }: { navigate: (path: string) => void }) {
  const [board, setBoard] = useState<BoardResult | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [daily, setDaily] = useState<any>(null);
  const [filters, setFilters] = useState({ q: "", group: "", assignment: "", status: "", priority: "" });
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [notice, setNotice] = useState("");
  const [scheduler, setScheduler] = useState<any>(null);
  const [archiveResults, setArchiveResults] = useState<SearchResult[]>([]);
  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.group) params.set("portfolio_group_id", filters.group);
    if (filters.assignment !== "") params.set("assignment_group", filters.assignment === "__none" ? "" : filters.assignment);
    if (filters.status) params.set("status", filters.status);
    if (filters.priority) params.set("priority", filters.priority);
    const [projects, groupData, dailyData, searchData] = await Promise.all([
      api.get<BoardResult>(`/api/projects?${params}`), api.get<Group[]>("/api/groups"), api.get<any>("/api/daily"),
      filters.q.trim() ? api.get<SearchResult[]>(`/api/search?q=${encodeURIComponent(filters.q)}`) : Promise.resolve([]),
    ]);
    setBoard(projects); setGroups(groupData); setDaily(dailyData); setArchiveResults(searchData);
  }, [filters]);
  useEffect(() => { const timer = setTimeout(() => load().catch(error => setNotice(error.message)), 120); return () => clearTimeout(timer); }, [load]);
  useEffect(() => { api.get<any>("/api/configuration").then(data => setScheduler(data.scheduler)).catch(() => {}); }, []);
  const grouped = useMemo(() => {
    const map = new Map<string, Project[]>();
    board?.items.forEach(project => map.set(project.portfolio_group_name, [...(map.get(project.portfolio_group_name) || []), project]));
    return [...map.entries()];
  }, [board]);
  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    const project = await api.post<Project>("/api/projects", { name: newName, portfolio_group_id: newGroup ? Number(newGroup) : null });
    setNewOpen(false); navigate(`/projects/${project.id}`);
  }
  async function createGroup() {
    const name = prompt("New portfolio group name")?.trim();
    if (!name) return;
    const created = await api.post<Group>("/api/groups", { name, sort_order: groups.length * 10 });
    setGroups(await api.get<Group[]>("/api/groups"));
    setNewGroup(String(created.id));
  }
  async function runDaily() {
    try { setDaily(await api.post("/api/daily", {})); }
    catch (error) { setNotice((error as Error).message); }
  }
  async function drop(projectId: string, file: File) {
    const lowerName = file.name.toLowerCase();
    const transcript = [".vtt", ".srt"].some(ext => lowerName.endsWith(ext)) ||
      ([".txt", ".md"].some(ext => lowerName.endsWith(ext)) && confirm("Is this text file a meeting transcript?"));
    let meeting_name = "", meeting_date = "";
    if (transcript) {
      meeting_name = prompt("Meeting name (required for transcript citations)") || "";
      meeting_date = prompt("Meeting date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "";
      if (!meeting_name || !meeting_date) { setNotice("Upload cancelled: meeting name and date are required for transcripts."); return; }
    }
    const result = await api.upload<any>(`/api/projects/${projectId}/sources`, file, { meeting_name, meeting_date, is_transcript: transcript });
    setNotice(result.duplicate ? `Already captured: ${file.name}` : `Captured ${file.name} for the selected project.`);
    await load();
    if (!result.duplicate && result.source?.id) {
      void pollProjectSource(projectId, result.source.id, load).catch(error => setNotice(error.message));
    }
  }
  return <>
    <PageHeader eyebrow={new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} title="Project portfolio">
      <label className="search-field"><span className="sr-only">Search projects</span><input value={filters.q} onChange={event => setFilters({ ...filters, q: event.target.value })} placeholder="Search projects" /></label>
      <button className="button" onClick={() => api.post<Record<string, number>>("/api/archive/rescan").then(result => { setNotice(`OneDrive rescan complete: ${result.projects} projects, ${result.sources} sources added; ${result.errors} archive item(s) could not be indexed.`); return load(); }).catch(error => setNotice(error.message))}>Rescan OneDrive</button>
      <button className="button" onClick={() => navigate("/import")}>Import SNOW</button>
      <button className="button primary" onClick={() => setNewOpen(true)}>+ New project</button>
    </PageHeader>
    <div className="page">
      {notice && <div className="notice" role="status">{notice}<button aria-label="Dismiss" onClick={() => setNotice("")}>×</button></div>}
      <section className="morning-banner">
        <span className="check">✓</span><div><strong>{daily ? "Morning update complete" : "Morning update not run"}</strong><small>{daily?.summary_text || "Generate the prior day’s committed portfolio changes."}</small></div>
        <span>{daily?.updated_at ? new Date(daily.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Not run today"}{scheduler?.message ? ` · ${scheduler.message}` : " · Checking morning task…"}</span>
        <button onClick={runDaily}>Run update now →</button>
      </section>
      <section className="metrics" aria-label="Portfolio metrics">
        <Metric label="Active" value={board?.metrics.active ?? "—"} />
        <Metric label="Red" value={board?.metrics.red ?? "—"} tone="danger" />
        <Metric label="Showing" value={`${board?.items.length ?? 0} / ${board?.total ?? 0}`} />
      </section>
      <section className="filterbar" aria-label="Project filters">
        <select aria-label="Portfolio group" value={filters.group} onChange={event => setFilters({ ...filters, group: event.target.value })}><option value="">All portfolio groups</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select>
        <select aria-label="SNOW Assignment Group" value={filters.assignment} onChange={event => setFilters({ ...filters, assignment: event.target.value })}><option value="">All assignment groups</option><option value="__none">No assignment group</option>{board?.assignment_groups.filter(Boolean).map(value => <option key={value} value={value}>{value}</option>)}</select>
        <select aria-label="Status" value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{["Green", "Yellow", "Red", "Complete"].map(value => <option key={value}>{value}</option>)}</select>
        <select aria-label="Priority" value={filters.priority} onChange={event => setFilters({ ...filters, priority: event.target.value })}><option value="">All priorities</option>{["Critical", "High", "Medium", "Low"].map(value => <option key={value}>{value}</option>)}</select>
      </section>
      {filters.q.trim() && <section className="panel archive-results"><header><div><small>Projects, sources, originals, excerpts, and knowledge</small><h2>Archive search</h2></div><span>{archiveResults.length} results</span></header>{archiveResults.map((result, index) => <article key={`${result.result_type}-${result.source_id || result.project_id}-${index}`}><div><small>{result.result_type.replaceAll("_", " ")} · {result.project_name || "Shared Intake"}</small><strong>{result.title}</strong><p>{result.excerpt || "Matching archive record"}</p></div>{result.original_file_id ? <a className="button compact" href={`/api/original-files/${result.original_file_id}`}>Open original</a> : result.source_id ? <a className="button compact" href={`/api/sources/${result.source_id}/original`}>Open source</a> : result.project_id ? <button className="button compact" onClick={() => navigate(`/projects/${result.project_id}`)}>Open project</button> : null}</article>)}</section>}
      <section className="board" aria-label="Project board">
        {grouped.map(([name, projects]) => <section className="project-group" key={name}>
          <header><div><span>⌄</span><strong>{name}</strong><small>{projects.length} shown</small></div><span>portfolio group</span></header>
          <div className="row headings"><span>Project</span><span>Priority</span><span>Status & latest change</span><span>Next action</span><span>Assignment group</span></div>
          {projects.map(project => <article className="row project-row" role="button" aria-label={`Open ${project.name}; files can be dropped here`} tabIndex={0} key={project.id} onClick={() => navigate(`/projects/${project.id}`)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); navigate(`/projects/${project.id}`); } }} onDragOver={event => { event.preventDefault(); event.currentTarget.classList.add("dragging"); }} onDragLeave={event => event.currentTarget.classList.remove("dragging")} onDrop={event => { event.preventDefault(); event.stopPropagation(); event.currentTarget.classList.remove("dragging"); const file = event.dataTransfer.files[0]; if (file) drop(project.id, file).catch(error => setNotice(error.message)); }}>
            <div className="project-name"><strong>{project.name}</strong><small>{project.snow_number || "No SNOW number"}</small><em>Drop a source here</em></div>
            <div><Pill value={project.priority} /></div>
            <div><Status value={project.status} /><p>{project.latest_change || "No cited changes yet."}</p></div>
            <div><strong>{project.next_action || "No next action set"}</strong><small>{project.next_action_due || "No due date"}</small></div>
            <div><span>{project.assignment_group || "No assignment group"}</span></div>
          </article>)}
        </section>)}
        {board && board.items.length === 0 && <div className="empty">No projects match the current filters.</div>}
      </section>
    </div>
    {newOpen && <Modal title="Create a project" onClose={() => setNewOpen(false)}><form onSubmit={createProject} className="form-stack"><label>Project name<input autoFocus required value={newName} onChange={event => setNewName(event.target.value)} /></label><label>Portfolio group<select value={newGroup} onChange={event => setNewGroup(event.target.value)}><option value="">Unassigned</option>{groups.filter(group => !group.is_system).map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><button type="button" className="button" onClick={() => createGroup().catch(error => setNotice(error.message))}>+ Create portfolio group</button><button className="button primary">Create project</button></form></Modal>}
  </>;
}

function Metric({ label, value, tone = "" }: { label: string; value: React.ReactNode; tone?: string }) { return <div className={`metric ${tone}`}><strong>{value}</strong><span>{label}</span></div>; }
function Pill({ value }: { value: string }) { return <span className={`pill ${value.toLowerCase()}`}>{value}</span>; }
function Status({ value }: { value: string }) { return <span className={`status ${value.toLowerCase()}`}><i />{value}</span>; }

function ProjectArchiveWorkspace({ projectId, navigate }: { projectId: string; navigate: (path: string) => void }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [notice, setNotice] = useState("");
  const [citation, setCitation] = useState<any>(null);
  const [sourceDetail, setSourceDetail] = useState<Source | null>(null);
  const [priorSummary, setPriorSummary] = useState<any>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasted, setPasted] = useState({ title: "Manual note", text: "", isTranscript: false, meetingName: "", meetingDate: new Date().toISOString().slice(0, 10) });
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<any>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [view, setView] = useState<"summary" | "knowledge" | "sources">("summary");
  const [filters, setFilters] = useState({ q: "", category: "", status: "all", source: "", date: "" });
  const load = useCallback(async () => {
    const [detail, groupData] = await Promise.all([
      api.get<ProjectDetail>(`/api/projects/${projectId}`), api.get<Group[]>("/api/groups"),
    ]);
    setProject(detail); setGroups(groupData); return detail;
  }, [projectId]);
  useEffect(() => { load().catch(error => setNotice(error.message)); }, [load]);
  async function patchProject(field: string, value: string | number | null) {
    setProject(await api.patch<ProjectDetail>(`/api/projects/${projectId}`, { [field]: value }));
  }
  async function uploadFiles(files: File[]) {
    const first = files[0];
    if (!first) return;
    const lowerNames = files.map(file => file.name.toLowerCase());
    const transcript = lowerNames.some(name => [".vtt", ".srt"].some(ext => name.endsWith(ext))) ||
      (lowerNames.some(name => [".txt", ".md"].some(ext => name.endsWith(ext))) && confirm("Does this selection contain a meeting transcript?"));
    let meeting_name = "", meeting_date = "";
    if (transcript) {
      meeting_name = prompt("Meeting name") || "";
      meeting_date = prompt("Meeting date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "";
      if (!meeting_name || !meeting_date) throw new Error("Meeting name and date are required for transcripts.");
    }
    const result = files.length === 1 && !first.webkitRelativePath
      ? await api.upload<any>(`/api/projects/${projectId}/sources`, first, { meeting_name, meeting_date, is_transcript: transcript })
      : await api.uploadMany<any>(`/api/projects/${projectId}/sources`, files, { meeting_name, meeting_date, is_transcript: transcript });
    setNotice(result.duplicate ? "This content is already preserved for the project." : "Source package preserved; local processing continues.");
    await load();
    if (!result.duplicate && result.source?.id) void pollProjectSource(projectId, result.source.id, load).catch(error => setNotice(error.message));
  }
  async function openCitation(item: Citation) { setCitation({ ...(await api.get<Record<string, unknown>>(`/api/chunks/${item.chunk_id}`)), citation_id: item.citation_id }); }
  async function reviewKnowledge(item: KnowledgeItem, status: "approved" | "flagged") {
    await api.patch(`/api/projects/${projectId}/knowledge/${item.id}`, { status });
    setNotice(`Knowledge item ${status}; Living Summary approval remains independent.`); await load();
  }
  async function reviewSummary(status: "approved" | "flagged") {
    await api.patch(`/api/projects/${projectId}/living-summary/review`, { status });
    setNotice(`Living Summary ${status}; Knowledge History states were not changed.`); await load();
  }
  async function refreshDerived(sourceId: number) {
    const result = await api.post<{ files_refreshed: number; unsupported_files: number }>(`/api/sources/${sourceId}/refresh-derived`);
    setNotice(`Rebuilt ${result.files_refreshed} extracted file(s); all original hashes were verified first.`);
    setSourceDetail(await api.get<Source>(`/api/sources/${sourceId}`));
    await load();
  }
  async function submitPastedText(event: React.FormEvent) {
    event.preventDefault();
    const source = await api.post<{ id: number }>(`/api/projects/${projectId}/notes`, {
      title: pasted.title, text: pasted.text, is_transcript: pasted.isTranscript,
      meeting_name: pasted.isTranscript ? pasted.meetingName : null,
      meeting_date: pasted.isTranscript ? pasted.meetingDate : null,
    });
    setPasteOpen(false);
    setPasted({ ...pasted, text: "" });
    setNotice("Pasted text was preserved exactly as submitted; processing continues locally.");
    await load();
    void pollProjectSource(projectId, source.id, load).catch(error => setNotice(error.message));
  }
  async function askProject(event: React.FormEvent) {
    event.preventDefault(); setChatBusy(true);
    try { setAnswer(await api.post(`/api/projects/${projectId}/chat`, { question })); setQuestion(""); }
    catch (error) { setNotice((error as Error).message); }
    finally { setChatBusy(false); }
  }
  async function addAction() {
    const description = prompt("Action description")?.trim();
    if (!description) return;
    const assignee = prompt("Assignee name, team/office, or Me", "Me")?.trim() || "Me";
    const due = prompt("Due date (YYYY-MM-DD), or leave blank", "")?.trim() || null;
    await api.post(`/api/projects/${projectId}/actions`, {
      description, assignee_type: assignee.toLowerCase() === "me" ? "me" : "person",
      assignee_value: assignee, due_date: due, state: "open", progress_text: null,
    });
    await load();
  }
  if (!project) return <div className="page loading">Loading project...</div>;
  const knowledge = project.knowledge_history.filter(item =>
    (!filters.q || `${item.text} ${item.source_title || ""}`.toLowerCase().includes(filters.q.toLowerCase())) &&
    (!filters.category || item.category === filters.category) &&
    (filters.status === "all" || item.review_status === filters.status) &&
    (!filters.source || String(item.source_id) === filters.source) &&
    (!filters.date || (item.source_date || "").slice(0, 10) === filters.date)
  );
  const citationsFor = (ids: string[]) => Array.from(new Map(
    project.knowledge_history.filter(item => ids.includes(item.id)).flatMap(item => item.citations)
      .map(item => [`${item.source_id}-${item.chunk_id}`, item])
  ).values());
  return <>
    <PageHeader eyebrow={project.portfolio_group_name} title={project.name}>
      <button className="button" onClick={() => navigate("/")}>Back to portfolio</button>
      <select aria-label="Project priority" value={project.priority} onChange={event => patchProject("priority", event.target.value)}>{["Critical", "High", "Medium", "Low"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Project status" value={project.status} onChange={event => patchProject("status", event.target.value)}>{["Green", "Yellow", "Red", "Complete"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Portfolio group" value={project.portfolio_group_id} onChange={event => patchProject("portfolio_group_id", Number(event.target.value))}>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select>
    </PageHeader>
    <div className="archive-workspace">
      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>x</button></div>}
      <section className="snow-strip"><div><small>SNOW number</small><strong>{project.snow_number || "Not linked"}</strong></div><div><small>SNOW state</small><strong>{project.snow_state || "-"}</strong></div><div><small>SNOW priority</small><strong>{project.snow_priority || "-"}</strong></div><div><small>Assignment Group</small><strong>{project.assignment_group || "None"}</strong></div></section>
      <nav className="project-tabs" aria-label="Project archive views"><button className={view === "summary" ? "active" : ""} onClick={() => setView("summary")}>Living Summary</button><button className={view === "knowledge" ? "active" : ""} onClick={() => setView("knowledge")}>Knowledge History <span>{project.knowledge_history.length}</span></button><button className={view === "sources" ? "active" : ""} onClick={() => setView("sources")}>Sources <span>{project.sources.filter(source => !source.parent_source_id).length}</span></button></nav>
      <section className="panel manual-fields"><header><div><small>User-controlled</small><h2>Project fields</h2></div></header><div><label>Project name<input defaultValue={project.name} onBlur={event => event.target.value !== project.name && patchProject("name", event.target.value)} /></label><label>Owner<input defaultValue={project.owner_text || ""} onBlur={event => patchProject("owner_text", event.target.value || null)} /></label><label>Primary next action<input defaultValue={project.next_action || ""} onBlur={event => patchProject("next_action", event.target.value || null)} /></label><label>Due date<input type="date" defaultValue={project.next_action_due || ""} onBlur={event => patchProject("next_action_due", event.target.value || null)} /></label></div></section>
      <IngestionDropZone onFiles={files => uploadFiles(files).catch(error => setNotice(error.message))} />
      <button className="button" onClick={() => setPasteOpen(true)}>Paste a note or transcript</button>
      {view === "summary" && <section className="panel summary-panel">
        <header><div><small>Revision {project.living_summary.revision} / {project.living_summary.generation_state} / {project.living_summary.review_status}</small><h2>Living Summary</h2></div><div className="header-actions"><button className="button compact" onClick={() => api.post(`/api/projects/${projectId}/living-summary/regenerate`).then(load).catch(error => setNotice(error.message))}>Regenerate</button><button className="button compact" onClick={() => reviewSummary("flagged").catch(error => setNotice(error.message))}>Flag</button><button className="button primary compact" onClick={() => reviewSummary("approved").catch(error => setNotice(error.message))}>Approve</button></div></header>
        {project.living_summary.generation_state !== "current" && <div className={`notice ${project.living_summary.generation_state === "failed" ? "error" : ""}`}>Summary is {project.living_summary.generation_state}. {project.living_summary.error || "The last valid version does not include all current knowledge."}</div>}
        {project.living_summary.current?.content.sections.length ? project.living_summary.current.content.sections.map((section, index) => <article className="summary-claim" key={`${section.section}-${index}`}><div><small>{section.section}</small><p>{section.text}</p><code>{section.knowledge_item_ids.join(", ")}</code><Citations items={citationsFor(section.knowledge_item_ids)} onOpen={openCitation} /></div><button className="button compact" onClick={() => Promise.all(section.knowledge_item_ids.map(id => api.patch(`/api/projects/${projectId}/knowledge/${id}`, { status: "flagged" }))).then(load).catch(error => setNotice(error.message))}>Flag statement</button></article>) : <p>No eligible source-grounded knowledge yet.</p>}
        <details><summary>Prior versions ({project.living_summary.versions.length})</summary>{project.living_summary.versions.map(version => <div className="version-row" key={version.id}><strong>Revision {version.revision}</strong><span>{version.review_status} / {new Date(version.created_at).toLocaleString()}</span><button className="button compact" onClick={() => api.get(`/api/projects/${projectId}/living-summary/versions/${version.revision}`).then(setPriorSummary).catch(error => setNotice(error.message))}>Compare</button></div>)}</details>
      </section>}
      {view === "knowledge" && <section className="panel"><header><div><small>Chronological and independently reviewed</small><h2>Knowledge History</h2></div><span>{knowledge.length} shown</span></header>
        <div className="knowledge-filters"><input placeholder="Search knowledge or source" value={filters.q} onChange={event => setFilters({ ...filters, q: event.target.value })} /><select value={filters.category} onChange={event => setFilters({ ...filters, category: event.target.value })}><option value="">All categories</option>{["decision", "development", "milestone", "risk", "action"].map(value => <option key={value}>{value}</option>)}</select><select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })}><option value="all">All review states</option>{["unreviewed", "approved", "flagged"].map(value => <option key={value}>{value}</option>)}</select><select value={filters.source} onChange={event => setFilters({ ...filters, source: event.target.value })}><option value="">All sources</option>{Array.from(new Map(project.knowledge_history.map(item => [item.source_id, item.source_title || item.source_type])).entries()).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select><input aria-label="Knowledge source date" type="date" value={filters.date} onChange={event => setFilters({ ...filters, date: event.target.value })} /></div>
        {knowledge.map(item => <article className="knowledge-item" key={item.id}><div><small>{item.category} / {item.source_date ? new Date(item.source_date).toLocaleDateString() : "No date"} / {item.review_status}</small><strong>{item.text}</strong><span>{item.source_title || item.source_type} / {item.id}</span>{item.supersedes_knowledge_item_id && <span>Supersedes {item.supersedes_knowledge_item_id}</span>}<Citations items={item.citations} onOpen={openCitation} /></div><div className="decision-actions"><button className="button compact" onClick={() => reviewKnowledge(item, "flagged").catch(error => setNotice(error.message))}>Flag</button><button className="button primary compact" onClick={() => reviewKnowledge(item, "approved").catch(error => setNotice(error.message))}>Approve</button></div></article>)}
      </section>}
      {view === "sources" && <section className="panel"><header><div><small>Self-contained OneDrive ingestion packages</small><h2>Sources</h2></div><button className="button" onClick={() => api.post("/api/sources/retry-pending").then(() => pollRefresh(load)).catch(error => setNotice(error.message))}>Retry pending</button></header><div className="source-list">{project.sources.filter(source => !source.parent_source_id).map(source => <article key={source.id}><div><small>{source.ingestion_id || "Linked source"} / {new Date(source.created_at).toLocaleString()}</small><strong>{source.source_title || source.original_filename}</strong><p>{source.source_summary || "Derived summary pending."}</p>{source.error_message && <p>{source.error_message}</p>}</div><span className={`state ${source.processing_state}`}>{source.processing_state.replace("_", " ")}</span><button className="button compact" onClick={() => api.get<Source>(`/api/sources/${source.id}`).then(setSourceDetail).catch(error => setNotice(error.message))}>Inspect package</button><a className="button compact" href={`/api/sources/${source.id}/original`}>Open original</a></article>)}</div></section>}
      <section className="panel"><header><div><small>User-managed and source-cited</small><h2>Project action items</h2></div><div className="header-actions"><span>{project.action_items.filter(item => item.state !== "complete").length} open</span><button className="button compact" onClick={() => addAction().catch(error => setNotice(error.message))}>+ Add action</button></div></header>{project.action_items.length ? project.action_items.map(item => <article className="action-item" key={item.id}><div><Status value={item.state === "complete" ? "Complete" : item.state === "blocked" ? "Red" : "Green"} /><strong>{item.description}</strong><small>{item.assignee_value} · {item.due_date || "No due date"}</small>{item.progress_text && <p>{item.progress_text}</p>}</div>{item.state !== "complete" && <button className="button" onClick={async () => { if (confirm("Complete this action item? This requires your explicit approval.")) { await api.post(`/api/projects/${projectId}/actions/${item.id}/complete`, { confirmed: true }); await load(); } }}>Complete</button>}</article>) : <p className="muted">No action items yet.</p>}</section>
      <section className="panel archive-chat"><header><div><small>Uses only this project's evidence</small><h2>Project-scoped assistant</h2></div></header><div className="chat-answer">{answer ? <><p>{answer.answer}</p>{answer.evidence_dropped_chunks > 0 && <div className="uncertainty">{answer.evidence_dropped_chunks} matching chunks were outside the configured evidence window.</div>}{answer.uncertainty && <div className="uncertainty">{answer.uncertainty}</div>}{answer.claims?.flatMap((claim: any) => claim.citations).length > 0 && <Citations items={answer.claims.flatMap((claim: any) => claim.citations)} onOpen={openCitation} />}</> : <p className="muted">Ask what changed, what remains unresolved, or what supports a decision.</p>}</div><form className="chat-form" onSubmit={askProject}><label htmlFor="archive-project-question">Ask this project</label><textarea id="archive-project-question" value={question} onChange={event => setQuestion(event.target.value)} required placeholder="Ask about this project" /><button className="button primary" disabled={chatBusy}>{chatBusy ? "Searching evidence…" : "Send ↑"}</button></form></section>
    </div>
    {citation && <Modal title="Source evidence" onClose={() => setCitation(null)}><div className="citation-detail"><small>{citation.original_filename} / {citation.locator}</small><blockquote>{citation.text}</blockquote><a className="button" href={citation.citation_id ? `/api/citations/${citation.citation_id}/original` : `/api/sources/${citation.source_id}/original`}>Open cited original</a></div></Modal>}
    {sourceDetail && <Modal title={sourceDetail.source_title || sourceDetail.original_filename} onClose={() => setSourceDetail(null)}><div className="source-package-detail"><p>{sourceDetail.source_summary}</p><small>{sourceDetail.ingestion_id} / {sourceDetail.capture_method}</small><button className="button" onClick={() => refreshDerived(sourceDetail.id).catch(error => setNotice(error.message))}>Rebuild derived files</button><h3>Original files</h3>{sourceDetail.original_files?.map(file => <article key={file.id}><div><strong>{file.original_name}</strong><small>{file.relative_path} / SHA-256 {file.sha256}</small></div><a className="button compact" href={`/api/original-files/${file.id}`}>Open original</a></article>)}<details><summary>Manifest</summary><pre>{JSON.stringify(sourceDetail.manifest, null, 2)}</pre></details></div></Modal>}
    {priorSummary && <Modal title={`Compare revision ${priorSummary.revision}`} onClose={() => setPriorSummary(null)}><div className="summary-comparison"><section><small>Selected revision</small>{priorSummary.content.sections.map((section: any, index: number) => <article key={index}><strong>{section.section}</strong><p>{section.text}</p></article>)}</section><section><small>Current valid summary</small>{project.living_summary.current?.content.sections.map((section, index) => <article key={index}><strong>{section.section}</strong><p>{section.text}</p></article>)}</section></div></Modal>}
    {pasteOpen && <Modal title="Preserve pasted text" onClose={() => setPasteOpen(false)}><form className="form-stack" onSubmit={event => submitPastedText(event).catch(error => setNotice(error.message))}><label>Title<input required value={pasted.title} onChange={event => setPasted({ ...pasted, title: event.target.value })} /></label><label className="check-field"><input type="checkbox" checked={pasted.isTranscript} onChange={event => setPasted({ ...pasted, isTranscript: event.target.checked })} /> This is a meeting transcript</label>{pasted.isTranscript && <><label>Meeting name<input required value={pasted.meetingName} onChange={event => setPasted({ ...pasted, meetingName: event.target.value })} /></label><label>Meeting date<input required type="date" value={pasted.meetingDate} onChange={event => setPasted({ ...pasted, meetingDate: event.target.value })} /></label></>}<label>Text exactly as submitted<textarea required rows={14} value={pasted.text} onChange={event => setPasted({ ...pasted, text: event.target.value })} /></label><p className="muted">The archive records this as pasted text, never as an original email container.</p><button className="button primary">Preserve and process</button></form></Modal>}
  </>;
}

function ProjectWorkspace({ projectId, navigate }: { projectId: string; navigate: (path: string) => void }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [notice, setNotice] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<any>(null);
  const [citation, setCitation] = useState<any>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const load = useCallback(async () => {
    const [detail, groupData] = await Promise.all([api.get<ProjectDetail>(`/api/projects/${projectId}`), api.get<Group[]>("/api/groups")]);
    setProject(detail); setGroups(groupData);
    return detail;
  }, [projectId]);
  useEffect(() => { load().catch(error => setNotice(error.message)); }, [load]);
  async function patch(field: string, value: string | number | null) { setProject(await api.patch<ProjectDetail>(`/api/projects/${projectId}`, { [field]: value })); }
  async function upload(file: File) {
    const lowerName = file.name.toLowerCase();
    const transcript = [".vtt", ".srt"].some(ext => lowerName.endsWith(ext)) ||
      ([".txt", ".md"].some(ext => lowerName.endsWith(ext)) && confirm("Is this text file a meeting transcript?"));
    let meeting_name = "", meeting_date = "";
    if (transcript) { meeting_name = prompt("Meeting name") || ""; meeting_date = prompt("Meeting date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || ""; if (!meeting_name || !meeting_date) { setNotice("Upload cancelled: meeting name and date are required for transcripts."); return; } }
    const result = await api.upload<any>(`/api/projects/${projectId}/sources`, file, { meeting_name, meeting_date, is_transcript: transcript });
    setNotice(result.duplicate ? "That source is already preserved for this project." : "Source preserved. Processing continues locally.");
    await load();
    if (!result.duplicate && result.source?.id) {
      void pollProjectSource(projectId, result.source.id, load).catch(error => setNotice(error.message));
    }
  }
  async function ask(event: React.FormEvent) { event.preventDefault(); setChatBusy(true); try { setAnswer(await api.post(`/api/projects/${projectId}/chat`, { question })); setQuestion(""); } catch (error) { setNotice((error as Error).message); } finally { setChatBusy(false); } }
  async function openCitation(item: Citation) { setCitation(await api.get(`/api/chunks/${item.chunk_id}`)); }
  async function addNote() {
    const text = prompt("Enter a manual project note")?.trim();
    if (!text) return;
    const source = await api.post<{ id: number }>(`/api/projects/${projectId}/notes`, { title: "Manual note", text });
    setNotice("Manual note preserved for processing.");
    await load();
    void pollProjectSource(projectId, source.id, load).catch(error => setNotice(error.message));
  }
  async function addAction() {
    const description = prompt("Action description")?.trim();
    if (!description) return;
    const assignee = prompt("Assignee name, team/office, or Me", "Me")?.trim() || "Me";
    const due = prompt("Due date (YYYY-MM-DD), or leave blank", "")?.trim() || null;
    await api.post(`/api/projects/${projectId}/actions`, {
      description, assignee_type: assignee.toLowerCase() === "me" ? "me" : "person",
      assignee_value: assignee, due_date: due, state: "open", progress_text: null,
    });
    await load();
  }
  if (!project) return <div className="page loading">Loading project…</div>;
  const latestCitedUpdate = project.updates.find(update => update.citations.length > 0);
  return <>
    <PageHeader eyebrow={project.portfolio_group_name} title={project.name}>
      <button className="button" onClick={() => navigate("/")}>← Portfolio</button>
      <select aria-label="Project priority" value={project.priority} onChange={event => patch("priority", event.target.value)}>{["Critical", "High", "Medium", "Low"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Project status" value={project.status} onChange={event => patch("status", event.target.value)}>{["Green", "Yellow", "Red", "Complete"].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Portfolio group" value={project.portfolio_group_id} onChange={event => patch("portfolio_group_id", Number(event.target.value))}>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select>
    </PageHeader>
    <div className="workspace-grid">
      <div className="workspace-main">
        {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}
        <section className="snow-strip"><div><small>SNOW number</small><strong>{project.snow_number || "Not linked"}</strong></div><div><small>SNOW state</small><strong>{project.snow_state || "—"}</strong></div><div><small>SNOW priority</small><strong>{project.snow_priority || "—"}</strong></div><div><small>Assignment Group</small><strong>{project.assignment_group || "No assignment group"}</strong></div></section>
        <section className="panel manual-fields"><header><div><small>User-controlled</small><h2>Project fields</h2></div></header><div><label>Project name<input defaultValue={project.name} onBlur={event => { if (event.target.value !== project.name) patch("name", event.target.value); }} /></label><label>Owner<input defaultValue={project.owner_text || ""} onBlur={event => patch("owner_text", event.target.value || null)} /></label><label>Primary next action<input defaultValue={project.next_action || ""} onBlur={event => patch("next_action", event.target.value || null)} /></label><label>Due date<input type="date" defaultValue={project.next_action_due || ""} onBlur={event => patch("next_action_due", event.target.value || null)} /></label></div></section>
        <DropZone label="Drop email, transcript, note, or project file here" onFile={file => upload(file).catch(error => setNotice(error.message))} />
        <section className="panel summary-panel"><header><div><small>Current project knowledge</small><h2>Source-grounded summary</h2></div></header><p>{project.current_summary || "No processed project evidence yet."}</p>{project.summary_citations.length > 0 && <div className="latest"><strong>Summary evidence</strong><Citations items={project.summary_citations} onOpen={openCitation} /></div>}{latestCitedUpdate && <div className="latest"><strong>Latest cited update</strong><p>{latestCitedUpdate.text}</p><Citations items={latestCitedUpdate.citations} onOpen={openCitation} /></div>}</section>
        <section className="panel"><header><h2>Project action items</h2><div className="header-actions"><span>{project.action_items.filter(item => item.state !== "complete").length} open</span><button className="button compact" onClick={() => addAction().catch(error => setNotice(error.message))}>+ Add action</button></div></header>{project.action_items.length ? project.action_items.map(item => <article className="action-item" key={item.id}><div><Status value={item.state === "complete" ? "Complete" : item.state === "blocked" ? "Red" : "Green"} /><strong>{item.description}</strong><small>{item.assignee_value} · {item.due_date || "No due date"}</small>{item.progress_text && <p>{item.progress_text}</p>}</div>{item.state !== "complete" && <button className="button" onClick={async () => { if (confirm("Complete this action item? This requires your explicit approval.")) { await api.post(`/api/projects/${projectId}/actions/${item.id}/complete`, { confirmed: true }); load(); } }}>Complete</button>}</article>) : <p className="muted">No action items yet.</p>}</section>
        <section className="panel"><header><h2>Sources</h2><div className="header-actions"><button className="button compact" onClick={() => addNote().catch(error => setNotice(error.message))}>+ Manual note</button><button className="button" onClick={() => api.post("/api/sources/retry-pending").then(() => pollRefresh(load)).catch(error => setNotice(error.message))}>Retry pending</button></div></header><div className="source-list">{project.sources.map(source => <article className={source.parent_source_id ? "child-source" : ""} key={source.id}><div><strong>{source.parent_source_id ? "↳ Attachment: " : ""}{source.original_filename}</strong><small>{source.parent_original_filename ? `From ${source.parent_original_filename} · ` : ""}{new Date(source.created_at).toLocaleString()}</small>{Number(source.metadata.evidence_dropped_chunks || 0) > 0 && <p>{String(source.metadata.evidence_dropped_chunks)} chunks were outside the configured AI evidence window; committed updates cover only the supplied chunks.</p>}{source.error_message && <p>{source.error_message}</p>}</div><span className={`state ${source.processing_state}`}>{source.processing_state.replace("_", " ")}</span>{["pending_ai", "error"].includes(source.processing_state) && <button className="button compact" onClick={() => api.post(`/api/sources/${source.id}/retry`).then(load).catch(error => setNotice(error.message))}>Retry</button>}<a className="button compact" href={`/api/sources/${source.id}/original`}>Original</a></article>)}</div></section>
        <section className="panel"><header><h2>Evidence history</h2><span>Append-only</span></header>{project.updates.map(update => <article className="timeline" key={update.id}><time>{new Date(update.created_at).toLocaleString()}</time><div><strong>{update.update_type.replace("_", " ")}</strong><p>{update.text}</p><Citations items={update.citations} onOpen={openCitation} /></div></article>)}</section>
      </div>
      <aside className="chat-panel"><div className="chat-context"><strong>✦ Project-scoped assistant</strong><p>Answers use only {project.name} sources.</p></div><div className="chat-answer">{answer ? <><p>{answer.answer}</p>{answer.evidence_dropped_chunks > 0 && <div className="uncertainty">{answer.evidence_dropped_chunks} matching chunks were outside the configured evidence window.</div>}{answer.uncertainty && <div className="uncertainty">{answer.uncertainty}</div>}{answer.claims?.flatMap((claim: any) => claim.citations).length > 0 && <Citations items={answer.claims.flatMap((claim: any) => claim.citations)} onOpen={openCitation} />}</> : <p className="muted">Ask what changed, what remains unresolved, or what supports a decision.</p>}</div><form className="chat-form" onSubmit={ask}><label htmlFor="project-question">Ask this project</label><textarea id="project-question" value={question} onChange={event => setQuestion(event.target.value)} required placeholder="Ask about this project" /><button className="button primary" disabled={chatBusy}>{chatBusy ? "Searching evidence…" : "Send ↑"}</button></form></aside>
    </div>
    {citation && <Modal title="Source evidence" onClose={() => setCitation(null)}><div className="citation-detail"><small>{citation.original_filename} · {citation.locator}</small>{citation.meeting_name && <strong>{citation.meeting_name} · {citation.meeting_date}</strong>}<blockquote>{citation.text}</blockquote><a className="button" href={`/api/sources/${citation.source_id}/original`}>Download preserved original</a></div></Modal>}
  </>;
}

function Citations({ items, onOpen }: { items: Citation[]; onOpen: (item: Citation) => void }) { return <div className="citations">{items.map((item, index) => <button key={`${item.source_id}-${item.chunk_id}`} onClick={() => onOpen(item)}>[{index + 1}] {item.original_filename || item.display_name || `Source ${item.source_id}`}{item.locator ? ` · ${item.locator}` : ""}</button>)}</div>; }

function IngestionDropZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const filesId = useMemo(() => `ingestion-files-${Math.random().toString(36).slice(2)}`, []);
  const folderId = useMemo(() => `ingestion-folder-${Math.random().toString(36).slice(2)}`, []);
  return <section className="dropzone ingestion-dropzone" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const files = [...event.dataTransfer.files]; if (files.length) onFiles(files); }}><span>+</span><strong>Drop files, email, documents, or transcripts</strong><small>Use Choose folder to preserve a folder tree. One selection becomes one durable ingestion package.</small><div className="header-actions"><label className="button" htmlFor={filesId}>Choose files</label><label className="button" htmlFor={folderId}>Choose folder</label></div><input id={filesId} type="file" multiple onChange={event => { const files = [...(event.target.files || [])]; if (files.length) onFiles(files); event.target.value = ""; }} /><input id={folderId} type="file" multiple ref={element => { if (element) element.setAttribute("webkitdirectory", ""); }} onChange={event => { const files = [...(event.target.files || [])]; if (files.length) onFiles(files); event.target.value = ""; }} /></section>;
}

function DropZone({ label, onFile, description = "MSG, EML, TXT, MD, VTT, SRT, DOCX, or text-layer PDF", accept }: { label: string; onFile: (file: File) => void; description?: string; accept?: string }) {
  const id = useMemo(() => `upload-${Math.random().toString(36).slice(2)}`, []);
  return <label className="dropzone" htmlFor={id} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) onFile(file); }}><span>⇧</span><strong>{label}</strong><small>{description}</small><span className="button">Choose file</span><input id={id} type="file" accept={accept} onChange={event => { const file = event.target.files?.[0]; if (file) onFile(file); event.target.value = ""; }} /></label>;
}

function ReviewQueue({ navigate, onChange }: { navigate: (path: string) => void; onChange: () => void }) {
  const [items, setItems] = useState<Review[]>([]);
  const [selected, setSelected] = useState<Review | null>(null);
  const [target, setTarget] = useState("");
  const [notice, setNotice] = useState("");
  const [meetingName, setMeetingName] = useState("");
  const [meetingDate, setMeetingDate] = useState(new Date().toISOString().slice(0, 10));
  const [isTranscript, setIsTranscript] = useState(false);
  const [citation, setCitation] = useState<any>(null);
  const [actionDraft, setActionDraft] = useState({ description: "", assignee_type: "me", assignee_value: "", due_date: "" });
  const load = useCallback(async () => { const data = await api.get<Review[]>("/api/reviews?status=open"); setItems(data); setSelected(current => data.find(item => item.id === current?.id) || data[0] || null); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    setTarget(selected?.project_id || "");
    const proposed = selected?.evidence[0] as any;
    setActionDraft({
      description: proposed?.description || "",
      assignee_type: proposed?.assignee_type || "me",
      assignee_value: proposed?.assignee_value || "",
      due_date: proposed?.due_date || "",
    });
  }, [selected]);
  async function upload(file: File) {
    const transcript = isTranscript || [".vtt", ".srt"].some(ext => file.name.toLowerCase().endsWith(ext));
    if (transcript && (!meetingName || !meetingDate)) throw new Error("Meeting name and date are required for a multi-project transcript.");
    await api.upload("/api/intake/multi-project", file, { meeting_name: meetingName, meeting_date: meetingDate, is_transcript: transcript });
    setNotice("Material preserved. Routing recommendations are processing locally.");
    void pollRefresh(load).catch(error => setNotice(error.message));
  }
  async function resolve(action: "apply" | "dismiss") {
    if (!selected) return;
    const evidence = selected.evidence[0] as any;
    const rule = evidence?.suggested_rule;
    if (action === "apply" && selected.kind === "multi_project_route" && (!target || !rule)) { setNotice("Choose a target project and confirm the displayed routing rule."); return; }
    if (action === "apply" && selected.kind === "cross_project_evidence" && !target) { setNotice("Choose the project that should receive this cited evidence."); return; }
    if (action === "apply" && selected.kind === "inferred_action" && (!actionDraft.description || !actionDraft.assignee_value || !actionDraft.due_date)) { setNotice("Confirm the action description, assignee, and due date."); return; }
    const fieldRecommendation = selected.kind === "project_field_recommendation" ? evidence : null;
    await api.post(`/api/reviews/${selected.id}/resolve`, { action, target_project_id: target || null, rule: action === "apply" && selected.kind === "multi_project_route" ? rule : null, action_item_id: evidence?.action_item_id || null, field: fieldRecommendation?.field || null, value: fieldRecommendation?.value || null, ...(selected.kind === "inferred_action" ? actionDraft : {}) });
    setNotice(action === "apply" ? (selected.kind === "multi_project_route" ? "Decision applied and routing rule saved." : "Decision applied.") : "Review item dismissed and retained for audit.");
    await load(); onChange();
  }
  return <>
    <PageHeader eyebrow="Uncertain and multi-project material" title="Review queue"><button className="button" onClick={() => navigate("/")}>← Portfolio</button></PageHeader>
    <div className="review-page">
      <section className="review-upload panel"><header><h2>Upload multi-project material</h2></header><div className="meeting-fields"><label>Meeting name<input value={meetingName} onChange={event => setMeetingName(event.target.value)} placeholder="Required for transcripts" /></label><label>Meeting date<input type="date" value={meetingDate} onChange={event => setMeetingDate(event.target.value)} /></label><label><input type="checkbox" checked={isTranscript} onChange={event => setIsTranscript(event.target.checked)} /> Treat TXT/MD as a transcript</label></div><DropZone label="Drop a multi-project transcript or file" onFile={file => upload(file).catch(error => setNotice(error.message))} />{notice && <div className="notice">{notice}</div>}</section>
      <div className="review-grid">
        <section className="review-list" aria-label="Open review items">{items.map(item => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelected(item)}><span className="kind">{item.kind.replaceAll("_", " ")}</span><strong>{item.question}</strong><small>{item.original_filename || item.project_name || "Portfolio intake"}</small></button>)}{items.length === 0 && <div className="empty">No open review items.</div>}</section>
        <section className="review-detail panel">{selected ? <><header><div><small>Why the assistant stopped</small><h2>{selected.question}</h2></div></header><p>{selected.reason}</p>{selected.evidence.map((evidence: any, index) => <div key={index}><blockquote>{evidence.text || evidence.excerpt || JSON.stringify(evidence)}</blockquote>{evidence.citations?.length > 0 && <Citations items={evidence.citations} onOpen={item => api.get(`/api/chunks/${item.chunk_id}`).then(setCitation).catch(error => setNotice(error.message))} />}</div>)}{["multi_project_route", "cross_project_evidence"].includes(selected.kind) && <label>Confirmed project<select value={target} onChange={event => setTarget(event.target.value)}><option value="">Choose a project</option>{selected.options.map(option => <option key={option.project_id} value={option.project_id}>{option.label}</option>)}</select></label>}{selected.kind === "inferred_action" && <div className="form-stack"><label>Action description<input value={actionDraft.description} onChange={event => setActionDraft({ ...actionDraft, description: event.target.value })} /></label><label>Assignee type<select value={actionDraft.assignee_type} onChange={event => setActionDraft({ ...actionDraft, assignee_type: event.target.value })}><option value="me">Me</option><option value="person">Person</option><option value="team_office">Team / office</option></select></label><label>Assignee<input value={actionDraft.assignee_value} onChange={event => setActionDraft({ ...actionDraft, assignee_value: event.target.value })} /></label><label>Due date<input type="date" value={actionDraft.due_date} onChange={event => setActionDraft({ ...actionDraft, due_date: event.target.value })} /></label></div>}<div className="memory-preview"><small>What it will remember</small><strong>{selected.memory_preview}</strong></div><div className="decision-actions"><button className="button" onClick={() => resolve("dismiss").catch(error => setNotice(error.message))}>Dismiss</button><button className="button primary" onClick={() => resolve("apply").catch(error => setNotice(error.message))}>{selected.kind === "multi_project_route" ? "Apply decision and teach" : "Apply decision"}</button></div></> : <div className="empty">Select an item to review its evidence and choices.</div>}</section>
      </div>
    </div>
    {citation && <Modal title="Source evidence" onClose={() => setCitation(null)}><div className="citation-detail"><small>{citation.original_filename} · {citation.locator}</small>{citation.meeting_name && <strong>{citation.meeting_name} · {citation.meeting_date}</strong>}<blockquote>{citation.text}</blockquote><a className="button" href={`/api/sources/${citation.source_id}/original`}>Download preserved original</a></div></Modal>}
  </>;
}

function SnowImport({ navigate }: { navigate: (path: string) => void }) {
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function upload(file: File) { setBusy(true); setError(""); try { setResult(await api.upload("/api/import/servicenow", file)); } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); } }
  return <><PageHeader eyebrow="Manual fixed-column import" title="ServiceNow export"><button className="button" onClick={() => navigate("/")}>← Portfolio</button></PageHeader><div className="import-page"><section className="panel import-card"><header><div><small>CSV or XLSX</small><h2>Import a cumulative SNOW export</h2></div></header><p>Required columns: Number, Short description, Assignment group, Updated, and Comments and Work notes. Application status, priority, group, and next action are never overwritten.</p><DropZone label={busy ? "Importing and processing…" : "Drop a SNOW CSV or XLSX export"} description="CSV or XLSX with the fixed recognized SNOW columns" accept=".csv,.xlsx" onFile={upload} />{error && <div className="notice error">{error}</div>}{result && <div className="import-results"><Metric label="Tickets read" value={result.tickets_read} /><Metric label="New comments" value={result.new_comments_applied} /><Metric label="Unchanged" value={result.tickets_unchanged} /><Metric label="Review / error" value={result.review_or_error_count} tone={result.review_or_error_count ? "danger" : ""} /></div>}</section>{result && <section className="panel"><header><h2>Import result</h2></header><p>{result.affected_projects.length} projects affected · {result.pending_ai} ticket sources waiting on AI.</p>{result.affected_projects.slice(0, 20).map((id: string) => <button className="link-button" key={id} onClick={() => navigate(`/projects/${id}`)}>Open affected project →</button>)}{result.review_item_ids.length > 0 && <button className="button" onClick={() => navigate("/review")}>Open review items</button>}</section>}</div></>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) { return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button aria-label="Close" onClick={onClose}>×</button></header>{children}</section></div>; }

export default App;
