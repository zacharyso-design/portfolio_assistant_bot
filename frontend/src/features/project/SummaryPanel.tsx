import type { Citation, ProjectDetail, SummaryVersion } from "../../api/contracts";
import { Citations } from "../../components/Citations";

type Props = {
  project: ProjectDetail;
  citationsFor: (knowledgeIds: string[]) => Citation[];
  onOpenCitation: (citation: Citation) => void;
  onRegenerate: () => void;
  onReview: (status: "approved" | "flagged") => void;
  onFlagStatement: (knowledgeIds: string[]) => void;
  onSelectVersion: (version: SummaryVersion) => void;
};

export function SummaryPanel({ project, citationsFor, onOpenCitation, onRegenerate, onReview, onFlagStatement, onSelectVersion }: Props) {
  const summary = project.living_summary;
  return <section className="panel summary-panel">
    <header>
      <div><small>Revision {summary.revision} / {summary.generation_state} / {summary.review_status}</small><h2>Living Summary</h2></div>
      <div className="header-actions"><button className="button compact" onClick={onRegenerate}>Regenerate</button><button className="button compact" onClick={() => onReview("flagged")}>Flag</button><button className="button primary compact" onClick={() => onReview("approved")}>Approve</button></div>
    </header>
    {summary.generation_state !== "current" && <div className={`notice ${summary.generation_state === "failed" ? "error" : ""}`}>Summary is {summary.generation_state}. {summary.error || "Regenerate it to build a summary from the current active knowledge."}</div>}
    {summary.current?.content.sections.length ? summary.current.content.sections.map((section, index) => <article className="summary-claim" key={`${section.section}-${index}`}><div><small>{section.section}</small><p>{section.text}</p><code>{section.knowledge_item_ids.join(", ")}</code><Citations items={citationsFor(section.knowledge_item_ids)} onOpen={onOpenCitation} /></div><button className="button compact" onClick={() => onFlagStatement(section.knowledge_item_ids)}>Flag statement</button></article>) : <p>No eligible source-grounded knowledge yet.</p>}
    <details><summary>Prior versions ({summary.versions.length})</summary>{summary.versions.map(version => <div className="version-row" key={version.id}><strong>Revision {version.revision}</strong><span>{version.review_status} / {new Date(version.created_at).toLocaleString()}</span><button className="button compact" onClick={() => onSelectVersion(version)}>Compare</button></div>)}</details>
  </section>;
}
