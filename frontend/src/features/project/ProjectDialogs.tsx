import type { CitationDetail, ProjectDetail, Source, SummaryVersionDetail } from "../../api/contracts";
import { apiLinks } from "../../api/links";
import { Modal } from "../../components/Modal";

export type PastedTextDraft = {
  title: string;
  text: string;
  isTranscript: boolean;
  meetingName: string;
  meetingDate: string;
};

type Props = {
  project: ProjectDetail;
  citation: CitationDetail | null;
  source: Source | null;
  priorSummary: SummaryVersionDetail | null;
  pasteOpen: boolean;
  pasted: PastedTextDraft;
  onCitationClose: () => void;
  onSourceClose: () => void;
  onSummaryClose: () => void;
  onPasteClose: () => void;
  onPastedChange: (draft: PastedTextDraft) => void;
  onSubmitPasted: (event: React.FormEvent) => void;
  onRefreshDerived: (source: Source) => void;
  onRemoveSource: (source: Source) => void;
  onRestoreSource: (source: Source) => void;
};

export function ProjectDialogs(props: Props) {
  const { project, citation, source, priorSummary, pasteOpen, pasted } = props;
  return <>
    {citation && <Modal title="Source evidence" onClose={props.onCitationClose}><div className="citation-detail"><small>{citation.original_filename} / {citation.locator}</small><blockquote>{citation.text}</blockquote><a className="button" href={citation.citation_id ? apiLinks.citationOriginal(citation.citation_id) : apiLinks.sourceOriginal(citation.source_id)}>Open cited original</a></div></Modal>}
    {source && <Modal title={source.source_title || source.original_filename} onClose={props.onSourceClose}><div className="source-package-detail"><p>{source.source_summary}</p><small>{source.ingestion_id} / {source.capture_method} / memory: {source.memory_state}</small><div className="header-actions"><button className="button" onClick={() => props.onRefreshDerived(source)}>Rebuild derived files</button>{source.ingestion_path && (source.memory_state === "removed" ? <button className="button primary" onClick={() => props.onRestoreSource(source)}>Restore to project</button> : <button className="button" onClick={() => props.onRemoveSource(source)}>Remove from project</button>)}</div><h3>Original files</h3>{source.original_files?.map(file => <article key={file.id}><div><strong>{file.original_name}</strong><small>{file.relative_path} / SHA-256 {file.sha256}</small></div><a className="button compact" href={apiLinks.originalFile(file.id)}>Open original</a></article>)}{source.lifecycle?.length ? <details><summary>Source history ({source.lifecycle.length})</summary>{source.lifecycle.map(event => <article key={event.id}><strong>{event.event_type.replaceAll("_", " ")}</strong><small>{new Date(event.created_at).toLocaleString()} / {event.reason}</small></article>)}</details> : null}<details><summary>Manifest</summary><pre>{JSON.stringify(source.manifest, null, 2)}</pre></details></div></Modal>}
    {priorSummary && <Modal title={`Compare revision ${priorSummary.revision}`} onClose={props.onSummaryClose}><div className="summary-comparison"><section><small>Selected revision</small>{priorSummary.content.sections.map((section, index) => <article key={index}><strong>{section.section}</strong><p>{section.text}</p></article>)}</section><section><small>Current valid summary</small>{project.living_summary.current?.content.sections.map((section, index) => <article key={index}><strong>{section.section}</strong><p>{section.text}</p></article>)}</section></div></Modal>}
    {pasteOpen && <Modal title="Preserve pasted text" onClose={props.onPasteClose}><form className="form-stack" onSubmit={props.onSubmitPasted}><label>Title<input required value={pasted.title} onChange={event => props.onPastedChange({ ...pasted, title: event.target.value })} /></label><label className="check-field"><input type="checkbox" checked={pasted.isTranscript} onChange={event => props.onPastedChange({ ...pasted, isTranscript: event.target.checked })} /> This is a meeting transcript</label>{pasted.isTranscript && <><label>Meeting name<input required value={pasted.meetingName} onChange={event => props.onPastedChange({ ...pasted, meetingName: event.target.value })} /></label><label>Meeting date<input required type="date" value={pasted.meetingDate} onChange={event => props.onPastedChange({ ...pasted, meetingDate: event.target.value })} /></label></>}<label>Text exactly as submitted<textarea required rows={14} value={pasted.text} onChange={event => props.onPastedChange({ ...pasted, text: event.target.value })} /></label><p className="muted">The archive records this as pasted text, never as an original email container.</p><button className="button primary">Preserve and process</button></form></Modal>}
  </>;
}
