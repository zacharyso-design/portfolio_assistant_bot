import { useState } from "react";
import { backend } from "../../api/backend";
import type { SnowImportResult } from "../../api/contracts";
import type { Navigate } from "../../app/router";
import { DropZone } from "../../components/DropZones";
import { Metric } from "../../components/Metric";
import { PageHeader } from "../../components/PageHeader";

export function SnowImportPage({ navigate }: { navigate: Navigate }) {
  const [result, setResult] = useState<SnowImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function upload(file: File) {
    setBusy(true); setError("");
    try { setResult(await backend.snow.import(file)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  return <>
    <PageHeader eyebrow="Manual fixed-column import" title="ServiceNow export"><button className="button" onClick={() => navigate("/")}>← Portfolio</button></PageHeader>
    <div className="import-page">
      <section className="panel import-card">
        <header><div><small>CSV or XLSX</small><h2>Import a cumulative SNOW export</h2></div></header>
        <p>Required columns: Number, Short description, Assignment group, Updated, and Comments and Work notes. Application status, priority, group, and next action are never overwritten.</p>
        <DropZone label={busy ? "Importing and processing…" : "Drop a SNOW CSV or XLSX export"} description="CSV or XLSX with the fixed recognized SNOW columns" accept=".csv,.xlsx" onFile={upload} />
        {error && <div className="notice error">{error}</div>}
        {result && <div className="import-results"><Metric label="Tickets read" value={result.tickets_read} /><Metric label="New comments" value={result.new_comments_applied} /><Metric label="Unchanged" value={result.tickets_unchanged} /><Metric label="Review / error" value={result.review_or_error_count} tone={result.review_or_error_count ? "danger" : ""} /></div>}
      </section>
      {result && <section className="panel"><header><h2>Import result</h2></header><p>{result.affected_projects.length} projects affected · {result.pending_ai} ticket sources waiting on AI.</p>{result.affected_projects.slice(0, 20).map(id => <button className="link-button" key={id} onClick={() => navigate(`/projects/${id}`)}>Open affected project →</button>)}{result.review_item_ids.length > 0 && <button className="button" onClick={() => navigate("/review")}>Open review items</button>}</section>}
    </div>
  </>;
}
