import type { Citation, KnowledgeItem, ProjectDetail } from "../../api/contracts";
import { Citations } from "../../components/Citations";
import type { KnowledgeFilters } from "./projectSelectors";

type Props = {
  project: ProjectDetail;
  knowledge: KnowledgeItem[];
  filters: KnowledgeFilters;
  onFiltersChange: (filters: KnowledgeFilters) => void;
  onOpenCitation: (citation: Citation) => void;
  onReview: (item: KnowledgeItem, status: "approved" | "flagged") => void;
};

export function KnowledgePanel({ project, knowledge, filters, onFiltersChange, onOpenCitation, onReview }: Props) {
  return <section className="panel">
    <header><div><small>Chronological and independently reviewed</small><h2>Knowledge History</h2></div><span>{knowledge.length} shown</span></header>
    <div className="knowledge-filters">
      <input placeholder="Search knowledge or source" value={filters.q} onChange={event => onFiltersChange({ ...filters, q: event.target.value })} />
      <select value={filters.category} onChange={event => onFiltersChange({ ...filters, category: event.target.value })}><option value="">All categories</option>{["decision", "development", "milestone", "risk", "action"].map(value => <option key={value}>{value}</option>)}</select>
      <select value={filters.status} onChange={event => onFiltersChange({ ...filters, status: event.target.value })}><option value="all">All review states</option>{["unreviewed", "approved", "flagged"].map(value => <option key={value}>{value}</option>)}</select>
      <select value={filters.source} onChange={event => onFiltersChange({ ...filters, source: event.target.value })}><option value="">All sources</option>{Array.from(new Map(project.knowledge_history.map(item => [item.source_id, item.source_title || item.source_type])).entries()).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select>
      <input aria-label="Knowledge source date" type="date" value={filters.date} onChange={event => onFiltersChange({ ...filters, date: event.target.value })} />
    </div>
    {knowledge.map(item => <article className="knowledge-item" key={item.id}><div><small>{item.category} / {item.source_date ? new Date(item.source_date).toLocaleDateString() : "No date"} / {item.review_status}</small><strong>{item.text}</strong><span>{item.source_title || item.source_type} / {item.id}</span>{item.supersedes_knowledge_item_id && <span>Supersedes {item.supersedes_knowledge_item_id}</span>}<Citations items={item.citations} onOpen={onOpenCitation} /></div><div className="decision-actions"><button className="button compact" onClick={() => onReview(item, "flagged")}>Flag</button><button className="button primary compact" onClick={() => onReview(item, "approved")}>Approve</button></div></article>)}
  </section>;
}
