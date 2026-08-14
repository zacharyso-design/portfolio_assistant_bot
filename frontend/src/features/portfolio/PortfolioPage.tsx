import { useCallback, useEffect, useMemo, useState } from "react";
import { backend, type ProjectFilters } from "../../api/backend";
import type { BoardResult, DailySummary, Group, Project, SearchResult } from "../../api/contracts";
import { apiLinks } from "../../api/links";
import type { Navigate } from "../../app/router";
import { Notice } from "../../components/Feedback";
import { Metric } from "../../components/Metric";
import { Modal } from "../../components/Modal";
import { PageHeader } from "../../components/PageHeader";
import { PriorityPill, Status } from "../../components/Status";
import { pollProjectSource } from "../../lib/polling";

const EMPTY_FILTERS: ProjectFilters = { q: "", group: "", assignment: "", status: "", priority: "" };

export function PortfolioPage({ navigate }: { navigate: Navigate }) {
  const [board, setBoard] = useState<BoardResult | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [daily, setDaily] = useState<DailySummary | null>(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [notice, setNotice] = useState("");
  const [schedulerMessage, setSchedulerMessage] = useState("");
  const [archiveResults, setArchiveResults] = useState<SearchResult[]>([]);

  const load = useCallback(async () => {
    const [projects, groupData, dailyData, searchData] = await Promise.all([
      backend.portfolio.list(filters),
      backend.groups.list(),
      backend.portfolio.getDaily(),
      filters.q.trim() ? backend.portfolio.search(filters.q) : Promise.resolve([]),
    ]);
    setBoard(projects);
    setGroups(groupData);
    setDaily(dailyData);
    setArchiveResults(searchData);
  }, [filters]);

  useEffect(() => {
    const timer = setTimeout(() => load().catch(error => setNotice(error.message)), 120);
    return () => clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    backend.configuration.get().then(data => setSchedulerMessage(data.scheduler?.message || "")).catch(() => undefined);
  }, []);

  const grouped = useMemo(() => {
    const map = new Map<string, Project[]>();
    board?.items.forEach(project => map.set(project.portfolio_group_name, [...(map.get(project.portfolio_group_name) || []), project]));
    return [...map.entries()];
  }, [board]);

  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    const project = await backend.portfolio.createProject(newName, newGroup ? Number(newGroup) : null);
    setNewOpen(false);
    navigate(`/projects/${project.id}`);
  }

  async function createGroup() {
    const name = prompt("New portfolio group name")?.trim();
    if (!name) return;
    const created = await backend.groups.create(name, groups.length * 10);
    setGroups(await backend.groups.list());
    setNewGroup(String(created.id));
  }

  async function runDaily() {
    try { setDaily(await backend.portfolio.runDaily()); }
    catch (error) { setNotice((error as Error).message); }
  }

  async function drop(projectId: string, file: File) {
    const lowerName = file.name.toLowerCase();
    const transcript = [".vtt", ".srt"].some(ext => lowerName.endsWith(ext)) ||
      ([".txt", ".md"].some(ext => lowerName.endsWith(ext)) && confirm("Is this text file a meeting transcript?"));
    let meeting_name = "";
    let meeting_date = "";
    if (transcript) {
      meeting_name = prompt("Meeting name (required for transcript citations)") || "";
      meeting_date = prompt("Meeting date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "";
      if (!meeting_name || !meeting_date) {
        setNotice("Upload cancelled: meeting name and date are required for transcripts.");
        return;
      }
    }
    const result = await backend.portfolio.uploadProjectSource(projectId, file, { meeting_name, meeting_date, is_transcript: transcript });
    setNotice(result.duplicate ? `Already captured: ${file.name}` : `Captured ${file.name} for the selected project.`);
    await load();
    if (!result.duplicate && result.source?.id) {
      void pollProjectSource(projectId, result.source.id, load).catch(error => setNotice(error.message));
    }
  }

  async function rescan() {
    const result = await backend.portfolio.rescanArchive();
    setNotice(`OneDrive rescan complete: ${result.projects} projects, ${result.sources} sources added; ${result.errors} archive item(s) could not be indexed.`);
    await load();
  }

  return <>
    <PageHeader eyebrow={new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} title="Project portfolio">
      <label className="search-field"><span className="sr-only">Search projects</span><input value={filters.q} onChange={event => setFilters({ ...filters, q: event.target.value })} placeholder="Search projects" /></label>
      <button className="button" onClick={() => rescan().catch(error => setNotice(error.message))}>Rescan OneDrive</button>
      <button className="button" onClick={() => navigate("/import")}>Import SNOW</button>
      <button className="button primary" onClick={() => setNewOpen(true)}>+ New project</button>
    </PageHeader>
    <div className="page">
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
      <section className="morning-banner">
        <span className="check">✓</span>
        <div><strong>{daily ? "Morning update complete" : "Morning update not run"}</strong><small>{daily?.summary_text || "Generate the prior day’s committed portfolio changes."}</small></div>
        <span>{daily?.updated_at ? new Date(daily.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Not run today"}{schedulerMessage ? ` · ${schedulerMessage}` : " · Checking morning task…"}</span>
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
      {filters.q.trim() && <section className="panel archive-results"><header><div><small>Projects, sources, originals, excerpts, and knowledge</small><h2>Archive search</h2></div><span>{archiveResults.length} results</span></header>{archiveResults.map((result, index) => <article key={`${result.result_type}-${result.source_id || result.project_id}-${index}`}><div><small>{result.result_type.replaceAll("_", " ")} · {result.project_name || "Shared Intake"}</small><strong>{result.title}</strong><p>{result.excerpt || "Matching archive record"}</p></div>{result.original_file_id ? <a className="button compact" href={apiLinks.originalFile(result.original_file_id)}>Open original</a> : result.source_id ? <a className="button compact" href={apiLinks.sourceOriginal(result.source_id)}>Open source</a> : result.project_id ? <button className="button compact" onClick={() => navigate(`/projects/${result.project_id}`)}>Open project</button> : null}</article>)}</section>}
      <section className="board" aria-label="Project board">
        {grouped.map(([name, projects]) => <section className="project-group" key={name}>
          <header><div><span>⌄</span><strong>{name}</strong><small>{projects.length} shown</small></div><span>portfolio group</span></header>
          <div className="row headings"><span>Project</span><span>Priority</span><span>Status & latest change</span><span>Next action</span><span>Assignment group</span></div>
          {projects.map(project => <article className="row project-row" role="button" aria-label={`Open ${project.name}; files can be dropped here`} tabIndex={0} key={project.id} onClick={() => navigate(`/projects/${project.id}`)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); navigate(`/projects/${project.id}`); } }} onDragOver={event => { event.preventDefault(); event.currentTarget.classList.add("dragging"); }} onDragLeave={event => event.currentTarget.classList.remove("dragging")} onDrop={event => { event.preventDefault(); event.stopPropagation(); event.currentTarget.classList.remove("dragging"); const file = event.dataTransfer.files[0]; if (file) drop(project.id, file).catch(error => setNotice(error.message)); }}>
            <div className="project-name"><strong>{project.name}</strong><small>{project.snow_number || "No SNOW number"}</small><em>Drop a source here</em></div>
            <div><PriorityPill value={project.priority} /></div>
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
