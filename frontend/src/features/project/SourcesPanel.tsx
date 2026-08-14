import type { Source } from "../../api/contracts";
import { apiLinks } from "../../api/links";

type Props = {
  sources: Source[];
  onRebuildKnowledge: () => void;
  onRetryPending: () => void;
  onInspect: (source: Source) => void;
  onRemove: (source: Source) => void;
  onRestore: (source: Source) => void;
};

export function SourcesPanel({ sources, onRebuildKnowledge, onRetryPending, onInspect, onRemove, onRestore }: Props) {
  return <section className="panel">
    <header><div><small>Self-contained OneDrive packages; removed items remain recoverable</small><h2>Sources</h2></div><div className="header-actions"><button className="button" onClick={onRebuildKnowledge}>Update project knowledge</button><button className="button" onClick={onRetryPending}>Retry pending</button></div></header>
    <div className="source-list">{sources.map(source => <article key={source.id}>
      <div><small>{source.ingestion_id || "Linked source"} / {new Date(source.created_at).toLocaleString()}</small><strong>{source.source_title || source.original_filename}</strong><p>{source.source_summary || "Derived summary pending."}</p>{source.memory_state === "removed" && <p>This package is archived and excluded from active project memory.</p>}{source.error_message && <p>{source.error_message}</p>}</div>
      <span className={`state ${source.memory_state}`}>{source.memory_state}</span>
      <span className={`state ${source.processing_state}`}>{source.processing_state.replace("_", " ")}</span>
      <button className="button compact" onClick={() => onInspect(source)}>Inspect package</button>
      <a className="button compact" href={apiLinks.sourceOriginal(source.id)}>Open original</a>
      {source.ingestion_path && (source.memory_state === "removed" ? <button className="button primary compact" onClick={() => onRestore(source)}>Restore to project</button> : <button className="button compact" onClick={() => onRemove(source)}>Remove from project</button>)}
    </article>)}</div>
  </section>;
}
