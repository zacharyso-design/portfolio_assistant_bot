import type { ActionItem } from "../../api/contracts";
import { Status } from "../../components/Status";

export function ActionItemsPanel({ items, onAdd, onComplete }: { items: ActionItem[]; onAdd: () => void; onComplete: (item: ActionItem) => void }) {
  return <section className="panel">
    <header><div><small>User-managed and source-cited</small><h2>Project action items</h2></div><div className="header-actions"><span>{items.filter(item => item.state !== "complete").length} open</span><button className="button compact" onClick={onAdd}>+ Add action</button></div></header>
    {items.length ? items.map(item => <article className="action-item" key={item.id}><div><Status value={item.state === "complete" ? "Complete" : item.state === "blocked" ? "Red" : "Green"} /><strong>{item.description}</strong><small>{item.assignee_value} · {item.due_date || "No due date"}</small>{item.progress_text && <p>{item.progress_text}</p>}</div>{item.state !== "complete" && <button className="button" onClick={() => onComplete(item)}>Complete</button>}</article>) : <p className="muted">No action items yet.</p>}
  </section>;
}
