import { useCallback, useEffect, useState } from "react";
import { backend } from "../../api/backend";
import type { CitationDetail, Review, ReviewEvidence } from "../../api/contracts";
import { apiLinks } from "../../api/links";
import type { Navigate } from "../../app/router";
import { Citations } from "../../components/Citations";
import { DropZone } from "../../components/DropZones";
import { Modal } from "../../components/Modal";
import { PageHeader } from "../../components/PageHeader";
import { pollRefresh } from "../../lib/polling";

export function ReviewQueuePage({ navigate, onChange }: { navigate: Navigate; onChange: () => void }) {
  const [items, setItems] = useState<Review[]>([]);
  const [selected, setSelected] = useState<Review | null>(null);
  const [target, setTarget] = useState("");
  const [notice, setNotice] = useState("");
  const [meetingName, setMeetingName] = useState("");
  const [meetingDate, setMeetingDate] = useState(new Date().toISOString().slice(0, 10));
  const [isTranscript, setIsTranscript] = useState(false);
  const [citation, setCitation] = useState<CitationDetail | null>(null);
  const [actionDraft, setActionDraft] = useState({ description: "", assignee_type: "me", assignee_value: "", due_date: "" });

  const load = useCallback(async () => {
    const data = await backend.reviews.list();
    setItems(data);
    setSelected(current => data.find(item => item.id === current?.id) || data[0] || null);
  }, []);
  useEffect(() => { load().catch(error => setNotice(error.message)); }, [load]);
  useEffect(() => {
    const proposed = selected?.evidence[0];
    const recommendation = proposed?.recommended_project_id;
    const fitTarget = selected?.options.some(option => option.project_id === recommendation)
      ? recommendation
      : selected?.options[0]?.project_id;
    setTarget(selected?.kind === "project_fit" ? (fitTarget || "") : (selected?.project_id || ""));
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
    await backend.reviews.upload(file, { meeting_name: meetingName, meeting_date: meetingDate, is_transcript: transcript });
    setNotice("Material preserved. Routing recommendations are processing locally.");
    void pollRefresh(load).catch(error => setNotice(error.message));
  }

  async function resolve(action: "apply" | "dismiss" | "keep" | "move" | "remove") {
    if (!selected) return;
    const evidence = selected.evidence[0];
    const rule = evidence?.suggested_rule;
    if (action === "apply" && selected.kind === "multi_project_route" && (!target || !rule)) { setNotice("Choose a target project and confirm the displayed routing rule."); return; }
    // Retained for review rows created before source lifecycle migration 003.
    if (action === "apply" && selected.kind === "cross_project_evidence" && !target) { setNotice("Choose the project that should receive this cited evidence."); return; }
    if (action === "move" && (!target || target === selected.project_id)) { setNotice("Choose a different project before moving this source."); return; }
    if (action === "apply" && selected.kind === "inferred_action" && (!actionDraft.description || !actionDraft.assignee_value || !actionDraft.due_date)) { setNotice("Confirm the action description, assignee, and due date."); return; }
    let reason: string | null = null;
    if (selected.kind === "project_fit" && (action === "move" || action === "remove")) {
      reason = prompt(
        action === "remove" ? "Why should this source be archived?" : "Why does this source belong in the selected project?",
        action === "remove" ? "Added to the wrong project" : "Confirmed the correct project during review",
      );
      if (reason === null) return;
    }
    const fieldRecommendation = selected.kind === "project_field_recommendation" ? evidence : null;
    await backend.reviews.resolve(selected.id, {
      action,
      target_project_id: target || null,
      reason: reason?.trim() || null,
      rule: action === "apply" && selected.kind === "multi_project_route" ? rule : null,
      action_item_id: evidence?.action_item_id || null,
      field: fieldRecommendation?.field || null,
      value: fieldRecommendation?.value || null,
      ...(selected.kind === "inferred_action" ? actionDraft : {}),
    });
    setNotice(selected.kind === "project_fit"
      ? (action === "remove" ? "Source archived without changing project memory." : "Project choice confirmed; source processing resumed.")
      : action === "apply" ? (selected.kind === "multi_project_route" ? "Decision applied and routing rule saved." : "Decision applied.") : "Review item dismissed and retained for audit.");
    await load();
    onChange();
  }

  async function openCitation(chunkId: number) {
    setCitation(await backend.sources.getCitation(chunkId));
  }

  return <>
    <PageHeader eyebrow="Uncertain and multi-project material" title="Review queue"><button className="button" onClick={() => navigate("/")}>← Portfolio</button></PageHeader>
    <div className="review-page">
      <section className="review-upload panel"><header><h2>Upload multi-project material</h2></header><div className="meeting-fields"><label>Meeting name<input value={meetingName} onChange={event => setMeetingName(event.target.value)} placeholder="Required for transcripts" /></label><label>Meeting date<input type="date" value={meetingDate} onChange={event => setMeetingDate(event.target.value)} /></label><label><input type="checkbox" checked={isTranscript} onChange={event => setIsTranscript(event.target.checked)} /> Treat TXT/MD as a transcript</label></div><DropZone label="Drop a multi-project transcript or file" onFile={file => upload(file).catch(error => setNotice(error.message))} />{notice && <div className="notice">{notice}</div>}</section>
      <div className="review-grid">
        <section className="review-list" aria-label="Open review items">{items.map(item => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelected(item)}><span className="kind">{item.kind.replaceAll("_", " ")}</span><strong>{item.question}</strong><small>{item.original_filename || item.project_name || "Portfolio intake"}</small></button>)}{items.length === 0 && <div className="empty">No open review items.</div>}</section>
        <section className="review-detail panel">{selected ? <><header><div><small>Why the assistant stopped</small><h2>{selected.question}</h2></div></header><p>{selected.reason}</p>{selected.evidence.map((evidence, index) => <div key={index}><blockquote>{evidence.text || evidence.excerpt || JSON.stringify(evidence)}</blockquote>{evidence.citations?.length ? <Citations items={evidence.citations} onOpen={item => openCitation(item.chunk_id).catch(error => setNotice(error.message))} /> : null}</div>)}{["multi_project_route", "cross_project_evidence", "project_fit"].includes(selected.kind) && <label>{selected.kind === "project_fit" ? "Move to project" : "Confirmed project"}<select value={target} onChange={event => setTarget(event.target.value)}><option value="">Choose a project</option>{selected.options.map(option => <option key={option.project_id} value={option.project_id}>{option.label}</option>)}</select></label>}{selected.kind === "inferred_action" && <div className="form-stack"><label>Action description<input value={actionDraft.description} onChange={event => setActionDraft({ ...actionDraft, description: event.target.value })} /></label><label>Assignee type<select value={actionDraft.assignee_type} onChange={event => setActionDraft({ ...actionDraft, assignee_type: event.target.value })}><option value="me">Me</option><option value="person">Person</option><option value="team_office">Team / office</option></select></label><label>Assignee<input value={actionDraft.assignee_value} onChange={event => setActionDraft({ ...actionDraft, assignee_value: event.target.value })} /></label><label>Due date<input type="date" value={actionDraft.due_date} onChange={event => setActionDraft({ ...actionDraft, due_date: event.target.value })} /></label></div>}<div className="memory-preview"><small>What it will remember</small><strong>{selected.memory_preview}</strong></div>{selected.kind === "project_fit" ? <div className="decision-actions"><button className="button" onClick={() => resolve("remove").catch(error => setNotice(error.message))}>Archive without using</button><button className="button" onClick={() => resolve("keep").catch(error => setNotice(error.message))}>Keep in this project</button><button className="button primary" onClick={() => resolve("move").catch(error => setNotice(error.message))}>Move and process</button></div> : <div className="decision-actions"><button className="button" onClick={() => resolve("dismiss").catch(error => setNotice(error.message))}>Dismiss</button><button className="button primary" onClick={() => resolve("apply").catch(error => setNotice(error.message))}>{selected.kind === "multi_project_route" ? "Apply decision and teach" : "Apply decision"}</button></div>}</> : <div className="empty">Select an item to review its evidence and choices.</div>}</section>
      </div>
    </div>
    {citation && <Modal title="Source evidence" onClose={() => setCitation(null)}><div className="citation-detail"><small>{citation.original_filename} · {citation.locator}</small>{citation.meeting_name && <strong>{citation.meeting_name} · {citation.meeting_date}</strong>}<blockquote>{citation.text}</blockquote><a className="button" href={apiLinks.sourceOriginal(citation.source_id)}>Download preserved original</a></div></Modal>}
  </>;
}
